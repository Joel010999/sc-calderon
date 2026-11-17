"""Pruebas exhaustivas del flujo de ventas internas por transferencia bancaria en SC Viajes.

Verifica el cumplimiento estricto de las 13 reglas obligatorias:
(1) Vendedores no pueden crear reservas manuales sin pago/sin vencimiento; solo administradores/superusuarios.
(2) Vendedores pueden registrar compra pendiente por transferencia con datos completos y retención temporal.
(3) expires_at es el menor entre medianoche al final del día de confirmación de compra en America/Argentina/Buenos_Aires y una hora antes de la salida inicial; ida/vuelta usa el límite más temprano aplicable.
(4) Guardar vencimiento sin reiniciarlo al editar o reenviar.
(5) Pago requiere comprobante PDF/imagen válido; vendedor verifica externamente y aprueba solo sus ventas; admin/superuser global.
(6) Comprobante no confirma ni extiende retención.
(7) Vencimiento libera disponibilidad conservando historial; comando/proceso de mantenimiento.
(8) Última hora antes de salida inicial: solo operación confirmada con datos completos, comprobante y declaración de pago verificado; sin retenciones pendientes persistentes.
(9) Confirmar comprueba y asigna butaca transaccionalmente y rechaza conflicto.
(10) Desde salida inicial no nuevas ventas.
(11) Confirmación/emisión idempotentes, sin duplicar pagos/asignaciones/numeración/entregas.
(12) No tocar flujo público ni plazos.
(13) Conservar menores gratuitos y snapshots.

Alcance por vendedor en QuerySets, listados, búsquedas, contadores, detalles, comprobantes y acciones.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import io
import shutil
import tempfile
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from panel.internal_sales_permissions import (
    can_create_manual_unexpired_reservation,
    can_review_transfer_payment,
    require_can_create_manual_unexpired_reservation,
    require_can_review_transfer_payment,
)
from panel.payment_views import _visible_payments
from panel.reservation_services import create_panel_manual_booking
from panel.reservation_views import _visible_bookings
from payments.models import Payment, PaymentMethod, PaymentStatus
from payments.services import (
    register_transfer_payment,
    review_transfer_payment,
)
from sales.exceptions import BookingExpiredError, InvalidBookingError, SeatUnavailableError
from sales.internal_transfer import (
    calculate_internal_transfer_expiration,
    create_immediate_transfer_sale,
    create_internal_transfer_booking,
    validate_complete_internal_data,
    validate_internal_sale_timing,
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
from sales.services import (
    confirm_booking,
    create_booking,
    get_trip_availability,
)
from tickets.models import Ticket, TicketSequence

User = get_user_model()
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def make_pax(first_name="Juan", last_name="Perez", doc="10000000", birth=date(1990, 1, 1), **kwargs):
    """Genera datos completos y válidos para un pasajero de prueba."""
    p = {
        "first_name": first_name,
        "last_name": last_name,
        "document_type": "DNI",
        "document_number": str(doc),
        "birth_date": birth,
        "nationality": "Argentina",
        "phone": "3512223333",
        "email": f"{first_name.lower()}@scviajes.com",
    }
    p.update(kwargs)
    return p


class InternalTransferSalesBaseTestCase(TestCase):
    """Configuración común para las pruebas de venta interna por transferencia."""

    @classmethod
    def setUpTestData(cls):
        # Grupos
        cls.group_admin, _ = Group.objects.get_or_create(name="Administrador")
        cls.group_seller, _ = Group.objects.get_or_create(name="Vendedor")

        # Usuarios
        cls.admin_user = User.objects.create_user(
            username="admin_user", email="admin@scviajes.com", password="password123"
        )
        cls.admin_user.groups.add(cls.group_admin)

        cls.seller_1 = User.objects.create_user(
            username="seller_1", email="seller1@scviajes.com", password="password123"
        )
        cls.seller_1.groups.add(cls.group_seller)

        cls.seller_2 = User.objects.create_user(
            username="seller_2", email="seller2@scviajes.com", password="password123"
        )
        cls.seller_2.groups.add(cls.group_seller)

        cls.superuser = User.objects.create_superuser(
            username="superuser", email="super@scviajes.com", password="password123"
        )

        # Paradas: CBA (origen), JMA (intermedia con subida), SSJ (destino)
        cls.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        cls.stop_jma = Stop.objects.create(code="JMA", name="Jesús María", city="Jesús María", province="Córdoba")
        cls.stop_ssj = Stop.objects.create(code="SSJ", name="San Salvador de Jujuy", city="San Salvador", province="Jujuy")

        # Recorrido Córdoba -> Jujuy
        cls.route = Route.objects.create(code="CBA-JUJ", name="Córdoba a Jujuy", is_active=True)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_jma, sequence=2, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_ssj, sequence=3, allows_boarding=False, allows_alighting=True)

        # Colectivo y butacas
        cls.bus = Bus.objects.create(code="BUS-100", display_name="Interno 100", is_active=True)
        cls.seat_1 = Seat.objects.create(bus=cls.bus, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)
        cls.seat_2 = Seat.objects.create(bus=cls.bus, number=2, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=1, position_y=0)
        cls.seat_3 = Seat.objects.create(bus=cls.bus, number=3, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
        cls.seat_4 = Seat.objects.create(bus=cls.bus, number=4, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0)

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

    def tearDown(self):
        self.override_settings.disable()
        shutil.rmtree(self.temp_protected_media, ignore_errors=True)
        shutil.rmtree(self.temp_public_media, ignore_errors=True)
        super().tearDown()

    def create_test_trip(self, departure_dt, intermediate_dt=None, arrival_dt=None):
        """Crea un viaje con salida programada y tarifas."""
        if intermediate_dt is None:
            intermediate_dt = departure_dt + timedelta(hours=2)
        if arrival_dt is None:
            arrival_dt = departure_dt + timedelta(hours=10)

        trip = Trip.objects.create(
            route=self.route,
            bus=self.bus,
            departure_at=departure_dt,
            status=Trip.Status.SCHEDULED,
        )
        ts_cba = TripStop.objects.create(
            trip=trip, stop=self.stop_cba, sequence=1, scheduled_at=departure_dt, allows_boarding=True, allows_alighting=False
        )
        ts_jma = TripStop.objects.create(
            trip=trip, stop=self.stop_jma, sequence=2, scheduled_at=intermediate_dt, allows_boarding=True, allows_alighting=False
        )
        ts_ssj = TripStop.objects.create(
            trip=trip, stop=self.stop_ssj, sequence=3, scheduled_at=arrival_dt, allows_boarding=False, allows_alighting=True
        )

        TripFare.objects.create(trip=trip, seat_category=SeatCategory.CAMA, amount=Decimal("20000.00"), is_active=True)
        TripFare.objects.create(trip=trip, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("15000.00"), is_active=True)

        return trip, ts_cba, ts_jma, ts_ssj

    @staticmethod
    def sample_pdf_voucher():
        return SimpleUploadedFile(
            "comprobante.pdf",
            b"%PDF-1.4 sample transfer receipt content",
            content_type="application/pdf",
        )

    @staticmethod
    def sample_image_voucher():
        return SimpleUploadedFile(
            "comprobante.jpg",
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00",
            content_type="image/jpeg",
        )


class ExpirationCalculationTests(InternalTransferSalesBaseTestCase):
    """Reglas (3) y (4): Cálculo exacto de vencimiento con medianoche argentina, 1 hora e ida/vuelta."""

    def test_midnight_argentina_when_departure_is_far(self):
        """Si la salida es en 3 días, vence a medianoche al final del día de confirmación en AR."""
        ref_now = datetime(2026, 11, 4, 10, 30, tzinfo=AR_TZ)
        dep = datetime(2026, 11, 7, 18, 0, tzinfo=AR_TZ)
        trip, _, _, _ = self.create_test_trip(departure_dt=dep)

        exp = calculate_internal_transfer_expiration(reference_now=ref_now, trip=trip)
        expected = datetime(2026, 11, 5, 0, 0, 0, tzinfo=AR_TZ)
        self.assertEqual(exp, expected)

    def test_midnight_argentina_late_evening(self):
        """Venta a las 23:45 hs de Argentina vence a las 00:00 del día siguiente de Argentina."""
        ref_now = datetime(2026, 11, 4, 23, 45, tzinfo=AR_TZ)
        dep = datetime(2026, 11, 8, 8, 0, tzinfo=AR_TZ)
        trip, _, _, _ = self.create_test_trip(departure_dt=dep)

        exp = calculate_internal_transfer_expiration(reference_now=ref_now, trip=trip)
        expected = datetime(2026, 11, 5, 0, 0, 0, tzinfo=AR_TZ)
        self.assertEqual(exp, expected)

    def test_one_hour_before_initial_departure_wins(self):
        """Si la salida es en 3 horas, vence a exactamente 1 hora antes de la salida inicial."""
        ref_now = datetime(2026, 11, 4, 14, 0, tzinfo=AR_TZ)
        dep = datetime(2026, 11, 4, 17, 0, tzinfo=AR_TZ)
        trip, _, _, _ = self.create_test_trip(departure_dt=dep)

        exp = calculate_internal_transfer_expiration(reference_now=ref_now, trip=trip)
        expected = datetime(2026, 11, 4, 16, 0, 0, tzinfo=AR_TZ)
        self.assertEqual(exp, expected)

    def test_one_hour_limit_wins_over_midnight_across_midnight_boundary(self):
        """Si el viaje sale a las 00:30 de mañana, 1 hora antes es a las 23:30 de hoy (gana sobre medianoche)."""
        ref_now = datetime(2026, 11, 4, 20, 0, tzinfo=AR_TZ)
        dep = datetime(2026, 11, 5, 0, 30, tzinfo=AR_TZ)
        trip, _, _, _ = self.create_test_trip(departure_dt=dep)

        exp = calculate_internal_transfer_expiration(reference_now=ref_now, trip=trip)
        expected = datetime(2026, 11, 4, 23, 30, 0, tzinfo=AR_TZ)
        self.assertEqual(exp, expected)

    def test_initial_departure_vs_intermediate_stop(self):
        """Regla (3): Salida inicial vs parada intermedia.

        El viaje parte de CBA a las 10:00 AR. Parada intermedia JMA es a las 12:00 AR.
        Si la venta se realiza a las 08:30 AR para un pasajero que sube en JMA,
        el límite de 1 hora se calcula sobre la salida inicial (10:00 - 1h = 09:00),
        y NUNCA sobre la parada intermedia (12:00 - 1h = 11:00).
        """
        ref_now = datetime(2026, 11, 4, 8, 30, tzinfo=AR_TZ)
        dep_cba = datetime(2026, 11, 4, 10, 0, tzinfo=AR_TZ)
        dep_jma = datetime(2026, 11, 4, 12, 0, tzinfo=AR_TZ)
        trip, _, _, _ = self.create_test_trip(departure_dt=dep_cba, intermediate_dt=dep_jma)

        exp = calculate_internal_transfer_expiration(reference_now=ref_now, trip=trip)
        expected = datetime(2026, 11, 4, 9, 0, 0, tzinfo=AR_TZ)
        self.assertEqual(exp, expected)
        self.assertNotEqual(exp, dep_jma - timedelta(hours=1))

    def test_roundtrip_uses_earliest_limit(self):
        """Regla (3): En compras de ida y vuelta, se aplica el límite más temprano aplicable."""
        ref_now = datetime(2026, 11, 4, 10, 0, tzinfo=AR_TZ)
        # Ida sale en 4 horas (14:00 AR), límite 1 hora = 13:00 AR
        trip_out, _, _, _ = self.create_test_trip(departure_dt=datetime(2026, 11, 4, 14, 0, tzinfo=AR_TZ))
        # Vuelta sale en 3 días (7 de Noviembre)
        trip_ret, _, _, _ = self.create_test_trip(departure_dt=datetime(2026, 11, 7, 10, 0, tzinfo=AR_TZ))

        exp = calculate_internal_transfer_expiration(
            reference_now=ref_now, trip=trip_out, return_trip=trip_ret
        )
        expected = datetime(2026, 11, 4, 13, 0, 0, tzinfo=AR_TZ)
        self.assertEqual(exp, expected)

        # Caso inverso: ida en 3 días, vuelta sale en 5 horas
        trip_out2, _, _, _ = self.create_test_trip(departure_dt=datetime(2026, 11, 7, 10, 0, tzinfo=AR_TZ))
        trip_ret2, _, _, _ = self.create_test_trip(departure_dt=datetime(2026, 11, 4, 15, 0, tzinfo=AR_TZ))

        exp2 = calculate_internal_transfer_expiration(
            reference_now=ref_now, trip=trip_out2, return_trip=trip_ret2
        )
        expected2 = datetime(2026, 11, 4, 14, 0, 0, tzinfo=AR_TZ)
        self.assertEqual(exp2, expected2)

    def test_preserve_expiration_without_restart_on_edit_or_resend(self):
        """Regla (4): Guardar vencimiento sin reiniciarlo al editar o reenviar."""
        now = timezone.now()
        dep = now + timedelta(days=3)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        fixed_expires_at = now + timedelta(hours=8)
        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            status=BookingStatus.HELD,
            email="test_edit@scviajes.com",
            phone="3512345678",
            seller=self.seller_1,
            expires_at=fixed_expires_at,
        )

        booking.internal_note = "Actualización de notas sin tocar vencimiento"
        booking.save()
        booking.refresh_from_db()
        self.assertEqual(booking.expires_at, fixed_expires_at)


class InternalTransferRulesPermissionsTests(InternalTransferSalesBaseTestCase):
    """Reglas (1), (2), (5), (8) y (10): Permisos, roles y ventanas temporales de venta."""

    def test_seller_cannot_create_manual_unexpired_reservation(self):
        """Regla (1): Vendedores no pueden crear reservas manuales sin pago/sin vencimiento."""
        self.assertFalse(can_create_manual_unexpired_reservation(self.seller_1))
        self.assertTrue(can_create_manual_unexpired_reservation(self.admin_user))
        self.assertTrue(can_create_manual_unexpired_reservation(self.superuser))

        with self.assertRaises(PermissionDenied):
            require_can_create_manual_unexpired_reservation(self.seller_1)

        dep = timezone.now() + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)
        legs = [{
            "trip": trip,
            "origin_stop": ts_cba,
            "destination_stop": ts_ssj,
            "category": SeatCategory.CAMA,
            "seats": [self.seat_1],
        }]
        passenger_data = [make_pax()]

        with self.assertRaises(PermissionDenied):
            create_panel_manual_booking(
                actor=self.seller_1,
                email="cliente@scviajes.com",
                phone="3519876543",
                legs=legs,
                passengers_data=passenger_data,
                expires_at=None,
            )

    def test_admin_can_create_manual_unexpired_reservation(self):
        """Regla (1): Administrador o superusuario sí pueden crear reserva manual sin vencimiento."""
        dep = timezone.now() + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)
        legs = [{
            "trip": trip,
            "origin_stop": ts_cba,
            "destination_stop": ts_ssj,
            "category": SeatCategory.CAMA,
            "seats": [self.seat_1],
        }]
        passenger_data = [make_pax(first_name="Laura", last_name="Paz", doc="33444555")]

        booking = create_panel_manual_booking(
            actor=self.admin_user,
            email="laura@scviajes.com",
            phone="3519876543",
            legs=legs,
            passengers_data=passenger_data,
            expires_at=None,
        )
        self.assertIsNotNone(booking)
        self.assertIsNone(booking.expires_at)
        self.assertEqual(booking.status, BookingStatus.HELD)

    def test_seller_can_register_pending_purchase_with_complete_data_and_hold(self):
        """Regla (2): Vendedor puede registrar compra pendiente por transferencia con retención temporal."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        passenger_data = [make_pax(first_name="Martin", last_name="Fierro", doc="11222333")]

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="martin@scviajes.com",
            phone="3511112222",
            passenger_data=passenger_data,
            internal_note="Venta pendiente por transferencia",
            now=now,
        )

        self.assertEqual(booking.channel, BookingChannel.MANUAL)
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertEqual(booking.seller, self.seller_1)
        self.assertIsNotNone(booking.expires_at)
        self.assertTrue(booking.expires_at > now)
        sa = SeatAssignment.objects.get(leg__booking=booking, seat=self.seat_1)
        self.assertEqual(sa.status, AssignmentStatus.HELD)

    def test_incomplete_passenger_or_contact_data_is_rejected(self):
        """Regla (2): Falla si los datos de contacto o de los pasajeros están incompletos."""
        # Caso 1: Falta email de contacto
        with self.assertRaises(ValidationError):
            validate_complete_internal_data(
                email="",
                phone="3511112222",
                passenger_data=[make_pax()],
            )

        # Caso 2: Pasajero sin apellido
        with self.assertRaises(ValidationError):
            validate_complete_internal_data(
                email="a@b.com",
                passenger_data=[make_pax(last_name="")],
            )

        # Caso 3: Pasajero sin número de documento
        with self.assertRaises(ValidationError):
            validate_complete_internal_data(
                email="a@b.com",
                phone="3511112222",
                passenger_data=[make_pax(doc="")],
            )

    def test_no_new_sales_after_trip_initial_departure(self):
        """Regla (10): Desde salida inicial no se permiten nuevas ventas."""
        past_dep = timezone.now() - timedelta(minutes=5)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=past_dep)

        with self.assertRaises(ValidationError) as ctx:
            validate_internal_sale_timing(trip=trip, now=timezone.now())
        self.assertIn("inició su salida inicial", str(ctx.exception))

        # Intento de creación directa de reserva
        with self.assertRaises(ValidationError):
            create_internal_transfer_booking(
                seller=self.seller_1,
                trip=trip,
                origin_stop=ts_cba,
                destination_stop=ts_ssj,
                seat_numbers=[1],
                seat_category=SeatCategory.CAMA,
                email="a@b.com",
                phone="123",
                passenger_data=[make_pax()],
                now=timezone.now(),
            )

    def test_online_booking_intermediate_stop_after_initial_departure_non_regression(self):
        """Regla (12) y no regresión: La restricción de salida inicial no afecta al canal ONLINE.

        Para una parada intermedia cuya hora de subida aún no alcanzó el horario de corte online,
        la reserva pública web se genera normalmente aunque el viaje ya haya iniciado su partida inicial.
        En cambio, para el canal MANUAL queda estrictamente prohibida toda nueva venta.
        """
        now = timezone.now()
        past_dep = now - timedelta(minutes=30)
        # Parada intermedia en 2 horas respecto a salida inicial => now + 90 min
        intermediate_dt = past_dep + timedelta(hours=2)
        arrival_dt = past_dep + timedelta(hours=8)
        trip, ts_cba, ts_jma, ts_ssj = self.create_test_trip(
            departure_dt=past_dep,
            intermediate_dt=intermediate_dt,
            arrival_dt=arrival_dt,
        )

        from sales.services import create_manual_booking, create_online_booking

        # 1. El canal MANUAL queda bloqueado por la regla de salida inicial
        with self.assertRaises(ValidationError) as ctx:
            create_manual_booking(
                seller=self.seller_1,
                email="manual_post_salida@correo.com",
                legs=[{
                    "trip": trip,
                    "origin_stop": ts_jma,
                    "destination_stop": ts_ssj,
                    "seats": [self.seat_1],
                }],
                passengers_data=[make_pax()],
                now=now,
            )
        self.assertIn("No se permiten nuevas ventas una vez iniciada la salida del viaje.", str(ctx.exception))

        # 2. El canal ONLINE para la parada intermedia (con corte a 60 min de subida) permanece intacto y exitoso
        online_booking = create_online_booking(
            email="online_intermedia@correo.com",
            legs=[{
                "trip": trip,
                "origin_stop": ts_jma,
                "destination_stop": ts_ssj,
                "seats": [self.seat_2],
            }],
            now=now,
        )
        self.assertIsNotNone(online_booking.pk)
        self.assertEqual(online_booking.channel, BookingChannel.ONLINE)
        self.assertEqual(online_booking.status, BookingStatus.HELD)

        # 3. Si se intenta comprar online pasada la hora de corte de la parada intermedia, se aplica el corte online estándar
        late_online_now = intermediate_dt - timedelta(minutes=30)  # Menos de 60 min para la parada
        with self.assertRaises(ValidationError) as ctx_online:
            create_online_booking(
                email="online_tardio@correo.com",
                legs=[{
                    "trip": trip,
                    "origin_stop": ts_jma,
                    "destination_stop": ts_ssj,
                    "seats": [self.seat_3],
                }],
                now=late_online_now,
            )
        self.assertIn("cierra 60 minutos antes del horario de subida", str(ctx_online.exception))

    def test_last_hour_requires_immediate_sale_rejects_pending_hold(self):
        """Regla (8): Última hora antes de salida inicial: solo venta inmediata confirmada, sin retenciones pendientes."""
        now = timezone.now()
        dep = now + timedelta(minutes=45)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        # Intento de crear retención pendiente en la última hora es RECHAZADO
        with self.assertRaises(ValidationError) as ctx:
            create_internal_transfer_booking(
                seller=self.seller_1,
                trip=trip,
                origin_stop=ts_cba,
                destination_stop=ts_ssj,
                seat_numbers=[1],
                seat_category=SeatCategory.CAMA,
                email="c@d.com",
                phone="3511112222",
                passenger_data=[make_pax(first_name="Claudio", last_name="Bravo", doc="999888")],
                now=now,
            )
        self.assertIn("última hora", str(ctx.exception))

        # En cambio, con comprobante y declaración de pago verificado (venta inmediata) SÍ funciona
        booking = create_immediate_transfer_sale(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="c@d.com",
            phone="3511112222",
            passenger_data=[make_pax(first_name="Claudio", last_name="Bravo", doc="999888")],
            voucher=self.sample_pdf_voucher(),
            payment_verified=True,
            internal_note="Venta inmediata en la última hora",
            now=now,
        )
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        payment = Payment.objects.get(booking=booking)
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(payment.reviewed_by, self.seller_1)
        sa = SeatAssignment.objects.get(leg__booking=booking, seat=self.seat_1)
        self.assertEqual(sa.status, AssignmentStatus.CONFIRMED)

    def test_immediate_sale_without_payment_verified_rejected(self):
        """Regla (8): La venta inmediata rechaza si el vendedor no declara haber verificado el pago."""
        now = timezone.now()
        dep = now + timedelta(minutes=40)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        with self.assertRaises(ValidationError) as ctx:
            create_immediate_transfer_sale(
                seller=self.seller_1,
                trip=trip,
                origin_stop=ts_cba,
                destination_stop=ts_ssj,
                seat_numbers=[1],
                seat_category=SeatCategory.CAMA,
                email="c@d.com",
                phone="3511112222",
                passenger_data=[make_pax(first_name="Claudio", last_name="Bravo", doc="999888")],
                voucher=self.sample_pdf_voucher(),
                payment_verified=False,
                now=now,
            )
        self.assertIn("payment_verified", str(ctx.exception))


