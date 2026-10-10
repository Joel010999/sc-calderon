from django.db import models


class NotificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente"
    PROCESSING = "PROCESSING", "En proceso"
    SENT = "SENT", "Enviado"
    FAILED = "FAILED", "Fallido"


class NotificationType(models.TextChoices):
    BOOKING_HELD = "BOOKING_HELD", "Reserva creada"
    TRANSFER_STARTED = "TRANSFER_STARTED", "Transferencia iniciada"
    VOUCHER_RECEIVED = "VOUCHER_RECEIVED", "Comprobante recibido"
    TRANSFER_APPROVED = "TRANSFER_APPROVED", "Transferencia aprobada"
    TRANSFER_REJECTED = "TRANSFER_REJECTED", "Transferencia rechazada"
    BOOKING_EXPIRED = "BOOKING_EXPIRED", "Reserva vencida"
    MANUAL_BOOKING_CONFIRMED = "MANUAL_BOOKING_CONFIRMED", "Reserva manual confirmada"


class TransactionalNotification(models.Model):
    booking = models.ForeignKey("sales.Booking", on_delete=models.PROTECT, related_name="transactional_notifications")
    notification_type = models.CharField(max_length=40, choices=NotificationType.choices)
    event_key = models.CharField(max_length=120)
    recipient_email = models.EmailField()
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=NotificationStatus.choices, default=NotificationStatus.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=160, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "event_key", "notification_type", "recipient_email"],
                name="notifications_event_recipient_type_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="notif_status_next_idx"),
            models.Index(fields=["booking", "status"], name="notif_booking_status_idx"),
        ]

    def __str__(self):
        return f"Notificación {self.notification_type} · {self.status} · reserva {self.booking_id}"
