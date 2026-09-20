from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from panel.models import AuditEvent
from sales.exceptions import BookingExpiredError, InvalidBookingError
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingStatus,
    SeatAssignment,
)
from sales.validators import validate_aware_datetime
from .exceptions import (
    InvalidPaymentStatusError,
    PaymentDuplicateError,
    PaymentError,
)
from .models import Payment, PaymentMethod, PaymentStatus
from .storage import validate_voucher_file


def calculate_booking_total(booking):
    """Calcula el total a pagar de la reserva a partir de los precios históricos de butacas.

    Suma exclusivamente las asignaciones activas (HELD o CONFIRMED).
    """
    total = SeatAssignment.objects.filter(
        leg__booking=booking,
        status__in=[AssignmentStatus.HELD, AssignmentStatus.CONFIRMED],
    ).aggregate(total=models.Sum("price"))["total"]
    return total or Decimal("0.00")


def _is_payment_collision_integrity_error(exc):
    """Determina si un IntegrityError corresponde a una colisión en la restricción
    'payments_active_booking_unique' en PostgreSQL o SQLite.
    """
    cause = getattr(exc, "__cause__", None)
    diag = getattr(cause, "diag", None) if cause is not None else getattr(exc, "diag", None)
    if diag is not None and getattr(diag, "constraint_name", None) == "payments_active_booking_unique":
        return True

    msg = str(exc).strip().lower()
    if "payments_active_booking_unique" in msg:
        return True
    if "unique constraint failed" in msg and "payments_payment.booking_id" in msg:
        return True

    return False


def validate_payment_agent(user):
    """Valida que el usuario tenga rol autorizado para registrar o revisar pagos."""
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        raise ValidationError("El usuario debe estar autenticado y activo.")
    if user.is_superuser or user.groups.filter(name__in=["Administrador", "Vendedor"]).exists():
        return True
    raise ValidationError("No tenés permiso para operar con pagos.")


def _enqueue_ticket_fulfillment(booking):
    """Encola fulfillment dentro de la transacción de confirmación sin ciclo de imports."""
    from tickets.services import enqueue_booking_fulfillment

    return enqueue_booking_fulfillment(booking)


def register_cash_payment(*, booking_or_id, seller, reference="", now=None):
    payment, expired_error = _register_cash_payment_atomic(
        booking_or_id=booking_or_id, seller=seller, reference=reference, now=now
    )
    if expired_error is not None:
        raise expired_error
    return payment


