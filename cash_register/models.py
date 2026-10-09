from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class CashSession(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Abierta"
        CLOSED = "CLOSED", "Cerrada"
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_sessions_opened")
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="cash_sessions_closed")
    closed_at = models.DateTimeField(null=True, blank=True)
    closing_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.00"))])
    expected_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.00"))])
    closing_note = models.CharField(max_length=255, blank=True, default="")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="cash_sessions_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True, default="")
    class Meta:
        ordering = ["-opened_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["OPEN", "CLOSED"]), name="cash_session_status_valid"),
            models.CheckConstraint(condition=Q(status="OPEN") | (Q(closed_by__isnull=False) & Q(closed_at__isnull=False) & Q(closing_amount__isnull=False)), name="cash_closed_fields_required"),
            models.UniqueConstraint(fields=["opened_by"], condition=Q(status="OPEN"), name="cash_one_open_session_per_operator"),
        ]
    def clean(self):
        super().clean()
        if self.status == self.Status.CLOSED and (self.closed_by_id is None or self.closed_at is None or self.closing_amount is None):
            raise ValidationError("Una caja cerrada debe registrar operador, fecha e importe final.")

    @property
    def difference(self):
        if self.closing_amount is None or self.expected_amount is None:
            return None
        return self.closing_amount - self.expected_amount


class CashMovement(models.Model):
    class Kind(models.TextChoices):
        OPENING = "OPENING", "Apertura"
        CASH_SALE = "CASH_SALE", "Cobro en efectivo"
        ADJUSTMENT_IN = "ADJUSTMENT_IN", "Ingreso manual"
        ADJUSTMENT_OUT = "ADJUSTMENT_OUT", "Egreso manual"
    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=14, choices=Kind.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_movements")
    created_at = models.DateTimeField(auto_now_add=True)
    reference = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    idempotency_key = models.CharField(max_length=100, unique=True)
    payment = models.OneToOneField("payments.Payment", on_delete=models.PROTECT, null=True, blank=True, related_name="cash_movement")
    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=["OPENING", "CASH_SALE", "ADJUSTMENT_IN", "ADJUSTMENT_OUT"]), name="cash_movement_kind_valid"),
            models.CheckConstraint(condition=Q(kind="OPENING") | Q(amount__gt=Decimal("0.00")), name="cash_movement_amount_valid"),
        ]
    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Los movimientos de caja son inmutables.")
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValidationError("Los movimientos de caja no se pueden eliminar.")
