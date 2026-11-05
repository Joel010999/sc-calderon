from decimal import Decimal

from django.core.exceptions import ValidationError


def open_cash(*, operator, opening_amount=Decimal("0.00")):
    raise ValidationError("La apertura de caja se encuentra deshabilitada.")


def current_cash(*, operator):
    return None


def record_cash_sale(*, payment, operator, idempotency_key=None):
    raise ValidationError("El registro de ventas en efectivo en caja se encuentra deshabilitado.")


def record_adjustment(*, operator, session_id, amount, kind, reason):
    raise ValidationError("El registro de ajustes de caja se encuentra deshabilitado.")


def close_cash(*, operator, closing_amount, note=""):
    raise ValidationError("El cierre de caja se encuentra deshabilitado.")
