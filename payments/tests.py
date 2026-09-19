"""Pruebas exhaustivas para el módulo payments."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import io
from pathlib import Path
import shutil
import tempfile
import threading
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from panel.models import AuditEvent
from payments.exceptions import (
    InvalidPaymentStatusError,
    PaymentDuplicateError,
    PaymentError,
    PaymentVoucherError,
)
from payments.models import Payment, PaymentMethod, PaymentStatus
from payments.services import (
    _is_payment_collision_integrity_error,
    calculate_booking_total,
    register_cash_payment,
    register_transfer_payment,
    review_transfer_payment,
)
from payments.storage import (
    ProtectedFileSystemStorage,
    get_voucher_storage,
    validate_voucher_file,
    voucher_upload_path,
)
from sales.exceptions import BookingExpiredError, InvalidBookingError
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


class PaymentsBaseTestCase(TestCase):
    """Configuración base para pruebas de pagos."""

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

        # Grupos de usuarios
        self.admin_group, _ = Group.objects.get_or_create(name="Administrador")
        self.seller_group, _ = Group.objects.get_or_create(name="Vendedor")

        # Usuarios
        self.admin_user = User.objects.create_user(username="admin_user", password="password123")
        self.admin_user.groups.add(self.admin_group)

        self.seller_user = User.objects.create_user(username="seller_user", password="password123")
        self.seller_user.groups.add(self.seller_group)

        self.staff_user = User.objects.create_user(username="staff_only", password="password123", is_staff=True)
        self.common_user = User.objects.create_user(username="common_user", password="password123")
        self.superuser = User.objects.create_superuser(username="super_user", password="password123")

        # Estructura operativa mínima
        self.stop_cba = Stop.objects.create(name="Córdoba Capital", code="CBA")
        self.stop_ssj = Stop.objects.create(name="San Salvador de Jujuy", code="SSJ")

        self.route = Route.objects.create(name="Córdoba - Jujuy", code="CBA-SSJ", is_active=True)
        RouteStop.objects.create(route=self.route, stop=self.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route, stop=self.stop_ssj, sequence=2, allows_boarding=False, allows_alighting=True)

        self.bus = Bus.objects.create(code="BUS-101", is_active=True)
        self.seat_cama_1 = Seat.objects.create(bus=self.bus, number=1, category=SeatCategory.CAMA, deck=Seat.Deck.LOWER, position_x=1, position_y=1, is_active=True)
        self.seat_semi_2 = Seat.objects.create(bus=self.bus, number=2, category=SeatCategory.SEMI_CAMA, deck=Seat.Deck.UPPER, position_x=1, position_y=2, is_active=True)


        now = timezone.now()
        dep = now + timedelta(days=2)
        arr = dep + timedelta(hours=10)

        self.trip = Trip.objects.create(route=self.route, bus=self.bus, departure_at=dep, status=Trip.Status.SCHEDULED)
        self.ts_cba = TripStop.objects.create(trip=self.trip, stop=self.stop_cba, sequence=1, scheduled_at=dep, allows_boarding=True, allows_alighting=False)
        self.ts_ssj = TripStop.objects.create(trip=self.trip, stop=self.stop_ssj, sequence=2, scheduled_at=arr, allows_boarding=False, allows_alighting=True)

        self.fare_cama = TripFare.objects.create(trip=self.trip, origin_stop=self.ts_cba, destination_stop=self.ts_ssj, seat_category=SeatCategory.CAMA, amount=Decimal("15000.00"), is_active=True)
        self.fare_semi = TripFare.objects.create(trip=self.trip, origin_stop=self.ts_cba, destination_stop=self.ts_ssj, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("10000.00"), is_active=True)


    def tearDown(self):
        self.override_settings.disable()
        shutil.rmtree(self.temp_protected_media, ignore_errors=True)
        shutil.rmtree(self.temp_public_media, ignore_errors=True)
        super().tearDown()

    def create_held_manual_booking(self, email="cliente@correo.com", hours_held=24, seats=None):
        """Crea una reserva manual HELD con asignaciones válidas."""
        now = timezone.now()
        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            status=BookingStatus.HELD,
            email=email,
            phone="3511234567",
            seller=self.seller_user,
            expires_at=now + timedelta(hours=hours_held),
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

        selected_seats = seats or [self.seat_cama_1]
        for idx, seat in enumerate(selected_seats, start=1):
            passenger = BookingPassenger.objects.create(
                booking=booking,
                position=idx,
                first_name=f"Pasajero {idx}",
                last_name="Prueba",
                document_type="DNI",
                document_number=f"3000000{idx}",
            )
            fare_amount = self.fare_cama.amount if seat.category == SeatCategory.CAMA else self.fare_semi.amount
            SeatAssignment.objects.create(
                leg=leg,
                passenger=passenger,
                trip=self.trip,
                seat=seat,
                status=AssignmentStatus.HELD,
                seat_number=seat.number,
                category=seat.category,
                price=fare_amount,
                currency="ARS",
            )
        return booking

    def sample_pdf_voucher(self, name="comprobante.pdf", content=b"%PDF-1.4 test voucher content"):
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def sample_image_voucher(self, name="comprobante.jpg", content=b"\xff\xd8\xff test jpg"):
        return SimpleUploadedFile(name, content, content_type="image/jpeg")


class PaymentModelAndStorageTests(PaymentsBaseTestCase):
    """Pruebas del modelo Payment, constraints y almacenamiento desacoplado."""

    def test_payment_creation_and_attributes(self):
        booking = self.create_held_manual_booking()
        payment = Payment.objects.create(
            booking=booking,
            method=PaymentMethod.CASH,
            status=PaymentStatus.APPROVED,
            amount=Decimal("15000.00"),
            currency="ARS",
            registered_by=self.seller_user,
            reviewed_by=self.seller_user,
            reviewed_at=timezone.now(),
        )
        self.assertIsInstance(payment.public_id, uuid.UUID)
        self.assertEqual(payment.currency, "ARS")
        self.assertEqual(payment.amount, Decimal("15000.00"))
        self.assertIn("Efectivo", str(payment))
        self.assertIn("Aprobado", str(payment))

    def test_payment_clean_rejects_float(self):
        booking = self.create_held_manual_booking()
        payment = Payment(
            booking=booking,
            method=PaymentMethod.CASH,
            status=PaymentStatus.APPROVED,
            amount=15000.0,  # float
            currency="ARS",
            registered_by=self.seller_user,
            reviewed_by=self.seller_user,
            reviewed_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as ctx:
            payment.clean_fields()
        self.assertIn("amount", ctx.exception.message_dict)

    def test_rejected_payment_requires_rejection_reason(self):
        booking = self.create_held_manual_booking()
        payment = Payment(
            booking=booking,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.REJECTED,
            amount=Decimal("15000.00"),
            registered_by=self.seller_user,
            reviewed_by=self.admin_user,
            reviewed_at=timezone.now(),
            rejection_reason="",  # vacío
        )
        with self.assertRaises(ValidationError) as ctx:
            payment.clean()
        self.assertIn("rejection_reason", ctx.exception.message_dict)

    def test_conditional_unique_constraint_allows_historical_rejected(self):
        booking = self.create_held_manual_booking()

        # 1. Pago rechazado 1
        p_rej1 = Payment.objects.create(
            booking=booking,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.REJECTED,
            amount=Decimal("15000.00"),
            registered_by=self.seller_user,
            reviewed_by=self.admin_user,
            reviewed_at=timezone.now(),
            rejection_reason="Comprobante borroso",
        )

        # 2. Pago rechazado 2 para la misma reserva: debe permitirse
        p_rej2 = Payment.objects.create(
            booking=booking,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.REJECTED,
            amount=Decimal("15000.00"),
            registered_by=self.seller_user,
            reviewed_by=self.seller_user,
            reviewed_at=timezone.now(),
            rejection_reason="Importe no coincide",
        )
        self.assertNotEqual(p_rej1.pk, p_rej2.pk)

        # 3. Pago en revisión
        p_review = Payment.objects.create(
            booking=booking,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.UNDER_REVIEW,
            amount=Decimal("15000.00"),
            registered_by=self.seller_user,
            voucher=self.sample_pdf_voucher(),
        )
        self.assertIsNotNone(p_review.pk)

        # 4. Segundo pago activo para la misma reserva: debe violar la restricción condicional
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    booking=booking,
                    method=PaymentMethod.CASH,
                    status=PaymentStatus.APPROVED,
                    amount=Decimal("15000.00"),
                    registered_by=self.seller_user,
                    reviewed_by=self.seller_user,
                    reviewed_at=timezone.now(),
                )

    def test_voucher_storage_isolated_from_public_media(self):
        storage = get_voucher_storage()
        self.assertIsInstance(storage, ProtectedFileSystemStorage)
        self.assertEqual(Path(storage.location).resolve(), Path(self.temp_protected_media).resolve())
        self.assertNotEqual(Path(storage.location).resolve(), Path(self.temp_public_media).resolve())
        self.assertIsNone(storage.base_url)

    def test_unpredictable_voucher_filename(self):
        fn1 = voucher_upload_path(None, "mi_comprobante_original.pdf")
        fn2 = voucher_upload_path(None, "mi_comprobante_original.pdf")
        self.assertTrue(fn1.startswith("vouchers/"))
        self.assertTrue(fn1.endswith(".pdf"))
        self.assertNotEqual(fn1, fn2)
        # Longitud hexadecimal de uuid4 es 32 + extensión
        base_name = Path(fn1).stem
        self.assertEqual(len(base_name), 32)

    def test_voucher_validation_allowed_extensions(self):
        for ext in [".pdf", ".jpg", ".jpeg", ".png", ".PDF", ".PNG"]:
            f = SimpleUploadedFile(f"test{ext}", b"data", content_type="application/octet-stream")
            try:
                validate_voucher_file(f)
            except ValidationError:
                self.fail(f"La extensión {ext} debería ser válida.")

    def test_voucher_validation_rejects_webp_and_others(self):
        # WebP explícitamente prohibido por decisión de coordinación
        f_webp = SimpleUploadedFile("comprobante.webp", b"data", content_type="image/webp")
        with self.assertRaises(ValidationError) as ctx:
            validate_voucher_file(f_webp)
        self.assertIn("Solo se admiten archivos PDF, JPG, JPEG o PNG", str(ctx.exception))

        for bad_ext in ["comprobante.exe", "doc.svg", "ticket.html", "script.sh"]:
            f_bad = SimpleUploadedFile(bad_ext, b"data")
            with self.assertRaises(ValidationError):
                validate_voucher_file(f_bad)

    def test_voucher_validation_max_size(self):
        # Configurado a 10 MB
        oversized_bytes = (10 * 1024 * 1024) + 1
        oversized_file = SimpleUploadedFile("grande.pdf", b"x" * 100, content_type="application/pdf")
        oversized_file.size = oversized_bytes

        with self.assertRaises(ValidationError) as ctx:
            validate_voucher_file(oversized_file)
        self.assertIn("supera el tamaño máximo permitido", str(ctx.exception))


class PaymentServicesTests(PaymentsBaseTestCase):
    """Pruebas exhaustivas de register_cash_payment, register_transfer_payment y review_transfer_payment."""

    def test_register_cash_payment_success(self):
        booking = self.create_held_manual_booking()
        self.assertEqual(booking.status, BookingStatus.HELD)

        payment = register_cash_payment(
            booking_or_id=booking,
            seller=self.seller_user,
            reference="Cobro en ventanilla 1",
        )

        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(payment.method, PaymentMethod.CASH)
        self.assertEqual(payment.amount, Decimal("15000.00"))
        self.assertEqual(payment.registered_by, self.seller_user)
        self.assertEqual(payment.reviewed_by, self.seller_user)
        self.assertIsNotNone(payment.reviewed_at)

        # Reserva y butacas confirmadas atómicamente
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertIsNotNone(booking.confirmed_at)
        self.assertEqual(
            booking.legs.first().seat_assignments.first().status,
            AssignmentStatus.CONFIRMED,
        )

        # Auditoría registrada en panel.AuditEvent
        audit_p = AuditEvent.objects.filter(entity_type=Payment._meta.label, entity_id=str(payment.pk))
        self.assertEqual(audit_p.count(), 1)
        self.assertEqual(audit_p.first().action, AuditEvent.Action.CREATE)

        audit_b = AuditEvent.objects.filter(entity_type=booking._meta.label, entity_id=str(booking.pk), action=AuditEvent.Action.UPDATE)
        self.assertTrue(audit_b.exists())

    def test_register_cash_payment_server_side_amount_calculation(self):
        # Reserva con dos butacas (cama 15000 + semicama 10000 = 25000)
        booking = self.create_held_manual_booking(seats=[self.seat_cama_1, self.seat_semi_2])
        self.assertEqual(calculate_booking_total(booking), Decimal("25000.00"))

        payment = register_cash_payment(
            booking_or_id=booking,
            seller=self.seller_user,
        )
        self.assertEqual(payment.amount, Decimal("25000.00"))

    def test_register_cash_payment_rejects_expired_booking(self):
        booking = self.create_held_manual_booking(hours_held=-1)  # ya vencida
        with self.assertRaises(BookingExpiredError):
            register_cash_payment(booking_or_id=booking, seller=self.seller_user)

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        self.assertEqual(booking.legs.first().seat_assignments.first().status, AssignmentStatus.RELEASED)
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 0)

    def test_register_cash_payment_rejects_online_booking(self):
        booking = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email="online@correo.com",
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        with self.assertRaises(InvalidBookingError):
            register_cash_payment(booking_or_id=booking, seller=self.seller_user)

    def test_register_cash_payment_rejects_already_confirmed_or_released(self):
        booking = self.create_held_manual_booking()
        register_cash_payment(booking_or_id=booking, seller=self.seller_user)

        # Intento de segundo cobro sobre reserva ya confirmada
        with self.assertRaises(InvalidBookingError):
            register_cash_payment(booking_or_id=booking, seller=self.seller_user)

    def test_register_transfer_payment_success(self):
        booking = self.create_held_manual_booking()
        voucher = self.sample_pdf_voucher()

        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_user,
            voucher=voucher,
            reference="Transf #987654",
        )

        self.assertEqual(payment.status, PaymentStatus.UNDER_REVIEW)
        self.assertEqual(payment.method, PaymentMethod.BANK_TRANSFER)
        self.assertEqual(payment.amount, Decimal("15000.00"))
        self.assertTrue(payment.voucher)
        self.assertIsNone(payment.reviewed_by)
        self.assertIsNone(payment.reviewed_at)

        # La reserva NO se confirma; sigue HELD
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertIsNone(booking.confirmed_at)
        self.assertEqual(
            booking.legs.first().seat_assignments.first().status,
            AssignmentStatus.HELD,
        )

        # Auditoría registrada
        audit_p = AuditEvent.objects.filter(entity_type=Payment._meta.label, entity_id=str(payment.pk))
        self.assertEqual(audit_p.count(), 1)
        self.assertEqual(audit_p.first().action, AuditEvent.Action.CREATE)

    def test_register_transfer_payment_duplicate_prevention(self):
        booking = self.create_held_manual_booking()
        register_transfer_payment(
            booking_or_id=booking,
            seller=self.seller_user,
            voucher=self.sample_pdf_voucher(),
        )

        # Intento de registrar otra transferencia o efectivo mientras está en revisión
        with self.assertRaises(PaymentDuplicateError):
            register_transfer_payment(
                booking_or_id=booking,
                seller=self.seller_user,
                voucher=self.sample_image_voucher(),
            )

        with self.assertRaises(PaymentDuplicateError):
            register_cash_payment(
                booking_or_id=booking,
                seller=self.seller_user,
            )

    def test_review_transfer_approval_by_seller_and_admin(self):
        # Vendedor aprueba transferencia (decisión confirmada: Vendedor y Administrador pueden)
        booking1 = self.create_held_manual_booking(email="b1@correo.com")
        p1 = register_transfer_payment(booking_or_id=booking1, seller=self.seller_user, voucher=self.sample_pdf_voucher())

        review_transfer_payment(payment_or_id=p1, reviewer=self.seller_user, approved=True)
        p1.refresh_from_db()
        self.assertEqual(p1.status, PaymentStatus.APPROVED)
        self.assertEqual(p1.reviewed_by, self.seller_user)
        booking1.refresh_from_db()
        self.assertEqual(booking1.status, BookingStatus.CONFIRMED)

        # Administrador aprueba transferencia
        booking2 = self.create_held_manual_booking(email="b2@correo.com", seats=[self.seat_semi_2])
        p2 = register_transfer_payment(booking_or_id=booking2, seller=self.seller_user, voucher=self.sample_image_voucher())

        review_transfer_payment(payment_or_id=p2, reviewer=self.admin_user, approved=True)
        p2.refresh_from_db()
        self.assertEqual(p2.status, PaymentStatus.APPROVED)
        self.assertEqual(p2.reviewed_by, self.admin_user)
        booking2.refresh_from_db()
        self.assertEqual(booking2.status, BookingStatus.CONFIRMED)

    def test_review_transfer_approval_when_booking_expired(self):
        """Aprobar bloquea Booking luego Payment, rechaza si vencida sin modificar Payment,
        y vence la reserva/butacas."""
        booking = self.create_held_manual_booking()
        payment = register_transfer_payment(booking_or_id=booking, seller=self.seller_user, voucher=self.sample_pdf_voucher())

        # Vencer la reserva posteriormente a la presentación
        future_now = booking.expires_at + timedelta(minutes=5)

        with self.assertRaises(BookingExpiredError):
            review_transfer_payment(payment_or_id=payment, reviewer=self.admin_user, approved=True, now=future_now)

        # Payment NO debe haber sido modificado a APPROVED
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.UNDER_REVIEW)

        # Booking y butacas deben haberse vencido y liberado
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        self.assertEqual(booking.legs.first().seat_assignments.first().status, AssignmentStatus.RELEASED)

    def test_review_transfer_rejection_requires_reason(self):
        booking = self.create_held_manual_booking()
        payment = register_transfer_payment(booking_or_id=booking, seller=self.seller_user, voucher=self.sample_pdf_voucher())

        with self.assertRaises(ValidationError) as ctx:
            review_transfer_payment(payment_or_id=payment, reviewer=self.admin_user, approved=False, rejection_reason="")
        self.assertIn("rejection_reason", ctx.exception.message_dict)

    def test_review_transfer_rejection_does_not_extend_expiration(self):
        """Rechazar exige motivo y deja Booking HELD si sigue vigente; rechazo no extiende vencimiento."""
        booking = self.create_held_manual_booking()
        original_expires_at = booking.expires_at

        payment = register_transfer_payment(booking_or_id=booking, seller=self.seller_user, voucher=self.sample_pdf_voucher())

        review_transfer_payment(
            payment_or_id=payment,
            reviewer=self.seller_user,
            approved=False,
            rejection_reason="Comprobante con datos ilegibles",
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.REJECTED)
        self.assertEqual(payment.rejection_reason, "Comprobante con datos ilegibles")

        # La reserva sigue HELD con la MISMA fecha de expiración
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertEqual(booking.expires_at, original_expires_at)

        # Auditoría registrada para el rechazo
        audit = AuditEvent.objects.filter(entity_type=Payment._meta.label, entity_id=str(payment.pk), action=AuditEvent.Action.UPDATE)
        self.assertTrue(audit.exists())
        self.assertIn("Rechazo", audit.first().description)

    def test_can_register_new_payment_after_rejection(self):
        """Al rechazarse una transferencia previa, la reserva vigente puede recibir un nuevo pago."""
        booking = self.create_held_manual_booking()
        p1 = register_transfer_payment(booking_or_id=booking, seller=self.seller_user, voucher=self.sample_pdf_voucher())
        review_transfer_payment(payment_or_id=p1, reviewer=self.admin_user, approved=False, rejection_reason="No impactó")

        # Registro de nuevo comprobante de transferencia válido
        p2 = register_transfer_payment(booking_or_id=booking, seller=self.seller_user, voucher=self.sample_image_voucher())
        self.assertEqual(p2.status, PaymentStatus.UNDER_REVIEW)

        # Aprobación del segundo pago
        review_transfer_payment(payment_or_id=p2, reviewer=self.seller_user, approved=True)
        p2.refresh_from_db()
        self.assertEqual(p2.status, PaymentStatus.APPROVED)
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)


class PaymentsPostgresConcurrencyTests(TransactionTestCase):
    """Pruebas concurrentes y de restricción única condicional en PostgreSQL.

    Se omiten automáticamente en SQLite y se ejecutan sin skip en el workflow de PostgreSQL.
    """

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Prueba específica de PostgreSQL (se ejecuta en CI).")

        self.admin_group, _ = Group.objects.get_or_create(name="Administrador")
        self.seller_group, _ = Group.objects.get_or_create(name="Vendedor")

        self.seller = User.objects.create_user(username="pg_seller", password="password123")
        self.seller.groups.add(self.seller_group)

        self.admin = User.objects.create_user(username="pg_admin", password="password123")
        self.admin.groups.add(self.admin_group)

        self.stop1 = Stop.objects.create(name="Parada A", code="PA")
        self.stop2 = Stop.objects.create(name="Parada B", code="PB")
        self.route = Route.objects.create(name="Ruta AB", code="R-AB", is_active=True)
        RouteStop.objects.create(route=self.route, stop=self.stop1, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route, stop=self.stop2, sequence=2, allows_boarding=False, allows_alighting=True)

        self.bus = Bus.objects.create(code="BUS-PG", is_active=True)
        self.seat = Seat.objects.create(bus=self.bus, number=1, category=SeatCategory.CAMA, deck=Seat.Deck.LOWER, position_x=1, position_y=1, is_active=True)


        now = timezone.now()
        self.trip = Trip.objects.create(route=self.route, bus=self.bus, departure_at=now + timedelta(days=1), status=Trip.Status.SCHEDULED)
        self.ts1 = TripStop.objects.create(trip=self.trip, stop=self.stop1, sequence=1, scheduled_at=now + timedelta(days=1), allows_boarding=True, allows_alighting=False)
        self.ts2 = TripStop.objects.create(trip=self.trip, stop=self.stop2, sequence=2, scheduled_at=now + timedelta(days=1, hours=4), allows_boarding=False, allows_alighting=True)
        self.fare = TripFare.objects.create(trip=self.trip, origin_stop=self.ts1, destination_stop=self.ts2, seat_category=SeatCategory.CAMA, amount=Decimal("8000.00"), is_active=True)


        self.booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            status=BookingStatus.HELD,
            email="pg_test@correo.com",
            seller=self.seller,
            expires_at=now + timedelta(hours=24),
        )
        self.leg = BookingLeg.objects.create(
            booking=self.booking,
            sequence=1,
            trip=self.trip,
            origin_stop=self.ts1,
            destination_stop=self.ts2,
            origin_stop_name="Parada A",
            destination_stop_name="Parada B",
            departure_at=self.ts1.scheduled_at,
            arrival_at=self.ts2.scheduled_at,
        )
        self.passenger = BookingPassenger.objects.create(booking=self.booking, position=1, first_name="Juan", last_name="Perez", document_type="DNI", document_number="30111222")
        SeatAssignment.objects.create(
            leg=self.leg,
            passenger=self.passenger,
            trip=self.trip,
            seat=self.seat,
            status=AssignmentStatus.HELD,
            seat_number=1,
            category=SeatCategory.CAMA,
            price=Decimal("8000.00"),
            currency="ARS",
        )

    def test_postgres_conditional_unique_constraint_direct_provocation(self):
        """Provoca directamente la restricción 'payments_active_booking_unique' en PostgreSQL.

        Verifica que PostgreSQL lance IntegrityError con diag.constraint_name exacto,
        y que _is_payment_collision_integrity_error lo detecte para traducir a PaymentDuplicateError.
        """
        p1 = Payment.objects.create(
            booking=self.booking,
            method=PaymentMethod.CASH,
            status=PaymentStatus.APPROVED,
            amount=Decimal("8000.00"),
            registered_by=self.seller,
            reviewed_by=self.seller,
            reviewed_at=timezone.now(),
        )

        p2 = Payment(
            booking=self.booking,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.UNDER_REVIEW,
            amount=Decimal("8000.00"),
            registered_by=self.seller,
        )

        with self.assertRaises(IntegrityError) as ctx:
            with transaction.atomic():
                p2.save()

        exc = ctx.exception
        cause = getattr(exc, "__cause__", None)
        diag = getattr(cause, "diag", None) if cause is not None else getattr(exc, "diag", None)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.constraint_name, "payments_active_booking_unique")
        self.assertTrue(_is_payment_collision_integrity_error(exc))

    def test_postgres_concurrent_cash_payments_serialization(self):
        """Dos transacciones concurrentes intentan registrar pago en efectivo para la misma reserva.
        Gracias al bloqueo select_for_update() en Booking, una se ejecuta primero y la segunda falla
        con InvalidBookingError (o PaymentDuplicateError)."""
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def attempt_payment(worker_name):
            try:
                barrier.wait(timeout=10)
                p = register_cash_payment(booking_or_id=self.booking.pk, seller=self.seller, reference=worker_name)
                results.append(p)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=attempt_payment, args=("worker1",))
        t2 = threading.Thread(target=attempt_payment, args=("worker2",))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(len(results), 1, "Solo un pago en efectivo debe concretarse")
        self.assertEqual(len(errors), 1, "El segundo hilo debe haber fallado")
        self.assertEqual(Payment.objects.filter(booking=self.booking, status=PaymentStatus.APPROVED).count(), 1)
