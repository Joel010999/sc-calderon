from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory
from .forms import BusForm, SeatForm
from .permissions import can_manage_operations, operations_access
from .services import WRITE_ERROR, save_configuration, set_configuration_active


def render_operations(request, template, **context):
    context["can_manage"] = can_manage_operations(request.user)
    return render(request, f"panel/operations/{template}.html", context)


def counted_buses():
    return Bus.objects.annotate(
        active_seats=Count("seats", filter=Q(seats__is_active=True)),
        cama_seats=Count("seats", filter=Q(seats__is_active=True, seats__category=SeatCategory.CAMA)),
        semicama_seats=Count("seats", filter=Q(seats__is_active=True, seats__category=SeatCategory.SEMI_CAMA)),
    ).order_by("code")


@operations_access()
@require_GET
def overview(request):
    seats = Seat.objects.filter(is_active=True).aggregate(
        total=Count("pk"), cama=Count("pk", filter=Q(category=SeatCategory.CAMA)),
        semicama=Count("pk", filter=Q(category=SeatCategory.SEMI_CAMA)),
    )
    metrics = [
        ("Recorridos activos", Route.objects.filter(is_active=True).count()),
        ("Colectivos activos", Bus.objects.filter(is_active=True).count()),
        ("Butacas activas", seats["total"]),
        ("Butacas cama activas", seats["cama"]),
        ("Butacas semicama activas", seats["semicama"]),
    ]
    return render_operations(request, "overview", title="Operaciones", metrics=metrics)


@operations_access()
@require_GET
def routes(request):
    queryset = Route.objects.order_by("code").prefetch_related(Prefetch(
        "route_stops", queryset=RouteStop.objects.select_related("stop").order_by("sequence")
    ))
    return render_operations(request, "routes", title="Recorridos", routes=queryset)


@operations_access()
@require_GET
def buses(request):
    return render_operations(request, "buses", title="Colectivos", buses=counted_buses())


@operations_access()
@require_GET
def bus_detail(request, pk):
    bus = get_object_or_404(counted_buses(), pk=pk)
    seats = list(bus.seats.order_by("number"))
    decks = []
    for value, label in Seat.Deck.choices:
        items = [{"seat": seat, "column": seat.position_x + 1, "row": seat.position_y + 1}
                 for seat in seats if seat.deck == value]
        decks.append({"label": label, "value": value, "items": items})
    return render_operations(request, "bus_detail", title=bus.display_name, bus=bus, decks=decks)


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def bus_form(request, pk=None):
    bus = get_object_or_404(Bus, pk=pk) if pk is not None else None
    if request.method == "POST":
        form, saved = save_configuration(actor=request.user, data=request.POST, pk=pk)
        if saved is not None:
            messages.success(request, "El colectivo se guardó correctamente.")
            return redirect("panel:bus_detail", pk=saved.pk)
    else:
        form = BusForm(instance=bus)
    return render_operations(request, "form", form=form, bus=bus,
                             title="Editar colectivo" if bus else "Nuevo colectivo")


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def seat_form(request, bus_pk, pk=None):
    bus = get_object_or_404(Bus, pk=bus_pk)
    seat = get_object_or_404(Seat, pk=pk, bus=bus) if pk is not None else None
    if request.method == "POST":
        form, saved = save_configuration(actor=request.user, data=request.POST, pk=pk, bus=bus)
        if saved is not None:
            messages.success(request, "La butaca se guardó correctamente.")
            return redirect("panel:bus_detail", pk=bus.pk)
    else:
        form = SeatForm(instance=seat, bus=bus)
    return render_operations(request, "form", form=form, bus=bus,
                             title="Editar butaca" if seat else "Nueva butaca")


@require_POST
@operations_access(write=True)
def bus_state(request, pk, active):
    try:
        _, changed = set_configuration_active(actor=request.user, pk=pk, active=active)
    except (IntegrityError, ValidationError):
        messages.error(request, WRITE_ERROR)
    else:
        messages.success(request, "Estado actualizado." if changed else "El colectivo ya tenía ese estado.")
    return redirect("panel:bus_detail", pk=pk)


@require_POST
@operations_access(write=True)
def seat_state(request, bus_pk, pk, active):
    bus = get_object_or_404(Bus, pk=bus_pk)
    try:
        _, changed = set_configuration_active(actor=request.user, pk=pk, active=active, bus=bus)
    except (IntegrityError, ValidationError):
        messages.error(request, WRITE_ERROR)
    else:
        messages.success(request, "Estado actualizado." if changed else "La butaca ya tenía ese estado.")
    return redirect("panel:bus_detail", pk=bus.pk)
