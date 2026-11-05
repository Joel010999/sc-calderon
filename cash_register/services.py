from decimal import Decimal

from django.core.exceptions import ValidationError

from panel.models import AuditEvent

from .models import CashMovement, CashSession


def _audit(actor, action, obj, description, after=None):
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity_type=obj._meta.label,
        entity_id=str(obj.pk),
        description=description,
        after=after or {},
    )


def _is_admin(user):
    return bool(user.is_superuser or user.groups.filter(name="Administrador").exists())


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
