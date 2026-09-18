from datetime import datetime, timedelta
from decimal import Decimal
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection, transaction, IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from sales.conf import (
    get_manual_hold_hours,
    get_max_passengers_per_booking,
    get_online_cutoff_minutes,
    get_online_hold_minutes,
)
from sales.exceptions import BookingExpiredError, InvalidBookingError, SeatUnavailableError
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)
from sales.services import (
    _is_seat_collision_integrity_error,
    confirm_booking,
    create_booking,
    create_manual_booking,
    create_online_booking,
    expire_booking,
    get_trip_availability,
    release_booking,
    release_expired_bookings,
)

User = get_user_model()
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class SalesBaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Grupos de usuarios
        cls.group_admin, _ = Group.objects.get_or_create(name="Administrador")
        cls.group_seller, _ = Group.objects.get_or_create(name="Vendedor")

        # Usuarios
        cls.admin_user = User.objects.create_user(username="admin_user", email="admin@scviajes.com")
        cls.admin_user.groups.add(cls.group_admin)

        cls.seller_user = User.objects.create_user(username="seller_user", email="seller@scviajes.com")
        cls.seller_user.groups.add(cls.group_seller)

        cls.superuser = User.objects.create_superuser(username="super_user", email="super@scviajes.com")

        cls.regular_user = User.objects.create_user(username="regular_user", email="regular@scviajes.com")
        cls.inactive_user = User.objects.create_user(username="inactive_user", email="inactive@scviajes.com", is_active=False)

        # Paradas
        cls.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", city="Córdoba", province="Córdoba")
        cls.stop_jma = Stop.objects.create(code="JMA", name="Jesús María", city="Jesús María", province="Córdoba")
        cls.stop_per = Stop.objects.create(code="PER", name="Perico", city="Perico", province="Jujuy")
        cls.stop_pal = Stop.objects.create(code="PAL", name="Palpalá", city="Palpalá", province="Jujuy")
        cls.stop_ssj = Stop.objects.create(code="SSJ", name="San Salvador de Jujuy", city="San Salvador de Jujuy", province="Jujuy")

        # Recorridos
        cls.route_cba_juj = Route.objects.create(code="CBA-JUJ", name="Córdoba a Jujuy")
        RouteStop.objects.create(route=cls.route_cba_juj, stop=cls.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route_cba_juj, stop=cls.stop_jma, sequence=2, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route_cba_juj, stop=cls.stop_per, sequence=3, allows_boarding=False, allows_alighting=True)
        RouteStop.objects.create(route=cls.route_cba_juj, stop=cls.stop_pal, sequence=4, allows_boarding=False, allows_alighting=True)
        RouteStop.objects.create(route=cls.route_cba_juj, stop=cls.stop_ssj, sequence=5, allows_boarding=False, allows_alighting=True)

        cls.route_juj_cba = Route.objects.create(code="JUJ-CBA", name="Jujuy a Córdoba")
        RouteStop.objects.create(route=cls.route_juj_cba, stop=cls.stop_ssj, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route_juj_cba, stop=cls.stop_pal, sequence=2, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route_juj_cba, stop=cls.stop_per, sequence=3, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route_juj_cba, stop=cls.stop_jma, sequence=4, allows_boarding=False, allows_alighting=True)
        RouteStop.objects.create(route=cls.route_juj_cba, stop=cls.stop_cba, sequence=5, allows_boarding=False, allows_alighting=True)

        # Colectivos
        cls.bus_1 = Bus.objects.create(code="BUS-01", display_name="Interno 1")
        cls.bus_2 = Bus.objects.create(code="BUS-02", display_name="Interno 2")

        # Butacas bus 1
        cls.seat_cama_1 = Seat.objects.create(bus=cls.bus_1, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)
        cls.seat_cama_2 = Seat.objects.create(bus=cls.bus_1, number=2, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=1, position_y=0)
        cls.seat_semi_3 = Seat.objects.create(bus=cls.bus_1, number=3, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
        cls.seat_semi_4 = Seat.objects.create(bus=cls.bus_1, number=4, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0)
        cls.seat_semi_5 = Seat.objects.create(bus=cls.bus_1, number=5, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=2, position_y=0)
        cls.seat_inactive = Seat.objects.create(bus=cls.bus_1, number=6, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=3, position_y=0, is_active=False)

        # Butacas bus 2
        cls.seat_bus2_1 = Seat.objects.create(bus=cls.bus_2, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)

        # Viajes (programados a 24 horas y 48 horas en el futuro)
        base_time = timezone.now().astimezone(AR_TZ) + timedelta(days=2)
        cls.base_time = base_time.replace(minute=0, second=0, microsecond=0)

        cls.trip_outbound = Trip.objects.create(
            route=cls.route_cba_juj,
            bus=cls.bus_1,
            departure_at=cls.base_time,
            status=Trip.Status.SCHEDULED,
        )
        cls.ts_out_cba = TripStop.objects.create(trip=cls.trip_outbound, stop=cls.stop_cba, sequence=1, scheduled_at=cls.base_time, allows_boarding=True, allows_alighting=False)
        cls.ts_out_jma = TripStop.objects.create(trip=cls.trip_outbound, stop=cls.stop_jma, sequence=2, scheduled_at=cls.base_time + timedelta(hours=1), allows_boarding=True, allows_alighting=False)
        cls.ts_out_per = TripStop.objects.create(trip=cls.trip_outbound, stop=cls.stop_per, sequence=3, scheduled_at=cls.base_time + timedelta(hours=10), allows_boarding=False, allows_alighting=True)
        cls.ts_out_pal = TripStop.objects.create(trip=cls.trip_outbound, stop=cls.stop_pal, sequence=4, scheduled_at=cls.base_time + timedelta(hours=11), allows_boarding=False, allows_alighting=True)
        cls.ts_out_ssj = TripStop.objects.create(trip=cls.trip_outbound, stop=cls.stop_ssj, sequence=5, scheduled_at=cls.base_time + timedelta(hours=12), allows_boarding=False, allows_alighting=True)

        # Tarifas viaje ida
        cls.fare_out_cba_ssj_cama = TripFare.objects.create(
            trip=cls.trip_outbound,
            origin_stop=cls.ts_out_cba,
            destination_stop=cls.ts_out_ssj,
            seat_category=SeatCategory.CAMA,
            amount=Decimal("12000.00"),
            currency="ARS",
            is_active=True,
        )
        cls.fare_out_cba_ssj_semi = TripFare.objects.create(
            trip=cls.trip_outbound,
            origin_stop=cls.ts_out_cba,
            destination_stop=cls.ts_out_ssj,
            seat_category=SeatCategory.SEMI_CAMA,
            amount=Decimal("9500.00"),
            currency="ARS",
            is_active=True,
        )
        cls.fare_out_jma_per_cama = TripFare.objects.create(
            trip=cls.trip_outbound,
            origin_stop=cls.ts_out_jma,
            destination_stop=cls.ts_out_per,
            seat_category=SeatCategory.CAMA,
            amount=Decimal("11000.00"),
            currency="ARS",
            is_active=True,
        )

        # Viaje vuelta (2 días después de ida)
        return_time = cls.base_time + timedelta(days=2)
        cls.trip_return = Trip.objects.create(
            route=cls.route_juj_cba,
            bus=cls.bus_1,
            departure_at=return_time,
            status=Trip.Status.SCHEDULED,
        )
        cls.ts_ret_ssj = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_ssj, sequence=1, scheduled_at=return_time, allows_boarding=True, allows_alighting=False)
        cls.ts_ret_pal = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_pal, sequence=2, scheduled_at=return_time + timedelta(hours=1), allows_boarding=True, allows_alighting=False)
        cls.ts_ret_per = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_per, sequence=3, scheduled_at=return_time + timedelta(hours=2), allows_boarding=True, allows_alighting=False)
        cls.ts_ret_jma = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_jma, sequence=4, scheduled_at=return_time + timedelta(hours=11), allows_boarding=False, allows_alighting=True)
        cls.ts_ret_cba = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_cba, sequence=5, scheduled_at=return_time + timedelta(hours=12), allows_boarding=False, allows_alighting=True)

        # Tarifas viaje vuelta
        cls.fare_ret_ssj_cba_cama = TripFare.objects.create(
            trip=cls.trip_return,
            origin_stop=cls.ts_ret_ssj,
            destination_stop=cls.ts_ret_cba,
            seat_category=SeatCategory.CAMA,
            amount=Decimal("12000.00"),
            currency="ARS",
            is_active=True,
        )
        cls.fare_ret_ssj_cba_semi = TripFare.objects.create(
            trip=cls.trip_return,
            origin_stop=cls.ts_ret_ssj,
            destination_stop=cls.ts_ret_cba,
            seat_category=SeatCategory.SEMI_CAMA,
            amount=Decimal("9500.00"),
            currency="ARS",
            is_active=True,
        )


