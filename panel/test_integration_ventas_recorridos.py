"""Pruebas de integración completa para flujos y navegación de ventas internas y recorridos versionados.

Valida:
1. Conexión de panel/internal_sales_urls.py sin duplicar rutas ni ocultar endpoints.
2. Accesos por rol: Administrador/superusuario a reserva manual y venta interna; Vendedor a venta interna.
3. Vendedor no puede crear reserva manual sin vencimiento (expires_at = None).
4. Vencimiento de venta interna: min(medianoche AR al final del día de compra, 1h antes de salida inicial).
5. Venta en la última hora: comprobante obligatorio y pago verificado para confirmación inmediata.
6. Autorización estricta antes de retornos idempotentes en revisión de transferencias.
7. Preservación del canal ONLINE (hold de 15 minutos).
8. Versionado de recorridos al editar rutas con viajes asociados.
9. Protección de paradas e históricos de viajes.
10. Navegación de recorridos dentro de Viajes.
"""

from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
import io
import shutil
import tempfile
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from panel.internal_sales_permissions import can_create_manual_unexpired_reservation
from panel.reservation_services import create_panel_manual_booking
from operations.services import save_route_configuration
from payments.models import Payment, PaymentMethod, PaymentStatus
from payments.services import register_transfer_payment, review_transfer_payment
from sales.internal_transfer import (
    calculate_internal_transfer_expiration,
    create_immediate_transfer_sale,
    create_internal_transfer_booking,
)
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)
from sales.services import create_online_booking

