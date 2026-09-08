from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from .models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from .services import schedule_trip


class InitialOperationsTests(TestCase):
    def seed(self):
        call_command("setup_initial_operations", stdout=StringIO())

    def test_initial_data_is_idempotent_and_limited_to_stops_and_routes(self):
        self.seed()
        original = {
            model: list(model.objects.order_by("pk").values())
            for model in (Stop, Route, RouteStop)
        }
        self.seed()
        for model, rows in original.items():
            self.assertEqual(list(model.objects.order_by("pk").values()), rows)
        self.assertEqual(Stop.objects.count(), 5)
        self.assertEqual(Route.objects.count(), 2)
        self.assertEqual(RouteStop.objects.count(), 10)
        for model in (Bus, Seat, Trip, TripStop, TripFare):
            self.assertFalse(model.objects.exists())

    def test_exact_routes_order_and_permissions(self):
        self.seed()
        expected = {
            "CBA-JUJ": [("CBA", 1, True, False), ("JMA", 2, True, False),
                        ("PER", 3, False, True), ("PAL", 4, False, True), ("SSJ", 5, False, True)],
            "JUJ-CBA": [("SSJ", 1, True, False), ("PAL", 2, True, False),
                        ("PER", 3, True, False), ("JMA", 4, False, True), ("CBA", 5, False, True)],
        }
        for code, rows in expected.items():
            route = Route.objects.get(code=code)
            self.assertEqual(list(route.route_stops.values_list(
                "stop__code", "sequence", "allows_boarding", "allows_alighting"
            )), rows)
        self.assertEqual(dict(Stop.objects.values_list("code", "name")), {
            "CBA": "Córdoba Capital", "JMA": "Jesús María", "PER": "Perico",
            "PAL": "Palpalá", "SSJ": "San Salvador de Jujuy",
        })

    def test_every_origin_destination_combination_in_both_directions(self):
        self.seed()
        stops = {stop.code: stop for stop in Stop.objects.all()}
        valid = {
            "CBA-JUJ": {(origin, destination) for origin in ("CBA", "JMA")
                        for destination in ("PER", "PAL", "SSJ")},
            "JUJ-CBA": {(origin, destination) for origin in ("SSJ", "PAL", "PER")
                        for destination in ("JMA", "CBA")},
        }
        for code, pairs in valid.items():
            route = Route.objects.get(code=code)
            for origin in stops:
                for destination in stops:
                    with self.subTest(route=code, origin=origin, destination=destination):
                        self.assertEqual(route.allows_journey(stops[origin], stops[destination]),
                                         (origin, destination) in pairs)

    def test_stops_outside_route_and_unsaved_stops_are_not_allowed(self):
        self.seed()
        route = Route.objects.get(code="CBA-JUJ")
        outside = Stop.objects.create(code="OTRA", name="Otra", city="Otra", province="Otra")
        start = Stop.objects.get(code="CBA")
        end = Stop.objects.get(code="SSJ")
        self.assertFalse(route.allows_journey(outside, end))
        self.assertFalse(route.allows_journey(start, outside))
        self.assertFalse(route.allows_journey(Stop(), end))
        self.assertFalse(Route().allows_journey(start, end))

    def test_command_preserves_existing_stop_and_route_names(self):
        self.seed()
        Stop.objects.filter(code="CBA").update(name="Nombre editado")
        Route.objects.filter(code="CBA-JUJ").update(name="Recorrido editado")
        self.seed()
        self.assertEqual(Stop.objects.get(code="CBA").name, "Nombre editado")
        self.assertEqual(Route.objects.get(code="CBA-JUJ").name, "Recorrido editado")

    def test_command_rejects_conflicting_route_without_partial_changes(self):
        route = Route.objects.create(code="CBA-JUJ", name="Existente")
        stop = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        RouteStop.objects.create(route=route, stop=stop, sequence=2,
                                 allows_boarding=True, allows_alighting=False)
        with self.assertRaises(CommandError):
            self.seed()
        self.assertEqual(Stop.objects.count(), 1)
        self.assertEqual(Route.objects.count(), 1)
        self.assertEqual(route.route_stops.get().sequence, 2)


class OperationsDataMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command("setup_initial_operations", stdout=StringIO())
        cls.route = Route.objects.get(code="CBA-JUJ")
        cls.bus = Bus.objects.create(code="PRUEBA", display_name="Colectivo de prueba")
        cls.stops = {stop.code: stop for stop in Stop.objects.all()}
        cls.start = datetime(2030, 1, 10, 8, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))

    def schedules(self, route=None):
        return {item.stop_id: self.start + timedelta(hours=index)
                for index, item in enumerate((route or self.route).route_stops.all())}

    def create_trip(self, route=None):
        return schedule_trip(route=route or self.route, bus=self.bus, schedules=self.schedules(route))

    def assert_database_rejects(self, action):
        with self.assertRaises(IntegrityError), transaction.atomic():
            action()


class StructureTests(OperationsDataMixin, TestCase):
    def test_stop_codes_are_validated_unique_and_stable(self):
        for code in ("cba", "C BA", "CBA\n", "-CBA", ""):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                Stop(code=code, name="Prueba", city="Prueba", province="Prueba").full_clean()
        stop = self.stops["CBA"]
        stop.code = "NUEVO"
        with self.assertRaises(ValidationError):
            stop.full_clean()
        self.assert_database_rejects(lambda: Stop.objects.create(code="CBA", name="Duplicada"))

    def test_route_and_bus_codes_are_unique(self):
        self.assert_database_rejects(lambda: Route.objects.create(code=self.route.code, name="Duplicado"))
        self.assert_database_rejects(lambda: Bus.objects.create(code=self.bus.code, display_name="Duplicado"))

    def test_route_sequence_and_stop_cannot_repeat(self):
        existing = self.route.route_stops.get(sequence=1)
        outside = Stop.objects.create(code="OTRA", name="Otra", city="Otra", province="Otra")
        for stop, sequence in [(outside, existing.sequence), (existing.stop, 6)]:
            with self.subTest(sequence=sequence):
                item = RouteStop(route=self.route, stop=stop, sequence=sequence,
                                 allows_boarding=True, allows_alighting=False)
                with self.assertRaises(ValidationError):
                    item.full_clean()
                self.assert_database_rejects(item.save)

    def test_route_sequence_must_start_at_one_or_above(self):
        item = self.route.route_stops.get(sequence=1)
        item.sequence = 0
        with self.assertRaises(ValidationError):
            item.full_clean()
        self.assert_database_rejects(item.save)

    def test_route_stop_requires_at_least_one_permission(self):
        item = self.route.route_stops.get(sequence=1)
        item.allows_boarding = item.allows_alighting = False
        with self.assertRaises(ValidationError):
            item.full_clean()
        self.assert_database_rejects(item.save)
        item.allows_boarding = item.allows_alighting = True
        item.full_clean()

    def test_optional_license_plate_is_unique_when_present(self):
        Bus.objects.create(code="SIN-PATENTE", display_name="Sin patente")
        self.bus.license_plate = "AB123CD"
        self.bus.full_clean()
        self.bus.save()
        duplicate = Bus(code="OTRO", display_name="Otro", license_plate="AB123CD")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        self.assert_database_rejects(duplicate.save)

    def make_seat(self, **changes):
        values = dict(bus=self.bus, number=1, deck=Seat.Deck.UPPER,
                      category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
        values.update(changes)
        return Seat(**values)

    def test_seat_number_and_position_cannot_repeat(self):
        self.make_seat().save()
        for changes in ({"position_x": 1}, {"number": 2}):
            with self.subTest(changes=changes):
                seat = self.make_seat(**changes)
                with self.assertRaises(ValidationError):
                    seat.full_clean()
                self.assert_database_rejects(seat.save)

    def test_seat_position_can_repeat_on_another_deck_or_bus(self):
        self.make_seat().save()
        other_bus = Bus.objects.create(code="OTRO", display_name="Otro")
        for seat in (self.make_seat(number=2, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA),
                     self.make_seat(bus=other_bus)):
            seat.full_clean()
            seat.save()
        self.assertEqual(Seat.objects.count(), 3)

    def test_seat_number_coordinates_and_choices_are_validated(self):
        for changes in ({"number": 0}, {"number": -1}, {"position_x": -1},
                        {"position_y": -1}, {"deck": "OTRA"}, {"category": "OTRA"}):
            with self.subTest(changes=changes):
                seat = self.make_seat(**changes)
                with self.assertRaises(ValidationError):
                    seat.full_clean()
                self.assert_database_rejects(seat.save)

    def test_capacity_counts_only_active_seats(self):
        self.assertEqual(self.bus.capacity, 0)
        seat = self.make_seat()
        seat.save()
        self.make_seat(number=2, position_x=1, is_active=False).save()
        other_bus = Bus.objects.create(code="OTRO", display_name="Otro")
        self.make_seat(bus=other_bus).save()
        self.assertEqual(self.bus.capacity, 1)
        seat.is_active = False
        seat.save()
        self.assertEqual(self.bus.capacity, 0)

    def test_operational_structure_is_protected_from_deletion(self):
        trip = self.create_trip()
        for item in (self.route, self.stops["CBA"], self.bus, trip):
            with self.subTest(model=type(item).__name__), self.assertRaises(ProtectedError):
                item.delete()

    def test_spanish_labels_and_representations(self):
        trip = self.create_trip()
        self.assertIn("Córdoba", str(self.stops["CBA"]))
        self.assertIn("CBA-JUJ", str(self.route))
        self.assertIn("Córdoba", str(self.route.route_stops.first()))
        self.assertIn("Colectivo de prueba", str(self.bus))
        self.assertIn("Butaca 1", str(self.make_seat()))
        self.assertIn("Programado", str(trip))
        self.assertIn("Córdoba", str(trip.trip_stops.first()))

    def test_admin_remains_disabled(self):
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)
        self.assertEqual(self.client.get("/admin/", secure=True).status_code, 404)