class SalesBookingCreationTests(SalesBaseTestCase):
    def test_one_way_online_booking_success(self):
        now = timezone.now()
        booking = create_online_booking(
            email="pasajero@correo.com",
            phone="3511234567",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1, self.seat_semi_3],
            }],
            now=now,
        )
        self.assertIsNotNone(booking.public_id)
        self.assertEqual(booking.channel, BookingChannel.ONLINE)
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertIsNone(booking.seller)
        self.assertEqual(booking.email, "pasajero@correo.com")
        self.assertEqual(booking.phone, "3511234567")
        self.assertEqual(booking.expires_at, now + timedelta(minutes=15))
        self.assertIsNone(booking.confirmed_at)

        # Tramos
        self.assertEqual(booking.legs.count(), 1)
        leg = booking.legs.first()
        self.assertEqual(leg.sequence, 1)
        self.assertEqual(leg.trip, self.trip_outbound)
        self.assertEqual(leg.origin_stop, self.ts_out_cba)
        self.assertEqual(leg.destination_stop, self.ts_out_ssj)
        self.assertEqual(leg.origin_stop_name, "Córdoba Capital")
        self.assertEqual(leg.destination_stop_name, "San Salvador de Jujuy")
        self.assertEqual(leg.departure_at, self.ts_out_cba.scheduled_at)
        self.assertEqual(leg.arrival_at, self.ts_out_ssj.scheduled_at)

        # Pasajeros
        self.assertEqual(booking.passengers.count(), 2)
        p1, p2 = booking.passengers.order_by("position")
        self.assertEqual(p1.position, 1)
        self.assertEqual(p2.position, 2)

        # Asignaciones de butaca y snapshots
        assignments = list(leg.seat_assignments.order_by("passenger__position"))
        self.assertEqual(len(assignments), 2)

        a1, a2 = assignments
        self.assertEqual(a1.passenger, p1)
        self.assertEqual(a1.seat, self.seat_cama_1)
        self.assertEqual(a1.status, AssignmentStatus.HELD)
        self.assertEqual(a1.seat_number, 1)
        self.assertEqual(a1.category, SeatCategory.CAMA)
        self.assertEqual(a1.price, Decimal("12000.00"))
        self.assertEqual(a1.currency, "ARS")

        self.assertEqual(a2.passenger, p2)
        self.assertEqual(a2.seat, self.seat_semi_3)
        self.assertEqual(a2.status, AssignmentStatus.HELD)
        self.assertEqual(a2.seat_number, 3)
        self.assertEqual(a2.category, SeatCategory.SEMI_CAMA)
        self.assertEqual(a2.price, Decimal("9500.00"))
        self.assertEqual(a2.currency, "ARS")

    def test_round_trip_booking_success(self):
        now = timezone.now()
        booking = create_online_booking(
            email="turista@correo.com",
            legs=[
                {
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1, self.seat_semi_3],
                },
                {
                    "trip": self.trip_return,
                    "origin_stop": self.ts_ret_ssj,
                    "destination_stop": self.ts_ret_cba,
                    "seats": [self.seat_cama_2, self.seat_semi_4],
                },
            ],
            now=now,
        )
        self.assertEqual(booking.legs.count(), 2)
        leg1 = booking.legs.get(sequence=1)
        leg2 = booking.legs.get(sequence=2)
        self.assertEqual(leg1.trip, self.trip_outbound)
        self.assertEqual(leg2.trip, self.trip_return)
        self.assertEqual(booking.passengers.count(), 2)
        self.assertEqual(leg1.seat_assignments.count(), 2)
        self.assertEqual(leg2.seat_assignments.count(), 2)

    def test_atomic_rollback_on_second_leg_failure(self):
        """Si el segundo tramo falla, ningún dato debe quedar persistido (sin restos)."""
        now = timezone.now()
        # Ocupamos la butaca 2 del viaje de vuelta
        create_online_booking(
            email="otro@correo.com",
            legs=[{
                "trip": self.trip_return,
                "origin_stop": self.ts_ret_ssj,
                "destination_stop": self.ts_ret_cba,
                "seats": [self.seat_cama_2],
            }],
            now=now,
        )

        initial_booking_count = Booking.objects.count()
        initial_leg_count = BookingLeg.objects.count()
        initial_passenger_count = BookingPassenger.objects.count()
        initial_assignment_count = SeatAssignment.objects.count()

        # Intentamos ida y vuelta solicitando seat_cama_1 para ida y seat_cama_2 para vuelta (que ya está tomada)
        with self.assertRaises(SeatUnavailableError):
            create_online_booking(
                email="fallara@correo.com",
                legs=[
                    {
                        "trip": self.trip_outbound,
                        "origin_stop": self.ts_out_cba,
                        "destination_stop": self.ts_out_ssj,
                        "seats": [self.seat_cama_1],
                    },
                    {
                        "trip": self.trip_return,
                        "origin_stop": self.ts_ret_ssj,
                        "destination_stop": self.ts_ret_cba,
                        "seats": [self.seat_cama_2],  # Conflicto aquí
                    },
                ],
                now=now,
            )

        # Verificamos que no haya quedado ningún resto en la base
        self.assertEqual(Booking.objects.count(), initial_booking_count)
        self.assertEqual(BookingLeg.objects.count(), initial_leg_count)
        self.assertEqual(BookingPassenger.objects.count(), initial_passenger_count)
        self.assertEqual(SeatAssignment.objects.count(), initial_assignment_count)

        # La butaca 1 del viaje de ida sigue disponible
        self.assertFalse(
            SeatAssignment.objects.filter(trip=self.trip_outbound, seat=self.seat_cama_1).exists()
        )

    def test_max_passengers_configurable_and_validated_on_server(self):
        # Configuración por defecto es 4
        self.assertEqual(get_max_passengers_per_booking(), 4)
        seats_4 = [self.seat_cama_1, self.seat_cama_2, self.seat_semi_3, self.seat_semi_4]

        booking = create_online_booking(
            email="cuatro@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": seats_4,
            }],
        )
        self.assertEqual(booking.passengers.count(), 4)

        # 5 butacas debe fallar por superar el máximo de 4
        seats_5 = seats_4 + [self.seat_semi_5]
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="cinco@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": seats_5,
                }],
            )
        self.assertIn("límite máximo de pasajeros", str(ctx.exception))

        # Modificación configurable mediante settings a 2
        with override_settings(SALES_MAX_PASSENGERS_PER_BOOKING=2):
            self.assertEqual(get_max_passengers_per_booking(), 2)
            with self.assertRaises(ValidationError) as ctx:
                create_online_booking(
                    email="tres@correo.com",
                    legs=[{
                        "trip": self.trip_outbound,
                        "origin_stop": self.ts_out_cba,
                        "destination_stop": self.ts_out_ssj,
                        "seats": [self.seat_cama_1, self.seat_semi_3, self.seat_semi_4],
                    }],
                )
            self.assertIn("límite máximo de pasajeros por reserva es de 2", str(ctx.exception))

    def test_empty_or_zero_passengers_rejected(self):
        with self.assertRaises(ValidationError):
            create_online_booking(
                email="cero@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [],
                }],
            )

    def test_passenger_count_mismatch_across_legs_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="mismatch@correo.com",
                legs=[
                    {
                        "trip": self.trip_outbound,
                        "origin_stop": self.ts_out_cba,
                        "destination_stop": self.ts_out_ssj,
                        "seats": [self.seat_cama_1, self.seat_semi_3],
                    },
                    {
                        "trip": self.trip_return,
                        "origin_stop": self.ts_ret_ssj,
                        "destination_stop": self.ts_ret_cba,
                        "seats": [self.seat_cama_2],  # Solo 1 butaca vs 2 en ida
                    },
                ],
            )
        self.assertIn("misma cantidad de butacas", str(ctx.exception))

    def test_duplicate_seat_in_same_leg_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="duplicada@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1, self.seat_cama_1],  # Misma butaca repetida
                }],
            )
        self.assertIn("No se puede asignar la misma butaca más de una vez", str(ctx.exception))

    def test_more_than_two_legs_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="tres_tramos@correo.com",
                legs=[
                    {"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]},
                    {"trip": self.trip_return, "origin_stop": self.ts_ret_ssj, "destination_stop": self.ts_ret_cba, "seats": [self.seat_cama_1]},
                    {"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]},
                ],
            )
        self.assertIn("solo ida o ida y vuelta", str(ctx.exception))

    def test_zero_legs_rejected(self):
        with self.assertRaises(ValidationError):
            create_online_booking(email="sin_tramos@correo.com", legs=[])

    def test_return_leg_departure_before_outbound_arrival_rejected(self):
        # Invertimos las fechas de retorno para que salga antes de la llegada de ida
        invalid_return_time = self.ts_out_cba.scheduled_at - timedelta(hours=2)
        trip_bad_return = Trip.objects.create(
            route=self.route_juj_cba,
            bus=self.bus_1,
            departure_at=invalid_return_time,
            status=Trip.Status.SCHEDULED,
        )
        ts_ret_bad_orig = TripStop.objects.create(trip=trip_bad_return, stop=self.stop_ssj, sequence=1, scheduled_at=invalid_return_time, allows_boarding=True, allows_alighting=False)
        ts_ret_bad_dest = TripStop.objects.create(trip=trip_bad_return, stop=self.stop_cba, sequence=2, scheduled_at=invalid_return_time + timedelta(hours=5), allows_boarding=False, allows_alighting=True)

        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="cronologia@correo.com",
                legs=[
                    {"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]},
                    {"trip": trip_bad_return, "origin_stop": ts_ret_bad_orig, "destination_stop": ts_ret_bad_dest, "seats": [self.seat_cama_2]},
                ],
            )
        self.assertIn("vuelta debe salir después de la llegada", str(ctx.exception))

    def test_round_trip_two_legs_on_same_trip_rejected(self):
        """Una reserva de dos tramos no puede utilizar el mismo viaje para ambos tramos."""
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="mismo_viaje@correo.com",
                legs=[
                    {"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]},
                    {"trip": self.trip_outbound, "origin_stop": self.ts_out_ssj, "destination_stop": self.ts_out_cba, "seats": [self.seat_cama_2]},
                ],
            )
        self.assertIn("viajes distintos", str(ctx.exception))

    def test_round_trip_legs_not_inverting_origin_and_destination_rejected(self):
        """El viaje de vuelta debe invertir estrictamente las paradas de origen y destino de la ida."""
        # Vuelta con parada de origen errónea (PAL en lugar de SSJ)
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="no_invierte_origen@correo.com",
                legs=[
                    {"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]},
                    {"trip": self.trip_return, "origin_stop": self.ts_ret_pal, "destination_stop": self.ts_ret_cba, "seats": [self.seat_cama_2]},
                ],
            )
        self.assertIn("invertir las paradas de origen y destino", str(ctx.exception))

        # Vuelta con parada de destino errónea (JMA en lugar de CBA)
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="no_invierte_destino@correo.com",
                legs=[
                    {"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]},
                    {"trip": self.trip_return, "origin_stop": self.ts_ret_ssj, "destination_stop": self.ts_ret_jma, "seats": [self.seat_cama_2]},
                ],
            )
        self.assertIn("invertir las paradas de origen y destino", str(ctx.exception))

    def test_return_leg_departure_equal_to_outbound_arrival_rejected(self):
        """La salida del viaje de vuelta debe ser estrictamente posterior (no igual) a la llegada de ida."""
        equal_time = self.ts_out_ssj.scheduled_at
        trip_equal_return = Trip.objects.create(
            route=self.route_juj_cba,
            bus=self.bus_1,
            departure_at=equal_time,
            status=Trip.Status.SCHEDULED,
        )
        ts_ret_eq_orig = TripStop.objects.create(trip=trip_equal_return, stop=self.stop_ssj, sequence=1, scheduled_at=equal_time, allows_boarding=True, allows_alighting=False)
        ts_ret_eq_dest = TripStop.objects.create(trip=trip_equal_return, stop=self.stop_cba, sequence=2, scheduled_at=equal_time + timedelta(hours=5), allows_boarding=False, allows_alighting=True)

        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="salida_igual@correo.com",
                legs=[
                    {"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]},
                    {"trip": trip_equal_return, "origin_stop": ts_ret_eq_orig, "destination_stop": ts_ret_eq_dest, "seats": [self.seat_cama_2]},
                ],
            )
        self.assertIn("vuelta debe salir después de la llegada", str(ctx.exception))


class SalesStopsAndRouteValidationTests(SalesBaseTestCase):
    def test_stop_from_another_trip_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="otra_parada@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_ret_ssj,  # Parada del viaje de vuelta, no de ida!
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("pertenecer al viaje", str(ctx.exception))

    def test_inverted_stops_sequence_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="orden_invertido@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_ssj,  # Secuencia 5
                    "destination_stop": self.ts_out_cba,  # Secuencia 1
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("posterior a la de subida", str(ctx.exception))

    def test_origin_does_not_allow_boarding_rejected(self):
        # PER en CBA-JUJ no permite subir (allows_boarding=False)
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="no_sube@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_per,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("no permite subir", str(ctx.exception))

    def test_destination_does_not_allow_alighting_rejected(self):
        # JMA en CBA-JUJ no permite bajar (allows_alighting=False)
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="no_baja@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_jma,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("no permite bajar", str(ctx.exception))

    def test_route_stop_change_after_trip_creation_does_not_invalidate_booking(self):
        """El snapshot TripStop es la autoridad para orden y permisos; cambios posteriores de RouteStop no invalidan reserva."""
        # Modificamos el RouteStop base para que no permita subir (sino sólo bajar, cumpliendo con ops_route_stop_permission)
        RouteStop.objects.filter(route=self.trip_outbound.route, stop=self.stop_cba).update(allows_boarding=False, allows_alighting=True)
        self.assertFalse(self.trip_outbound.route.allows_journey(self.stop_cba, self.stop_ssj))

        # La reserva debe seguir siendo válida porque los TripStops del viaje programado son la autoridad
        booking = create_online_booking(
            email="snapshot_stop@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )
        self.assertIsNotNone(booking.pk)

        # La consulta de disponibilidad también debe utilizar TripStops sin fallar
        avail = get_trip_availability(self.trip_outbound, origin_stop=self.ts_out_cba, destination_stop=self.ts_out_ssj)
        self.assertIn(self.seat_cama_2.pk, [s.pk for s in avail["available_seats"]])

    def test_stale_tripstop_instance_reloaded_from_database(self):
        """create_booking no confía en instancias en memoria de TripStop y recarga valores vigentes de la base."""
        # Modificamos el horario de salida en la base de datos
        new_time = self.base_time + timedelta(hours=2)
        TripStop.objects.filter(pk=self.ts_out_cba.pk).update(scheduled_at=new_time)

        # El objeto self.ts_out_cba en memoria tiene el horario anterior, pero la reserva debe guardar el valor de base
        booking = create_online_booking(
            email="stale_ts@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )
        leg = booking.legs.first()
        self.assertEqual(leg.departure_at, new_time)

        # Si en la base se desactiva la subida en la parada, una instancia en memoria desactualizada no elude la validación
        TripStop.objects.filter(pk=self.ts_out_cba.pk).update(allows_boarding=False)
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="stale_no_sube@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_2],
                }],
            )
        self.assertIn("no permite subir pasajeros", str(ctx.exception))

    def test_get_trip_availability_rejects_partial_origin_stop(self):
        """get_trip_availability rechaza una consulta con solo parada de origen indicada."""
        with self.assertRaises(ValidationError) as ctx:
            get_trip_availability(self.trip_outbound, origin_stop=self.ts_out_cba, destination_stop=None)
        self.assertIn("tanto la parada de subida como la de bajada", str(ctx.exception))

    def test_get_trip_availability_rejects_partial_destination_stop(self):
        """get_trip_availability rechaza una consulta con solo parada de destino indicada."""
        with self.assertRaises(ValidationError) as ctx:
            get_trip_availability(self.trip_outbound, origin_stop=None, destination_stop=self.ts_out_ssj)
        self.assertIn("tanto la parada de subida como la de bajada", str(ctx.exception))


