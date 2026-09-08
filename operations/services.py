"""Servicios explícitos del dominio de operaciones; sin signals ni acceso a ventas."""

from collections.abc import Mapping
from datetime import timezone as datetime_timezone

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery

from .models import Bus, Route, Trip, TripStop
from .validators import validate_aware_datetime


@transaction.atomic
def schedule_trip(*, route, bus, schedules):
    """Programa un viaje con {id_de_parada: datetime} o pares (id, datetime).

    Se admiten pares para detectar explícitamente paradas duplicadas. El orden
    de entrada es libre: la cronología se valida según RouteStop.sequence.
    La salida coincide con el horario de la primera parada del recorrido.
    """
    if route.pk is None or bus.pk is None:
        raise ValidationError("El recorrido y el colectivo deben estar guardados.")
    try:
        route = Route.objects.select_for_update().get(pk=route.pk)
        bus = Bus.objects.select_for_update().get(pk=bus.pk)
    except (Route.DoesNotExist, Bus.DoesNotExist) as exc:
        raise ValidationError("El recorrido o el colectivo ya no existe.") from exc

    if not route.is_active:
        raise ValidationError("El recorrido debe estar activo.")
    if not bus.is_active:
        raise ValidationError("El colectivo debe estar activo.")
    if not bus.seats.filter(is_active=True).exists():
        raise ValidationError("El colectivo debe tener al menos una butaca activa.")

    route_stops = list(route.route_stops.select_for_update().order_by("sequence"))
    if not route_stops:
        raise ValidationError("El recorrido debe tener paradas para programar un viaje.")

    times = {}
    try:
        entries = schedules.items() if isinstance(schedules, Mapping) else schedules
        for stop_id, scheduled_at in entries:
            if stop_id in times:
                raise ValidationError("No se puede repetir el horario de una misma parada.")
            validate_aware_datetime(scheduled_at)
            times[stop_id] = scheduled_at
    except (TypeError, ValueError) as exc:
        raise ValidationError("Los horarios deben ser pares de identificador de parada y fecha con zona horaria.") from exc

    if set(times) != {item.stop_id for item in route_stops}:
        raise ValidationError("Indicá exactamente un horario por parada, sin faltantes ni adicionales.")

    previous = None
    for item in route_stops:
        instant = times[item.stop_id].astimezone(datetime_timezone.utc)
        if previous is not None and instant <= previous:
            raise ValidationError("Los horarios deben ser estrictamente crecientes según el orden del recorrido.")
        previous = instant

    # El bloqueo del colectivo serializa las programaciones concurrentes en
    # PostgreSQL. Los límites provienen de la fotografía del viaje, no del recorrido.
    stops = TripStop.objects.filter(trip_id=OuterRef("pk"))
    conflicts = Trip.objects.filter(bus=bus).exclude(status=Trip.Status.CANCELLED).annotate(
        interval_start=Subquery(stops.order_by("sequence").values("scheduled_at")[:1]),
        interval_end=Subquery(stops.order_by("-sequence").values("scheduled_at")[:1]),
    ).filter(
        interval_start__lt=times[route_stops[-1].stop_id],
        interval_end__gt=times[route_stops[0].stop_id],
    )
    if conflicts.exists():
        raise ValidationError("El colectivo ya tiene un viaje con horarios superpuestos.")

    trip = Trip(route=route, bus=bus, departure_at=times[route_stops[0].stop_id])
    trip.full_clean()
    trip.save()
    for item in route_stops:
        trip_stop = TripStop(
            trip=trip,
            stop_id=item.stop_id,
            sequence=item.sequence,
            scheduled_at=times[item.stop_id],
            allows_boarding=item.allows_boarding,
            allows_alighting=item.allows_alighting,
        )
        trip_stop.full_clean()
        trip_stop.save()
    return trip
