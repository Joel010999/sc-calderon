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

        cls.common = User.objects.create_user(username="common_view", password="password123")

        cls.stop_cba = Stop.objects.create(code="CBA", name="Córdoba", city="Córdoba", province="Córdoba")
        cls.stop_ssj = Stop.objects.create(code="SSJ", name="Jujuy", city="Jujuy", province="Jujuy")

    def setUp(self):
        self.rf = RequestFactory()

    def test_seller_cannot_access_write_views(self):
        for view_fn, args in [
            (route_create, []),
            (stop_create, []),
        ]:
            request = self.rf.get("/dummy/")
            request.user = self.seller
            with self.assertRaises(PermissionDenied):
                view_fn(request, *args)

            request = self.rf.post("/dummy/", {})
            request.user = self.seller
            with self.assertRaises(PermissionDenied):
                view_fn(request, *args)

    def test_admin_can_create_stop_via_view(self):
        request = self.rf.post(
            "/operaciones/paradas/nueva/",
            {"code": "SLT", "name": "Salta Capital", "city": "Salta", "province": "Salta"},
        )
        request.user = self.admin
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request, "session", {})
        setattr(request, "_messages", FallbackStorage(request))

        response = stop_create(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Stop.objects.filter(code="SLT").exists())

    def test_admin_can_create_route_via_view(self):
        request = self.rf.post(
            "/operaciones/recorridos/nuevo/",
            {
                "code": "CBA-SSJ-V1",
                "name": "Córdoba a Jujuy Directo",
                "is_active": True,
                "sequence_1": 1, "stop_id_1": self.stop_cba.pk, "allows_boarding_1": True,
                "sequence_2": 2, "stop_id_2": self.stop_ssj.pk, "allows_alighting_2": True,
            },
        )
        request.user = self.admin
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request, "session", {})
        setattr(request, "_messages", FallbackStorage(request))

        response = route_create(request)
        self.assertEqual(response.status_code, 302)
        route = Route.objects.get(code="CBA-SSJ-V1")
        self.assertEqual(route.version, 1)
        self.assertEqual(route.route_stops.count(), 2)
