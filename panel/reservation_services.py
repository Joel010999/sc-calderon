"""Servicios de reservas manuales y auditoría del panel."""

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from operations.models import Trip, TripFare, TripStop
from sales.exceptions import InvalidBookingError, SeatUnavailableError
from sales.models import Booking, BookingChannel, BookingStatus, SeatAssignment
from sales.services import create_manual_booking, release_booking
from .models import AuditEvent
from .permissions import require_reservations_access


def booking_snapshot(booking):
    """Genera una fotografía estructurada de la reserva para eventos de auditoría."""
    total = Decimal("0.00")
    legs_data = []
    for leg in booking.legs.all().order_by("sequence"):
        seats_data = []
        for sa in leg.seat_assignments.all().order_by("passenger__position"):
            total += sa.price
            seats_data.append({
                "passenger_position": sa.passenger.position,
                "seat_id": sa.seat_id,
                "seat_number": sa.seat_number,
                "category": sa.category,
                "price": format(sa.price, ".2f"),
                "currency": sa.currency,
                "status": sa.status,
            })
        legs_data.append({
            "sequence": leg.sequence,
            "trip_id": leg.trip_id,
            "origin_stop_id": leg.origin_stop_id,
            "origin_stop_name": leg.origin_stop_name,
            "destination_stop_id": leg.destination_stop_id,
            "destination_stop_name": leg.destination_stop_name,
            "departure_at": leg.departure_at.isoformat() if leg.departure_at else None,
            "arrival_at": leg.arrival_at.isoformat() if leg.arrival_at else None,
            "seats": seats_data,
        })

    passengers_data = []
    for p in booking.passengers.all().order_by("position"):
        passengers_data.append({
            "position": p.position,
            "first_name": p.first_name,
            "last_name": p.last_name,
            "document_type": p.document_type,
            "document_number": p.document_number,
            "normalized_document": p.normalized_document,
            "birth_date": p.birth_date.isoformat() if p.birth_date else None,
            "nationality": p.nationality,
            "gender": p.gender,
        })

    return {
        "public_id": str(booking.public_id),
        "channel": booking.channel,
        "status": booking.status,
        "email": booking.email,
        "phone": booking.phone,
        "seller_id": booking.seller_id,
        "seller_username": booking.seller.username if booking.seller else None,
        "expires_at": booking.expires_at.isoformat() if booking.expires_at else None,
        "confirmed_at": booking.confirmed_at.isoformat() if booking.confirmed_at else None,
        "total": format(total, ".2f"),
        "passengers": passengers_data,
        "legs": legs_data,
    }


def record_booking_event(*, actor, booking, action, before, description=None):
    """Registra un evento de auditoría para una reserva en panel.AuditEvent."""
    if description is None:
        description = f"{action.label}: Reserva {booking.public_id}"
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity_type=booking._meta.label,
        entity_id=str(booking.pk),
        description=description,
        before=before,
        after=booking_snapshot(booking),
    )


@transaction.atomic
def create_panel_manual_booking(*, actor, email, phone="", legs, passengers_data=None, now=None):
    """Crea una reserva manual y registra su auditoría en la misma transacción."""
    require_reservations_access(actor)
    booking = create_manual_booking(
        seller=actor,
        email=email,
        phone=phone,
        legs=legs,
        passengers_data=passengers_data,
        now=now,
    )
    record_booking_event(
        actor=actor,
        booking=booking,
        action=AuditEvent.Action.CREATE,
        before={},
        description=f"Creación: Reserva {booking.public_id}",
    )
    return booking


@transaction.atomic
def release_panel_booking(*, actor, booking_or_id, now=None):
    """Libera una reserva HELD y registra su auditoría en la misma transacción.

    Es idempotente si la reserva ya está RELEASED. Rechaza CONFIRMED o estados distintos de HELD.
    """
    require_reservations_access(actor)
    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    booking = Booking.objects.select_for_update().prefetch_related(
        "legs__seat_assignments", "passengers"
    ).get(pk=booking_id)

    if booking.status == BookingStatus.RELEASED:
        return booking, False

    if booking.status == BookingStatus.CONFIRMED:
        raise InvalidBookingError("No se puede liberar una reserva confirmada sin una política de cancelación aprobada.")

    if booking.status != BookingStatus.HELD:
        raise InvalidBookingError(f"No se puede liberar una reserva en estado {booking.get_status_display()}.")

    before = booking_snapshot(booking)
    release_booking(booking, now=now)
    # Recargar asignaciones para el snapshot posterior
    booking.refresh_from_db()
    record_booking_event(
        actor=actor,
        booking=booking,
        action=AuditEvent.Action.UPDATE,
        before=before,
        description=f"Liberación: Reserva {booking.public_id}",
    )
    return booking, True
