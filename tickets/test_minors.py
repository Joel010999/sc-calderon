from datetime import date, timedelta
from decimal import Decimal

from django.test import TransactionTestCase
from django.utils import timezone

from sales.models import AssignmentStatus
from sales.services import confirm_booking, create_online_booking
from tickets.boarding import BoardingCode, validate_boarding
from tickets.services import issue_tickets_for_booking
from tickets.tests import TicketBaseMixin


class MinorTicketIssuanceTests(TicketBaseMixin, TransactionTestCase):
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

        self.assertEqual(
            booking.legs.first().seat_assignments.filter(status=AssignmentStatus.CONFIRMED).count(),
            1,
        )

        outbound_infant_ticket = next(t for t in infant_tickets if t.leg.trip_id == self.trip_ida.pk)
        outcome = validate_boarding(
            outbound_infant_ticket.ticket_code,
            self.trip_ida.pk,
            self.seller_user,
            now=purchase_at + timedelta(days=2),
        )
        self.assertEqual(outcome.code, BoardingCode.VALID)
        self.assertIsNone(outcome.record.seat_assignment)
