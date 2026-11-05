"""Reglas transaccionales para validar y revertir embarques."""

import hashlib
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from operations.models import Trip
from sales.models import AssignmentStatus, BookingStatus

from .models import (
    BoardingRecord,
    BoardingStatus,
    Ticket,
    TicketAuditEvent,
    TicketStatus,
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


def _require_validator(operator):
    if not getattr(operator, "is_authenticated", False) or not getattr(operator, "is_active", False):
        raise PermissionDenied("Solo un operador autorizado puede validar embarques.")
    if not (
        getattr(operator, "is_superuser", False)
        or operator.groups.filter(name__in=["Administrador", "Vendedor"]).exists()
    ):
        raise PermissionDenied("Solo Administrador y Vendedor pueden validar embarques.")


def _require_administrator(operator):
    if not getattr(operator, "is_authenticated", False) or not getattr(operator, "is_active", False):
        raise PermissionDenied("Solo un administrador puede revertir embarques.")
    if not (
        getattr(operator, "is_superuser", False)
        or operator.groups.filter(name="Administrador").exists()
    ):
        raise PermissionDenied("Solo Administrador puede revertir embarques.")


def _scan_kind(value: str):
    """Devuelve ``("token", token)`` o ``("code", ticket_code)`` sin persistir el secreto."""
    value = (value or "").strip()
    if not value or len(value) > 2048:
        return None, None

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        if parsed.path.rstrip("/") != "/tickets/verify":
            return None, None
        token = parse_qs(parsed.query).get("token", [""])[0].strip()
        return ("token", token) if token else (None, None)

    upper = value.upper()
    if upper.startswith("TK-") or upper.startswith("AAA-"):
        return "code", upper
        return "code", value.upper()
    return "token", value


def _find_ticket(scan_value):
    kind, value = _scan_kind(scan_value)
    if not value:
        return None
    # `seat_assignment` es nullable para menores gratuitos. En PostgreSQL un
    # FOR UPDATE implícito sobre todo el queryset no puede bloquear el lado
    # nullable del LEFT JOIN; el ticket es la fila que debe serializarse.
    queryset = Ticket.objects.select_for_update(of=("self",)).select_related(
        "booking",
        "leg",
        "passenger",
        "seat_assignment",
        "seat_assignment__leg",
        "seat_assignment__passenger",
        "seat_assignment__trip",
    )
    if kind == "code":
        return queryset.filter(ticket_code=value).first()
    token_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return queryset.filter(verification_token_hash=token_hash).first()


def validate_boarding(scan_value, selected_trip_id, operator, now=None):
    """El control de embarque se encuentra deshabilitado."""
    raise ValidationError("El control de embarque se encuentra deshabilitado.")


def reverse_boarding(record_id, operator, reason, now=None):
    """La reversión de embarque se encuentra deshabilitada."""
    raise ValidationError("La reversión de embarque se encuentra deshabilitada.")
