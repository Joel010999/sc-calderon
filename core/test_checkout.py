from datetime import date, datetime, timedelta
from decimal import Decimal
import uuid
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from sales.conf import get_online_cutoff_minutes, get_online_hold_minutes
from sales.exceptions import SeatUnavailableError
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)
from sales.services import create_online_booking

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class CheckoutBaseTestCase(TestCase):
    """Base con datos iniciales para pruebas del checkout público."""

    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.today_ar = timezone.localtime(self.now, AR_TZ).date()
        self.future_date = self.today_ar + timedelta(days=3)

        # 1. Paradas y recorrido
        self.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        self.stop_ssj = Stop.objects.create(code="SSJ", name="San Salvador de Jujuy", city="Jujuy", province="Jujuy")
        self.stop_per = Stop.objects.create(code="PER", name="Perico", city="Perico", province="Jujuy")

        self.route_ida = Route.objects.create(code="CBA-SSJ", name="Córdoba → Jujuy")
        RouteStop.objects.create(route=self.route_ida, stop=self.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route_ida, stop=self.stop_ssj, sequence=2, allows_boarding=False, allows_alighting=True)

        self.route_vuelta = Route.objects.create(code="SSJ-CBA", name="Jujuy → Córdoba")
        RouteStop.objects.create(route=self.route_vuelta, stop=self.stop_ssj, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route_vuelta, stop=self.stop_cba, sequence=2, allows_boarding=False, allows_alighting=True)

        # 2. Colectivo y butacas
        self.bus = Bus.objects.create(code="BUS-01", display_name="Scania Starlink", license_plate="AE123CD")
        self.seat_cama = Seat.objects.create(
            bus=self.bus, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0
        )
        self.seat_semicama1 = Seat.objects.create(
            bus=self.bus, number=2, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0
        )
        self.seat_semicama2 = Seat.objects.create(
            bus=self.bus, number=3, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0
        )

        # Colectivo 2 para regreso
        self.bus_ret = Bus.objects.create(code="BUS-02", display_name="Volvo Starlink", license_plate="AE999ZZ")
        self.seat_ret1 = Seat.objects.create(
            bus=self.bus_ret, number=10, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0
        )
        self.seat_ret2 = Seat.objects.create(
            bus=self.bus_ret, number=11, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0
        )

        # 3. Viaje de ida en horario futuro consciente de zona horaria
        dep_out = datetime.combine(self.future_date, datetime.min.time(), tzinfo=AR_TZ).replace(hour=14)
        arr_out = dep_out + timedelta(hours=12)

        self.trip_out = Trip.objects.create(
            route=self.route_ida, bus=self.bus, departure_at=dep_out, status=Trip.Status.SCHEDULED
        )
        self.ts_out_orig = TripStop.objects.create(
            trip=self.trip_out, stop=self.stop_cba, sequence=1, scheduled_at=dep_out,
            allows_boarding=True, allows_alighting=False
        )
        self.ts_out_dest = TripStop.objects.create(
            trip=self.trip_out, stop=self.stop_ssj, sequence=2, scheduled_at=arr_out,
            allows_boarding=False, allows_alighting=True
        )

        # Tarifas de ida
        self.fare_out_cama = TripFare.objects.create(
            trip=self.trip_out, origin_stop=self.ts_out_orig, destination_stop=self.ts_out_dest,
            seat_category=SeatCategory.CAMA, amount=Decimal("60000.00"), currency="ARS", is_active=True
        )
        self.fare_out_semi = TripFare.objects.create(
            trip=self.trip_out, origin_stop=self.ts_out_orig, destination_stop=self.ts_out_dest,
            seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("50000.00"), currency="ARS", is_active=True
        )

        # 4. Viaje de regreso (2 días después de la ida)
        self.return_date = self.future_date + timedelta(days=2)
        dep_ret = datetime.combine(self.return_date, datetime.min.time(), tzinfo=AR_TZ).replace(hour=18)
        arr_ret = dep_ret + timedelta(hours=12)

        self.trip_ret = Trip.objects.create(
            route=self.route_vuelta, bus=self.bus_ret, departure_at=dep_ret, status=Trip.Status.SCHEDULED
        )
        self.ts_ret_orig = TripStop.objects.create(
            trip=self.trip_ret, stop=self.stop_ssj, sequence=1, scheduled_at=dep_ret,
            allows_boarding=True, allows_alighting=False
        )
        self.ts_ret_dest = TripStop.objects.create(
            trip=self.trip_ret, stop=self.stop_cba, sequence=2, scheduled_at=arr_ret,
            allows_boarding=False, allows_alighting=True
        )

        # Tarifas de regreso
        self.fare_ret_cama = TripFare.objects.create(
            trip=self.trip_ret, origin_stop=self.ts_ret_orig, destination_stop=self.ts_ret_dest,
            seat_category=SeatCategory.CAMA, amount=Decimal("62000.00"), currency="ARS", is_active=True
        )
        self.fare_ret_semi = TripFare.objects.create(
            trip=self.trip_ret, origin_stop=self.ts_ret_orig, destination_stop=self.ts_ret_dest,
            seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("52000.00"), currency="ARS", is_active=True
        )


