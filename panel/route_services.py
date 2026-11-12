"""Servicios del panel para la gestión y versionado de recorridos y paradas."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404

from operations.models import Route, Stop
from operations.services import save_route_configuration
from .models import AuditEvent
from .permissions import require_operations_manager
from .route_forms import RouteConfigurationForm, StopForm
from .services import record_event, snapshot

ROUTE_WRITE_ERROR = "No se pudo guardar el recorrido. Revisá que el código no esté duplicado y que las paradas sean válidas."
STOP_WRITE_ERROR = "No se pudo guardar la parada. El código podría estar en uso."


@transaction.atomic
def save_panel_route(*, actor, data, pk=None, force_new_version=False):
    """Guarda o versiona un recorrido desde el panel registrando auditoría.

    Si el recorrido ya tiene viajes asociados, crea automáticamente una nueva versión
    para futuras salidas, preservando el recorrido y sus viajes históricos.
    """
    require_operations_manager(actor)
    route = get_object_or_404(Route.objects.select_for_update(), pk=pk) if pk is not None else None
    before = snapshot(route) if route is not None else {}

    form = RouteConfigurationForm(data, instance=route)
    if not form.is_valid():
        return form, None, False

    try:
        saved_route, created_version = save_route_configuration(
            code=form.cleaned_data["code"],
            name=form.cleaned_data["name"],
            stops_data=form.cleaned_stops,
            route=route,
            is_active=form.cleaned_data.get("is_active", True),
            force_new_version=force_new_version,
        )
        action = AuditEvent.Action.CREATE if (pk is None or created_version) else AuditEvent.Action.UPDATE
        record_event(actor, saved_route, action, before)
        return form, saved_route, created_version
    except (ValidationError, IntegrityError) as exc:
        if isinstance(exc, ValidationError):
            form.add_error(None, exc)
        else:
            form.add_error(None, ROUTE_WRITE_ERROR)
        return form, None, False


@transaction.atomic
def save_panel_stop(*, actor, data, pk=None):
    """Crea o edita una parada desde el panel."""
    require_operations_manager(actor)
    stop = get_object_or_404(Stop.objects.select_for_update(), pk=pk) if pk is not None else None
    before = snapshot(stop) if stop is not None else {}

    form = StopForm(data, instance=stop)
    if not form.is_valid():
        return form, None

    try:
        saved_stop = form.save()
        if before != snapshot(saved_stop):
            action = AuditEvent.Action.CREATE if pk is None else AuditEvent.Action.UPDATE
            record_event(actor, saved_stop, action, before)
        return form, saved_stop
    except (ValidationError, IntegrityError) as exc:
        if isinstance(exc, ValidationError):
            form.add_error(None, exc)
        else:
            form.add_error(None, STOP_WRITE_ERROR)
        return form, None


@transaction.atomic
def set_route_active(*, actor, pk, active):
    """Activa o desactiva un recorrido."""
    require_operations_manager(actor)
    route = get_object_or_404(Route.objects.select_for_update(), pk=pk)
    if route.is_active == active:
        return route, False
    before = snapshot(route)
    route.is_active = active
    route.full_clean()
    route.save(update_fields=["is_active", "updated_at"])
    action = AuditEvent.Action.ACTIVATE if active else AuditEvent.Action.DEACTIVATE
    record_event(actor, route, action, before)
    return route, True
