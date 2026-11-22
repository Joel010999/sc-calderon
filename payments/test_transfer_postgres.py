"""Validación de transferencias (ventas internas y compras web) orientada a PostgreSQL.

Las pruebas de concurrencia y de motor sólo se ejecutan en PostgreSQL (workflows
de CI); el resto también corre en SQLite para desarrollo local.
"""

import os
import shutil
import tempfile
import threading
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from payments.models import Payment, PaymentStatus
from payments.services import (
    initiate_public_transfer_payment,
    register_transfer_payment,
    review_transfer_payment,
    upload_public_transfer_voucher,
)
from sales.exceptions import SeatUnavailableError
from sales.internal_transfer import create_immediate_transfer_sale, create_internal_transfer_booking
from sales.models import AssignmentStatus, Booking, BookingChannel, BookingStatus, SeatAssignment
from sales.services import create_online_booking
from tickets.models import FulfillmentIssueStatus, Ticket, TicketFulfillment
from tickets.services import process_booking_fulfillment, reconcile_confirmed_fulfillments

User = get_user_model()


def _pax(doc="30111222", first="Ana", last="Prueba"):
    return {"first_name": first, "last_name": last, "document_type": "DNI", "document_number": doc,
            "birth_date": date(1990, 1, 1), "nationality": "Argentina"}


def _voucher(name="comprobante.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 comprobante ficticio", content_type="application/pdf")


class TransferValidationBase(TransactionTestCase):
    def setUp(self):
        self.protected = tempfile.mkdtemp()
        self.public_media = tempfile.mkdtemp()
        self.tickets_root = tempfile.mkdtemp()
        override = override_settings(
            PROTECTED_MEDIA_ROOT=self.protected, MEDIA_ROOT=self.public_media,
            TICKETS_STORAGE_ROOT=self.tickets_root,
        )
        override.enable()
        self.addCleanup(override.disable)
        for path in (self.protected, self.public_media, self.tickets_root):
            self.addCleanup(shutil.rmtree, path, ignore_errors=True)

        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        seller_group, _ = Group.objects.get_or_create(name="Vendedor")
        self.admin = User.objects.create_user(username="pgv_admin", password="x")
        self.admin.groups.add(admin_group)
        self.superuser = User.objects.create_superuser(username="pgv_super", password="x", email="s@x.invalid")
        self.seller_1 = User.objects.create_user(username="pgv_seller1", password="x")
        self.seller_2 = User.objects.create_user(username="pgv_seller2", password="x")
        for seller in (self.seller_1, self.seller_2):
            seller.groups.add(seller_group)

        cba = Stop.objects.create(code="PGCBA", name="Córdoba", city="Córdoba", province="Córdoba")
        ssj = Stop.objects.create(code="PGSSJ", name="Jujuy", city="Jujuy", province="Jujuy")
        route = Route.objects.create(code="PG-CBA-SSJ", name="Córdoba a Jujuy", is_active=True)
        RouteStop.objects.create(route=route, stop=cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=route, stop=ssj, sequence=2, allows_boarding=False, allows_alighting=True)
        bus = Bus.objects.create(code="PG-BUS", is_active=True)
        for number in (1, 2, 3):
            Seat.objects.create(bus=bus, number=number, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA,
                                position_x=number, position_y=0, is_active=True)
        dep = timezone.now() + timedelta(days=3)
        self.trip = Trip.objects.create(route=route, bus=bus, departure_at=dep, status=Trip.Status.SCHEDULED)
        self.ts_cba = TripStop.objects.create(trip=self.trip, stop=cba, sequence=1, scheduled_at=dep,
                                              allows_boarding=True, allows_alighting=False)
        self.ts_ssj = TripStop.objects.create(trip=self.trip, stop=ssj, sequence=2,
                                              scheduled_at=dep + timedelta(hours=10),
                                              allows_boarding=False, allows_alighting=True)
        TripFare.objects.create(trip=self.trip, seat_category=SeatCategory.CAMA, amount=Decimal("20000.00"),
                                is_active=True)

    def require_postgresql(self):
        if connection.vendor != "postgresql":
            self.skipTest("Prueba específica de PostgreSQL (se ejecuta en CI).")

    def internal_booking(self, seller, seat=1, doc="30111222"):
        return create_internal_transfer_booking(
            seller=seller, trip=self.trip, origin_stop=self.ts_cba, destination_stop=self.ts_ssj,
            seat_numbers=[seat], seat_category=SeatCategory.CAMA, email="interna@x.invalid",
            phone="3511112222", passenger_data=[_pax(doc)],
        )

    def immediate_sale(self, seller, seat=1, voucher=True, verified=True, doc="30111222"):
        return create_immediate_transfer_sale(
            seller=seller, trip=self.trip, origin_stop=self.ts_cba, destination_stop=self.ts_ssj,
            seat_numbers=[seat], seat_category=SeatCategory.CAMA, email="inmediata@x.invalid",
            phone="3511112222", passenger_data=[_pax(doc)], voucher=_voucher() if voucher else None,
            payment_verified=verified,
        )

    def public_payment_under_review(self, seat):
        seat_obj = Seat.objects.get(bus=self.trip.bus, number=seat)
        booking = create_online_booking(
            email="web@x.invalid", phone="3511112222",
            legs=[{"trip": self.trip, "origin_stop": self.ts_cba, "destination_stop": self.ts_ssj,
                   "seats": [seat_obj]}],
            passengers_data=[_pax("40111222", "Web", "Compradora")],
        )
        initiate_public_transfer_payment(booking_or_id=booking)
        return booking, upload_public_transfer_voucher(booking_or_id=booking, voucher_file=_voucher())

    def stored_vouchers(self):
        return [os.path.join(d, f) for d, _, files in os.walk(self.protected) for f in files]

    def assert_single_confirmation(self, booking):
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)
        self.assertEqual(Payment.objects.filter(booking=booking, status=PaymentStatus.APPROVED).count(), 1)
        self.assertEqual(SeatAssignment.objects.filter(leg__booking=booking).count(), 1)
        self.assertEqual(
            SeatAssignment.objects.filter(leg__booking=booking, status=AssignmentStatus.CONFIRMED).count(), 1
        )
        self.assertEqual(TicketFulfillment.objects.filter(booking=booking).count(), 1)
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 1)

    @staticmethod
    def run_concurrently(*callables):
        barrier = threading.Barrier(len(callables))
        results, errors = [], []

        def worker(fn):
            try:
                barrier.wait(timeout=10)
                results.append(fn())
            except Exception as exc:  # noqa: BLE001 - se inspecciona en la prueba
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(fn,)) for fn in callables]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        if any(thread.is_alive() for thread in threads):
            raise AssertionError("Un hilo concurrente no terminó a tiempo.")
        return results, errors


