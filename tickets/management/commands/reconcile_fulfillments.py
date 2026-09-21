from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from sales.models import Booking, BookingStatus
from tickets.models import (
    FulfillmentEmailStatus,
    FulfillmentIssueStatus,
    Ticket,
    TicketFulfillment,
)
from tickets.services import process_booking_fulfillment


class Command(BaseCommand):
    help = "Reconcilia trabajos de emisión y entrega de pasajes confirmados."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Inspecciona sin crear ni procesar trabajos.")
        parser.add_argument("--limit", type=int, default=None, help="Máximo de trabajos a considerar.")
        parser.add_argument("--status", choices=["pending", "processing", "failed", "email", "missing", "all"], default="all")
        parser.add_argument("--booking", help="UUID público de una reserva específica.")

    def handle(self, *args, **options):
        limit = options["limit"]
        max_limit = getattr(settings, "TICKETS_RECONCILE_MAX_LIMIT", 100)
        if limit is None:
            limit = max_limit
        if limit > max_limit:
            raise CommandError(f"--limit no puede superar {max_limit}.")
        if limit is not None and limit < 1:
            raise CommandError("--limit debe ser mayor que cero.")
        booking_id = options["booking"]
        booking_filter = {}
        if booking_id:
            try:
                booking_filter["public_id"] = booking_id
                if not Booking.objects.filter(**booking_filter, status=BookingStatus.CONFIRMED).exists():
                    raise CommandError("La reserva no existe o no está confirmada.")
            except Exception as exc:
                if isinstance(exc, CommandError):
                    raise
                raise CommandError("--booking debe ser un UUID de reserva válido.") from exc

        candidates = self._candidate_ids(options["status"], booking_id)
        if limit is not None:
            candidates = candidates[:limit]

        counts = {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}
        if options["dry_run"]:
            for pk, reason in candidates:
                self.stdout.write(f"DRY-RUN {pk}: {reason}")
                counts["skipped"] += 1
            self._summary(counts)
            return

        for pk, reason in candidates:
            counts["processed"] += 1
            try:
                job, _ = TicketFulfillment.objects.get_or_create(booking_id=pk)
                result = process_booking_fulfillment(job.pk, retry=True)
                complete = (
                    result.issue_status == FulfillmentIssueStatus.SUCCEEDED
                    and result.email_status == FulfillmentEmailStatus.SENT
                )
                counts["succeeded" if complete else "failed"] += 1
                self.stdout.write(f"{('OK' if complete else 'ERROR')} reserva={pk} motivo={reason}")
            except Exception as exc:
                counts["failed"] += 1
                self.stderr.write(f"ERROR reserva={pk}: {self._sanitize_error(exc)}")

        self._summary(counts)
        if counts["failed"]:
            raise CommandError("La reconciliación terminó con errores.")

    def _candidate_ids(self, status, booking_id):
        qs = Booking.objects.filter(status=BookingStatus.CONFIRMED)
        if booking_id:
            qs = qs.filter(public_id=booking_id)
        jobs = TicketFulfillment.objects.filter(booking__status=BookingStatus.CONFIRMED)
        if booking_id:
            jobs = jobs.filter(booking__public_id=booking_id)
        now = timezone.now()
        selected = {}

        def add(ids, reason):
            for value in ids:
                selected.setdefault(value, reason)

        if status in ("all", "missing"):
            add(qs.exclude(pk__in=jobs.values("booking_id")).values_list("pk", flat=True), "missing")
        if status in ("all", "pending"):
            add(jobs.filter(issue_status=FulfillmentIssueStatus.PENDING).values_list("booking_id", flat=True), "pending")
        if status in ("all", "processing"):
            add(jobs.filter(issue_status=FulfillmentIssueStatus.PROCESSING).filter(lease_until__lte=now).values_list("booking_id", flat=True), "abandoned")
        if status in ("all", "failed"):
            add(jobs.filter(issue_status=FulfillmentIssueStatus.FAILED).values_list("booking_id", flat=True), "issue-failed")
        if status in ("all", "email"):
            add(jobs.filter(issue_status=FulfillmentIssueStatus.SUCCEEDED, email_status__in=[FulfillmentEmailStatus.PENDING, FulfillmentEmailStatus.FAILED]).values_list("booking_id", flat=True), "email-pending")
            add(Ticket.objects.filter(booking__status=BookingStatus.CONFIRMED, status="ISSUED", booking__ticket_fulfillment__isnull=True).values_list("booking_id", flat=True), "tickets-without-fulfillment")
        return list(selected.items())

    @staticmethod
    def _sanitize_error(exc):
        return "Error de reconciliación: operación no completada."

    def _summary(self, counts):
        self.stdout.write(
            "Resumen: procesados={processed} exitosos={succeeded} fallidos={failed} omitidos={skipped}".format(**counts)
        )
