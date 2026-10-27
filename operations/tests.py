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
from .services import (
    detect_trip_fare_conflicts,
    schedule_trip,
    update_trip_schedules,
    validate_no_trip_fare_conflicts,
)


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
        self.assertEqual(Stop.objects.count(), 6)
        self.assertEqual(Route.objects.count(), 2)
        self.assertEqual(RouteStop.objects.count(), 12)
        for model in (Bus, Seat, Trip, TripStop, TripFare):
            self.assertFalse(model.objects.exists())

    def test_exact_routes_order_and_permissions(self):
        self.seed()
        expected = {
            "CBA-JUJ": [("CBA", 1, True, False), ("JMA", 2, True, False),
                        ("GUE", 3, False, True), ("PER", 4, False, True),
                        ("PAL", 5, False, True), ("SSJ", 6, False, True)],
            "JUJ-CBA": [("SSJ", 1, True, False), ("PAL", 2, True, False),
                        ("PER", 3, True, False), ("GUE", 4, True, False),
                        ("JMA", 5, False, True), ("CBA", 6, False, True)],
        }
        for code, rows in expected.items():
            route = Route.objects.get(code=code)
            self.assertEqual(list(route.route_stops.values_list(
                "stop__code", "sequence", "allows_boarding", "allows_alighting"
            )), rows)
        self.assertEqual(dict(Stop.objects.values_list("code", "name")), {
            "GUE": "G\u00fcemes",
            "CBA": "Córdoba Capital", "JMA": "Jesús María", "PER": "Perico",
            "PAL": "Palpalá", "SSJ": "San Salvador de Jujuy",
        })

    def test_every_origin_destination_combination_in_both_directions(self):
        self.seed()
        stops = {stop.code: stop for stop in Stop.objects.all()}
        valid = {
            "CBA-JUJ": {(origin, destination) for origin in ("CBA", "JMA")
                        for destination in ("GUE", "PER", "PAL", "SSJ")},
            "JUJ-CBA": {(origin, destination) for origin in ("SSJ", "PAL", "PER", "GUE")
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

    def test_command_dry_run_does_not_write_guemes_configuration(self):
        output = StringIO()
        call_command("setup_initial_operations", dry_run=True, stdout=output)
        self.assertIn("GUE", output.getvalue())
        self.assertEqual(Stop.objects.count(), 0)
        self.assertEqual(Route.objects.count(), 0)

    def test_command_adds_guemes_without_rewriting_existing_trips(self):
        self.seed()
        route = Route.objects.get(code="CBA-JUJ")
        bus = Bus.objects.create(code="PRE-GUE", display_name="Prueba")
        Seat.objects.create(bus=bus, number=1, deck=Seat.Deck.UPPER,
                            category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
        trip = self.create_trip_for_command(route, bus)
        RouteStop.objects.filter(route=route, stop__code="GUE").delete()
        with self.assertRaises(CommandError):
            self.seed()
        self.assertEqual(trip.trip_stops.count(), 6)

    def test_command_upgrades_legacy_route_without_trips(self):
        self.seed()
        route = Route.objects.get(code="CBA-JUJ")
        RouteStop.objects.filter(route=route, stop__code="GUE").delete()
        for item in sorted(route.route_stops.all(), key=lambda row: row.sequence):
            if item.sequence >= 4:
                item.sequence += 10
                item.save(update_fields=["sequence"])
        for item in sorted(route.route_stops.all(), key=lambda row: row.sequence):
            if item.sequence >= 14:
                item.sequence -= 11
                item.save(update_fields=["sequence"])
        self.seed()
        self.assertEqual(
            list(route.route_stops.order_by("sequence").values_list("stop__code", "sequence")),
            [("CBA", 1), ("JMA", 2), ("GUE", 3), ("PER", 4), ("PAL", 5), ("SSJ", 6)],
        )

    def create_trip_for_command(self, route, bus):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        start = datetime(2030, 1, 10, 8, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
        return schedule_trip(
            route=route,
            bus=bus,
            schedules={item.stop_id: start + timedelta(hours=item.sequence)
                       for item in route.route_stops.all()},
        )

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

    def ensure_active_seat(self):
        Seat.objects.get_or_create(bus=self.bus, number=1, defaults={
            "deck": Seat.Deck.UPPER, "category": SeatCategory.SEMI_CAMA,
            "position_x": 0, "position_y": 0,
        })

    def create_trip(self, route=None, days_offset=0):
        self.ensure_active_seat()
        schedules = {key: value + timedelta(days=days_offset) for key, value in self.schedules(route).items()}
        return schedule_trip(route=route or self.route, bus=self.bus, schedules=schedules)

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
    def setUp(self):
        self.ensure_active_seat()

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
                self.start += timedelta(days=1)

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
        self.assertEqual(trip.trip_stops.count(), 6)
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


class UpdateTripSchedulesTests(OperationsDataMixin, TestCase):
    """Pruebas de actualización de horarios estimados por parada reutilizando TripStop.scheduled_at."""

    def setUp(self):
        self.ensure_active_seat()
        self.trip = self.create_trip()

    def test_updates_schedules_across_midnight_in_buenos_aires_reusing_trip_stop(self):
        tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
        night_departure = datetime(2030, 1, 15, 23, 30, tzinfo=tz_ar)
        new_schedules = {
            self.stops["CBA"].pk: night_departure,
            self.stops["JMA"].pk: datetime(2030, 1, 16, 0, 20, tzinfo=tz_ar),
            self.stops["GUE"].pk: datetime(2030, 1, 16, 9, 0, tzinfo=tz_ar),
            self.stops["PER"].pk: datetime(2030, 1, 16, 9, 45, tzinfo=tz_ar),
            self.stops["PAL"].pk: datetime(2030, 1, 16, 10, 15, tzinfo=tz_ar),
            self.stops["SSJ"].pk: datetime(2030, 1, 16, 10, 45, tzinfo=tz_ar),
        }

        updated_trip = update_trip_schedules(trip=self.trip, schedules=new_schedules)
        self.assertEqual(updated_trip.pk, self.trip.pk)
        self.assertEqual(Trip.objects.count(), 1)
        self.assertEqual(TripStop.objects.filter(trip=self.trip).count(), 6)

        self.trip.refresh_from_db()
        self.assertEqual(self.trip.departure_at, night_departure)
        for stop_id, expected_dt in new_schedules.items():
            ts = self.trip.trip_stops.get(stop_id=stop_id)
            self.assertEqual(ts.scheduled_at, expected_dt)
            self.assertEqual(ts.scheduled_at.astimezone(tz_ar).strftime("%Y-%m-%d %H:%M"), expected_dt.strftime("%Y-%m-%d %H:%M"))

    def test_rejects_midnight_inversion_on_same_day(self):
        tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
        night_departure = datetime(2030, 1, 15, 23, 30, tzinfo=tz_ar)
        invalid_schedules = {
            self.stops["CBA"].pk: night_departure,
            # Error común: poner 00:20 del mismo día 15/01 en lugar del 16/01
            self.stops["JMA"].pk: datetime(2030, 1, 15, 0, 20, tzinfo=tz_ar),
            self.stops["GUE"].pk: datetime(2030, 1, 16, 9, 0, tzinfo=tz_ar),
            self.stops["PER"].pk: datetime(2030, 1, 16, 9, 45, tzinfo=tz_ar),
            self.stops["PAL"].pk: datetime(2030, 1, 16, 10, 15, tzinfo=tz_ar),
            self.stops["SSJ"].pk: datetime(2030, 1, 16, 10, 45, tzinfo=tz_ar),
        }
        before = dict(self.trip.trip_stops.values_list("stop_id", "scheduled_at"))
        with self.assertRaises(ValidationError):
            update_trip_schedules(trip=self.trip, schedules=invalid_schedules)
        self.assertEqual(dict(self.trip.trip_stops.values_list("stop_id", "scheduled_at")), before)

    def test_rejects_stop_not_in_route_missing_stop_and_naive_datetime(self):
        outside = Stop.objects.create(code="OTRA", name="Otra", city="Otra", province="Otra")
        before = dict(self.trip.trip_stops.values_list("stop_id", "scheduled_at"))

        # Parada ajena al recorrido
        with_outside = self.schedules()
        with_outside[outside.pk] = self.start + timedelta(hours=10)
        with self.assertRaises(ValidationError):
            update_trip_schedules(trip=self.trip, schedules=with_outside)

        # Parada faltante
        missing_one = self.schedules()
        del missing_one[self.stops["PER"].pk]
        with self.assertRaises(ValidationError):
            update_trip_schedules(trip=self.trip, schedules=missing_one)

        # Fecha/hora sin zona horaria (naive)
        naive_times = self.schedules()
        naive_times[self.stops["JMA"].pk] = (self.start + timedelta(hours=1)).replace(tzinfo=None)
        with self.assertRaises(ValidationError):
            update_trip_schedules(trip=self.trip, schedules=naive_times)

        self.assertEqual(dict(self.trip.trip_stops.values_list("stop_id", "scheduled_at")), before)

    def test_rejects_started_completed_cancelled_and_bus_overlaps(self):
        other_trip = self.create_trip(days_offset=2)
        overlapping_schedules = {
            item.stop_id: other_trip.departure_at + timedelta(hours=index)
            for index, item in enumerate(self.trip.trip_stops.order_by("sequence"))
        }
        with self.assertRaisesMessage(ValidationError, "superpuestos"):
            update_trip_schedules(trip=self.trip, schedules=overlapping_schedules)

        for status in (Trip.Status.STARTED, Trip.Status.COMPLETED, Trip.Status.CANCELLED):
            Trip.objects.filter(pk=self.trip.pk).update(status=status)
            with self.subTest(status=status), self.assertRaises(ValidationError):
                update_trip_schedules(
                    trip=self.trip,
                    schedules={k: v + timedelta(hours=1) for k, v in self.schedules().items()},
                )


class FareTests(OperationsDataMixin, TestCase):
    def setUp(self):
        self.trip = self.create_trip()
        self.trip_stops = {item.stop.code: item for item in self.trip.trip_stops.select_related("stop")}

    def make_fare(self, **changes):
        values = dict(trip=self.trip, seat_category=SeatCategory.SEMI_CAMA,
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

    def test_each_departure_can_have_cama_and_semicama_fares_in_both_directions(self):
        for index, route in enumerate(Route.objects.order_by("code"), 1):
            trip = self.create_trip(route, days_offset=index)
            for category, amount in ((SeatCategory.CAMA, Decimal("18000.00")),
                                     (SeatCategory.SEMI_CAMA, Decimal("14000.00"))):
                fare = self.make_fare(trip=trip, seat_category=category, amount=amount)
                fare.full_clean()
                fare.save()
            self.assertEqual(trip.fares.count(), 2)

    def test_fare_combination_is_unique_even_when_inactive(self):
        self.make_fare(is_active=False).save()
        fare = self.make_fare()
        with self.assertRaises(ValidationError):
            fare.full_clean()
        self.assert_database_rejects(fare.save)

    def test_same_trip_can_have_different_categories_and_other_trip_can_have_same_category(self):
        for category in SeatCategory:
            fare = self.make_fare(seat_category=category)
            fare.full_clean()
            fare.save()
        self.assertEqual(self.trip.fares.count(), 2)
        other_trip = self.create_trip(days_offset=1)
        other_fare = self.make_fare(trip=other_trip, seat_category=SeatCategory.CAMA)
        other_fare.full_clean()
        other_fare.save()
        self.assertEqual(other_trip.fares.count(), 1)

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

    def test_fare_protects_its_historical_trip(self):
        fare = self.make_fare()
        fare.save()
        with self.assertRaises(ProtectedError):
            self.trip.delete()

    def test_historical_fare_conflict_detection_rejects_divergent_prices_and_allows_identical(self):
        unambiguous = [
            {"trip_id": self.trip.pk, "seat_category": SeatCategory.CAMA, "amount": Decimal("15000.00"), "currency": "ARS", "is_active": True},
            {"trip_id": self.trip.pk, "seat_category": SeatCategory.CAMA, "amount": Decimal("15000.00"), "currency": "ARS", "is_active": True},
            {"trip_id": self.trip.pk, "seat_category": SeatCategory.SEMI_CAMA, "amount": Decimal("12000.00"), "currency": "ARS", "is_active": True},
        ]
        self.assertEqual(detect_trip_fare_conflicts(unambiguous), [])
        self.assertEqual(validate_no_trip_fare_conflicts(unambiguous), [])

        ambiguous = unambiguous + [
            {"trip_id": self.trip.pk, "seat_category": SeatCategory.CAMA, "amount": Decimal("16500.00"), "currency": "ARS", "is_active": True},
        ]
        conflicts = detect_trip_fare_conflicts(ambiguous)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["trip_id"], self.trip.pk)
        self.assertEqual(conflicts[0]["seat_category"], SeatCategory.CAMA)
        with self.assertRaises(ValidationError) as ctx:
            validate_no_trip_fare_conflicts(ambiguous)
        self.assertIn("tarifas históricas ambiguas", str(ctx.exception))
