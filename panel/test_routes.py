"""Pruebas completas de gestión de recorridos, versionado, protección de paradas y permisos."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory, TestCase
from panel.route_views import route_create, route_edit, route_list, route_state, stop_create, stop_edit
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare
from operations.services import (
    save_route_configuration,
    schedule_trip,
    validate_route_stops,
)
from panel.models import AuditEvent
from panel.route_services import save_panel_route, save_panel_stop, set_route_active
from panel.trip_services import complete_trip, create_panel_trip, start_trip

User = get_user_model()


class RouteManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Grupos y roles
        cls.group_admin = Group.objects.create(name="Administrador")
        cls.group_seller = Group.objects.create(name="Vendedor")

        cls.admin = User.objects.create_user(username="admin_rt", password="password123")
        cls.admin.groups.add(cls.group_admin)

        cls.seller = User.objects.create_user(username="seller_rt", password="password123")
        cls.seller.groups.add(cls.group_seller)

        cls.superuser = User.objects.create_superuser(username="super_rt", password="password123", email="super@test.com")
        cls.common = User.objects.create_user(username="common_rt", password="password123")

        # Paradas base
        cls.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        cls.stop_jma = Stop.objects.create(code="JMA", name="Jesús María", city="Jesús María", province="Córdoba")
        cls.stop_gue = Stop.objects.create(code="GUE", name="Güemes", city="Güemes", province="Salta")
        cls.stop_ssj = Stop.objects.create(code="SSJ", name="San Salvador de Jujuy", city="San Salvador de Jujuy", province="Jujuy")

        # Colectivo base
        cls.bus = Bus.objects.create(code="BUS-01", display_name="Colectivo 01", license_plate="AA100BB")
        cls.seat_cama = Seat.objects.create(
            bus=cls.bus, number=1, deck=Seat.Deck.LOWER,
            category=SeatCategory.CAMA, position_x=0, position_y=0,
        )
        cls.seat_semi = Seat.objects.create(
            bus=cls.bus, number=2, deck=Seat.Deck.UPPER,
            category=SeatCategory.SEMI_CAMA, position_x=0, position_y=1,
        )

    def setUp(self):
        self.client = Client()

    def _valid_stops_data(self):
        return [
            {"stop": self.stop_cba, "sequence": 1, "allows_boarding": True, "allows_alighting": False},
            {"stop": self.stop_jma, "sequence": 2, "allows_boarding": True, "allows_alighting": False},
            {"stop": self.stop_gue, "sequence": 3, "allows_boarding": False, "allows_alighting": True},
            {"stop": self.stop_ssj, "sequence": 4, "allows_boarding": False, "allows_alighting": True},
        ]

    # 1. Creación y validación de recorridos
    def test_create_route_with_valid_stops_and_interprovincial_journey(self):
        route, created_version = save_route_configuration(
            code="CBA-SSJ-TEST",
            name="Córdoba a Jujuy Test",
            stops_data=self._valid_stops_data(),
        )
        self.assertFalse(created_version)
        self.assertEqual(route.version, 1)
        self.assertEqual(route.route_stops.count(), 4)
        self.assertTrue(route.allows_journey(self.stop_cba, self.stop_ssj))
        self.assertFalse(route.allows_journey(self.stop_cba, self.stop_jma))  # Misma provincia rechazada

    def test_route_validation_requires_at_least_two_stops(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_route_stops([
                {"stop": self.stop_cba, "sequence": 1, "allows_boarding": True, "allows_alighting": False},
            ])
        self.assertIn("al menos dos paradas", str(ctx.exception))

    def test_route_validation_first_must_board_last_must_alight(self):
        # Primera parada no permite subir
        stops = self._valid_stops_data()
        stops[0]["allows_boarding"] = False
        stops[0]["allows_alighting"] = True
        with self.assertRaises(ValidationError) as ctx:
            validate_route_stops(stops)
        self.assertIn("primera parada", str(ctx.exception).lower())

        # Última parada no permite bajar
        stops = self._valid_stops_data()
        stops[-1]["allows_boarding"] = True
        stops[-1]["allows_alighting"] = False
        with self.assertRaises(ValidationError) as ctx:
            validate_route_stops(stops)
        self.assertIn("última parada", str(ctx.exception).lower())

    def test_route_validation_rejects_purely_intraprovincial_stops(self):
        # Recorrido donde todas las paradas son de Córdoba
        intra_stops = [
            {"stop": self.stop_cba, "sequence": 1, "allows_boarding": True, "allows_alighting": False},
            {"stop": self.stop_jma, "sequence": 2, "allows_boarding": False, "allows_alighting": True},
        ]
        with self.assertRaises(ValidationError) as ctx:
            validate_route_stops(intra_stops)
        self.assertIn("tramo interprovincial válido", str(ctx.exception))

    # 2. Edición de recorrido no utilizado
    def test_edit_unused_route_updates_in_place(self):
        route, _ = save_route_configuration(
            code="RT-UNUSED",
            name="Ruta Sin Usar",
            stops_data=self._valid_stops_data(),
        )
        self.assertFalse(route.has_trips)

        # Modificamos paradas y nombre directamente
        new_stops = [
            {"stop": self.stop_cba, "sequence": 1, "allows_boarding": True, "allows_alighting": False},
            {"stop": self.stop_gue, "sequence": 2, "allows_boarding": False, "allows_alighting": True},
            {"stop": self.stop_ssj, "sequence": 3, "allows_boarding": False, "allows_alighting": True},
        ]
        updated_route, created_version = save_route_configuration(
            code="RT-UNUSED",
            name="Ruta Sin Usar Modificada",
            stops_data=new_stops,
            route=route,
        )
        self.assertFalse(created_version)
        self.assertEqual(updated_route.pk, route.pk)
        self.assertEqual(updated_route.version, 1)
        self.assertEqual(updated_route.name, "Ruta Sin Usar Modificada")
        self.assertEqual(updated_route.route_stops.count(), 3)

    # 3. Versionado de recorrido utilizado
    def test_used_route_creates_new_version_preserving_history(self):
        route, _ = save_route_configuration(
            code="RT-USED",
            name="Ruta En Uso",
            stops_data=self._valid_stops_data(),
        )
        # Programar un viaje con este recorrido
        start = timezone.now() + timedelta(days=2)
        schedules = {
            self.stop_cba.pk: start,
            self.stop_jma.pk: start + timedelta(hours=1),
            self.stop_gue.pk: start + timedelta(hours=10),
            self.stop_ssj.pk: start + timedelta(hours=12),
        }
        trip = schedule_trip(route=route, bus=self.bus, schedules=schedules)
        self.assertTrue(route.has_trips)

        # Intento de modificación directa en el modelo falla
        route.name = "Cambio Directo Prohibido"
        with self.assertRaises(ValidationError) as ctx:
            route.clean()
        self.assertIn("no se puede modificar directamente", str(ctx.exception))

        # Modificación de paradas directas en el modelo falla
        outside_stop = Stop.objects.create(code="TUC", name="Tucumán", city="Tucumán", province="Tucumán")
        rs_invalid = RouteStop(route=route, stop=outside_stop, sequence=5, allows_boarding=True, allows_alighting=False)
        with self.assertRaises(ValidationError) as ctx:
            rs_invalid.clean()
        self.assertIn("no se pueden modificar", str(ctx.exception).lower())

        # Al usar el servicio de guardado, crea automáticamente una NUEVA VERSIÓN
        new_stops = [
            {"stop": self.stop_cba, "sequence": 1, "allows_boarding": True, "allows_alighting": False},
            {"stop": self.stop_gue, "sequence": 2, "allows_boarding": False, "allows_alighting": True},
            {"stop": self.stop_ssj, "sequence": 3, "allows_boarding": False, "allows_alighting": True},
        ]
        new_route, created_version = save_route_configuration(
            code="RT-USED",
            name="Ruta En Uso Versión 2",
            stops_data=new_stops,
            route=route,
        )
        self.assertTrue(created_version)
        self.assertEqual(new_route.version, 2)
        self.assertEqual(new_route.parent_route_id, route.pk)
        self.assertTrue(new_route.is_active)

        # El recorrido original fue desactivado pero preservado intacto
        route.refresh_from_db()
        self.assertFalse(route.is_active)
        self.assertEqual(route.version, 1)
        self.assertEqual(route.route_stops.count(), 4)

        # El viaje histórico sigue apuntando a la versión 1 y conserva sus paradas
        trip.refresh_from_db()
        self.assertEqual(trip.route_id, route.pk)
        self.assertEqual(trip.trip_stops.count(), 4)

    # 4. Protección frente a cambios en paradas compartidas
    def test_shared_stop_used_in_trips_blocks_in_place_modifications(self):
        route, _ = save_route_configuration(
            code="RT-SHARED",
            name="Ruta Compartida",
            stops_data=self._valid_stops_data(),
        )
        start = timezone.now() + timedelta(days=3)
        schedules = {
            self.stop_cba.pk: start,
            self.stop_jma.pk: start + timedelta(hours=1),
            self.stop_gue.pk: start + timedelta(hours=10),
            self.stop_ssj.pk: start + timedelta(hours=12),
        }
        schedule_trip(route=route, bus=self.bus, schedules=schedules)

        # stop_cba está en el viaje histórico
        self.assertTrue(self.stop_cba.has_historical_trips)

        # Intentar modificar provincia, nombre o ciudad de la parada compartida es rechazado
        self.stop_cba.province = "Santa Fe"
        with self.assertRaises(ValidationError) as ctx:
            self.stop_cba.clean()
        self.assertIn("province", ctx.exception.message_dict)

        self.stop_cba.refresh_from_db()
        self.stop_cba.name = "Córdoba Nueva"
        with self.assertRaises(ValidationError) as ctx:
            self.stop_cba.clean()
        self.assertIn("name", ctx.exception.message_dict)

        # Una parada sin viajes históricos sí puede editarse
        stop_libre = Stop.objects.create(code="LIBRE", name="Libre", city="Libre", province="Catamarca")
        stop_libre.province = "La Rioja"
        stop_libre.name = "Libre Modificada"
        stop_libre.clean()
        stop_libre.save()
        self.assertEqual(stop_libre.province, "La Rioja")

    # 5. Creación de viajes desde la nueva versión del recorrido
    def test_create_trip_from_new_route_version_with_bus_overlap_prevention(self):
        route, _ = save_route_configuration(
            code="RT-V1",
            name="Ruta V1",
            stops_data=self._valid_stops_data(),
        )
        # Salida 1 con colectivo
        start1 = timezone.now() + timedelta(days=5)
        trip1 = create_panel_trip(
            actor=self.admin,
            route=route,
            bus=self.bus,
            schedules={
                self.stop_cba.pk: start1,
                self.stop_jma.pk: start1 + timedelta(hours=1),
                self.stop_gue.pk: start1 + timedelta(hours=10),
                self.stop_ssj.pk: start1 + timedelta(hours=12),
            },
        )
        self.assertEqual(trip1.route.version, 1)

        # Crear versión 2
        new_stops = [
            {"stop": self.stop_cba, "sequence": 1, "allows_boarding": True, "allows_alighting": False},
            {"stop": self.stop_gue, "sequence": 2, "allows_boarding": False, "allows_alighting": True},
            {"stop": self.stop_ssj, "sequence": 3, "allows_boarding": False, "allows_alighting": True},
        ]
        route_v2, _ = save_route_configuration(
            code="RT-V1",
            name="Ruta V2",
            stops_data=new_stops,
            route=route,
        )
        self.assertEqual(route_v2.version, 2)

        # Superposición de colectivo debe ser rechazada para la nueva versión
        overlap_start = start1 + timedelta(hours=2)
        with self.assertRaises(ValidationError) as ctx:
            create_panel_trip(
                actor=self.admin,
                route=route_v2,
                bus=self.bus,
                schedules={
                    self.stop_cba.pk: overlap_start,
                    self.stop_gue.pk: overlap_start + timedelta(hours=9),
                    self.stop_ssj.pk: overlap_start + timedelta(hours=11),
                },
            )
        self.assertIn("superpuestos", str(ctx.exception))

        # Salida posterior válida sin superposición
        start2 = start1 + timedelta(days=2)
        trip2 = create_panel_trip(
            actor=self.admin,
            route=route_v2,
            bus=self.bus,
            schedules={
                self.stop_cba.pk: start2,
                self.stop_gue.pk: start2 + timedelta(hours=9),
                self.stop_ssj.pk: start2 + timedelta(hours=11),
            },
        )
        self.assertEqual(trip2.route_id, route_v2.pk)
        self.assertEqual(trip2.trip_stops.count(), 3)

    # 6. Bloqueo de cambio de colectivo en viaje ya creado
    def test_trip_bus_cannot_be_changed_after_creation(self):
        route, _ = save_route_configuration(code="RT-BUS", name="Ruta Bus", stops_data=self._valid_stops_data())
        start = timezone.now() + timedelta(days=10)
        trip = schedule_trip(
            route=route, bus=self.bus,
            schedules={
                self.stop_cba.pk: start,
                self.stop_jma.pk: start + timedelta(hours=1),
                self.stop_gue.pk: start + timedelta(hours=10),
                self.stop_ssj.pk: start + timedelta(hours=12),
            },
        )
        bus2 = Bus.objects.create(code="BUS-02", display_name="Colectivo 02", license_plate="AA200BB")
        trip.bus = bus2
        with self.assertRaises(ValidationError) as ctx:
            trip.clean()
        self.assertIn("bus", ctx.exception.message_dict)

    # 7. Permisos: Vendedor vs Administrador vs Superusuario
    def test_seller_cannot_manage_routes_or_stops(self):
        # Servicio directo
        with self.assertRaises(PermissionDenied):
            save_panel_route(actor=self.seller, data={"code": "FAIL", "name": "Fail"})
        with self.assertRaises(PermissionDenied):
            save_panel_stop(actor=self.seller, data={"code": "FAIL", "name": "Fail", "city": "X", "province": "Y"})
        with self.assertRaises(PermissionDenied):
            set_route_active(actor=self.seller, pk=1, active=False)

    def test_seller_read_routes_via_panel_routes(self):
        self.client.force_login(self.seller)
        # Acceso de lectura a recorridos permitido
        response = self.client.get(reverse("panel:routes"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_manage"])

    def test_admin_and_superuser_can_manage_routes_and_stops(self):
        for user in (self.admin, self.superuser):
            form, route, created_version = save_panel_route(
                actor=user,
                data={
                    "code": f"ADMIN-{user.username[:4].upper()}",
                    "name": f"Ruta por {user.username}",
                    "is_active": True,
                    "sequence_1": 1, "stop_id_1": self.stop_cba.pk, "allows_boarding_1": True,
                    "sequence_2": 2, "stop_id_2": self.stop_ssj.pk, "allows_alighting_2": True,
                },
            )
            self.assertIsNotNone(route)
            self.assertTrue(AuditEvent.objects.filter(actor=user, entity_type__iexact="operations.route").exists())



class RouteViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_admin = Group.objects.create(name="Administrador")
        cls.group_seller = Group.objects.create(name="Vendedor")

        cls.admin = User.objects.create_user(username="admin_view", password="password123")
        cls.admin.groups.add(cls.group_admin)

        cls.seller = User.objects.create_user(username="seller_view", password="password123")
        cls.seller.groups.add(cls.group_seller)

        cls.superuser = User.objects.create_superuser(username="super_view", password="password123", email="super_view@test.com")
        cls.common = User.objects.create_user(username="common_view", password="password123")

        cls.stop_cba = Stop.objects.create(code="CBA", name="Córdoba", city="Córdoba", province="Córdoba")
        cls.stop_jma = Stop.objects.create(code="JMA", name="Jesús María", city="Jesús María", province="Córdoba")
        cls.stop_gue = Stop.objects.create(code="GUE", name="Güemes", city="Güemes", province="Salta")
        cls.stop_ssj = Stop.objects.create(code="SSJ", name="Jujuy", city="Jujuy", province="Jujuy")

        cls.bus = Bus.objects.create(code="BUS-VW", display_name="Colectivo View", license_plate="VW123AA")
        Seat.objects.create(bus=cls.bus, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)

    def setUp(self):
        self.client = Client()

    def test_anonymous_user_redirected_to_login(self):
        protected_urls = [
            reverse("panel:trips"),
            reverse("panel:routes"),
            reverse("panel:route_create"),
            reverse("panel:stop_create"),
        ]
        for url in protected_urls:
            response = self.client.get(url, secure=True)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/panel/login/", response.headers["Location"])

    def test_seller_role_access_visibility_and_restrictions(self):
        self.client.force_login(self.seller)

        # 1. Acceso de consulta a Viajes
        response = self.client.get(reverse("panel:trips"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context.get("can_manage", True))
        self.assertNotContains(response, "Gestionar recorridos")
        self.assertNotContains(response, "Nuevo viaje")

        # 2. Acceso de consulta a Recorridos
        route = Route.objects.create(code="VIEW-TEST", name="Ruta Consulta", is_active=True)
        RouteStop.objects.create(route=route, stop=self.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=route, stop=self.stop_ssj, sequence=2, allows_boarding=False, allows_alighting=True)

        response = self.client.get(reverse("panel:routes"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context.get("can_manage", True))
        self.assertNotContains(response, "Nuevo recorrido")
        self.assertNotContains(response, "Nueva parada")
        self.assertNotContains(response, reverse("panel:route_create"))
        self.assertNotContains(response, reverse("panel:stop_create"))
        self.assertNotContains(response, reverse("panel:route_edit", args=[route.pk]))
        self.assertNotContains(response, reverse("panel:stop_edit", args=[self.stop_cba.pk]))

        # 3. Bloqueo 403 Forbidden a vistas de escritura de recorridos y paradas
        forbidden_calls = [
            ("get", reverse("panel:route_create"), {}),
            ("post", reverse("panel:route_create"), {"code": "FAIL"}),
            ("get", reverse("panel:route_edit", args=[route.pk]), {}),
            ("post", reverse("panel:route_edit", args=[route.pk]), {"code": "FAIL"}),
            ("get", reverse("panel:route_new_version", args=[route.pk]), {}),
            ("post", reverse("panel:route_new_version", args=[route.pk]), {"code": "FAIL"}),
            ("post", reverse("panel:route_activate", args=[route.pk]), {}),
            ("post", reverse("panel:route_deactivate", args=[route.pk]), {}),
            ("get", reverse("panel:stop_create"), {}),
            ("post", reverse("panel:stop_create"), {"code": "FAIL"}),
            ("get", reverse("panel:stop_edit", args=[self.stop_cba.pk]), {}),
            ("post", reverse("panel:stop_edit", args=[self.stop_cba.pk]), {"code": "FAIL"}),
        ]
        for method, url, data in forbidden_calls:
            with self.subTest(method=method, url=url):
                res = getattr(self.client, method)(url, data, secure=True)
                self.assertEqual(res.status_code, 403)

    def test_admin_role_access_visibility_and_crud_navigation(self):
        self.client.force_login(self.admin)

        # 1. Acceso a Viajes y visibilidad de 'Gestionar recorridos'
        response = self.client.get(reverse("panel:trips"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("can_manage", False))
        self.assertContains(response, "Gestionar recorridos")
        self.assertContains(response, reverse("panel:routes"))

        # 2. Acceso a Recorridos y enlaces de gestión
        response = self.client.get(reverse("panel:routes"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get("can_manage", False))
        self.assertContains(response, "Nuevo recorrido")
        self.assertContains(response, reverse("panel:route_create"))
        self.assertContains(response, "Nueva parada")
        self.assertContains(response, reverse("panel:stop_create"))

        # 3. Navegación y creación de parada vía HTTP
        res_get_stop = self.client.get(reverse("panel:stop_create"), secure=True)
        self.assertEqual(res_get_stop.status_code, 200)
        self.assertContains(res_get_stop, "Nueva parada")

        res_post_stop = self.client.post(
            reverse("panel:stop_create"),
            {"code": "SLT", "name": "Salta Capital", "city": "Salta", "province": "Salta"},
            secure=True,
        )
        self.assertEqual(res_post_stop.status_code, 302)
        self.assertRedirects(res_post_stop, reverse("panel:routes"))
        stop_slt = Stop.objects.get(code="SLT")
        self.assertEqual(stop_slt.province, "Salta")

        # 4. Navegación y edición de parada vía HTTP
        res_get_stop_edit = self.client.get(reverse("panel:stop_edit", args=[stop_slt.pk]), secure=True)
        self.assertEqual(res_get_stop_edit.status_code, 200)
        self.assertContains(res_get_stop_edit, "Editar parada SLT")

        res_post_stop_edit = self.client.post(
            reverse("panel:stop_edit", args=[stop_slt.pk]),
            {"code": "SLT", "name": "Terminal de Salta", "city": "Salta", "province": "Salta"},
            secure=True,
        )
        self.assertEqual(res_post_stop_edit.status_code, 302)
        self.assertRedirects(res_post_stop_edit, reverse("panel:routes"))
        stop_slt.refresh_from_db()
        self.assertEqual(stop_slt.name, "Terminal de Salta")

        # 5. Navegación y creación de recorrido vía HTTP
        res_get_route = self.client.get(reverse("panel:route_create"), secure=True)
        self.assertEqual(res_get_route.status_code, 200)
        self.assertContains(res_get_route, "Nuevo recorrido")

        res_post_route = self.client.post(
            reverse("panel:route_create"),
            {
                "code": "CBA-SLT-V1",
                "name": "Córdoba a Salta",
                "is_active": True,
                "sequence_1": 1, "stop_id_1": self.stop_cba.pk, "allows_boarding_1": True,
                "sequence_2": 2, "stop_id_2": stop_slt.pk, "allows_alighting_2": True,
            },
            secure=True,
        )
        self.assertEqual(res_post_route.status_code, 302)
        self.assertRedirects(res_post_route, reverse("panel:routes"))
        route = Route.objects.get(code="CBA-SLT-V1")
        self.assertEqual(route.version, 1)
        self.assertEqual(route.route_stops.count(), 2)

        # 6. Edición in-place de recorrido sin salidas asociadas
        res_get_route_edit = self.client.get(reverse("panel:route_edit", args=[route.pk]), secure=True)
        self.assertEqual(res_get_route_edit.status_code, 200)
        self.assertFalse(res_get_route_edit.context.get("is_versioning", True))
        self.assertContains(res_get_route_edit, f"Editar recorrido {route.code}")

        res_post_route_edit = self.client.post(
            reverse("panel:route_edit", args=[route.pk]),
            {
                "code": "CBA-SLT-V1",
                "name": "Córdoba a Salta con parada intermedia",
                "is_active": True,
                "sequence_1": 1, "stop_id_1": self.stop_cba.pk, "allows_boarding_1": True,
                "sequence_2": 2, "stop_id_2": self.stop_jma.pk, "allows_boarding_2": True,
                "sequence_3": 3, "stop_id_3": stop_slt.pk, "allows_alighting_3": True,
            },
            secure=True,
        )
        self.assertEqual(res_post_route_edit.status_code, 302)
        self.assertRedirects(res_post_route_edit, reverse("panel:routes"))
        route.refresh_from_db()
        self.assertEqual(route.version, 1)
        self.assertEqual(route.route_stops.count(), 3)
        self.assertEqual(route.name, "Córdoba a Salta con parada intermedia")

        # 7. Activar y desactivar recorrido vía HTTP
        res_deact = self.client.post(reverse("panel:route_deactivate", args=[route.pk]), secure=True)
        self.assertEqual(res_deact.status_code, 302)
        route.refresh_from_db()
        self.assertFalse(route.is_active)

        res_act = self.client.post(reverse("panel:route_activate", args=[route.pk]), secure=True)
        self.assertEqual(res_act.status_code, 302)
        route.refresh_from_db()
        self.assertTrue(route.is_active)

        # 8. Recorrido con viajes asociados genera automáticamente NUEVA VERSIÓN
        start = timezone.now() + timedelta(days=4)
        trip = schedule_trip(
            route=route,
            bus=self.bus,
            schedules={
                self.stop_cba.pk: start,
                self.stop_jma.pk: start + timedelta(hours=1),
                stop_slt.pk: start + timedelta(hours=10),
            },
        )
        self.assertEqual(trip.route_id, route.pk)

        # En el listado debe ofrecer 'Nueva versión'
        res_list = self.client.get(reverse("panel:routes"), secure=True)
        self.assertContains(res_list, reverse("panel:route_new_version", args=[route.pk]))
        self.assertContains(res_list, "Nueva versión")

        # GET a nueva versión informa el versionado automático
        res_get_ver = self.client.get(reverse("panel:route_new_version", args=[route.pk]), secure=True)
        self.assertEqual(res_get_ver.status_code, 200)
        self.assertTrue(res_get_ver.context.get("is_versioning", False))
        self.assertContains(res_get_ver, "Versionado automático de recorrido en uso")

        # POST a nueva versión crea versión 2 y preserva histórico
        res_post_ver = self.client.post(
            reverse("panel:route_new_version", args=[route.pk]),
            {
                "code": "CBA-SLT-V1",
                "name": route.name,
                "is_active": True,
                "sequence_1": 1, "stop_id_1": self.stop_cba.pk, "allows_boarding_1": True,
                "sequence_2": 2, "stop_id_2": self.stop_ssj.pk, "allows_alighting_2": True,
            },
            secure=True,
        )
        self.assertEqual(res_post_ver.status_code, 302)
        self.assertRedirects(res_post_ver, reverse("panel:routes"))

        route_v2 = Route.objects.get(code="CBA-SLT-V1", version=2)
        self.assertEqual(route_v2.parent_route_id, route.pk)
        self.assertTrue(route_v2.is_active)
        self.assertEqual(route_v2.route_stops.count(), 2)

        # La versión original queda desactivada para nuevas salidas pero el viaje conserva versión 1
        route.refresh_from_db()
        self.assertFalse(route.is_active)
        self.assertEqual(route.version, 1)
        trip.refresh_from_db()
        self.assertEqual(trip.route_id, route.pk)

    def test_superuser_role_full_navigation_and_permissions(self):
        self.client.force_login(self.superuser)

        res_trips = self.client.get(reverse("panel:trips"), secure=True)
        self.assertEqual(res_trips.status_code, 200)
        self.assertTrue(res_trips.context.get("can_manage", False))
        self.assertContains(res_trips, "Gestionar recorridos")

        res_routes = self.client.get(reverse("panel:routes"), secure=True)
        self.assertEqual(res_routes.status_code, 200)
        self.assertTrue(res_routes.context.get("can_manage", False))
        self.assertContains(res_routes, "Nuevo recorrido")
        self.assertContains(res_routes, "Nueva parada")

        res_post_stop = self.client.post(
            reverse("panel:stop_create"),
            {"code": "SUP", "name": "Parada Super", "city": "Super", "province": "Salta"},
            secure=True,
        )
        self.assertEqual(res_post_stop.status_code, 302)
        self.assertTrue(Stop.objects.filter(code="SUP").exists())