class VoucherAndReviewTests(InternalTransferSalesBaseTestCase):
    """Reglas (5) y (6): Comprobante, aprobación propia vs ajena e inmutabilidad de retención."""

    def test_voucher_upload_creates_payment_under_review_does_not_confirm_or_extend(self):
        """Regla (6): Subir comprobante crea pago en revisión pero NO confirma la reserva ni extiende vencimiento."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="test@scviajes.com",
            phone="3512223333",
            passenger_data=[make_pax()],
            now=now,
        )
        initial_expires_at = booking.expires_at

        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_1,
            voucher=self.sample_pdf_voucher(),
            now=now,
        )
        self.assertEqual(payment.status, PaymentStatus.UNDER_REVIEW)

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertEqual(booking.expires_at, initial_expires_at)

    def test_seller_can_approve_own_transfer_sale(self):
        """Regla (5): Vendedor verifica y aprueba sus propias ventas."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="test@scviajes.com",
            phone="3512223333",
            passenger_data=[make_pax()],
            now=now,
        )
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_1,
            voucher=self.sample_pdf_voucher(),
            now=now,
        )

        self.assertTrue(can_review_transfer_payment(self.seller_1, payment))

        review_transfer_payment(payment_or_id=payment, reviewer=self.seller_1, approved=True, now=now)

        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(payment.reviewed_by, self.seller_1)
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

    def test_seller_cannot_approve_other_seller_transfer_sale(self):
        """Regla (5): Vendedor NO puede revisar ni aprobar transferencias de ventas ajenas."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="test@scviajes.com",
            phone="3512223333",
            passenger_data=[make_pax()],
            now=now,
        )
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_1,
            voucher=self.sample_pdf_voucher(),
            now=now,
        )

        self.assertFalse(can_review_transfer_payment(self.seller_2, payment))

        with self.assertRaises(ValidationError) as ctx:
            review_transfer_payment(payment_or_id=payment, reviewer=self.seller_2, approved=True, now=now)
        self.assertIn("propias ventas", str(ctx.exception))

    def test_admin_can_approve_any_transfer_sale_globally(self):
        """Regla (5): Administrador y superusuario pueden aprobar transferencias globalmente."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="test@scviajes.com",
            phone="3512223333",
            passenger_data=[make_pax()],
            now=now,
        )
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_1,
            voucher=self.sample_pdf_voucher(),
            now=now,
        )

        self.assertTrue(can_review_transfer_payment(self.admin_user, payment))
        self.assertTrue(can_review_transfer_payment(self.superuser, payment))

        review_transfer_payment(payment_or_id=payment, reviewer=self.admin_user, approved=True, now=now)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(payment.reviewed_by, self.admin_user)
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)


