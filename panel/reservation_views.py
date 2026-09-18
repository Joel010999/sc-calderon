"""Vistas del panel para gestión de reservas manuales."""

from datetime import date, datetime
from decimal import Decimal
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from operations.models import Bus, Route, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from sales.conf import get_manual_hold_hours, get_max_passengers_per_booking
from sales.exceptions import InvalidBookingError, SeatUnavailableError
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingStatus,
    SeatAssignment,
    normalize_document,
)
from sales.services import get_trip_availability, validate_passenger_data
from .models import AuditEvent
from .permissions import reservations_access
from .reservation_services import (
    create_panel_manual_booking,
    release_panel_booking,
)


@reservations_access()
@require_GET
def booking_list(request):
    """Listado paginado de reservas con filtros y búsqueda."""
    queryset = Booking.objects.select_related("seller").prefetch_related(
        "legs__trip__route", "passengers"
    ).order_by("-created_at", "-pk")

    # Filtro por estado
    status_filter = request.GET.get("status", "").strip()
    if status_filter in BookingStatus.values:
        queryset = queryset.filter(status=status_filter)

    # Filtro por fecha (YYYY-MM-DD)
    date_filter = request.GET.get("date", "").strip()
    if date_filter:
        try:
            parsed_date = date.fromisoformat(date_filter)
            queryset = queryset.filter(created_at__date=parsed_date)
        except ValueError:
            pass

    # Filtro por viaje
    trip_filter = request.GET.get("trip", "").strip()
    if trip_filter and trip_filter.isdigit():
        queryset = queryset.filter(legs__trip_id=int(trip_filter))

    # Filtro por vendedor
    seller_filter = request.GET.get("seller", "").strip()
    if seller_filter and seller_filter.isdigit():
        queryset = queryset.filter(seller_id=int(seller_filter))

    # Búsqueda por identificador, email o documento normalizado
    q = request.GET.get("q", "").strip()
    if q:
        norm_doc = normalize_document(q)
        search_query = Q(email__icontains=q)
        # Intentar como UUID
        try:
            val_uuid = uuid.UUID(q)
            search_query |= Q(public_id=val_uuid)
        except ValueError:
            # Búsqueda parcial de UUID en string si no es UUID completo
            if len(q) >= 4 and all(c in "0123456789abcdefABCDEF-" for c in q):
                search_query |= Q(public_id__icontains=q)

        if norm_doc:
            search_query |= Q(passengers__normalized_document__icontains=norm_doc)

        queryset = queryset.filter(search_query)

    # Deduplicar claves primarias preservando orden
    matching_ids = list(dict.fromkeys(queryset.values_list("pk", flat=True)))
    if matching_ids:
        # Reconstruir queryset ordenado
        queryset = Booking.objects.filter(pk__in=matching_ids).select_related("seller").prefetch_related(
            "legs__trip__route", "passengers"
        ).order_by("-created_at", "-pk")
    else:
        queryset = Booking.objects.none()

    # Paginación (15 reservas por página)
    paginator = Paginator(queryset, 15)
    page_number = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page_number)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    # Datos para filtros en el template
    all_trips = Trip.objects.select_related("route").order_by("-departure_at")[:50]
    all_sellers = Booking.objects.filter(seller__isnull=False).values(
        "seller_id", "seller__username"
    ).distinct()

    return render(request, "panel/reservations/list.html", {
        "page_obj": page_obj,
        "statuses": BookingStatus.choices,
        "selected_status": status_filter,
        "selected_date": date_filter,
        "selected_trip": trip_filter,
        "selected_seller": seller_filter,
        "search_q": q,
        "all_trips": all_trips,
        "all_sellers": all_sellers,
    })


@reservations_access()
@require_GET
def booking_detail(request, public_id):
    """Detalle completo de una reserva con auditoría y acción de liberación (solo HELD)."""
    booking = get_object_or_404(
        Booking.objects.select_related("seller").prefetch_related(
            "legs__trip__route",
            "legs__trip__bus",
            "legs__origin_stop__stop",
            "legs__destination_stop__stop",
            "legs__seat_assignments__seat",
            "legs__seat_assignments__passenger",
            "passengers",
        ),
        public_id=public_id,
    )

    # Calcular total de la reserva a partir de los precios históricos
    total = Decimal("0.00")
    for leg in booking.legs.all():
        for sa in leg.seat_assignments.all():
            total += sa.price

    # Eventos de auditoría de la reserva
    audit_events = AuditEvent.objects.filter(
        entity_type=booking._meta.label,
        entity_id=str(booking.pk),
    ).select_related("actor").order_by("-created_at", "-pk")

    can_release = (booking.status == BookingStatus.HELD)

    return render(request, "panel/reservations/detail.html", {
        "booking": booking,
        "total": total,
        "audit_events": audit_events,
        "can_release": can_release,
    })


