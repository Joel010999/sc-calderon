from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

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


@transaction.atomic
def open_cash(*, operator, opening_amount=Decimal("0.00")):
    if opening_amount < 0:
        raise ValidationError("El fondo inicial no puede ser negativo.")
    CashSession.objects.select_for_update().filter(
        opened_by=operator,
        status=CashSession.Status.OPEN,
    ).first()
    try:
        session = CashSession.objects.create(
            opened_by=operator,
            opening_amount=opening_amount,
        )
    except IntegrityError as exc:
        raise ValidationError("Ya existe una caja abierta para este vendedor.") from exc
    CashMovement.objects.create(
        session=session,
        kind=CashMovement.Kind.OPENING,
        amount=opening_amount,
        created_by=operator,
        reference="Fondo inicial",
        idempotency_key=f"opening:{session.pk}",
    )
    _audit(operator, AuditEvent.Action.CREATE, session, "Apertura de caja", {
        "opening_amount": str(opening_amount),
    })
    return session


def current_cash(*, operator):
    return CashSession.objects.filter(
        opened_by=operator,
        status=CashSession.Status.OPEN,
    ).first()


@transaction.atomic
def record_cash_sale(*, payment, operator, idempotency_key=None):
    if payment.method != "CASH" or payment.status != "APPROVED":
        raise ValidationError("Solo un pago en efectivo APPROVED puede ingresar a caja.")
    key = idempotency_key or f"payment:{payment.pk}"
    existing = CashMovement.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    session = CashSession.objects.select_for_update().filter(
        opened_by=operator,
        status=CashSession.Status.OPEN,
    ).first()
    if not session:
        raise ValidationError("El vendedor debe tener una caja abierta para registrar un cobro en efectivo.")
    movement = CashMovement.objects.create(
        session=session,
        kind=CashMovement.Kind.CASH_SALE,
        amount=payment.amount,
        created_by=operator,
        payment=payment,
        reference=f"Pago {payment.public_id}",
        idempotency_key=key,
    )
    _audit(operator, AuditEvent.Action.CREATE, movement, "Ingreso de efectivo por pago aprobado", {
        "amount": str(payment.amount),
        "payment": str(payment.public_id),
    })
    return movement


@transaction.atomic
def record_adjustment(*, operator, session_id, amount, kind, reason):
    if not _is_admin(operator):
        raise ValidationError("Solo un Administrador puede registrar ajustes de caja.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("El motivo del ajuste es obligatorio.")
    if amount <= 0:
        raise ValidationError("El importe del ajuste debe ser mayor que cero.")
    if kind not in (CashMovement.Kind.ADJUSTMENT_IN, CashMovement.Kind.ADJUSTMENT_OUT):
        raise ValidationError("El tipo de ajuste no es válido.")
    session = CashSession.objects.select_for_update().get(pk=session_id)
    if session.status != CashSession.Status.OPEN:
        raise ValidationError("Una caja cerrada no puede recibir movimientos.")
    movement = CashMovement.objects.create(
        session=session,
        kind=kind,
        amount=amount,
        created_by=operator,
        reference=reason,
        reason=reason,
        idempotency_key=f"adjustment:{session.pk}:{timezone.now().isoformat()}:{operator.pk}",
    )
    _audit(operator, AuditEvent.Action.CREATE, movement, "Ajuste excepcional de caja", {
        "kind": kind,
        "amount": str(amount),
        "reason": reason,
    })
    return movement


@transaction.atomic
def close_cash(*, operator, closing_amount, note=""):
    session = CashSession.objects.select_for_update().filter(
        opened_by=operator,
        status=CashSession.Status.OPEN,
    ).first()
    if not session:
        raise ValidationError("No hay una caja abierta propia para cerrar.")
    if closing_amount < 0:
        raise ValidationError("El importe de cierre no puede ser negativo.")
    inflows = session.movements.filter(kind__in=[
        CashMovement.Kind.OPENING,
        CashMovement.Kind.CASH_SALE,
        CashMovement.Kind.ADJUSTMENT_IN,
    ]).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    outflows = session.movements.filter(
        kind=CashMovement.Kind.ADJUSTMENT_OUT,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    expected = inflows - outflows
    session.status = CashSession.Status.CLOSED
    session.closed_by = operator
    session.closed_at = timezone.now()
    session.closing_amount = closing_amount
    session.expected_amount = expected
    session.closing_note = note.strip()
    session.save(update_fields=[
        "status", "closed_by", "closed_at", "closing_amount",
        "expected_amount", "closing_note",
    ])
    _audit(operator, AuditEvent.Action.UPDATE, session, "Cierre de caja", {
        "closing_amount": str(closing_amount),
        "expected_amount": str(expected),
        "difference": str(closing_amount - expected),
    })
    return session
