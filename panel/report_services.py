"""Consultas de reportes de ventas: lectura de pagos aprobados confirmados."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.utils import timezone

from payments.models import Payment, PaymentMethod, PaymentStatus
from sales.models import BookingChannel, BookingLeg, BookingPassenger, BookingStatus


DEFAULT_MAX_RANGE_DAYS = 366
REPORT_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")


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
    # El reporte tiene una zona contractual independiente de la configuracion
    # del request: los limites y la fecha de confirmacion son hora argentina.
    tz = REPORT_TIMEZONE
    base_qs = Payment.objects.filter(
        status=PaymentStatus.APPROVED,
        booking__status=BookingStatus.CONFIRMED,
    ).select_related("booking", "booking__seller")
    qs = base_qs.annotate(
        passenger_count=Count("booking__passengers", distinct=True),
        leg_count=Count("booking__legs", distinct=True),
    ).order_by("-booking__confirmed_at", "-pk")
    if start_date and end_date and not error:
        start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
        end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
        base_qs = base_qs.filter(booking__confirmed_at__gte=start, booking__confirmed_at__lt=end)
    elif start_date and not error:
        start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
        base_qs = base_qs.filter(booking__confirmed_at__gte=start)
    elif end_date and not error:
        end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
        base_qs = base_qs.filter(booking__confirmed_at__lt=end)
    if params.get("medio") in PaymentMethod.values:
        base_qs = base_qs.filter(method=params["medio"])
    if params.get("canal") in BookingChannel.values:
        base_qs = base_qs.filter(booking__channel=params["canal"])
    seller_value = params.get("vendedor", "")
    seller_ids = set(
        get_user_model().objects.filter(
            is_active=True, groups__name__in=["Administrador", "Vendedor"]
        ).values_list("pk", flat=True)
    )
    if seller_value == "sin-vendedor":
        base_qs = base_qs.filter(booking__seller__isnull=True)
    elif seller_value.isdigit() and int(seller_value) in seller_ids:
        base_qs = base_qs.filter(booking__seller_id=int(seller_value))
    qs = base_qs.annotate(
        passenger_count=Count("booking__passengers", distinct=True),
        leg_count=Count("booking__legs", distinct=True),
    ).order_by("-booking__confirmed_at", "-pk")
    total = base_qs.aggregate(amount=Sum("amount"), payments=Count("pk", distinct=True), sales=Count("booking_id", distinct=True))
    booking_ids = base_qs.values("booking_id")
    passenger_total = BookingPassenger.objects.filter(booking_id__in=booking_ids).count()
    leg_total = BookingLeg.objects.filter(booking_id__in=booking_ids).count()
    total["passengers"] = passenger_total
    total["legs"] = leg_total
    by_method = base_qs.values("method").annotate(count=Count("pk", distinct=True), amount=Sum("amount")).order_by("method")
    by_channel = base_qs.values("booking__channel").annotate(count=Count("pk", distinct=True), amount=Sum("amount")).order_by("booking__channel")
    by_seller = base_qs.values("booking__seller__username").annotate(count=Count("pk", distinct=True), amount=Sum("amount")).order_by("booking__seller__username")
    sellers = get_user_model().objects.filter(
        is_active=True, groups__name__in=["Administrador", "Vendedor"]
    ).distinct().order_by("username")
    return {
        "queryset": qs,
        "total_amount": total["amount"] or Decimal("0.00"),
        "payment_count": total["payments"] or 0,
        "confirmed_sales_count": total["sales"] or 0,
        "passenger_count": total["passengers"] or 0,
        "leg_count": total["legs"] or 0,
        "cash_amount": base_qs.filter(method=PaymentMethod.CASH).aggregate(v=Sum("amount"))["v"] or Decimal("0.00"),
        "transfer_amount": base_qs.filter(method=PaymentMethod.BANK_TRANSFER).aggregate(v=Sum("amount"))["v"] or Decimal("0.00"),
        "error": error,
        "start_date": start_date,
        "end_date": end_date,
        "by_method": by_method,
        "by_channel": by_channel,
        "by_seller": by_seller,
        "sellers": sellers,
    }
