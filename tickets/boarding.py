"""Reglas transaccionales para validar y revertir embarques."""

import hashlib
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from django.core.exceptions import PermissionDenied
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

    if value.upper().startswith("TK-"):
        return "code", value.upper()
    return "token", value


def _find_ticket(scan_value):
    kind, value = _scan_kind(scan_value)
    if not value:
        return None
    queryset = Ticket.objects.select_for_update().select_related(
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
    """Valida el QR/código y registra un único embarque activo de forma atómica."""
    _require_validator(operator)
    effective_now = now or timezone.now()
    selected_trip = Trip.objects.filter(pk=selected_trip_id).first()
    if selected_trip is None:
        return BoardingOutcome(BoardingCode.WRONG_TRIP, MESSAGES[BoardingCode.WRONG_TRIP])

    with transaction.atomic():
        ticket = _find_ticket(scan_value)
        if ticket is None:
            return BoardingOutcome(BoardingCode.INVALID_TICKET, MESSAGES[BoardingCode.INVALID_TICKET])

        if ticket.status != TicketStatus.ISSUED:
            return BoardingOutcome(BoardingCode.TICKET_NOT_VALID, MESSAGES[BoardingCode.TICKET_NOT_VALID], ticket=ticket)
        if ticket.booking.status != BookingStatus.CONFIRMED:
            return BoardingOutcome(
                BoardingCode.BOOKING_NOT_CONFIRMED,
                MESSAGES[BoardingCode.BOOKING_NOT_CONFIRMED],
                ticket=ticket,
            )
        if ticket.seat_assignment.status != AssignmentStatus.CONFIRMED:
            return BoardingOutcome(
                BoardingCode.ASSIGNMENT_NOT_CONFIRMED,
                MESSAGES[BoardingCode.ASSIGNMENT_NOT_CONFIRMED],
                ticket=ticket,
            )

        relationships_match = (
            ticket.leg.booking_id == ticket.booking_id
            and ticket.passenger.booking_id == ticket.booking_id
            and ticket.seat_assignment.leg_id == ticket.leg_id
            and ticket.seat_assignment.passenger_id == ticket.passenger_id
            and ticket.seat_assignment.trip_id == ticket.leg.trip_id
        )
        if not relationships_match:
            return BoardingOutcome(BoardingCode.INVALID_TICKET, MESSAGES[BoardingCode.INVALID_TICKET], ticket=ticket)
        if ticket.leg.trip_id != selected_trip.pk:
            return BoardingOutcome(BoardingCode.WRONG_TRIP, MESSAGES[BoardingCode.WRONG_TRIP], ticket=ticket)
        active = BoardingRecord.objects.select_related("operator").filter(
            ticket=ticket,
            status=BoardingStatus.ACTIVE,
        ).first()
        if active:
            return BoardingOutcome(
                BoardingCode.ALREADY_BOARDED,
                MESSAGES[BoardingCode.ALREADY_BOARDED],
                ticket=ticket,
                record=active,
            )

        if selected_trip.status not in (Trip.Status.SCHEDULED, Trip.Status.BOARDING, Trip.Status.STARTED):
            return BoardingOutcome(BoardingCode.TICKET_NOT_VALID, MESSAGES[BoardingCode.TICKET_NOT_VALID], ticket=ticket)

        try:
            with transaction.atomic():
                record = BoardingRecord.objects.create(
                    ticket=ticket,
                    passenger=ticket.passenger,
                    trip=ticket.leg.trip,
                    seat_assignment=ticket.seat_assignment,
                    operator=operator,
                    boarded_at=effective_now,
                )
        except IntegrityError:
            active = BoardingRecord.objects.select_related("operator").filter(
                ticket=ticket,
                status=BoardingStatus.ACTIVE,
            ).first()
            if active:
                return BoardingOutcome(
                    BoardingCode.ALREADY_BOARDED,
                    MESSAGES[BoardingCode.ALREADY_BOARDED],
                    ticket=ticket,
                    record=active,
                )
            raise

        TicketAuditEvent.objects.create(
            actor=operator,
            action=TicketAuditEvent.Action.BOARDING,
            ticket=ticket,
            booking=ticket.booking,
            description=f"Embarque registrado para pasaje {ticket.ticket_code}",
            metadata={"boarding_id": record.pk, "trip_id": ticket.leg.trip_id, "seat_number": ticket.seat_number},
        )
        return BoardingOutcome(BoardingCode.VALID, MESSAGES[BoardingCode.VALID], ticket=ticket, record=record)


def reverse_boarding(record_id, operator, reason, now=None):
    """Revierte un embarque sin eliminar su registro histórico."""
    _require_administrator(operator)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("La reversión requiere un motivo.")
    effective_now = now or timezone.now()

    with transaction.atomic():
        record = BoardingRecord.objects.select_for_update().select_related("ticket", "ticket__booking").get(pk=record_id)
        if record.status != BoardingStatus.ACTIVE:
            raise ValueError("El embarque ya fue revertido.")
        record.status = BoardingStatus.REVERSED
        record.reversed_at = effective_now
        record.reversed_by = operator
        record.reversal_reason = reason
        record.save(update_fields=["status", "reversed_at", "reversed_by", "reversal_reason", "updated_at"])
        TicketAuditEvent.objects.create(
            actor=operator,
            action=TicketAuditEvent.Action.BOARDING_REVERSAL,
            ticket=record.ticket,
            booking=record.ticket.booking,
            description=f"Reversión del embarque {record.pk} del pasaje {record.ticket.ticket_code}",
            metadata={"boarding_id": record.pk, "trip_id": record.trip_id},
        )
    return record
