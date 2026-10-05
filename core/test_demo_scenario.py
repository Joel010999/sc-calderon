import os
from io import StringIO
from unittest.mock import patch
from django.core import management
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from customers.models import Customer
from notifications.models import NotificationStatus, TransactionalNotification
from operations.models import Route, Trip
from payments.models import Payment, PaymentStatus
from sales.models import Booking
from tickets.models import BoardingRecord, Ticket, TicketFulfillment

class DemoScenarioCommandTests(TransactionTestCase):
    def test_dry_run_does_not_write(self):
        before = (Booking.objects.count(), Customer.objects.count())
        management.call_command("seed_demo_scenario", "--dry-run", stdout=StringIO())
        self.assertEqual(before, (Booking.objects.count(), Customer.objects.count()))
    def test_production_is_rejected(self):
        with override_settings(DEBUG=False):
            with patch.dict(os.environ, {"SCVIAJES_ENVIRONMENT": "production", "DEMO_SCENARIO_ENABLED": "true"}):
                with self.assertRaises(CommandError):
                    management.call_command("seed_demo_scenario", stdout=StringIO())
    def test_seed_is_idempotent_and_reset_is_scoped(self):
        management.call_command("seed_demo_scenario", stdout=StringIO())
        first = Booking.objects.filter(email__endswith="@demo.scviajes.invalid").count()
        management.call_command("seed_demo_scenario", stdout=StringIO())
        self.assertEqual(first, Booking.objects.filter(email__endswith="@demo.scviajes.invalid").count())
        management.call_command("seed_demo_scenario", "--reset", stdout=StringIO())
        self.assertEqual(Booking.objects.filter(email__endswith="@demo.scviajes.invalid").count(), 0)

    def test_seed_covers_acceptance_surfaces(self):
        management.call_command("seed_demo_scenario", stdout=StringIO())
        self.assertEqual(Route.objects.filter(code__startswith="DEMO-20261005-").count(), 2)
        self.assertEqual(Trip.objects.filter(route__code__startswith="DEMO-20261005-").count(), 2)
        self.assertEqual(Booking.objects.filter(email__endswith="@demo.scviajes.invalid").count(), 8)
        self.assertEqual(Booking.objects.get(email="roundtrip@demo.scviajes.invalid").legs.count(), 2)
        self.assertTrue(Booking.objects.filter(email=f"held@demo.scviajes.invalid", status="HELD").exists())
        self.assertTrue(Booking.objects.filter(email=f"expired@demo.scviajes.invalid", status="EXPIRED").exists())
        self.assertTrue(Payment.objects.filter(status=PaymentStatus.AWAITING_VOUCHER).exists())
        self.assertTrue(Payment.objects.filter(status=PaymentStatus.UNDER_REVIEW).exists())
        self.assertTrue(Ticket.objects.filter(status="ISSUED").exists())
        self.assertTrue(BoardingRecord.objects.filter(status="ACTIVE").exists())
        self.assertTrue(TicketFulfillment.objects.filter(issue_status__in=["PENDING", "FAILED"]).exists())
        self.assertTrue(TransactionalNotification.objects.filter(status=NotificationStatus.PENDING).exists())
