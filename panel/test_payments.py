"""Pruebas exhaustivas para vistas de pagos en el panel personalizado."""

from datetime import date, timedelta
from decimal import Decimal
import io
from pathlib import Path
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from panel.models import AuditEvent
from payments.models import Payment, PaymentMethod, PaymentStatus
from payments.services import register_cash_payment, register_transfer_payment
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)

User = get_user_model()


class PanelPaymentsTestCase(TestCase):
    """Pruebas exhaustivas del panel de pagos: permisos, vistas, filtros, auditoría y métricas."""

    def setUp(self):
        super().setUp()
        self.temp_protected_media = tempfile.mkdtemp()
        self.temp_public_media = tempfile.mkdtemp()

        self.override_settings = override_settings(
            PROTECTED_MEDIA_ROOT=self.temp_protected_media,
            MEDIA_ROOT=self.temp_public_media,
            PAYMENTS_MAX_VOUCHER_SIZE_BYTES=10 * 1024 * 1024,
            PAYMENTS_ALLOWED_VOUCHER_EXTENSIONS=(".pdf", ".jpg", ".jpeg", ".png"),
        )
        self.override_settings.enable()

        self.client = Client()

        # Grupos de usuarios
        self.admin_group, _ = Group.objects.get_or_create(name="Administrador")
        self.seller_group, _ = Group.objects.get_or_create(name="Vendedor")

        # Usuarios
        self.admin_user = User.objects.create_user(username="admin_p", password="password123")
        self.admin_user.groups.add(self.admin_group)

        self.seller_user = User.objects.create_user(username="seller_p", password="password123")
        self.seller_user.groups.add(self.seller_group)

        self.staff_user = User.objects.create_user(username="staff_only", password="password123", is_staff=True)
        self.common_user = User.objects.create_user(username="common_user", password="password123")
        self.superuser = User.objects.create_superuser(username="super_p", password="password123")

        # Estructura operativa
        self.stop_cba = Stop.objects.create(name="Córdoba Capital", code="CBA")
        self.stop_ssj = Stop.objects.create(name="San Salvador de Jujuy", code="SSJ")

        self.route = Route.objects.create(name="Córdoba - Jujuy", code="CBA-SSJ", is_active=True)
        RouteStop.objects.create(route=self.route, stop=self.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route, stop=self.stop_ssj, sequence=2, allows_boarding=False, allows_alighting=True)

        self.bus = Bus.objects.create(code="BUS-P1", is_active=True)
        self.seat_1 = Seat.objects.create(bus=self.bus, number=1, category=SeatCategory.CAMA, deck=Seat.Deck.LOWER, position_x=1, position_y=1, is_active=True)
        self.seat_2 = Seat.objects.create(bus=self.bus, number=2, category=SeatCategory.SEMI_CAMA, deck=Seat.Deck.UPPER, position_x=1, position_y=2, is_active=True)
        self.extra_seats = [
            Seat.objects.create(bus=self.bus, number=number, category=SeatCategory.SEMI_CAMA, deck=Seat.Deck.UPPER, position_x=number, position_y=3, is_active=True)
            for number in range(3, 8)
        ]


        now = timezone.now()
        dep = now + timedelta(days=2)
        arr = dep + timedelta(hours=10)

        self.trip = Trip.objects.create(route=self.route, bus=self.bus, departure_at=dep, status=Trip.Status.SCHEDULED)
        self.ts_cba = TripStop.objects.create(trip=self.trip, stop=self.stop_cba, sequence=1, scheduled_at=dep, allows_boarding=True, allows_alighting=False)
        self.ts_ssj = TripStop.objects.create(trip=self.trip, stop=self.stop_ssj, sequence=2, scheduled_at=arr, allows_boarding=False, allows_alighting=True)

        self.fare_cama = TripFare.objects.create(trip=self.trip, origin_stop=self.ts_cba, destination_stop=self.ts_ssj, seat_category=SeatCategory.CAMA, amount=Decimal("12000.00"), is_active=True)
        self.fare_semi = TripFare.objects.create(trip=self.trip, origin_stop=self.ts_cba, destination_stop=self.ts_ssj, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("8000.00"), is_active=True)


    def tearDown(self):
        self.override_settings.disable()
        shutil.rmtree(self.temp_protected_media, ignore_errors=True)
        shutil.rmtree(self.temp_public_media, ignore_errors=True)
        super().tearDown()

    def create_booking(self, email="test@correo.com", status=BookingStatus.HELD, seats=None, hours_held=24):
        now = timezone.now()
        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            status=status,
            email=email,
            phone="3519998888",
            seller=self.seller_user,
            expires_at=now + timedelta(hours=hours_held),
            confirmed_at=now if status == BookingStatus.CONFIRMED else None,
        )
        leg = BookingLeg.objects.create(
            booking=booking,
            sequence=1,
            trip=self.trip,
            origin_stop=self.ts_cba,
            destination_stop=self.ts_ssj,
            origin_stop_name=self.stop_cba.name,
            destination_stop_name=self.stop_ssj.name,
            departure_at=self.ts_cba.scheduled_at,
            arrival_at=self.ts_ssj.scheduled_at,
        )

        if seats is None:
            used_seat_ids = SeatAssignment.objects.filter(trip=self.trip).values_list("seat_id", flat=True)
            selected_seats = list(
                Seat.objects.filter(bus=self.bus).exclude(pk__in=used_seat_ids).order_by("number")[:1]
            )
            if not selected_seats:
                raise AssertionError("Fixture agotó las butacas disponibles")
        else:
            selected_seats = seats
        for idx, seat in enumerate(selected_seats, start=1):
            passenger = BookingPassenger.objects.create(
                booking=booking,
                position=idx,
                first_name="Carlos",
                last_name="Tevez",
                document_type="DNI",
                document_number="28123456",
            )
            price = self.fare_cama.amount if seat.category == SeatCategory.CAMA else self.fare_semi.amount
            assign_status = AssignmentStatus.CONFIRMED if status == BookingStatus.CONFIRMED else AssignmentStatus.HELD
            SeatAssignment.objects.create(
                leg=leg,
                passenger=passenger,
                trip=self.trip,
                seat=seat,
                status=assign_status,
                seat_number=seat.number,
                category=seat.category,
                price=price,
                currency="ARS",
            )
        return booking

    def sample_voucher(self, name="comprobante.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4 test voucher content", content_type="application/pdf")

    # --- Permisos y Control de Acceso ---

    def test_payment_views_permissions(self):
        """Verifica que solo Administrador, Vendedor o superusuario puedan acceder;
        usuarios comunes o staff sin rol reciben 403 y anónimos son redirigidos a login."""
        booking = self.create_booking()
        p = register_transfer_payment(booking_or_id=booking, seller=self.seller_user, voucher=self.sample_voucher())

        urls = [
            reverse("panel:payment_list"),
            reverse("panel:pending_transfers"),
            reverse("panel:payment_detail", kwargs={"public_id": p.public_id}),
            reverse("panel:payment_voucher", kwargs={"public_id": p.public_id}),
        ]

        for url in urls:
            # 1. Anónimo -> redirect a login
            self.client.logout()
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)
            self.assertIn(reverse("panel:login"), resp.url)

            # 2. Usuario común autenticado -> 403 Forbidden
            self.client.force_login(self.common_user)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 403)

            # 3. Staff sin rol en Administrador o Vendedor -> 403 Forbidden
            self.client.force_login(self.staff_user)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 403)

            # 4. Vendedor -> 200 OK
            self.client.force_login(self.seller_user)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)

            # 5. Administrador -> 200 OK
            self.client.force_login(self.admin_user)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)

            # 6. Superusuario -> 200 OK
            self.client.force_login(self.superuser)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)

    # --- Acciones en Detalle de Reserva ---

    def test_booking_detail_held_shows_payment_actions_and_total(self):
        """Para reserva HELD, el detalle muestra el total y las acciones de pago en efectivo y transferencia."""
        self.client.force_login(self.seller_user)
        booking = self.create_booking()  # 12000.00
        url = reverse("panel:booking_detail", kwargs={"public_id": booking.public_id})

        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cobro y confirmación económica")
        self.assertContains(resp, "$ 12000.00")
        self.assertContains(resp, "Cobro en Efectivo")
        self.assertContains(resp, "Transferencia Bancaria")
        self.assertContains(resp, reverse("panel:booking_pay_cash", kwargs={"public_id": booking.public_id}))
        self.assertContains(resp, reverse("panel:booking_pay_transfer", kwargs={"public_id": booking.public_id}))

    def test_booking_detail_no_actions_on_confirmed_expired_released(self):
        """No se muestran acciones de pago en reservas EXPIRED, RELEASED o CONFIRMED."""
        self.client.force_login(self.seller_user)

        for status in [BookingStatus.CONFIRMED, BookingStatus.EXPIRED, BookingStatus.RELEASED]:
            b = self.create_booking(email=f"status_{status}@correo.com", status=status)
            url = reverse("panel:booking_detail", kwargs={"public_id": b.public_id})
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertNotContains(resp, "Confirmar cobro en efectivo")
            self.assertNotContains(resp, "Registrar transferencia")

    def test_booking_detail_with_pending_transfer_shows_review_notice(self):
        """Cuando una reserva tiene transferencia en revisión, no muestra el formulario de pago
        y muestra el aviso con enlace para revisar la transferencia."""
        self.client.force_login(self.seller_user)
        booking = self.create_booking()
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_user,
            voucher=self.sample_voucher(),
        )

        url = reverse("panel:booking_detail", kwargs={"public_id": booking.public_id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Transferencia en revisión")
        self.assertContains(resp, reverse("panel:payment_detail", kwargs={"public_id": payment.public_id}))
        self.assertNotContains(resp, "Confirmar cobro en efectivo")

    # --- Resistencia a Manipulación de Importes ---

    def test_cash_payment_ignores_tampered_amount_in_form(self):
        """El servidor calcula el importe total a partir de SeatAssignment; cualquier 'amount'
        falsificado enviado por POST es ignorado."""
        self.client.force_login(self.seller_user)
        booking = self.create_booking()  # Total real: 12000.00
        url = reverse("panel:booking_pay_cash", kwargs={"public_id": booking.public_id})

        # Enviamos un importe falso manipulado (ej. 1.00)
        resp = self.client.post(url, {
            "amount": "1.00",
            "reference": "Intento de pagar $1",
        })

        self.assertEqual(resp.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.amount, Decimal("12000.00"))
        self.assertNotEqual(payment.amount, Decimal("1.00"))

    def test_transfer_payment_ignores_tampered_amount_in_form(self):
        """En transferencias bancarias, cualquier importe enviado en POST es ignorado;
        se cobra el total server-side exacto."""
        self.client.force_login(self.seller_user)
        booking = self.create_booking()  # Total real: 12000.00
        url = reverse("panel:booking_pay_transfer", kwargs={"public_id": booking.public_id})

        resp = self.client.post(url, {
            "amount": "50.00",
            "voucher": self.sample_voucher(),
            "reference": "Transf con monto manipulado",
        })

        self.assertEqual(resp.status_code, 302)
        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.amount, Decimal("12000.00"))

    # --- Listado y Búsqueda de Pagos ---

    def test_payment_list_filters_and_search(self):
        self.client.force_login(self.seller_user)

        # Crear dos pagos: uno en efectivo y uno transferencia aprobada
        b1 = self.create_booking(email="carlos@correo.com")
        p1 = register_cash_payment(booking_or_id=b1, seller=self.seller_user)

        b2 = self.create_booking(email="ana@correo.com", seats=[self.seat_2])
        p2 = register_transfer_payment(booking_or_id=b2, seller=self.seller_user, voucher=self.sample_voucher())

        list_url = reverse("panel:payment_list")

        # 1. Filtro por método CASH
        resp = self.client.get(list_url, {"method": "CASH"})
        self.assertContains(resp, str(p1.public_id)[:8])
        self.assertNotContains(resp, str(p2.public_id)[:8])

        # 2. Filtro por estado UNDER_REVIEW
        resp = self.client.get(list_url, {"status": "UNDER_REVIEW"})
        self.assertContains(resp, str(p2.public_id)[:8])
        self.assertNotContains(resp, str(p1.public_id)[:8])

        # 3. Búsqueda por correo de reserva
        resp = self.client.get(list_url, {"q": "carlos@correo.com"})
        self.assertContains(resp, str(p1.public_id)[:8])
        self.assertNotContains(resp, str(p2.public_id)[:8])

        # 4. Búsqueda por documento normalizado
        resp = self.client.get(list_url, {"q": "28.123.456"})
        self.assertContains(resp, str(p1.public_id)[:8])

    # --- Transferencias Pendientes, Aprobación y Rechazo ---

    def test_pending_transfers_approve_flow(self):
        self.client.force_login(self.seller_user)
        booking = self.create_booking()
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_user,
            voucher=self.sample_voucher(),
        )

        pending_url = reverse("panel:pending_transfers")
        resp = self.client.get(pending_url)
        self.assertContains(resp, str(payment.public_id))

        # Acción POST para aprobar
        review_url = reverse("panel:transfer_review", kwargs={"public_id": payment.public_id})
        resp_post = self.client.post(review_url, {
            "action": "approve",
            "next": "pending",
        })
        self.assertEqual(resp_post.status_code, 302)
        self.assertRedirects(resp_post, pending_url)

        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(payment.reviewed_by, self.seller_user)

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

    def test_pending_transfers_reject_requires_reason(self):
        self.client.force_login(self.admin_user)
        booking = self.create_booking()
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_user,
            voucher=self.sample_voucher(),
        )

        review_url = reverse("panel:transfer_review", kwargs={"public_id": payment.public_id})

        # 1. Rechazo sin motivo -> falla
        resp_empty = self.client.post(review_url, {
            "action": "reject",
            "rejection_reason": "   ",
        })
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.UNDER_REVIEW)

        # 2. Rechazo con motivo -> éxito
        resp_ok = self.client.post(review_url, {
            "action": "reject",
            "rejection_reason": "Comprobante falso o ilegible",
            "next": "pending",
        })
        self.assertEqual(resp_ok.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.REJECTED)
        self.assertEqual(payment.rejection_reason, "Comprobante falso o ilegible")
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.HELD)

    # --- Descarga de Comprobante ---

    def test_download_voucher_as_attachment(self):
        self.client.force_login(self.seller_user)
        booking = self.create_booking()
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_user,
            voucher=self.sample_voucher("comprobante_oficial.pdf"),
        )

        dl_url = reverse("panel:payment_voucher", kwargs={"public_id": payment.public_id})
        resp = self.client.get(dl_url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
        self.assertIn(f"comprobante_{payment.public_id}.pdf", resp.headers.get("Content-Disposition", ""))

    # --- Métricas del Dashboard ---

    def test_dashboard_metrics_counts_and_amounts(self):
        """Verifica métricas: ventas confirmadas, ingresos confirmados en ARS con desglose CASH/BANK_TRANSFER,
        sin contar pagos en revisión ni rechazados."""
        self.client.force_login(self.seller_user)

        # 1. Pago en efectivo aprobado (12000)
        b1 = self.create_booking(email="cash@correo.com")
        register_cash_payment(booking_or_id=b1, seller=self.seller_user)

        # 2. Pago transferencia aprobado (8000)
        b2 = self.create_booking(email="transf_app@correo.com", seats=[self.seat_2])
        p2 = register_transfer_payment(booking_or_id=b2, seller=self.seller_user, voucher=self.sample_voucher())
        from payments.services import review_transfer_payment
        review_transfer_payment(payment_or_id=p2, reviewer=self.admin_user, approved=True)

        # 3. Pago transferencia en revisión (12000) -> NO DEBE CONTAR EN INGRESOS NI VENTAS CONFIRMADAS
        b3 = self.create_booking(email="transf_pend@correo.com")
        register_transfer_payment(booking_or_id=b3, seller=self.seller_user, voucher=self.sample_voucher())

        # 4. Pago transferencia rechazada (12000) -> NO DEBE CONTAR
        b4 = self.create_booking(email="transf_rej@correo.com")
        p4 = register_transfer_payment(booking_or_id=b4, seller=self.seller_user, voucher=self.sample_voucher())
        review_transfer_payment(payment_or_id=p4, reviewer=self.admin_user, approved=False, rejection_reason="Ilegible")

        dash_url = reverse("panel:dashboard")
        resp = self.client.get(dash_url)
        self.assertEqual(resp.status_code, 200)

        # Total ventas confirmadas: 2 (b1 y b2)
        self.assertEqual(resp.context["confirmed_sales_count"], 2)
        # Total ingresos confirmados: 12000 + 8000 = 20000.00
        self.assertEqual(resp.context["confirmed_revenue_total"], Decimal("20000.00"))

        # Desglose Efectivo: 1 venta, $ 12000.00
        self.assertEqual(resp.context["cash_sales_count"], 1)
        self.assertEqual(resp.context["cash_revenue"], Decimal("12000.00"))

        # Desglose Transferencia: 1 venta, $ 8000.00
        self.assertEqual(resp.context["transfer_sales_count"], 1)
        self.assertEqual(resp.context["transfer_revenue"], Decimal("8000.00"))

        # Transferencias en revisión: 1 (b3)
        self.assertEqual(resp.context["pending_transfers_count"], 1)

        # Verificar renderizado en HTML
        self.assertContains(resp, "Ventas Confirmadas")
        self.assertContains(resp, "2")
        self.assertContains(resp, "$ 20000.00")
        self.assertContains(resp, "Efectivo: $12000.00 (1)")
        self.assertContains(resp, "Transferencias: $8000.00 (1)")
