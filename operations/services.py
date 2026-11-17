"""Servicios explícitos del dominio de operaciones; sin signals ni acceso a ventas."""

from collections.abc import Mapping
from datetime import timezone as datetime_timezone

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef, Subquery

from django.db import models
from .models import Bus, Route, RouteStop, Trip, TripStop
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


@transaction.atomic
def update_trip_schedules(*, trip, schedules):
    """Actualiza los horarios estimados por parada (TripStop.scheduled_at) de una salida.

    Reutiliza TripStop.scheduled_at sin crear un segundo circuito de horarios.
    Valida pertenencia de cada parada al recorrido de la salida, secuencia y orden
    cronológico estricto con fechas conscientes de zona horaria (incluyendo cruces
    de medianoche). No recalcula reservas ni pasajes históricos ya emitidos.
    """
    if trip is None or trip.pk is None:
        raise ValidationError("El viaje debe estar guardado para actualizar sus horarios.")
    try:
        trip = Trip.objects.select_for_update().select_related("route", "bus").get(pk=trip.pk)
        bus = Bus.objects.select_for_update().get(pk=trip.bus_id)
    except (Trip.DoesNotExist, Bus.DoesNotExist) as exc:
        raise ValidationError("El viaje o el colectivo ya no existe.") from exc

    if (
        trip.status in (Trip.Status.STARTED, Trip.Status.COMPLETED, Trip.Status.CANCELLED)
        or trip.started_at is not None
        or trip.completed_at is not None
    ):
        raise ValidationError("No se pueden modificar los horarios de un viaje iniciado, finalizado o cancelado.")

    trip_stops = list(trip.trip_stops.select_for_update().select_related("stop").order_by("sequence"))
    if not trip_stops:
        raise ValidationError("El viaje no tiene paradas configuradas.")

    if schedules is None or isinstance(schedules, (str, bytes)):
        raise ValidationError("Los horarios deben ser pares de identificador de parada y fecha con zona horaria.")

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

    expected_stop_ids = {item.stop_id for item in trip_stops}
    if set(times) - expected_stop_ids:
        raise ValidationError("Se indicaron paradas que no pertenecen al recorrido de esta salida.")
    if set(times) != expected_stop_ids:
        raise ValidationError("Indicá exactamente un horario por parada, sin faltantes ni adicionales.")

    previous = None
    for index, item in enumerate(trip_stops, start=1):
        if item.sequence != index:
            raise ValidationError("La secuencia de paradas del viaje no es consecutiva.")
        instant = times[item.stop_id].astimezone(datetime_timezone.utc)
        if previous is not None and instant <= previous:
            raise ValidationError("Los horarios deben ser estrictamente crecientes según el orden del recorrido.")
        previous = instant

    stops = TripStop.objects.filter(trip_id=OuterRef("pk"))
    conflicts = (
        Trip.objects.filter(bus=bus)
        .exclude(pk=trip.pk)
        .exclude(status=Trip.Status.CANCELLED)
        .annotate(
            interval_start=Subquery(stops.order_by("sequence").values("scheduled_at")[:1]),
            interval_end=Subquery(stops.order_by("-sequence").values("scheduled_at")[:1]),
        )
        .filter(
            interval_start__lt=times[trip_stops[-1].stop_id],
            interval_end__gt=times[trip_stops[0].stop_id],
        )
    )
    if conflicts.exists():
        raise ValidationError("El colectivo ya tiene un viaje con horarios superpuestos.")

    trip.departure_at = times[trip_stops[0].stop_id]
    trip.full_clean()
    trip.save(update_fields=["departure_at", "updated_at"])
    for item in trip_stops:
        item.scheduled_at = times[item.stop_id]
        item.full_clean()
        item.save(update_fields=["scheduled_at"])
    return trip


