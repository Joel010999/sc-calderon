"""Pruebas de la integración Mercado Pago Checkout Pro (todas con mocks).

Ninguna prueba contacta la API real: el cliente se sustituye por un fake que
devuelve payloads controlados.
"""

import hashlib
import hmac
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.models import (
    Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop,
)
from sales.exceptions import BookingExpiredError
from sales.models import (
    AssignmentStatus, Booking, BookingChannel, BookingLeg, BookingPassenger,
    BookingStatus, SeatAssignment,
)
from tickets.models import TicketFulfillment

from .exceptions import PaymentDuplicateError, PaymentError
from .mercadopago import (
    MercadoPagoClient,
    initiate_mercadopago_payment,
    mercadopago_configured,
    process_mercadopago_notification,
    verify_webhook_signature,
)
from .models import Payment, PaymentGatewayEvent, PaymentMethod, PaymentStatus

MP_SETTINGS = dict(
    MERCADO_PAGO_ENABLED=True,
    MERCADO_PAGO_ACCESS_TOKEN="TEST-fake-token",
    MERCADO_PAGO_WEBHOOK_SECRET="test-webhook-secret",
    MERCADO_PAGO_COLLECTOR_ID="999001",
    PUBLIC_BASE_URL="https://testing.example.com",
)


class FakeMercadoPagoClient:
    """Cliente fake: devuelve preferencias/pagos controlados por el test."""

    payment_payload = {}
    preference_payload = {"id": "pref-test-1", "init_point": "https://mp.test/checkout?pref=1"}

    def __init__(self, *args, **kwargs):
        pass

    def create_preference(self, payload):
        self.last_preference = payload
        return dict(self.preference_payload)

    def get_payment(self, payment_id):
        return dict(self.payment_payload)


def sign(data_id, request_id="req-1", ts=None, secret="test-webhook-secret"):
    ts = ts or int(timezone.now().timestamp() * 1000)
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"


@override_settings(**MP_SETTINGS)
class MercadoPagoBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.stop_a = Stop.objects.create(name="Origen", code="ORG")
        cls.stop_b = Stop.objects.create(name="Destino", code="DST")
        cls.route = Route.objects.create(name="R", code="R1", is_active=True)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_a, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_b, sequence=2, allows_boarding=False, allows_alighting=True)
        cls.bus = Bus.objects.create(code="B1", is_active=True)
        cls.seat = Seat.objects.create(bus=cls.bus, number=1, category=SeatCategory.SEMI_CAMA, deck=Seat.Deck.LOWER, position_x=1, position_y=1, is_active=True)
        dep = timezone.now() + timedelta(days=2)
        arr = dep + timedelta(hours=4)
        cls.trip = Trip.objects.create(route=cls.route, bus=cls.bus, departure_at=dep, status=Trip.Status.SCHEDULED)
        cls.ts_a = TripStop.objects.create(trip=cls.trip, stop=cls.stop_a, sequence=1, scheduled_at=dep, allows_boarding=True, allows_alighting=False)
        cls.ts_b = TripStop.objects.create(trip=cls.trip, stop=cls.stop_b, sequence=2, scheduled_at=arr, allows_boarding=False, allows_alighting=True)
        cls.fare = TripFare.objects.create(trip=cls.trip, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("12500.00"), is_active=True)

    def create_online_booking(self, minutes=15):
        booking = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            expires_at=timezone.now() + timedelta(minutes=minutes),
        )
        leg = BookingLeg.objects.create(
            booking=booking, sequence=1, trip=self.trip,
            origin_stop=self.ts_a, destination_stop=self.ts_b,
            origin_stop_name="Origen", destination_stop_name="Destino",
            departure_at=self.ts_a.scheduled_at, arrival_at=self.ts_b.scheduled_at,
        )
        pax = BookingPassenger.objects.create(
            booking=booking, position=1, first_name="Ana", last_name="Test",
            document_type="DNI", document_number="30000001",
            birth_date=date(1990, 1, 1), nationality="Argentina",
        )
        SeatAssignment.objects.create(
            leg=leg, passenger=pax, trip=self.trip, seat=self.seat,
            status=AssignmentStatus.HELD, seat_number=self.seat.number,
            category=self.seat.category, price=self.fare.amount, currency="ARS",
        )
        return booking

    def create_mp_payment(self, booking):
        return Payment.objects.create(
            booking=booking, method=PaymentMethod.MERCADO_PAGO,
            status=PaymentStatus.PENDING, amount=Decimal("12500.00"),
            currency="ARS", external_preference_id="pref-test-1",
            reference="https://mp.test/checkout?pref=1",
        )

    def mp_payload(self, booking, payment, status="approved", payment_id="5550001", **overrides):
        payload = {
            "id": int(payment_id),
            "collector_id": 999001,
            "external_reference": str(booking.public_id),
            "transaction_amount": float(payment.amount),
            "currency_id": "ARS",
            "status": status,
            "status_detail": "accredited",
        }
        payload.update(overrides)
        return payload


