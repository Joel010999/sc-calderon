import json
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripStop
from payments.models import Payment, PaymentMethod, PaymentStatus
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)
from tickets.models import FulfillmentEmailStatus, FulfillmentIssueStatus
from .management.commands.run_operational_maintenance import Command

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class OperationalMaintenanceCommandTests(SimpleTestCase):
    @patch.object(Command, "_expired", return_value=False)
    @patch.object(Command, "_process", return_value=None)
    @patch.object(Command, "_candidates", return_value=[1, 2])
    def test_limit_is_not_reported_as_timeout(self, candidates, process, expired):
        output = StringIO()
        call_command("run_operational_maintenance", "--task", "expire", "--limit", "1", stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["processed"], 1)
        self.assertTrue(payload["limit_reached"])
        self.assertFalse(payload["timed_out"])

    @patch.object(Command, "_expired", return_value=False)
    @patch.object(
        Command,
        "_process",
        return_value=SimpleNamespace(
            issue_status=FulfillmentIssueStatus.FAILED,
            email_status=FulfillmentEmailStatus.PENDING,
        ),
    )
    @patch.object(Command, "_candidates", return_value=[1])
    def test_failed_fulfillment_returns_nonzero(self, candidates, process, expired):
        with self.assertRaises(CommandError):
            call_command("run_operational_maintenance", "--task", "fulfillment", "--limit", "1")


class ExpireTaskWithoutPaymentTests(TestCase):
    """Regresión: la tarea expire no debe descartar reservas HELD sin pagos."""

    def setUp(self):
        super().setUp()
        future = timezone.localtime(timezone.now(), AR_TZ).date() + timedelta(days=3)
        self.stop_a = Stop.objects.create(code="AAA", name="Origen Demo", city="Córdoba", province="Córdoba")
        self.stop_b = Stop.objects.create(code="BBB", name="Destino Demo", city="Jujuy", province="Jujuy")
        self.route = Route.objects.create(code="AAA-BBB", name="Origen → Destino")
        RouteStop.objects.create(route=self.route, stop=self.stop_a, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route, stop=self.stop_b, sequence=2, allows_boarding=False, allows_alighting=True)
        self.bus = Bus.objects.create(code="BUS-T", display_name="Bus Test", license_plate="TT000TT")
        dep = datetime.combine(future, datetime.min.time(), tzinfo=AR_TZ).replace(hour=14)
        arr = dep + timedelta(hours=8)
        self.trip = Trip.objects.create(route=self.route, bus=self.bus, departure_at=dep, status=Trip.Status.SCHEDULED)
        self.ts_orig = TripStop.objects.create(
            trip=self.trip, stop=self.stop_a, sequence=1, scheduled_at=dep,
            allows_boarding=True, allows_alighting=False,
        )
        self.ts_dest = TripStop.objects.create(
            trip=self.trip, stop=self.stop_b, sequence=2, scheduled_at=arr,
            allows_boarding=False, allows_alighting=True,
        )
        self.dep, self.arr = dep, arr

    def _held_booking(self, *, expires_at, email, seat_number):
        seat = Seat.objects.create(
            bus=self.bus, number=seat_number, deck=Seat.Deck.UPPER,
            category=SeatCategory.SEMI_CAMA, position_x=0, position_y=seat_number,
        )
        booking = Booking.objects.create(
            channel=BookingChannel.ONLINE, status=BookingStatus.HELD,
            email=email, expires_at=expires_at,
        )
        leg = BookingLeg.objects.create(
            booking=booking, sequence=1, trip=self.trip,
            origin_stop=self.ts_orig, destination_stop=self.ts_dest,
            origin_stop_name="Origen Demo", destination_stop_name="Destino Demo",
            departure_at=self.dep, arrival_at=self.arr,
        )
        passenger = BookingPassenger.objects.create(
            booking=booking, position=1, first_name="Test", last_name="Expira",
            document_number="12345678", document_type="DNI",
        )
        assignment = SeatAssignment.objects.create(
            leg=leg, passenger=passenger, trip=self.trip, seat=seat,
            status=AssignmentStatus.HELD, seat_number=seat.number,
            category=SeatCategory.SEMI_CAMA, price=Decimal("12500.00"),
        )
        return booking, assignment

    def _run_expire(self):
        output = StringIO()
        call_command("run_operational_maintenance", "--task", "expire", stdout=output)
        return json.loads(output.getvalue())

    def test_expire_processes_held_booking_without_payment(self):
        booking, assignment = self._held_booking(
            expires_at=timezone.now() - timedelta(minutes=5),
            email="sin-pago@demo.invalid", seat_number=10,
        )
        payload = self._run_expire()
        booking.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(payload["succeeded"], 1, payload)
        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        self.assertEqual(assignment.status, AssignmentStatus.RELEASED)

    def test_expire_keeps_unexpired_booking_without_payment_held(self):
        booking, _ = self._held_booking(
            expires_at=timezone.now() + timedelta(minutes=15),
            email="vigente@demo.invalid", seat_number=20,
        )
        self._run_expire()
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.HELD)

    def test_expire_skips_bookings_with_payments_in_review_states(self):
        for index, status in enumerate((
            PaymentStatus.AWAITING_VOUCHER,
            PaymentStatus.UNDER_REVIEW,
            PaymentStatus.REJECTED,
        )):
            booking, _ = self._held_booking(
                expires_at=timezone.now() - timedelta(minutes=5),
                email=f"pago-{status.lower()}@demo.invalid", seat_number=30 + index,
            )
            Payment.objects.create(
                booking=booking, method=PaymentMethod.BANK_TRANSFER,
                status=status, amount=Decimal("12500.00"),
            )
            self._run_expire()
            booking.refresh_from_db()
            self.assertEqual(booking.status, BookingStatus.HELD,
                             f"la reserva con pago {status} no debía expirar")