User = get_user_model()
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class IntegrationVentasRecorridosTests(TestCase):
    """Pruebas de integración exhaustivas HTTP y de dominio."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_group = Group.objects.create(name="Administrador")
        cls.seller_group = Group.objects.create(name="Vendedor")

        cls.admin_user = User.objects.create_user(username="admin_integ", password="password123")
        cls.admin_user.groups.add(cls.admin_group)

        cls.seller_user = User.objects.create_user(username="seller_integ", password="password123")
        cls.seller_user.groups.add(cls.seller_group)

        cls.other_seller = User.objects.create_user(username="other_seller_integ", password="password123")
        cls.other_seller.groups.add(cls.seller_group)

        cls.super_user = User.objects.create_superuser(
            username="super_integ", password="password123", email="super@integ.com"
        )

        # Paradas
        cls.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        cls.stop_jma = Stop.objects.create(code="JMA", name="Jesús María", city="Jesús María", province="Córdoba")
        cls.stop_ssj = Stop.objects.create(code="SSJ", name="San Salvador de Jujuy", city="San Salvador de Jujuy", province="Jujuy")

        # Recorrido v1
        cls.route = Route.objects.create(code="CBA-SSJ", name="Córdoba a Jujuy", is_active=True, version=1)
        cls.rs1 = RouteStop.objects.create(route=cls.route, stop=cls.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        cls.rs2 = RouteStop.objects.create(route=cls.route, stop=cls.stop_jma, sequence=2, allows_boarding=True, allows_alighting=False)
        cls.rs3 = RouteStop.objects.create(route=cls.route, stop=cls.stop_ssj, sequence=3, allows_boarding=False, allows_alighting=True)

        # Colectivo y butacas
        cls.bus = Bus.objects.create(code="BUS-INT-1", display_name="Bus Integración")
        cls.seat_1 = Seat.objects.create(bus=cls.bus, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)
        cls.seat_2 = Seat.objects.create(bus=cls.bus, number=2, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=1, position_y=0)
        cls.seat_3 = Seat.objects.create(bus=cls.bus, number=3, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
        cls.seat_4 = Seat.objects.create(bus=cls.bus, number=4, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0)

        # Viaje programado para dentro de 3 días (para probar medianoche AR como vencimiento)
        now = timezone.now()
        cls.dep_future = now + timedelta(days=3)
        cls.trip_future = Trip.objects.create(
            route=cls.route, bus=cls.bus, departure_at=cls.dep_future, status=Trip.Status.SCHEDULED
        )
        cls.ts_cba = TripStop.objects.create(trip=cls.trip_future, stop=cls.stop_cba, sequence=1, scheduled_at=cls.dep_future, allows_boarding=True, allows_alighting=False)
        cls.ts_ssj = TripStop.objects.create(trip=cls.trip_future, stop=cls.stop_ssj, sequence=2, scheduled_at=cls.dep_future + timedelta(hours=10), allows_boarding=False, allows_alighting=True)

        TripFare.objects.create(trip=cls.trip_future, seat_category=SeatCategory.CAMA, amount=Decimal("15000.00"), is_active=True)
        TripFare.objects.create(trip=cls.trip_future, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("10000.00"), is_active=True)

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.temp_protected = tempfile.mkdtemp()
        self.override_media = override_settings(
            MEDIA_ROOT=self.temp_media,
            PROTECTED_MEDIA_ROOT=self.temp_protected,
            PAYMENTS_MAX_VOUCHER_SIZE_BYTES=10 * 1024 * 1024,
            PAYMENTS_ALLOWED_VOUCHER_EXTENSIONS=(".pdf", ".jpg", ".jpeg", ".png"),
        )
        self.override_media.enable()
        self.client = Client()

    def tearDown(self):
        self.override_media.disable()
        shutil.rmtree(self.temp_media, ignore_errors=True)
        shutil.rmtree(self.temp_protected, ignore_errors=True)

    def sample_voucher(self, filename="comprobante.pdf"):
        return SimpleUploadedFile(filename, b"%PDF-1.4 sample content", content_type="application/pdf")

    # 1. Rutas internal_sales conectadas y accesibles
    def test_internal_sales_urls_connected_and_accessible(self):
        """Verifica que panel/internal_sales_urls.py esté conectado sin duplicar ni ocultar rutas existentes."""
        self.client.force_login(self.admin_user)

        # Rutas con namespace internal_sales dentro de panel
        url_pending = reverse("panel:internal_sales:pending_transfers")
        self.assertEqual(url_pending, "/panel/internal-sales/transferencias/pendientes/")
        resp = self.client.get(url_pending)
        self.assertEqual(resp.status_code, 200)

        # Rutas existentes de pagos intactas
        url_existing_pending = reverse("panel:pending_transfers")
        self.assertEqual(url_existing_pending, "/panel/pagos/transferencias-pendientes/")
        resp_existing = self.client.get(url_existing_pending)
        self.assertEqual(resp_existing.status_code, 200)

        # Crear un pago para probar revisar y comprobante en ambos endpoints
        booking = create_internal_transfer_booking(
            seller=self.seller_user,
            email="cliente_urls@test.com",
            legs=[{"trip": self.trip_future, "origin_stop": self.ts_cba, "destination_stop": self.ts_ssj, "seats": [self.seat_1]}],
            passengers_data=[{"first_name": "Juan", "last_name": "Perez", "document_type": "DNI", "document_number": "30111222", "birth_date": "1990-01-01", "nationality": "Argentina"}],
            voucher=self.sample_voucher(),
        )
        payment = booking.payments.first()

        url_review_ns = reverse("panel:internal_sales:transfer_review", kwargs={"public_id": payment.public_id})
        url_voucher_ns = reverse("panel:internal_sales:voucher_download", kwargs={"public_id": payment.public_id})

        self.assertEqual(url_review_ns, f"/panel/internal-sales/transferencias/{payment.public_id}/revisar/")
        self.assertEqual(url_voucher_ns, f"/panel/internal-sales/transferencias/{payment.public_id}/comprobante/")

        self.assertEqual(self.client.post(url_review_ns, {"action": "approve"}).status_code, 302)
        self.assertEqual(self.client.get(url_voucher_ns).status_code, 200)

    # 2. Administrador crea reserva manual sin vencimiento via HTTP
    def test_admin_creates_manual_unexpired_reservation_via_http(self):
        """El administrador crea una reserva manual sin vencimiento (expires_at is None)."""
        self.client.force_login(self.admin_user)
        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "sale_type": "manual",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_future.pk),
            "outbound_origin": str(self.ts_cba.pk),
            "outbound_destination": str(self.ts_ssj.pk),
            "outbound_seats": [str(self.seat_1.pk)],
            "contact_email": "admin_manual@test.com",
            "p_1_first_name": "Carlos",
            "p_1_last_name": "Gomez",
            "p_1_document_type": "DNI",
            "p_1_document_number": "28111222",
            "p_1_birth_date": "1988-05-10",
            "p_1_nationality": "Argentina",
        }
        resp = self.client.post(create_url, post_data, secure=True)
        self.assertEqual(resp.status_code, 302)

        booking = Booking.objects.get(email="admin_manual@test.com")
        self.assertEqual(booking.channel, BookingChannel.MANUAL)
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertIsNone(booking.expires_at)
        self.assertEqual(booking.seller, self.admin_user)

    # 3. Administrador crea venta interna con vencimiento temporal via HTTP
    def test_admin_creates_internal_sale_transfer_via_http(self):
        """El administrador crea una venta interna por transferencia con vencimiento temporal."""
        self.client.force_login(self.admin_user)
        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "sale_type": "transfer",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_future.pk),
            "outbound_origin": str(self.ts_cba.pk),
            "outbound_destination": str(self.ts_ssj.pk),
            "outbound_seats": [str(self.seat_2.pk)],
            "contact_email": "admin_transfer@test.com",
            "p_1_first_name": "Ana",
            "p_1_last_name": "Lopez",
            "p_1_document_type": "DNI",
            "p_1_document_number": "33444555",
            "p_1_birth_date": "1995-03-20",
            "p_1_nationality": "Argentina",
        }
        resp = self.client.post(create_url, post_data, secure=True)
        self.assertEqual(resp.status_code, 302)

        booking = Booking.objects.get(email="admin_transfer@test.com")
        self.assertEqual(booking.channel, BookingChannel.MANUAL)
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertIsNotNone(booking.expires_at)

        # Vencimiento debe ser la medianoche AR del día de compra
        now_ar = timezone.now().astimezone(AR_TZ)
        expected_midnight = datetime.combine(now_ar.date() + timedelta(days=1), time(0, 0, 0), tzinfo=AR_TZ)
        self.assertEqual(booking.expires_at, expected_midnight)

    # 4. Vendedor crea venta interna y nunca reserva sin vencimiento
    def test_seller_creates_internal_sale_and_never_unexpired_via_http(self):
        """El vendedor crea venta interna y su reserva siempre recibe expires_at."""
        self.client.force_login(self.seller_user)
        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "sale_type": "manual",  # Intento de forzar manual sin vencimiento
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_future.pk),
            "outbound_origin": str(self.ts_cba.pk),
            "outbound_destination": str(self.ts_ssj.pk),
            "outbound_seats": [str(self.seat_3.pk)],
            "contact_email": "seller_transfer@test.com",
            "p_1_first_name": "Mario",
            "p_1_last_name": "Rossi",
            "p_1_document_type": "DNI",
            "p_1_document_number": "36777888",
            "p_1_birth_date": "1992-11-15",
            "p_1_nationality": "Argentina",
        }
        resp = self.client.post(create_url, post_data, secure=True)
        self.assertEqual(resp.status_code, 302)

        booking = Booking.objects.get(email="seller_transfer@test.com")
        self.assertEqual(booking.channel, BookingChannel.MANUAL)
        self.assertEqual(booking.status, BookingStatus.HELD)
        # El vendedor nunca obtiene expires_at = None
        self.assertIsNotNone(booking.expires_at)
        now_ar = timezone.now().astimezone(AR_TZ)
        expected_midnight = datetime.combine(now_ar.date() + timedelta(days=1), time(0, 0, 0), tzinfo=AR_TZ)
        self.assertEqual(booking.expires_at, expected_midnight)

    # 5. Vendedor rechazado al intentar crear reserva sin vencimiento por servicio
    def test_seller_strictly_rejected_creating_unexpired_reservation_by_service(self):
        """El servicio create_panel_manual_booking rechaza a vendedor con PermissionDenied si expires_at=None."""
        self.assertFalse(can_create_manual_unexpired_reservation(self.seller_user))
        self.assertTrue(can_create_manual_unexpired_reservation(self.admin_user))
        self.assertTrue(can_create_manual_unexpired_reservation(self.super_user))

        with self.assertRaises(PermissionDenied):
            create_panel_manual_booking(
                actor=self.seller_user,
                email="fail@test.com",
                legs=[{"trip": self.trip_future, "origin_stop": self.ts_cba, "destination_stop": self.ts_ssj, "seats": [self.seat_4]}],
                passengers_data=[{"first_name": "Pedro", "last_name": "Diaz", "document_type": "DNI", "document_number": "35123456", "birth_date": "1994-01-01", "nationality": "Argentina"}],
                expires_at=None,
            )

    # 6. Cálculo de vencimiento: medianoche vs salida inicial menos una hora
    def test_expiration_calculation_rules(self):
        """Regla (3): expires_at = min(medianoche AR al final del día de compra, 1h antes de salida inicial)."""
        # Fixture fijo a las 14:00 AR para desacoplar la prueba de la hora real de ejecución.
        fixed_now = datetime(2026, 11, 4, 14, 0, 0, tzinfo=AR_TZ)
        midnight_ar = datetime(2026, 11, 5, 0, 0, 0, tzinfo=AR_TZ)

        # Caso A: Viaje dentro de 3 días -> menor es medianoche AR
        dep_future = fixed_now + timedelta(days=3)
        trip_future = Trip.objects.create(
            route=self.route, bus=self.bus, departure_at=dep_future, status=Trip.Status.SCHEDULED
        )
        exp_a = calculate_internal_transfer_expiration([trip_future], reference_now=fixed_now)
        self.assertEqual(exp_a, midnight_ar)

        # Caso B: Viaje hoy dentro de 3 horas (14:00 + 3h = 17:00, 1h antes = 16:00 < medianoche)
        # -> menor es salida inicial menos 1h
        dep_today = fixed_now + timedelta(hours=3)
        trip_today = Trip.objects.create(
            route=self.route, bus=self.bus, departure_at=dep_today, status=Trip.Status.SCHEDULED
        )
        exp_b = calculate_internal_transfer_expiration([trip_today], reference_now=fixed_now)
        self.assertEqual(exp_b, dep_today - timedelta(hours=1))
        self.assertEqual(exp_b, datetime(2026, 11, 4, 16, 0, 0, tzinfo=AR_TZ))

        # Caso C (Regresión límite nocturno): Compra cerca de las 22:00 AR (ej. 22:15 AR) con viaje en 3 horas (01:15 AR del día siguiente).
        # 1h antes de salida inicial es 00:15 AR del día siguiente, por lo que la medianoche local exacta
        # (00:00 AR) es menor y resulta ser el vencimiento correcto.
        # Valida además equivalencia exacta de instantes entre UTC y Argentina.
        late_now_ar = datetime(2026, 11, 4, 22, 15, 0, tzinfo=AR_TZ)
        late_now_utc = late_now_ar.astimezone(dt_timezone.utc)  # 2026-11-05 01:15:00 UTC
        dep_late = late_now_ar + timedelta(hours=3)          # 2026-11-05 01:15:00 AR_TZ
        trip_late = Trip.objects.create(
            route=self.route, bus=self.bus, departure_at=dep_late, status=Trip.Status.SCHEDULED
        )
        exp_c_ar = calculate_internal_transfer_expiration([trip_late], reference_now=late_now_ar)
        exp_c_utc = calculate_internal_transfer_expiration([trip_late], reference_now=late_now_utc)

        self.assertEqual(exp_c_ar, midnight_ar)
        self.assertEqual(exp_c_utc, midnight_ar)
        self.assertEqual(exp_c_ar.astimezone(dt_timezone.utc), datetime(2026, 11, 5, 3, 0, 0, tzinfo=dt_timezone.utc))
        self.assertLess(exp_c_ar, dep_late - timedelta(hours=1))

    # 7. Venta en la última hora: comprobante obligatorio y pago verificado
    def test_last_hour_sales_enforcement_via_http(self):
        """Regla (8): dentro de la última hora antes de salida inicial exige comprobante y declaración de pago verificado."""
        now = timezone.now()
        dep_last_hour = now + timedelta(minutes=45)
        trip_lh = Trip.objects.create(
            route=self.route, bus=self.bus, departure_at=dep_last_hour, status=Trip.Status.SCHEDULED
        )
        ts1 = TripStop.objects.create(trip=trip_lh, stop=self.stop_cba, sequence=1, scheduled_at=dep_last_hour, allows_boarding=True, allows_alighting=False)
        ts2 = TripStop.objects.create(trip=trip_lh, stop=self.stop_ssj, sequence=2, scheduled_at=dep_last_hour + timedelta(hours=8), allows_boarding=False, allows_alighting=True)
        TripFare.objects.create(trip=trip_lh, seat_category=SeatCategory.CAMA, amount=Decimal("15000.00"), is_active=True)

        self.client.force_login(self.seller_user)
        create_url = reverse("panel:booking_create")

        # Intento 1: Sin comprobante ni verificación -> rechazado en validación
        post_fail = {
            "action": "create_booking",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(trip_lh.pk),
            "outbound_origin": str(ts1.pk),
            "outbound_destination": str(ts2.pk),
            "outbound_seats": [str(self.seat_1.pk)],
            "contact_email": "lh_fail@test.com",
            "p_1_first_name": "Luis",
            "p_1_last_name": "Ramos",
            "p_1_document_type": "DNI",
            "p_1_document_number": "31999888",
            "p_1_birth_date": "1991-08-12",
            "p_1_nationality": "Argentina",
        }
        resp1 = self.client.post(create_url, post_fail, secure=True)
        self.assertEqual(resp1.status_code, 200)
        self.assertContains(resp1, "Dentro de la última hora previa a la salida inicial, solo se admiten operaciones confirmadas")
        self.assertFalse(Booking.objects.filter(email="lh_fail@test.com").exists())

        # Intento 2: Con comprobante y declaración verificada -> confirmado inmediato
        post_ok = dict(post_fail)
        post_ok["contact_email"] = "lh_ok@test.com"
        post_ok["payment_verified"] = "1"
        post_ok["voucher"] = self.sample_voucher("comprobante_lh.pdf")

        resp2 = self.client.post(create_url, post_ok, secure=True)
        self.assertEqual(resp2.status_code, 302)

        booking_ok = Booking.objects.get(email="lh_ok@test.com")
        self.assertEqual(booking_ok.status, BookingStatus.CONFIRMED)
        self.assertEqual(booking_ok.legs.first().seat_assignments.first().status, AssignmentStatus.CONFIRMED)
        self.assertEqual(booking_ok.payments.first().status, PaymentStatus.APPROVED)

    # 8. Autorización antes de retornos idempotentes en revisión
    def test_authorization_strictly_enforced_before_idempotent_returns(self):
        """Regla (5): vendedor ajeno no puede saltarse la autorización ante pagos ya revisados/confirmados."""
        # Crear venta del seller_user
        booking = create_internal_transfer_booking(
            seller=self.seller_user,
            email="seller_rev@test.com",
            legs=[{"trip": self.trip_future, "origin_stop": self.ts_cba, "destination_stop": self.ts_ssj, "seats": [self.seat_2]}],
            passengers_data=[{"first_name": "Elena", "last_name": "Paz", "document_type": "DNI", "document_number": "34222333", "birth_date": "1993-02-14", "nationality": "Argentina"}],
            voucher=self.sample_voucher(),
        )
        payment = booking.payments.first()

        # Vendedor dueño aprueba legítimamente su venta
        review_url = reverse("panel:transfer_review", kwargs={"public_id": payment.public_id})
        self.client.force_login(self.seller_user)
        resp_own = self.client.post(review_url, {"action": "approve"})
        self.assertEqual(resp_own.status_code, 302)

        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)

        # Ahora un vendedor ajeno (other_seller) intenta acceder al pago ya aprobado vía HTTP
        self.client.force_login(self.other_seller)
        resp_alien = self.client.post(review_url, {"action": "approve"})
        # Debe ser rechazado con 404 (IDOR protection en _visible_payments), NO 302 idempotente
        self.assertEqual(resp_alien.status_code, 404)

        voucher_url = reverse("panel:payment_voucher", kwargs={"public_id": payment.public_id})
        resp_alien_voucher = self.client.get(voucher_url)
        self.assertEqual(resp_alien_voucher.status_code, 404)

        # En el servicio, review_transfer_payment valida autorización antes de retornos idempotentes
        with self.assertRaises(ValidationError) as ctx:
            review_transfer_payment(payment_or_id=payment, reviewer=self.other_seller, approved=True)
        self.assertIn("Los vendedores solo pueden revisar y aprobar sus propias ventas", str(ctx.exception))

        # Pero el vendedor dueño obtiene respuesta legítima e idempotente
        self.client.force_login(self.seller_user)
        resp_idemp = self.client.post(review_url, {"action": "approve"})
        self.assertEqual(resp_idemp.status_code, 302)

        # Y el administrador también puede acceder globalmente
        self.client.force_login(self.admin_user)
        resp_admin = self.client.post(review_url, {"action": "approve"})
        self.assertEqual(resp_admin.status_code, 302)

    # 9. Canal ONLINE preservado con retención de 15 minutos
    def test_online_channel_preservation(self):
        """Verifica que el canal ONLINE conserve su retención estricta de 15 minutos."""
        now = timezone.now()
        booking_online = create_online_booking(
            email="online_check@test.com",
            legs=[{"trip": self.trip_future, "origin_stop": self.ts_cba, "destination_stop": self.ts_ssj, "seats": [self.seat_4]}],
            passengers_data=[{"first_name": "Lucas", "last_name": "Silva", "document_type": "DNI", "document_number": "39111444", "birth_date": "1997-09-09", "nationality": "Argentina"}],
            now=now,
        )
        self.assertEqual(booking_online.channel, BookingChannel.ONLINE)
        self.assertEqual(booking_online.status, BookingStatus.HELD)
        self.assertEqual(booking_online.expires_at, now + timedelta(minutes=15))

    # 10. Versionado de recorridos y preservación de históricos
    def test_versioned_route_management_and_history_preservation(self):
        """Verifica que modificar una ruta con viajes asociados cree una nueva versión inmutable."""
        self.assertTrue(self.route.has_trips)
        self.assertEqual(self.route.version, 1)

        # Generar nueva versión v2 a partir de v1
        new_v2, created_version = save_route_configuration(
            route=self.route,
            code="CBA-SSJ",
            name="Córdoba a Jujuy Directo v2",
            stops_data=[
                {"stop": self.stop_cba, "sequence": 1, "allows_boarding": True, "allows_alighting": False},
                {"stop": self.stop_ssj, "sequence": 2, "allows_boarding": False, "allows_alighting": True},
            ],
            force_new_version=True,
        )
        self.assertTrue(created_version)
        self.assertEqual(new_v2.version, 2)
        self.assertEqual(new_v2.parent_route, self.route)
        self.assertTrue(new_v2.is_active)

        # Ruta v1 debe haber sido desactivada
        self.route.refresh_from_db()
        self.assertFalse(self.route.is_active)

        # El viaje histórico existente conserva su vínculo con la v1
        self.trip_future.refresh_from_db()
        self.assertEqual(self.trip_future.route, self.route)
        self.assertEqual(self.trip_future.route.version, 1)

    # 11. Protección de paradas con viajes históricos
    def test_stop_protection_with_historical_trips(self):
        """Paradas con viajes asociados no admiten modificación de atributos críticos."""
        self.assertTrue(self.stop_cba.has_historical_trips)

        # Modificación de atributos congelados debe ser rechazada
        self.stop_cba.province = "Salta"
        with self.assertRaises(ValidationError) as ctx:
            self.stop_cba.clean()
        self.assertIn("No se puede modificar la provincia", str(ctx.exception))

    # 12. Navegación y accesos de rol en templates
    def test_sidebar_and_trips_navigation_roles(self):
        """Verifica enlaces en sidebar y botón 'Gestionar recorridos' en Viajes."""
        # Administrador:
        self.client.force_login(self.admin_user)
        resp_trips_admin = self.client.get(reverse("panel:trips"))
        self.assertEqual(resp_trips_admin.status_code, 200)
        self.assertContains(resp_trips_admin, "Gestionar recorridos")
        self.assertContains(resp_trips_admin, reverse("panel:routes"))
        self.assertContains(resp_trips_admin, "Nueva reserva")
        self.assertContains(resp_trips_admin, "Venta interna")

        # Vendedor:
        self.client.force_login(self.seller_user)
        resp_trips_seller = self.client.get(reverse("panel:trips"))
        self.assertEqual(resp_trips_seller.status_code, 200)
        # Vendedor no ve "Gestionar recorridos"
        self.assertNotContains(resp_trips_seller, "Gestionar recorridos")
        # Vendedor ve "Venta interna" en el sidebar, pero NO "Nueva reserva" (sin vencimiento)
        self.assertContains(resp_trips_seller, "Venta interna")
        self.assertNotContains(resp_trips_seller, "Nueva reserva")
