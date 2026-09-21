import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from sales.validators import validate_aware_datetime, validate_currency
from .storage import get_ticket_storage, ticket_upload_path


def mask_document(doc_type, doc_number):
    """Enmascara el documento para resguardar la privacidad del pasajero.

    Ejemplo: DNI 40123456 -> DNI ***3456
    """
    dt = (doc_type or "DNI").strip()
    num = str(doc_number or "").strip()
    if not num:
        return f"{dt} ***"
    if len(num) <= 4:
        return f"{dt} ***{num[-2:]}"
    return f"{dt} ***{num[-4:]}"


class TicketStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente"
    ISSUED = "ISSUED", "Emitido"
    VOID = "VOID", "Anulado"


class EmailAttemptStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente"
    SENT = "SENT", "Enviado"
    FAILED = "FAILED", "Fallido"


class Ticket(models.Model):
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
        related_name="tickets",
    )
    leg = models.ForeignKey(
        "sales.BookingLeg",
        verbose_name="tramo",
        on_delete=models.PROTECT,
        related_name="tickets",
    )
    passenger = models.ForeignKey(
        "sales.BookingPassenger",
        verbose_name="pasajero",
        on_delete=models.PROTECT,
        related_name="tickets",
    )
    seat_assignment = models.ForeignKey(
        "sales.SeatAssignment",
        verbose_name="asignación de butaca",
        on_delete=models.PROTECT,
        related_name="tickets",
    )
    ticket_code = models.CharField(
        "código legible de pasaje",
        max_length=40,
        unique=True,
        db_index=True,
    )
    verification_token_hash = models.CharField(
        "hash del token de verificación QR",
        max_length=64,
        unique=True,
        db_index=True,
    )
    download_token_hash = models.CharField(
        "hash del token de descarga",
        max_length=64,
        unique=True,
        db_index=True,
    )
    status = models.CharField(
        "estado del pasaje",
        max_length=15,
        choices=TicketStatus.choices,
        default=TicketStatus.PENDING,
    )
    pdf_file = models.FileField(
        "archivo PDF",
        upload_to=ticket_upload_path,
        storage=get_ticket_storage,
        blank=True,
        null=True,
    )
    pdf_path = models.CharField(
        "ruta PDF privada",
        max_length=255,
        blank=True,
        default="",
    )
    issued_at = models.DateTimeField(
        "fecha de emisión",
        null=True,
        blank=True,
        validators=[validate_aware_datetime],
    )
    voided_at = models.DateTimeField(
        "fecha de anulación",
        null=True,
        blank=True,
        validators=[validate_aware_datetime],
    )
    void_reason = models.TextField(
        "motivo de anulación",
        blank=True,
        default="",
    )

    # Snapshots inmutables de emisión histórica
    passenger_name = models.CharField("nombre completo", max_length=255)
    passenger_document_masked = models.CharField("documento enmascarado", max_length=50)
    origin_stop_name = models.CharField("parada de subida", max_length=150)
    destination_stop_name = models.CharField("parada de bajada", max_length=150)
    departure_at = models.DateTimeField("horario de subida", validators=[validate_aware_datetime])
    arrival_at = models.DateTimeField("horario de bajada", validators=[validate_aware_datetime])
    seat_number = models.PositiveIntegerField("número de butaca", validators=[MinValueValidator(1)])
    seat_category = models.CharField("categoría de butaca", max_length=20)
    seat_category_display = models.CharField("descripción de categoría", max_length=50)
    price = models.DecimalField(
        "precio histórico",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField("moneda", max_length=3, default="ARS", validators=[validate_currency])
    booking_public_id = models.UUIDField("código de reserva", db_index=True)

    created_at = models.DateTimeField("fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "pasaje"
        verbose_name_plural = "pasajes"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "leg", "passenger"],
                name="tickets_ticket_booking_leg_pax_unique",
                violation_error_message="Ya existe un pasaje para este pasajero en este tramo de la reserva.",
            ),
            models.UniqueConstraint(
                fields=["leg", "passenger"],
                name="tickets_ticket_leg_pax_unique",
                violation_error_message="El pasajero ya tiene un pasaje emitido en este tramo.",
            ),
            models.UniqueConstraint(
                fields=["seat_assignment"],
                name="tickets_ticket_seat_assignment_unique",
                violation_error_message="Esta asignación de butaca ya posee un pasaje asociado.",
            ),
            models.CheckConstraint(
                condition=Q(status__in=TicketStatus.values),
                name="tickets_ticket_status_valid",
                violation_error_message="El estado del pasaje no es válido.",
            ),
            models.CheckConstraint(
                condition=Q(price__gt=Decimal("0.00")),
                name="tickets_ticket_price_positive",
                violation_error_message="El precio del pasaje debe ser mayor que cero.",
            ),
            models.CheckConstraint(
                condition=Q(seat_number__gte=1),
                name="tickets_ticket_seat_num_positive",
                violation_error_message="El número de butaca debe ser mayor o igual a 1.",
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="tickets_tk_status_idx"),
            models.Index(fields=["created_at"], name="tickets_tk_created_idx"),
        ]

    def __str__(self):
        return f"Pasaje {self.ticket_code} ({self.get_status_display()}) · {self.passenger_name}"

    def clean_fields(self, exclude=None):
        if "price" not in (exclude or ()) and isinstance(self.price, float):
            raise ValidationError({"price": "Usá Decimal para el importe, nunca float."})
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        errors = {}
        if self.status == TicketStatus.VOID and not self.void_reason.strip():
            errors["void_reason"] = "Los pasajes anulados deben registrar un motivo de anulación."
        if self.status == TicketStatus.VOID and not self.voided_at:
            errors["voided_at"] = "Los pasajes anulados deben registrar fecha de anulación."
        if self.status == TicketStatus.ISSUED and not self.issued_at:
            errors["issued_at"] = "Los pasajes emitidos deben registrar fecha de emisión."

        if self.leg_id and self.booking_id and self.leg.booking_id != self.booking_id:
            errors["leg"] = "El tramo debe pertenecer a la reserva del pasaje."
        if self.passenger_id and self.booking_id and self.passenger.booking_id != self.booking_id:
            errors["passenger"] = "El pasajero debe pertenecer a la reserva del pasaje."
        if self.seat_assignment_id and self.leg_id and self.seat_assignment.leg_id != self.leg_id:
            errors["seat_assignment"] = "La asignación de butaca debe pertenecer al tramo del pasaje."
        if self.seat_assignment_id and self.passenger_id and self.seat_assignment.passenger_id != self.passenger_id:
            errors["seat_assignment"] = "La asignación de butaca debe pertenecer al pasajero del pasaje."

        if errors:
            raise ValidationError(errors)