class PostgresEngineTests(TransferValidationBase):
    def test_engine_is_postgresql_16(self):
        self.require_postgresql()
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            version = int(cursor.fetchone()[0])
            cursor.execute("SELECT current_database()")
            database = cursor.fetchone()[0]
        print(f"\n[motor] PostgreSQL server_version_num={version} base={database}")
        self.assertEqual(version // 10000, 16)
        self.assertTrue(database.startswith("test_"))


class InternalTransferValidationTests(TransferValidationBase):
    def test_voucher_is_mandatory_for_internal_payment_and_immediate_sale(self):
        booking = self.internal_booking(self.seller_1)
        with self.assertRaises(ValidationError):
            register_transfer_payment(booking_or_id=booking, seller=self.seller_1, voucher=None)
        with self.assertRaises(ValidationError):
            self.immediate_sale(self.seller_1, seat=2, voucher=False)
        with self.assertRaises(ValidationError):
            self.immediate_sale(self.seller_1, seat=2, verified=False)
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(Booking.objects.count(), 1)

    def test_owner_seller_confirms_internal_sale_with_voucher(self):
        booking = self.internal_booking(self.seller_1)
        payment = register_transfer_payment(booking_or_id=booking, seller=self.seller_1, voucher=_voucher())
        review_transfer_payment(payment_or_id=payment, reviewer=self.seller_1, approved=True)
        self.assert_single_confirmation(booking)
        payment.refresh_from_db()
        self.assertEqual(payment.reviewed_by, self.seller_1)

    def test_immediate_sale_by_owner_confirms_and_issues_once(self):
        booking = self.immediate_sale(self.seller_1)
        self.assertEqual(booking.channel, BookingChannel.MANUAL)
        self.assert_single_confirmation(booking)

    def test_seller_isolation_and_online_manual_separation(self):
        internal = self.internal_booking(self.seller_1)
        internal_payment = register_transfer_payment(booking_or_id=internal, seller=self.seller_1,
                                                     voucher=_voucher())
        _, public_payment = self.public_payment_under_review(seat=2)
        Payment.objects.filter(pk=public_payment.pk).update(registered_by=self.seller_1)
        public_payment.refresh_from_db()

        with self.assertRaises(ValidationError):
            review_transfer_payment(payment_or_id=internal_payment, reviewer=self.seller_2, approved=True)
        for seller in (self.seller_1, self.seller_2):
            with self.assertRaises(ValidationError):
                review_transfer_payment(payment_or_id=public_payment, reviewer=seller, approved=True)
        internal_payment.refresh_from_db()
        public_payment.refresh_from_db()
        self.assertEqual(internal_payment.status, PaymentStatus.UNDER_REVIEW)
        self.assertEqual(public_payment.status, PaymentStatus.UNDER_REVIEW)

        review_transfer_payment(payment_or_id=public_payment, reviewer=self.superuser, approved=True)
        self.assert_single_confirmation(public_payment.booking)

    def test_voucher_storage_failure_never_confirms_payment(self):
        with patch("payments.storage.ProtectedFileSystemStorage._save", side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError):
                self.immediate_sale(self.seller_1)
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(SeatAssignment.objects.exists())
        self.assertFalse(Ticket.objects.exists())
        # La butaca sigue disponible para una venta posterior.
        self.assert_single_confirmation(self.immediate_sale(self.seller_1))

    def test_confirmation_failure_after_voucher_saved_leaves_no_orphan_file(self):
        with patch("payments.services.review_transfer_payment", side_effect=RuntimeError("fallo al confirmar")):
            with self.assertRaises(RuntimeError):
                self.immediate_sale(self.seller_1)
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(self.stored_vouchers(), [])

    def test_register_failure_after_voucher_saved_leaves_no_orphan_file(self):
        booking = self.internal_booking(self.seller_1)
        with patch("notifications.services.schedule_notification", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                register_transfer_payment(booking_or_id=booking, seller=self.seller_1, voucher=_voucher())
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(self.stored_vouchers(), [])

    def test_public_upload_failure_after_voucher_saved_leaves_no_orphan_file(self):
        seat = Seat.objects.get(bus=self.trip.bus, number=2)
        booking = create_online_booking(
            email="web@x.invalid", phone="3511112222",
            legs=[{"trip": self.trip, "origin_stop": self.ts_cba, "destination_stop": self.ts_ssj, "seats": [seat]}],
            passengers_data=[_pax("40111222", "Web", "Compradora")],
        )
        payment = initiate_public_transfer_payment(booking_or_id=booking)
        with patch("notifications.services.schedule_notification", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                upload_public_transfer_voucher(booking_or_id=booking, voucher_file=_voucher())
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.AWAITING_VOUCHER)
        self.assertEqual(self.stored_vouchers(), [])

    def test_repeated_confirmation_is_idempotent(self):
        booking = self.internal_booking(self.seller_1)
        payment = register_transfer_payment(booking_or_id=booking, seller=self.seller_1, voucher=_voucher())
        for reviewer in (self.seller_1, self.seller_1, self.admin):
            review_transfer_payment(payment_or_id=payment, reviewer=reviewer, approved=True)
        self.assert_single_confirmation(booking)

    def test_fulfillment_failure_is_recovered_without_duplicates(self):
        with patch("tickets.services.build_ticket_pdf", side_effect=OSError("pdf no disponible")):
            booking = self.immediate_sale(self.seller_1)
        job = TicketFulfillment.objects.get(booking=booking)
        self.assertEqual(job.issue_status, FulfillmentIssueStatus.FAILED)
        self.assertFalse(Ticket.objects.filter(booking=booking).exists())
        reconcile_confirmed_fulfillments()
        process_booking_fulfillment(job.pk, retry=True)
        self.assert_single_confirmation(booking)


class TransferConcurrencyPostgresTests(TransferValidationBase):
    def test_concurrent_internal_confirmation_by_owner_and_admin(self):
        self.require_postgresql()
        booking = self.internal_booking(self.seller_1)
        payment = register_transfer_payment(booking_or_id=booking, seller=self.seller_1, voucher=_voucher())
        _, errors = self.run_concurrently(
            lambda: review_transfer_payment(payment_or_id=payment.pk, reviewer=self.seller_1, approved=True),
            lambda: review_transfer_payment(payment_or_id=payment.pk, reviewer=self.admin, approved=True),
        )
        self.assertEqual(errors, [])
        self.assert_single_confirmation(booking)

    def test_concurrent_public_transfer_approval_by_two_admins(self):
        self.require_postgresql()
        booking, payment = self.public_payment_under_review(seat=1)
        _, errors = self.run_concurrently(
            lambda: review_transfer_payment(payment_or_id=payment.pk, reviewer=self.admin, approved=True),
            lambda: review_transfer_payment(payment_or_id=payment.pk, reviewer=self.superuser, approved=True),
        )
        self.assertEqual(errors, [])
        self.assert_single_confirmation(booking)

    def test_concurrent_immediate_sales_for_same_seat(self):
        self.require_postgresql()
        results, errors = self.run_concurrently(
            lambda: self.immediate_sale(self.seller_1, seat=3, doc="30111222"),
            lambda: self.immediate_sale(self.seller_2, seat=3, doc="30333444"),
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], (SeatUnavailableError, ValidationError))
        self.assertEqual(Booking.objects.filter(status=BookingStatus.CONFIRMED).count(), 1)
        self.assertEqual(Payment.objects.filter(status=PaymentStatus.APPROVED).count(), 1)
        self.assertEqual(SeatAssignment.objects.filter(seat__number=3).count(), 1)
        self.assert_single_confirmation(results[0])

    def test_concurrent_fulfillment_retries_issue_tickets_once(self):
        self.require_postgresql()
        with patch("tickets.services.build_ticket_pdf", side_effect=OSError("pdf no disponible")):
            booking = self.immediate_sale(self.seller_1)
        job = TicketFulfillment.objects.get(booking=booking)
        self.run_concurrently(
            lambda: process_booking_fulfillment(job.pk, retry=True),
            lambda: process_booking_fulfillment(job.pk, retry=True),
        )
        process_booking_fulfillment(job.pk, retry=True)
        self.assert_single_confirmation(booking)
