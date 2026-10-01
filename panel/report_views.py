"""Reportes de ventas del panel; todos los endpoints son estrictamente GET."""
import csv
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from sales.models import BookingChannel
from payments.models import PaymentMethod
from .permissions import payments_access
from .report_services import REPORT_TIMEZONE, build_sales_report


def _safe_csv(value):
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@require_GET
@payments_access()
def sales_report(request):
    report = build_sales_report(request.GET)
    paginator = Paginator(report["queryset"], 25)
    page = paginator.get_page(request.GET.get("pagina", 1))
    context = {**report, "page": page, "paginator": paginator,
               "channels": BookingChannel.choices,
               "payment_methods": PaymentMethod.choices,
               "max_range_days": getattr(settings, "PANEL_REPORTS_MAX_RANGE_DAYS", 366),
               "query_string": urlencode({k: v for k, v in request.GET.items() if k != "pagina"})}
    return render(request, "panel/reports/sales.html", context)


@require_GET
@payments_access()
def sales_report_csv(request):
    report = build_sales_report(request.GET)
    if report["error"]:
        return HttpResponse(report["error"], status=400, content_type="text/plain; charset=utf-8")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("\ufeff")
    response["Content-Disposition"] = 'attachment; filename="reporte-ventas.csv"'
    writer = csv.writer(response)
    writer.writerow(["Referencia pública", "Fecha de confirmación", "Estado", "Medio", "Canal", "Importe", "Moneda", "Pasajeros", "Tramos", "Vendedor"])
    for payment in report["queryset"].iterator():
        local_dt = timezone.localtime(payment.booking.confirmed_at, REPORT_TIMEZONE)
        writer.writerow([_safe_csv(payment.booking.public_id), local_dt.strftime("%d/%m/%Y %H:%M"), "Confirmada",
                         payment.get_method_display(), payment.booking.get_channel_display(),
                         _safe_csv(payment.amount), _safe_csv(payment.currency), payment.passenger_count,
                         payment.leg_count, payment.booking.seller.username if payment.booking.seller else "Sin vendedor"])
    return response
