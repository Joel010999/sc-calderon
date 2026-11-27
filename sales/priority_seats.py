"""Elegibilidad server-side para butacas prioritarias (51 y 54).

Decisión confirmada por Joel: las butacas 51 y 54 de cada viaje permanecen
reservadas para personas con discapacidad, cuya solicitud se gestiona por
correo electrónico, hasta 48 horas exactas antes de la salida del viaje.
A partir de ese instante se liberan automáticamente para venta general
(web y panel) si están disponibles según las reglas normales.

El límite es un instante absoluto (salida menos 48 horas), no una fecha ni
una medianoche. Las reservas o ventas preexistentes conservan su ocupante:
la liberación no cancela reservas ni libera asignaciones válidas. No se
utiliza ``Seat.is_active`` global ni se requiere un worker para que la
liberación tenga efecto.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .exceptions import PrioritySeatUnavailableError
from .validators import validate_aware_datetime

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
PRIORITY_SEAT_NUMBERS = frozenset({51, 54})
PRIORITY_RELEASE_HOURS_BEFORE = 48
PRIORITY_POLICY_ID = "priority-seats-51-54-release-48h-before-departure"


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
    """Devuelve el instante exacto de liberación: la salida del viaje menos 48 horas."""
    departure_at = _extract_departure_at(trip_or_departure_at)
    return departure_at - timedelta(hours=PRIORITY_RELEASE_HOURS_BEFORE)


def is_priority_seat_released_for_general_sale(trip_or_departure_at, now=None) -> bool:
    """Indica si ya se alcanzó el instante de liberación para venta general en horario argentino."""
    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)
    return now >= priority_seat_release_at(trip_or_departure_at)


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
        release_local = timezone.localtime(priority_seat_release_at(trip), AR_TZ)
        seat_number = _extract_seat_number(seat)
        raise PrioritySeatUnavailableError(
            f"La butaca {seat_number} es prioritaria para personas con discapacidad y se gestiona "
            f"por correo electrónico hasta 48 horas antes de la salida. Estará disponible para "
            f"venta general desde el {release_local:%d/%m/%Y} a las {release_local:%H:%M} hs."
        )
