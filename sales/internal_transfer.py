"""Servicios y reglas de negocio para ventas internas por transferencia en SC Viajes.

Reglas obligatorias:
(1) Vendedores no pueden crear reservas manuales sin pago/sin vencimiento; solo administradores/superusuarios.
(2) Vendedores pueden registrar compra pendiente por transferencia con datos completos y retención temporal.
(3) expires_at es el menor entre medianoche al final del día de confirmación de compra en
    America/Argentina/Buenos_Aires y una hora antes de la salida inicial; ida/vuelta usa el límite más temprano aplicable.
(4) Guardar vencimiento sin reiniciarlo al editar o reenviar.
(5) Pago requiere comprobante PDF/imagen válido; vendedor verifica externamente y aprueba solo sus ventas; admin/superuser global.
(6) Comprobante no confirma/extiende.
(7) Vencimiento libera disponibilidad conservando historial.
(8) Última hora antes de salida inicial: solo operación confirmada con datos completos, comprobante y
    declaración de pago verificado; sin retenciones pendientes persistentes.
(9) Confirmar comprueba/asigna butaca transaccionalmente y rechaza conflicto.
(10) Desde salida inicial no nuevas ventas.
(11) Confirmación/emisión idempotentes, sin duplicar pagos/asignaciones/numeración/entregas.
(12) No tocar flujo público ni plazos.
(13) Conservar menores gratuitos y snapshots.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from payments.storage import validate_voucher_file
from sales.models import (
    Booking,
    BookingChannel,
)
from sales.services import (
    validate_quick_passenger_data,
)
from sales.validators import validate_aware_datetime

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def is_internal_admin(user) -> bool:
    """Indica si el usuario es administrador o superusuario con acceso global."""
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    return bool(user.is_superuser or user.groups.filter(name="Administrador").exists())


def is_internal_seller(user) -> bool:
    """Indica si el usuario pertenece al grupo Vendedor."""
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    return bool(user.groups.filter(name="Vendedor").exists())


def calculate_internal_transfer_expiration(trips=None, reference_now=None, *, trip=None, return_trip=None) -> datetime:
    """Calcula el vencimiento (expires_at) de una venta interna por transferencia.

    Regla (3):
    expires_at es el menor entre medianoche al final del día de confirmación de compra en
    America/Argentina/Buenos_Aires y una hora antes de la salida inicial; ida/vuelta usa
    el límite más temprano aplicable.

    - "Medianoche al final del día de compra": para una fecha D en Argentina, es (D + 1) a las 00:00:00 AR_TZ.
    - "Una hora antes de la salida inicial": se toma `trip.departure_at` (salida inicial del viaje),
      NO el horario de paradas intermedias. En ida/vuelta toma la salida inicial más temprana.
    """
    if trips is None:
        trip_list = []
        if trip is not None:
            trip_list.append(trip)
        if return_trip is not None:
            trip_list.append(return_trip)
    elif isinstance(trips, (list, tuple, set)):
        trip_list = list(trips)
    else:
        trip_list = [trips]

    if not trip_list:
        raise ValueError("Se debe especificar al menos un viaje.")

    if reference_now is None:
        reference_now = timezone.now()
    validate_aware_datetime(reference_now)

    local_ref = timezone.localtime(reference_now, AR_TZ)
    # Medianoche al término del día local de confirmación de compra
    midnight_end_of_day = datetime.combine(
        local_ref.date() + timedelta(days=1),
        time.min,
        tzinfo=AR_TZ,
    )

    # Salida inicial más temprana de los viajes involucrados (ida o vuelta)
    earliest_initial_departure = min(t.departure_at for t in trip_list)
    validate_aware_datetime(earliest_initial_departure)

    one_hour_before = earliest_initial_departure - timedelta(hours=1)

    return min(midnight_end_of_day, one_hour_before)


def validate_internal_sale_timing(trips=None, now=None, *, trip=None) -> tuple[datetime, bool]:
    """Valida los límites temporales respecto de la salida inicial del viaje.

    Regla (8): Última hora antes de salida inicial solo admite operaciones confirmadas
               con datos completos, comprobante y declaración de pago verificado.
    Regla (10): Desde salida inicial no nuevas ventas.

    Retorna: (earliest_initial_departure, is_last_hour)
    """
    if trips is None:
        trip_list = [trip] if trip is not None else []
    elif isinstance(trips, (list, tuple, set)):
        trip_list = list(trips)
    else:
        trip_list = [trips]

    if not trip_list:
        raise ValidationError("Se requiere al menos un viaje para validar tiempos.")

    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)

    earliest_initial_departure = min(t.departure_at for t in trip_list)
    validate_aware_datetime(earliest_initial_departure)

    if now >= earliest_initial_departure:
        raise ValidationError("El viaje ya inició su salida inicial. No se permiten nuevas ventas una vez iniciada la salida.")

    one_hour_before = earliest_initial_departure - timedelta(hours=1)
    is_last_hour = now >= one_hour_before

    return earliest_initial_departure, is_last_hour


def validate_complete_internal_data(
    *,
    email: str = "",
    contact_email: str = "",
    phone: str = "",
    contact_phone: str = "",
    passengers_data: list[dict] = None,
    passenger_data: list[dict] = None,
    passenger_count: int = None,
) -> None:
    """Valida que los datos de contacto y de todos los pasajeros estén completos.

    Exigido obligatoriamente para ventas por transferencia y ventas en última hora.
    """
    errors = {}
    email_val = (email or contact_email or "").strip()
    if not email_val:
        errors["email"] = ["El correo electrónico de contacto es obligatorio."]
    else:
        try:
            validate_email(email_val)
        except ValidationError:
            errors["email"] = ["El correo electrónico de contacto no tiene un formato válido."]

    pax_list = passengers_data if passengers_data is not None else (passenger_data or [])
    p_count = passenger_count if passenger_count is not None else len(pax_list)

    if not pax_list or len(pax_list) < p_count or p_count == 0:
        errors["passengers"] = ["Debés cargar los datos completos de todos los pasajeros."]

    for pos, p in enumerate(pax_list[:p_count], start=1):
        try:
            validate_quick_passenger_data(p, position=pos)
        except ValidationError as ve:
            if hasattr(ve, "message_dict"):
                for k, v in ve.message_dict.items():
                    errors[f"p_{pos}_{k}"] = v
            else:
                errors[f"p_{pos}"] = ve.messages

        # Validación estricta de campos indispensables
        if not (p.get("first_name") or "").strip():
            errors.setdefault(f"p_{pos}_first_name", []).append(f"El nombre del pasajero {pos} es obligatorio.")
        if not (p.get("last_name") or "").strip():
            errors.setdefault(f"p_{pos}_last_name", []).append(f"El apellido del pasajero {pos} es obligatorio.")
        if not (p.get("document_number") or "").strip():
            errors.setdefault(f"p_{pos}_document_number", []).append(f"El documento del pasajero {pos} es obligatorio.")
        if not p.get("birth_date"):
            errors.setdefault(f"p_{pos}_birth_date", []).append(f"La fecha de nacimiento del pasajero {pos} es obligatoria.")

    if errors:
        raise ValidationError(errors)


@transaction.atomic
def create_internal_transfer_booking(
    *,
    seller=None,
    actor=None,
    email: str = "",
    contact_email: str = "",
    phone: str = "",
    contact_phone: str = "",
    internal_note: str = "",
    legs: list[dict] = None,
    trip_data: dict = None,
    trip=None,
    origin_stop=None,
    destination_stop=None,
    seat_numbers: list[int] = None,
    seat_category=None,
    passengers_data: list[dict] = None,
    passenger_data: list[dict] = None,
    voucher=None,
    voucher_file=None,
    reference: str = "",
    now=None,
) -> Booking:
    """Registra una compra pendiente por transferencia con datos completos y retención temporal.

    Reglas aplicadas:
    (1) Si un vendedor intenta crear sin vencimiento, es rechazado.
    (2) Retención temporal calculada por regla (3).
    (3) expires_at = min(medianoche AR al final del día de compra, 1h antes de salida inicial).
    (4) Guarda vencimiento sin reiniciarlo al editar/reenviar.
    (6) El comprobante no confirma ni extiende la retención.
    (8) En la última hora antes de la salida inicial rechaza retenciones pendientes
        (debe usarse create_immediate_transfer_sale).
    (10) Rechaza compras si el viaje ya inició la salida.
    (13) Conserva menores gratuitos ($0, sin butaca) y sus snapshots.
    """
    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)

    seller_user = seller or actor
    email_val = (email or contact_email or "").strip()
    phone_val = (phone or contact_phone or "").strip()
    pax_data = passengers_data if passengers_data is not None else (passenger_data or [])
    voucher_obj = voucher or voucher_file

    if legs is None:
        if trip_data is not None:
            legs = [trip_data]
        elif trip is not None:
            legs = [{
                "trip": trip,
                "origin_stop": origin_stop,
                "destination_stop": destination_stop,
                "category": seat_category,
                "seats": seat_numbers or [],
            }]
        else:
            legs = []

    trips = [leg["trip"] for leg in legs]
    _, is_last_hour = validate_internal_sale_timing(trips, now=now)
    if is_last_hour:
        raise ValidationError(
            "En la última hora previa a la salida inicial no se permiten retenciones pendientes. "
            "La operación debe confirmarse de forma inmediata con comprobante y verificación de pago."
        )

    passenger_count = len(pax_data)
    validate_complete_internal_data(
        email=email_val,
        phone=phone_val,
        passengers_data=pax_data,
        passenger_count=passenger_count,
    )

    expires_at = calculate_internal_transfer_expiration(trips, reference_now=now)

    from sales.services import create_booking

    booking = create_booking(
        channel=BookingChannel.MANUAL,
        email=email_val,
        phone=phone_val,
        seller=seller_user,
        internal_note=internal_note,
        legs=legs,
        passengers_data=pax_data,
        expires_at=expires_at,
        now=now,
    )

    if voucher_obj:
        from payments.services import register_transfer_payment
        register_transfer_payment(
            booking_or_id=booking,
            seller=seller_user,
            voucher=voucher_obj,
            reference=reference,
            now=now,
        )

    # Registrar evento de auditoría
    from panel.models import AuditEvent
    from panel.reservation_services import record_booking_event
    record_booking_event(
        actor=seller_user,
        booking=booking,
        action=AuditEvent.Action.CREATE,
        before={},
        description=f"Venta interna por transferencia: Reserva {booking.public_id}",
    )

    return booking


@transaction.atomic
def create_immediate_transfer_sale(
    *,
    seller=None,
    actor=None,
    email: str = "",
    contact_email: str = "",
    phone: str = "",
    contact_phone: str = "",
    internal_note: str = "",
    legs: list[dict] = None,
    trip_data: dict = None,
    trip=None,
    origin_stop=None,
    destination_stop=None,
    seat_numbers: list[int] = None,
    seat_category=None,
    passengers_data: list[dict] = None,
    passenger_data: list[dict] = None,
    voucher=None,
    voucher_file=None,
    payment_verified: bool = False,
    reference: str = "",
    now=None,
) -> Booking:
    """Crea y confirma inmediatamente una venta por transferencia en una transacción atómica.

    Obligatorio para la última hora previa a la salida inicial (Regla 8) y disponible
    para vendedores cuando el cliente ya transfirió y el vendedor verificó externamente.

    Reglas aplicadas:
    (5) Pago requiere comprobante PDF/imagen válido. Vendedor verifica externamente.
    (8) Última hora antes de salida inicial: solo operación confirmada con datos completos,
        comprobante y declaración de pago verificado; sin retenciones pendientes persistentes.
    (9) Confirmar comprueba y asigna butaca transaccionalmente y rechaza conflictos.
    (10) Desde salida inicial no nuevas ventas.
    (11) Confirmación/emisión idempotentes.
    (13) Conservar menores gratuitos y snapshots.
    """
    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)

    seller_user = seller or actor
    email_val = (email or contact_email or "").strip()
    phone_val = (phone or contact_phone or "").strip()
    pax_data = passengers_data if passengers_data is not None else (passenger_data or [])
    voucher_obj = voucher or voucher_file

    if legs is None:
        if trip_data is not None:
            legs = [trip_data]
        elif trip is not None:
            legs = [{
                "trip": trip,
                "origin_stop": origin_stop,
                "destination_stop": destination_stop,
                "category": seat_category,
                "seats": seat_numbers or [],
            }]
        else:
            legs = []

    if not payment_verified:
        raise ValidationError(
            {"payment_verified": "Es obligatoria la declaración expresa de haber verificado externamente el pago."}
        )

    if not voucher_obj:
        raise ValidationError({"voucher": "El archivo de comprobante es obligatorio para confirmar la venta."})
    validate_voucher_file(voucher_obj)

    trips = [leg["trip"] for leg in legs]
    validate_internal_sale_timing(trips, now=now)

    passenger_count = len(pax_data)
    validate_complete_internal_data(
        email=email_val,
        phone=phone_val,
        passengers_data=pax_data,
        passenger_count=passenger_count,
    )

    # 1. Crear la reserva en HELD temporalmente dentro de la transacción
    from sales.services import create_booking

    temp_expiry = now + timedelta(minutes=5)
    booking = create_booking(
        channel=BookingChannel.MANUAL,
        email=email_val,
        phone=phone_val,
        seller=seller_user,
        internal_note=internal_note,
        legs=legs,
        passengers_data=pax_data,
        expires_at=temp_expiry,
        now=now,
    )

    from payments.services import register_transfer_payment, review_transfer_payment

    payment = register_transfer_payment(
        booking_or_id=booking,
        seller=seller_user,
        voucher=voucher_obj,
        reference=reference,
        now=now,
    )

    review_transfer_payment(
        payment_or_id=payment,
        reviewer=seller_user,
        approved=True,
        now=now,
    )

    booking.refresh_from_db()

    from panel.models import AuditEvent
    from panel.reservation_services import record_booking_event
    record_booking_event(
        actor=seller_user,
        booking=booking,
        action=AuditEvent.Action.CREATE,
        before={},
        description=f"Venta inmediata por transferencia confirmada: Reserva {booking.public_id}",
    )

    return booking
