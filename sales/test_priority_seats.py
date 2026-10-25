from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase

from operations.models import Seat, SeatCategory
from sales.exceptions import SeatUnavailableError
from sales.models import AssignmentStatus, Booking, BookingStatus
from sales.priority_seats import (
    AR_TZ,
    is_priority_seat,
    is_priority_seat_released_for_general_sale,
    is_seat_blocked_for_general_sale,
    priority_seat_release_at,
)
from sales.services import create_online_booking, get_trip_availability
from sales.tests import SalesBaseTestCase


class PrioritySeatPolicyTests(TestCase):
    def test_calendar_release_uses_argentina_midnight_not_48_hours(self):
        departure = datetime(2026, 10, 15, 20, 0, tzinfo=AR_TZ)
        release_at = priority_seat_release_at(departure)

        self.assertEqual(release_at, datetime(2026, 10, 13, 0, 0, tzinfo=AR_TZ))
        self.assertTrue(is_priority_seat_released_for_general_sale(departure, release_at))
        self.assertFalse(is_priority_seat_released_for_general_sale(departure, release_at - timedelta(seconds=1)))
        self.assertTrue(is_priority_seat_released_for_general_sale(departure, release_at + timedelta(seconds=1)))

    def test_timezone_and_year_boundary_are_calendar_based(self):
        departure_utc = datetime(2026, 10, 15, 23, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(
            priority_seat_release_at(departure_utc),
            datetime(2026, 10, 13, 0, 0, tzinfo=AR_TZ),
        )
        new_year_departure = datetime(2027, 1, 1, 1, 0, tzinfo=AR_TZ)
        self.assertEqual(
            priority_seat_release_at(new_year_departure),
            datetime(2026, 12, 30, 0, 0, tzinfo=AR_TZ),
        )

    def test_only_51_and_54_are_priority_and_policy_isolated_by_departure(self):
        departure_a = datetime(2026, 10, 15, 20, 0, tzinfo=AR_TZ)
        departure_b = datetime(2026, 10, 20, 20, 0, tzinfo=AR_TZ)
        now = datetime(2026, 10, 14, 12, 0, tzinfo=AR_TZ)

        self.assertTrue(is_priority_seat(51))
        self.assertTrue(is_priority_seat(54))
        self.assertFalse(is_priority_seat(50))
        self.assertTrue(is_seat_blocked_for_general_sale(departure_b, 51, now=now))
        self.assertFalse(is_seat_blocked_for_general_sale(departure_a, 51, now=now))
        self.assertFalse(is_seat_blocked_for_general_sale(departure_a, 50, now=now))


class PrioritySeatBookingTests(SalesBaseTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.priority_51 = Seat.objects.create(
            bus=cls.bus_1, number=51, deck=Seat.Deck.UPPER, category=SeatCategory.CAMA,
            position_x=8, position_y=0,
        )
        cls.priority_54 = Seat.objects.create(
            bus=cls.bus_1, number=54, deck=Seat.Deck.UPPER, category=SeatCategory.CAMA,
            position_x=9, position_y=0,
        )

    def passenger(self, position=1):
        return {
            "first_name": f"Pasajero{position}",
            "last_name": "Prioritario",
            "document_type": "DNI",
            "document_number": f"3700000{position}",
            "birth_date": "1990-01-01",
            "nationality": "Argentina",
        }

    def leg(self, seats):
        return {
            "trip": self.trip_outbound,
            "origin_stop": self.ts_out_cba,
            "destination_stop": self.ts_out_ssj,
            "seats": seats,
        }

    def test_before_release_blocks_both_priority_seats_server_side(self):
        release_at = priority_seat_release_at(self.trip_outbound)
        before = release_at - timedelta(seconds=1)

        for seat in (self.priority_51, self.priority_54):
            with self.subTest(seat=seat.number), self.assertRaises(ValidationError):
                create_online_booking(
                    email=f"antes-{seat.number}@correo.com",
                    legs=[self.leg([seat])],
                    passengers_data=[self.passenger()],
                    now=before,
                )

    def test_at_release_both_priority_seats_are_sellable(self):
        release_at = priority_seat_release_at(self.trip_outbound)
        booking = create_online_booking(
            email="exacto@correo.com",
            legs=[self.leg([self.priority_51, self.priority_54])],
            passengers_data=[self.passenger(1), self.passenger(2)],
            now=release_at,
        )

        self.assertEqual(booking.legs.first().seat_assignments.count(), 2)

    def test_existing_occupancy_is_not_released_or_hidden_as_priority_block(self):
        release_at = priority_seat_release_at(self.trip_outbound)
        booking = create_online_booking(
            email="ocupada@correo.com",
            legs=[self.leg([self.priority_51])],
            passengers_data=[self.passenger()],
            now=release_at,
        )
        before = release_at - timedelta(seconds=1)
        availability = get_trip_availability(self.trip_outbound, now=before, mutate=False)

        self.assertNotIn(self.priority_51, availability["available_seats"])
        self.assertIn(self.priority_51.pk, availability["occupied_seat_ids"])
        self.assertNotIn(self.priority_51.pk, availability["priority_blocked_seat_ids"])
        self.assertEqual(booking.legs.first().seat_assignments.count(), 1)

    def test_other_seat_and_double_booking_remain_normal(self):
        release_at = priority_seat_release_at(self.trip_outbound)
        first = create_online_booking(
            email="primera@correo.com",
            legs=[self.leg([self.seat_cama_1])],
            passengers_data=[self.passenger()],
            now=release_at,
        )
        self.assertEqual(first.legs.first().seat_assignments.count(), 1)
        with self.assertRaises(SeatUnavailableError):
            create_online_booking(
                email="segunda@correo.com",
                legs=[self.leg([self.seat_cama_1])],
                passengers_data=[self.passenger()],
                now=release_at,
            )

    def test_priority_seat_double_booking_is_rejected_after_release(self):
        release_at = priority_seat_release_at(self.trip_outbound)
        create_online_booking(
            email="prioritaria-primera@correo.com",
            legs=[self.leg([self.priority_54])],
            passengers_data=[self.passenger()],
            now=release_at,
        )
        with self.assertRaises(SeatUnavailableError):
            create_online_booking(
                email="prioritaria-segunda@correo.com",
                legs=[self.leg([self.priority_54])],
                passengers_data=[self.passenger(2)],
                now=release_at,
            )

    def test_get_availability_does_not_write_expired_hold(self):
        release_at = priority_seat_release_at(self.trip_outbound)
        booking = create_online_booking(
            email="hold-vencido@correo.com",
            legs=[self.leg([self.seat_cama_2])],
            passengers_data=[self.passenger()],
            now=release_at,
        )
        booking.expires_at = release_at - timedelta(minutes=1)
        booking.save(update_fields=["expires_at"])

        get_trip_availability(self.trip_outbound, now=release_at, mutate=False)

        booking.refresh_from_db()
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertEqual(assignment.status, AssignmentStatus.HELD)

    def test_existing_priority_occupancy_is_preserved_before_release(self):
        release_at = priority_seat_release_at(self.trip_outbound)
        booking = create_online_booking(
            email="historica@correo.com",
            legs=[self.leg([self.priority_51])],
            passengers_data=[self.passenger()],
            now=release_at,
        )
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = release_at
        booking.save(update_fields=["status", "confirmed_at"])

        availability = get_trip_availability(
            self.trip_outbound,
            now=release_at - timedelta(seconds=1),
            mutate=False,
        )
        self.assertIn(self.priority_51.pk, availability["occupied_seat_ids"])
        self.assertNotIn(self.priority_51.pk, availability["priority_blocked_seat_ids"])
        self.assertEqual(Booking.objects.get(pk=booking.pk).status, BookingStatus.CONFIRMED)
