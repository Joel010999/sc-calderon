from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from notifications.models import NotificationStatus, TransactionalNotification
from notifications.services import process_notification
from .models import AuditEvent
from .permissions import reservations_access


@reservations_access()
@require_POST
def notification_retry(request, notification_pk):
    notification = get_object_or_404(TransactionalNotification, pk=notification_pk)
    if notification.status == NotificationStatus.SENT:
        messages.info(request, "La notificación ya fue enviada y no se repetirá.")
        return redirect("panel:booking_detail", public_id=notification.booking.public_id)
    before = notification.status
    result = process_notification(notification.pk, retry=True, actor=request.user)
    AuditEvent.objects.create(
        actor=request.user,
        action=AuditEvent.Action.UPDATE,
        entity_type=TransactionalNotification._meta.label,
        entity_id=str(notification.pk),
        description="Reintento de notificación transaccional",
        before={"status": before},
        after={"status": result.status},
    )
    if result.status == NotificationStatus.SENT:
        messages.success(request, "La notificación fue enviada.")
    else:
        messages.error(request, "La notificación quedó pendiente para reintento.")
    return redirect("panel:booking_detail", public_id=notification.booking.public_id)
