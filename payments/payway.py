"""Integración con Payway (Decidir2) para compras públicas ONLINE.

Diseño:
- Deshabilitada por defecto (PAYWAY_ENABLED) y con credenciales solo por
  variables de entorno. Los secretos y el token de pago nunca se escriben en
  logs ni en respuestas.
- Tokenización en el navegador con la API key pública (SDK oficial
  ``decidir.js``): el servidor nunca recibe ni almacena PAN ni CVV. Esto
  reduce el alcance de seguridad pero no lo elimina; ver ``docs/PAYWAY.md``.
- Cobro único (``payment_type: "single"``, una cuota), importe calculado
  exclusivamente del lado servidor (snapshots de SeatAssignment vía
  ``calculate_booking_total``) y enviado en unidades mínimas.
- Intento persistente antes de contactar al proveedor: el ``Payment`` local
  queda en ``PENDING`` con su ``site_transaction_id`` determinístico y un
  evento ``EXECUTE`` en ``PaymentGatewayEvent`` marca la ejecución. No se
  mantienen bloqueos de base de datos durante las llamadas de red.
- Resultado desconocido (timeout, error de red o respuesta perdida): el
  intento permanece ``PENDING`` y bloquea otro cobro de esa reserva hasta
  conciliar. La conciliación consulta el proveedor por
  ``siteOperationId`` (``GET /payments``), por lo que funciona aunque nunca
  hayamos recibido el id del pago.
- Nunca se confirma por el retorno del navegador: sólo por la respuesta del
  proveedor o por una consulta autenticada.
- Si la aprobación llega con la reserva vencida o en estado terminal, se
  registra el conflicto para resolución operativa: jamás se reasigna una
  butaca, se emite indebidamente ni se inventa una devolución.
"""

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
from sales.exceptions import BookingExpiredError, InvalidBookingError
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

PAYWAY_APPROVED_STATUS = "approved"
PAYWAY_REJECTED_STATUSES = {"rejected"}
# Estados intermedios documentados por el proveedor que no confirman ni
# rechazan definitivamente (p. ej. revisión antifraude).
PAYWAY_PENDING_PROVIDER_STATUSES = {
    "pending", "in_process", "authorized", "review", "pre_approved",
}

# Resultados del evento EXECUTE.
OUTCOME_AWAITING = "AWAITING_RESPONSE"
OUTCOME_UNKNOWN = "UNKNOWN_RESULT"
OUTCOME_VALIDATION_ERROR = "VALIDATION_ERROR"

# El único resultado que autoriza un nuevo POST de cobro: el proveedor
# rechazó el payload antes de procesarlo (no hubo cobro posible). Cualquier
# otro resultado (en curso, desconocido, pendiente, ambiguo o incompleto)
# mantiene el intento bloqueado hasta una resolución verificable.
RETRYABLE_EXECUTE_OUTCOMES = frozenset({OUTCOME_VALIDATION_ERROR})


def payway_enabled():
    """True si la integración está habilitada por configuración."""
    return bool(getattr(settings, "PAYWAY_ENABLED", False))


def payway_configured():
    """True si la integración puede operar (habilitada + credenciales)."""
    return bool(
        payway_enabled()
        and getattr(settings, "PAYWAY_PRIVATE_API_KEY", "")
        and getattr(settings, "PAYWAY_PUBLIC_API_KEY", "")
    )


def payway_script_url():
    return getattr(
        settings,
        "PAYWAY_SCRIPT_URL",
        "https://ventasonline.payway.com.ar/static/v2.6.4/decidir.js",
    )


def payway_sandbox():
    """True si la integración apunta al entorno sandbox."""
    return bool(getattr(settings, "PAYWAY_SANDBOX", False))


def payway_payment_method_ids():
    """Medios de pago habilitados por el comercio ``{payment_method_id: etiqueta}``."""
    configured = getattr(settings, "PAYWAY_PAYMENT_METHODS", None) or {}
    return {str(k): str(v) for k, v in configured.items()}


def payway_site_transaction_id(payment):
    """Referencia determinística del intento (<= 39 caracteres)."""
    return f"scv{payment.public_id.hex}"


def _amount_minor_units(amount):
    """Importe Decimal → entero en unidades mínimas (dos decimales)."""
    return int((amount.quantize(Decimal("0.01")) * 100).to_integral_value())


