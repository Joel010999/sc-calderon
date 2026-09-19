from datetime import date, datetime, timedelta
from decimal import Decimal
import logging
import uuid
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from operations.models import Bus, Route, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from sales.conf import (
    get_max_passengers_per_booking,
    get_online_cutoff_minutes,
    get_online_hold_minutes,
)
from sales.exceptions import BookingExpiredError, InvalidBookingError, SeatUnavailableError
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
    normalize_document,
)
from sales.services import (
    create_booking,
    create_online_booking,
    expire_booking,
    get_trip_availability,
    release_booking,
    validate_passenger_data,
)

logger = logging.getLogger(__name__)
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _get_active_stops():
    """Retorna las paradas con recorridos operativos para el selector de búsqueda."""
    return Stop.objects.filter(route_stops__isnull=False).distinct().order_by("name")


def _build_deck_map(bus, active_seats, occupied_seat_ids, fares_by_category, selected_seat_ids=None):
    """Construye la distribución dinámica de butacas por planta y coordenadas."""
    if selected_seat_ids is None:
        selected_seat_ids = set()

    occupied_set = set(occupied_seat_ids)
    selected_set = set(selected_seat_ids)

    decks = []
    for deck_val, deck_lbl in Seat.Deck.choices:
        items = []
        for seat in active_seats:
            if seat.deck != deck_val:
                continue
            is_occupied = seat.pk in occupied_set
            is_selected = seat.pk in selected_set
            fare_amount = fares_by_category.get(seat.category)
            has_fare = fare_amount is not None

            items.append({
                "seat": seat,
                "column": seat.position_x + 1,
                "row": seat.position_y + 1,
                "is_occupied": is_occupied,
                "is_selected": is_selected,
                "is_available": (not is_occupied) and has_fare,
                "has_fare": has_fare,
                "fare_amount": fare_amount,
                "category_display": seat.get_category_display(),
            })
        if items:
            decks.append({"value": deck_val, "label": deck_lbl, "items": items})
    return decks


