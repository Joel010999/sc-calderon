"""Elegibilidad server-side para butacas prioritarias (51 y 54).

Las butacas 51 y 54 son prioritarias por salida/asiento para personas con
discapacidad. Mientras no estén definidas las reglas comerciales de
acreditación, tarifa diferencial y autorización interna, permanecen bloqueadas
para el circuito general (web y panel) hasta su liberación automática.

La liberación para venta general ocurre dos días calendario antes de la fecha
local de salida del viaje a las 00:00 en ``America/Argentina/Buenos_Aires``
(no restando 48 horas). Las reservas o ventas preexistentes conservan su
ocupante. No se utiliza ``Seat.is_active`` global ni se requiere un worker para
que la liberación tenga efecto.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .exceptions import PrioritySeatUnavailableError
from .validators import validate_aware_datetime

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
PRIORITY_SEAT_NUMBERS = frozenset({51, 54})
PRIORITY_RELEASE_DAYS_BEFORE = 2
PRIORITY_POLICY_ID = "provisional-priority-seats-51-54-until-sandro"


def _extract_seat_number(seat_or_number) -> int:
    if hasattr(seat_or_number, "number"):
        return int(seat_or_number.number)
    return int(seat_or_number)


def _extract_departure_at(trip_or_departure_at) -> datetime:
    departure_at = (
        trip_or_departure_at.departure_at
        if hasattr(trip_or_departure_at, "departure_at")
        else trip_or_departure_at
    )
    validate_aware_datetime(departure_at)
    return departure_at


def is_priority_seat(seat_or_number) -> bool:
    """Indica si el asiento corresponde a una de las butacas prioritarias (51 o 54)."""
    try:
        seat_number = _extract_seat_number(seat_or_number)
    except (TypeError, ValueError):
        return False
    return seat_number in PRIORITY_SEAT_NUMBERS


def priority_seat_release_at(trip_or_departure_at) -> datetime:
    """Devuelve el instante exacto de liberación (00:00 AR dos días calendario antes de la salida)."""
    departure_at = _extract_departure_at(trip_or_departure_at)
    local_departure = timezone.localtime(departure_at, AR_TZ)
    release_date = local_departure.date() - timedelta(days=PRIORITY_RELEASE_DAYS_BEFORE)
    return datetime.combine(release_date, time.min, tzinfo=AR_TZ)


def is_priority_seat_released_for_general_sale(trip_or_departure_at, now=None) -> bool:
    """Indica si ya se alcanzó el instante de liberación para venta general en horario argentino."""
    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)
    release_at = priority_seat_release_at(trip_or_departure_at)
    return timezone.localtime(now, AR_TZ) >= release_at


def is_seat_blocked_for_general_sale(trip_or_departure_at, seat_or_number, now=None) -> bool:
    """Indica si la butaca está bloqueada para venta general por ser prioritaria antes del plazo."""
    if not is_priority_seat(seat_or_number):
        return False
    return not is_priority_seat_released_for_general_sale(trip_or_departure_at, now=now)


def is_seat_eligible_for_general_sale(trip_or_departure_at, seat_or_number, now=None) -> bool:
    """Indica si la butaca es elegible para venta general en el instante consultado."""
    return not is_seat_blocked_for_general_sale(trip_or_departure_at, seat_or_number, now=now)


def validate_seat_eligible_for_general_sale(trip, seat, now=None) -> None:
    """Rechaza la selección en venta general si la butaca prioritaria aún no se liberó."""
    if is_seat_blocked_for_general_sale(trip, seat, now=now):
        release_local = priority_seat_release_at(trip)
        seat_number = _extract_seat_number(seat)
        raise PrioritySeatUnavailableError(
            f"La butaca {seat_number} es prioritaria para personas con discapacidad y no está "
            f"disponible para venta general antes del {release_local:%d/%m/%Y} a las 00:00 hs."
        )