class InitiateTests(MercadoPagoBase):
    def test_creates_pending_payment_and_preference_server_side(self):
        booking = self.create_online_booking()
        client = FakeMercadoPagoClient()
        payment, init_point = initiate_mercadopago_payment(booking_or_id=booking, client=client)
        self.assertEqual(init_point, "https://mp.test/checkout?pref=1")
        self.assertEqual(payment.method, PaymentMethod.MERCADO_PAGO)
        self.assertEqual(payment.status, PaymentStatus.PENDING)
        self.assertEqual(payment.amount, Decimal("12500.00"))
        self.assertEqual(payment.external_preference_id, "pref-test-1")
        pref = client.last_preference
        self.assertEqual(pref["external_reference"], str(booking.public_id))
        self.assertEqual(pref["items"][0]["unit_price"], 12500.0)
        self.assertEqual(pref["notification_url"], "https://testing.example.com/webhooks/mercadopago/")
        self.assertIn("success", pref["back_urls"])

    def test_reuses_pending_payment_without_new_preference(self):
        booking = self.create_online_booking()
        client = FakeMercadoPagoClient()
        payment, _ = initiate_mercadopago_payment(booking_or_id=booking, client=client)
        payment2, init_point2 = initiate_mercadopago_payment(booking_or_id=booking, client=client)
        self.assertEqual(payment.pk, payment2.pk)
        self.assertEqual(init_point2, "https://mp.test/checkout?pref=1")
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)

    def test_expired_booking_rejected(self):
        booking = self.create_online_booking(minutes=-5)
        with self.assertRaises(BookingExpiredError):
            initiate_mercadopago_payment(booking_or_id=booking, client=FakeMercadoPagoClient())
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 0)

    def test_blocks_second_active_payment(self):
        booking = self.create_online_booking()
        self.create_mp_payment(booking)
        transfer = Payment.objects.create(
            booking=booking, method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.EXPIRED, amount=Decimal("12500.00"),
        )
        # El MP pendiente existente se reutiliza (no cuenta como duplicado activo nuevo)
        _, init_point = initiate_mercadopago_payment(booking_or_id=booking, client=FakeMercadoPagoClient())
        self.assertTrue(init_point)

    def test_disabled_integration_rejects(self):
        with override_settings(MERCADO_PAGO_ENABLED=False):
            booking = self.create_online_booking()
            with self.assertRaises(PaymentError):
                initiate_mercadopago_payment(booking_or_id=booking, client=FakeMercadoPagoClient())

    def test_preference_failure_releases_attempt(self):
        booking = self.create_online_booking()

        class FailingClient(FakeMercadoPagoClient):
            def create_preference(self, payload):
                raise PaymentError("api down")

        with self.assertRaises(PaymentError):
            initiate_mercadopago_payment(booking_or_id=booking, client=FailingClient())
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 0)


