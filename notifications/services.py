import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import NotificationStatus, NotificationType, TransactionalNotification

logger = logging.getLogger(__name__)


def _valid_recipient(email):
    return bool((email or "").strip())


def schedule_notification(*, booking, notification_type, event_key, payload=None, recipient_email=None):
    """Persiste el outbox en la transacción de negocio y habilita el despacho después del commit."""
    recipient = (recipient_email if recipient_email is not None else booking.email or "").strip()
    if not _valid_recipient(recipient) or not getattr(settings, "TRANSACTIONAL_NOTIFICATIONS_ENABLED", True):
        return
    snapshot = {str(key): value for key, value in (payload or {}).items() if value is not None}

    try:
        notification, _ = TransactionalNotification.objects.get_or_create(
            booking=booking,
            event_key=event_key,
            notification_type=notification_type,
            recipient_email=recipient,
            defaults={"payload": snapshot},
        )
    except IntegrityError:
        # La restricción única hace idempotente el callback ante carreras.
        notification = TransactionalNotification.objects.get(
            booking=booking, event_key=event_key,
            notification_type=notification_type, recipient_email=recipient,
        )

    # No se hace I/O aquí. Si el proceso cae después del commit, la fila queda
    # PENDING y el mantenimiento la recupera; el callback sólo despierta la fila.
    transaction.on_commit(
        lambda notification_id=notification.pk: TransactionalNotification.objects.filter(
            pk=notification_id, status=NotificationStatus.PENDING,
        ).update(next_attempt_at=None)
    )
    return notification


def _message(notification):
    ref = notification.payload.get("booking_ref", str(notification.booking.public_id))
    deadline = notification.payload.get("deadline", "")
    messages = {
        NotificationType.BOOKING_HELD: ("Reserva recibida", "Tu reserva fue creada y las butacas quedaron retenidas."),
        NotificationType.TRANSFER_STARTED: ("Transferencia iniciada", "Tenés 5 minutos para cargar el comprobante de transferencia."),
        NotificationType.VOUCHER_RECEIVED: ("Comprobante recibido", "Recibimos el comprobante. La reserva queda en revisión hasta 24 horas."),
        NotificationType.TRANSFER_APPROVED: ("Transferencia aprobada", "La transferencia fue aprobada. Los pasajes se enviarán por el flujo correspondiente."),
        NotificationType.TRANSFER_REJECTED: ("Transferencia rechazada", "La transferencia fue rechazada. Consultá el estado de tu reserva para conocer los próximos pasos."),
        NotificationType.BOOKING_EXPIRED: ("Reserva vencida", "La reserva venció y las butacas fueron liberadas."),
        NotificationType.MANUAL_BOOKING_CONFIRMED: ("Reserva confirmada", "Tu reserva manual fue confirmada."),
    }
    subject, body = messages[notification.notification_type]
    if deadline:
        body = f"{body} Plazo informado: {deadline}."
    return {"subject": f"SC Viajes · {subject}", "booking_ref": ref, "body": body}


def process_notification(notification_id, *, retry=False, now=None, actor=None):
    """Reclama una fila con lock/lease, envía fuera de la transacción y finaliza atómicamente."""
    now = now or timezone.now()
    lease_seconds = max(1, int(getattr(settings, "NOTIFICATIONS_LEASE_SECONDS", 300)))
    max_attempts = max(1, int(getattr(settings, "NOTIFICATIONS_MAX_ATTEMPTS", 5)))
    with transaction.atomic():
        notification = TransactionalNotification.objects.select_for_update().get(pk=notification_id)
        if notification.status == NotificationStatus.SENT:
            return notification
        if notification.status == NotificationStatus.PROCESSING and notification.lease_until and notification.lease_until > now:
            return notification
        if notification.status == NotificationStatus.FAILED and not retry and notification.next_attempt_at and notification.next_attempt_at > now:
            return notification
        if notification.attempts >= max_attempts and not retry:
            return notification
        notification.status = NotificationStatus.PROCESSING
        notification.attempts += 1
        notification.last_attempt_at = now
        notification.lease_until = now + timedelta(seconds=lease_seconds)
        notification.last_error = ""
        notification.save(update_fields=["status", "attempts", "last_attempt_at", "lease_until", "last_error", "updated_at"])

    message = _message(notification)
    context = {**message, "notification": notification}
    try:
        text_body = render_to_string("notifications/transactional.txt", context)
        html_body = render_to_string("notifications/transactional.html", context)
        email = EmailMultiAlternatives(
            subject=message["subject"], body=text_body, from_email=settings.DEFAULT_FROM_EMAIL,
            to=[notification.recipient_email], reply_to=([settings.EMAIL_REPLY_TO] if getattr(settings, "EMAIL_REPLY_TO", "") else []),
        )
        email.attach_alternative(html_body, "text/html")
        email.send(fail_silently=False)
    except Exception:
        logger.warning("Fallo al despachar notificacion id=%s", notification_id)
        with transaction.atomic():
            current = TransactionalNotification.objects.select_for_update().get(pk=notification_id)
            current.status = NotificationStatus.FAILED
            current.lease_until = None
            current.next_attempt_at = timezone.now() + timedelta(seconds=int(getattr(settings, "NOTIFICATIONS_RETRY_DELAY_SECONDS", 300)))
            current.last_error = "No se pudo entregar el correo"
            current.save(update_fields=["status", "lease_until", "next_attempt_at", "last_error", "updated_at"])
        return current

    with transaction.atomic():
        current = TransactionalNotification.objects.select_for_update().get(pk=notification_id)
        if current.status != NotificationStatus.SENT:
            current.status = NotificationStatus.SENT
            current.sent_at = timezone.now()
            current.lease_until = None
            current.next_attempt_at = None
            current.save(update_fields=["status", "sent_at", "lease_until", "next_attempt_at", "updated_at"])
        return current