@reservations_access()
@require_POST
def booking_release(request, public_id):
    """Libera una reserva en estado HELD (acción POST con CSRF). No admite confirmación."""
    booking = get_object_or_404(Booking, public_id=public_id)

    if booking.status == BookingStatus.CONFIRMED:
        messages.error(request, "No se puede liberar una reserva confirmada sin una política de cancelación aprobada.")
        return redirect("panel:booking_detail", public_id=public_id)

    if booking.status == BookingStatus.RELEASED:
        messages.info(request, "La reserva ya se encontraba liberada.")
        return redirect("panel:booking_detail", public_id=public_id)

    if booking.status != BookingStatus.HELD:
        messages.error(request, f"No se puede liberar una reserva en estado {booking.get_status_display()}.")
        return redirect("panel:booking_detail", public_id=public_id)

    try:
        release_panel_booking(actor=request.user, booking_or_id=booking)
        messages.success(request, f"La reserva {booking.public_id} fue liberada correctamente y sus butacas quedaron disponibles.")
    except InvalidBookingError as e:
        messages.error(request, str(e))

    return redirect("panel:booking_detail", public_id=public_id)


def _build_deck_map(bus, active_seats, occupied_seat_ids, fares_by_category, selected_seat_ids=None):
    """Construye la distribución de butacas por planta para el colectivo."""
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