def _find_available_trips(origin_stop, destination_stop, travel_date, passengers_count, now):
    """Encuentra viajes vigentes y disponibles para el tramo y fecha local indicada."""
    cutoff_minutes = get_online_cutoff_minutes()

    trips = Trip.objects.filter(
        status__in=[Trip.Status.SCHEDULED, Trip.Status.BOARDING]
    ).select_related("route", "bus").prefetch_related("trip_stops__stop")

    results = []
    for trip in trips:
        stops_by_id = {ts.stop_id: ts for ts in trip.trip_stops.all()}
        ts_orig = stops_by_id.get(origin_stop.pk)
        ts_dest = stops_by_id.get(destination_stop.pk)

        if not ts_orig or not ts_dest:
            continue

        if not ts_orig.allows_boarding or not ts_dest.allows_alighting:
            continue

        if ts_orig.sequence >= ts_dest.sequence:
            continue

        orig_local_dt = timezone.localtime(ts_orig.scheduled_at, AR_TZ)
        if travel_date and orig_local_dt.date() != travel_date:
            continue

        # Validar cutoff online concreto
        cutoff_time = ts_orig.scheduled_at - timedelta(minutes=cutoff_minutes)
        if now >= cutoff_time:
            continue

        # Consultar disponibilidad real y tarifas activas
        avail = get_trip_availability(trip, ts_orig, ts_dest, now=now)
        fares = avail.get("fares_by_category", {})
        if not fares:
            continue

        avail_seats_count = avail.get("available_seat_count", 0)
        if avail_seats_count < passengers_count:
            continue
        available_by_category = {}
        for available_seat in avail.get("available_seats", []):
            available_by_category[available_seat.category] = available_by_category.get(available_seat.category, 0) + 1

        dest_local_dt = timezone.localtime(ts_dest.scheduled_at, AR_TZ)
        duration = ts_dest.scheduled_at - ts_orig.scheduled_at
        hours = int(duration.total_seconds() // 3600)
        minutes = int((duration.total_seconds() % 3600) // 60)
        duration_str = f"{hours} h" if minutes == 0 else f"{hours} h {minutes} min"

        results.append({
            "trip": trip,
            "origin_stop": ts_orig,
            "destination_stop": ts_dest,
            "origin_local_dt": orig_local_dt,
            "dest_local_dt": dest_local_dt,
            "duration_str": duration_str,
            "available_seat_count": avail_seats_count,
            "fares": fares,
            "available_by_category": available_by_category,
            "fare_options": [
                {
                    "category": category,
                    "amount": amount,
                    "available_count": available_by_category.get(category, 0),
                }
                for category, amount in fares.items()
            ],
            "min_fare": min(fares.values()),
            "bus": trip.bus,
        })

    # Ordenar por horario de subida local
    results.sort(key=lambda r: r["origin_local_dt"])
    return results


def _nearby_search_links(origin_stop, destination_stop, outbound_date, return_date, trip_type, passengers_count):
    links = []
    for offset, label in [(-1, "Día anterior"), (1, "Día siguiente")]:
        date_value = outbound_date + timedelta(days=offset)
        params = {
            "origin": origin_stop.pk,
            "destination": destination_stop.pk,
            "date": date_value.isoformat(),
            "trip_type": trip_type,
            "passengers": passengers_count,
        }
        if return_date:
            params["return_date"] = (return_date + timedelta(days=offset)).isoformat()
        links.append({"label": label, "date": date_value, "url": f"{reverse('buscar_viajes')}?{urlencode(params)}"})
    return links


def home(request):
    """Página institucional principal con buscador integrado."""
    stops = _get_active_stops()
    today_str = timezone.localtime(timezone.now(), AR_TZ).date().isoformat()
    return render(request, "core/base.html", {
        "stops": stops,
        "today_str": today_str,
        "max_passengers": get_max_passengers_per_booking(),
    })


@require_GET
def search_trips(request):
    """Búsqueda pública de viajes por origen, destino, fechas y pasajeros."""
    now = timezone.now()
    stops = _get_active_stops()
    max_passengers = get_max_passengers_per_booking()
    today_str = timezone.localtime(now, AR_TZ).date().isoformat()

    origin_param = request.GET.get("origin", "").strip()
    dest_param = request.GET.get("destination", "").strip()
    trip_type = request.GET.get("trip_type", "oneway").strip()
    date_str = request.GET.get("date", "").strip()
    return_date_str = request.GET.get("return_date", "").strip()
    passengers_str = request.GET.get("passengers", "1").strip()

    # Validar pasajeros
    try:
        passengers_count = int(passengers_str)
        if passengers_count < 1 or passengers_count > max_passengers:
            passengers_count = 1
    except (ValueError, TypeError):
        passengers_count = 1

    # Si no hay parámetros de búsqueda, renderizar vista de búsqueda limpia
    if not origin_param and not dest_param and not date_str:
        return render(request, "core/search_results.html", {
            "stops": stops,
            "max_passengers": max_passengers,
            "today_str": today_str,
            "passengers_count": passengers_count,
            "trip_type": trip_type,
            "performed_search": False,
        })

    errors = []

    # Resolver parada de origen
    origin_stop = None
    if origin_param:
        if origin_param.isdigit():
            origin_stop = Stop.objects.filter(pk=int(origin_param)).first()
        else:
            origin_stop = Stop.objects.filter(code=origin_param).first()
    if not origin_stop:
        errors.append("Seleccioná una parada de origen válida.")

    # Resolver parada de destino
    dest_stop = None
    if dest_param:
        if dest_param.isdigit():
            dest_stop = Stop.objects.filter(pk=int(dest_param)).first()
        else:
            dest_stop = Stop.objects.filter(code=dest_param).first()
    if not dest_stop:
        errors.append("Seleccioná una parada de destino válida.")

    if origin_stop and dest_stop and origin_stop.pk == dest_stop.pk:
        errors.append("El origen y el destino deben ser paradas diferentes.")

    # Resolver fecha de ida
    outbound_date = None
    if date_str:
        try:
            outbound_date = date.fromisoformat(date_str)
        except ValueError:
            errors.append("La fecha de ida indicada no tiene un formato válido.")
    else:
        errors.append("Indicá la fecha para el viaje de ida.")

    # Resolver fecha de vuelta si es ida y vuelta
    return_date = None
    is_roundtrip = (trip_type == "roundtrip")
    if is_roundtrip:
        if return_date_str:
            try:
                return_date = date.fromisoformat(return_date_str)
                if outbound_date and return_date < outbound_date:
                    errors.append("La fecha de regreso no puede ser anterior a la fecha de ida.")
            except ValueError:
                errors.append("La fecha de regreso indicada no tiene un formato válido.")
        else:
            errors.append("Indicá la fecha para el viaje de regreso.")

    if errors:
        return render(request, "core/search_results.html", {
            "stops": stops,
            "max_passengers": max_passengers,
            "today_str": today_str,
            "errors": errors,
            "performed_search": True,
            "origin_stop": origin_stop,
            "dest_stop": dest_stop,
            "outbound_date": outbound_date,
            "return_date": return_date,
            "passengers_count": passengers_count,
            "trip_type": trip_type,
            "outbound_trips": [],
            "return_trips": [],
        })

    # Búsqueda de viajes de ida
    outbound_trips = _find_available_trips(origin_stop, dest_stop, outbound_date, passengers_count, now)

    # Búsqueda de viajes de vuelta si corresponde
    return_trips = []
    if is_roundtrip:
        return_trips = _find_available_trips(dest_stop, origin_stop, return_date, passengers_count, now)

    return render(request, "core/search_results.html", {
        "stops": stops,
        "max_passengers": max_passengers,
        "today_str": today_str,
        "performed_search": True,
        "origin_stop": origin_stop,
        "dest_stop": dest_stop,
        "date_str": date_str,
        "return_date_str": return_date_str,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "passengers_count": passengers_count,
        "trip_type": trip_type,
        "is_roundtrip": is_roundtrip,
        "outbound_trips": outbound_trips,
        "return_trips": return_trips,
        "cutoff_minutes": get_online_cutoff_minutes(),
        "nearby_searches": _nearby_search_links(
            origin_stop, dest_stop, outbound_date, return_date, trip_type, passengers_count
        ),
    })


@require_http_methods(["GET", "POST"])
def checkout_view(request):
    """Vista de checkout público con selección interactiva de butacas y formulario de pasajeros."""
    now = timezone.now()
    cutoff_minutes = get_online_cutoff_minutes()
    max_passengers = get_max_passengers_per_booking()

    # Obtener parámetros de itinerario (desde GET o POST)
    src = request.POST if request.method == "POST" and "confirm_booking" not in request.POST else request.GET

    trip_type = src.get("trip_type", "oneway").strip()
    is_roundtrip = (trip_type == "roundtrip")

    try:
        passengers_count = int(src.get("passengers", src.get("passengers_count", 1)))
        if passengers_count < 1 or passengers_count > max_passengers:
            passengers_count = 1
    except (ValueError, TypeError):
        passengers_count = 1

    outbound_radio = src.get("outbound_trip_radio")
    if outbound_radio and ":" in outbound_radio:
        parts = outbound_radio.split(":")
        outbound_trip_id, outbound_orig_id, outbound_dest_id = parts[0], parts[1], parts[2]
    else:
        outbound_trip_id = src.get("outbound_trip") or src.get("trip")
        outbound_orig_id = src.get("outbound_origin") or src.get("origin")
        outbound_dest_id = src.get("outbound_destination") or src.get("destination")

    return_radio = src.get("return_trip_radio")
    if return_radio and ":" in return_radio:
        parts = return_radio.split(":")
        return_trip_id, return_orig_id, return_dest_id = parts[0], parts[1], parts[2]
    else:
        return_trip_id = src.get("return_trip")
        return_orig_id = src.get("return_origin")
        return_dest_id = src.get("return_destination")

    if not outbound_trip_id or not outbound_orig_id or not outbound_dest_id:
        messages.warning(request, "Seleccioná un viaje para continuar con tu reserva.")
        return redirect("buscar_viajes")

    # Validar viaje y paradas de ida
    try:
        outbound_trip = Trip.objects.select_related("bus", "route").get(pk=int(outbound_trip_id))
        outbound_orig = TripStop.objects.select_related("stop").get(pk=int(outbound_orig_id), trip=outbound_trip)
        outbound_dest = TripStop.objects.select_related("stop").get(pk=int(outbound_dest_id), trip=outbound_trip)
    except (Trip.DoesNotExist, TripStop.DoesNotExist, ValueError):
        messages.error(request, "El viaje o las paradas seleccionadas ya no están disponibles.")
        return redirect("buscar_viajes")

    # Validaciones de reglas de negocio para la ida
    if outbound_trip.status in [Trip.Status.STARTED, Trip.Status.COMPLETED, Trip.Status.CANCELLED]:
        messages.error(request, "El viaje seleccionado ya no admite reservas online.")
        return redirect("buscar_viajes")

    cutoff_time = outbound_orig.scheduled_at - timedelta(minutes=cutoff_minutes)
    if now >= cutoff_time:
        messages.error(request, f"La venta online cierra {cutoff_minutes} minutos antes del horario de subida.")
        return redirect("buscar_viajes")

    if outbound_orig.sequence >= outbound_dest.sequence:
        messages.error(request, "La parada de destino debe ser posterior a la de origen.")
        return redirect("buscar_viajes")

    if not outbound_orig.allows_boarding or not outbound_dest.allows_alighting:
        messages.error(request, "Las paradas seleccionadas no admiten ascenso o descenso de pasajeros.")
        return redirect("buscar_viajes")

    outbound_avail = get_trip_availability(outbound_trip, outbound_orig, outbound_dest, now=now)
    outbound_fares = outbound_avail.get("fares_by_category", {})
    if not outbound_fares:
        messages.error(request, "No existen tarifas activas para el tramo seleccionado.")
        return redirect("buscar_viajes")

    # Validar vuelta si aplica
    return_trip = None
    return_orig = None
    return_dest = None
    return_fares = {}
    return_decks = []

    if is_roundtrip:
        return_trip_id = src.get("return_trip")
        return_orig_id = src.get("return_origin")
        return_dest_id = src.get("return_destination")

        if not return_trip_id or not return_orig_id or not return_dest_id:
            messages.warning(request, "Seleccioná el viaje de regreso para continuar.")
            return redirect("buscar_viajes")

        try:
            return_trip = Trip.objects.select_related("bus", "route").get(pk=int(return_trip_id))
            return_orig = TripStop.objects.select_related("stop").get(pk=int(return_orig_id), trip=return_trip)
            return_dest = TripStop.objects.select_related("stop").get(pk=int(return_dest_id), trip=return_trip)
        except (Trip.DoesNotExist, TripStop.DoesNotExist, ValueError):
            messages.error(request, "El viaje de regreso seleccionado ya no está disponible.")
            return redirect("buscar_viajes")

        if return_trip.pk == outbound_trip.pk:
            messages.error(request, "El viaje de regreso debe ser un viaje diferente al de ida.")
            return redirect("buscar_viajes")

        if return_trip.status in [Trip.Status.STARTED, Trip.Status.COMPLETED, Trip.Status.CANCELLED]:
            messages.error(request, "El viaje de regreso ya no admite reservas online.")
            return redirect("buscar_viajes")

        ret_cutoff_time = return_orig.scheduled_at - timedelta(minutes=cutoff_minutes)
        if now >= ret_cutoff_time:
            messages.error(request, f"La venta online para el regreso cierra {cutoff_minutes} minutos antes de la subida.")
            return redirect("buscar_viajes")

        if return_orig.stop_id != outbound_dest.stop_id or return_dest.stop_id != outbound_orig.stop_id:
            messages.error(request, "El viaje de regreso debe invertir exactamente el origen y destino de la ida.")
            return redirect("buscar_viajes")

        if return_orig.scheduled_at <= outbound_dest.scheduled_at:
            messages.error(request, "El viaje de regreso debe salir después de la llegada del viaje de ida.")
            return redirect("buscar_viajes")

        return_avail = get_trip_availability(return_trip, return_orig, return_dest, now=now)
        return_fares = return_avail.get("fares_by_category", {})
        if not return_fares:
            messages.error(request, "No existen tarifas activas para el regreso.")
            return redirect("buscar_viajes")

        # Butacas de regreso
        ret_active_seats = list(Seat.objects.filter(bus=return_trip.bus, is_active=True).order_by("number"))
        ret_occupied_seat_ids = set(
            SeatAssignment.objects.filter(
                trip=return_trip,
                status__in=[AssignmentStatus.HELD, AssignmentStatus.CONFIRMED],
            ).values_list("seat_id", flat=True)
        )
        return_decks = _build_deck_map(return_trip.bus, ret_active_seats, ret_occupied_seat_ids, return_fares)

    # Butacas de ida
    out_active_seats = list(Seat.objects.filter(bus=outbound_trip.bus, is_active=True).order_by("number"))
    out_occupied_seat_ids = set(
        SeatAssignment.objects.filter(
            trip=outbound_trip,
            status__in=[AssignmentStatus.HELD, AssignmentStatus.CONFIRMED],
        ).values_list("seat_id", flat=True)
    )
    outbound_decks = _build_deck_map(outbound_trip.bus, out_active_seats, out_occupied_seat_ids, outbound_fares)

    return render(request, "core/checkout.html", {
        "is_roundtrip": is_roundtrip,
        "trip_type": trip_type,
        "passengers_count": passengers_count,
        "passenger_range": list(range(1, passengers_count + 1)),
        "outbound_trip": outbound_trip,
        "outbound_orig": outbound_orig,
        "outbound_dest": outbound_dest,
        "outbound_orig_local": timezone.localtime(outbound_orig.scheduled_at, AR_TZ),
        "outbound_dest_local": timezone.localtime(outbound_dest.scheduled_at, AR_TZ),
        "outbound_fares": outbound_fares,
        "outbound_decks": outbound_decks,
        "return_trip": return_trip,
        "return_orig": return_orig,
        "return_dest": return_dest,
        "return_orig_local": timezone.localtime(return_orig.scheduled_at, AR_TZ) if return_orig else None,
        "return_dest_local": timezone.localtime(return_dest.scheduled_at, AR_TZ) if return_dest else None,
        "return_fares": return_fares,
        "return_decks": return_decks,
        "cutoff_minutes": cutoff_minutes,
        "today_str": timezone.localtime(now, AR_TZ).date().isoformat(),
    })


# Alias para retrocompatibilidad con url 'checkout'
def checkout(request):
    if request.GET.get("outbound_trip") or request.GET.get("trip"):
        return checkout_view(request)
    return redirect("buscar_viajes")


@require_POST
def create_public_booking(request):
    """POST final que crea ONLINE HELD mediante sales.services.create_booking.

    Revalida íntegramente la selección en servidor, protege con honeypot, CSRF y
    limitación razonable de reservas sin Redis ni Celery.
    """
    now = timezone.now()
    now_ts = now.timestamp()

    # 1. Protección Honeypot (campo invisible para usuarios reales)
    honeypot = request.POST.get("website", "").strip()
    if honeypot:
        return HttpResponseBadRequest("Solicitud no válida.")

    # 2. Limitación razonable de holds sin Redis/Celery basada en sesión
    recent_holds = request.session.get("recent_holds", [])
    recent_holds = [ts for ts in recent_holds if now_ts - ts < 900]  # Ventana de 15 minutos
    if len(recent_holds) >= 3:
        messages.error(
            request,
            "Has alcanzado el límite de reservas retenidas simultáneas. "
            "Por favor aguardá unos minutos antes de generar una nueva solicitud."
        )
        return redirect("buscar_viajes")

    # 3. Datos de contacto y pasajeros
    email = request.POST.get("email", "").strip()
    phone = request.POST.get("phone", "").strip()

    if not email:
        messages.error(request, "El correo electrónico de contacto es obligatorio.")
        return redirect("buscar_viajes")

    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, "Ingresá un correo electrónico válido.")
        return redirect("buscar_viajes")

    trip_type = request.POST.get("trip_type", "oneway").strip()
    is_roundtrip = (trip_type == "roundtrip")

    try:
        passengers_count = int(request.POST.get("passengers_count", 1))
        max_allowed = get_max_passengers_per_booking()
        if passengers_count < 1 or passengers_count > max_allowed:
            messages.error(request, f"La cantidad de pasajeros debe estar entre 1 y {max_allowed}.")
            return redirect("buscar_viajes")
    except (ValueError, TypeError):
        messages.error(request, "Cantidad de pasajeros no válida.")
        return redirect("buscar_viajes")

    # 4. Extraer y validar butacas seleccionadas
    outbound_seat_pks = request.POST.getlist("outbound_seats")
    if len(outbound_seat_pks) != passengers_count:
        messages.error(
            request,
            f"Debes seleccionar exactamente {passengers_count} butaca(s) para el viaje de ida "
            f"(seleccionaste {len(outbound_seat_pks)})."
        )
        return redirect("buscar_viajes")

    return_seat_pks = []
    if is_roundtrip:
        return_seat_pks = request.POST.getlist("return_seats")
        if len(return_seat_pks) != passengers_count:
            messages.error(
                request,
                f"Debes seleccionar exactamente {passengers_count} butaca(s) para el viaje de regreso "
                f"(seleccionaste {len(return_seat_pks)})."
            )
            return redirect("buscar_viajes")

    # 5. Extraer y validar datos personales de cada pasajero
    passengers_data = []
    for pos in range(1, passengers_count + 1):
        p_dict = {
            "first_name": request.POST.get(f"passenger_{pos}_first_name", "").strip(),
            "last_name": request.POST.get(f"passenger_{pos}_last_name", "").strip(),
            "document_type": request.POST.get(f"passenger_{pos}_document_type", "DNI").strip(),
            "document_number": request.POST.get(f"passenger_{pos}_document_number", "").strip(),
            "birth_date": request.POST.get(f"passenger_{pos}_birth_date", "").strip(),
            "nationality": request.POST.get(f"passenger_{pos}_nationality", "Argentina").strip(),
            "gender": request.POST.get(f"passenger_{pos}_gender", "").strip(),
        }
        try:
            validate_passenger_data(p_dict, position=pos)
        except ValidationError as ve:
            err_msg = next(iter(ve.message_dict.values()))[0] if hasattr(ve, "message_dict") else str(ve)
            messages.error(request, err_msg)
            return redirect("buscar_viajes")
        passengers_data.append(p_dict)

    # 6. Revalidar viajes y paradas desde la base de datos
    outbound_trip_id = request.POST.get("outbound_trip")
    outbound_orig_id = request.POST.get("outbound_origin")
    outbound_dest_id = request.POST.get("outbound_destination")

    try:
        outbound_trip = Trip.objects.get(pk=int(outbound_trip_id))
        outbound_orig = TripStop.objects.get(pk=int(outbound_orig_id), trip=outbound_trip)
        outbound_dest = TripStop.objects.get(pk=int(outbound_dest_id), trip=outbound_trip)
        outbound_seats = list(Seat.objects.filter(pk__in=[int(pk) for pk in outbound_seat_pks], bus=outbound_trip.bus))
        if len(outbound_seats) != len(outbound_seat_pks):
            raise ValidationError("Una o más butacas no pertenecen al colectivo del viaje de ida.")
    except (Trip.DoesNotExist, TripStop.DoesNotExist, Seat.DoesNotExist, ValueError, ValidationError) as exc:
        messages.error(request, str(exc) if isinstance(exc, ValidationError) else "Datos del viaje de ida no válidos.")
        return redirect("buscar_viajes")

    legs = [
        {
            "trip": outbound_trip,
            "origin_stop": outbound_orig,
            "destination_stop": outbound_dest,
            "seats": outbound_seats,
        }
    ]

    if is_roundtrip:
        return_trip_id = request.POST.get("return_trip")
        return_orig_id = request.POST.get("return_origin")
        return_dest_id = request.POST.get("return_destination")
        try:
            return_trip = Trip.objects.get(pk=int(return_trip_id))
            return_orig = TripStop.objects.get(pk=int(return_orig_id), trip=return_trip)
            return_dest = TripStop.objects.get(pk=int(return_dest_id), trip=return_trip)
            return_seats = list(Seat.objects.filter(pk__in=[int(pk) for pk in return_seat_pks], bus=return_trip.bus))
            if len(return_seats) != len(return_seat_pks):
                raise ValidationError("Una o más butacas no pertenecen al colectivo del viaje de regreso.")
        except (Trip.DoesNotExist, TripStop.DoesNotExist, Seat.DoesNotExist, ValueError, ValidationError) as exc:
            messages.error(request, str(exc) if isinstance(exc, ValidationError) else "Datos del viaje de regreso no válidos.")
            return redirect("buscar_viajes")

        legs.append({
            "trip": return_trip,
            "origin_stop": return_orig,
            "destination_stop": return_dest,
            "seats": return_seats,
        })

    # 7. Crear la reserva ONLINE HELD transaccionalmente
    request.session["checkout_flow"] = {
        "trip_type": trip_type,
        "passengers_count": passengers_count,
        "outbound_trip": str(outbound_trip.pk),
        "outbound_origin": str(outbound_orig.pk),
        "outbound_destination": str(outbound_dest.pk),
        "outbound_seats": [str(seat.pk) for seat in outbound_seats],
        "return_trip": str(return_trip.pk) if is_roundtrip else None,
        "return_origin": str(return_orig.pk) if is_roundtrip else None,
        "return_destination": str(return_dest.pk) if is_roundtrip else None,
        "return_seats": [str(seat.pk) for seat in return_seats] if is_roundtrip else [],
    }
    request.session.modified = True

    try:
        booking = create_online_booking(
            email=email,
            phone=phone,
            legs=legs,
            passengers_data=passengers_data,
        )
    except SeatUnavailableError as sue:
        messages.error(request, str(sue))
        return redirect("buscar_viajes")
    except ValidationError as ve:
        err_msg = next(iter(ve.message_dict.values()))[0] if hasattr(ve, "message_dict") else str(ve)
        messages.error(request, err_msg)
        return redirect("buscar_viajes")
    except Exception as exc:
        logger.exception("Error inesperado al crear reserva online")
        messages.error(request, "Ocurrió un error al procesar la reserva. Por favor intentá nuevamente.")
        return redirect("buscar_viajes")

    # 8. Proteger el resumen mediante token de sesión y actualizar limitación de holds
    # Nota de seguridad: NO almacenar datos personales en cookies ni sesión
    session_token = uuid.uuid4().hex
    request.session[f"booking_access_{booking.public_id}"] = session_token
    recent_holds.append(now_ts)
    request.session["recent_holds"] = recent_holds
    request.session["active_held_booking_id"] = str(booking.public_id)
    request.session.pop("checkout_flow", None)

    return redirect("resumen_reserva", public_id=booking.public_id)


@require_GET
def booking_summary(request, public_id):
    """Resumen de reserva online protegido por token de sesión y public_id."""
    # Verificación de autorización de sesión (protección IDOR)
    session_token = request.session.get(f"booking_access_{public_id}")
    if not session_token:
        return HttpResponseForbidden("No tenés permiso para acceder al resumen de esta reserva.")

    booking = get_object_or_404(
        Booking.objects.prefetch_related(
            "legs__trip__bus",
            "legs__trip__route",
            "legs__origin_stop__stop",
            "legs__destination_stop__stop",
            "legs__seat_assignments__seat",
            "passengers",
        ),
        public_id=public_id,
        channel=BookingChannel.ONLINE,
    )

    now = timezone.now()

    # Expiración automática si venció el plazo de retención HELD
    if booking.status == BookingStatus.HELD and now >= booking.expires_at:
        try:
            expire_booking(booking, now=now)
            booking.refresh_from_db()
        except Exception:
            logger.exception("Error al expirar reserva en resumen")

    # Cálculo de segundos restantes para el temporizador regresivo
    remaining_seconds = 0
    if booking.status == BookingStatus.HELD:
        diff = (booking.expires_at - now).total_seconds()
        remaining_seconds = max(0, int(diff))

    # Cálculo total a partir de los precios unitarios históricos Decimal
    total = Decimal("0.00")
    for leg in booking.legs.all():
        for sa in leg.seat_assignments.all():
            total += sa.price

    # Formateo de tramos con fechas locales argentinas
    legs_data = []
    for leg in booking.legs.all():
        orig_local = timezone.localtime(leg.departure_at, AR_TZ)
        dest_local = timezone.localtime(leg.arrival_at, AR_TZ)
        assignments = list(leg.seat_assignments.select_related("passenger", "seat").order_by("seat_number"))
        legs_data.append({
            "leg": leg,
            "origin_local": orig_local,
            "dest_local": dest_local,
            "assignments": assignments,
        })

    return render(request, "core/summary.html", {
        "booking": booking,
        "legs_data": legs_data,
        "total": total,
        "remaining_seconds": remaining_seconds,
        "is_held": (booking.status == BookingStatus.HELD),
        "is_expired": (booking.status == BookingStatus.EXPIRED),
        "is_released": (booking.status == BookingStatus.RELEASED),
        "is_confirmed": (booking.status == BookingStatus.CONFIRMED),
        "expires_at_local": timezone.localtime(booking.expires_at, AR_TZ),
    })


@require_POST
def expire_public_booking(request, public_id):
    """Acción POST para cancelar o liberar una reserva HELD desde el resumen público."""
    session_token = request.session.get(f"booking_access_{public_id}")
    if not session_token:
        return HttpResponseForbidden("No tenés permiso para operar sobre esta reserva.")

    booking = get_object_or_404(Booking, public_id=public_id, channel=BookingChannel.ONLINE)

    if booking.status == BookingStatus.HELD:
        try:
            release_booking(booking)
            messages.info(request, "La reserva fue cancelada y las butacas quedaron liberadas.")
        except InvalidBookingError as ibe:
            messages.error(request, str(ibe))

    return redirect("resumen_reserva", public_id=public_id)


@require_GET
def payment_pending(request, public_id):
    """Pantalla honesta del siguiente paso, sin crear ni confirmar pagos públicos."""
    session_token = request.session.get(f"booking_access_{public_id}")
    if not session_token:
        return HttpResponseForbidden("No tenés permiso para acceder a esta reserva.")
    booking = get_object_or_404(
        Booking,
        public_id=public_id,
        channel=BookingChannel.ONLINE,
    )
    return render(request, "core/payment_pending.html", {"booking": booking})


def login_cliente(request):
    """Vista preparada para el login de clientes (pasajeros)."""
    return render(request, "core/base.html")


def registro_cliente(request):
    """Vista preparada para el registro de clientes (pasajeros)."""
    return render(request, "core/base.html")


def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            if row and row[0] == 1:
                return JsonResponse({"status": "ok", "database": "ok"}, status=200)
    except Exception:
        logger.exception("Database health check failed")

    return JsonResponse({"status": "error", "database": "error"}, status=503)
