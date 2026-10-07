from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from cash_register.models import CashMovement, CashSession
from cash_register.services import (
    close_cash,
    current_cash,
    open_cash,
    record_adjustment,
)

from .permissions import can_manage_operations, payments_access


@payments_access()
@require_GET
def cash_detail(request):
    is_admin = can_manage_operations(request.user)
    session = current_cash(operator=request.user)
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
    recent = session.movements.order_by("-created_at", "-pk")[:50] if session else []
    return render(request, "panel/cash/detail.html", {
        "session": session,
        "sessions": sessions,
        "recent_movements": recent,
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
    try:
        amount = Decimal(request.POST.get("opening_amount", "0").strip())
        open_cash(operator=request.user, opening_amount=amount)
        messages.success(request, "La caja quedó abierta correctamente.")
    except (InvalidOperation, ValueError):
        messages.error(request, "El fondo inicial debe ser un importe válido.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
    return redirect("panel:cash_detail")


@payments_access()
@require_POST
def cash_close(request):
    try:
        amount = Decimal(request.POST.get("closing_amount", "").strip())
        close_cash(operator=request.user, closing_amount=amount, note=request.POST.get("note", ""))
        messages.success(request, "La caja quedó cerrada correctamente.")
    except (InvalidOperation, ValueError):
        messages.error(request, "El importe de cierre debe ser válido.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
    return redirect("panel:cash_detail")


@payments_access()
@require_POST
def cash_adjustment(request):
    if not can_manage_operations(request.user):
        messages.error(request, "Solo un Administrador puede registrar ajustes.")
        return redirect("panel:cash_detail")
    try:
        amount = Decimal(request.POST.get("amount", "").strip())
        session_id = int(request.POST.get("session_id", ""))
        record_adjustment(
            operator=request.user,
            session_id=session_id,
            amount=amount,
            kind=request.POST.get("kind", ""),
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, "El ajuste fue registrado.")
    except (InvalidOperation, ValueError):
        messages.error(request, "El importe o la caja seleccionada no son válidos.")
    except (ValidationError, CashSession.DoesNotExist):
        messages.error(request, "No se pudo registrar el ajuste.")
    return redirect("panel:cash_detail")