class TicketEmailAttempt(models.Model):
    booking = models.ForeignKey(
        "sales.Booking",
        verbose_name="reserva",
        on_delete=models.PROTECT,
        related_name="ticket_email_attempts",
    )
    status = models.CharField(
        "estado del intento",
        max_length=15,
        choices=EmailAttemptStatus.choices,
        default=EmailAttemptStatus.PENDING,
    )
    recipient_email = models.EmailField("correo destinatario")
    attempted_at = models.DateTimeField("fecha de intento", default=timezone.now)
    completed_at = models.DateTimeField("fecha de finalización", null=True, blank=True)
    error_message = models.CharField("mensaje de error sanitizado", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "intento de envío de pasajes"
        verbose_name_plural = "intentos de envío de pasajes"
        ordering = ["-attempted_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=EmailAttemptStatus.values),
                name="tickets_attempt_status_valid",
                violation_error_message="El estado del intento de envío no es válido.",
            ),
        ]
        indexes = [
            models.Index(fields=["booking", "status"], name="tickets_email_bk_status_idx"),
            models.Index(fields=["attempted_at"], name="tickets_email_attempt_idx"),
        ]

    def __str__(self):
        return f"Envío {self.booking.public_id} · {self.get_status_display()} ({self.recipient_email})"


class TicketAuditEvent(models.Model):
    class Action(models.TextChoices):
        ISSUE = "ISSUE", "Emisión"
        DOWNLOAD = "DOWNLOAD", "Descarga"
        VOID = "VOID", "Anulación"
        EMAIL = "EMAIL", "Envío de correo"
        VERIFY = "VERIFY", "Verificación"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="usuario actor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ticket_audit_events",
    )
    action = models.CharField("acción", max_length=20, choices=Action.choices)
    ticket = models.ForeignKey(
        Ticket,
        verbose_name="pasaje",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    booking = models.ForeignKey(
        "sales.Booking",
        verbose_name="reserva",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ticket_audit_events",
    )
    description = models.CharField("descripción", max_length=255)
    metadata = models.JSONField("metadatos sin PII", default=dict)
    created_at = models.DateTimeField("fecha y hora", auto_now_add=True)

    class Meta:
        verbose_name = "evento de auditoría de pasajes"
        verbose_name_plural = "eventos de auditoría de pasajes"
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["action", "created_at"], name="tickets_audit_action_idx"),
        ]

    def __str__(self):
        return f"[{self.get_action_display()}] {self.description}"


class FulfillmentIssueStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente"
    PROCESSING = "PROCESSING", "Procesando"
    SUCCEEDED = "SUCCEEDED", "Emitido"
    FAILED = "FAILED", "Fallido"


class FulfillmentEmailStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente"
    SENT = "SENT", "Enviado"
    FAILED = "FAILED", "Fallido"


class TicketFulfillment(models.Model):
    """Trabajo durable que desacopla la confirmación económica de la entrega."""

    booking = models.OneToOneField(
        "sales.Booking",
        verbose_name="reserva",
        on_delete=models.PROTECT,
        related_name="ticket_fulfillment",
    )
    issue_status = models.CharField(
        "estado de emisión", max_length=15,
        choices=FulfillmentIssueStatus.choices,
        default=FulfillmentIssueStatus.PENDING,
    )
    email_status = models.CharField(
        "estado de correo", max_length=15,
        choices=FulfillmentEmailStatus.choices,
        default=FulfillmentEmailStatus.PENDING,
    )
    issue_error = models.CharField("error de emisión", max_length=255, blank=True, default="")
    email_error = models.CharField("error de correo", max_length=255, blank=True, default="")
    attempts = models.PositiveIntegerField("intentos", default=0)
    last_attempt_at = models.DateTimeField("último intento", null=True, blank=True)
    next_attempt_at = models.DateTimeField("próximo intento permitido", null=True, blank=True)
    lease_until = models.DateTimeField("bloqueo de procesamiento hasta", null=True, blank=True)
    completed_at = models.DateTimeField("finalizado", null=True, blank=True)
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        verbose_name = "trabajo de entrega de pasajes"
        verbose_name_plural = "trabajos de entrega de pasajes"
        constraints = [
            models.CheckConstraint(
                condition=Q(issue_status__in=FulfillmentIssueStatus.values),
                name="tickets_fulfillment_issue_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(email_status__in=FulfillmentEmailStatus.values),
                name="tickets_fulfillment_email_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["issue_status", "email_status"], name="tickets_fulfill_status_idx"),
            models.Index(fields=["next_attempt_at", "updated_at"], name="tickets_fulfill_retry_idx"),
        ]

    def __str__(self):
        return f"Fulfillment {self.booking.public_id} · {self.issue_status}/{self.email_status}"
