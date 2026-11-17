import io
from datetime import date, timedelta
from decimal import Decimal

import pypdf
from django.core.exceptions import ValidationError
from django.test import TransactionTestCase
from django.utils import timezone

from sales.models import AssignmentStatus, BookingStatus, SeatAssignment
from sales.services import confirm_booking, create_online_booking
from tickets.boarding import validate_boarding
from tickets.exceptions import InvalidTicketError
from tickets.models import Ticket
from tickets.services import issue_tickets_for_booking, send_booking_tickets
from tickets.storage import get_ticket_storage
from tickets.tests import TicketBaseMixin


class MinorTicketIssuanceTests(TicketBaseMixin, TransactionTestCase):
    def _extract_pdf_text(self, ticket: Ticket) -> str:
        storage = get_ticket_storage()
        with storage.open(ticket.pdf_path, "rb") as f:
            pdf_bytes = f.read()
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        self.assertEqual(len(reader.pages), 1)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def test_lap_infant_under_five_gets_ticket_without_seat_in_both_legs_and_boards(self):
        # Inicializar viaje de vuelta usando el helper existente y luego crear reserva con menor de 4 años
        self.create_confirmed_booking(passenger_count=1, round_trip=True, seats_list=[self.seat_2])

        purchase_at = self.now
        local_purchase_date = timezone.localtime(purchase_at).date()
        # Menor de 4 años en víspera de cumplir 5 años al comprar ida y vuelta
        eve_of_five = local_purchase_date.replace(year=local_purchase_date.year - 5) + timedelta(days=1)

        booking = create_online_booking(
            email="tickets-menor4@correo.com",
            legs=[
                {
                    "trip": self.trip_ida,
                    "origin_stop": self.ts_cba,
                    "destination_stop": self.ts_jujuy,
                    "seats": [self.seat_1],
                },
                {
                    "trip": self.trip_vuelta,
                    "origin_stop": self.ts_v_jujuy,
                    "destination_stop": self.ts_v_cba,
                    "seats": [self.seat_v3],
                },
            ],
            passengers_data=[
                {
                    "first_name": "Madre",
                    "last_name": "Acompañante",
                    "document_type": "DNI",
                    "document_number": "30111222",
                    "birth_date": date(1990, 1, 1).isoformat(),
                    "nationality": "Argentina",
                },
                {
                    "first_name": "Menor",
                    "last_name": "CuatroAnios",
                    "document_type": "DNI",
                    "document_number": "56111222",
                    "birth_date": eve_of_five.isoformat(),
                    "nationality": "Argentina",
                    "accompanied_by_position": 1,
                },
            ],
            now=purchase_at,
        )
        confirm_booking(booking, now=purchase_at + timedelta(minutes=5))

        # Emitir pasajes cuando el menor ya habría cumplido 5 años: respeta el snapshot sin recalcular
        tickets = issue_tickets_for_booking(booking, now=purchase_at + timedelta(days=2))
        self.assertEqual(len(tickets), 4)

        infant = booking.passengers.get(position=2)
        infant_tickets = [t for t in tickets if t.passenger_id == infant.pk]
        self.assertEqual(len(infant_tickets), 2)
        for infant_ticket in infant_tickets:
            self.assertIsNone(infant_ticket.seat_assignment)
            self.assertIsNone(infant_ticket.seat_number)
            self.assertEqual(infant_ticket.seat_category, "LAP_INFANT")
            self.assertEqual(infant_ticket.seat_category_display, "Sin butaca")
            self.assertEqual(infant_ticket.price, Decimal("0.00"))

        # No se crea ninguna asignación de butaca adicional para el menor
        self.assertEqual(
            booking.legs.first().seat_assignments.filter(status=AssignmentStatus.CONFIRMED).count(),
            1,
        )
        self.assertEqual(
            SeatAssignment.objects.filter(leg__booking=booking).count(),
            2,
        )

        outbound_infant_ticket = next(t for t in infant_tickets if t.leg.trip_id == self.trip_ida.pk)
        return_infant_ticket = next(t for t in infant_tickets if t.leg.trip_id == self.trip_vuelta.pk)

        # Sin asignación propia de butaca
        self.assertIsNone(outbound_infant_ticket.seat_assignment)
        self.assertIsNone(return_infant_ticket.seat_assignment)
        self.assertIsNone(outbound_infant_ticket.seat_number)
        self.assertIsNone(return_infant_ticket.seat_number)

        # Snapshot de butaca de referencia por cada tramo persistido en DB
        outbound_infant_ticket.refresh_from_db()
        return_infant_ticket.refresh_from_db()
        self.assertEqual(outbound_infant_ticket.companion_seat_reference, str(self.seat_1.number))
        self.assertEqual(return_infant_ticket.companion_seat_reference, str(self.seat_v3.number))

        # Leyenda exacta en ambos talones para ida
        text_ida = self._extract_pdf_text(outbound_infant_ticket)
        norm_ida = " ".join(text_ida.split())
        legend_ida = f"Sin asiento propio. Butaca de referencia del acompañante: {self.seat_1.number}"
        self.assertEqual(norm_ida.count(legend_ida), 2)

        # Leyenda exacta en ambos talones para vuelta
        text_vuelta = self._extract_pdf_text(return_infant_ticket)
        norm_vuelta = " ".join(text_vuelta.split())
        legend_vuelta = f"Sin asiento propio. Butaca de referencia del acompañante: {self.seat_v3.number}"
        self.assertEqual(norm_vuelta.count(legend_vuelta), 2)

        with self.assertRaises(ValidationError) as ctx:
            validate_boarding(
                outbound_infant_ticket.ticket_code,
                self.trip_ida.pk,
                self.seller_user,
                now=purchase_at + timedelta(days=2),
            )
        self.assertIn("deshabilitado", str(ctx.exception).lower())

    def test_lap_infant_idempotent_reissue_does_not_alter_historical_pdf(self):
        """Verifica que reemisiones/idempotencia no regeneren ni alteren los PDFs históricos emitidos."""
        self.create_confirmed_booking(passenger_count=1, round_trip=False, seats_list=[self.seat_2])
        booking = create_online_booking(
            email="tickets-reissue@correo.com",
            legs=[
                {
                    "trip": self.trip_ida,
                    "origin_stop": self.ts_cba,
                    "destination_stop": self.ts_jujuy,
                    "seats": [self.seat_1],
                },
            ],
            passengers_data=[
                {
                    "first_name": "Madre",
                    "last_name": "Test",
                    "document_type": "DNI",
                    "document_number": "31111222",
                    "birth_date": date(1992, 5, 10).isoformat(),
                    "nationality": "Argentina",
                },
                {
                    "first_name": "Bebé",
                    "last_name": "Test",
                    "document_type": "DNI",
                    "document_number": "57111222",
                    "birth_date": (self.now.date() - timedelta(days=300)).isoformat(),
                    "nationality": "Argentina",
                    "accompanied_by_position": 1,
                },
            ],
            now=self.now,
        )
        confirm_booking(booking, now=self.now + timedelta(minutes=5))

        tickets_first = issue_tickets_for_booking(booking, now=self.now + timedelta(minutes=6))
        infant_ticket_1 = next(t for t in tickets_first if t.passenger.is_lap_infant)
        pdf_path_1 = infant_ticket_1.pdf_path
        storage = get_ticket_storage()
        with storage.open(pdf_path_1, "rb") as f:
            pdf_bytes_1 = f.read()

        # Segunda llamada a emisión (idempotencia)
        tickets_second = issue_tickets_for_booking(booking, now=self.now + timedelta(minutes=10))
        infant_ticket_2 = next(t for t in tickets_second if t.passenger.is_lap_infant)
        self.assertEqual(infant_ticket_2.pdf_path, pdf_path_1)
        with storage.open(pdf_path_1, "rb") as f:
            pdf_bytes_2 = f.read()

        self.assertEqual(pdf_bytes_1, pdf_bytes_2)

        # Recarga desde DB conserva snapshot persistido
        infant_ticket_1.refresh_from_db()
        self.assertEqual(infant_ticket_1.companion_seat_reference, str(self.seat_1.number))

        # Reenvío de pasajes no recalcula ni altera PDF
        attempt = send_booking_tickets(booking, retry=True, now=self.now + timedelta(minutes=15))
        self.assertIsNotNone(attempt)
        with storage.open(pdf_path_1, "rb") as f:
            pdf_bytes_3 = f.read()
        self.assertEqual(pdf_bytes_1, pdf_bytes_3)

    def test_lap_infant_fails_and_leaves_evidence_when_companion_lacks_valid_seat(self):
        """Si falta asignación válida del acompañante, se conserva validación y se deja evidencia en logs."""
        booking = create_online_booking(
            email="tickets-no-seat@correo.com",
            legs=[
                {
                    "trip": self.trip_ida,
                    "origin_stop": self.ts_cba,
                    "destination_stop": self.ts_jujuy,
                    "seats": [self.seat_1],
                },
            ],
            passengers_data=[
                {
                    "first_name": "Madre",
                    "last_name": "Valida",
                    "document_type": "DNI",
                    "document_number": "31111333",
                    "birth_date": date(1992, 5, 10).isoformat(),
                    "nationality": "Argentina",
                },
                {
                    "first_name": "Bebé",
                    "last_name": "Uno",
                    "document_type": "DNI",
                    "document_number": "57111333",
                    "birth_date": (self.now.date() - timedelta(days=200)).isoformat(),
                    "nationality": "Argentina",
                    "accompanied_by_position": 1,
                },
            ],
            now=self.now,
        )
        confirm_booking(booking, now=self.now)

        # Crear un segundo menor en la misma compra sin asiento propio
        second_infant = booking.passengers.create(
            position=3,
            first_name="Bebé",
            last_name="Dos",
            document_type="DNI",
            document_number="58111333",
            birth_date=self.now.date() - timedelta(days=100),
            is_lap_infant=True,
            accompanied_by=None,
        )
        # Apuntar el accompanied_by del primer menor a este segundo menor (que no posee butaca)
        first_infant = booking.passengers.get(position=2)
        first_infant.accompanied_by = second_infant
        first_infant.save(update_fields=["accompanied_by"])

        with self.assertLogs("tickets.services", level="WARNING") as cm:
            with self.assertRaises(InvalidTicketError) as ctx:
                issue_tickets_for_booking(booking, now=self.now)
            self.assertIn("Falta asignación válida de butaca para el acompañante", str(ctx.exception))
            self.assertTrue(any("Falta asignación válida de butaca para el acompañante" in msg for msg in cm.output))

    def test_lap_infant_fails_and_leaves_evidence_when_accompanied_by_missing(self):
        """Si el menor gratuito no tiene accompanied_by vinculado, se rechaza la emisión con evidencia."""
        booking = create_online_booking(
            email="tickets-no-comp@correo.com",
            legs=[
                {
                    "trip": self.trip_ida,
                    "origin_stop": self.ts_cba,
                    "destination_stop": self.ts_jujuy,
                    "seats": [self.seat_1],
                },
            ],
            passengers_data=[
                {
                    "first_name": "Adulto",
                    "last_name": "Solo",
                    "document_type": "DNI",
                    "document_number": "31111444",
                    "birth_date": date(1992, 5, 10).isoformat(),
                    "nationality": "Argentina",
                },
                {
                    "first_name": "Bebé",
                    "last_name": "Huerfano",
                    "document_type": "DNI",
                    "document_number": "57111444",
                    "birth_date": (self.now.date() - timedelta(days=200)).isoformat(),
                    "nationality": "Argentina",
                    "accompanied_by_position": 1,
                },
            ],
            now=self.now,
        )
        confirm_booking(booking, now=self.now)

        # Forzar accompanied_by a None
        infant = booking.passengers.get(position=2)
        infant.accompanied_by = None
        infant.save(update_fields=["accompanied_by"])

        with self.assertLogs("tickets.services", level="WARNING") as cm:
            with self.assertRaises(InvalidTicketError) as ctx:
                issue_tickets_for_booking(booking, now=self.now)
            self.assertIn("Falta asignación válida", str(ctx.exception))
            self.assertTrue(any("Falta acompañante declarado" in msg for msg in cm.output))
