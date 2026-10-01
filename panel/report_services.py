"""Consultas de reportes de ventas: lectura de pagos aprobados confirmados."""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Sum
from django.utils import timezone

from payments.models import Payment, PaymentMethod, PaymentStatus
from sales.models import BookingChannel, BookingStatus


DEFAULT_MAX_RANGE_DAYS = 366


def _local_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def build_sales_report(params):
    """Devuelve queryset filtrado y agregados; jamás muta datos."""
    start_date = _local_date(params.get("desde"))
    end_date = _local_date(params.get("hasta"))
    max_days = int(getattr(settings, "PANEL_REPORTS_MAX_RANGE_DAYS", DEFAULT_MAX_RANGE_DAYS))
    error = ""
    if start_date and end_date:
        if end_date < start_date:
            error = "La fecha hasta debe ser igual o posterior a la fecha desde."
        elif (end_date - start_date).days + 1 > max_days:
            error = f"El rango no puede superar {max_days} días."
    tz = timezone.get_current_timezone()
    qs = Payment.objects.filter(
        status=PaymentStatus.APPROVED,
        booking__status=BookingStatus.CONFIRMED,
    ).select_related("booking", "booking__seller").order_by("-created_at", "-pk")
    if start_date and end_date and not error:
        start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
        end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
        qs = qs.filter(created_at__gte=start, created_at__lt=end)
    elif start_date and not error:
        start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
        qs = qs.filter(created_at__gte=start)
    elif end_date and not error:
        end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
        qs = qs.filter(created_at__lt=end)
    if params.get("medio") in PaymentMethod.values:
        qs = qs.filter(method=params["medio"])
    if params.get("canal") in BookingChannel.values:
        qs = qs.filter(booking__channel=params["canal"])
    if params.get("vendedor", "").isdigit():
        qs = qs.filter(booking__seller_id=int(params["vendedor"]))
    total = qs.aggregate(amount=Sum("amount"), payments=Count("pk"))
    return {
        "queryset": qs,
        "total_amount": total["amount"] or Decimal("0.00"),
        "payment_count": total["payments"] or 0,
        "error": error,
        "start_date": start_date,
        "end_date": end_date,
        "by_method": qs.values("method").annotate(count=Count("pk"), amount=Sum("amount")).order_by("method"),
        "by_channel": qs.values("booking__channel").annotate(count=Count("pk"), amount=Sum("amount")).order_by("booking__channel"),
        "by_seller": qs.values("booking__seller__username").annotate(count=Count("pk"), amount=Sum("amount")).order_by("booking__seller__username"),
    }
