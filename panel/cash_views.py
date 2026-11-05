from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from cash_register.models import CashSession

from .permissions import can_manage_operations, payments_access


@payments_access()
@require_GET
def cash_detail(request):
    is_admin = can_manage_operations(request.user)
    if is_admin:
        sessions = CashSession.objects.select_related(
            "opened_by", "closed_by",
        ).all()
        status = request.GET.get("status", "").strip()
        seller = request.GET.get("seller", "").strip()
        if status in {CashSession.Status.OPEN, CashSession.Status.CLOSED}:
            sessions = sessions.filter(status=status)
        if seller:
            sessions = sessions.filter(opened_by__username__icontains=seller)
        sessions = sessions[:100]
    else:
        sessions = CashSession.objects.select_related(
            "opened_by", "closed_by",
        ).filter(opened_by=request.user)[:100]
    return render(request, "panel/cash/detail.html", {
        "session": None,
        "sessions": sessions,
        "recent_movements": [],
        "is_admin": is_admin,
        "filter_status": request.GET.get("status", ""),
        "filter_seller": request.GET.get("seller", ""),
    })


@payments_access()
@require_GET
def cash_session_detail(request, session_pk):
    session = get_object_or_404(
        CashSession.objects.select_related("opened_by", "closed_by"),
        pk=session_pk,
    )
    if session.opened_by_id != request.user.pk and not can_manage_operations(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("No tenés permiso para consultar esta caja.")
    movements = session.movements.select_related("created_by", "payment").order_by("-created_at", "-pk")[:200]
    return render(request, "panel/cash/session_detail.html", {
        "session": session,
        "movements": movements,
        "is_admin": can_manage_operations(request.user),
    })


@payments_access()
@require_POST
def cash_open(request):
    raise ValidationError("La apertura de caja se encuentra deshabilitada.")


@payments_access()
@require_POST
def cash_close(request):
    raise ValidationError("El cierre de caja se encuentra deshabilitado.")


@payments_access()
@require_POST
def cash_adjustment(request):
    raise ValidationError("El registro de ajustes de caja se encuentra deshabilitado.")
