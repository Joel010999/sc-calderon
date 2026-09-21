"""Servicios de dominio para la emisión, entrega y anulación de pasajes."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import models, transaction
from django.utils import timezone

from sales.models import AssignmentStatus, Booking, BookingStatus, SeatAssignment
from .exceptions import (
    InvalidTicketError,
    TicketEmailDuplicateError,
    TicketEmailError,
    TicketEmailPendingError,
    TicketIssuanceError,
    TicketNotFoundError,
    TicketVoidError,
)
from .models import (
    EmailAttemptStatus,
    FulfillmentEmailStatus,
    FulfillmentIssueStatus,
    Ticket,
    TicketAuditEvent,
    TicketEmailAttempt,
    TicketFulfillment,
    TicketStatus,
    mask_document,
)
from .rendering import TicketData, build_ticket_pdf
from .storage import get_ticket_storage


def generate_ticket_code(booking: Booking, leg, passenger) -> str:
    """Genera un código único y legible para el pasaje.

    Formato: TK-<BOOKING_SHORT>-L<LEG_SEQ>-P<PAX_POS>
    Ejemplo: TK-3F4A1B2C-L1-P1
    """
    short_uuid = str(booking.public_id).replace("-", "")[:8].upper()
    return f"TK-{short_uuid}-L{leg.sequence}-P{passenger.position}"


def build_verification_url(raw_verification_token: str) -> str:
    """Construye la URL absoluta y confiable de verificación para el código QR.

    Utiliza exclusivamente la configuración del servidor, nunca cabeceras Host de la petición.
    """
    base_url = getattr(
        settings,
        "TICKETS_VERIFICATION_BASE_URL",
        getattr(settings, "SITE_URL", "https://scviajes.com.ar"),
    ).rstrip("/")
    return f"{base_url}/tickets/verify/?token={raw_verification_token}"


def issue_tickets_for_booking(booking_or_id, now=None) -> list[Ticket]:
    """Emite atómicamente todos los pasajes para una reserva confirmada.

    Contrato de durabilidad y limpieza de archivos:
    - Debe ejecutarse como servicio top-level; rechaza anidamiento dentro de transacciones
      activas previas para garantizar que una reversión externa no deje archivos huérfanos.
    - Si falla cualquier paso (PDF, storage, DB, commit o auditoría), revierte la transacción
      de base de datos y borra físicamente cada archivo PDF creado.
    - Idempotente: si todos los pasajes de la reserva ya están en ISSUED, los retorna sin duplicar.
    - Rechaza estrictamente reservas con pasajes en estado VOID o estados parciales/inconsistentes.
    """
    if transaction.get_connection().in_atomic_block:
        raise InvalidTicketError(
            "issue_tickets_for_booking debe ejecutarse como servicio top-level fuera de "
            "bloques atómicos activos para garantizar la limpieza atómica de archivos."
        )

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    effective_now = now or timezone.now()

    created_files = []
    storage = get_ticket_storage()
    issued_tickets = []

    try:
        with transaction.atomic():
            booking = Booking.objects.select_for_update().get(pk=booking_id)

            if booking.status != BookingStatus.CONFIRMED:
                raise InvalidTicketError(
                    f"Solo se pueden emitir pasajes para reservas confirmadas (estado actual: {booking.get_status_display()})."
                )

            # Comprobar pasajes existentes para idempotencia estricta
            existing_tickets = list(
                Ticket.objects.filter(booking=booking).order_by("leg__sequence", "passenger__position")
            )
            if existing_tickets:
                if any(t.status == TicketStatus.VOID for t in existing_tickets):
                    raise TicketVoidError("No se pueden reemitir pasajes de una reserva con pasajes anulados (VOID).")

                legs_count = booking.legs.count()
                passengers_count = booking.passengers.count()
                expected_count = legs_count * passengers_count

                if len(existing_tickets) == expected_count and all(t.status == TicketStatus.ISSUED for t in existing_tickets):
                    return existing_tickets

                raise InvalidTicketError("Estado inconsistente de pasajes existentes para la reserva.")

            legs = list(booking.legs.order_by("sequence"))
            passengers = list(booking.passengers.order_by("position"))
            expected_count = len(legs) * len(passengers)

            assignments = list(
                SeatAssignment.objects.filter(leg__booking=booking).select_related(
                    "leg", "passenger", "seat", "trip"
                )
            )

            if len(assignments) != expected_count:
                raise InvalidTicketError(
                    f"La cantidad de asignaciones ({len(assignments)}) no coincide con la matriz "
                    f"exacta de tramos x pasajeros ({expected_count})."
                )

            if any(a.status != AssignmentStatus.CONFIRMED for a in assignments):
                raise InvalidTicketError("Todas las asignaciones de butaca deben estar confirmadas para emitir pasajes.")

            # Validación de relaciones cruzadas
            for a in assignments:
                if a.leg.booking_id != booking.id:
                    raise InvalidTicketError("Asignación de butaca vinculada a una reserva cruzada.")
                if a.passenger.booking_id != booking.id:
                    raise InvalidTicketError("Asignación de butaca vinculada a un pasajero de otra reserva.")

            for leg in legs:
                for passenger in passengers:
                    assignment = next(
                        (a for a in assignments if a.leg_id == leg.id and a.passenger_id == passenger.id),
                        None,
                    )
                    if not assignment:
                        raise InvalidTicketError(
                            f"Falta asignación de butaca para el tramo {leg.sequence} y pasajero {passenger.position}."
                        )

                    # Generar tokens criptográficos seguros (>= 256 bits de entropía)
                    raw_verification_token = secrets.token_urlsafe(32)
                    raw_download_token = secrets.token_urlsafe(32)

                    v_hash = hashlib.sha256(raw_verification_token.encode("utf-8")).hexdigest()
                    d_hash = hashlib.sha256(raw_download_token.encode("utf-8")).hexdigest()

                    ticket_code = generate_ticket_code(booking, leg, passenger)
                    qr_url = build_verification_url(raw_verification_token)

                    masked_doc = mask_document(passenger.document_type, passenger.document_number)
                    cat_display = assignment.get_category_display()
                    p_name = f"{passenger.first_name} {passenger.last_name}".strip()
                    if not p_name:
                        p_name = f"Pasajero {passenger.position}"

                    # Preparar estructura desacoplada de datos para el PDF
                    ticket_data = TicketData(
                        ticket_code=ticket_code,
                        booking_code=str(booking.public_id),
                        passenger_name=p_name,
                        masked_document=masked_doc,
                        origin_stop_name=leg.origin_stop_name,
                        destination_stop_name=leg.destination_stop_name,
                        travel_date=leg.departure_at.strftime("%d/%m/%Y"),
                        departure_time=leg.departure_at.strftime("%H:%M"),
                        boarding_place=leg.origin_stop_name,
                        arrival_time=leg.arrival_at.strftime("%H:%M"),
                        seat_number=str(assignment.seat_number),
                        category_display=cat_display,
                        price_display=(
                            f"{assignment.currency} {assignment.price:,.2f}"
                            .replace(",", "X")
                            .replace(".", ",")
                            .replace("X", ".")
                        ),
                        issued_at=effective_now.strftime("%d/%m/%Y %H:%M"),
                        qr_payload_url=qr_url,
                    )

                    # Renderizar PDF y guardar en almacenamiento privado
                    pdf_bytes = build_ticket_pdf(ticket_data)
                    rel_path = f"tickets/{uuid.uuid4().hex}.pdf"
                    storage.save(rel_path, ContentFile(pdf_bytes))
                    created_files.append(rel_path)

                    ticket = Ticket(
                        booking=booking,
                        leg=leg,
                        passenger=passenger,
                        seat_assignment=assignment,
                        ticket_code=ticket_code,
                        verification_token_hash=v_hash,
                        download_token_hash=d_hash,
                        status=TicketStatus.ISSUED,
                        pdf_path=rel_path,
                        issued_at=effective_now,
                        passenger_name=ticket_data.passenger_name,
                        passenger_document_masked=masked_doc,
                        origin_stop_name=leg.origin_stop_name,
                        destination_stop_name=leg.destination_stop_name,
                        departure_at=leg.departure_at,
                        arrival_at=leg.arrival_at,
                        seat_number=assignment.seat_number,
                        seat_category=assignment.category,
                        seat_category_display=cat_display,
                        price=assignment.price,
                        currency=assignment.currency,
                        booking_public_id=booking.public_id,
                    )
                    ticket.full_clean()
                    ticket.save()

                    # Conservar tokens en memoria exclusivamente para el llamador inmediato
                    ticket._raw_verification_token = raw_verification_token
                    ticket._raw_download_token = raw_download_token

                    issued_tickets.append(ticket)

            # Registro de auditoría propio de tickets sin PII
            TicketAuditEvent.objects.create(
                actor=None,
                action=TicketAuditEvent.Action.ISSUE,
                booking=booking,
                description=f"Emisión exitosa de {len(issued_tickets)} pasajes para reserva {booking.public_id}",
                metadata={
                    "booking_public_id": str(booking.public_id),
                    "ticket_count": len(issued_tickets),
                    "ticket_codes": [t.ticket_code for t in issued_tickets],
                },
            )

    except Exception:
        # En caso de cualquier excepción (incluso en commit o auditoría), borrar todos los archivos creados
        for fpath in created_files:
            try:
                storage.delete(fpath)
            except Exception:
                pass
        raise

    return issued_tickets


def send_booking_tickets(booking_or_id, retry=False, now=None) -> TicketEmailAttempt:
    """Envía un único correo con todos los pasajes en PDF adjuntos al comprador.

    Reglas de concurrencia e idempotencia:
    - Reserva debe estar CONFIRMED.
    - Emite los pasajes idempotentemente si no fueron emitidos aún.
    - Si existe un intento SENT previo, retorna ese intento sin reenviar.
    - Si existe un intento PENDING, rechaza para evitar envíos concurrentes duplicados.
    - Si el último intento fue FAILED, requiere retry=True explícito para reintentar.
    - Persiste el intento PENDING en base de datos ANTES de realizar I/O de red.
    - El envío se realiza fuera de bloques de transacción para evitar correos fantasma si hay rollback.
    """
    if transaction.get_connection().in_atomic_block:
        raise InvalidTicketError(
            "send_booking_tickets no debe invocarse dentro de una transacción activa para no emitir correos que luego sufran rollback."
        )

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    effective_now = now or timezone.now()

    booking = Booking.objects.get(pk=booking_id)
    if booking.status != BookingStatus.CONFIRMED:
        raise InvalidTicketError(
            f"Solo se pueden enviar pasajes para reservas confirmadas (estado actual: {booking.get_status_display()})."
        )

    # 1. Asegurar emisión idempotente de pasajes
    tickets = issue_tickets_for_booking(booking, now=effective_now)
    if not tickets:
        raise TicketIssuanceError("No hay pasajes para adjuntar al correo de la reserva.")

    # 2. Reclamar intento PENDING de forma atómica
    with transaction.atomic():
        locked_booking = Booking.objects.select_for_update().get(pk=booking_id)

        # Comprobar intentos existentes
        existing_sent = TicketEmailAttempt.objects.filter(
            booking=locked_booking, status=EmailAttemptStatus.SENT
        ).first()
        if existing_sent:
            return existing_sent

        existing_pending = TicketEmailAttempt.objects.filter(
            booking=locked_booking, status=EmailAttemptStatus.PENDING
        ).first()
        if existing_pending:
            raise TicketEmailPendingError(
                "Existe un intento de envío en progreso o ambiguo para esta reserva."
            )

        failed_attempts = list(
            TicketEmailAttempt.objects.filter(
                booking=locked_booking, status=EmailAttemptStatus.FAILED
            ).order_by("-attempted_at")
        )
        if failed_attempts and not retry:
            raise TicketEmailError(
                "El envío anterior falló. Para reintentar debe especificarse retry=True explícitamente."
            )

        attempt = TicketEmailAttempt.objects.create(
            booking=locked_booking,
            status=EmailAttemptStatus.PENDING,
            recipient_email=locked_booking.email,
            attempted_at=effective_now,
        )

    # 3. Preparar y enviar correo fuera de transacción
    storage = get_ticket_storage()
    subject = f"Tus pasajes de SC Viajes - Reserva {str(booking.public_id)[:8].upper()}"

    body_lines = [
        "¡Gracias por confiar en SC Viajes!",
        "",
        f"Adjuntamos los pasajes electrónicos correspondientes a tu reserva {booking.public_id}.",
        "",
        "Información importante para tu viaje:",
        "- Cada pasajero debe presentarse en el punto de subida con su documento de identidad físico.",
        "- Te recomendamos llegar con al menos 15 minutos de anticipación al horario programado.",
        "- Podés verificar la validez de tu pasaje en cualquier momento escaneando el código QR impreso.",
        "",
        "SC Viajes · Córdoba ⇄ Jujuy",
    ]
    body = "\n".join(body_lines)

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "webmaster@localhost"),
        to=[booking.email],
    )

    for ticket in tickets:
        if not storage.exists(ticket.pdf_path):
            sanitized_error = "Uno o más archivos de pasaje no se encuentran disponibles en el almacenamiento."
            attempt.status = EmailAttemptStatus.FAILED
            attempt.error_message = sanitized_error
            attempt.completed_at = timezone.now()
            attempt.save(update_fields=["status", "error_message", "completed_at"])
            raise TicketEmailError(sanitized_error)

        with storage.open(ticket.pdf_path, "rb") as f:
            pdf_bytes = f.read()

        email.attach(f"pasaje-{ticket.ticket_code}.pdf", pdf_bytes, "application/pdf")

    try:
        email.send(fail_silently=False)
    except Exception as exc:
        sanitized_error = "Error al conectar con el servidor de correo electrónico."
        attempt.status = EmailAttemptStatus.FAILED
        attempt.error_message = sanitized_error
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["status", "error_message", "completed_at"])

        TicketAuditEvent.objects.create(
            actor=None,
            action=TicketAuditEvent.Action.EMAIL,
            booking=booking,
            description=f"Fallo en envío de pasajes a {booking.email}",
            metadata={"status": "FAILED", "error": sanitized_error},
        )
        raise TicketEmailError(sanitized_error) from exc

    attempt.status = EmailAttemptStatus.SENT
    attempt.completed_at = timezone.now()
    attempt.save(update_fields=["status", "completed_at"])

    TicketAuditEvent.objects.create(
        actor=None,
        action=TicketAuditEvent.Action.EMAIL,
        booking=booking,
        description=f"Envío exitoso de {len(tickets)} pasajes a {booking.email}",
        metadata={"status": "SENT", "ticket_count": len(tickets)},
    )

    return attempt


def enqueue_booking_fulfillment(booking_or_id, actor=None):
    """Crea o reutiliza el trabajo durable de una reserva confirmada.

    Debe llamarse dentro de la transacción que confirma el pago. El callback de
    ``on_commit`` solo intenta procesar el trabajo ya persistido; si el proceso
    cae antes, ``reconcile_confirmed_fulfillments`` puede recuperarlo.
    """
    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    job, _ = TicketFulfillment.objects.get_or_create(booking_id=booking_id)
    if job.issue_status == FulfillmentIssueStatus.SUCCEEDED and job.email_status == FulfillmentEmailStatus.SENT:
        return job

    from functools import partial
    transaction.on_commit(partial(process_booking_fulfillment, job.pk))
    return job


def process_booking_fulfillment(job_or_id, retry=False, actor=None, now=None):
    """Procesa emisión y correo fuera de la transacción de pago.

    Cada etapa conserva su resultado. Un error queda en el trabajo y no vuelve
    atrás el pago confirmado; el mismo trabajo puede reintentarse explícitamente.
    """
    job_id = job_or_id.pk if isinstance(job_or_id, TicketFulfillment) else job_or_id
    effective_now = now or timezone.now()
    stale_after = timedelta(seconds=getattr(settings, "TICKETS_FULFILLMENT_STALE_SECONDS", 900))
    with transaction.atomic():
        job = TicketFulfillment.objects.select_for_update().select_related("booking").get(pk=job_id)
        if job.next_attempt_at and job.next_attempt_at > effective_now and not retry:
            return job
        if job.booking.status != BookingStatus.CONFIRMED:
            raise InvalidTicketError("Solo se pueden procesar reservas confirmadas.")
        if job.issue_status == FulfillmentIssueStatus.SUCCEEDED and job.email_status == FulfillmentEmailStatus.SENT:
            return job
        if job.issue_status == FulfillmentIssueStatus.PROCESSING:
            lease_active = job.lease_until and job.lease_until > effective_now
            if lease_active:
                raise InvalidTicketError("El fulfillment ya se encuentra en proceso.")
        job.issue_status = FulfillmentIssueStatus.PROCESSING
        job.attempts += 1
        job.last_attempt_at = effective_now
        job.lease_until = effective_now + stale_after
        job.next_attempt_at = None
        job.issue_error = ""
        job.save(update_fields=["issue_status", "attempts", "last_attempt_at", "lease_until", "next_attempt_at", "issue_error", "updated_at"])

    try:
        issue_tickets_for_booking(job.booking_id, now=effective_now)
    except Exception:
        with transaction.atomic():
            job = TicketFulfillment.objects.select_for_update().get(pk=job_id)
            job.issue_status = FulfillmentIssueStatus.FAILED
            job.issue_error = "No se pudieron generar los pasajes. Reintentá desde el panel."
            job.next_attempt_at = effective_now + timedelta(seconds=getattr(settings, "TICKETS_RECONCILE_RETRY_DELAY_SECONDS", 0))
            job.lease_until = None
            job.save(update_fields=["issue_status", "issue_error", "next_attempt_at", "lease_until", "updated_at"])
        return job

    with transaction.atomic():
        job = TicketFulfillment.objects.select_for_update().get(pk=job_id)
        job.issue_status = FulfillmentIssueStatus.SUCCEEDED
        job.email_status = FulfillmentEmailStatus.PENDING
        job.issue_error = ""
        job.lease_until = effective_now + stale_after
        job.save(update_fields=["issue_status", "email_status", "issue_error", "lease_until", "updated_at"])

    try:
        send_booking_tickets(job.booking_id, retry=retry, now=effective_now)
    except TicketEmailPendingError:
        return TicketFulfillment.objects.get(pk=job_id)
    except Exception:
        with transaction.atomic():
            job = TicketFulfillment.objects.select_for_update().get(pk=job_id)
            job.email_status = FulfillmentEmailStatus.FAILED
            job.email_error = "No se pudo enviar el correo. Reintentá desde el panel."
            job.next_attempt_at = effective_now + timedelta(seconds=getattr(settings, "TICKETS_RECONCILE_RETRY_DELAY_SECONDS", 0))
            job.lease_until = None
            job.save(update_fields=["email_status", "email_error", "next_attempt_at", "lease_until", "updated_at"])
        return job

    with transaction.atomic():
        job = TicketFulfillment.objects.select_for_update().get(pk=job_id)
        job.email_status = FulfillmentEmailStatus.SENT
        job.email_error = ""
        job.completed_at = timezone.now()
        job.next_attempt_at = None
        job.lease_until = None
        job.save(update_fields=["email_status", "email_error", "completed_at", "next_attempt_at", "lease_until", "updated_at"])
        TicketAuditEvent.objects.create(
            actor=actor,
            action=TicketAuditEvent.Action.EMAIL,
            ticket=None,
            booking=job.booking,
            description=f"Fulfillment completado para reserva {job.booking.public_id}",
            metadata={"issue_status": job.issue_status, "email_status": job.email_status, "attempts": job.attempts},
        )
    return job


def reconcile_confirmed_fulfillments(limit=None):
    """Recupera trabajos pendientes/fallidos de reservas confirmadas."""
    qs = TicketFulfillment.objects.filter(booking__status=BookingStatus.CONFIRMED).filter(
        models.Q(issue_status__in=[FulfillmentIssueStatus.PENDING, FulfillmentIssueStatus.FAILED])
        | models.Q(email_status__in=[FulfillmentEmailStatus.PENDING, FulfillmentEmailStatus.FAILED])
        | models.Q(issue_status=FulfillmentIssueStatus.PROCESSING, lease_until__lte=timezone.now())
    ).order_by("updated_at", "pk")
    if limit:
        qs = qs[:limit]
    results = []
    for job in qs:
        results.append(process_booking_fulfillment(job.pk, retry=True))
    return results


def void_ticket(ticket_or_id, reason: str, actor=None, now=None) -> Ticket:
    """Anula un pasaje existente.

    Operación auditable que registra la fecha y motivo de anulación.
    No implementa cancelaciones comerciales ni reintegros.
    """
    ticket_id = ticket_or_id.pk if isinstance(ticket_or_id, Ticket) else ticket_or_id
    effective_now = now or timezone.now()
    clean_reason = (reason or "").strip()

    if not clean_reason:
        raise InvalidTicketError("El motivo de anulación es obligatorio.")

    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().get(pk=ticket_id)

        if ticket.status == TicketStatus.VOID:
            raise TicketVoidError("El pasaje ya se encuentra anulado.")

        ticket.status = TicketStatus.VOID
        ticket.voided_at = effective_now
        ticket.void_reason = clean_reason
        ticket.full_clean()
        ticket.save(update_fields=["status", "voided_at", "void_reason", "updated_at"])

        TicketAuditEvent.objects.create(
            actor=actor,
            action=TicketAuditEvent.Action.VOID,
            ticket=ticket,
            booking=ticket.booking,
            description=f"Anulación de pasaje {ticket.ticket_code}. Motivo: {clean_reason}",
            metadata={"ticket_code": ticket.ticket_code, "reason": clean_reason},
        )

    if isinstance(ticket_or_id, Ticket):
        ticket_or_id.status = ticket.status
        ticket_or_id.voided_at = ticket.voided_at
        ticket_or_id.void_reason = ticket.void_reason
        ticket_or_id.updated_at = ticket.updated_at

    return ticket
