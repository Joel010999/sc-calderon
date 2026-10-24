from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from sales.minors import is_free_lap_infant
from sales.services import create_online_booking
from sales.tests import SalesBaseTestCase


class MinorPassengerPolicyTests(SalesBaseTestCase):
    def _passenger(self, position, birth_date, mother=None):
        return {
            "first_name": f"Persona{position}",
            "last_name": "Prueba",
            "document_type": "DNI",
            "document_number": f"9900000{position}",
            "birth_date": birth_date.isoformat(),
            "nationality": "Argentina",
            "accompanied_by_position": mother,
        }

    def _leg(self, seats):
        return {
            "trip": self.trip_outbound,
            "origin_stop": self.ts_out_cba,
            "destination_stop": self.ts_out_ssj,
            "seats": seats,
        }

    def test_lap_infant_is_passenger_but_not_seat_or_commission_unit(self):
        purchase_at = timezone.now()
        infant_birth = purchase_at.date() - timedelta(days=365)
        booking = create_online_booking(
            email="familia@correo.com",
            legs=[self._leg([self.seat_cama_1])],
            passengers_data=[
                self._passenger(1, date(1990, 1, 1)),
                self._passenger(2, infant_birth, mother=1),
            ],
            now=purchase_at,
        )

        adult, infant = booking.passengers.order_by("position")
        self.assertEqual(booking.passengers.count(), 2)
        self.assertFalse(adult.is_lap_infant)
        self.assertTrue(infant.is_lap_infant)
        self.assertEqual(infant.accompanied_by, adult)
        self.assertEqual(booking.legs.first().seat_assignments.count(), 1)
        self.assertEqual(booking.legs.first().seat_assignments.first().passenger, adult)

    def test_round_trip_keeps_free_snapshot_after_second_birthday(self):
        purchase_at = timezone.now()
        infant_birth = purchase_at.date() - timedelta(days=365)
        booking = create_online_booking(
            email="ida-vuelta@correo.com",
            legs=[
                self._leg([self.seat_cama_1]),
                {
                    "trip": self.trip_return,
                    "origin_stop": self.ts_ret_ssj,
                    "destination_stop": self.ts_ret_cba,
                    "seats": [self.seat_semi_3],
                },
            ],
            passengers_data=[self._passenger(1, date(1990, 1, 1)), self._passenger(2, infant_birth, mother=1)],
            now=purchase_at,
        )

        infant = booking.passengers.get(position=2)
        self.assertTrue(infant.is_lap_infant)
        self.assertEqual(booking.legs.count(), 2)
        self.assertEqual(sum(leg.seat_assignments.count() for leg in booking.legs.all()), 2)

    def test_exact_second_birthday_and_later_purchase_require_seat(self):
        purchase_at = timezone.now()
        exactly_two = purchase_at.date().replace(year=purchase_at.date().year - 2)
        self.assertFalse(is_free_lap_infant(exactly_two, purchase_at))

        with self.assertRaises(ValidationError):
            create_online_booking(
                email="cumple@correo.com",
                legs=[self._leg([self.seat_cama_1])],
                passengers_data=[self._passenger(1, date(1990, 1, 1)), self._passenger(2, exactly_two, mother=1)],
                now=purchase_at,
            )

        later_birth = purchase_at.date().replace(year=purchase_at.date().year - 2) - timedelta(days=1)
        self.assertFalse(is_free_lap_infant(later_birth, purchase_at))

    def test_free_minor_must_reference_a_seated_passenger_in_same_purchase(self):
        purchase_at = timezone.now()
        infant_birth = purchase_at.date() - timedelta(days=365)
        for mother in (None, 2, 99):
            with self.subTest(mother=mother), self.assertRaises(ValidationError):
                create_online_booking(
                    email="relacion-invalida@correo.com",
                    legs=[self._leg([self.seat_cama_1])],
                    passengers_data=[self._passenger(1, date(1990, 1, 1)), self._passenger(2, infant_birth, mother=mother)],
                    now=purchase_at,
                )

