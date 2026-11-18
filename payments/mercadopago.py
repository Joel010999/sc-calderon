"""Integración con Mercado Pago Checkout Pro para compras públicas.

Diseño:
- Deshabilitada por defecto (MERCADO_PAGO_ENABLED) y con credenciales solo por
  variables de entorno. Los secretos nunca se escriben en logs ni respuestas.
- La preferencia se crea desde el backend con importe y reserva calculados del
  lado servidor (snapshots de SeatAssignment vía calculate_booking_total).
- La confirmación exige una notificación con firma válida (x-signature,
  HMAC-SHA256 del manifest ``id:<data.id>;request-id:<x-request-id>;ts:<ts>;``)
  más una consulta autenticada a ``GET /v1/payments/{id}``. La URL de retorno
  del navegador jamás confirma un pago.
- Se valida identidad de la operación: collector_id (cuenta receptora),
  external_reference (reserva local), importe y moneda.
- Idempotente ante notificaciones duplicadas/concurrentes vía la unicidad de
  PaymentGatewayEvent y bloqueos select_for_update sobre Booking/Payment.
- Si la aprobación llega con la reserva vencida, se registra el conflicto para
  resolución operativa: jamás se reasigna una butaca ni se inventa una
  devolución automática.
"""

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from panel.models import AuditEvent
from sales.exceptions import BookingExpiredError, InvalidBookingError, SeatUnavailableError
from sales.models import AssignmentStatus, Booking, BookingChannel, BookingStatus, SeatAssignment
from sales.validators import validate_aware_datetime

from .exceptions import PaymentDuplicateError, PaymentError
from .models import Payment, PaymentGatewayEvent, PaymentMethod, PaymentStatus
from .services import _enqueue_ticket_fulfillment, calculate_booking_total

logger = logging.getLogger(__name__)

GATEWAY_PENDING_STATUSES = (
    PaymentStatus.AWAITING_VOUCHER,
    PaymentStatus.UNDER_REVIEW,
    PaymentStatus.PENDING,
    PaymentStatus.APPROVED,
)

MP_FINAL_REJECTED = {"rejected", "cancelled", "refunded", "charged_back"}
MP_PENDING_STATES = {"pending", "in_process", "authorized"}


def mercadopago_enabled():
    """True si la integración está habilitada por configuración."""
    return bool(getattr(settings, "MERCADO_PAGO_ENABLED", False))


def mercadopago_configured():
    """True si la integración puede operar (habilitada + credenciales)."""
    return bool(
        mercadopago_enabled()
        and getattr(settings, "MERCADO_PAGO_ACCESS_TOKEN", "")
        and getattr(settings, "MERCADO_PAGO_WEBHOOK_SECRET", "")
        and getattr(settings, "MERCADO_PAGO_COLLECTOR_ID", "")
    )


class MercadoPagoClient:
    """Cliente HTTP mínimo para la API de Mercado Pago (sin SDK).

    Timeout corto y secretos únicamente en headers de autorización.
    """

    def __init__(self, access_token=None, api_base=None, timeout=10):
        self.access_token = access_token or settings.MERCADO_PAGO_ACCESS_TOKEN
        self.api_base = api_base or getattr(
            settings, "MERCADO_PAGO_API_BASE", "https://api.mercadopago.com"
        )
        self.timeout = timeout

    def _request(self, method, path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.api_base}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # noqa: F821 - importado abajo
            raise PaymentError(
                f"Mercado Pago respondió con error HTTP {exc.code}."
            ) from exc
        except urllib.error.URLError as exc:
            raise PaymentError("No se pudo contactar a Mercado Pago.") from exc

    def create_preference(self, payload):
        return self._request("POST", "/checkout/preferences", payload)

    def get_payment(self, payment_id):
        return self._request("GET", f"/v1/payments/{payment_id}")


def _parse_signature_header(x_signature):
    parts = {}
    for chunk in (x_signature or "").split(","):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parts[key.strip()] = value.strip()
    return parts.get("ts"), parts.get("v1")