def _extract_fare_field(record, name, default=None):
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def detect_trip_fare_conflicts(fare_records):
    """Detecta conflictos en tarifas históricas cuando una misma salida y categoría
    presenta más de una combinación de importe, moneda o estado activo.

    No inventa precios ni elige arbitrariamente entre valores divergentes.
    """
    grouped = {}
    for record in fare_records:
        trip_id = _extract_fare_field(record, "trip_id")
        if trip_id is None:
            trip_obj = _extract_fare_field(record, "trip")
            trip_id = getattr(trip_obj, "pk", None)
        category = _extract_fare_field(record, "seat_category")
        amount = _extract_fare_field(record, "amount")
        currency = (_extract_fare_field(record, "currency", "ARS") or "ARS").strip().upper()
        is_active = bool(_extract_fare_field(record, "is_active", True))
        key = (trip_id, category)
        signature = (amount, currency, is_active)
        grouped.setdefault(key, set()).add(signature)

    conflicts = []
    for (trip_id, category), signatures in sorted(grouped.items(), key=lambda item: (item[0][0] or 0, str(item[0][1]))):
        if len(signatures) > 1:
            variants = [
                {"amount": str(amt), "currency": curr, "is_active": act}
                for amt, curr, act in sorted(signatures, key=lambda s: (str(s[0]), s[1], s[2]))
            ]
            conflicts.append({
                "trip_id": trip_id,
                "seat_category": category,
                "variants": variants,
            })
    return conflicts


def validate_no_trip_fare_conflicts(fare_records):
    """Lanza ValidationError si existen tarifas ambiguas para una misma salida y categoría."""
    conflicts = detect_trip_fare_conflicts(fare_records)
    if conflicts:
        details = "; ".join(
            f"viaje {c['trip_id']} ({c['seat_category']}): "
            + ", ".join(f"{v['currency']} {v['amount']} (activa={v['is_active']})" for v in c["variants"])
            for c in conflicts
        )
        raise ValidationError(
            f"Se detectaron tarifas históricas ambiguas con precios o estados divergentes para la misma salida y categoría ({details}). "
            "Resolvé manualmente la ambigüedad antes de consolidar tarifas por salida."
        )
    return []


def validate_route_stops(stops_data):
    """Valida la consistencia de una secuencia de paradas para un recorrido.

    stops_data: iterable de dicts o tuplas con (stop, sequence, allows_boarding, allows_alighting).
    Verifica:
    - Al menos dos paradas (origen y destino distintos).
    - Orden secuencial estrictamente positivo y correlativo (1..N).
    - Paradas no repetidas en el mismo recorrido.
    - Primera parada debe permitir subir.
    - Última parada debe permitir bajar.
    - Cada parada debe permitir subir o bajar.
    - Al menos un tramo interprovincial válido (origen y destino en diferentes provincias,
      donde origen permite subir y destino permite bajar).
    """
    if not stops_data or len(stops_data) < 2:
        raise ValidationError("El recorrido debe tener al menos dos paradas (origen y destino).")

    normalized = []
    seen_stops = set()
    seen_sequences = set()

    for item in stops_data:
        if isinstance(item, Mapping):
            stop = item["stop"]
            sequence = int(item["sequence"])
            boarding = bool(item.get("allows_boarding", False))
            alighting = bool(item.get("allows_alighting", False))
        else:
            stop, sequence, boarding, alighting = item
            sequence = int(sequence)
            boarding = bool(boarding)
            alighting = bool(alighting)

        if stop is None or getattr(stop, "pk", None) is None:
            raise ValidationError("Cada parada debe ser válida y estar guardada.")
        if sequence < 1:
            raise ValidationError("El orden de cada parada debe ser mayor o igual a 1.")
        if stop.pk in seen_stops:
            raise ValidationError(f"La parada {stop.name} no se puede repetir en el mismo recorrido.")
        if sequence in seen_sequences:
            raise ValidationError(f"El orden {sequence} está repetido en el recorrido.")
        if not boarding and not alighting:
            raise ValidationError(f"La parada {stop.name} debe permitir al menos subir o bajar pasajeros.")

        seen_stops.add(stop.pk)
        seen_sequences.add(sequence)
        normalized.append({
            "stop": stop,
            "sequence": sequence,
            "allows_boarding": boarding,
            "allows_alighting": alighting,
        })

    normalized.sort(key=lambda s: s["sequence"])

    expected_sequences = list(range(1, len(normalized) + 1))
    actual_sequences = [s["sequence"] for s in normalized]
    if actual_sequences != expected_sequences:
        raise ValidationError("El orden de las paradas debe ser correlativo y comenzar en 1.")

    first = normalized[0]
    last = normalized[-1]

    if not first["allows_boarding"]:
        raise ValidationError(f"La primera parada ({first['stop'].name}) debe permitir subir pasajeros.")
    if not last["allows_alighting"]:
        raise ValidationError(f"La última parada ({last['stop'].name}) debe permitir bajar pasajeros.")

    has_interprovincial = False
    for i, origin in enumerate(normalized):
        if not origin["allows_boarding"]:
            continue
        for destination in normalized[i + 1:]:
            if not destination["allows_alighting"]:
                continue
            if origin["stop"].province != destination["stop"].province:
                has_interprovincial = True
                break
        if has_interprovincial:
            break

    if not has_interprovincial:
        raise ValidationError(
            "El recorrido debe permitir al menos un tramo interprovincial válido "
            "(origen y destino en diferentes provincias con permisos de subida y bajada)."
        )

    return normalized