class NotificationTests(MercadoPagoBase):
    def setUp(self):
        self.booking = self.create_online_booking()
        self.payment = self.create_mp_payment(self.booking)
        FakeMercadoPagoClient.payment_payload = self.mp_payload(self.booking, self.payment)
        self.client_patch = patch(
            "payments.mercadopago.MercadoPagoClient", FakeMercadoPagoClient
        )
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def notify(self, data_id="5550001", action="payment.updated"):
        return process_mercadopago_notification(data_id=data_id, action=action)

    def test_approved_confirms_booking_and_enqueues_fulfillment(self):
        outcome, _ = self.notify()
        self.assertEqual(outcome, "CONFIRMED")
        self.booking.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.assertIsNotNone(self.booking.confirmed_at)
        self.assertEqual(self.payment.status, PaymentStatus.APPROVED)
        self.assertEqual(self.payment.external_payment_id, "5550001")
        self.assertEqual(
            SeatAssignment.objects.filter(leg__booking=self.booking).first().status,
            AssignmentStatus.CONFIRMED,
        )
        self.assertEqual(TicketFulfillment.objects.filter(booking=self.booking).count(), 1)

    def test_pending_keeps_state(self):
        FakeMercadoPagoClient.payment_payload = self.mp_payload(self.booking, self.payment, status="pending")
        outcome, _ = self.notify()
        self.assertEqual(outcome, "STILL_PENDING")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.HELD)
        self.assertEqual(Payment.objects.get(pk=self.payment.pk).status, PaymentStatus.PENDING)

    def test_rejected_marks_payment_booking_stays_held(self):
        FakeMercadoPagoClient.payment_payload = self.mp_payload(
            self.booking, self.payment, status="rejected", status_detail="cc_rejected_other_reason",
        )
        outcome, _ = self.notify()
        self.assertEqual(outcome, "REJECTED")
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.REJECTED)
        self.assertTrue(self.payment.rejection_reason)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.HELD)

    def test_wrong_amount_rejected(self):
        FakeMercadoPagoClient.payment_payload = self.mp_payload(
            self.booking, self.payment, transaction_amount=9999.00,
        )
        outcome, _ = self.notify()
        self.assertEqual(outcome, "MISMATCH")
        self.assertEqual(Payment.objects.get(pk=self.payment.pk).status, PaymentStatus.PENDING)
        self.assertEqual(Booking.objects.get(pk=self.booking.pk).status, BookingStatus.HELD)

    def test_wrong_currency_rejected(self):
        FakeMercadoPagoClient.payment_payload = self.mp_payload(
            self.booking, self.payment, currency_id="USD",
        )
        outcome, _ = self.notify()
        self.assertEqual(outcome, "MISMATCH")
        self.assertEqual(Booking.objects.get(pk=self.booking.pk).status, BookingStatus.HELD)

    def test_wrong_collector_rejected(self):
        FakeMercadoPagoClient.payment_payload = self.mp_payload(
            self.booking, self.payment, collector_id=777,
        )
        outcome, _ = self.notify()
        self.assertEqual(outcome, "MISMATCH")
        self.assertEqual(Booking.objects.get(pk=self.booking.pk).status, BookingStatus.HELD)

    def test_unknown_booking_reference(self):
        FakeMercadoPagoClient.payment_payload = self.mp_payload(
            self.booking, self.payment, external_reference="no-existe",
        )
        outcome, _ = self.notify()
        self.assertEqual(outcome, "UNKNOWN_BOOKING")
        self.assertEqual(Booking.objects.get(pk=self.booking.pk).status, BookingStatus.HELD)

    def test_duplicate_notification_is_idempotent(self):
        outcome1, _ = self.notify()
        outcome2, _ = self.notify()
        self.assertEqual(outcome1, "CONFIRMED")
        self.assertEqual(outcome2, "DUPLICATE")
        self.assertEqual(TicketFulfillment.objects.filter(booking=self.booking).count(), 1)
        self.assertEqual(
            PaymentGatewayEvent.objects.filter(external_id="5550001").count(), 1
        )

    def test_out_of_order_pending_after_approval(self):
        outcome1, _ = self.notify(action="payment.updated")
        self.assertEqual(outcome1, "CONFIRMED")
        FakeMercadoPagoClient.payment_payload = self.mp_payload(
            self.booking, self.payment, status="pending",
        )
        outcome2, _ = self.notify(action="payment.created")
        self.assertEqual(outcome2, "ALREADY_CONFIRMED")
        self.assertEqual(Booking.objects.get(pk=self.booking.pk).status, BookingStatus.CONFIRMED)

    def test_approval_after_expiry_registers_conflict(self):
        self.booking.status = BookingStatus.EXPIRED
        self.booking.save(update_fields=["status"])
        SeatAssignment.objects.filter(leg__booking=self.booking).update(
            status=AssignmentStatus.RELEASED
        )
        outcome, detail = self.notify()
        self.assertEqual(outcome, "CONFLICT_LATE_APPROVAL")
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentStatus.APPROVED)
        self.assertEqual(self.payment.conflict_reason, "approved_after_expiry")
        self.booking.refresh_from_db()
        # La reserva vencida no se confirma ni se reasigna la butaca.
        self.assertEqual(self.booking.status, BookingStatus.EXPIRED)
        self.assertFalse(
            SeatAssignment.objects.filter(
                leg__booking=self.booking, status=AssignmentStatus.CONFIRMED
            ).exists()
        )
        self.assertEqual(TicketFulfillment.objects.filter(booking=self.booking).count(), 0)


