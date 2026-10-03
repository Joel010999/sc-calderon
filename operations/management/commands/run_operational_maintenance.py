"""Ejecuta mantenimiento operativo seguro en lotes pequeños.

Este comando es deliberadamente un coordinador: las reglas de dominio viven en
``sales`` y ``tickets``. No crea auditoría ni realiza I/O en modo dry-run.
"""

import json
import time
import uuid
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models
from django.db import close_old_connections
from django.utils import timezone

from sales.models import Booking, BookingStatus
from tickets.models import (
    FulfillmentEmailStatus,
    FulfillmentIssueStatus,
    Ticket,
    TicketFulfillment,
)


TASKS = ("expire", "payments", "fulfillment", "notifications")


class Command(BaseCommand):
    help = "Ejecuta expiración de reservas y reconciliación de fulfillment en forma acotada."

    def add_arguments(self, parser):
        parser.add_argument(
            "--task", action="append", choices=TASKS + ("reconcile", "all"),
            help="Tarea a ejecutar (se puede repetir). Por defecto: all.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Sólo inspecciona; no escribe, audita ni envía correo.")
        parser.add_argument("--limit", type=int, help="Máximo total de reservas/trabajos a considerar.")
        parser.add_argument("--booking", help="UUID público de una reserva específica.")
        parser.add_argument("--max-seconds", type=float, help="Tiempo máximo de ejecución (tope configurado).")

    def handle(self, *args, **options):
        started = time.monotonic()
        max_seconds = float(getattr(settings, "OPERATIONAL_MAINTENANCE_MAX_SECONDS", 30))
        if options["max_seconds"] is not None:
            if options["max_seconds"] <= 0:
                raise CommandError("--max-seconds debe ser mayor que cero.")
            max_seconds = min(options["max_seconds"], max_seconds)
        limit = options["limit"]
        configured_limit = int(getattr(settings, "OPERATIONAL_MAINTENANCE_MAX_LIMIT", 100))
        if limit is None:
            limit = configured_limit
        if limit < 1 or limit > configured_limit:
            raise CommandError(f"--limit debe estar entre 1 y {configured_limit}.")

        booking_id = self._parse_booking(options.get("booking"))
        tasks = self._tasks(options.get("task"))
        disabled = {
            task for task in tasks
            if not getattr(settings, f"OPERATIONAL_MAINTENANCE_ENABLE_{task.upper()}", True)
        }
        if disabled:
            raise CommandError("Tarea deshabilitada por configuración: " + ", ".join(sorted(disabled)))
        counts = Counter(processed=0, succeeded=0, failed=0, skipped=0, candidates=0)
        details = Counter()
        timed_out = False
        limit_reached = False
        considered = 0

        for task in tasks:
            if considered >= limit:
                limit_reached = True
                break
            if self._expired(started, max_seconds):
                timed_out = True
                break
            candidates = self._candidates(task, booking_id, timezone.now())[:limit - considered]
            counts["candidates"] += len(candidates)
            for candidate in candidates:
                if considered >= limit:
                    limit_reached = True
                    break
                if self._expired(started, max_seconds):
                    timed_out = True
                    break
                considered += 1
                if options["dry_run"]:
                    counts["skipped"] += 1
                    details[f"{task}:would_process"] += 1
                    continue
                counts["processed"] += 1
                try:
                    result = self._process(task, candidate)
                    if task == "fulfillment" and not self._fulfillment_succeeded(result):
                        raise RuntimeError("fulfillment no completado")
                    if task == "notifications" and getattr(result, "status", None) != "SENT":
                        raise RuntimeError("notificación no enviada")
                    counts["succeeded"] += 1
                    details[f"{task}:succeeded"] += 1
                except Exception:
                    # Un trabajo defectuoso no debe impedir el resto del lote.
                    counts["failed"] += 1
                    details[f"{task}:failed"] += 1
                    self.stderr.write("ERROR tarea=%s: operación no completada" % task)
            if considered >= limit and not timed_out:
                limit_reached = True
            if timed_out:
                break

        if timed_out:
            details["time_limit"] += 1
        elapsed_ms = int((time.monotonic() - started) * 1000)
        payload = {
            "maintenance": "operational",
            "dry_run": bool(options["dry_run"]),
            "tasks": tasks,
            "limit": limit,
            "processed": counts["processed"],
            "candidates": counts["candidates"],
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "timed_out": timed_out,
            "limit_reached": limit_reached,
            "elapsed_ms": elapsed_ms,
            "metrics": dict(details),
        }
        self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        # 0 = completo, 1 = fallos/tiempo agotado. dry-run siempre es de lectura.
        if counts["failed"] or timed_out:
            raise CommandError("El mantenimiento terminó con tareas pendientes o fallidas.")

    @staticmethod
    def _tasks(raw):
        if not raw or "all" in raw:
            return list(TASKS)
        return list(dict.fromkeys("fulfillment" if item == "reconcile" else item for item in raw))

    @staticmethod
    def _parse_booking(value):
        if not value:
            return None
        try:
            parsed = uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise CommandError("--booking debe ser un UUID público válido.") from exc
        if not Booking.objects.filter(public_id=parsed).exists():
            raise CommandError("La reserva indicada no existe.")
        return parsed

    @staticmethod
    def _expired(started, max_seconds):
        return time.monotonic() - started >= max_seconds

    @staticmethod
    def _fulfillment_succeeded(job):
        return (
            job.issue_status == FulfillmentIssueStatus.SUCCEEDED
            and job.email_status == FulfillmentEmailStatus.SENT
        )

    def _candidates(self, task, booking_id, now):
        if task == "expire":
            qs = Booking.objects.filter(status=BookingStatus.HELD, expires_at__lte=now).exclude(
                payments__status__in=["AWAITING_VOUCHER", "UNDER_REVIEW"]
            ).order_by("pk")
            if booking_id:
                qs = qs.filter(public_id=booking_id)
            return list(qs.values_list("pk", flat=True))

        if task == "payments":
            qs = Booking.objects.filter(
                status=BookingStatus.HELD,
                expires_at__lte=now,
                payments__status__in=["AWAITING_VOUCHER", "UNDER_REVIEW"],
            ).order_by("pk").distinct()
            if booking_id:
                qs = qs.filter(public_id=booking_id)
            return list(qs.values_list("pk", flat=True))

        if task == "notifications":
            from notifications.models import NotificationStatus, TransactionalNotification
            qs = TransactionalNotification.objects.filter(
                status__in=[NotificationStatus.PENDING, NotificationStatus.FAILED],
                attempts__lt=int(getattr(settings, "NOTIFICATIONS_MAX_ATTEMPTS", 5)),
            ).filter(
                models.Q(next_attempt_at__isnull=True) | models.Q(next_attempt_at__lte=now)
            ).order_by("pk")
            qs = qs.filter(booking__status__in=[BookingStatus.HELD, BookingStatus.CONFIRMED, BookingStatus.EXPIRED, BookingStatus.RELEASED])
            if booking_id:
                qs = qs.filter(booking__public_id=booking_id)
            stale = TransactionalNotification.objects.filter(
                status="PROCESSING", lease_until__lte=now,
            ).order_by("pk")
            if booking_id:
                stale = stale.filter(booking__public_id=booking_id)
            return list(qs.values_list("pk", flat=True)) + list(stale.values_list("pk", flat=True))

        jobs = TicketFulfillment.objects.filter(booking__status=BookingStatus.CONFIRMED)
        if booking_id:
            jobs = jobs.filter(booking__public_id=booking_id)
        missing = Booking.objects.filter(status=BookingStatus.CONFIRMED).exclude(
            pk__in=TicketFulfillment.objects.values("booking_id")
        )
        if booking_id:
            missing = missing.filter(public_id=booking_id)
        ids = list(missing.order_by("pk").values_list("pk", flat=True))
        ids += list(jobs.filter(
            issue_status__in=[FulfillmentIssueStatus.PENDING, FulfillmentIssueStatus.FAILED]
        ).values_list("booking_id", flat=True))
        ids += list(jobs.filter(
            email_status__in=[FulfillmentEmailStatus.PENDING, FulfillmentEmailStatus.FAILED]
        ).values_list("booking_id", flat=True))
        ids += list(jobs.filter(
            issue_status=FulfillmentIssueStatus.PROCESSING,
            lease_until__lte=now,
        ).values_list("booking_id", flat=True))
        # Tickets existentes sin trabajo también son reconciliables.
        orphan_tickets = Ticket.objects.filter(booking__status=BookingStatus.CONFIRMED).exclude(
            booking__in=TicketFulfillment.objects.values("booking_id")
        )
        if booking_id:
            orphan_tickets = orphan_tickets.filter(booking__public_id=booking_id)
        ids += list(orphan_tickets.values_list("booking_id", flat=True))
        return list(dict.fromkeys(ids))

    @staticmethod
    def _process(task, booking_pk):
        close_old_connections()
        if task == "expire":
            from sales.services import expire_booking
            expire_booking(booking_pk, now=timezone.now())
            return
        if task == "payments":
            from payments.services import expire_public_transfer_if_expired
            expire_public_transfer_if_expired(booking_pk, now=timezone.now())
            return
        if task == "notifications":
            from notifications.services import process_notification
            return process_notification(booking_pk, retry=True)
        from tickets.services import process_booking_fulfillment
        job, _ = TicketFulfillment.objects.get_or_create(booking_id=booking_pk)
        return process_booking_fulfillment(job.pk, retry=True)
