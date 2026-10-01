from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from operations.models import Seat, SeatCategory, TripFare
from sales.models import AssignmentStatus, BookingStatus, SeatAssignment
from sales.services import confirm_booking, create_manual_booking

from .test_reservations import ReservationPanelBaseTestCase


class TripPassengerManifestTests(ReservationPanelBaseTestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user_seller)
        TripFare.objects.create(
            trip=self.trip_outbound,
            origin_stop=self.ts_out_jma,
            destination_stop=self.ts_out_ssj,
            seat_category=SeatCategory.SEMI_CAMA,
            amount=Decimal("12000.00"),
            currency="ARS",
        )
        self.confirmed = self._booking(
            name="Cecilia",
            last_name="Ramos",
            document="30.000.001",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )
        self.intermediate = self._booking(
            name="Bruno",
            last_name="López",
            document="30.000.002",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_jma,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_semi_3],
            }],
        )
        self.round_trip = self._booking(
            name="Ana",
            last_name="Torres",
            document="30.000.003",
            legs=[
                {
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_2],
                },
                {
                    "trip": self.trip_return,
                    "origin_stop": self.ts_ret_ssj,
                    "destination_stop": self.ts_ret_cba,
                    "seats": [self.seat_b2_cama_1],
                },
            ],
        )

    def _booking(self, *, name, last_name, document, legs, status=BookingStatus.CONFIRMED):
        booking = create_manual_booking(
            seller=self.user_seller,
            email=f"{document.replace('.', '')}@example.com",
            legs=legs,
            passengers_data=[{
                "first_name": name,
                "last_name": last_name,
                "document_type": "DNI",
                "document_number": document,
                "birth_date": "1990-01-01",
                "nationality": "Argentina",
                "gender": "",
            } for _ in range(len(legs[0]["seats"]))],
        )
        if status == BookingStatus.CONFIRMED:
            return confirm_booking(booking, now=timezone.now())
        booking.status = status
        booking.save(update_fields=["status", "updated_at"])
        if status != BookingStatus.HELD:
            SeatAssignment.objects.filter(leg__booking=booking).update(status=AssignmentStatus.RELEASED)
        return booking

    def url(self, name, trip):
        return reverse(f"panel:{name}", kwargs={"trip_pk": trip.pk})

    def test_only_confirmed_passengers_for_requested_trip_are_listed(self):
        expired_seat = Seat.objects.create(
            bus=self.bus_1, number=6, deck=Seat.Deck.UPPER,
            category=SeatCategory.SEMI_CAMA, position_x=3, position_y=0,
        )
        released_seat = Seat.objects.create(
            bus=self.bus_1, number=7, deck=Seat.Deck.UPPER,
            category=SeatCategory.SEMI_CAMA, position_x=4, position_y=0,
        )
        unconfirmed_seat = Seat.objects.create(
            bus=self.bus_1, number=8, deck=Seat.Deck.UPPER,
            category=SeatCategory.SEMI_CAMA, position_x=5, position_y=0,
        )
        self._booking(
            name="Retenido", last_name="NoMostrar", document="30.000.004",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba,
                   "destination_stop": self.ts_out_ssj, "seats": [self.seat_semi_4]},
            ], status=BookingStatus.HELD,
        )
        self._booking(
            name="Expirada", last_name="NoMostrar", document="30.000.005",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba,
                   "destination_stop": self.ts_out_ssj, "seats": [expired_seat]},
            ], status=BookingStatus.EXPIRED,
        )
        self._booking(
            name="Liberada", last_name="NoMostrar", document="30.000.006",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba,
                   "destination_stop": self.ts_out_ssj, "seats": [released_seat]},
            ], status=BookingStatus.RELEASED,
        )
        unconfirmed = self._booking(
            name="Asignación", last_name="NoMostrar", document="30.000.007",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba,
                   "destination_stop": self.ts_out_ssj, "seats": [unconfirmed_seat]},
            ],
        )
        SeatAssignment.objects.filter(leg__booking=unconfirmed).update(status=AssignmentStatus.HELD)
        response = self.client.get(self.url("trip_manifest", self.trip_outbound), secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Cecilia Ramos", content)
        self.assertIn("Bruno López", content)
        self.assertIn("Ana Torres", content)
        self.assertNotIn("Retenido NoMostrar", content)
        self.assertNotIn("Expirada NoMostrar", content)
        self.assertNotIn("Liberada NoMostrar", content)
        self.assertNotIn("Asignación NoMostrar", content)
        self.assertNotIn(str(self.round_trip.public_id), content.split("Pasajeros confirmados", 1)[0])

    def test_round_trip_uses_each_trip_leg_and_correct_seat(self):
        outbound = self.client.get(self.url("trip_manifest", self.trip_outbound), secure=True)
        returning = self.client.get(self.url("trip_manifest", self.trip_return), secure=True)
        self.assertContains(outbound, "Ana Torres")
        self.assertContains(outbound, "Córdoba Capital")
        self.assertContains(outbound, "San Salvador de Jujuy")
        self.assertContains(outbound, "2")
        self.assertContains(returning, "Ana Torres")
        self.assertContains(returning, "San Salvador de Jujuy")
        self.assertContains(returning, "Córdoba Capital")
        self.assertContains(returning, "1")

    def test_order_and_stop_summary_use_trip_stop_sequences(self):
        response = self.client.get(self.url("trip_manifest", self.trip_outbound), secure=True)
        rows = response.context["rows"]
        self.assertEqual([row.passenger_name for row in rows], ["Cecilia Ramos", "Ana Torres", "Bruno López"])
        summaries = {item.name: item for item in response.context["summaries"]}
        cba = summaries[self.ts_out_cba.stop.name]
        jma = summaries[self.ts_out_jma.stop.name]
        ssj = summaries[self.ts_out_ssj.stop.name]
        self.assertEqual((cba.boarding, cba.alighting), (2, 0))
        self.assertEqual((jma.boarding, jma.alighting), (1, 0))
        self.assertEqual(jma.continuing, 2)
        self.assertEqual(ssj.alighting, 3)

    def test_manifest_does_not_query_other_trips_or_n_plus_one(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url("trip_manifest", self.trip_outbound), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 10)
        self.assertFalse(any("payments" in query["sql"].lower() for query in queries))

    def test_print_view_and_trip_detail_link(self):
        detail = self.client.get(reverse("panel:trip_detail", kwargs={"pk": self.trip_outbound.pk}), secure=True)
        self.assertContains(detail, reverse("panel:trip_manifest", kwargs={"trip_pk": self.trip_outbound.pk}))
        printable = self.client.get(self.url("trip_manifest_print", self.trip_outbound), secure=True)
        self.assertEqual(printable.status_code, 200)
        self.assertContains(printable, "manifest.css")
        self.assertContains(printable, "Cecilia Ramos")

    def test_csv_is_utf8_bom_safe_and_formula_protected(self):
        passenger = self.confirmed.passengers.first()
        passenger.first_name = "=SUM(1,1)"
        passenger.save()
        response = self.client.get(self.url("trip_manifest_csv", self.trip_outbound), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("attachment; filename=\"manifiesto-viaje-".encode(), response["Content-Disposition"].encode())
        self.assertIn(b"'=SUM(1,1)", response.content)

    def test_roles_and_idor(self):
        for user in (self.user_admin, self.user_seller, self.user_super):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url("trip_manifest", self.trip_outbound), secure=True).status_code, 200)
        for user in (self.user_common, self.user_staff):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url("trip_manifest", self.trip_outbound), secure=True).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url("trip_manifest", self.trip_outbound), secure=True).status_code, 302)
        self.client.force_login(self.user_seller)
        self.assertEqual(self.client.get(reverse("panel:trip_manifest", kwargs={"trip_pk": 999999}), secure=True).status_code, 404)

    def test_manifest_is_read_only_and_rejects_non_get(self):
        before = list(SeatAssignment.objects.values_list("pk", "status"))
        response = self.client.post(self.url("trip_manifest", self.trip_outbound), secure=True)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(before, list(SeatAssignment.objects.values_list("pk", "status")))
