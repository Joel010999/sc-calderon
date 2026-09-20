from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import shutil
import tempfile
import uuid
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
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

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class PublicTransferViewsTestCase(TestCase):
    """Pruebas de integración del flujo público de pago por transferencia bancaria."""

    def setUp(self):
        super().setUp()
        self.temp_protected_media = tempfile.mkdtemp()
        self.temp_public_media = tempfile.mkdtemp()
        self.settings_override = override_settings(
            PROTECTED_MEDIA_ROOT=Path(self.temp_protected_media),
            MEDIA_ROOT=Path(self.temp_public_media),
        )
        self.settings_override.enable()

        self.client = Client()
        self.now = timezone.now()
        self.today_ar = timezone.localtime(self.now, AR_TZ).date()
        self.future_date = self.today_ar + timedelta(days=2)

        # 1. Paradas y recorrido
        self.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        self.stop_ssj = Stop.objects.create(code="SSJ", name="San Salvador de Jujuy", city="Jujuy", province="Jujuy")

        self.route = Route.objects.create(code="CBA-SSJ", name="Córdoba → Jujuy")
        RouteStop.objects.create(route=self.route, stop=self.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route, stop=self.stop_ssj, sequence=2, allows_boarding=False, allows_alighting=True)

        # 2. Colectivo y butacas
        self.bus = Bus.objects.create(code="BUS-01", display_name="Scania Starlink")
        self.seat_cama = Seat.objects.create(
            bus=self.bus, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0
        )
        self.seat_semi = Seat.objects.create(
            bus=self.bus, number=2, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0
        )

        # 3. Viaje y paradas
        dep_time = timezone.make_aware(
            timezone.datetime.combine(self.future_date, timezone.datetime.min.time().replace(hour=20)),
            AR_TZ,
        )
        arr_time = dep_time + timedelta(hours=12)

        self.trip = Trip.objects.create(
            route=self.route,
            bus=self.bus,
            departure_at=dep_time,
            status=Trip.Status.SCHEDULED,
        )
        self.ts_orig = TripStop.objects.create(
            trip=self.trip, stop=self.stop_cba, sequence=1, scheduled_at=dep_time, allows_boarding=True, allows_alighting=False
        )
        self.ts_dest = TripStop.objects.create(
            trip=self.trip, stop=self.stop_ssj, sequence=2, scheduled_at=arr_time, allows_boarding=False, allows_alighting=True
        )

        # 4. Tarifas
        self.fare_cama = TripFare.objects.create(
            trip=self.trip,
            origin_stop=self.ts_orig,
            destination_stop=self.ts_dest,
            seat_category=SeatCategory.CAMA,
            amount=Decimal("15000.00"),
            is_active=True,
        )
        self.fare_semi = TripFare.objects.create(
            trip=self.trip,
            origin_stop=self.ts_orig,
            destination_stop=self.ts_dest,
            seat_category=SeatCategory.SEMI_CAMA,
            amount=Decimal("10000.00"),
            is_active=True,
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.temp_protected_media, ignore_errors=True)
        shutil.rmtree(self.temp_public_media, ignore_errors=True)
        super().tearDown()

    def create_held_booking(self, minutes_held=15):
        """Crea una reserva online HELD para pruebas con token en sesión."""
        now = timezone.now()
        booking = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email="viajero@ejemplo.com",
            phone="3519876543",
            seller=None,
            expires_at=now + timedelta(minutes=minutes_held),
        )
        leg = BookingLeg.objects.create(
            booking=booking,
            sequence=1,
            trip=self.trip,
            origin_stop=self.ts_orig,
            destination_stop=self.ts_dest,
            origin_stop_name=self.stop_cba.name,
            destination_stop_name=self.stop_ssj.name,
            departure_at=self.ts_orig.scheduled_at,
            arrival_at=self.ts_dest.scheduled_at,
        )
        p = BookingPassenger.objects.create(
            booking=booking,
            position=1,
            first_name="Carlos",
            last_name="Gomez",
            document_type="DNI",
            document_number="28123456",
            birth_date=date(1980, 5, 20),
            nationality="Argentina",
        )
        SeatAssignment.objects.create(
            leg=leg,
            passenger=p,
            trip=self.trip,
            seat=self.seat_cama,
            status=AssignmentStatus.HELD,
            seat_number=1,
            category=SeatCategory.CAMA,
            price=Decimal("15000.00"),
            currency="ARS",
        )
        # Establecer sesión autorizada para IDOR
        session = self.client.session
        token = uuid.uuid4().hex
        session[f"booking_access_{booking.public_id}"] = token
        session.save()
        return booking

    def sample_pdf_voucher(self):
        return SimpleUploadedFile("comprobante.pdf", b"%PDF-1.4 test voucher content", content_type="application/pdf")

    # --- PRUEBAS DE ACCESO Y SEGURIDAD (IDOR) ---

    def test_payment_views_require_session_token(self):
        booking = self.create_held_booking()
        # Cliente sin sesión
        unauth_client = Client()

        url_pago = reverse("pago_pendiente", kwargs={"public_id": booking.public_id})
        url_iniciar = reverse("iniciar_transferencia", kwargs={"public_id": booking.public_id})
        url_pantalla = reverse("pantalla_transferencia", kwargs={"public_id": booking.public_id})
        url_subir = reverse("subir_comprobante", kwargs={"public_id": booking.public_id})

        self.assertEqual(unauth_client.get(url_pago).status_code, 403)
        self.assertEqual(unauth_client.post(url_iniciar).status_code, 403)
        self.assertEqual(unauth_client.get(url_pantalla).status_code, 403)
        self.assertEqual(unauth_client.post(url_subir).status_code, 403)

    # --- PANTALLA PAGO PENDIENTE (SELECCIÓN DE MEDIOS) ---

    def test_payment_pending_shows_transfer_and_no_cash(self):
        booking = self.create_held_booking()
        url = reverse("pago_pendiente", kwargs={"public_id": booking.public_id})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Transferencia bancaria")
        self.assertContains(resp, "Pagar con Transferencia")
        self.assertContains(resp, "5 minutos")
        # Efectivo NO debe aparecer en el checkout público
        self.assertNotContains(resp, "Efectivo")
        # Pasarelas automáticas deben aparecer deshabilitadas / próximamente
        self.assertContains(resp, "Mercado Pago QR")
        self.assertContains(resp, "Payway")
        self.assertContains(resp, "Próximamente disponible")

    def test_payment_pending_redirects_if_active_transfer_exists(self):
        booking = self.create_held_booking()
        # Crear pago activo AWAITING_VOUCHER
        Payment.objects.create(
            booking=booking,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.AWAITING_VOUCHER,
            amount=Decimal("15000.00"),
            currency="ARS",
            proof_deadline_at=timezone.now() + timedelta(minutes=5),
        )

        url = reverse("pago_pendiente", kwargs={"public_id": booking.public_id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("pantalla_transferencia", kwargs={"public_id": booking.public_id}))

    # --- INICIO DE TRANSFERENCIA (POST IDEMPOTENTE) ---

    def test_iniciar_transferencia_idempotent_flow(self):
        booking = self.create_held_booking()
        url = reverse("iniciar_transferencia", kwargs={"public_id": booking.public_id})

        # Primer POST
        resp1 = self.client.post(url)
        self.assertEqual(resp1.status_code, 302)
        self.assertRedirects(resp1, reverse("pantalla_transferencia", kwargs={"public_id": booking.public_id}))

        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.status, PaymentStatus.AWAITING_VOUCHER)
        self.assertEqual(payment.amount, Decimal("15000.00"))

        # Segundo POST (idempotente)
        resp2 = self.client.post(url)
        self.assertEqual(resp2.status_code, 302)
        self.assertRedirects(resp2, reverse("pantalla_transferencia", kwargs={"public_id": booking.public_id}))

        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)

    def test_iniciar_transferencia_rate_limiting(self):
        booking = self.create_held_booking()
        url = reverse("iniciar_transferencia", kwargs={"public_id": booking.public_id})

        # Realizar 5 POSTs
        for _ in range(5):
            self.client.post(url)

        # El 6to POST excede el límite
        resp6 = self.client.post(url, follow=True)
        self.assertContains(resp6, "límite de solicitudes")

    # --- PANTALLA TRANSFERENCIA Y SUBIDA DE COMPROBANTE ---

    def test_pantalla_transferencia_displays_bank_data_and_countdown(self):
        booking = self.create_held_booking()
        # Iniciar transferencia
        self.client.post(reverse("iniciar_transferencia", kwargs={"public_id": booking.public_id}))

        url = reverse("pantalla_transferencia", kwargs={"public_id": booking.public_id})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mercado Pago")
        self.assertContains(resp, "scviajes.mp")
        self.assertContains(resp, "$15000.00 ARS")
        self.assertContains(resp, "Tiempo restante para transferir y subir comprobante")
        self.assertContains(resp, "Adjuntar comprobante de transferencia")

    def test_subir_comprobante_success_and_24h_extension(self):
        booking = self.create_held_booking()
        self.client.post(reverse("iniciar_transferencia", kwargs={"public_id": booking.public_id}))

        url = reverse("subir_comprobante", kwargs={"public_id": booking.public_id})
        voucher = self.sample_pdf_voucher()

        resp = self.client.post(url, {"voucher": voucher}, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Comprobante cargado correctamente")
        self.assertContains(resp, "revisión manual (plazo de 24 horas)")

        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.status, PaymentStatus.UNDER_REVIEW)
        self.assertTrue(payment.voucher)

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertEqual(booking.expires_at, payment.review_deadline_at)

    def test_subir_comprobante_rejects_corrupted_file(self):
        booking = self.create_held_booking()
        self.client.post(reverse("iniciar_transferencia", kwargs={"public_id": booking.public_id}))

        url = reverse("subir_comprobante", kwargs={"public_id": booking.public_id})
        bad_file = SimpleUploadedFile("comprobante.pdf", b"archivo corrupto no es pdf", content_type="application/pdf")

        resp = self.client.post(url, {"voucher": bad_file}, follow=True)

        self.assertContains(resp, "contenido del archivo no coincide")
        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.status, PaymentStatus.AWAITING_VOUCHER)

    # --- EXPIRACIÓN OPORTUNISTA EN VISTAS ---

    def test_pantalla_transferencia_opportunistic_expiration_after_5_minutes(self):
        booking = self.create_held_booking()
        self.client.post(reverse("iniciar_transferencia", kwargs={"public_id": booking.public_id}))

        payment = Payment.objects.get(booking=booking)
        # Simular que el plazo de 5 minutos ya venció
        past = timezone.now() - timedelta(minutes=6)
        payment.proof_deadline_at = past
        payment.save(update_fields=["proof_deadline_at"])
        booking.expires_at = past
        booking.save(update_fields=["expires_at"])

        url = reverse("pantalla_transferencia", kwargs={"public_id": booking.public_id})
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Plazo de transferencia expirado")
        self.assertContains(resp, "butacas asociadas fueron liberadas")

        booking.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        self.assertEqual(payment.status, PaymentStatus.EXPIRED)
        sa = SeatAssignment.objects.get(leg__booking=booking)
        self.assertEqual(sa.status, AssignmentStatus.RELEASED)

    # --- ESTADO EN PÁGINA DE RESUMEN ---

    def test_summary_reflects_transfer_status(self):
        booking = self.create_held_booking()
        summary_url = reverse("resumen_reserva", kwargs={"public_id": booking.public_id})

        # 1. Sin pago iniciado -> Continuar al pago
        resp1 = self.client.get(summary_url)
        self.assertContains(resp1, "Continuar al pago")

        # 2. Pago en AWAITING_VOUCHER -> Subir comprobante de transferencia
        self.client.post(reverse("iniciar_transferencia", kwargs={"public_id": booking.public_id}))
        resp2 = self.client.get(summary_url)
        self.assertContains(resp2, "Subir comprobante de transferencia")

        # 3. Pago en UNDER_REVIEW -> Ver estado de transferencia (En revisión)
        self.client.post(
            reverse("subir_comprobante", kwargs={"public_id": booking.public_id}),
            {"voucher": self.sample_pdf_voucher()},
        )
        resp3 = self.client.get(summary_url)
        self.assertContains(resp3, "Ver estado de transferencia (En revisión)")