class PaywayClient:
    """Cliente HTTP mínimo para la API Decidir2 (sin SDK).

    Autenticación con header ``apikey`` (clave privada), timeout corto y
    secretos únicamente en headers. Nunca registra payloads (contienen el
    token de pago).
    """

    def __init__(self, private_key=None, api_base=None, timeout=10):
        self.private_key = private_key or getattr(settings, "PAYWAY_PRIVATE_API_KEY", "")
        self.api_base = api_base or getattr(
            settings, "PAYWAY_API_BASE", "https://ventasonline.payway.com.ar/api/v2"
        ).rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, payload=None, query=None):
        url = f"{self.api_base}{path}"
        if query:
            from urllib.parse import urlencode
            url = f"{url}?{urlencode(query)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "apikey": self.private_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:500]
            except Exception:
                detail = ""
            raise PaywayHttpError(exc.code, detail) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PaymentError("No se pudo contactar a Payway.") from exc

    def create_payment(self, payload):
        return self._request("POST", "/payments", payload=payload)

    def get_payment(self, payment_id):
        return self._request("GET", f"/payments/{payment_id}")

    def find_by_site_operation_id(self, site_transaction_id):
        """Recupera pagos por referencia del comercio, aun sin conocer el id."""
        data = self._request(
            "GET", "/payments",
            query={"siteOperationId": site_transaction_id, "pageSize": 5},
        )
        results = data.get("results") if isinstance(data, dict) else None
        if results is None and isinstance(data, list):
            results = data
        return results or []


class PaywayHttpError(PaymentError):
    """Error HTTP de la API de Payway con código y detalle estructurado."""

    def __init__(self, status_code, detail=""):
        super().__init__(f"Payway respondió con error HTTP {status_code}.")
        self.status_code = status_code
        self.detail = detail

    def parsed_body(self):
        try:
            body = json.loads(self.detail) if self.detail else {}
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def error_type(self):
        """``error_type`` estructurado del cuerpo de error (API Decidir2)."""
        return str(self.parsed_body().get("error_type") or "")

    def parsed_errors(self):
        errors = self.parsed_body().get("validation_errors") or self.parsed_body().get("errors") or []
        if errors:
            return [str(e.get("code") or e.get("message") or e) for e in errors]
        return []


def _is_documented_validation_error(exc):
    """True sólo si el error es un rechazo de validación documentado por el
    proveedor, lo que garantiza que el payload se rechazó antes de procesar
    el cobro y por tanto no hubo cargo.

    Única clase reintentable: HTTP 400 con ``error_type`` estructurado
    ``invalid_request_error`` (la API Decidir2 lo documenta para errores de
    validación de parámetros, junto a ``validation_errors`` por campo).
    Cualquier otro error — 4xx distintos (401/403 auth, 404, 408 timeout del
    request, 409 conflicto/duplicado, 429 rate limit), 5xx, respuestas
    vacías o malformadas y cuerpos sin ``error_type`` — es ambiguo respecto
    del cobro y mantiene el resultado desconocido.
    """
    return (
        exc.status_code == 400
        and exc.error_type() == "invalid_request_error"
    )


def _latest_execute_event(payment):
    return (
        PaymentGatewayEvent.objects.filter(
            provider=PaymentGatewayEvent.Provider.PAYWAY,
            external_id=payway_site_transaction_id(payment),
            action__startswith="execute",
        )
        .order_by("-pk")
        .first()
    )


