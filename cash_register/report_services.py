"""Consultas y revisión de conciliación diaria de caja."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from panel.models import AuditEvent
from panel.permissions import can_manage_operations

from .models import CashMovement, CashSession

REPORT_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")
DEFAULT_MAX_RANGE_DAYS = 366


def _date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _bounds(start_date, end_date):
    tz = REPORT_TIMEZONE
    start = timezone.make_aware(datetime.combine(start_date, time.min), tz) if start_date else None
    finish = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz) if end_date else None
    return start, finish


def build_cash_report(params, user):
    start_date, end_date = _date(params.get("desde")), _date(params.get("hasta"))
    error = ""
    max_days = int(getattr(settings, "PANEL_REPORTS_MAX_RANGE_DAYS", DEFAULT_MAX_RANGE_DAYS))
    if start_date and end_date:
        if end_date < start_date:
            error = "La fecha hasta debe ser igual o posterior a la fecha desde."
        elif (end_date - start_date).days + 1 > max_days:
            error = f"El rango no puede superar {max_days} días."
    money = DecimalField(max_digits=12, decimal_places=2)
    zero = Value(Decimal("0.00"), output_field=money)
    qs = CashSession.objects.select_related("opened_by", "closed_by", "reviewed_by").annotate(
        cash_income=Coalesce(Sum("movements__amount", filter=Q(movements__kind=CashMovement.Kind.CASH_SALE)), zero),
        adjustment_income=Coalesce(Sum("movements__amount", filter=Q(movements__kind=CashMovement.Kind.ADJUSTMENT_IN)), zero),
        adjustment_out=Coalesce(Sum("movements__amount", filter=Q(movements__kind=CashMovement.Kind.ADJUSTMENT_OUT)), zero),
    ).annotate(
        calculated_expected=ExpressionWrapper(
            F("opening_amount") + F("cash_income") + F("adjustment_income") - F("adjustment_out"),
            output_field=money,
        ),
        report_difference=ExpressionWrapper(F("closing_amount") - F("expected_amount"), output_field=money),
    )
    if not can_manage_operations(user):
        qs = qs.filter(opened_by=user)
    start, finish = _bounds(start_date, end_date)
    if start and not error:
        qs = qs.filter(opened_at__gte=start)
    if finish and not error:
        qs = qs.filter(opened_at__lt=finish)
    status = (params.get("estado") or "").strip().upper()
    if status in CashSession.Status.values:
        qs = qs.filter(status=status)
    seller = (params.get("vendedor") or "").strip()
    if can_manage_operations(user) and seller.isdigit():
        qs = qs.filter(opened_by_id=int(seller))
    difference = (params.get("diferencia") or "").strip().lower()
    if difference in {"positiva", "negativa", "cero"}:
        qs = qs.filter(
            **({"report_difference__gt": 0} if difference == "positiva" else
               {"report_difference__lt": 0} if difference == "negativa" else
               {"report_difference": 0})
        )
    sellers = CashSession.objects.filter(opened_by__is_active=True).values_list("opened_by_id", flat=True).distinct().order_by("opened_by_id")
    return {"sessions": qs.order_by("-opened_at", "-pk"), "error": error, "start_date": start_date, "end_date": end_date,
            "sellers": sellers, "max_range_days": max_days, "is_admin": can_manage_operations(user),
            "filter_status": status, "filter_difference": difference, "filter_seller": seller}


@transaction.atomic
def review_cash_close(*, reviewer, session_id, note=""):
    if not can_manage_operations(reviewer):
        raise ValidationError("Solo un Administrador puede revisar cierres de caja.")
    session = CashSession.objects.select_for_update().get(pk=session_id)
    if session.status != CashSession.Status.CLOSED:
        raise ValidationError("Solo se puede revisar una caja cerrada.")
    if session.reviewed_at:
        return session
    note = (note or "").strip()
    session.reviewed_by = reviewer
    session.reviewed_at = timezone.now()
    session.review_note = note
    session.save(update_fields=["reviewed_by", "reviewed_at", "review_note"])
    AuditEvent.objects.create(actor=reviewer, action=AuditEvent.Action.UPDATE,
                              entity_type=CashSession._meta.label, entity_id=str(session.pk),
                              description="Revisión de cierre de caja", after={"reviewed": True})
    return session
