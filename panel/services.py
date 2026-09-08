"""Escrituras operativas y auditoría en una misma transacción."""

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404

from operations.models import Bus, Seat, Trip, TripFare
from .forms import BusForm, SeatForm
from .models import AuditEvent
from .permissions import require_operations_manager


BUS_FIELDS = ("code", "display_name", "license_plate", "is_active")
SEAT_FIELDS = ("bus_id", "number", "deck", "category", "position_x", "position_y", "is_active")
WRITE_ERROR = "No se pudo guardar el cambio. Revisá los datos: el código, la patente, el número o la posición podrían estar en uso."


def snapshot(instance):
    if isinstance(instance, Trip):
        stops = list(instance.trip_stops.order_by("sequence").values(
            "stop_id", "sequence", "scheduled_at", "allows_boarding", "allows_alighting"
        ))
        for stop in stops:
            stop["scheduled_at"] = stop["scheduled_at"].isoformat()
        return {
            "route_id": instance.route_id, "bus_id": instance.bus_id,
            "status": instance.status, "departure_at": instance.departure_at.isoformat(),
            "stops": stops,
        }
    if isinstance(instance, TripFare):
        return {
            "trip_id": instance.trip_id, "origin_stop_id": instance.origin_stop_id,
            "destination_stop_id": instance.destination_stop_id,
            "seat_category": instance.seat_category, "amount": format(instance.amount, ".2f"),
            "currency": instance.currency, "is_active": instance.is_active,
        }
    fields = BUS_FIELDS if isinstance(instance, Bus) else SEAT_FIELDS
    return {field: getattr(instance, field) for field in fields}


def record_event(actor, instance, action, before):
    if isinstance(instance, Trip):
        label = f"Viaje {instance.pk} del recorrido {instance.route_id}"
    elif isinstance(instance, TripFare):
        label = f"Tarifa {instance.pk} del viaje {instance.trip_id}"
    elif isinstance(instance, Bus):
        label = f"Colectivo {instance.code}"
    else:
        label = f"Butaca {instance.number} del colectivo {instance.bus_id}"
    AuditEvent.objects.create(
        actor=actor, action=action, entity_type=instance._meta.label,
        entity_id=str(instance.pk), description=f"{action.label}: {label}",
        before=before, after=snapshot(instance),
    )


def save_configuration(*, actor, data, pk=None, bus=None):
    """Devuelve (formulario, objeto guardado o None), sin exponer errores SQL."""
    require_operations_manager(actor)
    model, form_class = (Seat, SeatForm) if bus is not None else (Bus, BusForm)
    form = None
    try:
        with transaction.atomic():
            queryset = model.objects.select_for_update()
            if bus is not None:
                queryset = queryset.filter(bus=bus)
            instance = get_object_or_404(queryset, pk=pk) if pk is not None else model()
            before = snapshot(instance) if pk is not None else {}
            kwargs = {"bus": bus} if bus is not None else {}
            form = form_class(data, instance=instance, **kwargs)
            if not form.is_valid():
                return form, None
            instance = form.save()
            if before != snapshot(instance):
                action = AuditEvent.Action.UPDATE if pk is not None else AuditEvent.Action.CREATE
                record_event(actor, instance, action, before)
            return form, instance
    except IntegrityError:
        if form is None:
            raise
        form.add_error(None, WRITE_ERROR)
        return form, None


@transaction.atomic
def set_configuration_active(*, actor, pk, active, bus=None):
    require_operations_manager(actor)
    model = Seat if bus is not None else Bus
    queryset = model.objects.select_for_update()
    if bus is not None:
        queryset = queryset.filter(bus=bus)
    instance = get_object_or_404(queryset, pk=pk)
    if instance.is_active == active:
        return instance, False
    before = snapshot(instance)
    instance.is_active = active
    instance.full_clean()
    instance.save(update_fields=["is_active", "updated_at"])
    action = AuditEvent.Action.ACTIVATE if active else AuditEvent.Action.DEACTIVATE
    record_event(actor, instance, action, before)
    return instance, True