@transaction.atomic
def _register_cash_payment_atomic(*, booking_or_id, seller, reference="", now=None):
    """Registra un pago en efectivo para una reserva manual HELD y la confirma atómicamente.

    - Exige canal MANUAL y estado HELD vigente.
    - Calcula el importe exacto en el servidor sumando los snapshots de SeatAssignment.
    - Bloquea primero Booking con select_for_update().
    - Crea el pago en estado APPROVED con reviewed_by=seller y reviewed_at=now.
    - Confirma la reserva y sus asignaciones atómicamente.
    - Registra auditoría en panel.AuditEvent.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    validate_payment_agent(seller)
    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id

    # 1. Bloquear Booking primero para evitar deadlocks
    booking = Booking.objects.select_for_update().get(pk=booking_id)

    if booking.channel != BookingChannel.MANUAL:
        raise InvalidBookingError("Solo las reservas manuales admiten registro de pago en efectivo.")

    if booking.status == BookingStatus.CONFIRMED:
        raise InvalidBookingError("La reserva ya se encuentra confirmada.")
    elif booking.status == BookingStatus.EXPIRED:
        raise BookingExpiredError("La reserva ha expirado y no se puede pagar.")
    elif booking.status == BookingStatus.RELEASED:
        raise InvalidBookingError("La reserva ha sido liberada y no se puede pagar.")
    elif booking.status != BookingStatus.HELD:
        raise InvalidBookingError(f"Estado de reserva no válido para pago: {booking.status}")

    # Verificar vigencia
    if booking.expires_at <= effective_now:
        booking.status = BookingStatus.EXPIRED
        booking.full_clean()
        booking.save(update_fields=["status", "updated_at"])
        SeatAssignment.objects.filter(
            leg__booking=booking,
            status=AssignmentStatus.HELD,
        ).update(
            status=AssignmentStatus.RELEASED,
            updated_at=effective_now,
        )
        if isinstance(booking_or_id, Booking):
            booking_or_id.status = booking.status
            booking_or_id.updated_at = booking.updated_at
        return None, BookingExpiredError("La reserva ha expirado y no se puede registrar el pago.")

    # 2. Verificar que no exista ya un pago activo (UNDER_REVIEW o APPROVED)
    if Payment.objects.filter(
        booking=booking,
        status__in=[PaymentStatus.UNDER_REVIEW, PaymentStatus.APPROVED],
    ).exists():
        raise PaymentDuplicateError("Ya existe un pago en revisión o aprobado para esta reserva.")

    # 3. Calcular importe server-side
    total_amount = calculate_booking_total(booking)
    if total_amount <= Decimal("0.00"):
        raise PaymentError("El importe de la reserva debe ser mayor que cero.")

    # 4. Crear Payment en estado APPROVED
    payment = Payment(
        booking=booking,
        method=PaymentMethod.CASH,
        status=PaymentStatus.APPROVED,
        amount=total_amount,
        currency="ARS",
        reference=reference.strip() if reference else "",
        registered_by=seller,
        reviewed_by=seller,
        reviewed_at=effective_now,
    )
    payment.full_clean()

    try:
        payment.save()
    except IntegrityError as exc:
        if _is_payment_collision_integrity_error(exc):
            raise PaymentDuplicateError("Ya existe un pago en revisión o aprobado para esta reserva.") from exc
        raise

    # 5. Confirmar Booking y butacas atómicamente
    booking.status = BookingStatus.CONFIRMED
    booking.confirmed_at = effective_now
    booking.full_clean()
    booking.save(update_fields=["status", "confirmed_at", "updated_at"])

    SeatAssignment.objects.filter(
        leg__booking=booking,
        status=AssignmentStatus.HELD,
    ).update(
        status=AssignmentStatus.CONFIRMED,
        updated_at=effective_now,
    )

    # 6. Sincronizar en memoria si se recibió objeto
    if isinstance(booking_or_id, Booking):
        booking_or_id.status = booking.status
        booking_or_id.confirmed_at = booking.confirmed_at
        booking_or_id.updated_at = booking.updated_at

    # 7. Registrar eventos de auditoría
    AuditEvent.objects.create(
        actor=seller,
        action=AuditEvent.Action.CREATE,
        entity_type=Payment._meta.label,
        entity_id=str(payment.pk),
        description=f"Registro de pago en efectivo por {payment.currency} {payment.amount} para reserva {booking.public_id}",
        after={
            "public_id": str(payment.public_id),
            "booking_id": str(booking.public_id),
            "method": payment.method,
            "status": payment.status,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "reference": payment.reference,
            "registered_by": seller.username,
        },
    )

    AuditEvent.objects.create(
        actor=seller,
        action=AuditEvent.Action.UPDATE,
        entity_type=booking._meta.label,
        entity_id=str(booking.pk),
        description=f"Confirmación económica de reserva {booking.public_id} por pago en efectivo",
        before={"status": BookingStatus.HELD},
        after={
            "status": BookingStatus.CONFIRMED,
            "confirmed_at": effective_now.isoformat(),
        },
    )

    _enqueue_ticket_fulfillment(booking)

    return payment, None


def register_transfer_payment(*, booking_or_id, seller, voucher, reference="", now=None):
    payment, expired_error = _register_transfer_payment_atomic(
        booking_or_id=booking_or_id,
        seller=seller,
        voucher=voucher,
        reference=reference,
        now=now,
    )
    if expired_error is not None:
        raise expired_error
    return payment


@transaction.atomic
def _register_transfer_payment_atomic(*, booking_or_id, seller, voucher, reference="", now=None):
    """Registra una transferencia bancaria presentada para revisión.

    - Exige canal MANUAL y estado HELD vigente.
    - Voucher es obligatorio y validado (PDF, JPG, JPEG, PNG, max 10 MB).
    - Bloquea primero Booking con select_for_update().
    - Crea Payment en estado UNDER_REVIEW sin confirmar la reserva.
    - Registra auditoría en panel.AuditEvent.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    validate_payment_agent(seller)
    validate_voucher_file(voucher)

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id

    # 1. Bloquear Booking primero
    booking = Booking.objects.select_for_update().get(pk=booking_id)

    if booking.channel != BookingChannel.MANUAL:
        raise InvalidBookingError("Solo las reservas manuales admiten transferencias en el panel.")

    if booking.status == BookingStatus.CONFIRMED:
        raise InvalidBookingError("La reserva ya se encuentra confirmada.")
    elif booking.status == BookingStatus.EXPIRED:
        raise BookingExpiredError("La reserva ha expirado y no se puede presentar transferencia.")
    elif booking.status == BookingStatus.RELEASED:
        raise InvalidBookingError("La reserva ha sido liberada y no se puede presentar transferencia.")
    elif booking.status != BookingStatus.HELD:
        raise InvalidBookingError(f"Estado de reserva no válido para pago: {booking.status}")

    # Verificar vigencia
    if booking.expires_at <= effective_now:
        booking.status = BookingStatus.EXPIRED
        booking.full_clean()
        booking.save(update_fields=["status", "updated_at"])
        SeatAssignment.objects.filter(
            leg__booking=booking,
            status=AssignmentStatus.HELD,
        ).update(
            status=AssignmentStatus.RELEASED,
            updated_at=effective_now,
        )
        if isinstance(booking_or_id, Booking):
            booking_or_id.status = booking.status
            booking_or_id.updated_at = booking.updated_at
        return None, BookingExpiredError("La reserva ha expirado y no se puede presentar transferencia.")

    # 2. Verificar que no exista ya un pago activo (UNDER_REVIEW o APPROVED)
    if Payment.objects.filter(
        booking=booking,
        status__in=[PaymentStatus.UNDER_REVIEW, PaymentStatus.APPROVED],
    ).exists():
        raise PaymentDuplicateError("Ya existe un pago en revisión o aprobado para esta reserva.")

    # 3. Calcular importe server-side
    total_amount = calculate_booking_total(booking)
    if total_amount <= Decimal("0.00"):
        raise PaymentError("El importe de la reserva debe ser mayor que cero.")

    # 4. Crear Payment en estado UNDER_REVIEW
    payment = Payment(
        booking=booking,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.UNDER_REVIEW,
        amount=total_amount,
        currency="ARS",
        voucher=voucher,
        reference=reference.strip() if reference else "",
        registered_by=seller,
    )
    payment.full_clean()

    try:
        payment.save()
    except IntegrityError as exc:
        if _is_payment_collision_integrity_error(exc):
            raise PaymentDuplicateError("Ya existe un pago en revisión o aprobado para esta reserva.") from exc
        raise

    # 5. La reserva permanece HELD sin confirmar

    # 6. Auditoría
    AuditEvent.objects.create(
        actor=seller,
        action=AuditEvent.Action.CREATE,
        entity_type=Payment._meta.label,
        entity_id=str(payment.pk),
        description=f"Presentación de comprobante de transferencia bancaria por {payment.currency} {payment.amount} para reserva {booking.public_id}",
        after={
            "public_id": str(payment.public_id),
            "booking_id": str(booking.public_id),
            "method": payment.method,
            "status": payment.status,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "reference": payment.reference,
            "registered_by": seller.username,
        },
    )

    return payment, None


