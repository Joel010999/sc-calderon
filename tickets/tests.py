"""Suite completa de pruebas para la fundación del módulo tickets."""

import hashlib
import io
import os
import secrets
import shutil
import tempfile
import threading
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

import pypdf
import qrcode
from PIL import Image, ImageChops

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from operations.services import schedule_trip
from sales.models import AssignmentStatus, Booking, BookingChannel, BookingLeg, BookingPassenger, BookingStatus, SeatAssignment
from sales.services import create_booking, create_manual_booking
from tickets.exceptions import (
    InvalidTicketError,
    TicketEmailError,
    TicketEmailPendingError,
    TicketNotFoundError,
    TicketVoidError,
)
from tickets.models import (
    EmailAttemptStatus,
    Ticket,
    TicketAuditEvent,
    TicketEmailAttempt,
    TicketStatus,
    mask_document,
)
from tickets.rendering import TicketData, build_ticket_pdf
from tickets.services import (
    build_verification_url,
    generate_ticket_code,
    issue_tickets_for_booking,
    send_booking_tickets,
    void_ticket,
)
from tickets.storage import get_ticket_storage
from cash_register.services import open_cash

User = get_user_model()


class TicketBaseMixin:
    """Utilidades comunes para construir reservas, viajes y butacas de prueba."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.temp_storage_dir = tempfile.mkdtemp()
        self.storage_override = override_settings(
            TICKETS_STORAGE_ROOT=self.temp_storage_dir,
            PROTECTED_MEDIA_ROOT=self.temp_storage_dir,
            TICKETS_VERIFICATION_BASE_URL="https://scviajes.com.ar",
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        )
        self.storage_override.enable()

        # Crear paradas base
        self.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        self.stop_jm = Stop.objects.create(code="JM", name="Jesús María", city="Jesús María", province="Córdoba")
        self.stop_jujuy = Stop.objects.create(code="JUJ", name="San Salvador de Jujuy", city="San Salvador de Jujuy", province="Jujuy")

        # Colectivo con 3 butacas activas (2 semicama, 1 cama)
        self.bus = Bus.objects.create(code="BUS101", display_name="Interno 101", license_plate="AB123CD")
        self.seat_1 = Seat.objects.create(bus=self.bus, number=1, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
        self.seat_2 = Seat.objects.create(bus=self.bus, number=2, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0)
        self.seat_3 = Seat.objects.create(bus=self.bus, number=3, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)

        # Recorrido Ida (Córdoba -> Jujuy)
        self.route_ida = Route.objects.create(name="Córdoba - Jujuy", code="CBA-JUJ-01")
        RouteStop.objects.create(route=self.route_ida, stop=self.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route_ida, stop=self.stop_jm, sequence=2, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route_ida, stop=self.stop_jujuy, sequence=3, allows_boarding=False, allows_alighting=True)

        # Recorrido Vuelta (Jujuy -> Córdoba)
        self.route_vuelta = Route.objects.create(name="Jujuy - Córdoba", code="JUJ-CBA-01")
        RouteStop.objects.create(route=self.route_vuelta, stop=self.stop_jujuy, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route_vuelta, stop=self.stop_cba, sequence=2, allows_boarding=False, allows_alighting=True)

        self.now = timezone.now()
        self.departure_ida = self.now + timedelta(days=5)
        self.arrival_ida = self.departure_ida + timedelta(hours=14)

        self.departure_vuelta = self.departure_ida + timedelta(days=3)
        self.arrival_vuelta = self.departure_vuelta + timedelta(hours=14)

        # Viaje Ida
        self.trip_ida = schedule_trip(
            route=self.route_ida,
            bus=self.bus,
            schedules=[
                (self.stop_cba.pk, self.departure_ida),
                (self.stop_jm.pk, self.departure_ida + timedelta(hours=1)),
                (self.stop_jujuy.pk, self.arrival_ida),
            ],
        )

        self.ts_cba = self.trip_ida.trip_stops.get(stop=self.stop_cba)
        self.ts_jujuy = self.trip_ida.trip_stops.get(stop=self.stop_jujuy)

        # Tarifas Ida
        self.fare_semicama = TripFare.objects.create(
            trip=self.trip_ida,
            seat_category=SeatCategory.SEMI_CAMA,
            amount=Decimal("25000.00"),
        )
        self.fare_cama = TripFare.objects.create(
            trip=self.trip_ida,
            seat_category=SeatCategory.CAMA,
            amount=Decimal("32000.00"),
        )

        # Usuarios y roles
        self.admin_group = Group.objects.create(name="Administrador")
        self.seller_group = Group.objects.create(name="Vendedor")

        self.admin_user = User.objects.create_user("admin_user", "admin@test.com", "pass1234", is_staff=True)
        self.admin_user.groups.add(self.admin_group)

        self.seller_user = User.objects.create_user("seller_user", "seller@test.com", "pass1234")
        self.seller_user.groups.add(self.seller_group)
        open_cash(operator=self.seller_user, opening_amount=Decimal("0.00"))

        self.plain_user = User.objects.create_user("plain_user", "plain@test.com", "pass1234")
        self.staff_no_role = User.objects.create_user("staff_no_role", "staff@test.com", "pass1234", is_staff=True)

    def tearDown(self):
        self.storage_override.disable()
        shutil.rmtree(self.temp_storage_dir, ignore_errors=True)
        super().tearDown()

    def create_confirmed_booking(self, passenger_count=1, round_trip=False, seats_list=None):
        """Crea una reserva confirmada con todas sus asignaciones en CONFIRMED."""
        pax_data = [
            {
                "first_name": f"Pasajero{i}",
                "last_name": f"Prueba{i}",
                "document_type": "DNI",
                "document_number": f"4000000{i}",
                "birth_date": "1995-05-15",
                "nationality": "Argentina",
            }
            for i in range(1, passenger_count + 1)
        ]

        if seats_list is not None:
            seats = seats_list
        else:
            seats = [self.seat_1, self.seat_2, self.seat_3][:passenger_count]

        legs = [
            {
                "trip": self.trip_ida,
                "origin_stop": self.ts_cba,
                "destination_stop": self.ts_jujuy,
                "seats": seats,
            }
        ]

        if round_trip:
            # Colectivo y viaje de vuelta
            self.bus_vuelta = Bus.objects.create(code="BUS102", display_name="Interno 102", license_plate="CD456EF")
            self.seat_v1 = Seat.objects.create(bus=self.bus_vuelta, number=1, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
            self.seat_v2 = Seat.objects.create(bus=self.bus_vuelta, number=2, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0)
            self.seat_v3 = Seat.objects.create(bus=self.bus_vuelta, number=3, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)

            self.trip_vuelta = schedule_trip(
                route=self.route_vuelta,
                bus=self.bus_vuelta,
                schedules=[
                    (self.stop_jujuy.pk, self.departure_vuelta),
                    (self.stop_cba.pk, self.arrival_vuelta),
                ],
            )
            self.ts_v_jujuy = self.trip_vuelta.trip_stops.get(stop=self.stop_jujuy)
            self.ts_v_cba = self.trip_vuelta.trip_stops.get(stop=self.stop_cba)

            TripFare.objects.create(
                trip=self.trip_vuelta,
                seat_category=SeatCategory.SEMI_CAMA,
                amount=Decimal("25000.00"),
            )
            TripFare.objects.create(
                trip=self.trip_vuelta,
                seat_category=SeatCategory.CAMA,
                amount=Decimal("32000.00"),
            )

            seats_v = [self.seat_v1, self.seat_v2, self.seat_v3][:passenger_count]
            legs.append({
                "trip": self.trip_vuelta,
                "origin_stop": self.ts_v_jujuy,
                "destination_stop": self.ts_v_cba,
                "seats": seats_v,
            })

        booking = create_manual_booking(
            seller=self.seller_user,
            email="comprador@scviajes.com.ar",
            phone="3511234567",
            legs=legs,
            passengers_data=pax_data,
            now=self.now,
        )

        # Confirmar reserva y butacas
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = self.now
        booking.save(update_fields=["status", "confirmed_at"])

        SeatAssignment.objects.filter(leg__booking=booking).update(
            status=AssignmentStatus.CONFIRMED,
            updated_at=self.now,
        )

        return booking


class TicketMatrixAndSnapshotTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas de matriz tramos x pasajeros, instantáneas inmutables y validaciones de emisión."""

    def test_single_passenger_one_way_emits_one_ticket(self):
        booking = self.create_confirmed_booking(passenger_count=1, round_trip=False)
        tickets = issue_tickets_for_booking(booking)

        self.assertEqual(len(tickets), 1)
        ticket = tickets[0]
        self.assertEqual(ticket.status, TicketStatus.ISSUED)
        self.assertEqual(ticket.seat_number, 1)
        self.assertEqual(ticket.passenger_name, "Pasajero1 Prueba1")
        self.assertEqual(ticket.passenger_document_masked, "DNI ***0001")
        self.assertEqual(ticket.origin_stop_name, "Córdoba Capital")
        self.assertEqual(ticket.destination_stop_name, "San Salvador de Jujuy")
        self.assertEqual(ticket.price, Decimal("25000.00"))
        self.assertEqual(ticket.currency, "ARS")
        self.assertIsNotNone(ticket.issued_at)
        self.assertTrue(get_ticket_storage().exists(ticket.pdf_path))

    def test_three_passengers_one_way_emits_three_tickets(self):
        booking = self.create_confirmed_booking(passenger_count=3, round_trip=False)
        tickets = issue_tickets_for_booking(booking)

        self.assertEqual(len(tickets), 3)
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 3)
        codes = [t.ticket_code for t in tickets]
        self.assertEqual(len(set(codes)), 3)

    def test_three_passengers_round_trip_emits_six_tickets(self):
        booking = self.create_confirmed_booking(passenger_count=3, round_trip=True)
        tickets = issue_tickets_for_booking(booking)

        self.assertEqual(len(tickets), 6)
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 6)

        # 3 tramo ida, 3 tramo vuelta
        leg_1_tickets = [t for t in tickets if t.leg.sequence == 1]
        leg_2_tickets = [t for t in tickets if t.leg.sequence == 2]
        self.assertEqual(len(leg_1_tickets), 3)
        self.assertEqual(len(leg_2_tickets), 3)

    def test_rejects_held_expired_or_released_booking(self):
        booking = self.create_confirmed_booking(passenger_count=1)

        for invalid_status in [BookingStatus.HELD, BookingStatus.EXPIRED, BookingStatus.RELEASED]:
            booking.status = invalid_status
            booking.save(update_fields=["status"])

            with self.assertRaises(InvalidTicketError):
                issue_tickets_for_booking(booking)

    def test_rejects_if_any_seat_assignment_is_not_confirmed(self):
        booking = self.create_confirmed_booking(passenger_count=2)
        # Poner una butaca en HELD
        assignment = SeatAssignment.objects.filter(leg__booking=booking).first()
        assignment.status = AssignmentStatus.HELD
        assignment.save(update_fields=["status"])

        with self.assertRaises(InvalidTicketError):
            issue_tickets_for_booking(booking)

    def test_snapshots_remain_immutable_after_source_edits(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]

        # 1. Modificar tarifa base en TripFare (no debe afectar el pasaje emitido)
        self.fare_semicama.amount = Decimal("99999.00")
        self.fare_semicama.save()

        # 2. Modificar pasajero fuente
        passenger = booking.passengers.first()
        passenger.first_name = "NombreModificado"
        passenger.save()

        # 3. Modificar parada origen
        leg = booking.legs.first()
        leg.origin_stop_name = "ParadaModificada"
        leg.save()

        # Releer ticket de la base de datos
        ticket.refresh_from_db()
        self.assertEqual(ticket.price, Decimal("25000.00"))
        self.assertEqual(ticket.passenger_name, "Pasajero1 Prueba1")
        self.assertEqual(ticket.origin_stop_name, "Córdoba Capital")

    def test_database_uniqueness_constraints(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]

        # Violación de unicidad (booking, leg, passenger)
        duplicate_ticket = Ticket(
            booking=ticket.booking,
            leg=ticket.leg,
            passenger=ticket.passenger,
            seat_assignment=ticket.seat_assignment,
            ticket_code="TK-DUP-01",
            verification_token_hash="hash_dup_1",
            download_token_hash="hash_dup_2",
            status=TicketStatus.ISSUED,
            pdf_path="tickets/dup.pdf",
            passenger_name=ticket.passenger_name,
            passenger_document_masked=ticket.passenger_document_masked,
            origin_stop_name=ticket.origin_stop_name,
            destination_stop_name=ticket.destination_stop_name,
            departure_at=ticket.departure_at,
            arrival_at=ticket.arrival_at,
            seat_number=ticket.seat_number,
            seat_category=ticket.seat_category,
            seat_category_display=ticket.seat_category_display,
            price=ticket.price,
            currency=ticket.currency,
            booking_public_id=ticket.booking_public_id,
        )

        with self.assertRaises(IntegrityError):
            duplicate_ticket.save()

    def test_cross_relations_are_rejected(self):
        booking1 = self.create_confirmed_booking(passenger_count=1, seats_list=[self.seat_1])
        booking2 = self.create_confirmed_booking(passenger_count=1, seats_list=[self.seat_2])

        # Alterar asignación para apuntar a un pasajero de la otra reserva
        assignment = SeatAssignment.objects.filter(leg__booking=booking1).first()
        assignment.passenger = booking2.passengers.first()
        assignment.save()

        with self.assertRaises(InvalidTicketError):
            issue_tickets_for_booking(booking1)


class TicketAtomicityAndCleanupTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas de atomicidad estricta y eliminación garantizada de PDFs creados ante cualquier fallo."""

    def test_rejects_execution_inside_active_outer_transaction(self):
        booking = self.create_confirmed_booking(passenger_count=1)

        with transaction.atomic():
            with self.assertRaises(InvalidTicketError) as ctx:
                issue_tickets_for_booking(booking)
            self.assertIn("top-level fuera de bloques atómicos activos", str(ctx.exception))

    def test_failure_on_second_pdf_rolls_back_db_and_deletes_first_pdf(self):
        booking = self.create_confirmed_booking(passenger_count=2)

        original_build = build_ticket_pdf
        call_count = [0]

        def fail_on_second_call(data):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Fallo simulado al renderizar el segundo pasaje")
            return original_build(data)

        with patch("tickets.services.build_ticket_pdf", side_effect=fail_on_second_call):
            with self.assertRaises(RuntimeError):
                issue_tickets_for_booking(booking)

        # Verificar que la DB no tiene pasajes guardados
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 0)

        # Verificar que la carpeta de almacenamiento no tiene archivos huérfanos
        files_in_storage = os.listdir(os.path.join(self.temp_storage_dir, "tickets")) if os.path.exists(os.path.join(self.temp_storage_dir, "tickets")) else []
        self.assertEqual(len(files_in_storage), 0)

    def test_failure_on_audit_creation_rolls_back_db_and_deletes_pdfs(self):
        booking = self.create_confirmed_booking(passenger_count=1)

        with patch.object(TicketAuditEvent.objects, "create", side_effect=RuntimeError("Fallo simulado en auditoría")):
            with self.assertRaises(RuntimeError):
                issue_tickets_for_booking(booking)

        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 0)
        files_in_storage = os.listdir(os.path.join(self.temp_storage_dir, "tickets")) if os.path.exists(os.path.join(self.temp_storage_dir, "tickets")) else []
        self.assertEqual(len(files_in_storage), 0)

    def test_idempotent_reissuance_returns_existing_tickets_without_new_files(self):
        booking = self.create_confirmed_booking(passenger_count=2)
        tickets_1 = issue_tickets_for_booking(booking)
        self.assertEqual(len(tickets_1), 2)

        files_1 = sorted(os.listdir(os.path.join(self.temp_storage_dir, "tickets")))

        # Segunda llamada
        tickets_2 = issue_tickets_for_booking(booking)
        self.assertEqual(len(tickets_2), 2)
        self.assertEqual([t.pk for t in tickets_1], [t.pk for t in tickets_2])

        files_2 = sorted(os.listdir(os.path.join(self.temp_storage_dir, "tickets")))
        self.assertEqual(files_1, files_2)

    def test_booking_lock_query_uses_for_update_of_self_with_nullable_seller_join(self):
        from django.db.backends.postgresql.base import DatabaseWrapper

        booking = self.create_confirmed_booking(passenger_count=1)
        captured_ofs = []
        original_select_for_update = Booking.objects.select_for_update

        def spy_select_for_update(*args, **kwargs):
            captured_ofs.append(kwargs.get("of"))
            return original_select_for_update(*args, **kwargs)

        with patch.object(Booking.objects, "select_for_update", side_effect=spy_select_for_update):
            tickets = issue_tickets_for_booking(booking)

        self.assertEqual(len(tickets), 1)
        self.assertEqual(captured_ofs, [("self",)])

        pg_wrapper = DatabaseWrapper(
            {"ENGINE": "django.db.backends.postgresql", "NAME": "scviajes_ci", "TIME_ZONE": None},
            alias="pg_contract",
        )
        pg_wrapper.in_atomic_block = True
        pg_wrapper.get_autocommit = lambda: False
        lock_qs = (
            Booking.objects.select_for_update(of=("self",))
            .select_related("seller")
            .filter(pk=booking.pk)
        )
        lock_qs.query.clear_ordering(force=True)
        compiled_sql, _ = lock_qs.query.get_compiler(connection=pg_wrapper).as_sql()
        self.assertIn("LEFT OUTER JOIN", compiled_sql)
        self.assertIn('FOR UPDATE OF "sales_booking"', compiled_sql)
        self.assertFalse(compiled_sql.rstrip().endswith("FOR UPDATE"))

    def test_reissuance_rejected_if_any_ticket_is_void(self):
        booking = self.create_confirmed_booking(passenger_count=2)
        tickets = issue_tickets_for_booking(booking)

        # Anular un pasaje
        void_ticket(tickets[0], reason="Cancelación operativa de butaca")

        with self.assertRaises(TicketVoidError):
            issue_tickets_for_booking(booking)


class TicketPdfAndQrParsingTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas de renderizado PDF, parseo de texto con PyPDF y validación estructural de QR puro Python."""

    def test_pdf_content_accents_and_masked_document(self):
        # Crear reserva con nombre con acentos
        booking = self.create_confirmed_booking(passenger_count=1)
        passenger = booking.passengers.first()
        passenger.first_name = "María Belén"
        passenger.last_name = "González-Pérez"
        passenger.document_number = "38123456"
        passenger.save()

        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]

        storage = get_ticket_storage()
        with storage.open(ticket.pdf_path, "rb") as f:
            pdf_bytes = f.read()

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        self.assertGreaterEqual(len(reader.pages), 2)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)

        # Comprobaciones de contenido obligatorio
        self.assertIn("SC VIAJES", text)
        self.assertIn("PASAJE ELECTRÓNICO", text)
        self.assertIn(ticket.ticket_code, text)
        self.assertIn(ticket.passenger_name, text)
        self.assertIn("DNI ***3456", text)
        self.assertIn("Córdoba Capital", text)
        self.assertIn("San Salvador de Jujuy", text)
        self.assertNotIn("Plantilla provisional reemplazable sin cambiar dominio", text)
        self.assertIn("CONDICIONES DEL PASAJE", text)
        self.assertIn("Presentarse con documento de identidad para abordar", text)

        # Privacidad: el documento completo NO debe figurar en el texto del PDF
        self.assertNotIn("38123456", text)

    def test_embedded_qr_code_decodes_trusted_verification_url(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]

        raw_v_token = ticket._raw_verification_token
        self.assertIsNotNone(raw_v_token)

        storage = get_ticket_storage()
        with storage.open(ticket.pdf_path, "rb") as f:
            pdf_bytes = f.read()

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        page = reader.pages[0]
        self.assertTrue(len(page.images) >= 1)

        qr_img_data = page.images[0].data
        qr_image = Image.open(io.BytesIO(qr_img_data))

        # 1. Validación estructural de formato, dimensiones y aspecto cuadrado
        self.assertEqual(qr_image.format, "PNG")
        self.assertEqual(qr_image.width, qr_image.height)
        self.assertGreaterEqual(qr_image.width, 100)

        # 2. Validación de contraste y presencia de módulos claros y oscuros
        grayscale = qr_image.convert("L")
        extrema = grayscale.getextrema()
        self.assertLess(extrema[0], 50)
        self.assertGreater(extrema[1], 200)

        # 3. Validación estructural exacta contra la matriz QR esperada del enlace de verificación
        expected_url = f"https://scviajes.com.ar/tickets/verify/?token={raw_v_token}"
        ref_qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=1,
        )
        ref_qr.add_data(expected_url)
        ref_qr.make(fit=True)
        ref_img = ref_qr.make_image(fill_color="black", back_color="white")
        ref_pil = (ref_img._img if hasattr(ref_img, "_img") else ref_img).convert("RGB")

        # La imagen extraída debe coincidir exactamente con la generada para el payload esperado
        diff = ImageChops.difference(qr_image.convert("RGB"), ref_pil)
        self.assertIsNone(
            diff.getbbox(),
            "La imagen QR extraída del PDF difiere estructuralmente de la generada para el enlace de verificación.",
        )

        # 4. Verificación negativa: un token diferente altera la estructura de la imagen
        tampered_qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=1,
        )
        tampered_qr.add_data(f"https://scviajes.com.ar/tickets/verify/?token=token_alterado_{raw_v_token}")
        tampered_qr.make(fit=True)
        tampered_img = tampered_qr.make_image(fill_color="black", back_color="white")
        tampered_pil = (tampered_img._img if hasattr(tampered_img, "_img") else tampered_img).convert("RGB")
        diff_tampered = ImageChops.difference(qr_image.convert("RGB"), tampered_pil)
        self.assertIsNotNone(
            diff_tampered.getbbox(),
            "Una variación en el token debería alterar la estructura de la imagen QR.",
        )


    def test_long_passenger_fields_wrap_and_keep_branded_logo(self):
        data = TicketData(
            ticket_code="SYN-LONG-001",
            booking_code="RES-LONG-001",
            passenger_name="Nombre sintético deliberadamente extenso para verificar el ajuste de líneas",
            masked_document="DNI ***999",
            origin_stop_name="Córdoba - Terminal nueva de denominación extensa",
            destination_stop_name="San Salvador de Jujuy - Terminal de ómnibus",
            travel_date="26/10/2026",
            departure_time="22:30",
            boarding_place="Plataforma sintética número 17 con una referencia larga",
            arrival_time="07:15",
            arrival_date="27/10/2026",
            seat_number="12",
            category_display="Cama",
            price_display="$ 55.000,00",
            issued_at="26/10/2026 10:30",
            qr_payload_url="https://scviajes.test/tickets/verify/?token=SYN-LONG-001",
            total_amount_display="$ 55.000,00",
            fare_detail_display="$ 55.000,00",
            carrier_legal_name="Transportista sintético con una razón social extensa S.A.",
        )
        reader = pypdf.PdfReader(io.BytesIO(build_ticket_pdf(data)))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertNotIn("…", text)
        self.assertIn("CONDICIONES DEL PASAJE", text)
        self.assertTrue(len(reader.pages[0].images) >= 1)


class TicketVerificationViewTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas para el endpoint público de verificación por código QR."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_valid_ticket_verification_and_privacy_guarantees(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]
        raw_token = ticket._raw_verification_token

        response = self.client.get(f"/tickets/verify/?token={raw_token}")
        self.assertEqual(response.status_code, 200)

        # Cabeceras de seguridad
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIn("noindex", response["X-Robots-Tag"])

        html = response.content.decode("utf-8")
        self.assertIn("Pasaje Válido", html)
        self.assertIn(ticket.ticket_code, html)
        self.assertIn(ticket.passenger_name, html)
        self.assertIn(ticket.passenger_document_masked, html)
        self.assertIn(ticket.origin_stop_name, html)
        self.assertIn(ticket.destination_stop_name, html)
        self.assertIn(timezone.localtime(ticket.departure_at).strftime("%d/%m/%Y"), html)
        self.assertIn(str(ticket.seat_number), html)
        self.assertIn(ticket.seat_category_display, html)

        # Garantías estrictas de privacidad: NUNCA motivo, llegada, precio, email, teléfono, doc completo, IDs internos o pago
        self.assertNotIn("Llegada Estimada", html)
        self.assertNotIn("Motivo de Anulación", html)
        self.assertNotIn("25000", html)
        self.assertNotIn("comprador@scviajes.com.ar", html)
        self.assertNotIn("3511234567", html)
        self.assertNotIn("40000001", html)
        self.assertNotIn(f"id=\"{ticket.id}\"", html)

    def test_void_ticket_verification(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]
        raw_token = ticket._raw_verification_token

        void_ticket(ticket, reason="Fuerza mayor por corte de ruta")

        response = self.client.get(f"/tickets/verify/?token={raw_token}")
        self.assertEqual(response.status_code, 200)

        html = response.content.decode("utf-8")
        self.assertIn("Pasaje Anulado", html)
        self.assertNotIn("Pasaje Válido", html)

        # La página pública de verificación muestra exclusivamente los datos autorizados:
        self.assertIn(ticket.ticket_code, html)
        self.assertIn(ticket.passenger_name, html)
        self.assertIn(ticket.passenger_document_masked, html)
        self.assertIn(ticket.origin_stop_name, html)
        self.assertIn(ticket.destination_stop_name, html)
        self.assertIn(timezone.localtime(ticket.departure_at).strftime("%d/%m/%Y"), html)
        self.assertIn(str(ticket.seat_number), html)
        self.assertIn(ticket.seat_category_display, html)

        # NO debe mostrar motivo de anulación ni datos fuera de los autorizados
        self.assertNotIn("Fuerza mayor por corte de ruta", html)
        self.assertNotIn(ticket.void_reason, html)
        self.assertNotIn("Motivo de Anulación", html)
        self.assertNotIn("Llegada Estimada", html)

        # Garantías estrictas de privacidad y PII
        self.assertNotIn("25000", html)
        self.assertNotIn("comprador@scviajes.com.ar", html)
        self.assertNotIn("3511234567", html)
        self.assertNotIn("40000001", html)
        self.assertNotIn(f"id=\"{ticket.id}\"", html)

    def test_invalid_or_missing_token_verification(self):
        response = self.client.get("/tickets/verify/?token=invalido123")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn("Pasaje No Encontrado", html)

        response_empty = self.client.get("/tickets/verify/")
        self.assertEqual(response_empty.status_code, 200)
        html_empty = response_empty.content.decode("utf-8")
        self.assertIn("Pasaje No Encontrado", html_empty)

    def test_public_qr_get_does_not_create_audit_event(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        ticket = issue_tickets_for_booking(booking)[0]
        before = TicketAuditEvent.objects.filter(ticket=ticket).count()

        for _ in range(3):
            response = self.client.get(f"/tickets/verify/?token={ticket._raw_verification_token}")
            self.assertEqual(response.status_code, 200)

        self.assertEqual(TicketAuditEvent.objects.filter(ticket=ticket).count(), before)

    @override_settings(TICKETS_RATE_LIMIT_PER_MINUTE=3)
    def test_rate_limiting_by_hashed_ip(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        raw_token = tickets[0]._raw_verification_token

        # 3 solicitudes permitidas
        for _ in range(3):
            res = self.client.get(f"/tickets/verify/?token={raw_token}", REMOTE_ADDR="192.168.1.50")
            self.assertEqual(res.status_code, 200)

        # 4ta solicitud bloqueada con 429
        res_blocked = self.client.get(f"/tickets/verify/?token={raw_token}", REMOTE_ADDR="192.168.1.50")
        self.assertEqual(res_blocked.status_code, 429)

        # Otra IP distinta no debe estar bloqueada
        res_other = self.client.get(f"/tickets/verify/?token={raw_token}", REMOTE_ADDR="192.168.1.51")
        self.assertEqual(res_other.status_code, 200)


class TicketDownloadAccessTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas de descarga con control de acceso por roles, token bearer seguro y mitigación de IDOR."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_authorized_internal_roles_can_download(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]

        # Administrador
        self.client.force_login(self.admin_user)
        res_admin = self.client.get(f"/tickets/download/{ticket.public_id}/")
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin["Content-Type"], "application/pdf")
        self.assertIn(f"pasaje-{ticket.ticket_code}.pdf", res_admin["Content-Disposition"])

        # Vendedor
        self.client.force_login(self.seller_user)
        res_seller = self.client.get(f"/tickets/download/{ticket.public_id}/")
        self.assertEqual(res_seller.status_code, 200)

    def test_common_user_and_staff_without_role_receive_403(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]

        # Usuario común
        self.client.force_login(self.plain_user)
        res_plain = self.client.get(f"/tickets/download/{ticket.public_id}/")
        self.assertEqual(res_plain.status_code, 403)

        # Staff sin rol
        self.client.force_login(self.staff_no_role)
        res_staff = self.client.get(f"/tickets/download/{ticket.public_id}/")
        self.assertEqual(res_staff.status_code, 403)

    def test_public_user_with_download_token_can_download(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]
        raw_download_token = ticket._raw_download_token

        # Vía query param ?token=
        res = self.client.get(f"/tickets/download/{ticket.public_id}/?token={raw_download_token}")
        self.assertEqual(res.status_code, 200)

        # Vía cabecera Authorization: Bearer <token>
        res_bearer = self.client.get(
            f"/tickets/download/{ticket.public_id}/",
            HTTP_AUTHORIZATION=f"Bearer {raw_download_token}",
        )
        self.assertEqual(res_bearer.status_code, 200)

    def test_authorized_download_get_does_not_create_audit_event(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        ticket = issue_tickets_for_booking(booking)[0]
        before = TicketAuditEvent.objects.filter(ticket=ticket).count()

        response = self.client.get(f"/tickets/download/{ticket.public_id}/?token={ticket._raw_download_token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TicketAuditEvent.objects.filter(ticket=ticket).count(), before)

    def test_qr_verification_token_cannot_download_pdf(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]
        raw_v_token = ticket._raw_verification_token

        # Usar el token de verificación QR para intentar descargar el PDF debe dar 404
        res = self.client.get(f"/tickets/download/{ticket.public_id}/?token={raw_v_token}")
        self.assertEqual(res.status_code, 404)

    def test_missing_physical_file_returns_404_not_500(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        tickets = issue_tickets_for_booking(booking)
        ticket = tickets[0]

        # Borrar el archivo físico del storage
        get_ticket_storage().delete(ticket.pdf_path)

        self.client.force_login(self.admin_user)
        res = self.client.get(f"/tickets/download/{ticket.public_id}/")
        self.assertEqual(res.status_code, 404)


class TicketEmailGroupedTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas de envío agrupado por correo, idempotencia, reintentos y auditoría."""

    def test_send_single_email_with_all_ticket_pdfs(self):
        booking = self.create_confirmed_booking(passenger_count=3, round_trip=False)
        attempt = send_booking_tickets(booking)

        self.assertEqual(attempt.status, EmailAttemptStatus.SENT)
        self.assertEqual(attempt.recipient_email, "comprador@scviajes.com.ar")

        # Se envió exactamente UN correo
        self.assertEqual(len(mail.outbox), 1)
        sent_msg = mail.outbox[0]
        self.assertEqual(sent_msg.to, ["comprador@scviajes.com.ar"])
        self.assertIn("Tus pasajes de SC Viajes", sent_msg.subject)

        # 3 pasajes adjuntos
        self.assertEqual(len(sent_msg.attachments), 3)
        for att in sent_msg.attachments:
            self.assertTrue(att[0].endswith(".pdf"))
            self.assertEqual(att[2], "application/pdf")

    def test_email_idempotency_does_not_resend_if_already_sent(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        attempt_1 = send_booking_tickets(booking)
        self.assertEqual(attempt_1.status, EmailAttemptStatus.SENT)
        self.assertEqual(len(mail.outbox), 1)

        # Segunda llamada
        attempt_2 = send_booking_tickets(booking)
        self.assertEqual(attempt_2.pk, attempt_1.pk)
        # Sigue habiendo solo 1 correo en la bandeja
        self.assertEqual(len(mail.outbox), 1)

    def test_pending_concurrency_blocks_new_sends(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        issue_tickets_for_booking(booking)

        # Crear un intento PENDING previo
        TicketEmailAttempt.objects.create(
            booking=booking,
            status=EmailAttemptStatus.PENDING,
            recipient_email=booking.email,
        )

        with self.assertRaises(TicketEmailPendingError):
            send_booking_tickets(booking)

    def test_failed_send_requires_explicit_retry(self):
        booking = self.create_confirmed_booking(passenger_count=1)

        # Simular fallo en envío de correo
        with patch("django.core.mail.EmailMessage.send", side_effect=RuntimeError("SMTP connection timeout")):
            with self.assertRaises(TicketEmailError):
                send_booking_tickets(booking)

        attempt = TicketEmailAttempt.objects.filter(booking=booking).first()
        self.assertEqual(attempt.status, EmailAttemptStatus.FAILED)
        self.assertIn("Error al conectar con el servidor de correo", attempt.error_message)
        # NUNCA str(exception) sin sanitizar
        self.assertNotIn("SMTP connection timeout", attempt.error_message)

        # Llamar sin retry=True debe fallar
        with self.assertRaises(TicketEmailError) as ctx:
            send_booking_tickets(booking, retry=False)
        self.assertIn("retry=True", str(ctx.exception))

        # Llamar con retry=True debe reintentar y tener éxito
        successful_attempt = send_booking_tickets(booking, retry=True)
        self.assertEqual(successful_attempt.status, EmailAttemptStatus.SENT)
        self.assertEqual(len(mail.outbox), 1)


class TicketAuditTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas para verificar que la auditoría no expone PII ni tokens raw."""

    def test_audit_records_do_not_contain_pii(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        issue_tickets_for_booking(booking)
        send_booking_tickets(booking)

        events = TicketAuditEvent.objects.filter(booking=booking)
        self.assertTrue(events.exists())

        for ev in events:
            meta = ev.metadata
            meta_str = str(meta).lower()

            # Sin números de documento completos ni teléfonos ni correos en metadata
            self.assertNotIn("40000001", meta_str)
            self.assertNotIn("3511234567", meta_str)
            self.assertNotIn("token", meta)
            self.assertNotIn("raw", meta_str)


class TicketsPostgresConcurrencyTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas de emisión concurrente en PostgreSQL."""

    def test_concurrent_issuance_threads_produce_exact_tickets(self):
        if connection.vendor != "postgresql":
            self.skipTest("Prueba específica para PostgreSQL.")

        from django.db import close_old_connections, connections

        booking = self.create_confirmed_booking(passenger_count=2)
        errors = []
        results = []

        def worker():
            try:
                close_old_connections()
                # Cada hilo utiliza su propia conexión
                t = issue_tickets_for_booking(booking)
                results.append(t)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        self.assertEqual(
            errors,
            [],
            f"La emisión concurrente en PostgreSQL no debe lanzar excepciones: {errors!r}",
        )
        self.assertEqual(len(results), 3)

        # Debe haber exactamente 2 pasajes en la DB sin duplicados
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 2)

        # Todos los hilos que terminaron deben haber recibido 2 pasajes (idempotencia)
        for r in results:
            self.assertEqual(len(r), 2)



class TicketReusableDraftAndScheduleSnapshotTests(TicketBaseMixin, TransactionTestCase):
    """Pruebas del borrador reutilizable del pasaje PDF (dos talones), horarios por parada y snapshots."""

    def _extract_pdf_text(self, ticket: Ticket) -> str:
        storage = get_ticket_storage()
        with storage.open(ticket.pdf_path, "rb") as f:
            pdf_bytes = f.read()
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        self.assertGreaterEqual(len(reader.pages), 2)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def test_pdf_two_stubs_agency_vs_carrier_and_no_reference_data_leak(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        ticket = issue_tickets_for_booking(booking)[0]
        text = self._extract_pdf_text(ticket)

        # Ambos talones presentes en el borrador reutilizable
        self.assertIn("TALÓN SUPERIOR", text)
        self.assertIn("TALÓN PARA EL PASAJERO", text)

        # SC Viajes como agencia y empresa transportista separada
        self.assertIn("Agencia: SC VIAJES", text)
        self.assertIn("Empresa transportista: Pendiente de confirmación con Sandro", text)

        # Datos fiscales y de seguro fijos pendientes de Sandro (no pedidos por venta ni inventados)
        self.assertIn("Razón social: Pendiente de confirmación", text)
        self.assertIn("CUIT: Pendiente de confirmación", text)
        self.assertIn("IVA: Pendiente de confirmación", text)
        self.assertIn("Inicio de actividades: Pendiente de", text)
        self.assertIn("IIBB: Pendiente de confirmación", text)
        self.assertIn("Usted viaja asegurado por: Pendiente de", text)
        self.assertIn("Póliza/Ref.: Pendiente de confirmación", text)

        # Prohibición estricta: nunca usar datos fiscales, números, códigos, direcciones ni tasas del PDF de referencia
        forbidden_reference_literals = [
            "GRAL URQUIZA",
            "GENERAL URQUIZA",
            "SUV-212798751-0",
            "SCX001",
            "30-54622766-5",
            "908-30716745445",
            "Protección Mutual",
            "44945021",
            "Calle 4 y 41",
            "Peron 380",
            "Perón 380",
            "Tasa Emb",
            "Cortesia",
            "Cortesía",
        ]
        for literal in forbidden_reference_literals:
            self.assertNotIn(literal, text)

    def test_web_sale_omits_seller_while_manual_sale_includes_seller_and_real_payment_method(self):
        from payments.models import Payment, PaymentMethod, PaymentStatus

        # 1. Venta web (ONLINE) sin vendedor y con transferencia bancaria aprobada
        online_booking = create_booking(
            channel=BookingChannel.ONLINE,
            email="web@scviajes.com.ar",
            phone="3519998888",
            legs=[
                {
                    "trip": self.trip_ida,
                    "origin_stop": self.ts_cba,
                    "destination_stop": self.ts_jujuy,
                    "seats": [self.seat_1],
                }
            ],
            passengers_data=[
                {
                    "first_name": "Lucía",
                    "last_name": "Fernández",
                    "document_type": "DNI",
                    "document_number": "41222333",
                    "birth_date": "1996-08-20",
                    "nationality": "Argentina",
                }
            ],
            now=self.now,
        )
        online_booking.status = BookingStatus.CONFIRMED
        online_booking.confirmed_at = self.now
        online_booking.save(update_fields=["status", "confirmed_at"])
        SeatAssignment.objects.filter(leg__booking=online_booking).update(
            status=AssignmentStatus.CONFIRMED,
            updated_at=self.now,
        )
        Payment.objects.create(
            booking=online_booking,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.APPROVED,
            amount=Decimal("25000.00"),
            currency="ARS",
            reviewed_by=self.admin_user,
            reviewed_at=self.now,
        )

        online_ticket = issue_tickets_for_booking(online_booking)[0]
        online_text = self._extract_pdf_text(online_ticket)

        # En venta web jamás debe aparecer un vendedor
        self.assertNotIn("Vendedor", online_text)
        self.assertIn("Formas de pago: Transferencia bancaria", online_text)
        self.assertIn("Detalle: Tarifa $ 25.000,00", online_text)
        self.assertIn("Importe total: $ 25.000,00", online_text)

        # 2. Venta manual (MANUAL) con vendedor y pago en efectivo aprobado
        manual_booking = self.create_confirmed_booking(
            passenger_count=1,
            seats_list=[self.seat_2],
        )
        Payment.objects.create(
            booking=manual_booking,
            method=PaymentMethod.CASH,
            status=PaymentStatus.APPROVED,
            amount=Decimal("25000.00"),
            currency="ARS",
            registered_by=self.seller_user,
            reviewed_by=self.seller_user,
            reviewed_at=self.now,
        )
        manual_ticket = issue_tickets_for_booking(manual_booking)[0]
        manual_text = self._extract_pdf_text(manual_ticket)

        self.assertIn("Vendedor: seller_user", manual_text)
        self.assertIn("Formas de pago: Efectivo", manual_text)
        self.assertIn("Importe total: $ 25.000,00", manual_text)

    def test_boarding_departure_destination_arrival_midnight_crossing_and_emitted_pdf_preservation(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from operations.services import update_trip_schedules

        tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
        night_cba = datetime(2030, 5, 10, 22, 45, tzinfo=tz_ar)
        night_jm = datetime(2030, 5, 10, 23, 35, tzinfo=tz_ar)
        morning_jujuy = datetime(2030, 5, 11, 8, 20, tzinfo=tz_ar)

        update_trip_schedules(
            trip=self.trip_ida,
            schedules=[
                (self.stop_cba.pk, night_cba),
                (self.stop_jm.pk, night_jm),
                (self.stop_jujuy.pk, morning_jujuy),
            ],
        )

        ts_jm = self.trip_ida.trip_stops.get(stop=self.stop_jm)
        ts_jujuy = self.trip_ida.trip_stops.get(stop=self.stop_jujuy)

        booking = create_manual_booking(
            seller=self.seller_user,
            email="intermedia@scviajes.com.ar",
            phone="3525112233",
            legs=[
                {
                    "trip": self.trip_ida,
                    "origin_stop": ts_jm,
                    "destination_stop": ts_jujuy,
                    "seats": [self.seat_1],
                }
            ],
            passengers_data=[
                {
                    "first_name": "Carlos",
                    "last_name": "Sánchez",
                    "document_type": "DNI",
                    "document_number": "35999111",
                    "birth_date": "1990-03-10",
                    "nationality": "Argentina",
                }
            ],
            now=self.now,
        )
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = self.now
        booking.save(update_fields=["status", "confirmed_at"])
        SeatAssignment.objects.filter(leg__booking=booking).update(
            status=AssignmentStatus.CONFIRMED,
            updated_at=self.now,
        )

        ticket = issue_tickets_for_booking(booking)[0]
        storage = get_ticket_storage()
        with storage.open(ticket.pdf_path, "rb") as f:
            original_pdf_bytes = f.read()

        text = self._extract_pdf_text(ticket)
        # Muestra la salida correspondiente al embarque (Jesús María 10/05/2030 23:35, no 22:45 de Córdoba)
        self.assertIn("Sale de / Sube en: Jesús María", text)
        self.assertIn("Salida: 10/05/2030 - 23:35", text)
        self.assertNotIn("22:45", text)
        # Muestra la llegada estimada correspondiente al destino en el día siguiente (11/05/2030 08:20)
        self.assertIn("A / Baja en: San Salvador de Jujuy", text)
        self.assertIn("Arribo estimado: 11/05/2030 - 08:20", text)

        # Modificar posteriormente los horarios estimados de la salida NO debe recalcular ni sobrescribir el PDF emitido
        update_trip_schedules(
            trip=self.trip_ida,
            schedules=[
                (self.stop_cba.pk, night_cba + timedelta(hours=1)),
                (self.stop_jm.pk, night_jm + timedelta(hours=1)),
                (self.stop_jujuy.pk, morning_jujuy + timedelta(hours=2)),
            ],
        )

        ticket.refresh_from_db()
        self.assertEqual(ticket.departure_at, night_jm)
        self.assertEqual(ticket.arrival_at, morning_jujuy)

        reissued = issue_tickets_for_booking(booking)[0]
        self.assertEqual(reissued.pk, ticket.pk)
        with storage.open(ticket.pdf_path, "rb") as f:
            after_pdf_bytes = f.read()
        self.assertEqual(after_pdf_bytes, original_pdf_bytes)

    def test_lap_infant_without_seat_is_preserved_in_pdf_without_fake_seat(self):
        booking = create_manual_booking(
            seller=self.seller_user,
            email="familia@scviajes.com.ar",
            phone="3514445566",
            legs=[
                {
                    "trip": self.trip_ida,
                    "origin_stop": self.ts_cba,
                    "destination_stop": self.ts_jujuy,
                    "seats": [self.seat_3],
                }
            ],
            passengers_data=[
                {
                    "first_name": "Laura",
                    "last_name": "Gómez",
                    "document_type": "DNI",
                    "document_number": "32111222",
                    "birth_date": "1992-04-12",
                    "nationality": "Argentina",
                    "gender": "F",
                },
                {
                    "first_name": "Mateo",
                    "last_name": "Gómez",
                    "document_type": "DNI",
                    "document_number": "58111222",
                    "birth_date": (self.departure_ida.date() - timedelta(days=365)).isoformat(),
                    "nationality": "Argentina",
                    "accompanied_by_position": 1,
                },
            ],
            now=self.now,
        )
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = self.now
        booking.save(update_fields=["status", "confirmed_at"])
        SeatAssignment.objects.filter(leg__booking=booking).update(
            status=AssignmentStatus.CONFIRMED,
            updated_at=self.now,
        )

        tickets = issue_tickets_for_booking(booking)
        self.assertEqual(len(tickets), 2)

        infant_ticket = next(t for t in tickets if t.passenger.is_lap_infant)
        self.assertIsNone(infant_ticket.seat_number)
        self.assertEqual(infant_ticket.seat_category, "LAP_INFANT")
        self.assertEqual(infant_ticket.seat_category_display, "Sin butaca")
        self.assertEqual(infant_ticket.price, Decimal("0.00"))

        infant_text = self._extract_pdf_text(infant_ticket)
        self.assertIn("Categoría / Butaca: Sin butaca - Butaca: Sin butaca", infant_text)
        self.assertIn("Adulto acompañante: Laura Gómez", infant_text)
        self.assertIn("Detalle: Tarifa $ 0,00 (Menor sin butaca)", infant_text)
        self.assertIn("Importe total: $ 0,00", infant_text)
        self.assertIn("Pasaje gratuito — sin butaca", infant_text)
