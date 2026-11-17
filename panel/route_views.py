from django.contrib import messages
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from operations.models import Route, RouteStop, Stop
from .operations_views import render_operations
from .permissions import operations_access
from .route_forms import RouteConfigurationForm, StopForm
from .route_services import save_panel_route, save_panel_stop, set_route_active


@operations_access()
@require_GET
def route_list(request):
    queryset = Route.objects.annotate(
        trip_count=Count("trips")
    ).order_by("code", "version").prefetch_related(Prefetch(
        "route_stops", queryset=RouteStop.objects.select_related("stop").order_by("sequence")
    ))
    stops = Stop.objects.order_by("name")
    return render_operations(
        request, "routes", title="Recorridos",
        routes=queryset, all_stops=stops,
    )


@operations_access()
@require_GET
def route_detail(request, pk):
    route = get_object_or_404(
        Route.objects.annotate(trip_count=Count("trips")).prefetch_related(
            Prefetch("route_stops", queryset=RouteStop.objects.select_related("stop").order_by("sequence"))
        ),
        pk=pk,
    )
    return render_operations(
        request, "route_detail", title=f"Recorrido {route.code}",
        route=route,
    )


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def route_create(request):
    stops = Stop.objects.filter(is_active=True).order_by("name")
    if request.method == "POST":
        form, saved, _ = save_panel_route(actor=request.user, data=request.POST)
        if saved is not None:
            messages.success(request, f"El recorrido {saved.code} se creó correctamente.")
            return redirect("panel:routes")
    else:
        form = RouteConfigurationForm()
    return render_operations(
        request, "route_form", title="Nuevo recorrido",
        form=form, available_stops=stops, is_new=True,
    )


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def route_edit(request, pk):
    route = get_object_or_404(
        Route.objects.annotate(trip_count=Count("trips")).prefetch_related(
            Prefetch("route_stops", queryset=RouteStop.objects.select_related("stop").order_by("sequence"))
        ),
        pk=pk,
    )
    stops = Stop.objects.filter(is_active=True).order_by("name")
    is_versioning = route.trip_count > 0

    if request.method == "POST":
        form, saved, created_version = save_panel_route(actor=request.user, data=request.POST, pk=pk)
        if saved is not None:
            if created_version:
                messages.success(
                    request,
                    f"Se creó la nueva versión {saved.version} del recorrido {saved.code} para futuras salidas. "
                    "Las salidas y reservas anteriores se conservan intactas.",
                )
            else:
                messages.success(request, f"El recorrido {saved.code} se actualizó correctamente.")
            return redirect("panel:routes")
    else:
        form = RouteConfigurationForm(instance=route)

    existing_stops = [
        {
            "stop_id": rs.stop_id,
            "stop_name": rs.stop.name,
            "stop_code": rs.stop.code,
            "province": rs.stop.province,
            "sequence": rs.sequence,
            "allows_boarding": rs.allows_boarding,
            "allows_alighting": rs.allows_alighting,
        }
        for rs in route.route_stops.all()
    ]

    return render_operations(
        request, "route_form",
        title=f"Crear nueva versión de {route.code}" if is_versioning else f"Editar recorrido {route.code}",
        form=form, route=route, available_stops=stops,
        existing_stops=existing_stops, is_versioning=is_versioning, is_new=False,
    )


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def route_new_version(request, pk):
    return route_edit(request, pk)


@require_POST
@operations_access(write=True)
def route_state(request, pk, active):
    route, changed = set_route_active(actor=request.user, pk=pk, active=active)
    state = "activado" if active else "desactivado"
    if changed:
        messages.success(request, f"El recorrido {route.code} fue {state}.")
    else:
        messages.info(request, f"El recorrido {route.code} ya estaba {state}.")
    return redirect("panel:routes")


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def stop_create(request):
    if request.method == "POST":
        form, saved = save_panel_stop(actor=request.user, data=request.POST)
        if saved is not None:
            messages.success(request, f"La parada {saved.code} se creó correctamente.")
            return redirect("panel:routes")
    else:
        form = StopForm()
    return render_operations(
        request, "stop_form", title="Nueva parada",
        form=form, is_new=True,
    )


@operations_access(write=True)
@require_http_methods(["GET", "POST"])
def stop_edit(request, pk):
    stop = get_object_or_404(Stop, pk=pk)
    if request.method == "POST":
        form, saved = save_panel_stop(actor=request.user, data=request.POST, pk=pk)
        if saved is not None:
            messages.success(request, f"La parada {saved.code} se actualizó correctamente.")
            return redirect("panel:routes")
    else:
        form = StopForm(instance=stop)
    return render_operations(
        request, "stop_form", title=f"Editar parada {stop.code}",
        form=form, stop=stop, is_new=False,
    )