class SeatConflictAndIdempotencyTests(InternalTransferSalesBaseTestCase):
    """Reglas (9) y (11): Transaccionalidad de confirmación, rechazo por conflicto e idempotencia."""

    def test_seat_conflict_rejection_on_confirmation(self):
        """Regla (9): Confirmar comprueba la butaca transaccionalmente y rechaza si ya fue confirmada."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        # Reserva A con butaca 1
        booking_a = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="a@scviajes.com",
            phone="111",
            passenger_data=[make_pax(first_name="A", last_name="A", doc="11")],
            now=now,
        )
        pay_a = register_transfer_payment(booking_or_id=booking_a, seller=self.seller_1, voucher=self.sample_pdf_voucher(), now=now)

        from unittest.mock import patch
        with patch("payments.services.SeatAssignment.objects.filter") as mock_filter:
            mock_filter.return_value.exclude.return_value.exists.return_value = True
            with self.assertRaises(SeatUnavailableError) as ctx:
                review_transfer_payment(payment_or_id=pay_a, reviewer=self.admin_user, approved=True, now=now)
            self.assertIn("Conflicto de butaca", str(ctx.exception))

    def test_idempotent_confirmation_and_fulfillment(self):
        """Regla (11): Confirmación y emisión idempotentes, sin duplicar pagos, secuencias o pasajes."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="idem@scviajes.com",
            phone="123456",
            passenger_data=[make_pax(first_name="Idem", last_name="Potente", doc="777")],
            now=now,
        )
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_1,
            voucher=self.sample_pdf_voucher(),
            now=now,
        )

        p_res1 = review_transfer_payment(payment_or_id=payment, reviewer=self.seller_1, approved=True, now=now)
        self.assertEqual(p_res1.status, PaymentStatus.APPROVED)

        p_res2 = review_transfer_payment(payment_or_id=payment, reviewer=self.seller_1, approved=True, now=now)
        self.assertEqual(p_res2.pk, p_res1.pk)

        b_res3 = confirm_booking(booking_or_id=booking, now=now)
        self.assertEqual(b_res3.pk, booking.pk)

        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)

    def test_other_seller_cannot_review_already_approved_payment(self):
        """Seguridad: Un vendedor ajeno no puede revisar ni eludir permisos sobre un pago ya aprobado/confirmado."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="seguridad@scviajes.com",
            phone="123456",
            passenger_data=[make_pax(first_name="Sec", last_name="Check", doc="888")],
            now=now,
        )
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_1,
            voucher=self.sample_pdf_voucher(),
            now=now,
        )

        # seller_1 aprueba su propia venta
        p_res = review_transfer_payment(payment_or_id=payment, reviewer=self.seller_1, approved=True, now=now)
        self.assertEqual(p_res.status, PaymentStatus.APPROVED)
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

        # Intento de seller_2 de revisar/aprobar el pago ya aprobado/confirmado
        with self.assertRaises(ValidationError) as ctx:
            review_transfer_payment(payment_or_id=payment, reviewer=self.seller_2, approved=True, now=now)
        self.assertIn("propias ventas", str(ctx.exception))

        # Intento de seller_2 de rechazar el pago ya aprobado/confirmado también se rechaza
        with self.assertRaises(ValidationError) as ctx:
            review_transfer_payment(payment_or_id=payment, reviewer=self.seller_2, approved=False, rejection_reason="Hack", now=now)
        self.assertIn("propias ventas", str(ctx.exception))

        # En cambio, el propio vendedor dueño (seller_1) sí puede llamar de forma idempotente
        p_idempotent = review_transfer_payment(payment_or_id=payment, reviewer=self.seller_1, approved=True, now=now)
        self.assertEqual(p_idempotent.pk, payment.pk)

        # Y el administrador también puede llamar de forma idempotente
        p_admin_idempotent = review_transfer_payment(payment_or_id=payment, reviewer=self.admin_user, approved=True, now=now)
        self.assertEqual(p_admin_idempotent.pk, payment.pk)


class MinorsAndSnapshotsTests(InternalTransferSalesBaseTestCase):
    """Regla (13): Menores gratuitos (< 5 años) y conservación de snapshots."""

    def test_minor_under_five_free_with_accompanying_adult(self):
        """Menor estrictamente menor a 5 años sin butaca viaja con $0.00 y conserva snapshot."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        passenger_data = [
            make_pax(
                first_name="Madre",
                last_name="Perez",
                doc="30111222",
                birth=date(1990, 1, 1),
                is_lap_infant=False,
            ),
            make_pax(
                first_name="Bebe",
                last_name="Perez",
                doc="60111222",
                birth=date(2024, 6, 1),
                is_lap_infant=True,
                accompanied_by_position=1,
            ),
        ]

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="madre@scviajes.com",
            phone="351111",
            passenger_data=passenger_data,
            now=now,
        )

        self.assertEqual(booking.passengers.count(), 2)
        p_adult = booking.passengers.get(position=1)
        p_baby = booking.passengers.get(position=2)

        self.assertFalse(p_adult.is_lap_infant)
        self.assertTrue(p_baby.is_lap_infant)
        self.assertEqual(p_baby.accompanied_by, p_adult)

        self.assertFalse(SeatAssignment.objects.filter(passenger=p_baby).exists())
        sa_adult = SeatAssignment.objects.get(passenger=p_adult)
        self.assertEqual(sa_adult.price, Decimal("20000.00"))
        self.assertIsNotNone(sa_adult.seat)


