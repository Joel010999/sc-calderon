"""Política provisional para menores gratuitos sin butaca.

Hoy no existe un instante de compra separado de ``Booking.created_at`` antes
de confirmar una reserva. Mientras Sandro no confirme otra definición, el
servicio recibe ``purchase_at`` al crearla y guarda el resultado en el pasajero.
No se recalcula al confirmar ni al consultar.
"""

from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.utils import timezone


MINOR_FREE_AGE_YEARS = 2
MINOR_POLICY_ID = "provisional-created-at-until-sandro"


def age_on(birth_date: date, on_date: date) -> int:
    years = on_date.year - birth_date.year
    if (on_date.month, on_date.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def is_free_lap_infant(birth_date: date, purchase_at: datetime) -> bool:
    if not birth_date:
        return False
    if isinstance(birth_date, str):
        birth_date = date.fromisoformat(birth_date.strip())
    local_date = timezone.localtime(purchase_at).date() if timezone.is_aware(purchase_at) else purchase_at.date()
    return age_on(birth_date, local_date) < MINOR_FREE_AGE_YEARS


def classify_passengers(passengers_data, purchase_at):
    """Valida acompañantes dentro de la misma compra y devuelve snapshots."""
    passengers = list(passengers_data or [])
    positions = set(range(1, len(passengers) + 1))
    snapshots = []
    for position, data in enumerate(passengers, start=1):
        free = is_free_lap_infant(data.get("birth_date"), purchase_at)
        raw_mother = data.get("accompanied_by_position")
        if raw_mother in (None, "", 0, "0"):
            mother_position = None
        else:
            try:
                mother_position = int(raw_mother)
            except (TypeError, ValueError) as exc:
                raise ValidationError({"accompanied_by_position": "La persona acompañante no es válida."}) from exc
        if free and mother_position not in positions:
            raise ValidationError({"accompanied_by_position": f"El menor {position} debe estar asociado a su madre dentro de la compra."})
        if free and mother_position == position:
            raise ValidationError({"accompanied_by_position": "Un menor no puede acompañarse a sí mismo."})
        if not free and mother_position is not None:
            raise ValidationError({"accompanied_by_position": "Solo un menor gratuito puede declarar una madre acompañante."})
        snapshots.append({"is_lap_infant": free, "mother_position": mother_position})
    for snapshot in snapshots:
        mother_position = snapshot["mother_position"]
        if mother_position is not None and snapshots[mother_position - 1]["is_lap_infant"]:
            raise ValidationError({"accompanied_by_position": "La madre acompañante debe tener butaca propia."})
    return snapshots


def required_seat_count(passengers_data, purchase_at):
    return sum(not item["is_lap_infant"] for item in classify_passengers(passengers_data, purchase_at))