class SchedulingTests(OperationsDataMixin, TestCase):
    def test_creates_trip_with_exact_operational_snapshot(self):
        for route in Route.objects.all():
            with self.subTest(route=route.code):
                trip = self.create_trip(route)
                self.assertEqual(trip.departure_at, self.start)
                self.assertEqual(trip.status, Trip.Status.SCHEDULED)
                self.assertEqual(trip.bus, self.bus)
                self.assertEqual(trip.route, route)
                self.assertEqual(list(trip.trip_stops.values_list(
                    "stop_id", "sequence", "allows_boarding", "allows_alighting"
                )), list(route.route_stops.values_list(
                    "stop_id", "sequence", "allows_boarding", "allows_alighting"
                )))
                self.assertEqual(dict(trip.trip_stops.values_list("stop_id", "scheduled_at")),
                                 self.schedules(route))

    def test_snapshot_does_not_follow_route_changes(self):
        trip = self.create_trip()
        before = list(trip.trip_stops.values())
        item = self.route.route_stops.get(sequence=1)
        item.sequence = 10
        item.allows_boarding = False
        item.allows_alighting = True
        item.save()
        self.assertEqual(list(trip.trip_stops.values()), before)

    def test_all_initial_statuses_and_default(self):
        self.assertEqual(Trip().status, Trip.Status.SCHEDULED)
        self.assertEqual(set(Trip.Status.values), {"SCHEDULED", "BOARDING", "STARTED", "COMPLETED", "CANCELLED"})
        trip = self.create_trip()
        for status in Trip.Status:
            trip.status = status
            trip.full_clean()
            trip.save()
            trip.refresh_from_db()
            self.assertEqual(trip.status, status)
        trip.status = "OTRO"
        with self.assertRaises(ValidationError):
            trip.full_clean()
        self.assert_database_rejects(trip.save)

    def test_bus_cannot_change_after_scheduling(self):
        trip = self.create_trip()
        trip.bus = Bus.objects.create(code="OTRO", display_name="Otro")
        with self.assertRaises(ValidationError):
            trip.full_clean()

    def assert_schedule_rejected(self, schedules, route=None):
        before = Trip.objects.count(), TripStop.objects.count()
        with self.assertRaises(ValidationError):
            schedule_trip(route=route or self.route, bus=self.bus, schedules=schedules)
        self.assertEqual((Trip.objects.count(), TripStop.objects.count()), before)

    def test_missing_schedule_is_rejected(self):
        times = self.schedules()
        del times[self.stops["PER"].pk]
        self.assert_schedule_rejected(times)

    def test_additional_schedule_is_rejected(self):
        outside = Stop.objects.create(code="OTRA", name="Otra", city="Otra", province="Otra")
        times = self.schedules()
        times[outside.pk] = self.start + timedelta(days=1)
        self.assert_schedule_rejected(times)

    def test_duplicated_stop_schedule_is_rejected(self):
        times = list(self.schedules().items())
        self.assert_schedule_rejected(times + [times[0]])

    def test_equal_times_are_rejected(self):
        times = self.schedules()
        times[self.stops["JMA"].pk] = self.start
        self.assert_schedule_rejected(times)

    def test_nonincreasing_times_are_rejected(self):
        times = self.schedules()
        times[self.stops["PER"].pk] = self.start - timedelta(hours=1)
        self.assert_schedule_rejected(times)

    def test_naive_times_are_rejected(self):
        times = self.schedules()
        times[self.stops["JMA"].pk] = self.start.replace(tzinfo=None)
        self.assert_schedule_rejected(times)

    def test_invalid_schedule_types_are_rejected(self):
        for value in (None, "mañana", 123):
            with self.subTest(value=value):
                times = self.schedules()
                times[self.stops["JMA"].pk] = value
                self.assert_schedule_rejected(times)
        self.assert_schedule_rejected(None)
        self.assert_schedule_rejected([(self.stops["CBA"].pk,)])

    def test_order_of_input_does_not_override_route_order(self):
        times = list(reversed(list(self.schedules().items())))
        trip = schedule_trip(route=self.route, bus=self.bus, schedules=times)
        self.assertEqual(trip.departure_at, self.start)

    def test_times_are_compared_as_instants_across_timezones(self):
        times = self.schedules()
        times[self.stops["JMA"].pk] = times[self.stops["JMA"].pk].astimezone(timezone.utc)
        trip = schedule_trip(route=self.route, bus=self.bus, schedules=times)
        self.assertEqual(trip.trip_stops.count(), 5)
        times[self.stops["JMA"].pk] = self.start.astimezone(timezone.utc)
        self.assert_schedule_rejected(times)

    def test_empty_route_is_rejected(self):
        empty = Route.objects.create(code="VACIO", name="Vacío")
        self.assert_schedule_rejected({}, route=empty)

    def test_unsaved_route_and_bus_are_rejected(self):
        for route, bus in ((Route(), self.bus), (self.route, Bus())):
            with self.subTest(route=route.pk, bus=bus.pk), self.assertRaises(ValidationError):
                schedule_trip(route=route, bus=bus, schedules=self.schedules())
        self.assertFalse(Trip.objects.exists())

    def test_write_failure_rolls_back_trip_and_already_saved_stops(self):
        original_save = TripStop.save
        observed = []

        def fail_on_third(instance, *args, **kwargs):
            if instance.sequence == 3:
                observed.append((Trip.objects.count(), TripStop.objects.count()))
                raise ValidationError("Fallo de escritura de prueba.")
            return original_save(instance, *args, **kwargs)

        with patch.object(TripStop, "save", fail_on_third):
            self.assert_schedule_rejected(self.schedules())
        self.assertEqual(observed, [(1, 2)])

    def test_trip_stop_sequence_and_stop_cannot_repeat(self):
        trip = self.create_trip()
        outside = Stop.objects.create(code="OTRA", name="Otra", city="Otra", province="Otra")
        for stop, sequence in ((outside, 1), (self.stops["CBA"], 6)):
            with self.subTest(sequence=sequence):
                item = TripStop(trip=trip, stop=stop, sequence=sequence,
                                scheduled_at=self.start, allows_boarding=True, allows_alighting=False)
                with self.assertRaises(ValidationError):
                    item.full_clean()
                self.assert_database_rejects(item.save)

    def test_trip_stop_sequence_must_be_positive(self):
        item = self.create_trip().trip_stops.first()
        item.sequence = 0
        with self.assertRaises(ValidationError):
            item.full_clean()
        self.assert_database_rejects(item.save)

    def test_model_datetime_validation_rejects_naive_values(self):
        trip = self.create_trip()
        trip.departure_at = self.start.replace(tzinfo=None)
        with self.assertRaises(ValidationError):
            trip.full_clean()
        item = trip.trip_stops.first()
        item.scheduled_at = self.start.replace(tzinfo=None)
        with self.assertRaises(ValidationError):
            item.full_clean()


