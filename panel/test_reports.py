"""Pruebas de contrato para el reporte básico de ventas del panel."""

import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from panel.models import AuditEvent
from panel.report_views import _safe_csv
from payments.models import Payment, PaymentMethod, PaymentStatus
from payments.services import register_cash_payment, register_transfer_payment, review_transfer_payment
from cash_register.services import open_cash
from cash_register.models import CashMovement
from sales.models import AssignmentStatus, Booking, BookingChannel, BookingLeg, BookingPassenger, BookingStatus, SeatAssignment


AR = ZoneInfo("America/Argentina/Buenos_Aires")
User = get_user_model()


class SalesReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_group = Group.objects.create(name="Administrador")
        cls.seller_group = Group.objects.create(name="Vendedor")
        cls.admin = User.objects.create_user(username="report-admin", password="pass")
        cls.admin.groups.add(cls.admin_group)
        cls.seller = User.objects.create_user(username="report-seller", password="pass")
        cls.seller.groups.add(cls.seller_group)
        open_cash(operator=cls.seller, opening_amount=Decimal("0.00"))
        cls.staff = User.objects.create_user(username="report-staff", password="pass", is_staff=True)
        cls.common = User.objects.create_user(username="report-common", password="pass")
        cls.stop_a = Stop.objects.create(name="Córdoba Capital", code="RPT-CBA")
        cls.stop_b = Stop.objects.create(name="San Salvador de Jujuy", code="RPT-SSJ")
        cls.route = Route.objects.create(name="Reporte", code="RPT", is_active=True)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_a, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_b, sequence=2, allows_boarding=False, allows_alighting=True)
        cls.bus = Bus.objects.create(code="RPT-BUS", is_active=True)
        cls.seats = [Seat.objects.create(bus=cls.bus, number=i, category=SeatCategory.CAMA,
                                         deck=Seat.Deck.LOWER, position_x=i, position_y=1, is_active=True)
                     for i in range(1, 45)]
        departure = timezone.now() + timedelta(days=10)
        cls.trip = Trip.objects.create(route=cls.route, bus=cls.bus, departure_at=departure,
                                       status=Trip.Status.SCHEDULED)
        cls.ts_a = TripStop.objects.create(trip=cls.trip, stop=cls.stop_a, sequence=1,
                                           scheduled_at=departure, allows_boarding=True, allows_alighting=False)
        cls.ts_b = TripStop.objects.create(trip=cls.trip, stop=cls.stop_b, sequence=2,
                                           scheduled_at=departure + timedelta(hours=10), allows_boarding=False, allows_alighting=True)
        cls.fare = TripFare.objects.create(trip=cls.trip, origin_stop=cls.ts_a, destination_stop=cls.ts_b,
                                           seat_category=SeatCategory.CAMA, amount=Decimal("1000.00"), is_active=True)

    def setUp(self):
        self.client = Client()

    def local(self, year, month, day, hour=12):
        return timezone.make_aware(datetime(year, month, day, hour), AR)

    def booking(self, *, channel=BookingChannel.MANUAL, seller=None, status=BookingStatus.CONFIRMED,
                confirmed_at=None, passengers=1, legs=1, email="pii@example.com"):
        when = confirmed_at or self.local(2026, 9, 15)
        expires_at = timezone.now() + timedelta(days=1) if status != BookingStatus.CONFIRMED else when + timedelta(days=1)
        booking = Booking.objects.create(channel=channel, seller=seller, status=status, email=email,
                                         phone="3515551234", expires_at=expires_at,
                                         confirmed_at=when if status == BookingStatus.CONFIRMED else None)
        passengers_by_position = {
            position: BookingPassenger.objects.create(
                booking=booking, position=position, first_name="Pasajero", last_name="Privado",
                document_type="DNI", document_number="30123456",
            )
            for position in range(1, passengers + 1)
        }
        used_seats = set(SeatAssignment.objects.filter(trip=self.trip).values_list("seat_id", flat=True))
        free_seats = [seat for seat in self.seats if seat.pk not in used_seats]
        seat_cursor = 0
        for sequence in range(1, legs + 1):
            leg = BookingLeg.objects.create(
                booking=booking, sequence=sequence, trip=self.trip, origin_stop=self.ts_a,
                destination_stop=self.ts_b, origin_stop_name=self.stop_a.name,
                destination_stop_name=self.stop_b.name, departure_at=self.ts_a.scheduled_at,
                arrival_at=self.ts_b.scheduled_at,
            )
            for position, passenger in passengers_by_position.items():
                seat = free_seats[seat_cursor]
                seat_cursor += 1
                SeatAssignment.objects.create(
                    leg=leg, passenger=passenger, trip=self.trip, seat=seat,
                    status=AssignmentStatus.CONFIRMED if status == BookingStatus.CONFIRMED else AssignmentStatus.HELD,
                    seat_number=seat.number, category=seat.category, price=self.fare.amount, currency="ARS",
                )
        return booking

    def approved_payment(self, booking, method, amount=Decimal("1000.00")):
        return Payment.objects.create(booking=booking, method=method, status=PaymentStatus.APPROVED,
                                      amount=amount, currency="ARS", reviewed_by=self.admin,
                                      reviewed_at=booking.confirmed_at, registered_by=self.seller)

    def voucher(self):
        return SimpleUploadedFile("privado.pdf", b"%PDF-1.4 comprobante", content_type="application/pdf")

    def report_url(self):
        return reverse("panel:sales_report")

    def csv_url(self):
        return reverse("panel:sales_report_csv")

    def test_includes_cash_bank_and_public_transfer_once_with_totals_passengers_and_legs(self):
        cash_booking = self.booking(seller=self.seller, status=BookingStatus.HELD, passengers=2, legs=2,
                                    confirmed_at=self.local(2026, 9, 10))
        cash = register_cash_payment(booking_or_id=cash_booking, seller=self.seller)
        bank_booking = self.booking(seller=self.seller, status=BookingStatus.HELD, confirmed_at=self.local(2026, 9, 11))
        bank = register_transfer_payment(booking_or_id=bank_booking, seller=self.seller, voucher=self.voucher())
        review_transfer_payment(payment_or_id=bank, reviewer=self.admin, approved=True)
        self.assertEqual(CashMovement.objects.filter(payment=bank).count(), 0)
        public_booking = self.booking(channel=BookingChannel.ONLINE, seller=None, confirmed_at=self.local(2026, 9, 12))
        public = self.approved_payment(public_booking, PaymentMethod.BANK_TRANSFER, Decimal("1000.00"))

        self.client.force_login(self.admin)
        response = self.client.get(self.report_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["payment_count"], 3)
        self.assertEqual(response.context["confirmed_sales_count"], 3)
        self.assertEqual(response.context["total_amount"], cash.amount + bank.amount + public.amount)
        self.assertEqual(response.context["cash_amount"], cash.amount)
        self.assertEqual(response.context["transfer_amount"], bank.amount + public.amount)
        self.assertEqual(response.context["passenger_count"], 4)
        self.assertEqual(response.context["leg_count"], 4)
        self.assertContains(response, str(cash_booking.public_id))
        self.assertContains(response, str(bank_booking.public_id))
        self.assertContains(response, str(public_booking.public_id))
        self.assertContains(response, "Sin vendedor")

    def test_excludes_nonapproved_statuses_and_unconfirmed_booking(self):
        included = self.booking(seller=self.seller)
        self.approved_payment(included, PaymentMethod.CASH)
        for status in (PaymentStatus.UNDER_REVIEW, PaymentStatus.REJECTED, PaymentStatus.EXPIRED,
                       PaymentStatus.AWAITING_VOUCHER):
            booking = self.booking(seller=self.seller, status=BookingStatus.HELD,
                                   confirmed_at=self.local(2026, 9, 16))
            Payment.objects.create(booking=booking, method=PaymentMethod.BANK_TRANSFER, status=status,
                                   amount=Decimal("999.00"), currency="ARS",
                                   proof_deadline_at=booking.expires_at if status == PaymentStatus.AWAITING_VOUCHER else None,
                                   rejection_reason="no corresponde" if status == PaymentStatus.REJECTED else "")
        unconfirmed = self.booking(seller=self.seller, status=BookingStatus.HELD)
        Payment.objects.create(booking=unconfirmed, method=PaymentMethod.CASH, status=PaymentStatus.APPROVED,
                               amount=Decimal("999.00"), currency="ARS", reviewed_by=self.admin,
                               reviewed_at=self.local(2026, 9, 15))
        self.client.force_login(self.seller)
        response = self.client.get(self.report_url())
        self.assertEqual(response.context["payment_count"], 1)
        self.assertEqual(response.context["total_amount"], Decimal("1000.00"))

    def test_filters_method_channel_seller_and_without_seller(self):
        manual = self.booking(seller=self.seller)
        self.approved_payment(manual, PaymentMethod.CASH)
        online = self.booking(channel=BookingChannel.ONLINE, seller=None, confirmed_at=self.local(2026, 9, 16))
        self.approved_payment(online, PaymentMethod.BANK_TRANSFER)
        self.client.force_login(self.admin)
        cases = [({"medio": PaymentMethod.CASH}, 1), ({"canal": BookingChannel.ONLINE}, 1),
                 ({"vendedor": str(self.seller.pk)}, 1), ({"vendedor": "sin-vendedor"}, 1)]
        for params, expected in cases:
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.report_url(), params).context["payment_count"], expected)

    def test_local_date_range_and_configurable_limit(self):
        before = self.booking(seller=self.seller, confirmed_at=self.local(2026, 9, 30, 23))
        self.approved_payment(before, PaymentMethod.CASH)
        after = self.booking(seller=self.seller, confirmed_at=self.local(2026, 10, 1, 0))
        self.approved_payment(after, PaymentMethod.CASH)
        self.client.force_login(self.admin)
        response = self.client.get(self.report_url(), {"desde": "2026-09-30", "hasta": "2026-09-30"})
        self.assertEqual(response.context["payment_count"], 1)
        with override_settings(PANEL_REPORTS_MAX_RANGE_DAYS=2):
            response = self.client.get(self.report_url(), {"desde": "2026-09-01", "hasta": "2026-09-03"})
            self.assertContains(response, "El rango no puede superar 2 días")
            self.assertEqual(response.context["payment_count"], 2)

    def test_pagination_keeps_full_totals(self):
        for index in range(26):
            booking = self.booking(seller=self.seller, confirmed_at=self.local(2026, 9, 1) + timedelta(minutes=index),
                                   passengers=0, legs=0, email=f"p{index}@example.com")
            self.approved_payment(booking, PaymentMethod.CASH)
        self.client.force_login(self.seller)
        first = self.client.get(self.report_url(), {"pagina": 1})
        second = self.client.get(self.report_url(), {"pagina": 2})
        self.assertEqual(first.context["paginator"].count, 26)
        self.assertEqual(second.context["paginator"].count, 26)
        self.assertEqual(first.context["total_amount"], second.context["total_amount"])
        self.assertEqual(len(first.context["page"].object_list), 25)
        self.assertEqual(len(second.context["page"].object_list), 1)

    def test_csv_contract_privacy_formula_safety_and_filters(self):
        booking = self.booking(seller=None, email="no-exponer@example.com")
        payment = self.approved_payment(booking, PaymentMethod.CASH)
        payment.reference = "=NO_DEBE_APARECER"
        payment.voucher = "private/receipt.pdf"
        payment.save(update_fields=["reference", "voucher"])
        self.client.force_login(self.admin)
        response = self.client.get(self.csv_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn('attachment; filename="reporte-ventas.csv"', response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Referencia pública", "Fecha de confirmación", "Estado", "Medio", "Canal", "Importe", "Moneda", "Pasajeros", "Tramos", "Vendedor"])
        body = response.content.decode("utf-8")
        self.assertNotIn("no-exponer@example.com", body)
        self.assertNotIn("NO_DEBE_APARECER", body)
        self.assertNotIn("receipt.pdf", body)
        self.assertTrue(_safe_csv("=1+1").startswith("'"))
        self.assertTrue(_safe_csv("+1").startswith("'"))

    def test_access_is_admin_or_seller_only_and_get_is_read_only(self):
        self.booking(seller=self.seller)
        urls = (self.report_url(), self.csv_url())
        for user in (self.admin, self.seller):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 200)
        for user in (self.common, self.staff):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_get_html_and_csv_do_not_mutate_entities_or_audit(self):
        booking = self.booking(seller=self.seller)
        self.approved_payment(booking, PaymentMethod.CASH)
        counts_before = {model: model.objects.count() for model in (Booking, Payment, BookingPassenger, BookingLeg, SeatAssignment, AuditEvent)}
        self.client.force_login(self.admin)
        self.client.get(self.report_url())
        self.client.get(self.csv_url())
        counts_after = {model: model.objects.count() for model in counts_before}
        self.assertEqual(counts_before, counts_after)

    def test_report_query_count_is_bounded(self):
        booking = self.booking(seller=self.seller)
        self.approved_payment(booking, PaymentMethod.CASH)
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.report_url())
        self.assertLessEqual(len(queries), 20)
        self.assertEqual(response.status_code, 200)