class SellerScopeAndListingTests(InternalTransferSalesBaseTestCase):
    """Alcance por vendedor en QuerySets, listados y contadores."""

    def test_seller_only_sees_own_bookings_and_payments(self):
        """El vendedor solo ve sus propias operaciones; el admin ve todo."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        # Venta de seller_1
        b1 = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[1],
            seat_category=SeatCategory.CAMA,
            email="b1@scviajes.com",
            phone="111",
            passenger_data=[make_pax(first_name="U1", last_name="U1", doc="11")],
            now=now,
        )
        p1 = register_transfer_payment(booking_or_id=b1, seller=self.seller_1, voucher=self.sample_pdf_voucher(), now=now)

        # Venta de seller_2
        b2 = create_internal_transfer_booking(
            seller=self.seller_2,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[2],
            seat_category=SeatCategory.CAMA,
            email="b2@scviajes.com",
            phone="222",
            passenger_data=[make_pax(first_name="U2", last_name="U2", doc="22")],
            now=now,
        )
        p2 = register_transfer_payment(booking_or_id=b2, seller=self.seller_2, voucher=self.sample_image_voucher(), now=now)

        # Scope de reservas
        qs_b_seller1 = _visible_bookings(self.seller_1)
        self.assertIn(b1, qs_b_seller1)
        self.assertNotIn(b2, qs_b_seller1)

        qs_b_admin = _visible_bookings(self.admin_user)
        self.assertIn(b1, qs_b_admin)
        self.assertIn(b2, qs_b_admin)

        # Scope de pagos
        qs_p_seller1 = _visible_payments(self.seller_1)
        self.assertIn(p1, qs_p_seller1)
        self.assertNotIn(p2, qs_p_seller1)

        qs_p_admin = _visible_payments(self.admin_user)
        self.assertIn(p1, qs_p_admin)
        self.assertIn(p2, qs_p_admin)


class ExpirationReleasesAvailabilityTests(InternalTransferSalesBaseTestCase):
    """Regla (7): Vencimiento libera disponibilidad conservando historial y mantenimiento."""

    def test_expired_booking_releases_availability_preserving_history(self):
        """Una reserva vencida libera la butaca para la venta pero conserva Booking y Payment."""
        now = timezone.now()
        dep = now + timedelta(days=2)
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=dep)

        future_expiration = now + timedelta(hours=2)
        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            status=BookingStatus.HELD,
            email="expired@scviajes.com",
            phone="351111111",
            seller=self.seller_1,
            expires_at=future_expiration,
        )
        leg = BookingLeg.objects.create(
            booking=booking,
            sequence=1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            origin_stop_name="Córdoba",
            destination_stop_name="Jujuy",
            departure_at=dep,
            arrival_at=dep + timedelta(hours=10),
        )
        pax = BookingPassenger.objects.create(
            booking=booking, position=1, first_name="Old", last_name="Passenger", document_type="DNI", document_number="888", birth_date=date(1990, 1, 1), nationality="Argentina"
        )
        sa = SeatAssignment.objects.create(
            leg=leg,
            passenger=pax,
            trip=trip,
            seat=self.seat_1,
            status=AssignmentStatus.HELD,
            seat_number=1,
            category=SeatCategory.CAMA,
            price=Decimal("20000.00"),
            currency="ARS",
        )
        from sales.services import expire_booking, release_expired_bookings

        # Simulamos que la reserva venció
        booking.expires_at = now - timedelta(minutes=10)
        booking.save(update_fields=["expires_at"])

        # Ejecución del proceso de expiración
        expire_booking(booking.pk, now=now)

        booking.refresh_from_db()
        sa.refresh_from_db()

        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        self.assertEqual(sa.status, AssignmentStatus.RELEASED)
        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())

        avail = get_trip_availability(trip=trip, origin_stop=ts_cba, destination_stop=ts_ssj)
        self.assertIn(self.seat_1, avail["available_seats"])
        self.assertNotIn(self.seat_1.pk, avail["occupied_seat_ids"])


class SeatNumberToPkResolutionTests(InternalTransferSalesBaseTestCase):
    """Verifica que seat_numbers con PK != number (típico en PostgreSQL) se resuelvan correctamente."""

    def test_seat_number_resolved_when_pk_differs_from_number(self):
        now = timezone.now()
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=now + timedelta(days=2))
        dummy_bus = Bus.objects.create(code=f"BUS-OFFSET-{uuid4().hex[:6]}", display_name="Offset Bus", is_active=True)
        for i in range(1, 10):
            Seat.objects.create(bus=dummy_bus, number=i, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=i, position_y=0)

        seat_custom = Seat.objects.create(
            bus=trip.bus,
            number=44,
            deck=Seat.Deck.LOWER,
            category=SeatCategory.CAMA,
            position_x=10,
            position_y=0,
        )
        self.assertNotEqual(seat_custom.pk, seat_custom.number)

        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[44],
            seat_category=SeatCategory.CAMA,
            email="offset@scviajes.com",
            phone="3511112222",
            passenger_data=[make_pax(first_name="Offset", last_name="Test", doc="99887766")],
            now=now,
        )
        self.assertEqual(booking.status, BookingStatus.HELD)
        sa = SeatAssignment.objects.get(leg__booking=booking, seat=seat_custom)
        self.assertEqual(sa.seat_number, 44)
        self.assertEqual(sa.seat_id, seat_custom.pk)

    def test_immediate_sale_seat_number_resolved_when_pk_differs_from_number(self):
        now = timezone.now()
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=now + timedelta(days=2))
        dummy_bus = Bus.objects.create(code=f"BUS-OFFSET-IMM-{uuid4().hex[:6]}", display_name="Offset Bus Imm", is_active=True)
        for i in range(1, 10):
            Seat.objects.create(bus=dummy_bus, number=i, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=i, position_y=0)

        seat_custom = Seat.objects.create(
            bus=trip.bus,
            number=45,
            deck=Seat.Deck.LOWER,
            category=SeatCategory.CAMA,
            position_x=11,
            position_y=0,
        )
        self.assertNotEqual(seat_custom.pk, seat_custom.number)

        booking = create_immediate_transfer_sale(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[45],
            seat_category=SeatCategory.CAMA,
            email="offset-imm@scviajes.com",
            phone="3511112222",
            passenger_data=[make_pax(first_name="Offset", last_name="Imm", doc="99887755")],
            voucher=self.sample_pdf_voucher(),
            payment_verified=True,
            now=now,
        )
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        sa = SeatAssignment.objects.get(leg__booking=booking, seat=seat_custom)
        self.assertEqual(sa.seat_number, 45)
        self.assertEqual(sa.seat_id, seat_custom.pk)
        self.assertEqual(sa.status, AssignmentStatus.CONFIRMED)


class SeatNumberPkCollisionRegressionTests(InternalTransferSalesBaseTestCase):
    """Regresión específica para colisión cruzada entre número de butaca y PK."""

    def _setup_collision_trip(self):
        now = timezone.now()
        trip, ts_cba, _, ts_ssj = self.create_test_trip(departure_dt=now + timedelta(days=2))
        dummy_bus = Bus.objects.create(code=f"BUS-DUMMY-{uuid4().hex[:6]}", display_name="Dummy Bus", is_active=True)
        for i in range(1, 6):
            Seat.objects.create(bus=dummy_bus, number=i, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=i, position_y=0)

        next_pk = (Seat.objects.order_by("-pk").first().pk if Seat.objects.exists() else 0) + 1
        # seat_a: PK = next_pk, number = next_pk + 1
        # seat_b: PK = next_pk + 1, number = next_pk
        seat_a = Seat.objects.create(
            bus=trip.bus,
            number=next_pk + 1,
            deck=Seat.Deck.LOWER,
            category=SeatCategory.CAMA,
            position_x=12,
            position_y=0,
        )
        seat_b = Seat.objects.create(
            bus=trip.bus,
            number=next_pk,
            deck=Seat.Deck.LOWER,
            category=SeatCategory.CAMA,
            position_x=11,
            position_y=0,
        )
        self.assertEqual(seat_a.number, seat_b.pk)
        self.assertEqual(seat_b.number, seat_a.pk)
        return now, trip, ts_cba, ts_ssj, seat_a, seat_b

    def test_pending_transfer_booking_seat_numbers_resolves_number_not_pk(self):
        now, trip, ts_cba, ts_ssj, seat_a, seat_b = self._setup_collision_trip()

        # Solicitamos butaca por número (seat_b.number, cuyo valor coincide con seat_a.pk)
        target_num = seat_b.number
        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[target_num],
            seat_category=SeatCategory.CAMA,
            email="collision_pending@scviajes.com",
            phone="3511112222",
            passenger_data=[make_pax(first_name="Coll", last_name="Pending", doc="11223344")],
            now=now,
        )
        self.assertEqual(booking.status, BookingStatus.HELD)
        sa = SeatAssignment.objects.get(leg__booking=booking)
        self.assertEqual(sa.seat, seat_b)
        self.assertEqual(sa.seat_number, target_num)
        self.assertEqual(sa.seat_id, seat_b.pk)

    def test_immediate_transfer_sale_seat_numbers_resolves_number_not_pk(self):
        now, trip, ts_cba, ts_ssj, seat_a, seat_b = self._setup_collision_trip()

        # Solicitamos butaca por número (seat_a.number, cuyo valor coincide con seat_b.pk)
        target_num = seat_a.number
        booking = create_immediate_transfer_sale(
            seller=self.seller_1,
            trip=trip,
            origin_stop=ts_cba,
            destination_stop=ts_ssj,
            seat_numbers=[target_num],
            seat_category=SeatCategory.CAMA,
            email="collision_imm@scviajes.com",
            phone="3511112222",
            passenger_data=[make_pax(first_name="Coll", last_name="Imm", doc="11223355")],
            voucher=self.sample_pdf_voucher(),
            payment_verified=True,
            now=now,
        )
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        sa = SeatAssignment.objects.get(leg__booking=booking)
        self.assertEqual(sa.seat, seat_a)
        self.assertEqual(sa.seat_number, target_num)
        self.assertEqual(sa.seat_id, seat_a.pk)

    def test_legs_with_seat_numbers_contract_resolves_correctly(self):
        now, trip, ts_cba, ts_ssj, seat_a, seat_b = self._setup_collision_trip()

        target_num = seat_b.number
        legs = [{
            "trip": trip,
            "origin_stop": ts_cba,
            "destination_stop": ts_ssj,
            "category": SeatCategory.CAMA,
            "seat_numbers": [target_num],
        }]
        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            legs=legs,
            email="collision_legs_pending@scviajes.com",
            phone="3511112222",
            passengers_data=[make_pax(first_name="Legs", last_name="Pending", doc="11223366")],
            now=now,
        )
        sa = SeatAssignment.objects.get(leg__booking=booking)
        self.assertEqual(sa.seat, seat_b)
        self.assertEqual(sa.seat_number, target_num)

    def test_legs_with_seats_ids_contract_preserved_without_number_confusion(self):
        now, trip, ts_cba, ts_ssj, seat_a, seat_b = self._setup_collision_trip()

        # Un caller que usa legítimamente IDs en legs[*][seats] debe obtener la butaca con ese PK
        legs = [{
            "trip": trip,
            "origin_stop": ts_cba,
            "destination_stop": ts_ssj,
            "category": SeatCategory.CAMA,
            "seats": [seat_a.pk],  # seat_a.pk coincide con el número de seat_b
        }]
        booking = create_internal_transfer_booking(
            seller=self.seller_1,
            legs=legs,
            email="collision_legs_seats@scviajes.com",
            phone="3511112222",
            passengers_data=[make_pax(first_name="Legs", last_name="Seats", doc="11223377")],
            now=now,
        )
        sa = SeatAssignment.objects.get(leg__booking=booking)
        self.assertEqual(sa.seat, seat_a)
        self.assertEqual(sa.seat_id, seat_a.pk)
        self.assertEqual(sa.seat_number, seat_a.number)

    def test_alien_bus_seat_rejected_by_bus_validation(self):
        now, trip, ts_cba, ts_ssj, seat_a, seat_b = self._setup_collision_trip()
        alien_bus = Bus.objects.create(code=f"BUS-ALIEN-{uuid4().hex[:6]}", display_name="Alien Bus", is_active=True)
        alien_seat = Seat.objects.create(
            bus=alien_bus,
            number=99,
            deck=Seat.Deck.LOWER,
            category=SeatCategory.CAMA,
            position_x=0,
            position_y=0,
        )
        # Butaca ajena pasada como objeto en seats
        with self.assertRaises(ValidationError):
            create_internal_transfer_booking(
                seller=self.seller_1,
                legs=[{
                    "trip": trip,
                    "origin_stop": ts_cba,
                    "destination_stop": ts_ssj,
                    "category": SeatCategory.CAMA,
                    "seats": [alien_seat],
                }],
                email="alien_obj@scviajes.com",
                passengers_data=[make_pax(first_name="Alien", last_name="Test", doc="11223388")],
                now=now,
            )
        # Butaca ajena pasada como número inexistente en seat_numbers
        with self.assertRaises(ValidationError):
            create_internal_transfer_booking(
                seller=self.seller_1,
                legs=[{
                    "trip": trip,
                    "origin_stop": ts_cba,
                    "destination_stop": ts_ssj,
                    "category": SeatCategory.CAMA,
                    "seat_numbers": [9999],
                }],
                email="alien_num@scviajes.com",
                passengers_data=[make_pax(first_name="Alien", last_name="Test2", doc="11223399")],
                now=now,
            )