class HomeViewTests(CheckoutBaseTestCase):
    """Pruebas de la página institucional de inicio."""

    def test_home_renders_institutional_page_without_breaking(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("ÉXODO", content)
        self.assertIn("Viajes y Turismo", content)
        self.assertIn("¿A dónde viajás?", content)
        self.assertIn("wi-fi a bordo", content.lower())
        self.assertIn("Córdoba", content)
        self.assertIn("Jujuy", content)

    def test_home_search_form_targets_buscar_viajes(self):
        response = self.client.get(reverse("home"))
        content = response.content.decode("utf-8")
        self.assertIn('action="/buscar/"', content)
        self.assertIn('name="trip_type"', content)


class SearchTripsViewTests(CheckoutBaseTestCase):
    """Pruebas de la búsqueda pública de viajes."""

    def test_search_empty_shows_search_interface(self):
        response = self.client.get(reverse("buscar_viajes"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buscador de viajes")

    def test_search_missing_date_shows_error(self):
        response = self.client.get(reverse("buscar_viajes"), {
            "origin": str(self.stop_cba.pk),
            "destination": str(self.stop_ssj.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Indicá la fecha para el viaje de ida.")

    def test_search_same_origin_and_destination_shows_error(self):
        response = self.client.get(reverse("buscar_viajes"), {
            "origin": str(self.stop_cba.pk),
            "destination": str(self.stop_cba.pk),
            "date": self.future_date.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El origen y el destino deben ser paradas diferentes.")

    def test_search_finds_real_trips_by_argentine_date(self):
        response = self.client.get(reverse("buscar_viajes"), {
            "origin": str(self.stop_cba.pk),
            "destination": str(self.stop_ssj.pk),
            "date": self.future_date.isoformat(),
            "trip_type": "oneway",
            "passengers": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scania Starlink")
        self.assertContains(response, "14:00 hs")
        self.assertContains(response, "3 butacas disponibles")
        self.assertContains(response, "$50000.00")

    def test_search_does_not_return_trips_for_other_dates(self):
        other_date = self.future_date + timedelta(days=5)
        response = self.client.get(reverse("buscar_viajes"), {
            "origin": str(self.stop_cba.pk),
            "destination": str(self.stop_ssj.pk),
            "date": other_date.isoformat(),
            "trip_type": "oneway",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No encontramos salidas disponibles")

    def test_search_respects_online_cutoff(self):
        # Crear viaje cuya salida sea dentro de 30 minutos (menor al cutoff de 60 min)
        soon = timezone.now() + timedelta(minutes=30)
        trip_soon = Trip.objects.create(
            route=self.route_ida, bus=self.bus, departure_at=soon, status=Trip.Status.SCHEDULED
        )
        ts1 = TripStop.objects.create(trip=trip_soon, stop=self.stop_cba, sequence=1, scheduled_at=soon, allows_boarding=True, allows_alighting=False)
        ts2 = TripStop.objects.create(trip=trip_soon, stop=self.stop_ssj, sequence=2, scheduled_at=soon + timedelta(hours=10), allows_boarding=False, allows_alighting=True)
        TripFare.objects.create(trip=trip_soon, origin_stop=ts1, destination_stop=ts2, seat_category=SeatCategory.CAMA, amount=Decimal("50000.00"), is_active=True)

        today_iso = timezone.localtime(soon, AR_TZ).date().isoformat()
        response = self.client.get(reverse("buscar_viajes"), {
            "origin": str(self.stop_cba.pk),
            "destination": str(self.stop_ssj.pk),
            "date": today_iso,
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(soon.strftime("%H:%M")))

    def test_search_excludes_started_or_cancelled_trips(self):
        self.trip_out.status = Trip.Status.STARTED
        self.trip_out.save()

        response = self.client.get(reverse("buscar_viajes"), {
            "origin": str(self.stop_cba.pk),
            "destination": str(self.stop_ssj.pk),
            "date": self.future_date.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No encontramos salidas disponibles")

    def test_roundtrip_search_finds_both_legs(self):
        response = self.client.get(reverse("buscar_viajes"), {
            "origin": str(self.stop_cba.pk),
            "destination": str(self.stop_ssj.pk),
            "date": self.future_date.isoformat(),
            "return_date": self.return_date.isoformat(),
            "trip_type": "roundtrip",
            "passengers": "2",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paso 1")
        self.assertContains(response, "Viaje de ida: Córdoba Capital → San Salvador de Jujuy")
        self.assertContains(response, "Paso 2")
        self.assertContains(response, "Viaje de regreso: San Salvador de Jujuy → Córdoba Capital")
        self.assertContains(response, "Continuar a la selección de butacas")


class CheckoutViewTests(CheckoutBaseTestCase):
    """Pruebas de la pantalla de selección de butacas y checkout."""

    def test_checkout_get_renders_seat_map_and_passenger_form(self):
        response = self.client.get(reverse("checkout"), {
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "passengers": "2",
            "trip_type": "oneway",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selección de Butacas")
        self.assertContains(response, "Datos de los Pasajeros")
        self.assertContains(response, "Pasajero 1")
        self.assertContains(response, "Pasajero 2")
        self.assertContains(response, "Butaca 1")
        self.assertContains(response, "Planta baja")
        self.assertContains(response, "Planta alta")

    def test_checkout_occupied_seats_are_disabled(self):
        # Ocupar butaca 1
        create_online_booking(
            email="ocupante@correo.com",
            legs=[{
                "trip": self.trip_out,
                "origin_stop": self.ts_out_orig,
                "destination_stop": self.ts_out_dest,
                "seats": [self.seat_cama],
            }],
        )

        response = self.client.get(reverse("checkout"), {
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "passengers": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is-occupied")
        self.assertContains(response, "Ocupada")

    def test_checkout_roundtrip_renders_both_deck_maps(self):
        response = self.client.get(reverse("checkout"), {
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "return_trip": str(self.trip_ret.pk),
            "return_origin": str(self.ts_ret_orig.pk),
            "return_destination": str(self.ts_ret_dest.pk),
            "passengers": "1",
            "trip_type": "roundtrip",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Butacas viaje de ida")
        self.assertContains(response, "Butacas viaje de regreso")
        self.assertContains(response, "Volvo Starlink")

    def test_checkout_rejects_roundtrip_with_same_trip(self):
        response = self.client.get(reverse("checkout"), {
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "return_trip": str(self.trip_out.pk),
            "return_origin": str(self.ts_out_dest.pk),
            "return_destination": str(self.ts_out_orig.pk),
            "passengers": "1",
            "trip_type": "roundtrip",
        })
        self.assertRedirects(response, reverse("buscar_viajes"))


class CreatePublicBookingViewTests(CheckoutBaseTestCase):
    """Pruebas de la creación transaccional de reservas online."""

    def test_honeypot_field_blocks_bots(self):
        payload = {
            "website": "http://spambot.com",
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "outbound_seats": [str(self.seat_cama.pk)],
            "email": "pasajero@correo.com",
            "phone": "388 123456",
            "passenger_1_first_name": "María",
            "passenger_1_last_name": "Gómez",
            "passenger_1_document_type": "DNI",
            "passenger_1_document_number": "35123456",
            "passenger_1_birth_date": "1990-05-15",
            "passenger_1_nationality": "Argentina",
        }
        response = self.client.post(reverse("crear_reserva"), payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Booking.objects.count(), 0)

    def test_rate_limit_blocks_excessive_holds_in_session(self):
        session = self.client.session
        now_ts = timezone.now().timestamp()
        session["recent_holds"] = [now_ts - 100, now_ts - 200, now_ts - 300]
        session.save()

        payload = {
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "outbound_seats": [str(self.seat_cama.pk)],
            "email": "pasajero@correo.com",
            "phone": "388 123456",
            "passenger_1_first_name": "María",
            "passenger_1_last_name": "Gómez",
            "passenger_1_document_type": "DNI",
            "passenger_1_document_number": "35123456",
            "passenger_1_birth_date": "1990-05-15",
            "passenger_1_nationality": "Argentina",
        }
        response = self.client.post(reverse("crear_reserva"), payload)
        self.assertRedirects(response, reverse("buscar_viajes"))
        self.assertEqual(Booking.objects.count(), 0)

    def test_create_booking_success_oneway_with_passengers_data(self):
        payload = {
            "trip_type": "oneway",
            "passengers_count": "2",
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "outbound_seats": [str(self.seat_cama.pk), str(self.seat_semicama1.pk)],
            "email": "contacto@correo.com",
            "phone": "351 9876543",
            "passenger_1_first_name": "Carlos",
            "passenger_1_last_name": "López",
            "passenger_1_document_type": "DNI",
            "passenger_1_document_number": "33.444.555",
            "passenger_1_birth_date": "1988-10-20",
            "passenger_1_nationality": "Argentina",
            "passenger_1_gender": "Masculino",
            "passenger_2_first_name": "Ana",
            "passenger_2_last_name": "Pérez",
            "passenger_2_document_type": "DNI",
            "passenger_2_document_number": "40.111.222",
            "passenger_2_birth_date": "1998-03-12",
            "passenger_2_nationality": "Argentina",
            "passenger_2_gender": "Femenino",
        }

        response = self.client.post(reverse("crear_reserva"), payload)
        self.assertEqual(response.status_code, 302)

        booking = Booking.objects.first()
        self.assertIsNotNone(booking)
        self.assertEqual(booking.channel, BookingChannel.ONLINE)
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertEqual(booking.email, "contacto@correo.com")
        self.assertIsNone(booking.seller)

        # Verificar redirección a resumen protegido
        self.assertRedirects(response, reverse("resumen_reserva", kwargs={"public_id": booking.public_id}))

        # Verificar token en sesión
        session_token = self.client.session.get(f"booking_access_{booking.public_id}")
        self.assertIsNotNone(session_token)

        # Verificar ausencia de datos personales en sesión
        session_dump = str(self.client.session.items())
        self.assertNotIn("Carlos", session_dump)
        self.assertNotIn("33.444.555", session_dump)
        self.assertNotIn("33444555", session_dump)

        # Verificar pasajeros creados con datos completos y normalizados
        passengers = list(booking.passengers.order_by("position"))
        self.assertEqual(len(passengers), 2)
        self.assertEqual(passengers[0].first_name, "Carlos")
        self.assertEqual(passengers[0].last_name, "López")
        self.assertEqual(passengers[0].normalized_document, "33444555")
        self.assertEqual(passengers[0].birth_date, date(1988, 10, 20))
        self.assertEqual(passengers[1].first_name, "Ana")
        self.assertEqual(passengers[1].normalized_document, "40111222")

        # Verificar asignaciones de butaca
        assignments = list(SeatAssignment.objects.filter(leg__booking=booking).order_by("seat_number"))
        self.assertEqual(len(assignments), 2)
        self.assertEqual(assignments[0].seat, self.seat_cama)
        self.assertEqual(assignments[0].price, Decimal("60000.00"))
        self.assertEqual(assignments[1].seat, self.seat_semicama1)
        self.assertEqual(assignments[1].price, Decimal("50000.00"))

    def test_create_booking_success_roundtrip(self):
        payload = {
            "trip_type": "roundtrip",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "outbound_seats": [str(self.seat_cama.pk)],
            "return_trip": str(self.trip_ret.pk),
            "return_origin": str(self.ts_ret_orig.pk),
            "return_destination": str(self.ts_ret_dest.pk),
            "return_seats": [str(self.seat_ret1.pk)],
            "email": "viajero@correo.com",
            "phone": "388 999888",
            "passenger_1_first_name": "Sofía",
            "passenger_1_last_name": "Ríos",
            "passenger_1_document_type": "DNI",
            "passenger_1_document_number": "38999888",
            "passenger_1_birth_date": "1994-07-04",
            "passenger_1_nationality": "Argentina",
        }

        response = self.client.post(reverse("crear_reserva"), payload)
        self.assertEqual(response.status_code, 302)

        booking = Booking.objects.first()
        self.assertIsNotNone(booking)
        self.assertEqual(booking.legs.count(), 2)

        leg1 = booking.legs.get(sequence=1)
        self.assertEqual(leg1.trip, self.trip_out)
        self.assertEqual(leg1.origin_stop, self.ts_out_orig)

        leg2 = booking.legs.get(sequence=2)
        self.assertEqual(leg2.trip, self.trip_ret)
        self.assertEqual(leg2.origin_stop, self.ts_ret_orig)

    def test_create_booking_atomic_rollback_on_conflict(self):
        # Ocupar la butaca 1 antes del POST concurrente
        create_online_booking(
            email="primero@correo.com",
            legs=[{
                "trip": self.trip_out,
                "origin_stop": self.ts_out_orig,
                "destination_stop": self.ts_out_dest,
                "seats": [self.seat_cama],
            }],
        )

        initial_bookings_count = Booking.objects.count()

        # Intentar reservar la misma butaca ocupada
        payload = {
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "outbound_seats": [str(self.seat_cama.pk)],
            "email": "segundo@correo.com",
            "phone": "388 111222",
            "passenger_1_first_name": "Segundo",
            "passenger_1_last_name": "Llegó",
            "passenger_1_document_type": "DNI",
            "passenger_1_document_number": "39111222",
            "passenger_1_birth_date": "1995-01-01",
            "passenger_1_nationality": "Argentina",
        }

        response = self.client.post(reverse("crear_reserva"), payload)
        self.assertRedirects(response, reverse("buscar_viajes"))

        # Rollback atómico verificado: no se creó ninguna reserva adicional ni huérfana
        self.assertEqual(Booking.objects.count(), initial_bookings_count)
        self.assertEqual(BookingPassenger.objects.filter(first_name="Segundo").count(), 0)

    def test_create_booking_server_revalidation_rejects_tampered_seats(self):
        # Intentar pasar una butaca que pertenece al colectivo 2 en el viaje 1
        payload = {
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "outbound_seats": [str(self.seat_ret1.pk)],  # Butaca ajena
            "email": "hacker@correo.com",
            "phone": "388 000000",
            "passenger_1_first_name": "Test",
            "passenger_1_last_name": "Tamper",
            "passenger_1_document_type": "DNI",
            "passenger_1_document_number": "33000000",
            "passenger_1_birth_date": "1990-01-01",
            "passenger_1_nationality": "Argentina",
        }
        response = self.client.post(reverse("crear_reserva"), payload)
        self.assertRedirects(response, reverse("buscar_viajes"))
        self.assertEqual(Booking.objects.count(), 0)


class BookingSummaryViewTests(CheckoutBaseTestCase):
    """Pruebas del resumen público protegido y vencimientos."""

    def setUp(self):
        super().setUp()
        self.booking = create_online_booking(
            email="titular@correo.com",
            phone="388 4567890",
            legs=[{
                "trip": self.trip_out,
                "origin_stop": self.ts_out_orig,
                "destination_stop": self.ts_out_dest,
                "seats": [self.seat_cama],
            }],
            passengers_data=[{
                "first_name": "Martín",
                "last_name": "Fierro",
                "document_type": "DNI",
                "document_number": "12345678",
                "birth_date": "1980-01-01",
                "nationality": "Argentina",
            }],
        )

    def test_summary_access_forbidden_without_session_token(self):
        # Cliente sin sesión
        response = self.client.get(reverse("resumen_reserva", kwargs={"public_id": self.booking.public_id}))
        self.assertEqual(response.status_code, 403)

    def test_summary_access_allowed_with_valid_session_token(self):
        session = self.client.session
        session[f"booking_access_{self.booking.public_id}"] = "valid_token_123"
        session.save()

        response = self.client.get(reverse("resumen_reserva", kwargs={"public_id": self.booking.public_id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.booking.public_id))
        self.assertContains(response, "Martín Fierro")
        self.assertContains(response, "Butaca")
        self.assertContains(response, "60000.00")
        self.assertContains(response, "Tiempo de retención restante")

    def test_summary_payment_button_shows_pending_notice(self):
        session = self.client.session
        session[f"booking_access_{self.booking.public_id}"] = "valid_token_123"
        session.save()

        response = self.client.get(reverse("resumen_reserva", kwargs={"public_id": self.booking.public_id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pagos online pendientes de activación")
        self.assertContains(response, "Continuar al pago")
        self.assertContains(response, reverse("pago_pendiente", kwargs={"public_id": self.booking.public_id}))
        # Asegurar que payments no se expone
        self.assertNotContains(response, "/payments/")

    def test_summary_auto_expires_when_time_elapsed(self):
        # Adelantar el vencimiento al pasado
        self.booking.expires_at = timezone.now() - timedelta(minutes=5)
        self.booking.save(update_fields=["expires_at"])

        session = self.client.session
        session[f"booking_access_{self.booking.public_id}"] = "valid_token_123"
        session.save()

        response = self.client.get(reverse("resumen_reserva", kwargs={"public_id": self.booking.public_id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El plazo de retención ha expirado")

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.EXPIRED)
        self.assertEqual(
            SeatAssignment.objects.filter(leg__booking=self.booking).first().status,
            AssignmentStatus.RELEASED
        )

    def test_expire_public_booking_action_releases_hold(self):
        session = self.client.session
        session[f"booking_access_{self.booking.public_id}"] = "valid_token_123"
        session.save()

        response = self.client.post(reverse("expirar_reserva", kwargs={"public_id": self.booking.public_id}))
        self.assertRedirects(response, reverse("resumen_reserva", kwargs={"public_id": self.booking.public_id}))

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.RELEASED)
        self.assertEqual(
            SeatAssignment.objects.filter(leg__booking=self.booking).first().status,
            AssignmentStatus.RELEASED
        )

    def test_expire_public_booking_forbidden_without_session_token(self):
        response = self.client.post(reverse("expirar_reserva", kwargs={"public_id": self.booking.public_id}))
        self.assertEqual(response.status_code, 403)


class ArchitectureBoundaryTests(TestCase):
    """Verifica límites arquitectónicos estrictos."""

    def test_core_does_not_import_panel(self):
        import core.views
        import core.urls

        views_source = ""
        with open(core.views.__file__, "r", encoding="utf-8") as f:
            views_source = f.read()

        urls_source = ""
        with open(core.urls.__file__, "r", encoding="utf-8") as f:
            urls_source = f.read()

        self.assertNotIn("from panel", views_source)
        self.assertNotIn("import panel", views_source)
        self.assertNotIn("from panel", urls_source)
        self.assertNotIn("import panel", urls_source)

    def test_core_only_exposes_protected_public_transfer_surface(self):
        import core.views
        import core.urls

        with open(core.views.__file__, "r", encoding="utf-8") as f:
            views_source = f.read()

        with open(core.urls.__file__, "r", encoding="utf-8") as f:
            urls_source = f.read()

        self.assertNotIn("from panel", views_source)
        self.assertNotIn("import panel", views_source)
        self.assertNotIn("from panel", urls_source)
        self.assertNotIn("import panel", urls_source)
        self.assertIn("iniciar_transferencia", urls_source)
        self.assertIn("subir_comprobante", urls_source)


class SalesOnlineBookingServiceTests(CheckoutBaseTestCase):
    """Pruebas específicas del servicio create_online_booking con passengers_data."""

    def test_create_online_booking_with_passengers_data(self):
        p_data = [{
            "first_name": "Valeria",
            "last_name": "Sosa",
            "document_type": "DNI",
            "document_number": "31.999.888",
            "birth_date": "1985-12-01",
            "nationality": "Argentina",
            "gender": "Femenino",
        }]

        b = create_online_booking(
            email="valeria@correo.com",
            legs=[{
                "trip": self.trip_out,
                "origin_stop": self.ts_out_orig,
                "destination_stop": self.ts_out_dest,
                "seats": [self.seat_cama],
            }],
            passengers_data=p_data,
        )

        self.assertEqual(b.channel, BookingChannel.ONLINE)
        self.assertEqual(b.status, BookingStatus.HELD)
        self.assertEqual(b.passengers.count(), 1)
        p = b.passengers.first()
        self.assertEqual(p.first_name, "Valeria")
        self.assertEqual(p.last_name, "Sosa")
        self.assertEqual(p.normalized_document, "31999888")
        self.assertEqual(p.birth_date, date(1985, 12, 1))
        self.assertEqual(p.gender, "Femenino")

    def test_create_online_booking_backward_compatible_without_passengers_data(self):
        b = create_online_booking(
            email="anonimo@correo.com",
            legs=[{
                "trip": self.trip_out,
                "origin_stop": self.ts_out_orig,
                "destination_stop": self.ts_out_dest,
                "seats": [self.seat_cama],
            }],
        )
        self.assertEqual(b.channel, BookingChannel.ONLINE)
        self.assertEqual(b.status, BookingStatus.HELD)
        self.assertEqual(b.passengers.count(), 1)
        p = b.passengers.first()
        self.assertEqual(p.first_name, "")
        self.assertEqual(p.normalized_document, "")

    def test_create_online_booking_rejects_mismatched_passengers_count(self):
        p_data = [{
            "first_name": "Uno",
            "last_name": "Solo",
            "document_type": "DNI",
            "document_number": "31999888",
            "birth_date": "1985-12-01",
            "nationality": "Argentina",
        }]
        with self.assertRaises(ValidationError):
            create_online_booking(
                email="error@correo.com",
                legs=[{
                    "trip": self.trip_out,
                    "origin_stop": self.ts_out_orig,
                    "destination_stop": self.ts_out_dest,
                    "seats": [self.seat_cama, self.seat_semicama1],  # 2 butacas
                }],
                passengers_data=p_data,  # 1 pasajero -> debe fallar
            )
