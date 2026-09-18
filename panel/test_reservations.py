from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from panel.models import AuditEvent
from panel.permissions import can_manage_operations, can_manage_reservations
from sales.conf import get_manual_hold_hours, get_max_passengers_per_booking
from sales.exceptions import InvalidBookingError, SeatUnavailableError
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
    normalize_document,
)
from sales.services import create_manual_booking, get_trip_availability

User = get_user_model()
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class ReservationPanelBaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_admin = Group.objects.create(name="Administrador")
        cls.group_seller = Group.objects.create(name="Vendedor")

        cls.user_admin = User.objects.create_user(username="admin_test", password="password123")
        cls.user_admin.groups.add(cls.group_admin)

        cls.user_seller = User.objects.create_user(username="seller_test", password="password123")
        cls.user_seller.groups.add(cls.group_seller)

        cls.user_super = User.objects.create_superuser(username="super_test", password="password123", email="super@test.com")
        cls.user_common = User.objects.create_user(username="common_test", password="password123")
        cls.user_staff = User.objects.create_user(username="staff_test", password="password123", is_staff=True)

        # Paradas
        cls.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        cls.stop_jma = Stop.objects.create(code="JMA", name="JesÃºs MarÃ­a", city="JesÃºs MarÃ­a", province="Córdoba")
        cls.stop_ssj = Stop.objects.create(code="SSJ", name="San Salvador de Jujuy", city="San Salvador de Jujuy", province="Jujuy")

        # Recorridos
        cls.route_cba_ssj = Route.objects.create(code="CBA-SSJ", name="Córdoba a Jujuy")
        cls.rs_out_1 = RouteStop.objects.create(route=cls.route_cba_ssj, stop=cls.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        cls.rs_out_2 = RouteStop.objects.create(route=cls.route_cba_ssj, stop=cls.stop_jma, sequence=2, allows_boarding=True, allows_alighting=False)
        cls.rs_out_3 = RouteStop.objects.create(route=cls.route_cba_ssj, stop=cls.stop_ssj, sequence=3, allows_boarding=False, allows_alighting=True)

        cls.route_ssj_cba = Route.objects.create(code="SSJ-CBA", name="Jujuy a Córdoba")
        cls.rs_ret_1 = RouteStop.objects.create(route=cls.route_ssj_cba, stop=cls.stop_ssj, sequence=1, allows_boarding=True, allows_alighting=False)
        cls.rs_ret_2 = RouteStop.objects.create(route=cls.route_ssj_cba, stop=cls.stop_jma, sequence=2, allows_boarding=False, allows_alighting=True)
        cls.rs_ret_3 = RouteStop.objects.create(route=cls.route_ssj_cba, stop=cls.stop_cba, sequence=3, allows_boarding=False, allows_alighting=True)

        # Colectivos y butacas
        cls.bus_1 = Bus.objects.create(code="BUS-101", display_name="Interno 101")
        cls.bus_2 = Bus.objects.create(code="BUS-102", display_name="Interno 102")

        cls.seat_cama_1 = Seat.objects.create(bus=cls.bus_1, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)
        cls.seat_cama_2 = Seat.objects.create(bus=cls.bus_1, number=2, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=1, position_y=0)
        cls.seat_semi_3 = Seat.objects.create(bus=cls.bus_1, number=3, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
        cls.seat_semi_4 = Seat.objects.create(bus=cls.bus_1, number=4, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0)
        cls.seat_inactive = Seat.objects.create(bus=cls.bus_1, number=5, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=2, position_y=0, is_active=False)

        # Butacas bus 2
        cls.seat_b2_cama_1 = Seat.objects.create(bus=cls.bus_2, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)
        cls.seat_b2_semi_2 = Seat.objects.create(bus=cls.bus_2, number=2, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0)

        # Viajes futuros
        now = timezone.now()
        cls.base_departure = now + timedelta(days=2)

        cls.trip_outbound = Trip.objects.create(
            route=cls.route_cba_ssj, bus=cls.bus_1,
            departure_at=cls.base_departure, status=Trip.Status.SCHEDULED,
        )
        cls.ts_out_cba = TripStop.objects.create(trip=cls.trip_outbound, stop=cls.stop_cba, sequence=1, scheduled_at=cls.base_departure, allows_boarding=True, allows_alighting=False)
        cls.ts_out_jma = TripStop.objects.create(trip=cls.trip_outbound, stop=cls.stop_jma, sequence=2, scheduled_at=cls.base_departure + timedelta(hours=1), allows_boarding=True, allows_alighting=False)
        cls.ts_out_ssj = TripStop.objects.create(trip=cls.trip_outbound, stop=cls.stop_ssj, sequence=3, scheduled_at=cls.base_departure + timedelta(hours=10), allows_boarding=False, allows_alighting=True)

        # Tarifas viaje de ida
        cls.fare_out_cama = TripFare.objects.create(
            trip=cls.trip_outbound, origin_stop=cls.ts_out_cba, destination_stop=cls.ts_out_ssj,
            seat_category=SeatCategory.CAMA, amount=Decimal("15000.00"), currency="ARS", is_active=True,
        )
        cls.fare_out_semi = TripFare.objects.create(
            trip=cls.trip_outbound, origin_stop=cls.ts_out_cba, destination_stop=cls.ts_out_ssj,
            seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("11000.00"), currency="ARS", is_active=True,
        )

        # Viaje de vuelta
        cls.ret_departure = cls.base_departure + timedelta(days=3)
        cls.trip_return = Trip.objects.create(
            route=cls.route_ssj_cba, bus=cls.bus_2,
            departure_at=cls.ret_departure, status=Trip.Status.SCHEDULED,
        )
        cls.ts_ret_ssj = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_ssj, sequence=1, scheduled_at=cls.ret_departure, allows_boarding=True, allows_alighting=False)
        cls.ts_ret_jma = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_jma, sequence=2, scheduled_at=cls.ret_departure + timedelta(hours=9), allows_boarding=False, allows_alighting=True)
        cls.ts_ret_cba = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_cba, sequence=3, scheduled_at=cls.ret_departure + timedelta(hours=10), allows_boarding=False, allows_alighting=True)

        # Tarifas viaje de vuelta
        cls.fare_ret_cama = TripFare.objects.create(
            trip=cls.trip_return, origin_stop=cls.ts_ret_ssj, destination_stop=cls.ts_ret_cba,
            seat_category=SeatCategory.CAMA, amount=Decimal("16000.00"), currency="ARS", is_active=True,
        )
        cls.fare_ret_semi = TripFare.objects.create(
            trip=cls.trip_return, origin_stop=cls.ts_ret_ssj, destination_stop=cls.ts_ret_cba,
            seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("12000.00"), currency="ARS", is_active=True,
        )


