from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop
from .forms import BusForm, SeatForm
from .models import AuditEvent
from .services import save_configuration, set_configuration_active


class OperationsPanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.admin = users.objects.create_user(username="administrador")
        cls.admin.groups.add(Group.objects.create(name="Administrador"))
        cls.seller = users.objects.create_user(username="vendedor")
        cls.seller.groups.add(Group.objects.create(name="Vendedor"))
        cls.staff = users.objects.create_user(username="personal", is_staff=True)
        cls.superuser = users.objects.create_user(username="superusuario", is_superuser=True)
        cls.common = users.objects.create_user(username="comun")
        cls.bus = Bus.objects.create(code="TEST-1", display_name="Colectivo de prueba")
        cls.other_bus = Bus.objects.create(code="TEST-2", display_name="Otro colectivo", is_active=False)
        cls.seat = Seat.objects.create(bus=cls.bus, number=1, deck=Seat.Deck.UPPER,
                                      category=SeatCategory.SEMI_CAMA, position_x=2, position_y=3)

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, name, **kwargs):
        return reverse(f"panel:{name}", kwargs=kwargs)

    def read_urls(self):
        return [self.url("operations"), self.url("routes"), self.url("buses"),
                self.url("bus_detail", pk=self.bus.pk)]

    def mutations(self):
        return [
            (self.url("bus_create"), self.bus_data()),
            (self.url("bus_edit", pk=self.bus.pk), self.bus_data()),
            (self.url("bus_activate", pk=self.bus.pk), {}),
            (self.url("bus_deactivate", pk=self.bus.pk), {}),
            (self.url("seat_create", bus_pk=self.bus.pk), self.seat_data()),
            (self.url("seat_edit", bus_pk=self.bus.pk, pk=self.seat.pk), self.seat_data()),
            (self.url("seat_activate", bus_pk=self.bus.pk, pk=self.seat.pk), {}),
            (self.url("seat_deactivate", bus_pk=self.bus.pk, pk=self.seat.pk), {}),
        ]

    def bus_data(self, **changes):
        data = {"code": "NUEVO", "display_name": "Nuevo colectivo", "license_plate": "AB123CD"}
        data.update(changes)
        return data

    def seat_data(self, **changes):
        data = {"number": 8, "deck": Seat.Deck.LOWER, "category": SeatCategory.CAMA,
                "position_x": 0, "position_y": 0}
        data.update(changes)
        return data

    def state(self):
        return (list(Bus.objects.order_by("pk").values()),
                list(Seat.objects.order_by("pk").values()),
                list(AuditEvent.objects.order_by("pk").values()))

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        for url in self.read_urls():
            with self.subTest(url=url):
                response = self.client.get(url, secure=True)
                self.assertRedirects(response, f"{self.url('login')}?next={url}", fetch_redirect_response=False)
        before = self.state()
        for url, data in self.mutations():
            self.assertEqual(self.client.post(url, data, secure=True).status_code, 302)
        self.assertEqual(self.state(), before)

    def test_common_user_cannot_read_or_write(self):
        self.client.force_login(self.common)
        for url in self.read_urls():
            self.assertEqual(self.client.get(url, secure=True).status_code, 403)
        before = self.state()
        for url, data in self.mutations():
            self.assertEqual(self.client.post(url, data, secure=True).status_code, 403)
        self.assertEqual(self.state(), before)

    def test_seller_staff_admin_and_superuser_can_read_all_screens(self):
        for user in (self.seller, self.staff, self.admin, self.superuser):
            self.client.force_login(user)
            for url in self.read_urls():
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url, secure=True).status_code, 200)

    def test_seller_and_staff_cannot_write_any_structure(self):
        for user in (self.seller, self.staff):
            self.client.force_login(user)
            for url, data in self.mutations():
                with self.subTest(user=user.username, url=url):
                    before = self.state()
                    self.assertEqual(self.client.post(url, data, secure=True).status_code, 403)
                    self.assertEqual(self.state(), before)

    def test_readers_cannot_open_editing_forms_or_see_write_actions(self):
        for user in (self.seller, self.staff):
            self.client.force_login(user)
            for name, kwargs in (("bus_create", {}), ("bus_edit", {"pk": self.bus.pk}),
                                 ("seat_create", {"bus_pk": self.bus.pk}),
                                 ("seat_edit", {"bus_pk": self.bus.pk, "pk": self.seat.pk})):
                self.assertEqual(self.client.get(self.url(name, **kwargs), secure=True).status_code, 403)
            for url in (self.url("buses"), self.url("bus_detail", pk=self.bus.pk)):
                response = self.client.get(url, secure=True)
                for write_url, _ in self.mutations():
                    self.assertNotContains(response, write_url)

    def test_administrators_can_open_editing_forms(self):
        for name, kwargs in (("bus_create", {}), ("bus_edit", {"pk": self.bus.pk}),
                             ("seat_create", {"bus_pk": self.bus.pk}),
                             ("seat_edit", {"bus_pk": self.bus.pk, "pk": self.seat.pk})):
            response = self.client.get(self.url(name, **kwargs), secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_service_permissions_cannot_be_bypassed(self):
        before = self.state()
        for user in (self.seller, self.staff, self.common):
            with self.assertRaises(PermissionDenied):
                save_configuration(actor=user, data=self.bus_data())
            with self.assertRaises(PermissionDenied):
                set_configuration_active(actor=user, pk=self.bus.pk, active=False)
        self.assertEqual(self.state(), before)

    def test_routes_are_ordered_and_permissions_rendered(self):
        call_command("setup_initial_operations", stdout=StringIO())
        response = self.client.get(self.url("routes"), secure=True)
        rendered = response.content.decode()
        expected = {
            "CBA-JUJ": [("CBA", True, False), ("JMA", True, False), ("PER", False, True),
                        ("PAL", False, True), ("SSJ", False, True)],
            "JUJ-CBA": [("SSJ", True, False), ("PAL", True, False), ("PER", True, False),
                        ("JMA", False, True), ("CBA", False, True)],
        }
        self.assertEqual(len(response.context["routes"]), 2)
        for route in response.context["routes"]:
            rows = list(route.route_stops.all())
            self.assertEqual([(item.stop.code, item.allows_boarding, item.allows_alighting) for item in rows], expected[route.code])
            article = next(part for part in rendered.split('<article') if f"{route.code} ·" in part)
            previous = -1
            for item in rows:
                index = article.index(item.stop.name)
                self.assertGreater(index, previous)
                previous = index
                boarding = "Sí" if item.allows_boarding else "No"
                alighting = "Sí" if item.allows_alighting else "No"
                self.assertIn(f"</small></th><td>{boarding}</td><td>{alighting}</td>", article[index:])

    def test_routes_empty_state_does_not_seed_data(self):
        response = self.client.get(self.url("routes"), secure=True)
        self.assertContains(response, "Todavía no hay recorridos")
        self.assertFalse(Route.objects.exists())
        self.assertFalse(RouteStop.objects.exists())
        self.assertFalse(Stop.objects.exists())

    def test_routes_and_stops_have_no_write_endpoints(self):
        before = (Route.objects.count(), Stop.objects.count())
        for path in ("recorridos/nuevo/", "recorridos/1/editar/", "paradas/nueva/", "paradas/1/editar/"):
            self.assertEqual(self.client.post(f"/panel/operaciones/{path}", secure=True).status_code, 404)
        self.assertEqual(self.client.post(self.url("routes"), secure=True).status_code, 405)
        self.assertEqual((Route.objects.count(), Stop.objects.count()), before)
        self.assertFalse(AuditEvent.objects.exists())

    def test_summary_and_bus_counts_use_active_seats(self):
        call_command("setup_initial_operations", stdout=StringIO())
        Route.objects.filter(code="JUJ-CBA").update(is_active=False)
        Seat.objects.create(bus=self.bus, **self.seat_data())
        Seat.objects.create(bus=self.bus, **self.seat_data(number=9, position_x=1), is_active=False)
        response = self.client.get(self.url("operations"), secure=True)
        self.assertEqual(dict(response.context["metrics"]), {
            "Recorridos activos": 1, "Colectivos activos": 1, "Butacas activas": 2,
            "Butacas cama activas": 1, "Butacas semicama activas": 1,
        })
        response = self.client.get(self.url("buses"), secure=True)
        bus = next(bus for bus in response.context["buses"] if bus.pk == self.bus.pk)
        self.assertEqual((bus.active_seats, bus.cama_seats, bus.semicama_seats), (2, 1, 1))
        self.assertContains(response, "Sin informar")

    def test_empty_bus_list(self):
        self.seat.delete()
        Bus.objects.all().delete()
        self.assertContains(self.client.get(self.url("buses"), secure=True), "Todavía no hay colectivos")

    def test_dashboard_exposes_operations_and_keeps_future_modules(self):
        response = self.client.get(self.url("dashboard"), secure=True)
        self.assertContains(response, self.url("operations"))
        self.assertContains(response, "Próximamente")

    def test_bus_creation_records_actor_and_whitelisted_snapshot(self):
        response = self.client.post(self.url("bus_create"), self.bus_data(is_active=False, secret="no guardar"), secure=True)
        bus = Bus.objects.get(code="NUEVO")
        self.assertRedirects(response, self.url("bus_detail", pk=bus.pk), fetch_redirect_response=False)
        self.assertTrue(bus.is_active)
        event = AuditEvent.objects.get()
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.action, AuditEvent.Action.CREATE)
        self.assertEqual((event.entity_type, event.entity_id), ("operations.Bus", str(bus.pk)))
        self.assertEqual(event.before, {})
        self.assertEqual(event.after, {"code": "NUEVO", "display_name": "Nuevo colectivo", "license_plate": "AB123CD", "is_active": True})

    def test_bus_edit_records_before_and_after(self):
        response = self.client.post(self.url("bus_edit", pk=self.bus.pk), self.bus_data(is_active=False), secure=True)
        self.assertEqual(response.status_code, 302)
        self.bus.refresh_from_db()
        self.assertEqual(self.bus.display_name, "Nuevo colectivo")
        self.assertTrue(self.bus.is_active)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditEvent.Action.UPDATE)
        self.assertEqual(event.before["code"], "TEST-1")
        self.assertEqual(event.after["code"], "NUEVO")
        self.assertEqual(event.actor, self.admin)

    def test_superuser_can_modify_bus_and_seat(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.post(self.url("bus_create"), self.bus_data(), secure=True).status_code, 302)
        self.assertEqual(self.client.post(self.url("seat_create", bus_pk=self.bus.pk), self.seat_data(), secure=True).status_code, 302)
        self.assertEqual(set(AuditEvent.objects.values_list("actor_id", flat=True)), {self.superuser.pk})

    def test_duplicate_bus_code_is_controlled(self):
        before = self.state()
        response = self.client.post(self.url("bus_create"), self.bus_data(code=self.bus.code), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("code", response.context["form"].errors)
        self.assertEqual(self.state(), before)

    def test_duplicate_plate_is_controlled(self):
        self.bus.license_plate = "AB123CD"
        self.bus.save()
        before = self.state()
        response = self.client.post(self.url("bus_create"), self.bus_data(), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La patente ya está asignada")
        self.assertEqual(self.state(), before)

    def test_invalid_bus_code_has_field_errors_and_accessible_summary(self):
        response = self.client.post(self.url("bus_create"), self.bus_data(code="inválido"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="#id_code"')
        self.assertContains(response, 'for="id_code"')
        self.assertContains(response, 'id="id_code_error"')
        self.assertFalse(AuditEvent.objects.exists())

    def test_bus_state_is_post_only_and_idempotent(self):
        for name, active, action in (("bus_deactivate", False, AuditEvent.Action.DEACTIVATE),
                                     ("bus_activate", True, AuditEvent.Action.ACTIVATE)):
            url = self.url(name, pk=self.bus.pk)
            before = self.state()
            self.assertEqual(self.client.get(url, secure=True).status_code, 405)
            self.assertEqual(self.state(), before)
            self.assertEqual(self.client.post(url, secure=True).status_code, 302)
            self.bus.refresh_from_db()
            self.assertEqual(self.bus.is_active, active)
            event = AuditEvent.objects.first()
            self.assertEqual(event.action, action)
            self.assertEqual(event.before["is_active"], not active)
            self.assertEqual(event.after["is_active"], active)
            before = self.state()
            self.client.post(url, secure=True)
            self.assertEqual(self.state(), before)

    def test_seat_creation_uses_url_bus_and_ignores_state(self):
        data = self.seat_data(bus=self.other_bus.pk, bus_id=self.other_bus.pk, is_active=False)
        response = self.client.post(self.url("seat_create", bus_pk=self.bus.pk), data, secure=True)
        self.assertEqual(response.status_code, 302)
        seat = Seat.objects.get(bus=self.bus, number=8)
        self.assertTrue(seat.is_active)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditEvent.Action.CREATE)
        self.assertEqual(event.entity_type, "operations.Seat")
        self.assertEqual(event.after["bus_id"], self.bus.pk)
        self.assertEqual(event.actor, self.admin)

    def test_seat_edit_cannot_move_bus_and_records_snapshot(self):
        response = self.client.post(self.url("seat_edit", bus_pk=self.bus.pk, pk=self.seat.pk),
                                    self.seat_data(bus=self.other_bus.pk, is_active=False), secure=True)
        self.assertEqual(response.status_code, 302)
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.bus, self.bus)
        self.assertEqual(self.seat.number, 8)
        self.assertEqual(self.seat.deck, Seat.Deck.LOWER)
        self.assertTrue(self.seat.is_active)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditEvent.Action.UPDATE)
        self.assertEqual(event.before["number"], 1)
        self.assertEqual(event.after["number"], 8)

    def test_wrong_parent_seat_requests_are_404_without_mutation(self):
        before = self.state()
        for name in ("seat_edit", "seat_activate", "seat_deactivate"):
            url = self.url(name, bus_pk=self.other_bus.pk, pk=self.seat.pk)
            self.assertEqual(self.client.post(url, self.seat_data(), secure=True).status_code, 404)
        self.assertEqual(self.state(), before)

    def test_duplicate_seat_number_and_position_are_controlled(self):
        for data, message in ((self.seat_data(number=1), "El número de butaca ya existe"),
                              (self.seat_data(deck=Seat.Deck.UPPER, position_x=2, position_y=3), "La posición ya está ocupada")):
            before = self.state()
            response = self.client.post(self.url("seat_create", bus_pk=self.bus.pk), data, secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, message)
            self.assertEqual(self.state(), before)

    def test_invalid_seat_coordinates_are_controlled(self):
        before = self.state()
        response = self.client.post(self.url("seat_create", bus_pk=self.bus.pk), self.seat_data(position_x=-1), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("position_x", response.context["form"].errors)
        self.assertEqual(self.state(), before)

    def test_seat_state_is_post_only_and_idempotent(self):
        for name, active, action in (("seat_deactivate", False, AuditEvent.Action.DEACTIVATE),
                                     ("seat_activate", True, AuditEvent.Action.ACTIVATE)):
            url = self.url(name, bus_pk=self.bus.pk, pk=self.seat.pk)
            before = self.state()
            self.assertEqual(self.client.get(url, secure=True).status_code, 405)
            self.assertEqual(self.state(), before)
            self.client.post(url, secure=True)
            self.seat.refresh_from_db()
            self.assertEqual(self.seat.is_active, active)
            event = AuditEvent.objects.first()
            self.assertEqual(event.action, action)
            self.assertEqual(event.before["is_active"], not active)
            self.assertEqual(event.after["is_active"], active)
            before = self.state()
            self.client.post(url, secure=True)
            self.assertEqual(self.state(), before)

    def test_seat_map_uses_stored_positions_and_separates_decks(self):
        lower = Seat.objects.create(bus=self.bus, **self.seat_data(), is_active=False)
        response = self.client.get(self.url("bus_detail", pk=self.bus.pk), secure=True)
        upper_deck, lower_deck = response.context["decks"]
        self.assertEqual(upper_deck["value"], Seat.Deck.UPPER)
        self.assertEqual(lower_deck["value"], Seat.Deck.LOWER)
        self.assertEqual([item["seat"].pk for item in upper_deck["items"]], [self.seat.pk])
        self.assertEqual([item["seat"].pk for item in lower_deck["items"]], [lower.pk])
        self.assertContains(response, 'grid-column: 3; grid-row: 4;')
        self.assertContains(response, 'grid-column: 1; grid-row: 1;')
        for label in ("Planta alta", "Planta baja", "Cama", "Semicama", "Inactiva"):
            self.assertContains(response, label)

    def test_deck_empty_state(self):
        response = self.client.get(self.url("bus_detail", pk=self.other_bus.pk), secure=True)
        self.assertContains(response, "Todavía no hay butacas en esta planta.", count=2)

    def test_forms_have_only_explicit_editable_fields(self):
        self.assertEqual(list(BusForm().fields), ["code", "display_name", "license_plate"])
        self.assertEqual(list(SeatForm(bus=self.bus).fields), ["number", "deck", "category", "position_x", "position_y"])

    def test_csrf_is_required_for_every_mutation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        before = self.state()
        for url, data in self.mutations():
            self.assertEqual(client.post(url, data, secure=True).status_code, 403)
        self.assertEqual(self.state(), before)
        client.get(self.url("bus_detail", pk=self.bus.pk), secure=True)
        token = client.cookies["csrftoken"].value
        response = client.post(self.url("bus_deactivate", pk=self.bus.pk),
                               {"csrfmiddlewaretoken": token}, secure=True, HTTP_REFERER="https://testserver/")
        self.assertEqual(response.status_code, 302)
        self.bus.refresh_from_db()
        self.assertFalse(self.bus.is_active)

    def test_no_physical_delete_or_audit_write_endpoint(self):
        before = self.state()
        for url in (f"/panel/operaciones/colectivos/{self.bus.pk}/eliminar/",
                    f"/panel/operaciones/colectivos/{self.bus.pk}/butacas/{self.seat.pk}/eliminar/",
                    "/panel/auditoria/1/editar/", "/panel/auditoria/1/eliminar/"):
            self.assertEqual(self.client.post(url, secure=True).status_code, 404)
        self.assertEqual(self.client.delete(self.url("bus_detail", pk=self.bus.pk), secure=True).status_code, 405)
        self.assertEqual(self.state(), before)

    def test_uniqueness_races_return_form_errors_without_audit(self):
        for model, url, data in ((Bus, self.url("bus_create"), self.bus_data()),
                                 (Seat, self.url("seat_create", bus_pk=self.bus.pk), self.seat_data())):
            before = self.state()
            with patch.object(model, "save", side_effect=IntegrityError("Colisión simulada")):
                response = self.client.post(url, data, secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].non_field_errors())
            self.assertEqual(self.state(), before)

    def test_audit_failure_rolls_back_creation_edit_and_state_changes(self):
        for url, data in self.mutations():
            # Forzar la transición opuesta antes de cada acción de estado.
            if url.endswith(("/activar/", "/desactivar/")):
                model, pk = (Seat, self.seat.pk) if "/butacas/" in url else (Bus, self.bus.pk)
                model.objects.filter(pk=pk).update(is_active=url.endswith("/desactivar/"))
            before = self.state()

            def fail_audit(**event):
                model = Bus if event["entity_type"] == "operations.Bus" else Seat
                saved = model.objects.get(pk=event["entity_id"])
                for field, value in event["after"].items():
                    self.assertEqual(getattr(saved, field), value)
                raise IntegrityError("Auditoría no disponible")

            with patch("panel.services.AuditEvent.objects.create", side_effect=fail_audit) as audit:
                response = self.client.post(url, data, secure=True)
            audit.assert_called_once()
            self.assertIn(response.status_code, (200, 302))
            self.assertEqual(self.state(), before)

    def test_conflicting_edits_do_not_change_existing_bus_or_seat(self):
        other_seat = Seat.objects.create(bus=self.bus, **self.seat_data())
        for url, data in (
            (self.url("bus_edit", pk=self.bus.pk), self.bus_data(code=self.other_bus.code)),
            (self.url("seat_edit", bus_pk=self.bus.pk, pk=self.seat.pk), self.seat_data(number=other_seat.number)),
        ):
            before = self.state()
            response = self.client.post(url, data, secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertEqual(self.state(), before)

    def test_audit_actor_is_set_null_after_user_deletion(self):
        self.client.post(self.url("bus_create"), self.bus_data(), secure=True)
        event = AuditEvent.objects.get()
        self.admin.delete()
        event.refresh_from_db()
        self.assertIsNone(event.actor)
        self.assertEqual(event.after["code"], "NUEVO")

    def test_success_and_error_messages_have_distinct_styles(self):
        response = self.client.post(self.url("bus_deactivate", pk=self.bus.pk), secure=True, follow=True)
        self.assertContains(response, 'class="alert-success"')
        with patch("panel.services.AuditEvent.objects.create", side_effect=IntegrityError("Fallo")):
            response = self.client.post(self.url("bus_activate", pk=self.bus.pk), secure=True, follow=True)
        self.assertContains(response, 'class="alert-error"')

    def test_noop_edit_does_not_create_audit(self):
        self.client.post(self.url("bus_edit", pk=self.bus.pk),
                         self.bus_data(code=self.bus.code, display_name=self.bus.display_name, license_plate=""), secure=True)
        self.assertFalse(AuditEvent.objects.exists())

    def test_admin_stays_disabled_and_pages_use_no_remote_frontend(self):
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)
        self.assertEqual(self.client.get("/admin/", secure=True).status_code, 404)
        for url in self.read_urls() + [self.url("bus_create")]:
            response = self.client.get(url, secure=True)
            self.assertContains(response, "panel/css/operations")
            for remote in ('src="http', 'href="https://cdn', 'cdn.tailwindcss', 'cdn.jsdelivr', 'unpkg.com'):
                self.assertNotContains(response, remote)
