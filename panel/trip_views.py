from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import OuterRef, Prefetch, Subquery
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from operations.models import Bus, Route, Trip, TripFare, TripStop
from .operations_views import render_operations
from .permissions import operations_access
from .trip_forms import TripFareForm, TripScheduleForm, TripSelectionForm
from .trip_services import FARE_ERROR, create_panel_trip, save_fare, set_fare_active


CONFIRMATION_SALT = "panel.trip-schedule"


def trips_with_arrival():
    last_stop = TripStop.objects.filter(trip_id=OuterRef("pk")).order_by("-sequence")
    return Trip.objects.select_related("route", "bus").annotate(
        arrival_at=Subquery(last_stop.values("scheduled_at")[:1])
    )


@operations_access()
@require_GET
def trips(request):
    now = timezone.now()
    queryset = trips_with_arrival()
    return render_operations(
        request, "trips", title="Viajes", display_timezone=settings.TIME_ZONE,
        upcoming=queryset.filter(departure_at__gte=now).order_by("departure_at", "pk"),
        past=queryset.filter(departure_at__lt=now).order_by("-departure_at", "-pk"),
    )


@operations_access()
@require_GET
def trip_detail(request, pk):
    queryset = trips_with_arrival().prefetch_related(
        Prefetch("trip_stops", queryset=TripStop.objects.select_related("stop").order_by("sequence")),
        Prefetch("fares", queryset=TripFare.objects.select_related(
            "origin_stop__stop", "destination_stop__stop"
        ).order_by("origin_stop__sequence", "destination_stop__sequence", "seat_category", "pk")),
    )
    trip = get_object_or_404(queryset, pk=pk)
    return render_operations(request, "trip_detail", title=f"Viaje {trip.pk}",
                             trip=trip, display_timezone=settings.TIME_ZONE)


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def trip_select(request):
    form = TripSelectionForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        return redirect("panel:trip_schedule", route_pk=form.cleaned_data["route"].pk,
                        bus_pk=form.cleaned_data["bus"].pk)
    return render_operations(request, "trip_form", title="Nuevo viaje · Paso 1 de 2", form=form,
                             selection=True)


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def trip_schedule(request, route_pk, bus_pk):
    route = get_object_or_404(Route, pk=route_pk, is_active=True)
    bus = get_object_or_404(Bus.objects.filter(is_active=True, seats__is_active=True).distinct(), pk=bus_pk)
    form = TripScheduleForm(request.POST if request.method == "POST" else None, route=route)
    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action")
        payload = form.confirmation_data(route, bus)
        if action == "review":
            rows = [(item, form.cleaned_data[f"stop_{item.stop_id}"]) for item in form.route_stops]
            return render_operations(
                request, "trip_confirm", title="Revisá el viaje antes de crearlo", form=form,
                route=route, bus=bus, rows=rows, display_timezone=settings.TIME_ZONE,
                confirmation=signing.dumps(payload, salt=CONFIRMATION_SALT),
            )
        if action == "create":
            try:
                confirmed = signing.loads(request.POST.get("confirmation", ""), salt=CONFIRMATION_SALT)
                if confirmed != payload:
                    raise signing.BadSignature()
            except signing.BadSignature:
                form.add_error(None, "Los datos cambiaron o falta la confirmación. Revisá el viaje nuevamente.")
            else:
                try:
                    trip = create_panel_trip(actor=request.user, route=route, bus=bus, schedules=form.schedules())
                except ValidationError as error:
                    form.add_error(None, error.messages)
                except IntegrityError:
                    form.add_error(None, "No se pudo guardar el viaje y su auditoría. No se guardó ningún cambio.")
                else:
                    messages.success(request, "El viaje se creó correctamente.")
                    return redirect("panel:trip_detail", pk=trip.pk)
        elif action != "edit":
            form.add_error(None, "Revisá los horarios antes de confirmar la creación del viaje.")
    return render_operations(request, "trip_form", title="Nuevo viaje · Paso 2 de 2", form=form,
                             route=route, bus=bus, selection=False)


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def fare_form(request, trip_pk, pk=None):
    trip = get_object_or_404(Trip.objects.select_related("route", "bus"), pk=trip_pk)
    fare = get_object_or_404(TripFare, pk=pk, trip=trip) if pk is not None else None
    if request.method == "POST":
        form, saved = save_fare(actor=request.user, trip_pk=trip_pk, data=request.POST, pk=pk)
        if saved is not None:
            messages.success(request, "La tarifa se guardó correctamente.")
            return redirect("panel:trip_detail", pk=trip_pk)
    else:
        form = TripFareForm(instance=fare, trip=trip)
    return render_operations(request, "fare_form", title="Editar tarifa" if fare else "Nueva tarifa",
                             form=form, trip=trip, display_timezone=settings.TIME_ZONE)


@require_POST
@operations_access(write=True)
def fare_state(request, trip_pk, pk, active):
    try:
        changed = set_fare_active(actor=request.user, trip_pk=trip_pk, pk=pk, active=active)
    except (ValidationError, IntegrityError):
        messages.error(request, FARE_ERROR)
    else:
        messages.success(request, "Estado de la tarifa actualizado." if changed else "La tarifa ya tenía ese estado.")
    return redirect("panel:trip_detail", pk=trip_pk)
