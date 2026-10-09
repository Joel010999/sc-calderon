import csv
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.views.decorators.http import require_GET, require_POST

from cash_register.models import CashSession
from cash_register.report_services import build_cash_report, review_cash_close, REPORT_TIMEZONE
from .permissions import payments_access, can_manage_operations
from .report_views import _safe_csv


def _context(request, report):
    page = Paginator(report["sessions"], 25).get_page(request.GET.get("pagina", 1))
    return {**report, "page": page,
            "query_string": urlencode({k: v for k, v in request.GET.items() if k != "pagina"})}


@require_GET
@payments_access()
def cash_report(request):
    return render(request, "panel/reports/cash.html", _context(request, build_cash_report(request.GET, request.user)))


@require_GET
@payments_access()
def cash_report_csv(request):
    report = build_cash_report(request.GET, request.user)
    if report["error"]:
        return HttpResponse(report["error"], status=400, content_type="text/plain; charset=utf-8")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("\ufeff")
    response["Content-Disposition"] = 'attachment; filename="reporte-caja.csv"'
    writer = csv.writer(response)
    writer.writerow(["Caja", "Vendedor", "Apertura", "Cierre", "Estado", "Saldo inicial", "Ingresos efectivo", "Ajustes ingreso", "Ajustes egreso", "Total esperado", "Importe contado", "Diferencia", "Revisada"])
    for session in report["sessions"]:
        opened_local = session.opened_at.astimezone(REPORT_TIMEZONE)
        closed_local = session.closed_at.astimezone(REPORT_TIMEZONE) if session.closed_at else None
        writer.writerow([_safe_csv(session.pk), _safe_csv(f"Vendedor #{session.opened_by_id}"), opened_local.strftime("%d/%m/%Y %H:%M"),
                         closed_local.strftime("%d/%m/%Y %H:%M") if closed_local else "", session.get_status_display(),
                         _safe_csv(session.opening_amount), _safe_csv(session.cash_income), _safe_csv(session.adjustment_income),
                         _safe_csv(session.adjustment_out), _safe_csv(session.calculated_expected), _safe_csv(session.closing_amount),
                         _safe_csv(session.difference), "Sí" if session.reviewed_at else "No"])
    return response


@require_GET
@payments_access()
def cash_report_detail(request, session_pk):
    session = get_object_or_404(CashSession.objects.select_related("opened_by", "closed_by", "reviewed_by"), pk=session_pk)
    if session.opened_by_id != request.user.pk and not can_manage_operations(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("No tenés permiso para consultar esta caja.")
    page = Paginator(session.movements.select_related("created_by", "payment").order_by("-created_at", "-pk"), 25).get_page(request.GET.get("pagina", 1))
    return render(request, "panel/reports/cash_detail.html", {"session": session, "page": page, "is_admin": can_manage_operations(request.user)})


@require_POST
@payments_access()
def cash_report_review(request, session_pk):
    if not can_manage_operations(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Solo un Administrador puede revisar cierres.")
    try:
        review_cash_close(reviewer=request.user, session_id=session_pk, note=request.POST.get("note", ""))
        messages.success(request, "El cierre fue revisado correctamente.")
    except CashSession.DoesNotExist:
        raise Http404("La caja solicitada no existe.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if getattr(exc, "messages", None) else str(exc))
    return redirect("panel:cash_report_detail", session_pk=session_pk)