class ReservationPermissionsTests(ReservationPanelBaseTestCase):
    def setUp(self):
        self.client = Client()

    def test_anonymous_redirected_to_login(self):
        for url_name in ["booking_list", "booking_create"]:
            url = reverse(f"panel:{url_name}")
            response = self.client.get(url, secure=True)
            self.assertRedirects(response, f"{reverse('panel:login')}?next={url}", fetch_redirect_response=False)

    def test_common_user_forbidden(self):
        self.client.force_login(self.user_common)
        for url_name in ["booking_list", "booking_create"]:
            response = self.client.get(reverse(f"panel:{url_name}"), secure=True)
            self.assertEqual(response.status_code, 403)

    def test_staff_without_role_forbidden(self):
        self.client.force_login(self.user_staff)
        for url_name in ["booking_list", "booking_create"]:
            response = self.client.get(reverse(f"panel:{url_name}"), secure=True)
            self.assertEqual(response.status_code, 403)

    def test_seller_can_access(self):
        self.client.force_login(self.user_seller)
        response = self.client.get(reverse("panel:booking_list"), secure=True)
        self.assertEqual(response.status_code, 200)

    def test_admin_can_access(self):
        self.client.force_login(self.user_admin)
        response = self.client.get(reverse("panel:booking_list"), secure=True)
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access(self):
        self.client.force_login(self.user_super)
        response = self.client.get(reverse("panel:booking_list"), secure=True)
        self.assertEqual(response.status_code, 200)

    def test_operations_permissions_not_expanded_for_seller(self):
        """El acceso a reservas no amplÃ­a los permisos de escritura en operations."""
        self.client.force_login(self.user_seller)
        # El vendedor no puede crear ni modificar colectivos
        response = self.client.post(reverse("panel:bus_create"), {"code": "TEST", "display_name": "Test"}, secure=True)
        self.assertEqual(response.status_code, 403)


