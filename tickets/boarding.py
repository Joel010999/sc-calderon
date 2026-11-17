"""Reglas transaccionales para validar y revertir embarques."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .models import (
    BoardingRecord,
    Ticket,
)


class BoardingCode:
    VALID = "VALID"
    ALREADY_BOARDED = "ALREADY_BOARDED"
    WRONG_TRIP = "WRONG_TRIP"
    INVALID_TICKET = "INVALID_TICKET"
    BOOKING_NOT_CONFIRMED = "BOOKING_NOT_CONFIRMED"
    ASSIGNMENT_NOT_CONFIRMED = "ASSIGNMENT_NOT_CONFIRMED"
    TICKET_NOT_VALID = "TICKET_NOT_VALID"


MESSAGES = {
    BoardingCode.VALID: "Pasaje válido y embarque registrado.",
    BoardingCode.ALREADY_BOARDED: "Este pasaje ya fue utilizado para embarcar.",
    BoardingCode.WRONG_TRIP: "El pasaje pertenece a otro viaje.",
    BoardingCode.INVALID_TICKET: "El pasaje o código QR no es válido.",
    BoardingCode.BOOKING_NOT_CONFIRMED: "La reserva del pasaje no está confirmada.",
    BoardingCode.ASSIGNMENT_NOT_CONFIRMED: "La asignación de butaca no está confirmada.",
    BoardingCode.TICKET_NOT_VALID: "El pasaje no está emitido o ya no está vigente.",
}


@dataclass(frozen=True)
class BoardingOutcome:
    code: str
    message: str
    ticket: Ticket | None = None
    record: BoardingRecord | None = None

    @property
    def is_success(self):
        return self.code == BoardingCode.VALID


def validate_boarding(scan_value, selected_trip_id, operator, now=None):
    """El control de embarque se encuentra deshabilitado."""
    raise ValidationError("El control de embarque se encuentra deshabilitado.")


def reverse_boarding(record_id, operator, reason, now=None):
    """La reversión de embarque se encuentra deshabilitada."""
    raise ValidationError("La reversión de embarque se encuentra deshabilitada.")