@transaction.atomic
def save_route_configuration(*, code, name, stops_data, route=None, is_active=True, force_new_version=False):
    """Crea, edita o versiona un recorrido junto con sus paradas de forma atómica.

    - Si route es None: crea un nuevo Route (versión 1) y sus RouteStops.
    - Si route existe y no tiene viajes asociados y not force_new_version:
      edita el Route existente y actualiza sus RouteStops.
    - Si route existe y tiene viajes asociados (o force_new_version=True):
      preserva el Route existente (y sus RouteStops y viajes históricos),
      desactiva el recorrido anterior y crea una NUEVA VERSIÓN del recorrido
      con version = max_version + 1, parent_route = route, y las nuevas paradas.
    """
    code = (code or "").strip().upper()
    name = (name or "").strip()
    if not code:
        raise ValidationError("El código de recorrido no puede estar vacío.")
    if not name:
        raise ValidationError("El nombre de recorrido no puede estar vacío.")

    validated_stops = validate_route_stops(stops_data)

    if route is None:
        new_route = Route(code=code, name=name, version=1, is_active=is_active)
        new_route.full_clean()
        new_route.save()
        for item in validated_stops:
            rs = RouteStop(
                route=new_route,
                stop=item["stop"],
                sequence=item["sequence"],
                allows_boarding=item["allows_boarding"],
                allows_alighting=item["allows_alighting"],
            )
            rs.full_clean()
            rs.save()
        return new_route, False

    route = Route.objects.select_for_update().get(pk=route.pk)
    has_trips = route.trips.exists()

    if has_trips or force_new_version:
        max_v = Route.objects.filter(code=code).aggregate(m=models.Max("version"))["m"] or route.version
        new_version = max_v + 1
        if route.is_active:
            route.is_active = False
            route.save(update_fields=["is_active", "updated_at"])

        new_route = Route(
            code=code,
            name=name,
            version=new_version,
            parent_route=route,
            is_active=is_active,
        )
        new_route.full_clean()
        new_route.save()
        for item in validated_stops:
            rs = RouteStop(
                route=new_route,
                stop=item["stop"],
                sequence=item["sequence"],
                allows_boarding=item["allows_boarding"],
                allows_alighting=item["allows_alighting"],
            )
            rs.full_clean()
            rs.save()
        return new_route, True

    route.code = code
    route.name = name
    route.is_active = is_active
    route.full_clean()
    route.save(update_fields=["code", "name", "is_active", "updated_at"])

    # Reemplazo atómico de paradas para recorrido sin viajes
    route.route_stops.all().delete()
    for item in validated_stops:
        rs = RouteStop(
            route=route,
            stop=item["stop"],
            sequence=item["sequence"],
            allows_boarding=item["allows_boarding"],
            allows_alighting=item["allows_alighting"],
        )
        rs.full_clean()
        rs.save()
    return route, False
