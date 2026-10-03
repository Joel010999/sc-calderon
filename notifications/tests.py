import json
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from sales.services import create_online_booking
from sales.tests import SalesBaseTestCase
from .models import NotificationStatus, NotificationType, TransactionalNotification
from .services import process_notification, schedule_notification


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    TRANSACTIONAL_NOTIFICATIONS_ENABLED=True,
    NOTIFICATIONS_RETRY_DELAY_SECONDS=0,
)
class NotificationOutboxTests(SalesBaseTestCase):
    def _booking(self):
        return create_online_booking(
            email="cliente@example.test",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )

    def test_online_booking_schedules_after_commit_and_is_idempotent(self):
        with self.captureOnCommitCallbacks(execute=True):
            booking = self._booking()
        notification = TransactionalNotification.objects.get(booking=booking)
        self.assertEqual(notification.notification_type, NotificationType.BOOKING_HELD)
        self.assertEqual(notification.status, NotificationStatus.PENDING)
        with self.captureOnCommitCallbacks(execute=True):
            schedule_notification(
                booking=booking, notification_type=NotificationType.BOOKING_HELD,
                event_key=f"booking:{booking.public_id}:held", payload={"booking_ref": str(booking.public_id)},
            )
        self.assertEqual(TransactionalNotification.objects.filter(booking=booking).count(), 1)

    def test_smtp_failure_is_recoverable_and_sent_is_not_duplicated(self):
        booking = self._booking()
        notification = TransactionalNotification.objects.create(
            booking=booking, notification_type=NotificationType.BOOKING_HELD,
            event_key="test:event:1", recipient_email=booking.email,
            payload={"booking_ref": str(booking.public_id)},
        )
        with patch("notifications.services.EmailMultiAlternatives.send", side_effect=OSError("smtp")):
            failed = process_notification(notification.pk, retry=True)
        self.assertEqual(failed.status, NotificationStatus.FAILED)
        with patch("notifications.services.EmailMultiAlternatives.send", return_value=1) as send:
            sent = process_notification(notification.pk, retry=True)
            again = process_notification(notification.pk, retry=True)
        self.assertEqual(sent.status, NotificationStatus.SENT)
        self.assertEqual(again.status, NotificationStatus.SENT)
        send.assert_called_once()

    def test_operational_maintenance_processes_notifications_with_json_summary(self):
        booking = self._booking()
        TransactionalNotification.objects.all().delete()
        notification = TransactionalNotification.objects.create(
            booking=booking, notification_type=NotificationType.BOOKING_HELD,
            event_key="test:event:maintenance", recipient_email=booking.email,
            payload={"booking_ref": str(booking.public_id)},
        )
        output = StringIO()
        with patch("notifications.services.EmailMultiAlternatives.send", return_value=1):
            call_command("run_operational_maintenance", "--task", "notifications", "--limit", "1", stdout=output)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["succeeded"], 1)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.SENT)

    def test_operational_maintenance_dry_run_does_not_write_or_send(self):
        booking = self._booking()
        TransactionalNotification.objects.all().delete()
        notification = TransactionalNotification.objects.create(
            booking=booking, notification_type=NotificationType.BOOKING_HELD,
            event_key="test:event:dry-run", recipient_email=booking.email,
            payload={"booking_ref": str(booking.public_id)},
        )
        output = StringIO()
        with patch("notifications.services.EmailMultiAlternatives.send") as send:
            call_command("run_operational_maintenance", "--task", "notifications", "--dry-run", "--limit", "1", stdout=output)
        notification.refresh_from_db()
        self.assertEqual(notification.status, NotificationStatus.PENDING)
        self.assertEqual(notification.attempts, 0)
        send.assert_not_called()