def verify_webhook_signature(x_signature, x_request_id, data_id, now=None):
    """Valida la firma HMAC-SHA256 de una notificación de Mercado Pago.

    Manifest: ``id:<data.id>;request-id:<x-request-id>;ts:<ts>;`` omitiendo los
    pares cuyo valor falte. Rechaza timestamps fuera de la tolerancia
    configurada (anti-replay) y compara en tiempo constante.
    """
    secret = getattr(settings, "MERCADO_PAGO_WEBHOOK_SECRET", "")
    if not secret:
        return False

    ts, v1 = _parse_signature_header(x_signature)
    if not ts or not v1 or not data_id:
        return False

    tolerance = getattr(settings, "MERCADO_PAGO_WEBHOOK_TOLERANCE_SECONDS", 300)
    effective_now = now or timezone.now()
    try:
        ts_num = float(ts)
    except (TypeError, ValueError):
        return False
    # Acepta segundos o milisegundos.
    ts_seconds = ts_num / 1000 if ts_num > 1e12 else ts_num
    if abs(effective_now.timestamp() - ts_seconds) > tolerance:
        return False

    pairs = []
    if data_id:
        pairs.append(f"id:{data_id}")
    if x_request_id:
        pairs.append(f"request-id:{x_request_id}")
    if ts:
        pairs.append(f"ts:{ts}")
    manifest = ";".join(pairs) + ";"

    expected = hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def _public_base_url():
    """Base pública absoluta para back_urls/notification_url (None si es local)."""
    base = getattr(settings, "PUBLIC_BASE_URL", "") or ""
    base = base.strip().rstrip("/")
    if base.startswith("https://") and "localhost" not in base and "127.0.0.1" not in base:
        return base
    return None


def build_preference_payload(booking, payment):
    """Arma el payload de la preferencia con datos calculados del lado servidor."""
    from django.urls import reverse

    items = [
        {
            "id": str(booking.public_id),
            "title": f"Pasaje SC Viajes {booking.public_id}",
            "quantity": 1,
            "currency_id": payment.currency,
            "unit_price": float(payment.amount),
        }
    ]
    payload = {
        "items": items,
        "external_reference": str(booking.public_id),
        "statement_descriptor": "SC VIAJES",
    }
    base = _public_base_url()
    if base:
        summary_url = f"{base}{reverse('resumen_reserva', args=[booking.public_id])}"
        payload["back_urls"] = {
            "success": summary_url,
            "pending": summary_url,
            "failure": summary_url,
        }
        payload["auto_return"] = "approved"
        payload["notification_url"] = f"{base}/webhooks/mercadopago/"
    return payload


