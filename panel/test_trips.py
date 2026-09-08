from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, Seat, SeatCategory, Trip, TripFare, TripStop
from operations.services import schedule_trip
from .models import AuditEvent
from .trip_forms import TripFareForm
from .trip_services import create_panel_trip, save_fare, set_fare_active


class TripDataMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command("setup_initial_operations", stdout=StringIO())
        cls.route = Route.objects.get(code="CBA-JUJ")
        cls.bus = Bus.objects.create(code="VIAJES", display_name="Colectivo de prueba")
        cls.seat = Seat.objects.create(bus=cls.bus, number=1, deck=Seat.Deck.LOWER,
                                      category=SeatCategory.CAMA, position_x=0, position_y=0)
        users = get_user_model()
        cls.admin = users.objects.create_user(username="admin-viajes")
        cls.admin.groups.add(Group.objects.get_or_create(name="Administrador")[0])
        cls.seller = users.objects.create_user(username="vendedor-viajes")
        cls.seller.groups.add(Group.objects.get_or_create(name="Vendedor")[0])
        cls.staff = users.objects.create_user(username="staff-viajes", is_staff=True)
        cls.superuser = users.objects.create_user(username="super-viajes", is_superuser=True)
        cls.common = users.objects.create_user(username="comun-viajes")
        cls.start = timezone.localtime(timezone.now() + timedelta(days=2)).replace(hour=8, minute=0, second=0, microsecond=0)

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, **kwargs):
        return reverse(f"panel:{name}", kwargs=kwargs)

    def schedules(self, start=None):
        return {item.stop_id: (start or self.start) + timedelta(hours=index)
                for index, item in enumerate(self.route.route_stops.all())}

    def schedule_data(self, start=None):
        return {f"stop_{pk}": value.strftime("%Y-%m-%dT%H:%M") for pk, value in self.schedules(start).items()}

    def schedule_url(self):
        return self.url("trip_schedule", route_pk=self.route.pk, bus_pk=self.bus.pk)

    def review(self, data=None):
        return self.client.post(self.schedule_url(), {**(data or self.schedule_data()), "action": "review"}, secure=True)

    def create(self, data=None):
        data = data or self.schedule_data()
        review = self.review(data)
        self.assertIn("confirmation", review.context)
        return self.client.post(self.schedule_url(), {**data, "action": "create",
            "confirmation": review.context["confirmation"]}, secure=True)

    def make_trip(self, start=None, bus=None, status=Trip.Status.SCHEDULED):
        trip = schedule_trip(route=self.route, bus=bus or self.bus, schedules=self.schedules(start))
        if status != Trip.Status.SCHEDULED:
            trip.status = status
            trip.save(update_fields=["status"])
        return trip

    def fare_data(self, trip, **changes):
        data = {"origin_stop": trip.trip_stops.get(sequence=1).pk,
                "destination_stop": trip.trip_stops.get(sequence=5).pk,
                "seat_category": SeatCategory.CAMA, "amount": "12345.67", "currency": "ARS"}
        data.update(changes)
        return data

    def make_fare(self, trip, **changes):
        data = self.fare_data(trip, **changes)
        return TripFare.objects.create(trip=trip, origin_stop_id=data["origin_stop"],
            destination_stop_id=data["destination_stop"], seat_category=data["seat_category"],
            amount=Decimal(data["amount"]), currency=data["currency"])

    def database_state(self):
        return [list(model.objects.order_by("pk").values()) for model in (Trip, TripStop, TripFare, AuditEvent)]