def review_transfer_payment(*, payment_or_id, reviewer, approved, rejection_reason="", now=None):
    payment, expired_error = _review_transfer_payment_atomic(
        payment_or_id=payment_or_id,
        reviewer=reviewer,
        approved=approved,
        rejection_reason=rejection_reason,
        now=now,
    )
    if expired_error is not None:
        raise expired_error
    return payment


@transaction.atomic
def _review_transfer_payment_atomic(*, payment_or_id, reviewer, approved, rejection_reason="", now=None):
    """Revisa una transferencia en estado UNDER_REVIEW: aprueba o rechaza.

    - Tanto Administrador como Vendedor pueden revisar.
    - Orden de bloqueos determinístico para evitar deadlocks: Booking primero, Payment después.
    - Si se aprueba:
      * Si la reserva ya expiró, no modifica Payment, vence la reserva y lanza BookingExpiredError.
      * Si está vigente, actualiza Payment a APPROVED y confirma Booking/butacas atómicamente.
    - Si se rechaza:
      * Exige motivo de rechazo obligatorio.
      * Deja la reserva en HELD si sigue vigente (no extiende vencimiento).
      * Deja Payment en REJECTED (permitiendo nuevo registro si la reserva no expiró).
    - Auditoría completa en panel.AuditEvent.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    validate_payment_agent(reviewer)

    if isinstance(payment_or_id, Payment):
        payment_id = payment_or_id.pk
        booking_id = payment_or_id.booking_id
    else:
        payment_id = payment_or_id
        booking_id = Payment.objects.filter(pk=payment_id).values_list("booking_id", flat=True).first()
        if not booking_id:
            raise Payment.DoesNotExist(f"No existe el pago con id {payment_id}")

    # 1. Bloquear Booking primero, Payment después
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    payment = Payment.objects.select_for_update().get(pk=payment_id)

    if payment.method != PaymentMethod.BANK_TRANSFER:
        raise InvalidPaymentStatusError("Solo las transferencias bancarias están sujetas a revisión.")

    if payment.status != PaymentStatus.UNDER_REVIEW:
        raise InvalidPaymentStatusError(
            f"La transferencia no está pendiente de revisión (estado actual: {payment.get_status_display()})."
        )

    if approved:
        # Verificar vencimiento de la reserva
        if booking.expires_at <= effective_now or booking.status == BookingStatus.EXPIRED:
            # Rechaza si vencida sin modificar Payment
            booking.status = BookingStatus.EXPIRED
            booking.full_clean()
            booking.save(update_fields=["status", "updated_at"])
            SeatAssignment.objects.filter(
                leg__booking=booking,
                status=AssignmentStatus.HELD,
            ).update(
                status=AssignmentStatus.RELEASED,
                updated_at=effective_now,
            )
            if isinstance(payment_or_id, Payment):
                payment_or_id.booking.status = booking.status
                payment_or_id.booking.updated_at = booking.updated_at
            return payment, BookingExpiredError("La reserva ha expirado y no se puede confirmar el pago.")

        if booking.status != BookingStatus.HELD:
            raise InvalidBookingError(
                f"La reserva no se encuentra en estado retenida (estado actual: {booking.get_status_display()})."
            )

        # Transicionar Payment a APPROVED
        payment.status = PaymentStatus.APPROVED
        payment.reviewed_by = reviewer
        payment.reviewed_at = effective_now
        payment.full_clean()
        payment.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

        # Confirmar reserva y butacas
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = effective_now
        booking.full_clean()
        booking.save(update_fields=["status", "confirmed_at", "updated_at"])

        SeatAssignment.objects.filter(
            leg__booking=booking,
            status=AssignmentStatus.HELD,
        ).update(
            status=AssignmentStatus.CONFIRMED,
            updated_at=effective_now,
        )

        if isinstance(payment_or_id, Payment):
            payment_or_id.status = payment.status
            payment_or_id.reviewed_by = payment.reviewed_by
            payment_or_id.reviewed_at = payment.reviewed_at
            payment_or_id.updated_at = payment.updated_at

        # Auditoría
        AuditEvent.objects.create(
            actor=reviewer,
            action=AuditEvent.Action.UPDATE,
            entity_type=Payment._meta.label,
            entity_id=str(payment.pk),
            description=f"Aprobación de transferencia bancaria por {payment.currency} {payment.amount} para reserva {booking.public_id}",
            before={"status": PaymentStatus.UNDER_REVIEW},
            after={
                "status": PaymentStatus.APPROVED,
                "reviewed_by": reviewer.username,
                "reviewed_at": effective_now.isoformat(),
            },
        )

        AuditEvent.objects.create(
            actor=reviewer,
            action=AuditEvent.Action.UPDATE,
            entity_type=booking._meta.label,
            entity_id=str(booking.pk),
            description=f"Confirmación económica de reserva {booking.public_id} por aprobación de transferencia",
            before={"status": BookingStatus.HELD},
            after={
                "status": BookingStatus.CONFIRMED,
                "confirmed_at": effective_now.isoformat(),
            },
        )

        _enqueue_ticket_fulfillment(booking)

    else:
        # Rechazar: motivo obligatorio
        reason = (rejection_reason or "").strip()
        if not reason:
            raise ValidationError({"rejection_reason": "El motivo de rechazo es obligatorio."})

        payment.status = PaymentStatus.REJECTED
        payment.reviewed_by = reviewer
        payment.reviewed_at = effective_now
        payment.rejection_reason = reason
        payment.full_clean()
        payment.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])

        # El rechazo no extiende vencimiento; si la reserva sigue vigente, queda en HELD
        # Si ya venció en este instante, se actualiza a EXPIRED
        if booking.status == BookingStatus.HELD and booking.expires_at <= effective_now:
            booking.status = BookingStatus.EXPIRED
            booking.full_clean()
            booking.save(update_fields=["status", "updated_at"])
            SeatAssignment.objects.filter(
                leg__booking=booking,
                status=AssignmentStatus.HELD,
            ).update(
                status=AssignmentStatus.RELEASED,
                updated_at=effective_now,
            )

        if isinstance(payment_or_id, Payment):
            payment_or_id.status = payment.status
            payment_or_id.reviewed_by = payment.reviewed_by
            payment_or_id.reviewed_at = payment.reviewed_at
            payment_or_id.rejection_reason = payment.rejection_reason
            payment_or_id.updated_at = payment.updated_at

        # Auditoría
        AuditEvent.objects.create(
            actor=reviewer,
            action=AuditEvent.Action.UPDATE,
            entity_type=Payment._meta.label,
            entity_id=str(payment.pk),
            description=f"Rechazo de comprobante de transferencia para reserva {booking.public_id}. Motivo: {reason}",
            before={"status": PaymentStatus.UNDER_REVIEW},
            after={
                "status": PaymentStatus.REJECTED,
                "reviewed_by": reviewer.username,
                "reviewed_at": effective_now.isoformat(),
                "rejection_reason": reason,
            },
        )

    return payment, None


def initiate_public_transfer_payment(*, booking_or_id, now=None):
    payment, expired_error = _initiate_public_transfer_payment_atomic(
        booking_or_id=booking_or_id, now=now
    )
    if expired_error is not None:
        raise expired_error
    return payment


@transaction.atomic
def _initiate_public_transfer_payment_atomic(*, booking_or_id, now=None):
    """Inicia o reutiliza un flujo de pago por transferencia bancaria para una reserva ONLINE HELD.

    - Exige canal ONLINE y estado HELD vigente.
    - Idempotente y concurrentemente seguro bajo select_for_update().
    - Si ya existe un Payment en AWAITING_VOUCHER:
        * Si venció el plazo de 5 minutos: expira la reserva, butacas y pago, y lanza BookingExpiredError.
        * Si está vigente: lo reutiliza y conserva expires_at y proof_deadline_at sin reiniciarlos en recargas.
    - Si existe un Payment en UNDER_REVIEW o APPROVED: lanza PaymentDuplicateError.
    - Si no existe:
        * Calcula el importe exclusivamente desde los snapshots de SeatAssignment.
        * Fija server-side la ventana de 5 minutos (PAYMENTS_PROOF_WINDOW_MINUTES).
        * Ajusta booking.expires_at al plazo de 5 minutos.
        * Crea y persiste el Payment en estado AWAITING_VOUCHER con registered_by=None.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id

    booking = Booking.objects.select_for_update().get(pk=booking_id)

    if booking.channel != BookingChannel.ONLINE:
        raise InvalidBookingError("Solo las reservas online admiten transferencia pública.")

    if booking.status == BookingStatus.CONFIRMED:
        raise InvalidBookingError("La reserva ya se encuentra confirmada.")
    elif booking.status in (BookingStatus.EXPIRED, BookingStatus.RELEASED):
        raise InvalidBookingError(f"La reserva no se encuentra activa (estado: {booking.get_status_display()}).")
    elif booking.status != BookingStatus.HELD:
        raise InvalidBookingError(f"Estado de reserva no válido para pago: {booking.status}")

    # Comprobación de vencimiento de la reserva
    if booking.expires_at <= effective_now:
        booking.status = BookingStatus.EXPIRED
        booking.full_clean()
        booking.save(update_fields=["status", "updated_at"])
        SeatAssignment.objects.filter(
            leg__booking=booking,
            status=AssignmentStatus.HELD,
        ).update(
            status=AssignmentStatus.RELEASED,
            updated_at=effective_now,
        )
        Payment.objects.filter(
            booking=booking,
            status=PaymentStatus.AWAITING_VOUCHER,
        ).update(
            status=PaymentStatus.EXPIRED,
            updated_at=effective_now,
        )
        if isinstance(booking_or_id, Booking):
            booking_or_id.status = booking.status
            booking_or_id.updated_at = booking.updated_at
        return None, BookingExpiredError("La reserva ha expirado y no se puede iniciar la transferencia.")

    # Verificar si ya existe un pago activo
    existing_payment = Payment.objects.select_for_update().filter(
        booking=booking,
        status__in=[
            PaymentStatus.AWAITING_VOUCHER,
            PaymentStatus.UNDER_REVIEW,
            PaymentStatus.APPROVED,
        ],
    ).order_by("-created_at", "-pk").first()

    if existing_payment:
        if existing_payment.status == PaymentStatus.APPROVED:
            raise PaymentDuplicateError("Esta reserva ya cuenta con un pago aprobado.")
        elif existing_payment.status == PaymentStatus.UNDER_REVIEW:
            raise PaymentDuplicateError("El comprobante de transferencia ya fue subido y está en revisión.")
        elif existing_payment.status == PaymentStatus.AWAITING_VOUCHER:
            # Comprobar si venció la ventana del comprobante
            if existing_payment.proof_deadline_at and existing_payment.proof_deadline_at <= effective_now:
                existing_payment.status = PaymentStatus.EXPIRED
                existing_payment.full_clean()
                existing_payment.save(update_fields=["status", "updated_at"])
                booking.status = BookingStatus.EXPIRED
                booking.full_clean()
                booking.save(update_fields=["status", "updated_at"])
                SeatAssignment.objects.filter(
                    leg__booking=booking,
                    status=AssignmentStatus.HELD,
                ).update(
                    status=AssignmentStatus.RELEASED,
                    updated_at=effective_now,
                )
                if isinstance(booking_or_id, Booking):
                    booking_or_id.status = booking.status
                    booking_or_id.updated_at = booking.updated_at
                return None, BookingExpiredError("El plazo de 5 minutos para subir el comprobante ha expirado.")

            # Reutilizar sin reiniciar la ventana
            if isinstance(booking_or_id, Booking):
                booking_or_id.expires_at = booking.expires_at
                booking_or_id.updated_at = booking.updated_at
            return existing_payment, None

    # No existe pago activo: calcular total server-side exclusivamente desde snapshots
    total_amount = calculate_booking_total(booking)
    if total_amount <= Decimal("0.00"):
        raise PaymentError("El importe de la reserva debe ser mayor que cero.")

    window_minutes = getattr(settings, "PAYMENTS_PROOF_WINDOW_MINUTES", 5)
    proof_deadline = effective_now + timedelta(minutes=window_minutes)

    # Ajustar expires_at de la reserva a la ventana del comprobante
    booking.expires_at = proof_deadline
    booking.full_clean()
    booking.save(update_fields=["expires_at", "updated_at"])

    payment = Payment(
        booking=booking,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.AWAITING_VOUCHER,
        amount=total_amount,
        currency="ARS",
        proof_deadline_at=proof_deadline,
        registered_by=None,
    )
    payment.full_clean()
    try:
        payment.save()
    except IntegrityError as exc:
        if _is_payment_collision_integrity_error(exc):
            collision_payment = Payment.objects.filter(
                booking=booking,
                status__in=[PaymentStatus.AWAITING_VOUCHER, PaymentStatus.UNDER_REVIEW, PaymentStatus.APPROVED],
            ).first()
            if collision_payment:
                return collision_payment, None
            raise PaymentDuplicateError("Ya existe un pago activo para esta reserva.") from exc
        raise

    if isinstance(booking_or_id, Booking):
        booking_or_id.expires_at = booking.expires_at
        booking_or_id.updated_at = booking.updated_at

    return payment, None


