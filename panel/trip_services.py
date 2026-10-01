"""Coordinación transaccional del panel de viajes y tarifas."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from operations.models import Trip, TripFare
from operations.services import schedule_trip
from .models import AuditEvent
from .permissions import require_operations_manager
from .services import record_event, snapshot
from .trip_forms import TripFareForm


FARE_ERROR = "No se pudo guardar la tarifa. Revisá si ya existe una tarifa para ese tramo y categoría."


def _trip_status_snapshot(trip):
    return {"status": trip.status}


@transaction.atomic
def transition_trip(*, actor, trip_pk, target_status):
    """Cambia el estado operativo permitido de un viaje, de forma serializable.

    El viaje se bloquea antes de validar el estado, por lo que dos operadores
    concurrentes no pueden ejecutar la misma transición ni duplicar auditoría.
    """
    from .permissions import require_reservations_access

    require_reservations_access(actor)
    trip = get_object_or_404(Trip.objects.select_for_update(), pk=trip_pk)
    allowed = {
        Trip.Status.STARTED: Trip.Status.SCHEDULED,
        Trip.Status.COMPLETED: Trip.Status.STARTED,
    }
    current = trip.status
    if current == target_status:
        return trip, False
    if allowed.get(target_status) != current:
        raise ValidationError("La transición de estado del viaje no está permitida.")
    before = _trip_status_snapshot(trip)
    trip.status = target_status
    trip.full_clean()
    trip.save(update_fields=["status", "updated_at"])
    record_event(actor, trip, AuditEvent.Action.UPDATE, before)
    return trip, True


def start_trip(*, actor, trip_pk):
    return transition_trip(actor=actor, trip_pk=trip_pk, target_status=Trip.Status.STARTED)


def complete_trip(*, actor, trip_pk):
    return transition_trip(actor=actor, trip_pk=trip_pk, target_status=Trip.Status.COMPLETED)


def trip_pending_summary(trip_pk):
    """Cuenta reservas y pagos pendientes sin consultar fila por fila."""
    from django.db.models import Count, Q
    from payments.models import Payment, PaymentStatus
    from sales.models import Booking, BookingStatus

    held_bookings = Booking.objects.filter(
        legs__trip_id=trip_pk, status=BookingStatus.HELD,
    ).distinct().count()
    row = Payment.objects.filter(booking__legs__trip_id=trip_pk).aggregate(
        pending_payments=Count("pk", filter=Q(status__in=[
            PaymentStatus.AWAITING_VOUCHER, PaymentStatus.UNDER_REVIEW,
        ])),
    )
    return {"held_bookings": held_bookings, "pending_payments": row["pending_payments"]}


@transaction.atomic
def create_panel_trip(*, actor, route, bus, schedules):
    require_operations_manager(actor)
    trip = schedule_trip(route=route, bus=bus, schedules=schedules)
    # Revalidar después del bloqueo, incluso si la petición estuvo esperando.
    if trip.departure_at <= timezone.now():
        raise ValidationError("La salida debe estar en el futuro.")
    record_event(actor, trip, AuditEvent.Action.CREATE, {})
    return trip


def save_fare(*, actor, trip_pk, data, pk=None):
    require_operations_manager(actor)
    form = None
    try:
        with transaction.atomic():
            trip = get_object_or_404(Trip.objects.select_for_update(), pk=trip_pk)
            fare = get_object_or_404(TripFare.objects.select_for_update(), pk=pk, trip=trip) if pk is not None else TripFare(trip=trip)
            before = snapshot(fare) if pk is not None else {}
            form = TripFareForm(data, instance=fare, trip=trip)
            if not form.is_valid():
                return form, None
            fare = form.save()
            if before != snapshot(fare):
                action = AuditEvent.Action.UPDATE if pk is not None else AuditEvent.Action.CREATE
                record_event(actor, fare, action, before)
            return form, fare
    except IntegrityError:
        if form is None:
            raise
        form.add_error(None, FARE_ERROR)
        return form, None


@transaction.atomic
def set_fare_active(*, actor, trip_pk, pk, active):
    require_operations_manager(actor)
    trip = get_object_or_404(Trip.objects.select_for_update(), pk=trip_pk)
    fare = get_object_or_404(TripFare.objects.select_for_update(), pk=pk, trip=trip)
    if fare.is_active == active:
        return False
    before = snapshot(fare)
    fare.is_active = active
    fare.full_clean()
    fare.save(update_fields=["is_active", "updated_at"])
    action = AuditEvent.Action.ACTIVATE if active else AuditEvent.Action.DEACTIVATE
    record_event(actor, fare, action, before)
    return True