class TripCreationTests(TripDataMixin, TestCase):
    def test_complete_two_step_flow_with_visual_confirmation(self):
        response = self.client.post(self.url("trip_create"), {"route": self.route.pk, "bus": self.bus.pk}, secure=True)
        self.assertRedirects(response, self.schedule_url(), fetch_redirect_response=False)
        response = self.client.get(self.schedule_url(), secure=True)
        self.assertContains(response, 'type="datetime-local"', count=5)
        self.assertContains(response, "Subida: sí. Bajada: no.")
        self.assertFalse(Trip.objects.exists())
        review = self.review()
        self.assertTemplateUsed(review, "panel/operations/trip_confirm.html")
        self.assertContains(review, "Confirmar y crear viaje")
        self.assertFalse(Trip.objects.exists())
        response = self.create()
        trip = Trip.objects.get()
        self.assertRedirects(response, self.url("trip_detail", pk=trip.pk), fetch_redirect_response=False)
        self.assertEqual(trip.status, Trip.Status.SCHEDULED)
        self.assertEqual(trip.route, self.route)
        self.assertEqual(trip.bus, self.bus)

    def test_local_times_are_aware_and_snapshot_is_exact(self):
        self.create()
        trip = Trip.objects.get()
        self.assertTrue(timezone.is_aware(trip.departure_at))
        self.assertEqual(timezone.localtime(trip.departure_at).hour, 8)
        self.assertEqual(trip.departure_at.utcoffset(), timedelta(0))
        self.assertEqual(trip.departure_at.hour, 11)
        self.assertEqual(dict(trip.trip_stops.values_list("stop_id", "scheduled_at")), self.schedules())
        self.assertEqual(list(trip.trip_stops.values_list("stop_id", "sequence", "allows_boarding", "allows_alighting")),
                         list(self.route.route_stops.values_list("stop_id", "sequence", "allows_boarding", "allows_alighting")))

    def test_inactive_route_or_bus_and_bus_without_active_seats_are_not_choices(self):
        for model, instance in ((Route, self.route), (Bus, self.bus), (Seat, self.seat)):
            with self.subTest(model=model.__name__):
                model.objects.filter(pk=instance.pk).update(is_active=False)
                response = self.client.post(self.url("trip_create"), {"route": self.route.pk, "bus": self.bus.pk}, secure=True)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(self.client.get(self.schedule_url(), secure=True).status_code, 404)
                self.assertEqual(self.review().status_code, 404)
                model.objects.filter(pk=instance.pk).update(is_active=True)
        self.assertFalse(Trip.objects.exists())

    def test_deleted_objects_are_rechecked_in_second_step(self):
        url = self.url("trip_schedule", route_pk=self.route.pk, bus_pk=999999)
        self.assertEqual(self.client.get(url, secure=True).status_code, 404)
        self.assertFalse(Trip.objects.exists())

    def test_first_departure_in_past_is_rejected(self):
        response = self.review(self.schedule_data(timezone.now() - timedelta(days=1)))
        self.assertContains(response, "La salida debe estar en el futuro.")
        self.assertFalse(Trip.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_missing_extra_equal_and_unordered_times_are_rejected(self):
        keys = list(self.schedule_data())
        missing = self.schedule_data()
        del missing[keys[2]]
        extra = {**self.schedule_data(), "stop_999999": "2030-01-01T08:00"}
        equal = self.schedule_data()
        equal[keys[1]] = equal[keys[0]]
        unordered = self.schedule_data()
        unordered[keys[2]] = unordered[keys[0]]
        for data in (missing, extra, equal, unordered):
            with self.subTest(data=data):
                response = self.review(data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertNotIn("confirmation", response.context)
                self.assertFalse(Trip.objects.exists())
                self.assertFalse(AuditEvent.objects.exists())

    def test_duplicate_schedule_fields_are_rejected(self):
        data = self.schedule_data()
        key = next(iter(data))
        data[key] = [data[key], data[key]]
        response = self.review(data)
        self.assertContains(response, "sin faltantes ni duplicados")
        self.assertFalse(Trip.objects.exists())

    def test_confirmation_cannot_be_skipped_or_tampered(self):
        before = self.database_state()
        data = self.schedule_data()
        response = self.client.post(self.schedule_url(), {**data, "action": "create"}, secure=True)
        self.assertContains(response, "falta la confirmación")
        review = self.review(data)
        data[next(iter(data))] = (self.start - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(self.schedule_url(), {**data, "action": "create",
            "confirmation": review.context["confirmation"]}, secure=True)
        self.assertContains(response, "Los datos cambiaron")
        self.assertEqual(self.database_state(), before)

    def test_correcting_schedule_does_not_create_trip(self):
        review = self.review()
        response = self.client.post(self.schedule_url(), {**self.schedule_data(), "action": "edit",
            "confirmation": review.context["confirmation"]}, secure=True)
        self.assertTemplateUsed(response, "panel/operations/trip_form.html")
        self.assertFalse(response.context["form"].errors)
        self.assertFalse(Trip.objects.exists())

    def test_audit_contains_one_complete_trip_snapshot(self):
        self.create()
        trip = Trip.objects.get()
        event = AuditEvent.objects.get()
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.action, AuditEvent.Action.CREATE)
        self.assertEqual(event.entity_type, "operations.Trip")
        self.assertEqual(event.entity_id, str(trip.pk))
        self.assertEqual(event.before, {})
        self.assertEqual(event.after["route_id"], self.route.pk)
        self.assertEqual(event.after["bus_id"], self.bus.pk)
        self.assertEqual(event.after["status"], Trip.Status.SCHEDULED)
        self.assertEqual(datetime.fromisoformat(event.after["departure_at"]), trip.departure_at)
        for saved, row in zip(trip.trip_stops.all(), event.after["stops"]):
            self.assertEqual(row["stop_id"], saved.stop_id)
            self.assertEqual(row["sequence"], saved.sequence)
            self.assertEqual(row["allows_boarding"], saved.allows_boarding)
            self.assertEqual(row["allows_alighting"], saved.allows_alighting)
            self.assertEqual(datetime.fromisoformat(row["scheduled_at"]), saved.scheduled_at)

    def test_stop_failure_rolls_back_trip_and_previous_stops(self):
        original = TripStop.save
        observed = []
        def fail_third(instance, *args, **kwargs):
            if instance.sequence == 3:
                observed.append((Trip.objects.count(), TripStop.objects.count()))
                raise IntegrityError("Fallo simulado de parada")
            return original(instance, *args, **kwargs)
        with patch.object(TripStop, "save", fail_third):
            response = self.create()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(observed, [(1, 2)])
        self.assertFalse(Trip.objects.exists())
        self.assertFalse(TripStop.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_audit_failure_rolls_back_trip_and_all_stops(self):
        observed = []
        def fail_audit(**kwargs):
            observed.append((Trip.objects.count(), TripStop.objects.count()))
            raise IntegrityError("Fallo simulado de auditoría")
        with patch("panel.services.AuditEvent.objects.create", side_effect=fail_audit):
            response = self.create()
        self.assertContains(response, "No se guardó ningún cambio")
        self.assertEqual(observed, [(1, 5)])
        self.assertFalse(Trip.objects.exists())
        self.assertFalse(TripStop.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_domain_rechecks_active_structure_using_fresh_objects(self):
        for model, instance in ((Route, self.route), (Bus, self.bus), (Seat, self.seat)):
            model.objects.filter(pk=instance.pk).update(is_active=False)
            with self.assertRaises(ValidationError):
                schedule_trip(route=self.route, bus=self.bus, schedules=self.schedules())
            model.objects.filter(pk=instance.pk).update(is_active=True)
        self.assertFalse(Trip.objects.exists())

    def test_domain_rejects_naive_datetimes(self):
        naive = {key: value.replace(tzinfo=None) for key, value in self.schedules().items()}
        with self.assertRaises(ValidationError):
            schedule_trip(route=self.route, bus=self.bus, schedules=naive)
        self.assertFalse(Trip.objects.exists())

    def test_domain_allows_historical_dates_but_panel_service_rejects_them(self):
        past = self.start - timedelta(days=5)
        with self.assertRaises(ValidationError):
            create_panel_trip(actor=self.admin, route=self.route, bus=self.bus, schedules=self.schedules(past))
        self.assertFalse(Trip.objects.exists())
        self.assertEqual(self.make_trip(start=past).departure_at, past)


class OverlapTests(TripDataMixin, TestCase):
    def test_overlap_in_either_direction_and_containment_is_rejected(self):
        self.make_trip()
        for start in (self.start, self.start - timedelta(hours=1), self.start + timedelta(hours=1)):
            before = self.database_state()
            with self.assertRaisesMessage(ValidationError, "superpuestos"):
                self.make_trip(start=start)
            self.assertEqual(self.database_state(), before)
        times = self.schedules(self.start - timedelta(hours=1))
        times[list(times)[-1]] = self.start + timedelta(hours=6)
        with self.assertRaisesMessage(ValidationError, "superpuestos"):
            schedule_trip(route=self.route, bus=self.bus, schedules=times)

    def test_touching_intervals_are_allowed_on_both_ends(self):
        self.make_trip()
        self.make_trip(self.start + timedelta(hours=4))
        self.make_trip(self.start - timedelta(hours=4))
        self.assertEqual(Trip.objects.count(), 3)

    def test_other_bus_can_overlap(self):
        self.make_trip()
        other = Bus.objects.create(code="OTRO", display_name="Otro")
        Seat.objects.create(bus=other, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)
        self.make_trip(bus=other)
        self.assertEqual(Trip.objects.count(), 2)

    def test_cancelled_trip_does_not_block(self):
        self.make_trip(status=Trip.Status.CANCELLED)
        self.make_trip()
        self.assertEqual(Trip.objects.count(), 2)

    def test_all_non_cancelled_states_block(self):
        trip = self.make_trip()
        for status in (Trip.Status.SCHEDULED, Trip.Status.BOARDING, Trip.Status.STARTED, Trip.Status.COMPLETED):
            Trip.objects.filter(pk=trip.pk).update(status=status)
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.make_trip()
        self.assertEqual(Trip.objects.count(), 1)

    def test_overlap_uses_trip_snapshot_not_modified_route(self):
        self.make_trip()
        self.route.route_stops.all().delete()
        reverse_route = Route.objects.get(code="JUJ-CBA")
        times = {item.stop_id: self.start + timedelta(hours=index) for index, item in enumerate(reverse_route.route_stops.all())}
        with self.assertRaisesMessage(ValidationError, "superpuestos"):
            schedule_trip(route=reverse_route, bus=self.bus, schedules=times)

    def test_panel_reports_overlap_without_partial_trip_or_audit(self):
        self.make_trip()
        before = self.database_state()
        response = self.create()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "superpuestos")
        self.assertEqual(self.database_state(), before)


class TripPermissionTests(TripDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.trip = self.make_trip(self.start + timedelta(days=1))
        self.fare = self.make_fare(self.trip)

    def read_urls(self):
        return [self.url("trips"), self.url("trip_detail", pk=self.trip.pk)]

    def writes(self):
        return [
            (self.url("trip_create"), {"route": self.route.pk, "bus": self.bus.pk}),
            (self.schedule_url(), {**self.schedule_data(), "action": "review"}),
            (self.url("fare_create", trip_pk=self.trip.pk), self.fare_data(self.trip)),
            (self.url("fare_edit", trip_pk=self.trip.pk, pk=self.fare.pk), self.fare_data(self.trip, amount="999.00")),
            (self.url("fare_activate", trip_pk=self.trip.pk, pk=self.fare.pk), {}),
            (self.url("fare_deactivate", trip_pk=self.trip.pk, pk=self.fare.pk), {}),
        ]

    def test_seller_and_staff_can_read_but_all_writes_are_forbidden(self):
        for user in (self.seller, self.staff):
            self.client.force_login(user)
            for url in self.read_urls():
                response = self.client.get(url, secure=True)
                self.assertEqual(response.status_code, 200)
                for write_url, _ in self.writes():
                    self.assertNotContains(response, write_url)
            before = self.database_state()
            for url, data in self.writes():
                self.assertEqual(self.client.post(url, data, secure=True).status_code, 403)
            self.assertEqual(self.database_state(), before)

    def test_anonymous_redirect_and_common_user_rejection(self):
        self.client.logout()
        for url in self.read_urls():
            self.assertRedirects(self.client.get(url, secure=True), f"{self.url('login')}?next={url}", fetch_redirect_response=False)
        for url, data in self.writes():
            self.assertEqual(self.client.post(url, data, secure=True).status_code, 302)
        self.client.force_login(self.common)
        before = self.database_state()
        for url in self.read_urls():
            self.assertEqual(self.client.get(url, secure=True).status_code, 403)
        for url, data in self.writes():
            self.assertEqual(self.client.post(url, data, secure=True).status_code, 403)
        self.assertEqual(self.database_state(), before)

    def test_admin_and_superuser_can_create_and_manage_fares(self):
        for index, user in enumerate((self.admin, self.superuser), 2):
            self.client.force_login(user)
            response = self.create(self.schedule_data(self.start + timedelta(days=index)))
            self.assertEqual(response.status_code, 302)
            trip = Trip.objects.latest("pk")
            response = self.client.post(self.url("fare_create", trip_pk=trip.pk), self.fare_data(trip), secure=True)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(AuditEvent.objects.first().actor, user)

    def test_direct_service_access_is_guarded(self):
        before = self.database_state()
        for user in (self.seller, self.staff, self.common):
            with self.assertRaises(PermissionDenied):
                create_panel_trip(actor=user, route=self.route, bus=self.bus, schedules=self.schedules())
            with self.assertRaises(PermissionDenied):
                save_fare(actor=user, trip_pk=self.trip.pk, data=self.fare_data(self.trip))
            with self.assertRaises(PermissionDenied):
                set_fare_active(actor=user, trip_pk=self.trip.pk, pk=self.fare.pk, active=False)
        self.assertEqual(self.database_state(), before)

    def test_every_write_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        before = self.database_state()
        for url, data in self.writes():
            self.assertEqual(client.post(url, data, secure=True).status_code, 403)
        self.assertEqual(self.database_state(), before)

    def test_no_trip_edit_state_or_delete_and_no_fare_delete(self):
        before = self.database_state()
        for suffix in ("editar/", "estado/", "eliminar/", "horarios/editar/", f"tarifas/{self.fare.pk}/eliminar/"):
            self.assertEqual(self.client.post(f"/panel/operaciones/viajes/{self.trip.pk}/{suffix}", secure=True).status_code, 404)
        self.assertEqual(self.database_state(), before)


class TripFarePanelTests(TripDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.trip = self.make_trip()

    def test_creation_and_edit_use_decimal_and_audit_snapshots(self):
        response = self.client.post(self.url("fare_create", trip_pk=self.trip.pk), self.fare_data(self.trip), secure=True)
        self.assertEqual(response.status_code, 302)
        fare = TripFare.objects.get()
        self.assertIsInstance(fare.amount, Decimal)
        self.assertEqual(fare.amount, Decimal("12345.67"))
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditEvent.Action.CREATE)
        self.assertEqual(event.after["amount"], "12345.67")
        self.assertEqual(event.actor, self.admin)
        self.assertTrue(timezone.is_aware(event.created_at))
        response = self.client.post(self.url("fare_edit", trip_pk=self.trip.pk, pk=fare.pk), self.fare_data(self.trip, amount="987.65"), secure=True)
        self.assertEqual(response.status_code, 302)
        fare.refresh_from_db()
        self.assertEqual(fare.amount, Decimal("987.65"))
        event = AuditEvent.objects.first()
        self.assertEqual(event.action, AuditEvent.Action.UPDATE)
        self.assertEqual(event.before["amount"], "12345.67")
        self.assertEqual(event.after["amount"], "987.65")

    def test_options_are_scoped_to_trip_and_permissions(self):
        other = self.make_trip(self.start + timedelta(days=1))
        form = TripFareForm(trip=self.trip)
        self.assertEqual(list(form.fields), ["origin_stop", "destination_stop", "seat_category", "amount", "currency"])
        for field, permission in (("origin_stop", "allows_boarding"), ("destination_stop", "allows_alighting")):
            choices = list(form.fields[field].queryset)
            self.assertTrue(choices)
            self.assertTrue(all(item.trip_id == self.trip.pk and getattr(item, permission) for item in choices))
            self.assertFalse(any(item.trip_id == other.pk for item in choices))
        self.assertNotIn("Select an option", form.as_p())
        self.assertIn("Seleccioná una categoría", form.as_p())

    def test_foreign_stops_are_rejected_and_foreign_trip_post_is_ignored(self):
        other = self.make_trip(self.start + timedelta(days=1))
        for field, sequence in (("origin_stop", 1), ("destination_stop", 5)):
            before = self.database_state()
            data = self.fare_data(self.trip, **{field: other.trip_stops.get(sequence=sequence).pk})
            response = self.client.post(self.url("fare_create", trip_pk=self.trip.pk), data, secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn(field, response.context["form"].errors)
            self.assertEqual(self.database_state(), before)
        data = {**self.fare_data(self.trip), "trip": other.pk, "is_active": False}
        self.client.post(self.url("fare_create", trip_pk=self.trip.pk), data, secure=True)
        fare = TripFare.objects.get()
        self.assertEqual(fare.trip, self.trip)
        self.assertTrue(fare.is_active)

    def test_invalid_segment_or_nonpositive_amount_is_rejected(self):
        for changes in ({"amount": "0"}, {"amount": "-1"}, {"amount": "1.001"},
                        {"origin_stop": self.trip.trip_stops.get(sequence=5).pk},
                        {"destination_stop": self.trip.trip_stops.get(sequence=1).pk}):
            response = self.client.post(self.url("fare_create", trip_pk=self.trip.pk), self.fare_data(self.trip, **changes), secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertFalse(TripFare.objects.exists())
            self.assertFalse(AuditEvent.objects.exists())

    def test_duplicate_fare_is_controlled_even_if_inactive(self):
        fare = self.make_fare(self.trip)
        TripFare.objects.filter(pk=fare.pk).update(is_active=False)
        before = self.database_state()
        response = self.client.post(self.url("fare_create", trip_pk=self.trip.pk), self.fare_data(self.trip), secure=True)
        self.assertContains(response, "Ya existe una tarifa")
        self.assertEqual(self.database_state(), before)

    def test_uniqueness_race_is_controlled(self):
        before = self.database_state()
        with patch.object(TripFare, "save", side_effect=IntegrityError("Colisión simulada")):
            response = self.client.post(self.url("fare_create", trip_pk=self.trip.pk), self.fare_data(self.trip), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(self.database_state(), before)

    def test_fare_state_is_post_only_and_idempotent(self):
        fare = self.make_fare(self.trip)
        for name, active, action in (("fare_deactivate", False, AuditEvent.Action.DEACTIVATE),
                                     ("fare_activate", True, AuditEvent.Action.ACTIVATE)):
            url = self.url(name, trip_pk=self.trip.pk, pk=fare.pk)
            before = self.database_state()
            self.assertEqual(self.client.get(url, secure=True).status_code, 405)
            self.assertEqual(self.database_state(), before)
            self.client.post(url, secure=True)
            fare.refresh_from_db()
            self.assertEqual(fare.is_active, active)
            event = AuditEvent.objects.first()
            self.assertEqual(event.action, action)
            self.assertEqual(event.before["is_active"], not active)
            self.assertEqual(event.after["is_active"], active)
            before = self.database_state()
            self.client.post(url, secure=True)
            self.assertEqual(self.database_state(), before)

    def test_wrong_url_trip_cannot_edit_or_toggle_fare(self):
        other = self.make_trip(self.start + timedelta(days=1))
        fare = self.make_fare(self.trip)
        before = self.database_state()
        for name in ("fare_edit", "fare_activate", "fare_deactivate"):
            response = self.client.post(self.url(name, trip_pk=other.pk, pk=fare.pk), self.fare_data(other), secure=True)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.database_state(), before)

    def test_audit_failure_rolls_back_fare_creation_edit_and_state(self):
        before = self.database_state()
        with patch("panel.services.AuditEvent.objects.create", side_effect=IntegrityError("Auditoría no disponible")) as audit:
            self.client.post(self.url("fare_create", trip_pk=self.trip.pk), self.fare_data(self.trip), secure=True)
        audit.assert_called_once()
        self.assertEqual(self.database_state(), before)
        fare = self.make_fare(self.trip)
        for name in ("fare_edit", "fare_deactivate", "fare_activate"):
            TripFare.objects.filter(pk=fare.pk).update(is_active=name != "fare_activate")
            before = self.database_state()
            with patch("panel.services.AuditEvent.objects.create", side_effect=IntegrityError("Auditoría no disponible")) as audit:
                response = self.client.post(self.url(name, trip_pk=self.trip.pk, pk=fare.pk), self.fare_data(self.trip, amount="999.99"), secure=True)
            audit.assert_called_once()
            self.assertIn(response.status_code, (200, 302))
            self.assertEqual(self.database_state(), before)

    def test_unchanged_fare_edit_does_not_audit(self):
        fare = self.make_fare(self.trip)
        self.client.post(self.url("fare_edit", trip_pk=self.trip.pk, pk=fare.pk), self.fare_data(self.trip), secure=True)
        self.assertFalse(AuditEvent.objects.exists())


class TripReadTests(TripDataMixin, TestCase):
    def test_list_order_is_future_first_then_past_descending(self):
        future_late = self.make_trip(self.start + timedelta(days=2))
        past_old = self.make_trip(self.start - timedelta(days=10))
        future_early = self.make_trip()
        past_recent = self.make_trip(self.start - timedelta(days=5))
        response = self.client.get(self.url("trips"), secure=True)
        self.assertEqual([trip.pk for trip in response.context["upcoming"]], [future_early.pk, future_late.pk])
        self.assertEqual([trip.pk for trip in response.context["past"]], [past_recent.pk, past_old.pk])
        html = response.content.decode()
        self.assertLess(html.index("Próximos viajes"), html.index("Viajes pasados"))

    def test_local_times_complete_schedule_and_fares_are_visible(self):
        trip = self.make_trip()
        fare = self.make_fare(trip)
        response = self.client.get(self.url("trip_detail", pk=trip.pk), secure=True)
        self.assertContains(response, self.start.strftime("%d/%m/%Y %H:%M"))
        self.assertContains(response, (self.start + timedelta(hours=4)).strftime("%d/%m/%Y %H:%M"))
        self.assertEqual(len(response.context["trip"].trip_stops.all()), 5)
        self.assertEqual([item.pk for item in response.context["trip"].fares.all()], [fare.pk])
        for item in trip.trip_stops.select_related("stop"):
            self.assertContains(response, item.stop.name)
        self.assertContains(response, "12345,67")
        self.assertContains(response, "ARS")
        self.assertContains(response, "Cama")
        self.assertContains(response, "Activa")

    def test_list_and_detail_empty_states(self):
        response = self.client.get(self.url("trips"), secure=True)
        self.assertContains(response, "Todavía no hay viajes próximos")
        self.assertContains(response, "No hay viajes pasados")
        trip = self.make_trip()
        self.assertContains(self.client.get(self.url("trip_detail", pk=trip.pk), secure=True), "Todavía no hay tarifas")

    def test_list_has_no_query_growth_per_trip(self):
        self.make_trip()
        with CaptureQueriesContext(connection) as initial:
            self.client.get(self.url("trips"), secure=True)
        for index in range(1, 8):
            self.make_trip(self.start + timedelta(days=index))
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(self.url("trips"), secure=True)
        self.assertEqual(len(response.context["upcoming"]), 8)
        self.assertEqual(len(expanded), len(initial))

    def test_detail_has_no_query_growth_per_fare(self):
        trip = self.make_trip()
        self.make_fare(trip)
        with CaptureQueriesContext(connection) as initial:
            self.client.get(self.url("trip_detail", pk=trip.pk), secure=True)
        self.make_fare(trip, seat_category=SeatCategory.SEMI_CAMA)
        with CaptureQueriesContext(connection) as expanded:
            response = self.client.get(self.url("trip_detail", pk=trip.pk), secure=True)
        self.assertEqual(len(response.context["trip"].fares.all()), 2)
        self.assertEqual(len(expanded), len(initial))

    def test_overview_counts_scheduled_future_and_boarding(self):
        self.make_trip()
        self.make_trip(self.start + timedelta(days=1), status=Trip.Status.BOARDING)
        self.make_trip(self.start - timedelta(days=5))
        self.make_trip(self.start + timedelta(days=2), status=Trip.Status.CANCELLED)
        response = self.client.get(self.url("operations"), secure=True)
        metrics = dict(response.context["metrics"])
        self.assertEqual(metrics["Viajes programados futuros"], 1)
        self.assertEqual(metrics["Viajes en embarque"], 1)
        self.assertContains(response, self.url("trips"))

    def test_admin_and_remote_frontend_regression(self):
        trip = self.make_trip()
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)
        self.assertEqual(self.client.get("/admin/", secure=True).status_code, 404)
        for url in (self.url("trips"), self.url("trip_detail", pk=trip.pk), self.url("trip_create"), self.schedule_url()):
            response = self.client.get(url, secure=True)
            for remote in ('src="http', 'cdn.tailwindcss', 'cdn.jsdelivr', 'unpkg.com'):
                self.assertNotContains(response, remote)