def start_payway_payment(*, booking_or_id, now=None):
    """Inicia o reutiliza el intento de pago con tarjeta de una reserva ONLINE HELD.

    Persiste el ``Payment`` en ``PENDING`` con su ``site_transaction_id``
    antes de cualquier contacto con el proveedor. No realiza llamadas de red.
    Devuelve el ``Payment``.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    if not payway_configured():
        raise PaymentError("El pago con tarjeta no está disponible.")

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking_id)

        if booking.channel != BookingChannel.ONLINE:
            raise InvalidBookingError("Solo las reservas online admiten pago con tarjeta.")
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

        if existing_payment:
            if existing_payment.status == PaymentStatus.APPROVED:
                raise PaymentDuplicateError("Esta reserva ya cuenta con un pago aprobado.")
            if existing_payment.method == PaymentMethod.PAYWAY:
                return existing_payment
            raise PaymentDuplicateError("Ya existe un pago activo para esta reserva.")

        total_amount = calculate_booking_total(booking)
        if total_amount <= Decimal("0.00"):
            raise PaymentError("El importe de la reserva debe ser mayor que cero.")

        payment = Payment(
            booking=booking,
            method=PaymentMethod.PAYWAY,
            status=PaymentStatus.PENDING,
            amount=total_amount,
            currency="ARS",
            registered_by=None,
        )
        payment.reference = payway_site_transaction_id(payment)
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

    return payment


def _claim_execution(payment, effective_now):
    """Marca el intento de cobro con un evento EXECUTE dentro de una
    transacción corta (sin llamadas de red). Devuelve ``(event, blocking_outcome)``.

    Si otro intento de ejecución está en curso o su resultado es desconocido,
    ``blocking_outcome`` indica que no se debe cobrar de nuevo.
    """
    site_tx_id = payway_site_transaction_id(payment)
    last = _latest_execute_event(payment)
    if last and last.outcome not in RETRYABLE_EXECUTE_OUTCOMES:
        return None, last.outcome
    attempt = (
        PaymentGatewayEvent.objects.filter(
            provider=PaymentGatewayEvent.Provider.PAYWAY,
            external_id=site_tx_id,
            action__startswith="execute",
        ).count()
        + 1
    )
    try:
        event = PaymentGatewayEvent.objects.create(
            provider=PaymentGatewayEvent.Provider.PAYWAY,
            external_id=site_tx_id,
            action=f"execute:{attempt}",
            signature_ok=True,
            outcome=OUTCOME_AWAITING,
        )
    except IntegrityError:
        return None, OUTCOME_AWAITING
    return event, None


def _set_event_outcome(event, outcome, detail=""):
    if event is not None:
        PaymentGatewayEvent.objects.filter(pk=event.pk).update(
            outcome=outcome, detail=detail[:255]
        )


def execute_payway_payment(*, payment_or_id, token, payment_method_id, now=None, client=None):
    """Ejecuta el cobro de un intento PENDING contra Payway.

    Persiste la ejecución antes de la llamada de red; no mantiene bloqueos de
    base de datos durante el contacto con el proveedor. Ante un resultado
    desconocido deja el intento pendiente de conciliación y bloquea un nuevo
    cobro. Devuelve ``(outcome, detail)``.
    """
    if not payway_configured():
        raise PaymentError("El pago con tarjeta no está disponible.")
    token = (token or "").strip()
    if not token:
        raise ValidationError("No se recibió el token de pago de la tarjeta.")
    if not payment_method_id:
        raise ValidationError("Debe seleccionar el medio de pago de la tarjeta.")

    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    payment_id = payment_or_id.pk if isinstance(payment_or_id, Payment) else payment_or_id
    booking_id = Payment.objects.values_list("booking_id", flat=True).get(pk=payment_id)

    with transaction.atomic():
        # Orden de bloqueo unificado con los demás servicios: Booking → Payment.
        booking = Booking.objects.select_for_update().get(pk=booking_id)
        payment = Payment.objects.select_for_update().get(pk=payment_id)

        if payment.method != PaymentMethod.PAYWAY:
            raise InvalidBookingError("El pago no corresponde a Payway.")
        if payment.status == PaymentStatus.APPROVED:
            return "ALREADY_CONFIRMED", "El pago ya estaba aprobado."
        if payment.status in (PaymentStatus.REJECTED, PaymentStatus.EXPIRED):
            raise InvalidBookingError("El intento de pago ya se encuentra cerrado; inicie uno nuevo.")
        if payment.status != PaymentStatus.PENDING:
            raise InvalidBookingError(f"Estado de pago no válido para cobro: {payment.status}")
        if booking.status != BookingStatus.HELD:
            raise InvalidBookingError(f"Estado de reserva no válido para pago: {booking.status}")
        if booking.expires_at is not None and booking.expires_at <= effective_now:
            raise BookingExpiredError("La reserva ha expirado y no se puede ejecutar el cobro.")

        event, blocking = _claim_execution(payment, effective_now)
    # Fin de la transacción: desde aquí no hay bloqueos durante la red.

    if blocking == OUTCOME_AWAITING:
        return "IN_PROGRESS", "El cobro está en proceso; aguardá unos instantes."
    if blocking:
        # Resultado no verificable (desconocido, pendiente, ambiguo o
        # incompleto): no autorizar otro POST de cobro; conciliar primero.
        return reconcile_payway_payment(
            payment_or_id=payment_id, now=now, client=client
        )

    payload = {
        "site_transaction_id": payway_site_transaction_id(payment),
        "token": token,
        "payment_method_id": int(payment_method_id),
        "bin": "",
        "amount": _amount_minor_units(payment.amount),
        "currency": payment.currency,
        "installments": 1,
        "payment_type": "single",
        "sub_payments": [],
    }

    pw_client = client or PaywayClient()
    try:
        remote = pw_client.create_payment(payload)
    except PaywayHttpError as exc:
        if _is_documented_validation_error(exc):
            # Rechazo de validación documentado: el cobro no se procesó.
            codes = exc.parsed_errors()
            _set_event_outcome(event, OUTCOME_VALIDATION_ERROR, ", ".join(codes) or "error de validación")
            return "VALIDATION_ERROR", "El proveedor rechazó los datos enviados; verificá la tarjeta e intentá nuevamente."
        # Cualquier otro error (incluidos 408/409/429 y respuestas
        # ambiguas o malformadas): resultado desconocido → conciliar,
        # sin habilitar otro cobro ni otro medio de pago.
        _set_event_outcome(event, OUTCOME_UNKNOWN, f"HTTP {exc.status_code}")
        return reconcile_payway_payment(payment_or_id=payment_id, now=now, client=client)
    except PaymentError:
        # Timeout/red: puede haberse cobrado. Resultado desconocido → conciliar.
        _set_event_outcome(event, OUTCOME_UNKNOWN, "sin respuesta del proveedor")
        return reconcile_payway_payment(payment_or_id=payment_id, now=now, client=client)

    return _apply_payway_result(
        payment_id=payment_id, remote=remote, effective_now=effective_now, event=event
    )


def _remote_minor_amount(remote):
    try:
        return Decimal(str(remote.get("amount"))) / Decimal("100")
    except (InvalidOperation, TypeError, ValueError):
        return None


def _apply_payway_result(*, payment_id, remote, effective_now, event=None):
    """Aplica la respuesta del proveedor (o de una conciliación) al intento local.

    La hora efectiva se toma dentro de la transacción: una reserva que venció
    durante la llamada al proveedor se registra como conflicto, no se confirma.
    """
    remote_status = str(remote.get("status") or "").lower()
    remote_id = str(remote.get("id") or "")
    remote_site_tx = str(remote.get("site_transaction_id") or "")
    remote_amount = _remote_minor_amount(remote)
    remote_currency = str(remote.get("currency") or "").upper()

    booking_id = Payment.objects.values_list("booking_id", flat=True).get(pk=payment_id)

    with transaction.atomic():
        # Orden de bloqueo unificado: Booking → Payment.
        booking = Booking.objects.select_for_update().get(pk=booking_id)
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        applied_now = timezone.now()

        # Identidad: la referencia del intento debe estar presente y coincidir.
        if not remote_site_tx:
            _set_event_outcome(event, "INCOMPLETE", "Sin referencia del intento")
            return "INCOMPLETE", "La respuesta no trae la referencia del intento; quedará pendiente de conciliación."
        if remote_site_tx != payway_site_transaction_id(payment):
            detail = "La respuesta corresponde a otra operación."
            _set_event_outcome(event, "MISMATCH", detail)
            return "MISMATCH", detail

        if remote_id:
            payment.external_payment_id = remote_id

        if payment.status == PaymentStatus.APPROVED and booking.status == BookingStatus.CONFIRMED:
            payment.save(update_fields=["external_payment_id", "updated_at"])
            _set_event_outcome(event, "ALREADY_CONFIRMED")
            return "ALREADY_CONFIRMED", "El pago ya estaba aprobado y la reserva confirmada."

        if payment.status in (PaymentStatus.REJECTED, PaymentStatus.EXPIRED):
            if remote_status == PAYWAY_APPROVED_STATUS:
                payment.conflict_reason = "approval_after_terminal_state"
                payment.save(update_fields=[
                    "external_payment_id", "conflict_reason", "updated_at",
                ])
                _set_event_outcome(event, "CONFLICT_TERMINAL")
                return "CONFLICT_TERMINAL", "Aprobación tardía sobre un intento ya cerrado."
            payment.save(update_fields=["external_payment_id", "updated_at"])
            _set_event_outcome(event, "IGNORED")
            return "IGNORED", "Intento cerrado; resultado ignorado."

        # Identidad económica: campos presentes deben coincidir con el pago local.
        if remote_amount is not None and remote_amount != payment.amount.quantize(Decimal("0.01")):
            _set_event_outcome(event, "MISMATCH", "Importe no coincide")
            return "MISMATCH", "El importe devuelto no coincide con el pago local."
        if remote_currency and remote_currency != payment.currency.upper():
            _set_event_outcome(event, "MISMATCH", "Moneda no coincide")
            return "MISMATCH", "La moneda devuelta no coincide con el pago local."

        if remote_status == PAYWAY_APPROVED_STATUS:
            # Antes de aplicar una aprobación se exigen todos los campos
            # necesarios: id del pago, importe y moneda verificados.
            if not remote_id or remote_amount is None or not remote_currency:
                _set_event_outcome(event, "INCOMPLETE", "Aprobación con campos requeridos ausentes")
                return "INCOMPLETE", "La aprobación carece de campos requeridos; quedará pendiente de conciliación."
            outcome, detail = _confirm_approved_payment(
                payment, booking, remote_id, applied_now
            )
            _set_event_outcome(event, outcome, detail)
            return outcome, detail

        if remote_status in PAYWAY_REJECTED_STATUSES:
            status_details = remote.get("status_details") or {}
            reason = status_details.get("error") or {}
            if isinstance(reason, dict):
                reason = reason.get("reason") or reason.get("message") or "rejected"
            payment.status = PaymentStatus.REJECTED
            payment.rejection_reason = str(reason)[:255]
            payment.full_clean()
            payment.save(update_fields=[
                "status", "rejection_reason", "external_payment_id", "updated_at",
            ])
            _set_event_outcome(event, "REJECTED")
            return "REJECTED", "El pago fue rechazado por el proveedor."

        if remote_status in PAYWAY_PENDING_PROVIDER_STATUSES:
            payment.save(update_fields=["external_payment_id", "updated_at"])
            _set_event_outcome(event, "STILL_PENDING", remote_status)
            return "STILL_PENDING", f"El pago quedó en estado '{remote_status}' en el proveedor."

        # Estado no reconocido: resultado desconocido, nunca lo tratamos como rechazo.
        payment.save(update_fields=["external_payment_id", "updated_at"])
        _set_event_outcome(event, OUTCOME_UNKNOWN, remote_status or "sin estado")
        return OUTCOME_UNKNOWN, "El proveedor devolvió un estado no reconocido; quedará pendiente de conciliación."


def _confirm_approved_payment(payment, booking, remote_id, effective_now):
    """Confirma pago y reserva tras una aprobación verificada del proveedor."""
    expired = (
        booking.status == BookingStatus.EXPIRED
        or (booking.expires_at is not None and booking.expires_at <= effective_now)
    )
    if expired:
        # Dinero cobrado con reserva vencida: conflicto, jamás reasignar butaca.
        payment.status = PaymentStatus.APPROVED
        payment.reviewed_at = effective_now
        payment.conflict_reason = "approved_after_expiry"
        payment.full_clean()
        payment.save(update_fields=[
            "status", "reviewed_at", "conflict_reason",
            "external_payment_id", "updated_at",
        ])
        AuditEvent.objects.create(
            actor=None,
            action=AuditEvent.Action.UPDATE,
            entity_type=Payment._meta.label,
            entity_id=str(payment.pk),
            description=(
                f"Pago de Payway aprobado con la reserva {booking.public_id} "
                "vencida: conflicto pendiente de resolución."
            ),
            before={"status": PaymentStatus.PENDING},
            after={"status": PaymentStatus.APPROVED, "conflict": "approved_after_expiry"},
        )
        return "CONFLICT_LATE_APPROVAL", "Aprobado tras el vencimiento; requiere resolución."

    if booking.status != BookingStatus.HELD:
        payment.conflict_reason = f"approved_with_booking_{booking.status.lower()}"
        payment.save(update_fields=[
            "external_payment_id", "conflict_reason", "updated_at",
        ])
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
            payment.conflict_reason = f"seat_conflict:{sa.seat_number}"
            payment.save(update_fields=[
                "external_payment_id", "conflict_reason", "updated_at",
            ])
            return "CONFLICT_SEAT", f"Butaca {sa.seat_number} ya confirmada por otra reserva."

    payment.status = PaymentStatus.APPROVED
    payment.reviewed_at = effective_now
    payment.full_clean()
    payment.save(update_fields=["status", "reviewed_at", "external_payment_id", "updated_at"])

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
            f"por pago de Payway {payment.external_payment_id}"
        ),
        before={"status": BookingStatus.HELD},
        after={"status": BookingStatus.CONFIRMED, "confirmed_at": effective_now.isoformat()},
    )
    _enqueue_ticket_fulfillment(booking)
    return "CONFIRMED", "Pago aprobado; reserva confirmada."


def _remote_has_required_fields(remote):
    """True si la fila remota trae identidad y datos económicos completos."""
    return bool(
        remote.get("id")
        and remote.get("site_transaction_id")
        and remote.get("status")
        and remote.get("amount") is not None
        and remote.get("currency")
    )


def reconcile_payway_payment(*, payment_or_id, now=None, client=None):
    """Concilia un intento PENDING cuyo resultado no está resuelto.

    Consulta el proveedor por ``siteOperationId``: no exige haber recibido el
    id del pago. Una respuesta vacía o sin correspondencia verificable no
    prueba ausencia de cobro: el intento permanece pendiente y bloqueado. Si
    el listado es parcial, se reconsulta el detalle autenticado por id.
    Devuelve ``(outcome, detail)``.
    """
    if now is not None:
        validate_aware_datetime(now)
    effective_now = now or timezone.now()

    payment_id = payment_or_id.pk if isinstance(payment_or_id, Payment) else payment_or_id
    payment = Payment.objects.get(pk=payment_id)
    if payment.method != PaymentMethod.PAYWAY or payment.status != PaymentStatus.PENDING:
        return "NOT_PENDING", "El intento no está pendiente de conciliación."

    site_tx_id = payway_site_transaction_id(payment)
    pw_client = client or PaywayClient()
    try:
        results = pw_client.find_by_site_operation_id(site_tx_id) or []
    except PaymentError:
        return "RECONCILE_FAILED", "No se pudo consultar el estado en el proveedor; se reintentará."

    # Verificar correspondencia por la referencia del intento y ambigüedad.
    candidates = [
        row for row in results
        if str(row.get("site_transaction_id") or "") == site_tx_id
    ]
    if len({str(row.get("id") or "") for row in candidates}) > 1:
        _set_event_outcome(_latest_execute_event(payment), "AMBIGUOUS", "Varios pagos con la misma referencia")
        return "AMBIGUOUS", "El proveedor devolvió más de un pago con la referencia del intento; requiere resolución manual."

    if not candidates:
        # Ausencia no verificada: no habilita otro cobro de la reserva.
        _set_event_outcome(
            _latest_execute_event(payment), OUTCOME_UNKNOWN,
            "sin correspondencia verificable en el listado",
        )
        return "STILL_UNKNOWN", "El proveedor no devolvió un pago con la referencia del intento; el resultado sigue desconocido y el cobro permanece bloqueado."

    remote = candidates[0]
    # Si el listado es parcial, reconsultar el detalle autenticado por id.
    if remote.get("id") and not _remote_has_required_fields(remote):
        try:
            detail = pw_client.get_payment(str(remote["id"]))
        except PaymentError:
            return "RECONCILE_FAILED", "No se pudo consultar el detalle del pago en el proveedor; se reintentará."
        if detail:
            remote = detail

    return _apply_payway_result(
        payment_id=payment_id, remote=remote, effective_now=effective_now,
        event=_latest_execute_event(payment),
    )


def reconcile_pending_payway_payments(*, now=None, limit=25, client=None):
    """Reconcilia intentos Payway pendientes con resultado desconocido.

    Pensado para el comando de mantenimiento operativo existente. Devuelve un
    resumen ``{outcome: cantidad}``.
    """
    from collections import Counter

    summary = Counter()
    pending = (
        Payment.objects.filter(
            method=PaymentMethod.PAYWAY,
            status=PaymentStatus.PENDING,
        )
        .exclude(reference="")
        .order_by("created_at")[:limit]
    )
    for payment in pending:
        last = _latest_execute_event(payment)
        if last is None or last.outcome in RETRYABLE_EXECUTE_OUTCOMES:
            continue
        try:
            outcome, _detail = reconcile_payway_payment(payment_or_id=payment, now=now, client=client)
        except Exception as exc:  # noqa: BLE001 - registrar y seguir con el siguiente
            logger.exception("Error conciliando pago Payway %s", payment.public_id)
            outcome = f"ERROR:{exc.__class__.__name__}"
        summary[outcome] += 1
    return dict(summary)