class SalesSeatsAndFaresValidationTests(SalesBaseTestCase):
    def test_seat_from_another_bus_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="otro_bus@correo.com",
                legs=[{
                    "trip": self.trip_outbound,  # Colectivo BUS-01
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_bus2_1],  # Butaca de BUS-02
                }],
            )
        self.assertIn("no pertenece al colectivo", str(ctx.exception))

    def test_inactive_seat_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="butaca_inactiva@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_inactive],
                }],
            )
        self.assertIn("no está activa", str(ctx.exception))

    def test_missing_active_fare_rejected(self):
        # JMA -> SSJ no tiene tarifa cargada
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="sin_tarifa@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_jma,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("No existe una tarifa activa", str(ctx.exception))

    def test_inactive_fare_rejected(self):
        self.fare_out_cba_ssj_cama.is_active = False
        self.fare_out_cba_ssj_cama.save()

        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="tarifa_inactiva@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("No existe una tarifa activa", str(ctx.exception))

    def test_historical_snapshot_preserves_fare_when_fare_changes_later(self):
        booking = create_online_booking(
            email="snapshot@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(assignment.price, Decimal("12000.00"))
        self.assertIsInstance(assignment.price, Decimal)

        # Modificamos la tarifa de operaciones
        self.fare_out_cba_ssj_cama.amount = Decimal("15000.00")
        self.fare_out_cba_ssj_cama.save()

        # El snapshot histórico en sales no debe alterarse
        assignment.refresh_from_db()
        self.assertEqual(assignment.price, Decimal("12000.00"))

    def test_stale_seat_instance_deactivated_rejected(self):
        """Si una butaca fue desactivada en base de datos (e.g. por el panel), una instancia en memoria activa es rechazada."""
        Seat.objects.filter(pk=self.seat_cama_1.pk).update(is_active=False)
        # self.seat_cama_1 en memoria sigue teniendo is_active=True
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="stale_seat@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("no está activa", str(ctx.exception))

    def test_stale_seat_instance_category_changed_uses_fresh_category_and_fare(self):
        """Si la categoría de la butaca cambió en base de datos, create_booking recarga la categoría y tarifa vigentes."""
        # Cambiamos la butaca 1 de CAMA a SEMI_CAMA en la base
        Seat.objects.filter(pk=self.seat_cama_1.pk).update(category=SeatCategory.SEMI_CAMA)
        # self.seat_cama_1 en memoria tiene category=CAMA, pero se debe persistir SEMI_CAMA y tarifa SEMI_CAMA (9500)
        booking = create_online_booking(
            email="stale_cat@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(assignment.category, SeatCategory.SEMI_CAMA)
        self.assertEqual(assignment.price, Decimal("9500.00"))


class SalesCutoffAndDeadlinesTests(SalesBaseTestCase):
    def test_online_cutoff_exact_boundary(self):
        """El corte online ocurre a la hora exacta (now >= cutoff_time).

        1 segundo antes es permitido; en el segundo exacto y después es rechazado.
        """
        departure_time = self.ts_out_cba.scheduled_at
        cutoff_time = departure_time - timedelta(minutes=60)

        # 1 segundo antes del horario de corte: permitido
        booking = create_online_booking(
            email="a_tiempo@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
            now=cutoff_time - timedelta(seconds=1),
        )
        self.assertIsNotNone(booking.pk)

        # En el horario exacto del corte (now == cutoff_time): rechazado
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="borde_exacto@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_2],
                }],
                now=cutoff_time,
            )
        self.assertIn("cierra 60 minutos antes del horario de subida", str(ctx.exception))

        # 1 segundo después del horario de corte: rechazado
        with self.assertRaises(ValidationError) as ctx:
            create_online_booking(
                email="tarde@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_2],
                }],
                now=cutoff_time + timedelta(seconds=1),
            )
        self.assertIn("cierra 60 minutos antes del horario de subida", str(ctx.exception))

    def test_online_booking_trip_statuses(self):
        """Las reservas online están permitidas en SCHEDULED y BOARDING (antes del corte),

        y rechazadas en STARTED, COMPLETED y CANCELLED.
        """
        # BOARDING antes del horario de corte: permitido
        self.trip_outbound.status = Trip.Status.BOARDING
        self.trip_outbound.save()
        now_valid = self.ts_out_cba.scheduled_at - timedelta(hours=3)
        b_boarding = create_online_booking(
            email="online_boarding@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now_valid,
        )
        self.assertIsNotNone(b_boarding.pk)

        # Prohibido en STARTED, COMPLETED, CANCELLED
        for invalid_status in [Trip.Status.STARTED, Trip.Status.COMPLETED, Trip.Status.CANCELLED]:
            self.trip_outbound.status = invalid_status
            self.trip_outbound.save()
            with self.subTest(status=invalid_status):
                with self.assertRaises(ValidationError) as ctx:
                    create_online_booking(
                        email="online_invalido@correo.com",
                        legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_2]}],
                        now=now_valid,
                    )
                self.assertIn("no están permitidas en viajes iniciados, finalizados o cancelados", str(ctx.exception))

    def test_configurable_online_cutoff(self):
        departure_time = self.ts_out_cba.scheduled_at
        time_80m_before = departure_time - timedelta(minutes=80)

        with override_settings(SALES_ONLINE_CUTOFF_MINUTES=90):
            # Con 90 minutos de corte, 80 minutos antes ya está cerrado
            with self.assertRaises(ValidationError) as ctx:
                create_online_booking(
                    email="corte_configurado@correo.com",
                    legs=[{
                        "trip": self.trip_outbound,
                        "origin_stop": self.ts_out_cba,
                        "destination_stop": self.ts_out_ssj,
                        "seats": [self.seat_cama_2],
                    }],
                    now=time_80m_before,
                )
            self.assertIn("cierra 90 minutos antes", str(ctx.exception))

    def test_online_hold_duration_15_min_and_configurable(self):
        now = timezone.now()
        b1 = create_online_booking(
            email="hold15@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
            now=now,
        )
        self.assertEqual(b1.expires_at, now + timedelta(minutes=15))

        with override_settings(SALES_ONLINE_HOLD_MINUTES=30):
            b2 = create_online_booking(
                email="hold30@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_2],
                }],
                now=now,
            )
            self.assertEqual(b2.expires_at, now + timedelta(minutes=30))

    def test_manual_hold_duration_24_hours_and_configurable(self):
        now = timezone.now()
        b1 = create_manual_booking(
            seller=self.seller_user,
            email="manual24@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
            now=now,
        )
        self.assertEqual(b1.expires_at, now + timedelta(hours=24))

        with override_settings(SALES_MANUAL_HOLD_HOURS=48):
            b2 = create_manual_booking(
                seller=self.seller_user,
                email="manual48@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_2],
                }],
                now=now,
            )
            self.assertEqual(b2.expires_at, now + timedelta(hours=48))

    def test_create_booking_rereads_clock_after_locks_detects_cutoff(self):
        """Si create_booking espera locks y pasa el corte online, la relectura de timezone.now detecta el cutoff y rechaza."""
        departure_time = self.ts_out_cba.scheduled_at
        cutoff_time = departure_time - timedelta(minutes=60)
        time_before_cutoff = cutoff_time - timedelta(seconds=15)
        time_after_cutoff = cutoff_time + timedelta(seconds=10)

        call_times = [time_before_cutoff, time_after_cutoff]

        def mock_now():
            if call_times:
                return call_times.pop(0)
            return time_after_cutoff

        with patch("sales.services.timezone.now", side_effect=mock_now):
            with self.assertRaises(ValidationError) as ctx:
                create_online_booking(
                    email="reread_cutoff@correo.com",
                    legs=[{
                        "trip": self.trip_outbound,
                        "origin_stop": self.ts_out_cba,
                        "destination_stop": self.ts_out_ssj,
                        "seats": [self.seat_cama_1],
                    }],
                    now=None,
                )
            self.assertIn("cierra 60 minutos antes", str(ctx.exception))

    def test_create_booking_uses_effective_clock_for_expires_at(self):
        """Verifica que expires_at se calcule con el tiempo efectivo posterior al bloqueo de filas."""
        t0 = timezone.now()
        t1_after_lock = t0 + timedelta(seconds=30)
        call_times = [t0, t1_after_lock]

        def mock_now():
            if call_times:
                return call_times.pop(0)
            return t1_after_lock

        with patch("sales.services.timezone.now", side_effect=mock_now):
            booking = create_online_booking(
                email="effective_exp@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
                now=None,
            )
            expected_expires = t1_after_lock + timedelta(minutes=15)
            self.assertEqual(booking.expires_at, expected_expires)

    def test_create_booking_with_explicit_now_preserves_controlled_semantics(self):
        """Si now es inyectado explícitamente en tests, no se sobrescribe con timezone.now()."""
        controlled_now = timezone.now() - timedelta(hours=2)
        booking = create_online_booking(
            email="controlled_now@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
            now=controlled_now,
        )
        self.assertEqual(booking.expires_at, controlled_now + timedelta(minutes=15))