class ReservationListingAndSearchTests(ReservationPanelBaseTestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user_seller)

        # Crear reservas con distintos estados
        self.b_held = create_manual_booking(
            seller=self.user_seller,
            email="held@correo.com",
            phone="3511111111",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
            passengers_data=[{
                "first_name": "Juan",
                "last_name": "PÃ©rez",
                "document_type": "DNI",
                "document_number": "40.123.456",
                "birth_date": "1995-05-15",
                "nationality": "Argentina",
                "gender": "Masculino",
            }],
        )

        self.b_other = create_manual_booking(
            seller=self.user_admin,
            email="admin_booking@correo.com",
            phone="3512222222",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_2],
            }],
            passengers_data=[{
                "first_name": "MarÃ­a",
                "last_name": "GonzÃ¡lez",
                "document_type": "DNI",
                "document_number": "35.987.654",
                "birth_date": "1990-10-20",
                "nationality": "Argentina",
                "gender": "Femenino",
            }],
        )

    def test_listing_renders_required_fields(self):
        response = self.client.get(reverse("panel:booking_list"), secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Columnas e informaciÃ³n visible
        self.assertIn(str(self.b_held.public_id)[:8], content)
        self.assertIn("held@correo.com", content)
        self.assertIn("Córdoba Capital → San Salvador de Jujuy", content)
        self.assertIn("seller_test", content)
        self.assertIn("Retenida", content)

    def test_filter_by_status(self):
        response = self.client.get(reverse("panel:booking_list") + "?status=HELD", secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("held@correo.com", content)

        # Filtro por estado CONFIRMED (no hay ninguna aÃºn)
        response_conf = self.client.get(reverse("panel:booking_list") + "?status=CONFIRMED", secure=True)
        self.assertEqual(response_conf.status_code, 200)
        self.assertNotIn("held@correo.com", response_conf.content.decode("utf-8"))

    def test_filter_by_seller(self):
        response = self.client.get(reverse("panel:booking_list") + f"?seller={self.user_admin.pk}", secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("admin_booking@correo.com", content)
        self.assertNotIn("held@correo.com", content)

    def test_search_by_public_id(self):
        response = self.client.get(reverse("panel:booking_list") + f"?q={self.b_held.public_id}", secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("held@correo.com", content)
        self.assertNotIn("admin_booking@correo.com", content)

    def test_search_by_email(self):
        response = self.client.get(reverse("panel:booking_list") + "?q=admin_booking", secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("admin_booking@correo.com", content)
        self.assertNotIn("held@correo.com", content)

    def test_search_by_normalized_document(self):
        """BÃºsqueda por documento sin formato normalizado encuentra la reserva."""
        # El documento fue guardado con puntos: '40.123.456', buscamos '40123456'
        response = self.client.get(reverse("panel:booking_list") + "?q=40123456", secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("held@correo.com", content)
        self.assertNotIn("admin_booking@correo.com", content)

        # BÃºsqueda con espacios o minÃºsculas
        response_fmt = self.client.get(reverse("panel:booking_list") + "?q= 40.123.456 ", secure=True)
        self.assertEqual(response_fmt.status_code, 200)
        self.assertIn("held@correo.com", response_fmt.content.decode("utf-8"))

    def test_visual_status_badges_present(self):
        response = self.client.get(reverse("panel:booking_list"), secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("badge-status-held", content)


class ReservationDetailAndReleaseTests(ReservationPanelBaseTestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user_seller)

        self.booking = create_manual_booking(
            seller=self.user_seller,
            email="detalle@correo.com",
            phone="3513333333",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
            passengers_data=[{
                "first_name": "Carlos",
                "last_name": "Romero",
                "document_type": "DNI",
                "document_number": "30.456.789",
                "birth_date": "1985-03-12",
                "nationality": "Argentina",
                "gender": "Masculino",
            }],
        )
        # Registrar auditorÃ­a de creaciÃ³n inicial como lo hace el servicio del panel
        AuditEvent.objects.create(
            actor=self.user_seller,
            action=AuditEvent.Action.CREATE,
            entity_type=self.booking._meta.label,
            entity_id=str(self.booking.pk),
            description=f"CreaciÃ³n: Reserva {self.booking.public_id}",
            before={},
            after={},
        )

    def test_detail_view_shows_all_required_information(self):
        response = self.client.get(reverse("panel:booking_detail", kwargs={"public_id": self.booking.public_id}), secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Contacto
        self.assertIn("detalle@correo.com", content)
        self.assertIn("3513333333", content)
        # Tramos / paradas
        self.assertIn("Córdoba Capital", content)
        self.assertIn("San Salvador de Jujuy", content)
        # Pasajero y documento
        self.assertIn("Carlos Romero", content)
        self.assertIn("30.456.789", content)
        # Pasajero no almacena email ni telÃ©fono
        self.assertNotIn("carlos@correo.com", content)
        # Butaca, categorÃ­a y precio histÃ³rico
        self.assertIn("Butaca 1", content)
        self.assertIn("Cama", content)
        self.assertIn("15000.00", content)
        # Vendedor
        self.assertIn("seller_test", content)
        # Auditoría existente
        self.assertIn("CreaciÃ³n: Reserva", content)
        # AcciÃ³n para liberar visible para HELD
        self.assertIn("Liberar reserva", content)
        # NO ofrece confirmar
        self.assertNotIn("Confirmar reserva", content)
        self.assertNotIn("Confirmar pasaje", content)

    def test_idor_protection_invalid_uuid(self):
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        response = self.client.get(reverse("panel:booking_detail", kwargs={"public_id": fake_uuid}), secure=True)
        self.assertEqual(response.status_code, 404)

    def test_release_action_post_with_csrf_releases_held_booking_and_seats(self):
        release_url = reverse("panel:booking_release", kwargs={"public_id": self.booking.public_id})

        # GET debe ser rechazado con 405
        get_response = self.client.get(release_url, secure=True)
        self.assertEqual(get_response.status_code, 405)

        # POST libera la reserva
        post_response = self.client.post(release_url, secure=True)
        self.assertRedirects(post_response, reverse("panel:booking_detail", kwargs={"public_id": self.booking.public_id}))

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.RELEASED)

        assignment = SeatAssignment.objects.get(leg__booking=self.booking)
        self.assertEqual(assignment.status, AssignmentStatus.RELEASED)

        # La butaca queda nuevamente disponible para consultar
        avail = get_trip_availability(self.trip_outbound, self.ts_out_cba, self.ts_out_ssj)
        avail_seat_pks = [s.pk for s in avail["available_seats"]]
        self.assertIn(self.seat_cama_1.pk, avail_seat_pks)

        # Auditoría de liberaciÃ³n registrada
        audit = AuditEvent.objects.filter(entity_type=self.booking._meta.label, entity_id=str(self.booking.pk), description__startswith="Liberación:")
        self.assertTrue(audit.exists())
        self.assertEqual(audit.first().actor, self.user_seller)

    def test_release_action_not_offered_when_already_released(self):
        self.client.post(reverse("panel:booking_release", kwargs={"public_id": self.booking.public_id}), secure=True)
        detail_response = self.client.get(reverse("panel:booking_detail", kwargs={"public_id": self.booking.public_id}), secure=True)
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotIn("Liberar reserva", detail_response.content.decode("utf-8"))

    def test_cannot_release_confirmed_booking(self):
        # Confirmar reserva
        self.booking.status = BookingStatus.CONFIRMED
        self.booking.confirmed_at = timezone.now()
        self.booking.save()

        release_url = reverse("panel:booking_release", kwargs={"public_id": self.booking.public_id})
        response = self.client.post(release_url, secure=True)
        self.assertRedirects(response, reverse("panel:booking_detail", kwargs={"public_id": self.booking.public_id}))

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)


class ReservationCreationTests(ReservationPanelBaseTestCase):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user_seller)

    def test_one_way_manual_booking_success(self):
        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_outbound.pk),
            "outbound_origin": str(self.ts_out_cba.pk),
            "outbound_destination": str(self.ts_out_ssj.pk),
            "outbound_seats": [str(self.seat_cama_1.pk)],
            "contact_email": "nuevo_cliente@correo.com",
            "contact_phone": "3514444444",
            "p_1_first_name": "AgustÃ­n",
            "p_1_last_name": "Morales",
            "p_1_document_type": "DNI",
            "p_1_document_number": "38.555.666",
            "p_1_birth_date": "1993-07-22",
            "p_1_nationality": "Argentina",
            "p_1_gender": "Masculino",
        }

        response = self.client.post(create_url, post_data, secure=True)
        booking = Booking.objects.filter(email="nuevo_cliente@correo.com").first()
        self.assertIsNotNone(booking)
        self.assertRedirects(response, reverse("panel:booking_detail", kwargs={"public_id": booking.public_id}))

        self.assertEqual(booking.channel, BookingChannel.MANUAL)
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertEqual(booking.seller, self.user_seller)
        self.assertEqual(booking.passengers.count(), 1)

        p = booking.passengers.first()
        self.assertEqual(p.first_name, "AgustÃ­n")
        self.assertEqual(p.last_name, "Morales")
        self.assertEqual(p.normalized_document, "38555666")
        self.assertFalse(hasattr(p, "email"))
        self.assertFalse(hasattr(p, "phone"))

        # Snapshot de asignaciÃ³n de butaca y precio tomado del servidor
        assignment = SeatAssignment.objects.get(passenger=p)
        self.assertEqual(assignment.seat, self.seat_cama_1)
        self.assertEqual(assignment.price, Decimal("15000.00"))
        self.assertEqual(assignment.status, AssignmentStatus.HELD)

        # Auditoría de creaciÃ³n registrada en panel.AuditEvent en la misma transacciÃ³n
        audit = AuditEvent.objects.filter(entity_type=booking._meta.label, entity_id=str(booking.pk), action=AuditEvent.Action.CREATE)
        self.assertTrue(audit.exists())
        self.assertEqual(audit.first().actor, self.user_seller)
        audit_after = audit.first().after
        self.assertEqual(audit_after["email"], "nuevo_cliente@correo.com")
        self.assertEqual(audit_after["phone"], "3514444444")
        p_snapshot = audit_after["passengers"][0]
        self.assertEqual(p_snapshot["first_name"], "AgustÃ­n")
        self.assertNotIn("email", p_snapshot)
        self.assertNotIn("phone", p_snapshot)

    def test_round_trip_manual_booking_success(self):
        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "trip_type": "roundtrip",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_outbound.pk),
            "outbound_origin": str(self.ts_out_cba.pk),
            "outbound_destination": str(self.ts_out_ssj.pk),
            "outbound_seats": [str(self.seat_cama_1.pk)],
            "return_trip": str(self.trip_return.pk),
            "return_origin": str(self.ts_ret_ssj.pk),
            "return_destination": str(self.ts_ret_cba.pk),
            "return_seats": [str(self.seat_b2_cama_1.pk)],
            "contact_email": "turista_idavuelta@correo.com",
            "contact_phone": "3517777777",
            "p_1_first_name": "LucÃ­a",
            "p_1_last_name": "FernÃ¡ndez",
            "p_1_document_type": "DNI",
            "p_1_document_number": "39.111.222",
            "p_1_birth_date": "1994-09-08",
            "p_1_nationality": "Argentina",
            "p_1_gender": "Femenino",
        }

        response = self.client.post(create_url, post_data, secure=True)
        booking = Booking.objects.filter(email="turista_idavuelta@correo.com").first()
        self.assertIsNotNone(booking)
        self.assertRedirects(response, reverse("panel:booking_detail", kwargs={"public_id": booking.public_id}))

        self.assertEqual(booking.legs.count(), 2)
        leg1, leg2 = booking.legs.order_by("sequence")
        self.assertEqual(leg1.trip, self.trip_outbound)
        self.assertEqual(leg2.trip, self.trip_return)

        # Precios histÃ³ricos por asignaciÃ³n
        self.assertEqual(leg1.seat_assignments.first().price, Decimal("15000.00"))
        self.assertEqual(leg2.seat_assignments.first().price, Decimal("16000.00"))

    def test_price_manipulation_ignored(self):
        """Los precios enviados desde el navegador son ignorados; se usa la tarifa del servidor."""
        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_outbound.pk),
            "outbound_origin": str(self.ts_out_cba.pk),
            "outbound_destination": str(self.ts_out_ssj.pk),
            "outbound_seats": [str(self.seat_cama_1.pk)],
            "price": "1.00",  # Intento malicioso de manipular precio
            "contact_email": "hacker@correo.com",
            "p_1_first_name": "Bad",
            "p_1_last_name": "Actor",
            "p_1_document_type": "DNI",
            "p_1_document_number": "11.222.333",
            "p_1_birth_date": "1990-01-01",
            "p_1_nationality": "Argentina",
        }

        self.client.post(create_url, post_data, secure=True)
        booking = Booking.objects.filter(email="hacker@correo.com").first()
        self.assertIsNotNone(booking)
        # El precio guardado debe ser 15000.00, no 1.00
        assignment = SeatAssignment.objects.get(leg__booking=booking)
        self.assertEqual(assignment.price, Decimal("15000.00"))

    def test_occupied_seat_rejected_atomically(self):
        # Ocupar butaca 1
        create_manual_booking(
            seller=self.user_seller,
            email="primero@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )

        initial_booking_count = Booking.objects.count()
        initial_assignment_count = SeatAssignment.objects.count()

        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_outbound.pk),
            "outbound_origin": str(self.ts_out_cba.pk),
            "outbound_destination": str(self.ts_out_ssj.pk),
            "outbound_seats": [str(self.seat_cama_1.pk)],  # Butaca ya ocupada
            "contact_email": "segundo@correo.com",
            "p_1_first_name": "Segundo",
            "p_1_last_name": "Usuario",
            "p_1_document_type": "DNI",
            "p_1_document_number": "22.333.444",
            "p_1_birth_date": "1992-02-02",
            "p_1_nationality": "Argentina",
        }

        response = self.client.post(create_url, post_data, secure=True)
        self.assertEqual(response.status_code, 200)

        # Rollback atÃ³mico verificado
        self.assertEqual(Booking.objects.count(), initial_booking_count)
        self.assertEqual(SeatAssignment.objects.count(), initial_assignment_count)
        self.assertFalse(Booking.objects.filter(email="segundo@correo.com").exists())

    def test_alien_seat_rejected(self):
        """Butaca perteneciente a otro colectivo es rechazada."""
        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_outbound.pk),  # Bus 1
            "outbound_origin": str(self.ts_out_cba.pk),
            "outbound_destination": str(self.ts_out_ssj.pk),
            "outbound_seats": [str(self.seat_b2_cama_1.pk)],  # Butaca del Bus 2
            "contact_email": "alien@correo.com",
            "p_1_first_name": "Alien",
            "p_1_last_name": "Seat",
            "p_1_document_type": "DNI",
            "p_1_document_number": "99.888.777",
            "p_1_birth_date": "1990-01-01",
            "p_1_nationality": "Argentina",
        }

        response = self.client.post(create_url, post_data, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Booking.objects.filter(email="alien@correo.com").exists())

    def test_missing_passenger_required_fields_rejected(self):
        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_outbound.pk),
            "outbound_origin": str(self.ts_out_cba.pk),
            "outbound_destination": str(self.ts_out_ssj.pk),
            "outbound_seats": [str(self.seat_cama_1.pk)],
            "contact_email": "incompleto@correo.com",
            # Sin first_name ni last_name ni document_number
            "p_1_first_name": "",
            "p_1_last_name": "",
            "p_1_document_type": "DNI",
            "p_1_document_number": "",
            "p_1_birth_date": "",
            "p_1_nationality": "Argentina",
        }

        response = self.client.post(create_url, post_data, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Booking.objects.filter(email="incompleto@correo.com").exists())

    def test_passenger_document_normalization_and_reuse_allowed(self):
        """Mismo documento puede usarse en reservas distintas (sin restricciÃ³n global de unicidad)."""
        create_manual_booking(
            seller=self.user_seller,
            email="reserva1@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
            passengers_data=[{
                "first_name": "Pablo",
                "last_name": "GÃ³mez",
                "document_type": "DNI",
                "document_number": "33.444.555",
                "birth_date": "1988-04-04",
                "nationality": "Argentina",
            }],
        )

        # Crear segunda reserva con el mismo documento pero en el viaje de regreso
        create_manual_booking(
            seller=self.user_seller,
            email="reserva2@correo.com",
            legs=[{
                "trip": self.trip_return,
                "origin_stop": self.ts_ret_ssj,
                "destination_stop": self.ts_ret_cba,
                "seats": [self.seat_b2_cama_1],
            }],
            passengers_data=[{
                "first_name": "Pablo",
                "last_name": "GÃ³mez",
                "document_type": "DNI",
                "document_number": "33.444.555",
                "birth_date": "1988-04-04",
                "nationality": "Argentina",
            }],
        )

        # Ambas reservas deben coexistir sin violar unicidad global
        p1 = BookingPassenger.objects.filter(booking__email="reserva1@correo.com").first()
        p2 = BookingPassenger.objects.filter(booking__email="reserva2@correo.com").first()
        self.assertIsNotNone(p1)
        self.assertIsNotNone(p2)
        self.assertEqual(p1.normalized_document, "33444555")
        self.assertEqual(p2.normalized_document, "33444555")

    @override_settings(SALES_MANUAL_HOLD_HOURS=48)
    def test_configurable_manual_hold_hours(self):
        now = timezone.now()
        booking = create_manual_booking(
            seller=self.user_seller,
            email="hold48@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
            now=now,
        )
        self.assertEqual(booking.expires_at, now + timedelta(hours=48))

    @override_settings(SALES_MAX_PASSENGERS_PER_BOOKING=2)
    def test_configurable_max_passengers_limit_enforced(self):
        with self.assertRaises(Exception):
            create_manual_booking(
                seller=self.user_seller,
                email="excedido@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1, self.seat_cama_2, self.seat_semi_3],  # 3 > 2
                }],
            )

    def test_started_or_cancelled_trips_excluded(self):
        self.trip_outbound.status = Trip.Status.STARTED
        self.trip_outbound.save()

        create_url = reverse("panel:booking_create")
        post_data = {
            "action": "create_booking",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_outbound.pk),
            "outbound_origin": str(self.ts_out_cba.pk),
            "outbound_destination": str(self.ts_out_ssj.pk),
            "outbound_seats": [str(self.seat_cama_1.pk)],
            "contact_email": "started@correo.com",
            "p_1_first_name": "Test",
            "p_1_last_name": "Test",
            "p_1_document_type": "DNI",
            "p_1_document_number": "12345678",
            "p_1_birth_date": "1990-01-01",
            "p_1_nationality": "Argentina",
        }

        response = self.client.post(create_url, post_data, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Booking.objects.filter(email="started@correo.com").exists())

    def test_csrf_protection(self):
        """PeticiÃ³n sin token CSRF es rechazada por el middleware de Django."""
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user_seller)

        response = csrf_client.post(reverse("panel:booking_create"), {"action": "create_booking"}, secure=True)
        self.assertEqual(response.status_code, 403)

    def test_templates_do_not_use_remote_resources(self):
        """Verificar que no existan enlaces a CDN externos ni dependencias remotas."""
        for url_name in ["booking_list", "booking_create"]:
            response = self.client.get(reverse(f"panel:{url_name}"), secure=True)
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertNotIn("cdn.tailwindcss.com", content)
            self.assertNotIn("https://", content)
            self.assertNotIn("http://", content)

    def test_build_deck_map_includes_has_fare_and_categories(self):
        from panel.reservation_views import _build_deck_map
        active_seats = [self.seat_cama_1, self.seat_semi_3]
        # Solo CAMA tiene tarifa configurada
        fares = {SeatCategory.CAMA: Decimal("15000.00")}
        decks = _build_deck_map(self.bus_1, active_seats, set(), fares)

        deck_items = [item for d in decks for item in d["items"]]
        cama_item = next(i for i in deck_items if i["seat"] == self.seat_cama_1)
        semi_item = next(i for i in deck_items if i["seat"] == self.seat_semi_3)

        self.assertIn("has_fare", cama_item)
        self.assertTrue(cama_item["has_fare"])
        self.assertTrue(cama_item["is_available"])
        self.assertEqual(cama_item["category_display"], "Cama")

        self.assertIn("has_fare", semi_item)
        self.assertFalse(semi_item["has_fare"])
        self.assertFalse(semi_item["is_available"])
        self.assertEqual(semi_item["category_display"], "Semicama")

    def test_deck_map_template_classifies_seats_with_and_without_fare(self):
        # Desactivar temporalmente la tarifa de SEMI_CAMA
        self.fare_out_semi.is_active = False
        self.fare_out_semi.save()

        try:
            url = reverse("panel:booking_create")
            params = {
                "outbound_trip": str(self.trip_outbound.pk),
                "outbound_origin": str(self.ts_out_cba.pk),
                "outbound_destination": str(self.ts_out_ssj.pk),
            }
            response = self.client.get(url, params, secure=True)
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")

            # Butaca con tarifa (CAMA) debe ser disponible y mostrar Cama
            self.assertIn("available", content)
            self.assertIn("badge-cama", content)
            self.assertIn("Cama", content)

            # Butaca sin tarifa (SEMI_CAMA) debe ser no-fare y mostrar Semicama
            self.assertIn("no-fare", content)
            self.assertIn("Sin tarifa", content)
            self.assertIn("badge-semi_cama", content)
            self.assertIn("Semicama", content)
        finally:
            self.fare_out_semi.is_active = True
            self.fare_out_semi.save()

    def test_seat_selection_without_fare_rejected_by_service(self):
        # Desactivar temporalmente la tarifa de SEMI_CAMA
        self.fare_out_semi.is_active = False
        self.fare_out_semi.save()

        try:
            with self.assertRaises(ValidationError) as ctx:
                create_manual_booking(
                    seller=self.user_seller,
                    email="sin_tarifa@correo.com",
                    legs=[{
                        "trip": self.trip_outbound,
                        "origin_stop": self.ts_out_cba,
                        "destination_stop": self.ts_out_ssj,
                        "seats": [self.seat_semi_3],  # Butaca sin tarifa activa
                    }],
                )
            self.assertIn("tarifa activa", str(ctx.exception))
        finally:
            self.fare_out_semi.is_active = True
            self.fare_out_semi.save()
