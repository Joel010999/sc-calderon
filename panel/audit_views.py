import csv
from django.core.paginator import Paginator
from django.http import HttpResponse, Http404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from .audit_services import build_audit_explorer, resolve_audit_token
from .permissions import audit_explorer_access


def _csv_safe(value):
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@require_GET
@audit_explorer_access()
def audit_list(request):
    data = build_audit_explorer(request.GET)
    page = Paginator(data["records"], 25).get_page(request.GET.get("pagina", 1))
    query_params = request.GET.copy()
    query_params.pop("pagina", None)
    return render(request, "panel/audit/list.html", {**data, "page": page,
        "module_counter_items": data["module_counts"].items(),
        "result_counter_items": data["result_counts"].items(),
        "query_string": query_params.urlencode()})


@require_GET
@audit_explorer_access()
def audit_detail(request, token):
    try:
        record = resolve_audit_token(token)
    except Exception:
        record = None
    if not record:
        raise Http404
    return render(request, "panel/audit/detail.html", {"record": record})


@require_GET
@audit_explorer_access()
def audit_csv(request):
    data = build_audit_explorer(request.GET)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("\ufeff")
    response["Content-Disposition"] = 'attachment; filename="auditoria-central.csv"'
    writer = csv.writer(response)
    writer.writerow(["Fecha", "Actor", "Módulo", "Acción", "Resultado", "Referencia pública"])
    for row in data["records"]:
        writer.writerow([row.created_at.isoformat(), _csv_safe(row.actor), _csv_safe(row.module),
                         _csv_safe(row.action_label), _csv_safe(row.result), _csv_safe(row.reference)])
    return response
