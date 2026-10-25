from django.test import TransactionTestCase

from sales.models import AssignmentStatus
from tickets.services import issue_tickets_for_booking
from tickets.tests import TicketBaseMixin


class MinorTicketIssuanceTests(TicketBaseMixin, TransactionTestCase):
    def test_lap_infant_gets_ticket_without_seat(self):
        booking = self.create_confirmed_booking(passenger_count=2, round_trip=False)
        infant = booking.passengers.get(position=2)
        infant.is_lap_infant = True
        infant.accompanied_by = booking.passengers.get(position=1)
        infant.save(update_fields=["is_lap_infant", "accompanied_by", "updated_at"])
        infant.seat_assignments.all().delete()

        tickets = issue_tickets_for_booking(booking)

        infant_ticket = next(ticket for ticket in tickets if ticket.passenger_id == infant.pk)
        self.assertIsNone(infant_ticket.seat_assignment)
        self.assertEqual(infant_ticket.seat_number, None)
        self.assertEqual(infant_ticket.price, 0)
        self.assertEqual(booking.legs.first().seat_assignments.filter(status=AssignmentStatus.CONFIRMED).count(), 1)