class SalesSellerValidationTests(SalesBaseTestCase):
    def test_manual_booking_by_seller_group(self):
        booking = create_manual_booking(
            seller=self.seller_user,
            email="cliente_vendedor@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )
        self.assertEqual(booking.channel, BookingChannel.MANUAL)
        self.assertEqual(booking.seller, self.seller_user)

    def test_manual_booking_by_admin_group(self):
        booking = create_manual_booking(
            seller=self.admin_user,
            email="cliente_admin@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )
        self.assertEqual(booking.seller, self.admin_user)

    def test_manual_booking_by_superuser(self):
        booking = create_manual_booking(
            seller=self.superuser,
            email="cliente_super@correo.com",
            legs=[{
                "trip": self.trip_outbound,
                "origin_stop": self.ts_out_cba,
                "destination_stop": self.ts_out_ssj,
                "seats": [self.seat_cama_1],
            }],
        )
        self.assertEqual(booking.seller, self.superuser)

    def test_manual_booking_by_inactive_user_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_manual_booking(
                seller=self.inactive_user,
                email="cliente@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("usuario activo", str(ctx.exception))

    def test_manual_booking_by_unauthorized_user_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_manual_booking(
                seller=self.regular_user,
                email="cliente@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("grupo Vendedor o Administrador", str(ctx.exception))

    def test_manual_booking_without_seller_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_manual_booking(
                seller=None,
                email="cliente@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("requieren un vendedor", str(ctx.exception))

    def test_online_booking_with_seller_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_booking(
                channel=BookingChannel.ONLINE,
                seller=self.seller_user,
                email="cliente@correo.com",
                legs=[{
                    "trip": self.trip_outbound,
                    "origin_stop": self.ts_out_cba,
                    "destination_stop": self.ts_out_ssj,
                    "seats": [self.seat_cama_1],
                }],
            )
        self.assertIn("no llevan vendedor", str(ctx.exception))

    def test_manual_booking_trip_statuses(self):
        # Permitido en SCHEDULED
        self.trip_outbound.status = Trip.Status.SCHEDULED
        self.trip_outbound.save()
        b_sched = create_manual_booking(
            seller=self.seller_user,
            email="sched@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
        )
        self.assertIsNotNone(b_sched.pk)

        # Permitido en BOARDING
        self.trip_outbound.status = Trip.Status.BOARDING
        self.trip_outbound.save()
        b_board = create_manual_booking(
            seller=self.seller_user,
            email="board@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_2]}],
        )
        self.assertIsNotNone(b_board.pk)

        # Prohibido en STARTED, COMPLETED, CANCELLED
        for invalid_status in [Trip.Status.STARTED, Trip.Status.COMPLETED, Trip.Status.CANCELLED]:
            self.trip_outbound.status = invalid_status
            self.trip_outbound.save()
            with self.subTest(status=invalid_status):
                with self.assertRaises(ValidationError) as ctx:
                    create_manual_booking(
                        seller=self.seller_user,
                        email="invalido@correo.com",
                        legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_semi_3]}],
                    )
                self.assertIn("no están permitidas en viajes iniciados, finalizados o cancelados", str(ctx.exception))


class SalesConfirmationReleaseAndExpirationTests(SalesBaseTestCase):
    def test_confirm_booking_success(self):
        now = timezone.now()
        booking = create_online_booking(
            email="confirmar@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertEqual(booking.legs.first().seat_assignments.first().status, AssignmentStatus.HELD)

        confirm_time = now + timedelta(minutes=5)
        confirmed = confirm_booking(booking, now=confirm_time)
        self.assertEqual(confirmed.status, BookingStatus.CONFIRMED)
        self.assertEqual(confirmed.confirmed_at, confirm_time)

        assignment = confirmed.legs.first().seat_assignments.first()
        self.assertEqual(assignment.status, AssignmentStatus.CONFIRMED)

    def test_confirm_already_confirmed_is_idempotent(self):
        booking = create_online_booking(
            email="idempotente@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
        )
        confirm_booking(booking)
        # Segunda llamada devuelve la misma sin error
        confirmed_again = confirm_booking(booking)
        self.assertEqual(confirmed_again.status, BookingStatus.CONFIRMED)

    def test_confirm_expired_booking_rejected(self):
        now = timezone.now()
        booking = create_online_booking(
            email="expirada@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        # Intentamos confirmar 20 minutos después (vence a los 15m)
        time_expired = now + timedelta(minutes=20)
        with self.assertRaises(BookingExpiredError):
            confirm_booking(booking, now=time_expired)

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(assignment.status, AssignmentStatus.RELEASED)

    def test_confirm_released_booking_rejected(self):
        booking = create_online_booking(
            email="liberada@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
        )
        release_booking(booking)
        with self.assertRaises(InvalidBookingError):
            confirm_booking(booking)

    def test_release_booking_success_and_reutilization(self):
        booking = create_online_booking(
            email="reutilizar@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
        )
        release_booking(booking)
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.RELEASED)
        old_assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(old_assignment.status, AssignmentStatus.RELEASED)

        # Ahora la butaca 1 puede ser tomada por una nueva reserva
        new_booking = create_online_booking(
            email="nuevo_pasajero@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
        )
        self.assertEqual(new_booking.status, BookingStatus.HELD)
        new_assignment = new_booking.legs.first().seat_assignments.first()
        self.assertEqual(new_assignment.status, AssignmentStatus.HELD)

        # Ambos registros de asignación coexisten en la base: el liberado para auditoría y el nuevo retenido
        all_assignments = SeatAssignment.objects.filter(trip=self.trip_outbound, seat=self.seat_cama_1)
        self.assertEqual(all_assignments.count(), 2)
        self.assertTrue(all_assignments.filter(status=AssignmentStatus.RELEASED).exists())
        self.assertTrue(all_assignments.filter(status=AssignmentStatus.HELD).exists())

    def test_opportunistic_release_before_availability_query(self):
        now = timezone.now()
        # Creamos reserva en T0
        booking = create_online_booking(
            email="oportunista@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        # A los 10 minutos la butaca no está disponible
        avail_10m = get_trip_availability(self.trip_outbound, now=now + timedelta(minutes=10))
        avail_pks_10m = [s.pk for s in avail_10m["available_seats"]]
        self.assertNotIn(self.seat_cama_1.pk, avail_pks_10m)

        # A los 16 minutos (ya vencida), consultar disponibilidad ejecuta release_expired_bookings oportunamente
        avail_16m = get_trip_availability(self.trip_outbound, now=now + timedelta(minutes=16))
        avail_pks_16m = [s.pk for s in avail_16m["available_seats"]]
        self.assertIn(self.seat_cama_1.pk, avail_pks_16m)

        # La reserva y la butaca quedaron actualizadas
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        self.assertEqual(booking.legs.first().seat_assignments.first().status, AssignmentStatus.RELEASED)

    def test_opportunistic_release_before_booking_creation(self):
        now = timezone.now()
        create_online_booking(
            email="vencera@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        # Intentamos reservar la misma butaca 20 minutos después: la creación oportuna libera la vencida y permite la nueva
        new_booking = create_online_booking(
            email="nueva@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now + timedelta(minutes=20),
        )
        self.assertEqual(new_booking.status, BookingStatus.HELD)

    def test_release_confirmed_booking_rejected_and_remains_confirmed(self):
        """release_booking no debe liberar una reserva CONFIRMED para evitar revender pasajes confirmados sin política aprobada."""
        booking = create_online_booking(
            email="confirmada_no_libera@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
        )
        booking = confirm_booking(booking)
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

        with self.assertRaises(InvalidBookingError) as ctx:
            release_booking(booking)
        self.assertIn("política de cancelación", str(ctx.exception))

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(assignment.status, AssignmentStatus.CONFIRMED)

    def test_release_held_booking_idempotent(self):
        """Liberar una reserva HELD es idempotente."""
        booking = create_online_booking(
            email="liberar_idempotente@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
        )
        b1 = release_booking(booking)
        self.assertEqual(b1.status, BookingStatus.RELEASED)
        b2 = release_booking(booking)
        self.assertEqual(b2.status, BookingStatus.RELEASED)

    def test_opportunistic_expiration_scoped_to_affected_trips(self):
        """La expiración oportunista se acota a los viajes involucrados, sin bloquear ni alterar reservas de otros viajes."""
        now = timezone.now()
        # Reserva en Viaje 1 (outbound)
        b1 = create_online_booking(
            email="viaje1@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        # Reserva en Viaje 2 (return)
        b2 = create_online_booking(
            email="viaje2@correo.com",
            legs=[{"trip": self.trip_return, "origin_stop": self.ts_ret_ssj, "destination_stop": self.ts_ret_cba, "seats": [self.seat_cama_2]}],
            now=now,
        )

        time_expired = now + timedelta(minutes=20)
        # Consultamos disponibilidad de Viaje 1
        get_trip_availability(self.trip_outbound, now=time_expired)

        b1.refresh_from_db()
        b2.refresh_from_db()

        # Viaje 1 fue expirado oportunamente
        self.assertEqual(b1.status, BookingStatus.EXPIRED)
        # Viaje 2 NO fue tocado ni alterado porque la expiración se acotó a Viaje 1
        self.assertEqual(b2.status, BookingStatus.HELD)

        # Ahora creamos una reserva en Viaje 2: la expiración acotada a Viaje 2 procesa b2
        create_online_booking(
            email="viaje2_nuevo@correo.com",
            legs=[{"trip": self.trip_return, "origin_stop": self.ts_ret_ssj, "destination_stop": self.ts_ret_cba, "seats": [self.seat_cama_2]}],
            now=time_expired,
        )
        b2.refresh_from_db()
        self.assertEqual(b2.status, BookingStatus.EXPIRED)

    def test_release_expired_bookings_scope_by_trip_ids_and_no_distinct(self):
        """release_expired_bookings con trip_ids acota la liberación a los viajes provistos y no usa DISTINCT en el query final."""
        now = timezone.now()
        # Reserva 1 en Viaje Ida
        b1 = create_online_booking(
            email="exp_outbound@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        # Reserva 2 en Viaje Vuelta
        b2 = create_online_booking(
            email="exp_return@correo.com",
            legs=[{"trip": self.trip_return, "origin_stop": self.ts_ret_ssj, "destination_stop": self.ts_ret_cba, "seats": [self.seat_cama_2]}],
            now=now,
        )
        # Reserva 3 en Viaje Ida con expiración más lejana
        b3 = create_online_booking(
            email="valid_outbound@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_semi_3]}],
            now=now,
        )
        b3.expires_at = now + timedelta(minutes=60)
        b3.save(update_fields=["expires_at"])

        eval_time = now + timedelta(minutes=20)

        # Verificar que la consulta SQL construida sobre Booking no contenga DISTINCT ni GROUP BY
        from sales.models import BookingLeg
        qs = Booking.objects.select_for_update().filter(status=BookingStatus.HELD, expires_at__lte=eval_time).filter(
            pk__in=BookingLeg.objects.filter(trip_id__in=[self.trip_outbound.pk]).values("booking_id")
        )
        query_sql = str(qs.query).upper()
        self.assertNotIn("DISTINCT", query_sql, "El query de select_for_update no debe usar DISTINCT para compatibilidad con PostgreSQL.")
        self.assertNotIn("GROUP BY", query_sql, "El query de select_for_update no debe usar GROUP BY.")

        # Liberar acotado exclusivamente a self.trip_outbound
        released_count = release_expired_bookings(now=eval_time, trip_ids=[self.trip_outbound.pk])
        self.assertEqual(released_count, 1)

        b1.refresh_from_db()
        b2.refresh_from_db()
        b3.refresh_from_db()

        self.assertEqual(b1.status, BookingStatus.EXPIRED)
        self.assertEqual(b1.legs.first().seat_assignments.first().status, AssignmentStatus.RELEASED)
        # b2 fuera de alcance por trip_ids: debe seguir HELD
        self.assertEqual(b2.status, BookingStatus.HELD)
        self.assertEqual(b2.legs.first().seat_assignments.first().status, AssignmentStatus.HELD)
        # b3 no vencida: debe seguir HELD
        self.assertEqual(b3.status, BookingStatus.HELD)

        # Ahora liberar acotado a self.trip_return
        released_ret = release_expired_bookings(now=eval_time, trip_ids=[self.trip_return.pk])
        self.assertEqual(released_ret, 1)
        b2.refresh_from_db()
        self.assertEqual(b2.status, BookingStatus.EXPIRED)
        self.assertEqual(b2.legs.first().seat_assignments.first().status, AssignmentStatus.RELEASED)

    def test_confirm_booking_exact_expiration_boundary(self):
        """Prueba del borde exacto de expiración en confirm_booking:

        exactamente en expires_at vence, un microsegundo antes confirma, un microsegundo después vence.
        """
        now = timezone.now()
        t_exp = now + timedelta(minutes=15)

        # 1. Exactamente en expires_at (now == expires_at): vence y persiste la liberación
        b_exact = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email="b_exact@correo.com",
            expires_at=t_exp,
        )
        leg_exact = BookingLeg.objects.create(
            booking=b_exact,
            sequence=1,
            trip=self.trip_outbound,
            origin_stop=self.ts_out_cba,
            destination_stop=self.ts_out_ssj,
            origin_stop_name="Córdoba Capital",
            destination_stop_name="San Salvador de Jujuy",
            departure_at=self.ts_out_cba.scheduled_at,
            arrival_at=self.ts_out_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_exact,
            passenger=BookingPassenger.objects.create(booking=b_exact, position=1),
            trip=self.trip_outbound,
            seat=self.seat_cama_1,
            status=AssignmentStatus.HELD,
            seat_number=1,
            category=SeatCategory.CAMA,
            price=Decimal("12000.00"),
            currency="ARS",
        )
        with self.assertRaises(BookingExpiredError):
            confirm_booking(b_exact, now=t_exp)
        b_exact.refresh_from_db()
        self.assertEqual(b_exact.status, BookingStatus.EXPIRED)
        self.assertEqual(b_exact.legs.first().seat_assignments.first().status, AssignmentStatus.RELEASED)

        # 2. Un microsegundo antes (now = t_exp - 1 us): confirma exitosamente
        b_before = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email="b_before@correo.com",
            expires_at=t_exp,
        )
        leg_before = BookingLeg.objects.create(
            booking=b_before,
            sequence=1,
            trip=self.trip_outbound,
            origin_stop=self.ts_out_cba,
            destination_stop=self.ts_out_ssj,
            origin_stop_name="Córdoba Capital",
            destination_stop_name="San Salvador de Jujuy",
            departure_at=self.ts_out_cba.scheduled_at,
            arrival_at=self.ts_out_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_before,
            passenger=BookingPassenger.objects.create(booking=b_before, position=1),
            trip=self.trip_outbound,
            seat=self.seat_cama_2,
            status=AssignmentStatus.HELD,
            seat_number=2,
            category=SeatCategory.CAMA,
            price=Decimal("12000.00"),
            currency="ARS",
        )
        time_before = t_exp - timedelta(microseconds=1)
        confirmed_obj = confirm_booking(b_before, now=time_before)
        self.assertEqual(confirmed_obj.status, BookingStatus.CONFIRMED)
        self.assertEqual(confirmed_obj.confirmed_at, time_before)
        b_before.refresh_from_db()
        self.assertEqual(b_before.status, BookingStatus.CONFIRMED)
        self.assertEqual(b_before.legs.first().seat_assignments.first().status, AssignmentStatus.CONFIRMED)

        # 3. Un microsegundo después (now = t_exp + 1 us): vence
        b_after = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email="b_after@correo.com",
            expires_at=t_exp,
        )
        leg_after = BookingLeg.objects.create(
            booking=b_after,
            sequence=1,
            trip=self.trip_outbound,
            origin_stop=self.ts_out_cba,
            destination_stop=self.ts_out_ssj,
            origin_stop_name="Córdoba Capital",
            destination_stop_name="San Salvador de Jujuy",
            departure_at=self.ts_out_cba.scheduled_at,
            arrival_at=self.ts_out_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_after,
            passenger=BookingPassenger.objects.create(booking=b_after, position=1),
            trip=self.trip_outbound,
            seat=self.seat_semi_3,
            status=AssignmentStatus.HELD,
            seat_number=3,
            category=SeatCategory.SEMI_CAMA,
            price=Decimal("9500.00"),
            currency="ARS",
        )
        time_after = t_exp + timedelta(microseconds=1)
        with self.assertRaises(BookingExpiredError):
            confirm_booking(b_after, now=time_after)
        b_after.refresh_from_db()
        self.assertEqual(b_after.status, BookingStatus.EXPIRED)
        self.assertEqual(b_after.legs.first().seat_assignments.first().status, AssignmentStatus.RELEASED)

    def test_confirm_booking_evaluates_expiration_under_lock_without_b_pre(self):
        """Verifica que confirm_booking evalúe la expiración bajo el bloqueo con el reloj efectivo,

        confirmando la liberación antes de lanzar BookingExpiredError sin revertirla.
        """
        now = timezone.now()
        booking = create_online_booking(
            email="no_bpre@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        t_expired_after_lock = booking.expires_at + timedelta(seconds=2)

        with patch("sales.services.timezone.now", return_value=t_expired_after_lock):
            with self.assertRaises(BookingExpiredError):
                confirm_booking(booking, now=None)

        # La reserva y la asignación se confirmaron en base como EXPIRED y RELEASED; la excepción no revirtió el commit
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(assignment.status, AssignmentStatus.RELEASED)

    def test_confirm_booking_race_with_release_expired_and_release_booking(self):
        """confirm_booking es seguro frente a carreras con release_expired_bookings y release_booking."""
        now = timezone.now()
        # Carrera 1: release_expired_bookings vence la reserva antes o durante
        b1 = create_online_booking(
            email="carrera_exp@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        release_expired_bookings(now=now + timedelta(minutes=20), trip_ids=[self.trip_outbound.pk])
        b1.refresh_from_db()
        self.assertEqual(b1.status, BookingStatus.EXPIRED)

        with self.assertRaises(BookingExpiredError):
            confirm_booking(b1)

        # Carrera 2: release_booking libera la reserva
        b2 = create_online_booking(
            email="carrera_rel@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_2]}],
            now=now,
        )
        release_booking(b2)
        b2.refresh_from_db()
        self.assertEqual(b2.status, BookingStatus.RELEASED)

        with self.assertRaises(InvalidBookingError):
            confirm_booking(b2)

    def test_confirm_booking_synchronizes_passed_instance_even_if_already_confirmed(self):
        """confirm_booking sincroniza la instancia pasada en memoria incluso si la reserva ya estaba CONFIRMED en la base."""
        now = timezone.now()
        booking = create_online_booking(
            email="sync_confirmed@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        confirm_time = now + timedelta(minutes=5)
        confirmed_db = confirm_booking(booking.pk, now=confirm_time)
        self.assertEqual(confirmed_db.status, BookingStatus.CONFIRMED)

        # La instancia en memoria aún tiene status HELD y confirmed_at None
        self.assertEqual(booking.status, BookingStatus.HELD)
        self.assertIsNone(booking.confirmed_at)

        res = confirm_booking(booking)
        self.assertEqual(res.status, BookingStatus.CONFIRMED)
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(booking.confirmed_at, confirm_time)
        self.assertEqual(booking.updated_at, confirmed_db.updated_at)

    def test_expire_booking_held_past_expiration_transitions_to_expired_and_releases_seats(self):
        """expire_booking aplicado directamente sobre una reserva HELD vencida transiciona a EXPIRED y libera butacas."""
        now = timezone.now()
        booking = create_online_booking(
            email="expire_held_vencida@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        expired_time = now + timedelta(minutes=20)
        res = expire_booking(booking, now=expired_time)

        self.assertEqual(res.status, BookingStatus.EXPIRED)
        self.assertEqual(booking.status, BookingStatus.EXPIRED)

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.EXPIRED)
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(assignment.status, AssignmentStatus.RELEASED)

    def test_expire_booking_held_not_yet_expired_raises_validation_error(self):
        """expire_booking sobre una reserva HELD aún vigente lanza ValidationError y no modifica su estado."""
        now = timezone.now()
        booking = create_online_booking(
            email="expire_held_vigente@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        before_expiration = now + timedelta(minutes=5)
        with self.assertRaises(ValidationError) as ctx:
            expire_booking(booking, now=before_expiration)
        self.assertIn("aún no ha alcanzado su horario de vencimiento", str(ctx.exception))

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.HELD)
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(assignment.status, AssignmentStatus.HELD)

    def test_expire_booking_confirmed_raises_invalid_booking_error(self):
        """expire_booking sobre una reserva CONFIRMED lanza InvalidBookingError y jamás libera sus butacas (semántica segura)."""
        now = timezone.now()
        booking = create_online_booking(
            email="expire_confirmed@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        confirm_booking(booking, now=now + timedelta(minutes=5))
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

        with self.assertRaises(InvalidBookingError) as ctx:
            expire_booking(booking, now=now + timedelta(minutes=30))
        self.assertIn("No se puede expirar una reserva confirmada", str(ctx.exception))

        # La reserva confirmada y sus butacas permanecen intactas
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        assignment = booking.legs.first().seat_assignments.first()
        self.assertEqual(assignment.status, AssignmentStatus.CONFIRMED)

    def test_expire_booking_released_raises_invalid_booking_error(self):
        """expire_booking sobre una reserva RELEASED lanza InvalidBookingError por estado incompatible."""
        now = timezone.now()
        booking = create_online_booking(
            email="expire_released@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        release_booking(booking, now=now + timedelta(minutes=5))
        self.assertEqual(booking.status, BookingStatus.RELEASED)

        with self.assertRaises(InvalidBookingError) as ctx:
            expire_booking(booking, now=now + timedelta(minutes=30))
        self.assertIn("No se puede expirar una reserva que ya ha sido liberada", str(ctx.exception))

    def test_expire_booking_already_expired_is_idempotent(self):
        """expire_booking sobre una reserva ya EXPIRED es idempotente y sincroniza la instancia."""
        now = timezone.now()
        booking = create_online_booking(
            email="expire_idempotent@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        expire_booking(booking, now=now + timedelta(minutes=20))
        self.assertEqual(booking.status, BookingStatus.EXPIRED)

        res = expire_booking(booking, now=now + timedelta(minutes=30))
        self.assertEqual(res.status, BookingStatus.EXPIRED)


class SalesSeatCollisionsAndConstraintsTests(SalesBaseTestCase):
    def test_conflict_held_seat_cannot_be_booked_again(self):
        now = timezone.now()
        create_online_booking(
            email="titular@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        # Intento de tomar la misma butaca retenida
        with self.assertRaises(SeatUnavailableError):
            create_online_booking(
                email="competidor@correo.com",
                legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
                now=now + timedelta(minutes=2),
            )

    def test_conflict_confirmed_seat_cannot_be_booked_again(self):
        now = timezone.now()
        booking = create_online_booking(
            email="titular@correo.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            now=now,
        )
        confirm_booking(booking, now=now + timedelta(minutes=5))

        with self.assertRaises(SeatUnavailableError):
            create_online_booking(
                email="otro@correo.com",
                legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
                now=now + timedelta(minutes=6),
            )

    def test_conditional_unique_constraint_held_or_confirmed(self):
        """Valida que la base de datos rechace por constraint única dos registros HELD o CONFIRMED para la misma butaca."""
        booking = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email="test_uc@correo.com",
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        passenger1 = BookingPassenger.objects.create(booking=booking, position=1)
        passenger2 = BookingPassenger.objects.create(booking=booking, position=2)
        leg = BookingLeg.objects.create(
            booking=booking,
            sequence=1,
            trip=self.trip_outbound,
            origin_stop=self.ts_out_cba,
            destination_stop=self.ts_out_ssj,
            origin_stop_name="Córdoba Capital",
            destination_stop_name="San Salvador de Jujuy",
            departure_at=self.ts_out_cba.scheduled_at,
            arrival_at=self.ts_out_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg,
            passenger=passenger1,
            trip=self.trip_outbound,
            seat=self.seat_cama_1,
            status=AssignmentStatus.HELD,
            seat_number=1,
            category=SeatCategory.CAMA,
            price=Decimal("12000.00"),
            currency="ARS",
        )
        # Intentar insertar otra asignación HELD para el mismo (trip, seat) debe violar la restricción única
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SeatAssignment.objects.create(
                    leg=leg,
                    passenger=passenger2,
                    trip=self.trip_outbound,
                    seat=self.seat_cama_1,
                    status=AssignmentStatus.HELD,
                    seat_number=1,
                    category=SeatCategory.CAMA,
                    price=Decimal("12000.00"),
                    currency="ARS",
                )

        # Pero una asignación con estado RELEASED sí se puede insertar sin violar la restricción única condicional
        released_assignment = SeatAssignment.objects.create(
            leg=leg,
            passenger=passenger2,
            trip=self.trip_outbound,
            seat=self.seat_cama_1,
            status=AssignmentStatus.RELEASED,
            seat_number=1,
            category=SeatCategory.CAMA,
            price=Decimal("12000.00"),
            currency="ARS",
        )
        self.assertIsNotNone(released_assignment.pk)

    def test_alien_integrity_error_is_not_translated_and_rolls_back(self):
        """Sólo errores de colisión de butaca se traducen a SeatUnavailableError; errores ajenos se preservan y mantienen rollback."""
        with patch.object(SeatAssignment, "save", side_effect=IntegrityError("CHECK constraint failed: fake_alien_check")):
            with self.assertRaises(IntegrityError) as ctx:
                create_online_booking(
                    email="alien_err@correo.com",
                    legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
                )
            self.assertNotIsInstance(ctx.exception, SeatUnavailableError)
            self.assertIn("fake_alien_check", str(ctx.exception))

        # Rollback verificado: no quedan registros en base
        self.assertFalse(Booking.objects.filter(email="alien_err@correo.com").exists())
        self.assertFalse(SeatAssignment.objects.filter(trip=self.trip_outbound, seat=self.seat_cama_1).exists())

    def test_is_seat_collision_integrity_error_sqlite_exact_columns(self):
        """Verifica que el error exacto de SQLite para sales_active_trip_seat_unique sea reconocido."""
        err1 = IntegrityError("UNIQUE constraint failed: sales_seatassignment.trip_id, sales_seatassignment.seat_id")
        err2 = IntegrityError("UNIQUE constraint failed: sales_seatassignment.seat_id, sales_seatassignment.trip_id")
        self.assertTrue(_is_seat_collision_integrity_error(err1))
        self.assertTrue(_is_seat_collision_integrity_error(err2))

    def test_is_seat_collision_integrity_error_sqlite_exact_match_rejects_extra_text(self):
        """El mensaje de SQLite debe coincidir por igualdad exacta; mensajes con texto adicional son rechazados."""
        err_suffix = IntegrityError("UNIQUE constraint failed: sales_seatassignment.trip_id, sales_seatassignment.seat_id in table extra")
        self.assertFalse(_is_seat_collision_integrity_error(err_suffix))

        err_prefix = IntegrityError("Context error: UNIQUE constraint failed: sales_seatassignment.trip_id, sales_seatassignment.seat_id")
        self.assertFalse(_is_seat_collision_integrity_error(err_prefix))

        err_both = IntegrityError("prefix UNIQUE constraint failed: sales_seatassignment.seat_id, sales_seatassignment.trip_id suffix")
        self.assertFalse(_is_seat_collision_integrity_error(err_both))

    def test_is_seat_collision_integrity_error_postgres_constraint_name(self):
        """Verifica que en PostgreSQL se reconozca exclusivamente mediante diag.constraint_name sales_active_trip_seat_unique."""
        class FakeDiag:
            constraint_name = "sales_active_trip_seat_unique"

        class FakeCause(Exception):
            diag = FakeDiag()

        err_obj = IntegrityError("duplicate key")
        err_obj.__cause__ = FakeCause()
        self.assertTrue(_is_seat_collision_integrity_error(err_obj))

        err_direct = IntegrityError("duplicate key")
        err_direct.diag = FakeDiag()
        self.assertTrue(_is_seat_collision_integrity_error(err_direct))

        # Sin diag.constraint_name, el mensaje de texto no debe reconocerse (sin fallback genérico)
        err_msg_only = IntegrityError('duplicate key value violates unique constraint "sales_active_trip_seat_unique"')
        self.assertFalse(_is_seat_collision_integrity_error(err_msg_only))

    def test_is_seat_collision_integrity_error_rejects_broad_or_alien_errors(self):
        """Verifica que errores ajenos con palabras trip o seat o restricciones genéricas NO se clasifiquen como colisión."""
        alien_errors = [
            IntegrityError("UNIQUE constraint failed: other_table.trip_id, other_table.seat_id"),
            IntegrityError("UNIQUE constraint failed: sales_seatassignment.other_col"),
            IntegrityError("CHECK constraint failed: check_trip_seat_valid"),
            IntegrityError("FOREIGN KEY constraint failed: sales_seatassignment.trip_id"),
            IntegrityError('duplicate key value violates unique constraint "other_trip_seat_idx"'),
            IntegrityError("unique constraint with trip and seat keywords in message"),
        ]
        for err in alien_errors:
            with self.subTest(error=str(err)):
                self.assertFalse(_is_seat_collision_integrity_error(err))

    def test_alien_integrity_error_with_trip_and_seat_words_is_not_translated(self):
        """Un IntegrityError ajeno que contenga palabras como 'trip' y 'seat' no se traduce a SeatUnavailableError."""
        broad_alien_err = IntegrityError("UNIQUE constraint failed: trips_trip.code, seats_seat.number")
        with patch.object(SeatAssignment, "save", side_effect=broad_alien_err):
            with self.assertRaises(IntegrityError) as ctx:
                create_online_booking(
                    email="broad_alien@correo.com",
                    legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba, "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
                )
            self.assertNotIsInstance(ctx.exception, SeatUnavailableError)
            self.assertEqual(ctx.exception, broad_alien_err)

        # Rollback verificado
        self.assertFalse(Booking.objects.filter(email="broad_alien@correo.com").exists())
        self.assertFalse(SeatAssignment.objects.filter(trip=self.trip_outbound, seat=self.seat_cama_1).exists())


class SalesArchitectureAndNoPersonalDataTests(TestCase):
    def test_initial_passenger_data_fields_on_passenger(self):
        fields = [f.name for f in BookingPassenger._meta.get_fields()]
        expected = ["first_name", "last_name", "document_type", "document_number", "normalized_document", "birth_date", "gender", "nationality"]
        for field in expected:
            self.assertIn(field, fields, f"El modelo BookingPassenger debe incluir el campo: {field}")
        self.assertNotIn("email", fields, "BookingPassenger no debe almacenar email.")
        self.assertNotIn("phone", fields, "BookingPassenger no debe almacenar phone.")
        booking_fields = [f.name for f in Booking._meta.get_fields()]
        self.assertIn("email", booking_fields, "Booking debe conservar el campo email.")
        self.assertIn("phone", booking_fields, "Booking debe conservar el campo phone.")

    def test_validate_passenger_data_approved_fields_success(self):
        from sales.services import validate_passenger_data
        valid_data = {
            "first_name": "Esteban",
            "last_name": "Quito",
            "document_type": "DNI",
            "document_number": "31.222.333",
            "birth_date": "1990-04-12",
            "nationality": "Argentina",
            "gender": "Masculino",
        }
        # No debe lanzar excepción aun sin email ni phone
        try:
            validate_passenger_data(valid_data, position=1)
        except ValidationError:
            self.fail("validate_passenger_data no debe fallar con datos válidos aprobados sin email ni phone.")

    def test_validate_passenger_data_required_fields_enforced(self):
        from sales.services import validate_passenger_data
        invalid_data = {
            "first_name": "",
            "last_name": "",
            "document_type": "",
            "document_number": "",
            "birth_date": "",
            "nationality": "",
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_passenger_data(invalid_data, position=1)
        errs = ctx.exception.message_dict
        self.assertIn("first_name", errs)
        self.assertIn("last_name", errs)
        self.assertIn("document_type", errs)
        self.assertIn("document_number", errs)
        self.assertIn("birth_date", errs)
        self.assertIn("nationality", errs)
        self.assertNotIn("email", errs, "Email no debe ser exigido para el pasajero.")
        self.assertNotIn("phone", errs, "Phone no debe ser exigido para el pasajero.")

    def test_validate_passenger_data_future_birth_date_rejected(self):
        from sales.services import validate_passenger_data
        future_date = (timezone.now() + timedelta(days=5)).date().isoformat()
        data = {
            "first_name": "Futuro",
            "last_name": "Viajero",
            "document_type": "DNI",
            "document_number": "45.000.000",
            "birth_date": future_date,
            "nationality": "Argentina",
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_passenger_data(data, position=1)
        self.assertIn("birth_date", ctx.exception.message_dict)

    def test_booking_aggregate_no_separate_compra_reserva_venta_models(self):
        from django.apps import apps
        sales_models = [m.__name__ for m in apps.get_app_config("sales").get_models()]
        self.assertIn("Booking", sales_models)
        self.assertIn("BookingLeg", sales_models)
        self.assertIn("BookingPassenger", sales_models)
        self.assertIn("SeatAssignment", sales_models)

        for separate_name in ["Compra", "Reserva", "Venta"]:
            self.assertNotIn(separate_name, sales_models, f"No deben existir entidades separadas {separate_name}; Booking es el agregado principal.")

    def test_operations_does_not_import_sales(self):
        import importlib
        import inspect
        import operations.models
        import operations.services
        import operations.validators

        for mod in [operations.models, operations.services, operations.validators]:
            source = inspect.getsource(mod)
            self.assertNotIn("sales", source, f"El módulo de operaciones ({mod.__name__}) no debe depender de sales.")


class SalesPostgresConcurrencyTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "Requiere PostgreSQL para probar concurrencia real y select_for_update; "
                "SQLite no admite bloqueo de filas concurrente."
            )

    def setUp(self):
        super().setUp()
        self.stop1 = Stop.objects.create(code="CONC1", name="Origen", city="C", province="P")
        self.stop2 = Stop.objects.create(code="CONC2", name="Destino", city="C", province="P")
        self.route = Route.objects.create(code="CONC-RT", name="Ruta Concurrencia")
        RouteStop.objects.create(route=self.route, stop=self.stop1, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=self.route, stop=self.stop2, sequence=2, allows_boarding=False, allows_alighting=True)

        self.bus = Bus.objects.create(code="CONC-BUS", display_name="Bus Concurrente")
        self.seat = Seat.objects.create(bus=self.bus, number=1, deck=Seat.Deck.LOWER, category=SeatCategory.CAMA, position_x=0, position_y=0)

        dep_time = timezone.now() + timedelta(days=5)
        self.trip = Trip.objects.create(route=self.route, bus=self.bus, departure_at=dep_time, status=Trip.Status.SCHEDULED)
        self.ts1 = TripStop.objects.create(trip=self.trip, stop=self.stop1, sequence=1, scheduled_at=dep_time, allows_boarding=True, allows_alighting=False)
        self.ts2 = TripStop.objects.create(trip=self.trip, stop=self.stop2, sequence=2, scheduled_at=dep_time + timedelta(hours=2), allows_boarding=False, allows_alighting=True)

        TripFare.objects.create(trip=self.trip, origin_stop=self.ts1, destination_stop=self.ts2, seat_category=SeatCategory.CAMA, amount=Decimal("5000.00"), is_active=True)

    def tearDown(self):
        from django.db import connections
        connections.close_all()
        super().tearDown()

    def test_concurrent_booking_attempts_for_same_seat(self):
        """En PostgreSQL real, dos transacciones concurrentes sobre la misma butaca sincronizan su arranque
        con una barrera, y se verifica la serialización estricta bajo el bloqueo exclusivo Trip.objects.select_for_update().

        Bajo el bloqueo a nivel de fila de Trip, las transacciones se ejecutan secuencialmente:
        el primer hilo adquiere el lock y completa la reserva; el segundo hilo espera hasta que el primero
        realiza el commit. Al desbloquearse, el segundo hilo ejecuta su prevalidación en Python
        (SeatAssignment.objects.filter(...).exists()), detecta la butaca ocupada y lanza SeatUnavailableError
        a nivel de dominio, evidenciando serialización real y evitando colisiones de integridad no controladas.
        """
        results = []
        barrier_errors = []
        seat_collision_errors = []
        unexpected_errors = []
        barrier = threading.Barrier(2)

        def attempt_booking(email):
            from django.db import connections
            try:
                try:
                    barrier.wait(timeout=10)
                except Exception as be:
                    barrier_errors.append(be)
                    return

                b = create_online_booking(
                    email=email,
                    legs=[{"trip": self.trip, "origin_stop": self.ts1, "destination_stop": self.ts2, "seats": [self.seat]}],
                )
                results.append(b)
            except SeatUnavailableError as sue:
                seat_collision_errors.append(sue)
            except Exception as e:
                unexpected_errors.append(e)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=attempt_booking, args=("t1@correo.com",))
        t2 = threading.Thread(target=attempt_booking, args=("t2@correo.com",))

        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # 1. Comprobar que ambos hilos hayan terminado tras join
        self.assertFalse(t1.is_alive(), "El hilo 1 debe haber finalizado tras join.")
        self.assertFalse(t2.is_alive(), "El hilo 2 debe haber finalizado tras join.")

        # 2. Diferenciar errores de barrera de conflictos de butaca y errores inesperados
        self.assertEqual(barrier_errors, [], f"Hubo errores de sincronización en la barrera: {barrier_errors}")
        self.assertEqual(unexpected_errors, [], f"Hubo errores inesperados en los hilos: {unexpected_errors}")

        # 3. Evidenciar serialización real bajo Trip lock: una reserva victoriosa y un conflicto de dominio
        self.assertEqual(len(results), 1, "Exactamente una reserva debe triunfar")
        self.assertEqual(results[0].status, BookingStatus.HELD)
        self.assertEqual(len(seat_collision_errors), 1, "El hilo perdedor debe recibir SeatUnavailableError")
        self.assertEqual(
            SeatAssignment.objects.filter(trip=self.trip, seat=self.seat, status=AssignmentStatus.HELD).count(),
            1,
        )

    def test_postgres_conditional_unique_constraint_and_selective_translation(self):
        """Provoca directamente la restricción única condicional 'sales_active_trip_seat_unique' en PostgreSQL.

        Inserta dos registros SeatAssignment activos para el mismo viaje y butaca mediante el ORM,
        eludiendo la prevalidación Python de create_booking (donde el Trip lock serializa y gana la comprobación
        de existencia). Comprueba que PostgreSQL lance IntegrityError con diag.constraint_name exacto,
        que _is_seat_collision_integrity_error lo reconozca y que se traduzca selectivamente a SeatUnavailableError.
        """
        # 1. Reserva inicial que ocupa la butaca en estado HELD
        booking1 = create_online_booking(
            email="pg_direct_hold@correo.com",
            legs=[{"trip": self.trip, "origin_stop": self.ts1, "destination_stop": self.ts2, "seats": [self.seat]}],
        )
        self.assertEqual(booking1.status, BookingStatus.HELD)

        # 2. Segunda reserva y tramo creados independientemente
        booking2 = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email="pg_direct_coll@correo.com",
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        passenger2 = BookingPassenger.objects.create(booking=booking2, position=1)
        leg2 = BookingLeg.objects.create(
            booking=booking2,
            sequence=1,
            trip=self.trip,
            origin_stop=self.ts1,
            destination_stop=self.ts2,
            origin_stop_name=self.ts1.stop.name,
            destination_stop_name=self.ts2.stop.name,
            departure_at=self.ts1.scheduled_at,
            arrival_at=self.ts2.scheduled_at,
        )

        # 3. Inserción directa de un segundo SeatAssignment para el mismo trip y seat en estado HELD.
        # Al omitir create_booking, no interviene la prevalidación Python ni el Trip lock,
        # provocando directamente la restricción 'sales_active_trip_seat_unique' a nivel PostgreSQL.
        dup_assignment = SeatAssignment(
            leg=leg2,
            passenger=passenger2,
            trip=self.trip,
            seat=self.seat,
            status=AssignmentStatus.HELD,
            seat_number=self.seat.number,
            category=self.seat.category,
            price=Decimal("5000.00"),
            currency="ARS",
        )

        with self.assertRaises(IntegrityError) as ctx:
            with transaction.atomic():
                dup_assignment.save()

        # 4. Verificar diag.constraint_name en PostgreSQL
        exc = ctx.exception
        cause = getattr(exc, "__cause__", None)
        diag = getattr(cause, "diag", None) if cause is not None else getattr(exc, "diag", None)
        self.assertIsNotNone(diag, "En PostgreSQL psycopg2 debe exponer el atributo diag.")
        self.assertEqual(diag.constraint_name, "sales_active_trip_seat_unique")

        # 5. Verificar que _is_seat_collision_integrity_error reconozca el error
        self.assertTrue(_is_seat_collision_integrity_error(exc))

        # 6. Verificar la traducción selectiva de este IntegrityError a SeatUnavailableError
        # (sin afirmar que create_booking llegue a este punto cuando la prevalidación Python serializada gana)
        try:
            if _is_seat_collision_integrity_error(exc):
                raise SeatUnavailableError("Una o más butacas ya están reservadas o no están disponibles.") from exc
            raise exc
        except SeatUnavailableError as sue:
            self.assertIn("ya están reservadas o no están disponibles", str(sue))
            self.assertEqual(sue.__cause__, exc)

    def test_postgres_release_expired_bookings_real_route_with_trip_ids(self):
        """Verifica que en PostgreSQL real release_expired_bookings con trip_ids se ejecute exitosamente

        sin error de SELECT DISTINCT ... FOR UPDATE sobre el modelo Booking.
        """
        now = timezone.now()
        b = create_online_booking(
            email="pg_expired@correo.com",
            legs=[{"trip": self.trip, "origin_stop": self.ts1, "destination_stop": self.ts2, "seats": [self.seat]}],
            now=now,
        )
        expired_count = release_expired_bookings(now=now + timedelta(minutes=20), trip_ids=[self.trip.pk])
        self.assertEqual(expired_count, 1)
        b.refresh_from_db()
        self.assertEqual(b.status, BookingStatus.EXPIRED)
        self.assertEqual(
            SeatAssignment.objects.filter(trip=self.trip, seat=self.seat, status=AssignmentStatus.RELEASED).count(),
            1,
        )
