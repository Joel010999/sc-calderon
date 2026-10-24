"""Consultas de ventas y balance con alcance server-side."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from payments.models import Payment, PaymentMethod, PaymentStatus
from sales.models import AssignmentStatus, BookingChannel, BookingLeg, BookingPassenger, BookingStatus, SeatAssignment


DEFAULT_MAX_RANGE_DAYS = 366
REPORT_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")
COMMISSION_PER_TICKET = Decimal("5000.00")


def _local_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _is_admin(actor):
    return bool(actor and actor.is_authenticated and actor.is_active and (
        actor.is_superuser or actor.groups.filter(name="Administrador").exists()
    ))


def build_sales_report(params, *, actor):
    """Construye primero el alcance del actor y después todos los filtros."""
    start_date = _local_date(params.get("desde"))
    end_date = _local_date(params.get("hasta"))
    trip_start_date = _local_date(params.get("viaje_desde"))
    trip_end_date = _local_date(params.get("viaje_hasta"))
    max_days = int(getattr(settings, "PANEL_REPORTS_MAX_RANGE_DAYS", DEFAULT_MAX_RANGE_DAYS))
    error = ""
    for label, raw_start, raw_end, parsed_start, parsed_end in (
        ("confirmación", params.get("desde"), params.get("hasta"), start_date, end_date),
        ("viaje", params.get("viaje_desde"), params.get("viaje_hasta"), trip_start_date, trip_end_date),
    ):
        if (raw_start and parsed_start is None) or (raw_end and parsed_end is None):
            error = f"La fecha de {label} no es válida."
    for label, first, last in (("confirmación", start_date, end_date), ("viaje", trip_start_date, trip_end_date)):
        if first and last and last < first:
            error = f"La fecha hasta de {label} debe ser igual o posterior a la fecha desde."
        elif first and last and (last - first).days + 1 > max_days:
            error = f"El rango de {label} no puede superar {max_days} días."

    base_qs = Payment.objects.filter(
        status=PaymentStatus.APPROVED,
        booking__status=BookingStatus.CONFIRMED,
    ).select_related("booking", "booking__seller")
    admin_view = _is_admin(actor)
    if not admin_view:
        base_qs = base_qs.filter(booking__seller_id=getattr(actor, "pk", None))

    tz = REPORT_TIMEZONE
    if start_date and not error:
        base_qs = base_qs.filter(booking__confirmed_at__gte=timezone.make_aware(datetime.combine(start_date, time.min), tz))
    if end_date and not error:
        base_qs = base_qs.filter(booking__confirmed_at__lt=timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz))
    if trip_start_date and not error:
        base_qs = base_qs.filter(booking__legs__trip__departure_at__gte=timezone.make_aware(datetime.combine(trip_start_date, time.min), tz))
    if trip_end_date and not error:
        base_qs = base_qs.filter(booking__legs__trip__departure_at__lt=timezone.make_aware(datetime.combine(trip_end_date + timedelta(days=1), time.min), tz))
    if params.get("medio") in PaymentMethod.values:
        base_qs = base_qs.filter(method=params["medio"])
    if params.get("canal") in BookingChannel.values:
        base_qs = base_qs.filter(booking__channel=params["canal"])

    # Solo un administrador puede seleccionar otro vendedor o «sin vendedor».
    seller_value = (params.get("vendedor") or "").strip()
    if admin_view:
        if seller_value == "sin-vendedor":
            base_qs = base_qs.filter(booking__seller__isnull=True)
        elif seller_value.isdigit():
            base_qs = base_qs.filter(booking__seller_id=int(seller_value))

    # El filtro por viaje puede unir dos tramos; distinct evita duplicar pagos.
    base_qs = base_qs.distinct()
    qs = base_qs.annotate(
        passenger_count=Count("booking__passengers", distinct=True),
        leg_count=Count("booking__legs", distinct=True),
        commissionable_count=Count(
            "booking__legs__seat_assignments",
            filter=Q(
                booking__legs__seat_assignments__status=AssignmentStatus.CONFIRMED,
                booking__legs__seat_assignments__price__gt=Decimal("0.00"),
            ),
            distinct=True,
        ),
    ).order_by("-booking__confirmed_at", "-pk")
    total = base_qs.aggregate(amount=Sum("amount"), payments=Count("pk", distinct=True), sales=Count("booking_id", distinct=True))
    booking_ids = base_qs.values("booking_id")
    passenger_total = BookingPassenger.objects.filter(booking_id__in=booking_ids).count()
    leg_total = BookingLeg.objects.filter(booking_id__in=booking_ids).count()

    commissionable = SeatAssignment.objects.filter(
        leg__booking_id__in=booking_ids,
        leg__booking__status=BookingStatus.CONFIRMED,
        status=AssignmentStatus.CONFIRMED,
        price__gt=Decimal("0.00"),
    )
    commissionable_units = commissionable.count()
    commission_total = COMMISSION_PER_TICKET * commissionable_units
    gross = total["amount"] or Decimal("0.00")
    by_departure = commissionable.values("leg__trip__departure_at", "leg__trip__route__code").annotate(
        units=Count("pk"), amount=Sum("price")
    ).order_by("leg__trip__departure_at")
    by_seller = base_qs.values("booking__seller__username").annotate(
        count=Count("pk", distinct=True), amount=Sum("amount")
    ).order_by("booking__seller__username") if admin_view else []
    sellers = get_user_model().objects.filter(
        is_active=True, groups__name="Vendedor"
    ).distinct().order_by("username") if admin_view else []
    return {
        "queryset": qs,
        "total_amount": gross,
        "payment_count": total["payments"] or 0,
        "confirmed_sales_count": total["sales"] or 0,
        "passenger_count": passenger_total,
        "leg_count": leg_total,
        "cash_amount": base_qs.filter(method=PaymentMethod.CASH).aggregate(v=Sum("amount"))["v"] or Decimal("0.00"),
        "transfer_amount": base_qs.filter(method=PaymentMethod.BANK_TRANSFER).aggregate(v=Sum("amount"))["v"] or Decimal("0.00"),
        "commissionable_units": commissionable_units,
        "commission_total": commission_total,
        "net_operating": gross - commission_total,
        "by_departure": by_departure,
        "error": error,
        "start_date": start_date,
        "end_date": end_date,
        "trip_start_date": trip_start_date,
        "trip_end_date": trip_end_date,
        "by_method": base_qs.values("method").annotate(count=Count("pk", distinct=True), amount=Sum("amount")).order_by("method"),
        "by_channel": base_qs.values("booking__channel").annotate(count=Count("pk", distinct=True), amount=Sum("amount")).order_by("booking__channel"),
        "by_seller": by_seller,
        "sellers": sellers,
        "is_admin": admin_view,
    }