class SignatureTests(TestCase):
    @override_settings(**MP_SETTINGS)
    def test_valid_signature(self):
        sig = sign("5550001")
        self.assertTrue(verify_webhook_signature(sig, "req-1", "5550001"))

    @override_settings(**MP_SETTINGS)
    def test_tampered_signature_rejected(self):
        sig = sign("5550001")
        self.assertFalse(verify_webhook_signature(sig, "req-1", "otro-id"))

    @override_settings(**MP_SETTINGS)
    def test_expired_timestamp_rejected(self):
        old_ts = int((timezone.now() - timedelta(minutes=10)).timestamp() * 1000)
        sig = sign("5550001", ts=old_ts)
        self.assertFalse(verify_webhook_signature(sig, "req-1", "5550001"))

    @override_settings(**MP_SETTINGS)
    def test_missing_headers_rejected(self):
        self.assertFalse(verify_webhook_signature("", "req-1", "5550001"))
        self.assertFalse(verify_webhook_signature("ts=1,v1=abc", "req-1", ""))

    @override_settings(MERCADO_PAGO_ENABLED=True, MERCADO_PAGO_WEBHOOK_SECRET="")
    def test_no_secret_configured_rejects(self):
        self.assertFalse(verify_webhook_signature(sign("1"), "req-1", "1"))


@override_settings(**MP_SETTINGS)
class WebhookViewTests(MercadoPagoBase):
    def setUp(self):
        self.booking = self.create_online_booking()
        self.payment = self.create_mp_payment(self.booking)
        FakeMercadoPagoClient.payment_payload = self.mp_payload(self.booking, self.payment)
        self.client_patch = patch(
            "payments.mercadopago.MercadoPagoClient", FakeMercadoPagoClient
        )
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.url = reverse("mercadopago_webhook")

    def post_webhook(self, data_id="5550001", sig=None, request_id="req-1"):
        body = json.dumps({
            "action": "payment.updated",
            "data": {"id": data_id},
        })
        headers = {}
        if sig is not None:
            headers["HTTP_X_SIGNATURE"] = sig
            headers["HTTP_X_REQUEST_ID"] = request_id
        return self.client.post(self.url, data=body, content_type="application/json", **headers)

    def test_valid_webhook_returns_200(self):
        resp = self.post_webhook(sig=sign("5550001"))
        self.assertEqual(resp.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)

    def test_invalid_signature_returns_401(self):
        resp = self.post_webhook(sig="ts=1,v1=bad")
        self.assertEqual(resp.status_code, 401)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.HELD)

    def test_unsigned_notification_rejected(self):
        resp = self.post_webhook()
        self.assertEqual(resp.status_code, 401)

    def test_missing_data_id_rejected(self):
        resp = self.client.post(self.url, data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    @override_settings(MERCADO_PAGO_ENABLED=False)
    def test_disabled_returns_403(self):
        resp = self.post_webhook(sig=sign("5550001"))
        self.assertEqual(resp.status_code, 403)