@transaction.atomic
def initiate_mercadopago_payment(*, booking_or_id, now=None, client=None):
    """Inicia o reutiliza un pago de Checkout Pro para una reserva ONLINE HELD.

    - Exige canal ONLINE, estado HELD vigente y ausencia de pago activo.
    - Importe calculado exclusivamente desde snapshots server-side.
    - Reutiliza un Payment PENDING existente (sin crear preferencia nueva).
    - Devuelve ``(payment, init_point)``.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    if not mercadopago_configured():
        raise PaymentError("El pago con Mercado Pago no está disponible.")

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    booking = Booking.objects.select_for_update().get(pk=booking_id)

    if booking.channel != BookingChannel.ONLINE:
        raise InvalidBookingError("Solo las reservas online admiten Mercado Pago.")
    if booking.status == BookingStatus.CONFIRMED:
        raise InvalidBookingError("La reserva ya se encuentra confirmada.")
    if booking.status != BookingStatus.HELD:
        raise InvalidBookingError(f"Estado de reserva no válido para pago: {booking.status}")
    if booking.expires_at is not None and booking.expires_at <= effective_now:
        raise BookingExpiredError("La reserva ha expirado y no se puede iniciar el pago.")

    existing_payment = Payment.objects.select_for_update().filter(
        booking=booking,
        status__in=GATEWAY_PENDING_STATUSES,
    ).order_by("-created_at", "-pk").first()

    mp_client = client or MercadoPagoClient()

    if existing_payment:
        if existing_payment.status == PaymentStatus.APPROVED:
            raise PaymentDuplicateError("Esta reserva ya cuenta con un pago aprobado.")
        if existing_payment.status in (PaymentStatus.AWAITING_VOUCHER, PaymentStatus.UNDER_REVIEW):
            raise PaymentDuplicateError("Ya existe una transferencia en curso para esta reserva.")
        # PENDING de Mercado Pago: reutilizar la preferencia sin reiniciarla.
        if existing_payment.method == PaymentMethod.MERCADO_PAGO:
            init_point = existing_payment.reference or ""
            if not init_point:
                raise PaymentError("El pago pendiente no tiene preferencia asociada.")
            return existing_payment, init_point
        raise PaymentDuplicateError("Ya existe un pago activo para esta reserva.")

    total_amount = calculate_booking_total(booking)
    if total_amount <= Decimal("0.00"):
        raise PaymentError("El importe de la reserva debe ser mayor que cero.")

    payment = Payment(
        booking=booking,
        method=PaymentMethod.MERCADO_PAGO,
        status=PaymentStatus.PENDING,
        amount=total_amount,
        currency="ARS",
        registered_by=None,
    )
    payment.full_clean()
    try:
        payment.save()
    except IntegrityError as exc:
        collision = Payment.objects.filter(
            booking=booking, status__in=GATEWAY_PENDING_STATUSES
        ).first()
        if collision:
            raise PaymentDuplicateError("Ya existe un pago activo para esta reserva.") from exc
        raise

    try:
        preference = mp_client.create_preference(build_preference_payload(booking, payment))
    except Exception:
        # Si la preferencia falla, liberar el intento para permitir reintento.
        payment.delete()
        raise

    payment.external_preference_id = str(preference.get("id", ""))
    init_point = preference.get("init_point") or preference.get("sandbox_init_point") or ""
    payment.reference = init_point
    payment.save(update_fields=["external_preference_id", "reference", "updated_at"])

    return payment, init_point


def _apply_payment_state(payment, mp_payment, effective_now):
    """Aplica el estado verificado de Mercado Pago sobre Payment/Booking.

    Devuelve una tupla ``(outcome, detail)``. Las transiciones son idempotentes
    y seguras ante entregas duplicadas, concurrentes o fuera de orden.
    """
    mp_status = (mp_payment.get("status") or "").lower()

    with transaction.atomic():
        # Orden de bloqueo determinístico: Booking primero, Payment después.
        booking = Booking.objects.select_for_update().get(pk=payment.booking_id)
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        locked.external_payment_id = str(mp_payment.get("id", locked.external_payment_id))

        if locked.status == PaymentStatus.APPROVED and booking.status == BookingStatus.CONFIRMED:
            locked.save(update_fields=["external_payment_id", "updated_at"])
            return "ALREADY_CONFIRMED", "El pago ya estaba aprobado y la reserva confirmada."

        if locked.status in (PaymentStatus.REJECTED, PaymentStatus.EXPIRED):
            if mp_status == "approved":
                # Aprobación fuera de orden sobre intento cerrado: conflicto operativo.
                locked.conflict_reason = "approval_after_terminal_state"
                locked.save(update_fields=["external_payment_id", "conflict_reason", "updated_at"])
                return "CONFLICT_TERMINAL", "Aprobación tardía sobre un intento ya cerrado."
            locked.save(update_fields=["external_payment_id", "updated_at"])
            return "IGNORED", "Intento cerrado; notificación ignorada."

        if mp_status == "approved":
            expired = (
                booking.status == BookingStatus.EXPIRED
                or (booking.expires_at is not None and booking.expires_at <= effective_now)
            )
            if expired:
                # Dinero cobrado con reserva vencida: nunca reasignar la butaca.
                locked.status = PaymentStatus.APPROVED
                locked.reviewed_at = effective_now
                locked.conflict_reason = "approved_after_expiry"
                locked.full_clean()
                locked.save(update_fields=[
                    "status", "reviewed_at", "conflict_reason",
                    "external_payment_id", "updated_at",
                ])
                AuditEvent.objects.create(
                    actor=None,
                    action=AuditEvent.Action.UPDATE,
                    entity_type=Payment._meta.label,
                    entity_id=str(locked.pk),
                    description=(
                        f"Pago de Mercado Pago aprobado con la reserva {booking.public_id} "
                        "vencida: conflicto pendiente de resolución."
                    ),
                    before={"status": PaymentStatus.PENDING},
                    after={"status": PaymentStatus.APPROVED, "conflict": "approved_after_expiry"},
                )
                return "CONFLICT_LATE_APPROVAL", "Aprobado tras el vencimiento; requiere resolución."

            if booking.status != BookingStatus.HELD:
                locked.conflict_reason = f"approved_with_booking_{booking.status.lower()}"
                locked.save(update_fields=["external_payment_id", "conflict_reason", "updated_at"])
                return "CONFLICT_STATE", "Aprobado con reserva en estado no retenido."

            assignments = list(
                SeatAssignment.objects.select_for_update().filter(
                    leg__booking=booking, status=AssignmentStatus.HELD,
                ).select_related("seat", "trip")
            )
            for sa in assignments:
                conflicts = SeatAssignment.objects.filter(
                    trip=sa.trip, seat=sa.seat, status=AssignmentStatus.CONFIRMED,
                ).exclude(leg__booking=booking).exists()
                if conflicts:
                    locked.conflict_reason = f"seat_conflict:{sa.seat_number}"
                    locked.save(update_fields=["external_payment_id", "conflict_reason", "updated_at"])
                    return "CONFLICT_SEAT", f"Butaca {sa.seat_number} ya confirmada por otra reserva."

            locked.status = PaymentStatus.APPROVED
            locked.reviewed_at = effective_now
            locked.full_clean()
            locked.save(update_fields=["status", "reviewed_at", "external_payment_id", "updated_at"])

            booking.status = BookingStatus.CONFIRMED
            booking.confirmed_at = effective_now
            booking.full_clean()
            booking.save(update_fields=["status", "confirmed_at", "updated_at"])

            SeatAssignment.objects.filter(
                leg__booking=booking, status=AssignmentStatus.HELD,
            ).update(status=AssignmentStatus.CONFIRMED, updated_at=effective_now)

            AuditEvent.objects.create(
                actor=None,
                action=AuditEvent.Action.UPDATE,
                entity_type=booking._meta.label,
                entity_id=str(booking.pk),
                description=(
                    f"Confirmación económica de reserva {booking.public_id} "
                    f"por pago de Mercado Pago {locked.external_payment_id}"
                ),
                before={"status": BookingStatus.HELD},
                after={"status": BookingStatus.CONFIRMED, "confirmed_at": effective_now.isoformat()},
            )
            _enqueue_ticket_fulfillment(booking)
            return "CONFIRMED", "Pago aprobado; reserva confirmada."

        if mp_status in MP_FINAL_REJECTED:
            if locked.status == PaymentStatus.PENDING:
                locked.status = PaymentStatus.REJECTED
                locked.rejection_reason = mp_payment.get("status_detail") or mp_status
                locked.full_clean()
                locked.save(update_fields=[
                    "status", "rejection_reason", "external_payment_id", "updated_at",
                ])
                return "REJECTED", "Pago rechazado por la pasarela."
            locked.save(update_fields=["external_payment_id", "updated_at"])
            return "IGNORED", "Rechazo sobre intento no pendiente."

        # Estados intermedios (pending, in_process, authorized): no confirman.
        locked.save(update_fields=["external_payment_id", "updated_at"])
        return "STILL_PENDING", f"Estado en pasarela: {mp_status or 'desconocido'}."


def process_mercadopago_notification(*, data_id, action="", request_id=""):
    """Procesa una notificación de pago ya validada por firma.

    Deduplica por (provider, external_id, action), consulta el pago de forma
    autenticada y aplica la transición solo si coinciden cuenta receptora,
    reserva, importe y moneda. Devuelve ``(outcome, detail)``.
    """
    effective_now = timezone.now()

    try:
        with transaction.atomic():
            event = PaymentGatewayEvent.objects.create(
                provider=PaymentGatewayEvent.Provider.MERCADO_PAGO,
                external_id=str(data_id),
                action=action or "",
                request_id=request_id or "",
                signature_ok=True,
            )
    except IntegrityError:
        return "DUPLICATE", "Notificación ya procesada."

    outcome, detail = "ERROR", ""
    try:
        mp_payment = MercadoPagoClient().get_payment(data_id)

        # Identidad de la operación: cuenta receptora configurada.
        expected_collector = str(getattr(settings, "MERCADO_PAGO_COLLECTOR_ID", ""))
        if str(mp_payment.get("collector_id", "")) != expected_collector:
            outcome, detail = "MISMATCH", "collector_id no coincide con la cuenta configurada."
        else:
            external_reference = mp_payment.get("external_reference") or ""
            try:
                booking = Booking.objects.filter(public_id=external_reference).first()
            except (ValidationError, ValueError):
                booking = None
            if booking is None:
                outcome, detail = "UNKNOWN_BOOKING", "external_reference sin reserva local."
            else:
                payment = Payment.objects.filter(
                    booking=booking, method=PaymentMethod.MERCADO_PAGO,
                ).order_by("-created_at", "-pk").first()
                if payment is None:
                    outcome, detail = "UNKNOWN_PAYMENT", "No hay pago local para la referencia."
                else:
                    amount_ok = _amount_matches(mp_payment.get("transaction_amount"), payment.amount)
                    currency_ok = (mp_payment.get("currency_id") or "").upper() == payment.currency
                    if not (amount_ok and currency_ok):
                        outcome, detail = "MISMATCH", "Importe o moneda no coinciden con el pago local."
                    else:
                        outcome, detail = _apply_payment_state(payment, mp_payment, effective_now)
    except Exception as exc:  # noqa: BLE001 - el evento registra el fallo y MP reintenta
        logger.exception("Error procesando notificación de Mercado Pago %s", data_id)
        outcome, detail = "ERROR", exc.__class__.__name__

    PaymentGatewayEvent.objects.filter(pk=event.pk).update(outcome=outcome, detail=detail[:255])
    return outcome, detail


def _amount_matches(remote_amount, local_amount):
    try:
        remote_decimal = Decimal(str(remote_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return remote_decimal == local_amount.quantize(Decimal("0.01"))