def upload_public_transfer_voucher(*, booking_or_id, voucher_file, now=None):
    payment, expired_error = _upload_public_transfer_voucher_atomic(
        booking_or_id=booking_or_id,
        voucher_file=voucher_file,
        now=now,
    )
    if expired_error is not None:
        raise expired_error
    return payment


@transaction.atomic
def _upload_public_transfer_voucher_atomic(*, booking_or_id, voucher_file, now=None):
    """Carga el comprobante de transferencia bancaria para una reserva online HELD.

    - Exige canal ONLINE y estado HELD vigente.
    - Se realiza solo dentro de la ventana de 5 minutos (proof_deadline_at).
    - Valida contenido real y extensión (PDF, JPG, JPEG, PNG, max 10 MB).
    - En la misma transacción:
        * Payment pasa a UNDER_REVIEW con el comprobante guardado.
        * Booking permanece HELD.
        * expires_at se extiende 24 horas desde la carga (review_deadline_at).
        * Registra auditoría en panel.AuditEvent.
    - Si la carga o validación falla: la transacción se revierte completa sin cambiar estado ni plazo.
    - No permite reemplazar comprobante si ya está en UNDER_REVIEW.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    if not voucher_file:
        raise ValidationError({"voucher": "El archivo de comprobante es obligatorio."})

    # Validar archivo (extensión, tamaño máximo y magic bytes de contenido)
    validate_voucher_file(voucher_file)

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id

    # 1. Bloquear Booking y luego Payment en orden estable
    booking = Booking.objects.select_for_update().get(pk=booking_id)

    if booking.channel != BookingChannel.ONLINE:
        raise InvalidBookingError("Solo las reservas online admiten carga pública de comprobante.")

    if booking.status == BookingStatus.CONFIRMED:
        raise InvalidBookingError("La reserva ya se encuentra confirmada.")
    elif booking.status in (BookingStatus.EXPIRED, BookingStatus.RELEASED):
        raise InvalidBookingError(f"La reserva no se encuentra activa (estado: {booking.get_status_display()}).")
    elif booking.status != BookingStatus.HELD:
        raise InvalidBookingError(f"Estado de reserva no válido para carga de comprobante: {booking.status}")

    # Buscar el pago activo de la reserva
    payment = Payment.objects.select_for_update().filter(
        booking=booking,
        method=PaymentMethod.BANK_TRANSFER,
        status__in=[
            PaymentStatus.AWAITING_VOUCHER,
            PaymentStatus.UNDER_REVIEW,
            PaymentStatus.APPROVED,
        ],
    ).order_by("-created_at", "-pk").first()

    if not payment:
        raise InvalidPaymentStatusError("No existe una transferencia iniciada para esta reserva.")

    if payment.status == PaymentStatus.UNDER_REVIEW:
        raise InvalidPaymentStatusError("El comprobante ya fue subido y se encuentra en revisión. No se puede reemplazar.")

    if payment.status == PaymentStatus.APPROVED:
        raise InvalidPaymentStatusError("La reserva ya cuenta con un pago aprobado.")

    if payment.status != PaymentStatus.AWAITING_VOUCHER:
        raise InvalidPaymentStatusError(f"El estado del pago ({payment.get_status_display()}) no permite cargar comprobante.")

    # 2. Verificar vencimiento del plazo de 5 minutos (proof_deadline_at o booking.expires_at)
    is_expired = False
    if payment.proof_deadline_at and effective_now >= payment.proof_deadline_at:
        is_expired = True
    elif effective_now >= booking.expires_at:
        is_expired = True

    if is_expired:
        payment.status = PaymentStatus.EXPIRED
        payment.full_clean()
        payment.save(update_fields=["status", "updated_at"])
        booking.status = BookingStatus.EXPIRED
        booking.full_clean()
        booking.save(update_fields=["status", "updated_at"])
        SeatAssignment.objects.filter(
            leg__booking=booking,
            status=AssignmentStatus.HELD,
        ).update(
            status=AssignmentStatus.RELEASED,
            updated_at=effective_now,
        )
        if isinstance(booking_or_id, Booking):
            booking_or_id.status = booking.status
            booking_or_id.updated_at = booking.updated_at
        return None, BookingExpiredError("El plazo de 5 minutos para subir el comprobante ha expirado.")

    # 3. Guardar comprobante y extender a 24 horas
    review_hours = getattr(settings, "PAYMENTS_REVIEW_WINDOW_HOURS", 24)
    review_deadline = effective_now + timedelta(hours=review_hours)

    payment.voucher = voucher_file
    payment.status = PaymentStatus.UNDER_REVIEW
    payment.review_deadline_at = review_deadline
    payment.full_clean()
    payment.save(update_fields=["voucher", "status", "review_deadline_at", "updated_at"])

    booking.expires_at = review_deadline
    booking.full_clean()
    booking.save(update_fields=["expires_at", "updated_at"])

    if isinstance(booking_or_id, Booking):
        booking_or_id.expires_at = booking.expires_at
        booking_or_id.updated_at = booking.updated_at

    # 4. Auditoría
    AuditEvent.objects.create(
        actor=None,
        action=AuditEvent.Action.UPDATE,
        entity_type=Payment._meta.label,
        entity_id=str(payment.pk),
        description=f"Carga de comprobante de transferencia online para reserva {booking.public_id}. Revisión fijada hasta {review_deadline.isoformat()}.",
        before={"status": PaymentStatus.AWAITING_VOUCHER},
        after={
            "status": PaymentStatus.UNDER_REVIEW,
            "review_deadline_at": review_deadline.isoformat(),
            "expires_at": booking.expires_at.isoformat(),
        },
    )

    return payment, None


@transaction.atomic
def expire_public_transfer_if_expired(booking, now=None):
    """Expiración oportunista para reservas online y sus transferencias asociadas.

    Si la reserva está en HELD y ha superado su plazo de expiración (15m iniciales,
    5m de carga de comprobante o 24h de revisión), libera las butacas mediante el
    servicio de dominio de sales y transiciona cualquier Payment activo a EXPIRED.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    booking_id = booking.pk if isinstance(booking, Booking) else booking
    locked_booking = Booking.objects.select_for_update().get(pk=booking_id)
    if locked_booking.status == BookingStatus.HELD and locked_booking.expires_at <= effective_now:
        locked_booking.status = BookingStatus.EXPIRED
        locked_booking.full_clean()
        locked_booking.save(update_fields=["status", "updated_at"])
        SeatAssignment.objects.filter(
            leg__booking=locked_booking,
            status=AssignmentStatus.HELD,
        ).update(status=AssignmentStatus.RELEASED, updated_at=effective_now)
        Payment.objects.filter(
            booking=locked_booking,
            status__in=[PaymentStatus.AWAITING_VOUCHER, PaymentStatus.UNDER_REVIEW],
        ).update(status=PaymentStatus.EXPIRED, updated_at=effective_now)
        if isinstance(booking, Booking):
            booking.status = locked_booking.status
            booking.updated_at = locked_booking.updated_at
        return True
    return False