class FareTests(OperationsDataMixin, TestCase):
    def setUp(self):
        self.trip = self.create_trip()
        self.trip_stops = {item.stop.code: item for item in self.trip.trip_stops.select_related("stop")}

    def make_fare(self, **changes):
        values = dict(trip=self.trip, origin_stop=self.trip_stops["CBA"],
                      destination_stop=self.trip_stops["SSJ"], seat_category=SeatCategory.SEMI_CAMA,
                      amount=Decimal("12345.67"))
        values.update(changes)
        return TripFare(**values)

    def test_valid_fare_persists_decimal_and_default_currency(self):
        fare = self.make_fare()
        fare.full_clean()
        fare.save()
        fare.refresh_from_db()
        self.assertIsInstance(fare.amount, Decimal)
        self.assertEqual(fare.amount, Decimal("12345.67"))
        self.assertEqual(fare.currency, "ARS")
        self.assertIn("ARS 12345.67", str(fare))

    def test_fares_follow_valid_segments_in_both_directions(self):
        for route in Route.objects.all():
            trip = self.create_trip(route)
            items = {item.stop.code: item for item in trip.trip_stops.select_related("stop")}
            origins = ("CBA", "JMA") if route.code == "CBA-JUJ" else ("SSJ", "PAL", "PER")
            destinations = ("PER", "PAL", "SSJ") if route.code == "CBA-JUJ" else ("JMA", "CBA")
            for origin_code, origin in items.items():
                for destination_code, destination in items.items():
                    with self.subTest(route=route.code, origin=origin_code, destination=destination_code):
                        fare = self.make_fare(trip=trip, origin_stop=origin, destination_stop=destination)
                        if origin_code in origins and destination_code in destinations:
                            fare.full_clean()
                        else:
                            with self.assertRaises(ValidationError):
                                fare.full_clean()

    def test_stops_from_other_trips_are_rejected(self):
        other_trip = self.create_trip()
        for changes in (
            {"origin_stop": other_trip.trip_stops.get(stop=self.stops["CBA"])},
            {"destination_stop": other_trip.trip_stops.get(stop=self.stops["SSJ"])},
            {"trip": other_trip},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make_fare(**changes).full_clean()

    def test_equal_origin_destination_is_rejected_by_database(self):
        fare = self.make_fare(destination_stop=self.trip_stops["CBA"])
        self.assert_database_rejects(fare.save)

    def test_fare_combination_is_unique_even_when_inactive(self):
        self.make_fare(is_active=False).save()
        fare = self.make_fare()
        with self.assertRaises(ValidationError):
            fare.full_clean()
        self.assert_database_rejects(fare.save)

    def test_same_segment_can_have_different_categories(self):
        for category in SeatCategory:
            fare = self.make_fare(seat_category=category)
            fare.full_clean()
            fare.save()
        self.assertEqual(self.trip.fares.count(), 2)

    def test_positive_amount_is_required_in_domain_and_database(self):
        for amount in (Decimal("0.00"), Decimal("-1.00")):
            with self.subTest(amount=amount):
                fare = self.make_fare(amount=amount)
                with self.assertRaises(ValidationError):
                    fare.full_clean()
                self.assert_database_rejects(fare.save)

    def test_amount_precision_nonfinite_values_and_float_are_rejected(self):
        for amount in (Decimal("1.001"), Decimal("NaN"), Decimal("Infinity"), 12.34):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                self.make_fare(amount=amount).full_clean()

    def test_currency_requires_three_uppercase_letters(self):
        for currency in ("AR", "ARSS", "ars", "123", ""):
            with self.subTest(currency=currency), self.assertRaises(ValidationError):
                self.make_fare(currency=currency).full_clean()

    def test_invalid_category_is_rejected(self):
        fare = self.make_fare(seat_category="OTRA")
        with self.assertRaises(ValidationError):
            fare.full_clean()
        self.assert_database_rejects(fare.save)

    def test_fare_uses_trip_snapshot_after_route_permissions_change(self):
        self.route.route_stops.filter(stop=self.stops["CBA"]).update(
            allows_boarding=False, allows_alighting=True
        )
        self.assertFalse(self.route.allows_journey(self.stops["CBA"], self.stops["SSJ"]))
        self.make_fare().full_clean()

    def test_fare_protects_its_historical_trip_stops(self):
        fare = self.make_fare()
        fare.save()
        for item in (fare.origin_stop, fare.destination_stop):
            with self.assertRaises(ProtectedError):
                item.delete()