@reservations_access()
@require_http_methods(["GET", "POST"])
def booking_create(request):
    """Creación de reserva manual (solo ida o ida y vuelta).

    Calcula disponibilidad real, valida reglas en servidor y llama a create_manual_booking.
    """
    now = timezone.now()
    max_passengers = get_max_passengers_per_booking()
    manual_hold_hours = get_manual_hold_hours()

    # Viajes programados vigentes (no iniciados, no finalizados, no cancelados, salida futura)
    eligible_trips = Trip.objects.filter(
        status__in=[Trip.Status.SCHEDULED, Trip.Status.BOARDING],
        departure_at__gt=now,
    ).exclude(
        status__in=[Trip.Status.STARTED, Trip.Status.COMPLETED, Trip.Status.CANCELLED]
    ).select_related("route", "bus").prefetch_related(
        "trip_stops__stop", "fares"
    ).order_by("departure_at")

    # Parámetros básicos
    trip_type = request.POST.get("trip_type") if request.method == "POST" else request.GET.get("trip_type", "oneway")
    is_roundtrip = (trip_type == "roundtrip")

    passenger_count_raw = request.POST.get("passengers_count") if request.method == "POST" else request.GET.get("passengers_count", "1")
    try:
        passenger_count = int(passenger_count_raw)
        if passenger_count < 1 or passenger_count > max_passengers:
            passenger_count = 1
    except (ValueError, TypeError):
        passenger_count = 1

    # Tramo de ida
    outbound_trip_id = request.POST.get("outbound_trip") if request.method == "POST" else request.GET.get("outbound_trip", "")
    outbound_orig_id = request.POST.get("outbound_origin") if request.method == "POST" else request.GET.get("outbound_origin", "")
    outbound_dest_id = request.POST.get("outbound_destination") if request.method == "POST" else request.GET.get("outbound_destination", "")

    # Tramo de vuelta (si aplica)
    return_trip_id = request.POST.get("return_trip") if request.method == "POST" else request.GET.get("return_trip", "")
    return_orig_id = request.POST.get("return_origin") if request.method == "POST" else request.GET.get("return_origin", "")
    return_dest_id = request.POST.get("return_destination") if request.method == "POST" else request.GET.get("return_destination", "")

    contact_email = (request.POST.get("contact_email") or "").strip()
    contact_phone = (request.POST.get("contact_phone") or "").strip()

    # Resolver viajes y paradas de ida
    outbound_trip = None
    outbound_orig = None
    outbound_dest = None
    outbound_decks = []
    outbound_avail = None
    outbound_fares = {}

    if outbound_trip_id and outbound_trip_id.isdigit():
        outbound_trip = eligible_trips.filter(pk=int(outbound_trip_id)).first()

    outbound_stops = []
    if outbound_trip:
        outbound_stops = list(outbound_trip.trip_stops.select_related("stop").order_by("sequence"))
        if outbound_orig_id and outbound_orig_id.isdigit():
            outbound_orig = next((s for s in outbound_stops if s.pk == int(outbound_orig_id)), None)
        if outbound_dest_id and outbound_dest_id.isdigit():
            outbound_dest = next((s for s in outbound_stops if s.pk == int(outbound_dest_id)), None)

        if outbound_orig and outbound_dest and outbound_orig.sequence < outbound_dest.sequence:
            try:
                outbound_avail = get_trip_availability(outbound_trip, outbound_orig, outbound_dest)
                outbound_fares = outbound_avail["fares_by_category"]
                occupied_ids = set(
                    SeatAssignment.objects.filter(
                        trip=outbound_trip,
                        status__in=[AssignmentStatus.HELD, AssignmentStatus.CONFIRMED],
                    ).values_list("seat_id", flat=True)
                )
                active_seats = list(Seat.objects.filter(bus=outbound_trip.bus, is_active=True).order_by("number"))
                selected_out_seats = {
                    int(s) for s in request.POST.getlist("outbound_seats") if s.isdigit()
                } if request.method == "POST" else set()
                outbound_decks = _build_deck_map(
                    outbound_trip.bus, active_seats, occupied_ids, outbound_fares, selected_out_seats
                )
            except ValidationError:
                pass

    # Resolver viaje de vuelta si aplica
    return_trip = None
    return_orig = None
    return_dest = None
    return_decks = []
    return_avail = None
    return_fares = {}
    eligible_return_trips = []

    if is_roundtrip and outbound_trip and outbound_orig and outbound_dest:
        # Los viajes de vuelta deben invertir las paradas del viaje de ida
        # Origen de la vuelta debe coincidir con destino de la ida, y destino de vuelta con origen de ida
        target_ret_orig_stop_id = outbound_dest.stop_id
        target_ret_dest_stop_id = outbound_orig.stop_id
        outbound_arrival = outbound_dest.scheduled_at

        # Filtrar viajes elegibles para la vuelta
        candidate_return_trips = eligible_trips.exclude(pk=outbound_trip.pk)
        for cand in candidate_return_trips:
            c_stops = {s.stop_id: s for s in cand.trip_stops.all()}
            c_orig = c_stops.get(target_ret_orig_stop_id)
            c_dest = c_stops.get(target_ret_dest_stop_id)
            if c_orig and c_dest and c_orig.sequence < c_dest.sequence:
                if c_orig.allows_boarding and c_dest.allows_alighting:
                    if c_orig.scheduled_at > outbound_arrival:
                        eligible_return_trips.append((cand, c_orig, c_dest))

        if return_trip_id and return_trip_id.isdigit():
            match = next((item for item in eligible_return_trips if item[0].pk == int(return_trip_id)), None)
            if match:
                return_trip, return_orig, return_dest = match
                try:
                    return_avail = get_trip_availability(return_trip, return_orig, return_dest)
                    return_fares = return_avail["fares_by_category"]
                    occupied_ret_ids = set(
                        SeatAssignment.objects.filter(
                            trip=return_trip,
                            status__in=[AssignmentStatus.HELD, AssignmentStatus.CONFIRMED],
                        ).values_list("seat_id", flat=True)
                    )
                    active_ret_seats = list(Seat.objects.filter(bus=return_trip.bus, is_active=True).order_by("number"))
                    selected_ret_seats = {
                        int(s) for s in request.POST.getlist("return_seats") if s.isdigit()
                    } if request.method == "POST" else set()
                    return_decks = _build_deck_map(
                        return_trip.bus, active_ret_seats, occupied_ret_ids, return_fares, selected_ret_seats
                    )
                except ValidationError:
                    pass

    # Manejo del POST para crear la reserva
    if request.method == "POST" and request.POST.get("action") == "create_booking":
        form_errors = []

        if not contact_email:
            form_errors.append("El correo de contacto es obligatorio.")

        if not outbound_trip or not outbound_orig or not outbound_dest:
            form_errors.append("Debés seleccionar el viaje y las paradas de ida válidas.")

        if is_roundtrip and (not return_trip or not return_orig or not return_dest):
            form_errors.append("Debés seleccionar un viaje de vuelta válido.")

        # Obtener butacas seleccionadas de ida
        outbound_selected_seats = [
            int(s) for s in request.POST.getlist("outbound_seats") if s.isdigit()
        ]
        if len(outbound_selected_seats) != passenger_count:
            form_errors.append(
                f"Debés seleccionar exactamente {passenger_count} butaca(s) para el viaje de ida."
            )

        return_selected_seats = []
        if is_roundtrip:
            return_selected_seats = [
                int(s) for s in request.POST.getlist("return_seats") if s.isdigit()
            ]
            if len(return_selected_seats) != passenger_count:
                form_errors.append(
                    f"Debés seleccionar exactamente {passenger_count} butaca(s) para el viaje de vuelta."
                )

        # Recoger y validar datos de cada pasajero
        passengers_data = []
        for p_idx in range(1, passenger_count + 1):
            p_data = {
                "first_name": (request.POST.get(f"p_{p_idx}_first_name") or "").strip(),
                "last_name": (request.POST.get(f"p_{p_idx}_last_name") or "").strip(),
                "document_type": (request.POST.get(f"p_{p_idx}_document_type") or "DNI").strip(),
                "document_number": (request.POST.get(f"p_{p_idx}_document_number") or "").strip(),
                "birth_date": (request.POST.get(f"p_{p_idx}_birth_date") or "").strip(),
                "nationality": (request.POST.get(f"p_{p_idx}_nationality") or "Argentina").strip(),
                "gender": (request.POST.get(f"p_{p_idx}_gender") or "").strip(),
            }
            try:
                validate_passenger_data(p_data, position=p_idx)
            except ValidationError as ve:
                for err_field, err_msg in ve.message_dict.items():
                    form_errors.extend(err_msg)
            passengers_data.append(p_data)

        if not form_errors:
            # Armar los tramos para create_manual_booking
            legs = [{
                "trip": outbound_trip,
                "origin_stop": outbound_orig,
                "destination_stop": outbound_dest,
                "seats": outbound_selected_seats,
            }]
            if is_roundtrip:
                legs.append({
                    "trip": return_trip,
                    "origin_stop": return_orig,
                    "destination_stop": return_dest,
                    "seats": return_selected_seats,
                })

            try:
                booking = create_panel_manual_booking(
                    actor=request.user,
                    email=contact_email,
                    phone=contact_phone,
                    legs=legs,
                    passengers_data=passengers_data,
                )
                messages.success(
                    request,
                    f"Reserva manual {booking.public_id} creada exitosamente con vencimiento de {manual_hold_hours} horas."
                )
                return redirect("panel:booking_detail", public_id=booking.public_id)
            except (ValidationError, SeatUnavailableError, InvalidBookingError) as exc:
                if hasattr(exc, "message_dict"):
                    for field, errs in exc.message_dict.items():
                        for err in errs:
                            messages.error(request, err)
                elif hasattr(exc, "messages"):
                    for err in exc.messages:
                        messages.error(request, err)
                else:
                    messages.error(request, str(exc))
        else:
            for err in form_errors:
                messages.error(request, err)

    # Rango de pasajeros para los formularios en el template
    passengers_range = list(range(1, passenger_count + 1))

    return render(request, "panel/reservations/create.html", {
        "eligible_trips": eligible_trips,
        "trip_type": trip_type,
        "is_roundtrip": is_roundtrip,
        "passenger_count": passenger_count,
        "max_passengers": max_passengers,
        "manual_hold_hours": manual_hold_hours,
        "passengers_range": passengers_range,
        "outbound_trip": outbound_trip,
        "outbound_stops": outbound_stops,
        "outbound_orig": outbound_orig,
        "outbound_dest": outbound_dest,
        "outbound_decks": outbound_decks,
        "outbound_fares": outbound_fares,
        "eligible_return_trips": [t[0] for t in eligible_return_trips],
        "return_trip": return_trip,
        "return_orig": return_orig,
        "return_dest": return_dest,
        "return_decks": return_decks,
        "return_fares": return_fares,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
    })
