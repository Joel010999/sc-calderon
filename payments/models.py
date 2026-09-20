import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from sales.validators import validate_aware_datetime, validate_currency
from .storage import get_voucher_storage, voucher_upload_path


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Efectivo"
    BANK_TRANSFER = "BANK_TRANSFER", "Transferencia bancaria"


class PaymentStatus(models.TextChoices):
    AWAITING_VOUCHER = "AWAITING_VOUCHER", "Esperando comprobante"
    UNDER_REVIEW = "UNDER_REVIEW", "En revisión"
    APPROVED = "APPROVED", "Aprobado"
    REJECTED = "REJECTED", "Rechazado"
    EXPIRED = "EXPIRED", "Expirado"


class Payment(models.Model):
    public_id = models.UUIDField(
        "identificador público",
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )
    booking = models.ForeignKey(
        "sales.Booking",
        verbose_name="reserva",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    method = models.CharField(
        "método de pago",
        max_length=20,
        choices=PaymentMethod.choices,
    )
    status = models.CharField(
        "estado del pago",
        max_length=20,
        choices=PaymentStatus.choices,
    )
    amount = models.DecimalField(
        "importe",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField(
        "moneda",
        max_length=3,
        default="ARS",
        validators=[validate_currency],
    )
    voucher = models.FileField(
        "comprobante",
        upload_to=voucher_upload_path,
        storage=get_voucher_storage,
        blank=True,
        null=True,
    )
    reference = models.CharField(
        "referencia o notas",
        max_length=255,
        blank=True,
        default="",
    )
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="registrado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="registered_payments",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="revisado por",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_payments",
    )
    reviewed_at = models.DateTimeField(
        "fecha de revisión",
        null=True,
        blank=True,
        validators=[validate_aware_datetime],
    )
    proof_deadline_at = models.DateTimeField(
        "límite para subir comprobante",
        null=True,
        blank=True,
        validators=[validate_aware_datetime],
    )
    review_deadline_at = models.DateTimeField(
        "límite de revisión",
        null=True,
        blank=True,
        validators=[validate_aware_datetime],
    )
    rejection_reason = models.TextField(
        "motivo de rechazo",
        blank=True,
        default="",
    )
    created_at = models.DateTimeField("fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "pago"
        verbose_name_plural = "pagos"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["booking"],
                condition=Q(
                    status__in=[
                        PaymentStatus.AWAITING_VOUCHER,
                        PaymentStatus.UNDER_REVIEW,
                        PaymentStatus.APPROVED,
                    ]
                ),
                name="payments_active_booking_unique",
                violation_error_message="Ya existe un pago en revisión o aprobado para esta reserva.",
            ),
            models.CheckConstraint(
                condition=Q(method__in=PaymentMethod.values),
                name="payments_method_valid",
                violation_error_message="El método de pago no es válido.",
            ),
            models.CheckConstraint(
                condition=Q(status__in=PaymentStatus.values),
                name="payments_status_valid",
                violation_error_message="El estado del pago no es válido.",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="payments_amount_positive",
                violation_error_message="El importe del pago debe ser mayor que cero.",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "method"], name="payments_status_method_idx"),
            models.Index(fields=["created_at"], name="payments_created_idx"),
        ]

    def __str__(self):
        return (
            f"Pago {self.public_id} · {self.get_method_display()} · "
            f"{self.currency} {self.amount} ({self.get_status_display()})"
        )

    def clean_fields(self, exclude=None):
        if "amount" not in (exclude or ()) and isinstance(self.amount, float):
            raise ValidationError({"amount": "Usá Decimal para el importe, nunca float."})
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        errors = {}

        if self.status == PaymentStatus.REJECTED and not self.rejection_reason.strip():
            errors["rejection_reason"] = "Los pagos rechazados deben indicar un motivo de rechazo."

        if self.status == PaymentStatus.APPROVED:
            if not self.reviewed_at:
                errors["reviewed_at"] = "Los pagos aprobados deben registrar fecha de revisión."
            if not self.reviewed_by_id:
                errors["reviewed_by"] = "Los pagos aprobados deben registrar el usuario revisor."

        if self.status == PaymentStatus.AWAITING_VOUCHER and not self.proof_deadline_at:
            errors["proof_deadline_at"] = "Los pagos en espera de comprobante deben registrar el límite de carga."

        if self.method == PaymentMethod.BANK_TRANSFER and self.status == PaymentStatus.UNDER_REVIEW:
            if not self.voucher:
                errors["voucher"] = "Las transferencias en revisión requieren comprobante."

        if errors:
            raise ValidationError(errors)
