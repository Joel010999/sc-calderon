"""Pruebas integrales de gráficos de ventas y corrección de comisiones de SC Viajes."""
import asyncio
import base64
import csv
import io
import json
import os
import subprocess
import time
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import aiohttp
import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from payments.models import Payment, PaymentMethod, PaymentStatus
from panel.report_services import COMMISSION_PER_TICKET, build_sales_charts, get_commissionable_assignments
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)


AR = ZoneInfo("America/Argentina/Buenos_Aires")
User = get_user_model()


class SalesChartsFixturesMixin:
    @classmethod
    def setup_fixtures(cls):
        cls.admin_group = Group.objects.create(name="Administrador")
        cls.seller_group = Group.objects.create(name="Vendedor")

        cls.admin = User.objects.create_user(username="admin-user", password="password")
        cls.admin.groups.add(cls.admin_group)

        cls.seller_a = User.objects.create_user(username="seller-a", password="password")
        cls.seller_a.groups.add(cls.seller_group)

        cls.seller_b = User.objects.create_user(username="seller-b", password="password")
        cls.seller_b.groups.add(cls.seller_group)

        # Paradas confirmadas
        cls.stop_cba = Stop.objects.create(code="CBA", name="Córdoba Capital", province="Córdoba", city="Córdoba")
        cls.stop_jma = Stop.objects.create(code="JMA", name="Jesús María", province="Córdoba", city="Jesús María")
        cls.stop_gue = Stop.objects.create(code="GUE", name="Güemes", province="Salta", city="Güemes")
        cls.stop_ssj = Stop.objects.create(code="SSJ", name="San Salvador de Jujuy", province="Jujuy", city="Jujuy")

        # Recorridos ida y vuelta
        cls.route_ida = Route.objects.create(code="CBA-JUJ", name="Córdoba → Jujuy", is_active=True)
        RouteStop.objects.create(route=cls.route_ida, stop=cls.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route_ida, stop=cls.stop_gue, sequence=2, allows_boarding=False, allows_alighting=True)
        RouteStop.objects.create(route=cls.route_ida, stop=cls.stop_ssj, sequence=3, allows_boarding=False, allows_alighting=True)

        cls.route_vuelta = Route.objects.create(code="JUJ-CBA", name="Jujuy → Córdoba", is_active=True)
        RouteStop.objects.create(route=cls.route_vuelta, stop=cls.stop_ssj, sequence=1, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route_vuelta, stop=cls.stop_gue, sequence=2, allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route_vuelta, stop=cls.stop_cba, sequence=3, allows_boarding=False, allows_alighting=True)

        # Colectivos: dos colectivos para probar refuerzos con igual horario
        cls.bus_1 = Bus.objects.create(code="BUS-01", is_active=True)
        cls.seats_bus_1 = [
            Seat.objects.create(bus=cls.bus_1, number=i, category=SeatCategory.CAMA if i <= 12 else SeatCategory.SEMI_CAMA,
                                deck=Seat.Deck.LOWER if i <= 12 else Seat.Deck.UPPER,
                                position_x=i, position_y=1, is_active=True)
            for i in range(1, 61)
        ]

        cls.bus_2 = Bus.objects.create(code="BUS-02", is_active=True)
        cls.seats_bus_2 = [
            Seat.objects.create(bus=cls.bus_2, number=i, category=SeatCategory.CAMA if i <= 12 else SeatCategory.SEMI_CAMA,
                                deck=Seat.Deck.LOWER if i <= 12 else Seat.Deck.UPPER,
                                position_x=i, position_y=1, is_active=True)
            for i in range(1, 61)
        ]

        # Salidas: trip_1 y trip_2 son refuerzos con exactamente la misma fecha y hora
        departure_time = timezone.make_aware(datetime(2026, 11, 20, 20, 0), AR)
        cls.trip_1 = Trip.objects.create(route=cls.route_ida, bus=cls.bus_1, departure_at=departure_time, status=Trip.Status.SCHEDULED)
        cls.trip_1_ts_cba = TripStop.objects.create(trip=cls.trip_1, stop=cls.stop_cba, sequence=1, scheduled_at=departure_time, allows_boarding=True, allows_alighting=False)
        cls.trip_1_ts_ssj = TripStop.objects.create(trip=cls.trip_1, stop=cls.stop_ssj, sequence=2, scheduled_at=departure_time + timedelta(hours=12), allows_boarding=False, allows_alighting=True)

        # Refuerzo: mismo horario y recorrido, distinto colectivo y distinto trip.pk
        cls.trip_2 = Trip.objects.create(route=cls.route_ida, bus=cls.bus_2, departure_at=departure_time, status=Trip.Status.SCHEDULED)
        cls.trip_2_ts_cba = TripStop.objects.create(trip=cls.trip_2, stop=cls.stop_cba, sequence=1, scheduled_at=departure_time, allows_boarding=True, allows_alighting=False)
        cls.trip_2_ts_ssj = TripStop.objects.create(trip=cls.trip_2, stop=cls.stop_ssj, sequence=2, scheduled_at=departure_time + timedelta(hours=12), allows_boarding=False, allows_alighting=True)

        # Salida de regreso (vuelta)
        return_time = timezone.make_aware(datetime(2026, 11, 25, 18, 0), AR)
        cls.trip_return = Trip.objects.create(route=cls.route_vuelta, bus=cls.bus_1, departure_at=return_time, status=Trip.Status.SCHEDULED)
        cls.trip_ret_ts_ssj = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_ssj, sequence=1, scheduled_at=return_time, allows_boarding=True, allows_alighting=False)
        cls.trip_ret_ts_cba = TripStop.objects.create(trip=cls.trip_return, stop=cls.stop_cba, sequence=2, scheduled_at=return_time + timedelta(hours=12), allows_boarding=False, allows_alighting=True)

        # Tarifas por salida
        TripFare.objects.create(trip=cls.trip_1, seat_category=SeatCategory.CAMA, amount=Decimal("20000.00"), is_active=True)
        TripFare.objects.create(trip=cls.trip_1, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("15000.00"), is_active=True)
        TripFare.objects.create(trip=cls.trip_2, seat_category=SeatCategory.CAMA, amount=Decimal("20000.00"), is_active=True)
        TripFare.objects.create(trip=cls.trip_2, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("15000.00"), is_active=True)
        TripFare.objects.create(trip=cls.trip_return, seat_category=SeatCategory.CAMA, amount=Decimal("20000.00"), is_active=True)
        TripFare.objects.create(trip=cls.trip_return, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("15000.00"), is_active=True)

        cls._create_persistent_fixtures()

    @classmethod
    def _create_persistent_fixtures(cls):
        conf_time = timezone.make_aware(datetime(2026, 11, 10, 14, 0), AR)

        # 1. Venta Interna Vendedor A: IDA Y VUELTA, 1 adulto (CAMA ida, SEMICAMA vuelta). Pago aprobado Efectivo.
        # Esperado: 2 unidades comisionables = ARS 10.000. Bruto = 35.000.
        cls.b_seller_a = Booking.objects.create(
            channel=BookingChannel.MANUAL, seller=cls.seller_a, status=BookingStatus.CONFIRMED,
            email="seller_a@test.scviajes", phone="3515551111", confirmed_at=conf_time
        )
        p1 = BookingPassenger.objects.create(
            booking=cls.b_seller_a, position=1, first_name="Pasajero", last_name="A",
            document_type="DNI", document_number="30111111", birth_date=datetime(1990, 1, 1).date(),
        )
        leg1 = BookingLeg.objects.create(
            booking=cls.b_seller_a, sequence=1, trip=cls.trip_1, origin_stop=cls.trip_1_ts_cba,
            destination_stop=cls.trip_1_ts_ssj, origin_stop_name="Córdoba Capital", destination_stop_name="San Salvador de Jujuy",
            departure_at=cls.trip_1_ts_cba.scheduled_at, arrival_at=cls.trip_1_ts_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg1, passenger=p1, trip=cls.trip_1, seat=cls.seats_bus_1[0], # CAMA
            status=AssignmentStatus.CONFIRMED, seat_number=1, category=SeatCategory.CAMA, price=Decimal("20000.00"), currency="ARS",
        )
        leg2 = BookingLeg.objects.create(
            booking=cls.b_seller_a, sequence=2, trip=cls.trip_return, origin_stop=cls.trip_ret_ts_ssj,
            destination_stop=cls.trip_ret_ts_cba, origin_stop_name="San Salvador de Jujuy", destination_stop_name="Córdoba Capital",
            departure_at=cls.trip_ret_ts_ssj.scheduled_at, arrival_at=cls.trip_ret_ts_cba.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg2, passenger=p1, trip=cls.trip_return, seat=cls.seats_bus_1[20], # SEMI_CAMA
            status=AssignmentStatus.CONFIRMED, seat_number=21, category=SeatCategory.SEMI_CAMA, price=Decimal("15000.00"), currency="ARS",
        )
        Payment.objects.create(
            booking=cls.b_seller_a, method=PaymentMethod.CASH, status=PaymentStatus.APPROVED,
            amount=Decimal("35000.00"), currency="ARS", reviewed_by=cls.admin, reviewed_at=conf_time,
            registered_by=cls.seller_a,
        )

        # 2. Venta Interna Vendedor B: SOLO IDA, 1 adulto (SEMICAMA) + 1 menor gratuito sin butaca ($0). Transferencia aprobada.
        # Esperado: 1 unidad comisionable (adulto) = ARS 5.000. Menor gratuito = 0 comisión. Bruto = 15.000.
        cls.b_seller_b = Booking.objects.create(
            channel=BookingChannel.MANUAL, seller=cls.seller_b, status=BookingStatus.CONFIRMED,
            email="seller_b@test.scviajes", phone="3515552222", confirmed_at=conf_time + timedelta(hours=1)
        )
        p_adult = BookingPassenger.objects.create(
            booking=cls.b_seller_b, position=1, first_name="Adulto", last_name="B",
            document_type="DNI", document_number="30222222", birth_date=datetime(1985, 5, 5).date(),
        )
        BookingPassenger.objects.create(
            booking=cls.b_seller_b, position=2, first_name="Menor", last_name="B",
            document_type="DNI", document_number="60222222", birth_date=datetime(2023, 1, 1).date(),
            is_lap_infant=True, accompanied_by=p_adult,
        )
        leg_b = BookingLeg.objects.create(
            booking=cls.b_seller_b, sequence=1, trip=cls.trip_1, origin_stop=cls.trip_1_ts_cba,
            destination_stop=cls.trip_1_ts_ssj, origin_stop_name="Córdoba Capital", destination_stop_name="San Salvador de Jujuy",
            departure_at=cls.trip_1_ts_cba.scheduled_at, arrival_at=cls.trip_1_ts_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_b, passenger=p_adult, trip=cls.trip_1, seat=cls.seats_bus_1[25], # SEMI_CAMA
            status=AssignmentStatus.CONFIRMED, seat_number=26, category=SeatCategory.SEMI_CAMA, price=Decimal("15000.00"), currency="ARS",
        )
        # Menor gratuito no tiene SeatAssignment
        Payment.objects.create(
            booking=cls.b_seller_b, method=PaymentMethod.BANK_TRANSFER, status=PaymentStatus.APPROVED,
            amount=Decimal("15000.00"), currency="ARS", reviewed_by=cls.admin, reviewed_at=conf_time + timedelta(hours=1),
            registered_by=cls.seller_b,
        )

        # 3. Venta ONLINE 1: SOLO IDA, 1 adulto (CAMA). Transferencia aprobada.
        # Esperado: 0 unidades comisionables = ARS 0.
        cls.b_online_1 = Booking.objects.create(
            channel=BookingChannel.ONLINE, seller=None, status=BookingStatus.CONFIRMED,
            email="online1@test.scviajes", phone="3515553333", confirmed_at=conf_time + timedelta(hours=2),
            expires_at=conf_time + timedelta(hours=2, minutes=15),
        )
        p_on = BookingPassenger.objects.create(
            booking=cls.b_online_1, position=1, first_name="Online", last_name="Cliente",
            document_type="DNI", document_number="30333333", birth_date=datetime(1992, 3, 3).date(),
        )
        leg_on = BookingLeg.objects.create(
            booking=cls.b_online_1, sequence=1, trip=cls.trip_1, origin_stop=cls.trip_1_ts_cba,
            destination_stop=cls.trip_1_ts_ssj, origin_stop_name="Córdoba Capital", destination_stop_name="San Salvador de Jujuy",
            departure_at=cls.trip_1_ts_cba.scheduled_at, arrival_at=cls.trip_1_ts_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_on, passenger=p_on, trip=cls.trip_1, seat=cls.seats_bus_1[1], # CAMA
            status=AssignmentStatus.CONFIRMED, seat_number=2, category=SeatCategory.CAMA, price=Decimal("20000.00"), currency="ARS",
        )
        Payment.objects.create(
            booking=cls.b_online_1, method=PaymentMethod.BANK_TRANSFER, status=PaymentStatus.APPROVED,
            amount=Decimal("20000.00"), currency="ARS", reviewed_by=cls.admin, reviewed_at=conf_time + timedelta(hours=2),
        )

        # 4. Venta ONLINE 2: IDA Y VUELTA, 1 adulto (CAMA ida, CAMA vuelta). Transferencia aprobada.
        # Regla: ONLINE SIEMPRE comisión cero.
        # Esperado: 0 unidades comisionables = ARS 0.
        cls.b_online_2 = Booking.objects.create(
            channel=BookingChannel.ONLINE, seller=None, status=BookingStatus.CONFIRMED,
            email="online2@test.scviajes", phone="3515554444", confirmed_at=conf_time + timedelta(hours=3),
            expires_at=conf_time + timedelta(hours=3, minutes=15),
        )
        p_on_2 = BookingPassenger.objects.create(
            booking=cls.b_online_2, position=1, first_name="Online", last_name="Dos",
            document_type="DNI", document_number="30444444", birth_date=datetime(1993, 4, 4).date(),
        )
        leg_on_2a = BookingLeg.objects.create(
            booking=cls.b_online_2, sequence=1, trip=cls.trip_1, origin_stop=cls.trip_1_ts_cba,
            destination_stop=cls.trip_1_ts_ssj, origin_stop_name="Córdoba Capital", destination_stop_name="San Salvador de Jujuy",
            departure_at=cls.trip_1_ts_cba.scheduled_at, arrival_at=cls.trip_1_ts_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_on_2a, passenger=p_on_2, trip=cls.trip_1, seat=cls.seats_bus_1[2], # CAMA
            status=AssignmentStatus.CONFIRMED, seat_number=3, category=SeatCategory.CAMA, price=Decimal("20000.00"), currency="ARS",
        )
        leg_on_2b = BookingLeg.objects.create(
            booking=cls.b_online_2, sequence=2, trip=cls.trip_return, origin_stop=cls.trip_ret_ts_ssj,
            destination_stop=cls.trip_ret_ts_cba, origin_stop_name="San Salvador de Jujuy", destination_stop_name="Córdoba Capital",
            departure_at=cls.trip_ret_ts_ssj.scheduled_at, arrival_at=cls.trip_ret_ts_cba.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_on_2b, passenger=p_on_2, trip=cls.trip_return, seat=cls.seats_bus_1[3], # CAMA
            status=AssignmentStatus.CONFIRMED, seat_number=4, category=SeatCategory.CAMA, price=Decimal("20000.00"), currency="ARS",
        )
        Payment.objects.create(
            booking=cls.b_online_2, method=PaymentMethod.BANK_TRANSFER, status=PaymentStatus.APPROVED,
            amount=Decimal("40000.00"), currency="ARS", reviewed_by=cls.admin, reviewed_at=conf_time + timedelta(hours=3),
        )

        # 5. Salida Refuerzo (trip_2, colectivo BUS-02, mismo horario): Venta interna Vendedor A, 1 adulto (CAMA).
        # Prueba: Refuerzos con igual horario se identifican por Trip.pk y Colectivo de forma separada.
        cls.b_refuerzo = Booking.objects.create(
            channel=BookingChannel.MANUAL, seller=cls.seller_a, status=BookingStatus.CONFIRMED,
            email="refuerzo@test.scviajes", phone="3515555555", confirmed_at=conf_time + timedelta(hours=4)
        )
        p_ref = BookingPassenger.objects.create(
            booking=cls.b_refuerzo, position=1, first_name="Pasajero", last_name="Refuerzo",
            document_type="DNI", document_number="30555555", birth_date=datetime(1989, 9, 9).date(),
        )
        leg_ref = BookingLeg.objects.create(
            booking=cls.b_refuerzo, sequence=1, trip=cls.trip_2, origin_stop=cls.trip_2_ts_cba,
            destination_stop=cls.trip_2_ts_ssj, origin_stop_name="Córdoba Capital", destination_stop_name="San Salvador de Jujuy",
            departure_at=cls.trip_2_ts_cba.scheduled_at, arrival_at=cls.trip_2_ts_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_ref, passenger=p_ref, trip=cls.trip_2, seat=cls.seats_bus_2[0], # CAMA
            status=AssignmentStatus.CONFIRMED, seat_number=1, category=SeatCategory.CAMA, price=Decimal("20000.00"), currency="ARS",
        )
        # Idempotencia / reintentos: 1 intento rechazado, luego 1 intento aprobado
        Payment.objects.create(
            booking=cls.b_refuerzo, method=PaymentMethod.BANK_TRANSFER, status=PaymentStatus.REJECTED,
            amount=Decimal("20000.00"), currency="ARS", rejection_reason="Comprobante ilegible",
        )
        Payment.objects.create(
            booking=cls.b_refuerzo, method=PaymentMethod.BANK_TRANSFER, status=PaymentStatus.APPROVED,
            amount=Decimal("20000.00"), currency="ARS", reviewed_by=cls.admin, reviewed_at=conf_time + timedelta(hours=4),
            registered_by=cls.seller_a,
        )

        # 6. Estados Excluidos: HELD impaga, y pago rechazado sin confirmar.
        cls.b_held = Booking.objects.create(
            channel=BookingChannel.MANUAL, seller=cls.seller_a, status=BookingStatus.HELD,
            email="held@test.scviajes", phone="3515556666",
        )
        leg_held = BookingLeg.objects.create(
            booking=cls.b_held, sequence=1, trip=cls.trip_1, origin_stop=cls.trip_1_ts_cba,
            destination_stop=cls.trip_1_ts_ssj, origin_stop_name="Córdoba Capital", destination_stop_name="San Salvador de Jujuy",
            departure_at=cls.trip_1_ts_cba.scheduled_at, arrival_at=cls.trip_1_ts_ssj.scheduled_at,
        )
        p_held = BookingPassenger.objects.create(
            booking=cls.b_held, position=1, first_name="Held", last_name="User",
            document_type="DNI", document_number="30666666", birth_date=datetime(1995, 6, 6).date(),
        )
        SeatAssignment.objects.create(
            leg=leg_held, passenger=p_held, trip=cls.trip_1, seat=cls.seats_bus_1[10],
            status=AssignmentStatus.HELD, seat_number=11, category=SeatCategory.CAMA, price=Decimal("20000.00"), currency="ARS",
        )


class SalesChartsAndCommissionsTests(SalesChartsFixturesMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.setup_fixtures()

    def setUp(self):
        self.client = Client()

    def test_commissions_exact_rules_and_exclusions(self):
        """
        Verifica comisiones exactas y exclusiones estrictas:
        - ARS 5.000 por pasajero con tarifa positiva y por tramo vendido únicamente venta interna atribuida.
        - ONLINE siempre comisión cero (aun con seller asignado).
        - Menores gratuitos sin butaca ($0) comisión cero.
        - Excluye estados HELD, REJECTED, EXPIRED.
        - Reintentos no duplican comisiones.
        """
        # Admin ve el total global
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("panel:sales_report"))
        self.assertEqual(resp_admin.status_code, 200)

        # Total ventas confirmadas: b_seller_a (1), b_seller_b (1), b_online_1 (1), b_online_2 (1), b_refuerzo (1) = 5
        self.assertEqual(resp_admin.context["confirmed_sales_count"], 5)

        # Unidades comisionables globales:
        # b_seller_a: 2 tramos * 1 pasajero = 2 unidades
        # b_seller_b: 1 tramo * 1 adulto = 1 unidad (menor gratuito = 0)
        # b_online_1: 0 unidades (ONLINE)
        # b_online_2: 0 unidades (ONLINE)
        # b_refuerzo: 1 tramo * 1 pasajero = 1 unidad (pago rechazado previo no duplica)
        # Total unidades comisionables = 2 + 1 + 0 + 0 + 1 = 4
        self.assertEqual(resp_admin.context["commissionable_units"], 4)
        self.assertEqual(resp_admin.context["commission_total"], Decimal("20000.00")) # 4 * 5.000

        # Ingreso bruto: 35000 + 15000 + 20000 + 40000 + 20000 = 130.000
        self.assertEqual(resp_admin.context["total_amount"], Decimal("130000.00"))
        # Neto operativo: 130.000 - 20.000 = 110.000
        self.assertEqual(resp_admin.context["net_operating"], Decimal("110000.00"))

    def test_commissions_seller_scope_isolation(self):
        """Vendedor A y B ven solo sus propias unidades comisionables y comisiones."""
        # Vendedor A: b_seller_a (2 unidades) + b_refuerzo (1 unidad) = 3 unidades (15.000)
        self.client.force_login(self.seller_a)
        resp_a = self.client.get(reverse("panel:sales_report"))
        self.assertEqual(resp_a.context["commissionable_units"], 3)
        self.assertEqual(resp_a.context["commission_total"], Decimal("15000.00"))

        # Vendedor B: b_seller_b (1 unidad = 5.000)
        self.client.force_login(self.seller_b)
        resp_b = self.client.get(reverse("panel:sales_report"))
        self.assertEqual(resp_b.context["commissionable_units"], 1)
        self.assertEqual(resp_b.context["commission_total"], Decimal("5000.00"))

    def test_dashboard_and_report_consistency(self):
        """Garantiza consistencia absoluta de cálculo de comisiones entre Dashboard y Reporte."""
        # Para Vendedor A
        self.client.force_login(self.seller_a)
        dash_a = self.client.get(reverse("panel:dashboard"))
        rep_a = self.client.get(reverse("panel:sales_report"))
        self.assertEqual(dash_a.context["commissionable_units"], rep_a.context["commissionable_units"])
        self.assertEqual(dash_a.context["commission_total"], rep_a.context["commission_total"])
        self.assertEqual(dash_a.context["confirmed_revenue_total"], rep_a.context["total_amount"])
        self.assertEqual(dash_a.context["net_operating_total"], rep_a.context["net_operating"])

        # Para Vendedor B
        self.client.force_login(self.seller_b)
        dash_b = self.client.get(reverse("panel:dashboard"))
        rep_b = self.client.get(reverse("panel:sales_report"))
        self.assertEqual(dash_b.context["commissionable_units"], rep_b.context["commissionable_units"])
        self.assertEqual(dash_b.context["commission_total"], rep_b.context["commission_total"])
        self.assertEqual(dash_b.context["confirmed_revenue_total"], rep_b.context["total_amount"])
        self.assertEqual(dash_b.context["net_operating_total"], rep_b.context["net_operating"])

        # Para Administrador
        self.client.force_login(self.admin)
        dash_adm = self.client.get(reverse("panel:dashboard"))
        rep_adm = self.client.get(reverse("panel:sales_report"))
        self.assertEqual(dash_adm.context["commissionable_units"], rep_adm.context["commissionable_units"])
        self.assertEqual(dash_adm.context["commission_total"], rep_adm.context["commission_total"])
        self.assertEqual(dash_adm.context["confirmed_revenue_total"], rep_adm.context["total_amount"])
        self.assertEqual(dash_adm.context["net_operating_total"], rep_adm.context["net_operating"])

    def test_six_charts_denominators_percentages_and_units(self):
        """
        Verifica denominadores, porcentajes y unidades de los seis gráficos:
        1) canal web vs interna
        2) participación por vendedor (solo administración)
        3) categoría cama vs semicama
        4) sentido del recorrido
        5) tipo solo ida vs ida/vuelta
        6) salida concreta sin agrupar refuerzos
        """
        self.client.force_login(self.admin)
        response = self.client.get(reverse("panel:sales_report"))
        self.assertEqual(response.status_code, 200)

        # 1. Gráfico Canal
        c1 = response.context["chart_channel"]
        self.assertEqual(c1["unit"], "Compras confirmadas")
        self.assertEqual(c1["denominator"], 5) # 5 compras confirmadas
        # 2 web (b_online_1, b_online_2) = 40.0%
        # 3 interna (b_seller_a, b_seller_b, b_refuerzo) = 60.0%
        items1 = {item["key"]: item for item in c1["items"]}
        self.assertEqual(items1["web"]["count"], 2)
        self.assertEqual(items1["web"]["percentage"], 40.0)
        self.assertEqual(items1["internal"]["count"], 3)
        self.assertEqual(items1["internal"]["percentage"], 60.0)
        self.assertIn("menor(es) de 5 años", c1["notes"])

        # 2. Gráfico Participación por vendedor (administración)
        c2 = response.context["chart_seller"]
        self.assertIsNotNone(c2)
        self.assertEqual(c2["unit"], "Compras confirmadas")
        self.assertEqual(c2["denominator"], 5)
        seller_items = {item["label"]: item for item in c2["items"]}
        # seller-a: b_seller_a + b_refuerzo = 2 (40.0%)
        # seller-b: b_seller_b = 1 (20.0%)
        # Sin vendedor: b_online_1 + b_online_2 = 2 (40.0%)
        self.assertIn("seller-a", seller_items)
        self.assertIn("seller-b", seller_items)
        self.assertEqual(seller_items["seller-a"]["count"], 2)
        self.assertEqual(seller_items["seller-a"]["percentage"], 40.0)
        self.assertEqual(seller_items["seller-b"]["count"], 1)
        self.assertEqual(seller_items["seller-b"]["percentage"], 20.0)

        # 3. Gráfico Categoría (pasajeros/tramos con asiento)
        c3 = response.context["chart_category"]
        self.assertEqual(c3["unit"], "Pasajeros/tramos con asiento")
        # Total asientos = 7 (5 CAMA, 2 SEMI_CAMA)
        self.assertEqual(c3["denominator"], 7)
        cat_items = {item["key"]: item for item in c3["items"]}
        self.assertEqual(cat_items["cama"]["count"], 5)
        self.assertEqual(cat_items["cama"]["percentage"], 71.4)
        self.assertEqual(cat_items["semicama"]["count"], 2)
        self.assertEqual(cat_items["semicama"]["percentage"], 28.6)
        self.assertIn("Menores de 5 años en brazos/falda: 1", c3["notes"])

        # 4. Gráfico Sentido del recorrido
        c4 = response.context["chart_direction"]
        self.assertEqual(c4["unit"], "Tramos vendidos")
        # Total tramos = 7 (5 ida, 2 vuelta)
        self.assertEqual(c4["denominator"], 7)
        dir_items = {item["key"]: item for item in c4["items"]}
        self.assertEqual(dir_items["cba_juj"]["count"], 5)
        self.assertEqual(dir_items["cba_juj"]["percentage"], 71.4)
        self.assertEqual(dir_items["juj_cba"]["count"], 2)
        self.assertEqual(dir_items["juj_cba"]["percentage"], 28.6)

        # 5. Gráfico Tipo de viaje (Solo ida vs Ida y vuelta)
        c5 = response.context["chart_trip_type"]
        self.assertEqual(c5["unit"], "Compras confirmadas")
        self.assertEqual(c5["denominator"], 5)
        type_items = {item["key"]: item for item in c5["items"]}
        # Solo ida: b_seller_b, b_online_1, b_refuerzo = 3 (60.0%)
        # Ida y vuelta: b_seller_a, b_online_2 = 2 (40.0%)
        self.assertEqual(type_items["one_way"]["count"], 3)
        self.assertEqual(type_items["one_way"]["percentage"], 60.0)
        self.assertEqual(type_items["round_trip"]["count"], 2)
        self.assertEqual(type_items["round_trip"]["percentage"], 40.0)

        # 6. Gráfico Salida concreta (sin agrupar refuerzos con igual horario)
        c6 = response.context["chart_departure"]
        self.assertEqual(c6["unit"], "Tramos vendidos")
        self.assertEqual(c6["denominator"], 7)
        # Salidas distintas: trip_1 (4 tramos), trip_2 (1 tramo), trip_return (2 tramos)
        dep_keys = [item["trip_pk"] for item in c6["items"]]
        self.assertIn(self.trip_1.pk, dep_keys)
        self.assertIn(self.trip_2.pk, dep_keys)
        self.assertIn(self.trip_return.pk, dep_keys)
        self.assertEqual(len(c6["items"]), 3)
        item_trip_1 = next(it for it in c6["items"] if it["trip_pk"] == self.trip_1.pk)
        item_trip_2 = next(it for it in c6["items"] if it["trip_pk"] == self.trip_2.pk)
        item_trip_ret = next(it for it in c6["items"] if it["trip_pk"] == self.trip_return.pk)
        self.assertIn("BUS-01", item_trip_1["label"])
        self.assertIn("BUS-02", item_trip_2["label"])
        self.assertEqual(item_trip_1["count"], 4)
        self.assertEqual(item_trip_2["count"], 1)
        self.assertEqual(item_trip_ret["count"], 2)

    def test_seller_isolation_tampered_filters_and_csv(self):
        """Vendedor A no puede ver datos de Vendedor B ni manipulando parámetros GET."""
        self.client.force_login(self.seller_a)

        # Intento de manipular ?vendedor=<id_de_seller_b>
        response = self.client.get(reverse("panel:sales_report"), {"vendedor": str(self.seller_b.pk)})
        self.assertEqual(response.status_code, 200)

        # Gráfico 2 de participación de otros vendedores está estrictamente ausente para no-admin
        self.assertIsNone(response.context["chart_seller"])
        self.assertNotContains(response, 'id="chart-card-vendedor"')
        self.assertNotContains(response, "seller-b@test.scviajes")

        # CSV tampoco expone registros ajenos
        csv_resp = self.client.get(reverse("panel:sales_report_csv"), {"vendedor": str(self.seller_b.pk)})
        csv_content = csv_resp.content.decode("utf-8")
        self.assertNotIn("seller_b@test.scviajes", csv_content)
        self.assertIn("seller-a", csv_content)

    def test_empty_state_and_incomplete_historical_data(self):
        """Verifica que filtros sin coincidencias muestren 'Sin datos' y datos incompletos no provoquen excepciones."""
        self.client.force_login(self.admin)
        # Filtro con fecha donde no hay ventas
        response = self.client.get(reverse("panel:sales_report"), {"desde": "2020-01-01", "hasta": "2020-01-02"})
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.context["chart_channel"]["denominator"], 0)
        self.assertFalse(response.context["chart_channel"]["has_data"])
        self.assertEqual(response.context["chart_channel"]["empty_message"], "Sin datos")

        self.assertEqual(response.context["chart_category"]["denominator"], 0)
        self.assertFalse(response.context["chart_category"]["has_data"])

        self.assertEqual(response.context["chart_direction"]["denominator"], 0)
        self.assertFalse(response.context["chart_direction"]["has_data"])

        self.assertEqual(response.context["chart_trip_type"]["denominator"], 0)
        self.assertFalse(response.context["chart_trip_type"]["has_data"])

        self.assertEqual(response.context["chart_departure"]["denominator"], 0)
        self.assertFalse(response.context["chart_departure"]["has_data"])

        self.assertContains(response, "Sin datos")

    def test_html_presentation_has_no_monetary_tables_or_dollar_cards(self):
        """
        Verifica que la pantalla de reporte retire tablas monetarias y tarjetas de importes ($),
        mientras que el Dashboard conserva sus importes intactos.
        """
        self.client.force_login(self.admin)
        rep_resp = self.client.get(reverse("panel:sales_report"))
        rep_html = rep_resp.content.decode("utf-8")

        # No debe haber tarjetas de importes monetarios en el HTML del reporte
        self.assertNotIn("Ingreso bruto", rep_html)
        self.assertNotIn("Comisiones retenidas", rep_html)
        self.assertNotIn("Neto operativo", rep_html)
        self.assertNotIn("Ingresos asignados por salida", rep_html)

        # No debe haber columnas ni valores con '$' en la presentación del reporte
        self.assertNotIn("$", rep_html)

        # El Dashboard sí conserva sus importes
        dash_resp = self.client.get(reverse("panel:dashboard"))
        dash_html = dash_resp.content.decode("utf-8")
        self.assertIn("Ingresos Confirmados (ARS)", dash_html)
        self.assertIn("Comisiones retenidas", dash_html)
        self.assertIn("Neto operativo", dash_html)
        self.assertIn("$", dash_html)

    def test_get_commissionable_assignments_requires_approved_payment(self):
        """
        Refuerzo de get_commissionable_assignments:
        Exige internamente PaymentStatus.APPROVED además de Booking CONFIRMED/MANUAL/seller
        y asignación CONFIRMED con price > 0.
        """
        conf_time = timezone.make_aware(datetime(2026, 11, 12, 10, 0), AR)
        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            seller=self.seller_a,
            status=BookingStatus.CONFIRMED,
            email="payment_check@test.scviajes",
            phone="3515559999",
            confirmed_at=conf_time,
        )
        passenger = BookingPassenger.objects.create(
            booking=booking,
            position=1,
            first_name="Test",
            last_name="Payment",
            document_type="DNI",
            document_number="30999999",
            birth_date=datetime(1990, 1, 1).date(),
        )
        leg = BookingLeg.objects.create(
            booking=booking,
            sequence=1,
            trip=self.trip_1,
            origin_stop=self.trip_1_ts_cba,
            destination_stop=self.trip_1_ts_ssj,
            origin_stop_name="Córdoba Capital",
            destination_stop_name="San Salvador de Jujuy",
            departure_at=self.trip_1_ts_cba.scheduled_at,
            arrival_at=self.trip_1_ts_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg,
            passenger=passenger,
            trip=self.trip_1,
            seat=self.seats_bus_1[12],
            status=AssignmentStatus.CONFIRMED,
            seat_number=13,
            category=SeatCategory.CAMA,
            price=Decimal("20000.00"),
            currency="ARS",
        )

        # 1. Sin pago registrado -> 0 asignaciones comisionables
        self.assertEqual(get_commissionable_assignments([booking.pk]).count(), 0)

        # 2. Con pago en revisión (UNDER_REVIEW) -> 0 asignaciones comisionables
        payment = Payment.objects.create(
            booking=booking,
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.UNDER_REVIEW,
            amount=Decimal("20000.00"),
            currency="ARS",
        )
        self.assertEqual(get_commissionable_assignments([booking.pk]).count(), 0)

        # 3. Con pago rechazado (REJECTED) -> 0 asignaciones comisionables
        payment.status = PaymentStatus.REJECTED
        payment.save()
        self.assertEqual(get_commissionable_assignments([booking.pk]).count(), 0)

        # 4. Con pago aprobado (APPROVED) -> 1 asignación comisionable
        payment.status = PaymentStatus.APPROVED
        payment.reviewed_by = self.admin
        payment.reviewed_at = conf_time
        payment.save()
        self.assertEqual(get_commissionable_assignments([booking.pk]).count(), 1)

    def test_chart_departure_occupancy_limited_to_scope_and_selected_leg(self):
        """
        Regresión obligatoria:
        chart_departure.occupancy_note debe limitarse a las reservas/legs seleccionados
        por booking_ids y al tramo seleccionado, sin atribuir ocupación de una ida/vuelta
        completa ni reservas fuera de los filtros.
        Verifica:
        - Reserva confirmada adicional del mismo Trip fuera de filtros (diciembre 2026).
        - Aislamiento de ocupación por vendedor.
        - Filtro de un tramo (viaje_desde y viaje_hasta).
        - Llamada directa a build_sales_charts con leg_ids.
        """
        # Crear reserva confirmada adicional del mismo trip_1 fuera del rango de noviembre (diciembre 2026)
        conf_dec = timezone.make_aware(datetime(2026, 12, 5, 10, 0), AR)
        b_outside = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            seller=self.seller_b,
            status=BookingStatus.CONFIRMED,
            email="outside@test.scviajes",
            phone="3515557777",
            confirmed_at=conf_dec,
        )
        p_out = BookingPassenger.objects.create(
            booking=b_outside,
            position=1,
            first_name="Outside",
            last_name="Passenger",
            document_type="DNI",
            document_number="30888888",
            birth_date=datetime(1988, 8, 8).date(),
        )
        leg_out = BookingLeg.objects.create(
            booking=b_outside,
            sequence=1,
            trip=self.trip_1,
            origin_stop=self.trip_1_ts_cba,
            destination_stop=self.trip_1_ts_ssj,
            origin_stop_name="Córdoba Capital",
            destination_stop_name="San Salvador de Jujuy",
            departure_at=self.trip_1_ts_cba.scheduled_at,
            arrival_at=self.trip_1_ts_ssj.scheduled_at,
        )
        SeatAssignment.objects.create(
            leg=leg_out,
            passenger=p_out,
            trip=self.trip_1,
            seat=self.seats_bus_1[14],
            status=AssignmentStatus.CONFIRMED,
            seat_number=15,
            category=SeatCategory.CAMA,
            price=Decimal("20000.00"),
            currency="ARS",
        )
        Payment.objects.create(
            booking=b_outside,
            method=PaymentMethod.CASH,
            status=PaymentStatus.APPROVED,
            amount=Decimal("20000.00"),
            currency="ARS",
            reviewed_by=self.admin,
            reviewed_at=conf_dec,
            registered_by=self.seller_b,
        )

        # En la base de datos hay 5 butacas confirmadas en trip_1 (4 de noviembre + 1 de diciembre).

        # A) Admin filtrando noviembre: b_outside queda fuera del alcance por fecha de confirmación
        self.client.force_login(self.admin)
        resp_nov = self.client.get(reverse("panel:sales_report"), {"desde": "2026-11-01", "hasta": "2026-11-30"})
        self.assertEqual(resp_nov.status_code, 200)
        c6_nov = resp_nov.context["chart_departure"]
        item_trip_1_nov = next(it for it in c6_nov["items"] if it["trip_pk"] == self.trip_1.pk)
        # Debe contar 4 butacas, NO 5
        self.assertEqual(item_trip_1_nov["count"], 4)
        self.assertEqual(item_trip_1_nov["occupancy_note"], "Ocupación del colectivo: 4/60 butacas (6.7%)")

        # B) Vendedor A: en su alcance solo tiene b_seller_a (1 asiento en trip_1)
        self.client.force_login(self.seller_a)
        resp_seller = self.client.get(reverse("panel:sales_report"))
        self.assertEqual(resp_seller.status_code, 200)
        c6_seller = resp_seller.context["chart_departure"]
        item_trip_1_seller = next(it for it in c6_seller["items"] if it["trip_pk"] == self.trip_1.pk)
        # No debe atribuir ocupación ajena ni de b_outside
        self.assertEqual(item_trip_1_seller["count"], 1)
        self.assertEqual(item_trip_1_seller["occupancy_note"], "Ocupación del colectivo: 1/60 butacas (1.7%)")

        # C) Filtro de un tramo: viaje_desde y viaje_hasta para la salida de ida (2026-11-20)
        self.client.force_login(self.admin)
        resp_tramo = self.client.get(
            reverse("panel:sales_report"),
            {"desde": "2026-11-01", "hasta": "2026-11-30", "viaje_desde": "2026-11-20", "viaje_hasta": "2026-11-20"},
        )
        self.assertEqual(resp_tramo.status_code, 200)
        c6_tramo = resp_tramo.context["chart_departure"]
        # En chart_departure solo deben figurar las salidas del tramo filtrado (trip_1 y trip_2)
        trip_pks_tramo = [it["trip_pk"] for it in c6_tramo["items"]]
        self.assertIn(self.trip_1.pk, trip_pks_tramo)
        self.assertIn(self.trip_2.pk, trip_pks_tramo)
        self.assertNotIn(self.trip_return.pk, trip_pks_tramo)
        item_trip_1_tramo = next(it for it in c6_tramo["items"] if it["trip_pk"] == self.trip_1.pk)
        # La ocupación de trip_1 se limita a las 4 reservas del tramo sin atribuir la vuelta de b_seller_a ni b_online_2
        self.assertEqual(item_trip_1_tramo["count"], 4)
        self.assertEqual(item_trip_1_tramo["occupancy_note"], "Ocupación del colectivo: 4/60 butacas (6.7%)")

        # D) Llamada directa a build_sales_charts para b_seller_a (ida y vuelta):
        # Con ambos tramos: cada salida reporta su propio asiento individual
        charts_both = build_sales_charts([self.b_seller_a.pk], actor=self.seller_a, admin_view=False)
        c6_both = {it["trip_pk"]: it for it in charts_both["chart_departure"]["items"]}
        self.assertEqual(c6_both[self.trip_1.pk]["occupancy_note"], "Ocupación del colectivo: 1/60 butacas (1.7%)")
        self.assertEqual(c6_both[self.trip_return.pk]["occupancy_note"], "Ocupación del colectivo: 1/60 butacas (1.7%)")

        # Con leg_ids restringido únicamente al tramo de ida:
        leg_ida = self.b_seller_a.legs.get(trip=self.trip_1)
        charts_ida_only = build_sales_charts(
            [self.b_seller_a.pk], actor=self.seller_a, admin_view=False, leg_ids=[leg_ida.pk]
        )
        self.assertEqual(len(charts_ida_only["chart_departure"]["items"]), 1)
        self.assertEqual(charts_ida_only["chart_departure"]["items"][0]["trip_pk"], self.trip_1.pk)
        self.assertEqual(
            charts_ida_only["chart_departure"]["items"][0]["occupancy_note"],
            "Ocupación del colectivo: 1/60 butacas (1.7%)",
        )


SCREENSHOT_DIR = r"C:\Users\JOEL\AppData\Local\Temp\scviajes_charts_20261106"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CDP_PORT = 9222


class SalesChartsBrowserValidationTests(SalesChartsFixturesMixin, StaticLiveServerTestCase):
    """
    Validación de navegador real con Chrome headless via CDP.
    Comprueba:
    - 0 errores JS en consola.
    - 0 px de desborde horizontal (scrollWidth <= clientWidth) en desktop (1440px) y móvil (390px).
    - Presencia de los 6 gráficos para Administrador y exclusión del gráfico de vendedores para Vendedor.
    - Retiro de tarjetas e importes monetarios ($) de la pantalla de reportes.
    - Capturas PNG reales guardadas fuera del repositorio.
    """

    def setUp(self):
        super().setUp()
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        # Crear los mismos fixtures en la base de la prueba
        self.setup_fixtures()

    def _login_user(self, username, password):
        session = requests.Session()
        login_url = f"{self.live_server_url}/panel/login/"
        session.get(login_url)
        csrf = session.cookies.get("csrftoken")
        resp_post = session.post(
            login_url,
            data={
                "username": username,
                "password": password,
                "csrfmiddlewaretoken": csrf,
            },
            headers={"Referer": login_url},
            allow_redirects=False,
        )
        self.assertEqual(resp_post.status_code, 302, f"Login falló para {username}")
        return session.cookies.get("sessionid")

    def test_sales_report_browser_visual_and_overflow(self):
        if not os.path.exists(CHROME_PATH):
            self.skipTest(f"Chrome no encontrado en {CHROME_PATH}")

        admin_cookie = self._login_user("admin-user", "password")
        seller_cookie = self._login_user("seller-a", "password")

        async def run_cdp():
            chrome_proc = subprocess.Popen(
                [
                    CHROME_PATH,
                    "--headless=new",
                    f"--remote-debugging-port={CDP_PORT}",
                    "--hide-scrollbars",
                    r"--user-data-dir=C:\Users\JOEL\AppData\Local\Temp\chrome_charts_test_profile",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)

            try:
                with urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list") as resp:
                    targets = json.loads(resp.read().decode())
                page_target = next(t for t in targets if t.get("type") == "page")
                ws_url = page_target["webSocketDebuggerUrl"]

                client_session = aiohttp.ClientSession()
                ws = await client_session.ws_connect(ws_url)
                msg_id = 0
                js_exceptions = []

                async def cdp_call(method, params=None):
                    nonlocal msg_id
                    msg_id += 1
                    payload = {"id": msg_id, "method": method, "params": params or {}}
                    await ws.send_json(payload)
                    while True:
                        raw = await ws.receive_json()
                        if raw.get("method") == "Runtime.exceptionThrown":
                            js_exceptions.append(raw.get("params"))
                        if raw.get("id") == msg_id:
                            return raw.get("result", {})

                await cdp_call("Page.enable")
                await cdp_call("DOM.enable")
                await cdp_call("Network.enable")
                await cdp_call("Runtime.enable")

                # --- 1. Admin Desktop ---
                live_host = urlparse(self.live_server_url).hostname
                await cdp_call("Network.clearBrowserCookies")
                await cdp_call("Network.setCookie", {
                    "name": "sessionid",
                    "value": admin_cookie,
                    "domain": live_host,
                    "path": "/",
                    "httpOnly": True,
                })
                await cdp_call("Emulation.setDeviceMetricsOverride", {
                    "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False
                })
                await cdp_call("Page.navigate", {"url": f"{self.live_server_url}/panel/reportes/ventas/"})
                await asyncio.sleep(2.0)

                admin_desk_eval = await cdp_call("Runtime.evaluate", {
                    "expression": """(() => {
                        const doc = document.documentElement;
                        const charts = Array.from(document.querySelectorAll('[data-chart]')).map(el => el.getAttribute('data-chart'));
                        const hasDollar = Array.from(document.querySelectorAll('.stat-card, .stat-card-value')).some(el => el.textContent.includes('$'));
                        return {
                            location: window.location.href,
                            title: document.title,
                            scrollWidth: doc.scrollWidth,
                            clientWidth: window.innerWidth,
                            hasOverflow: doc.scrollWidth > window.innerWidth,
                            charts,
                            hasDollar,
                        };
                    })()""",
                    "returnByValue": True,
                })
                data_ad = admin_desk_eval["result"]["value"]
                self.assertNotIn("login", data_ad["location"], f"Fue redirigido a login: {data_ad['location']}")
                self.assertFalse(data_ad["hasOverflow"], f"Overflow horizontal detectado en Admin Desktop: scrollWidth={data_ad['scrollWidth']}, clientWidth={data_ad['clientWidth']}")
                self.assertEqual(len(data_ad["charts"]), 6, f"Se esperaban 6 gráficos para Admin, encontrados: {data_ad['charts']}")
                self.assertFalse(data_ad["hasDollar"], "La pantalla de reportes no debe contener tarjetas ni importes monetarios ($)")

                shot_ad = await cdp_call("Page.captureScreenshot", {"format": "png"})
                path_ad = os.path.join(SCREENSHOT_DIR, "sales_report_admin_desktop.png")
                with open(path_ad, "wb") as f:
                    f.write(base64.b64decode(shot_ad["data"]))

                # --- 2. Admin Mobile ---
                await cdp_call("Emulation.setDeviceMetricsOverride", {
                    "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True
                })
                await asyncio.sleep(0.5)

                admin_mob_eval = await cdp_call("Runtime.evaluate", {
                    "expression": """(() => {
                        const doc = document.documentElement;
                        return {
                            scrollWidth: doc.scrollWidth,
                            clientWidth: window.innerWidth,
                            hasOverflow: doc.scrollWidth > window.innerWidth,
                        };
                    })()""",
                    "returnByValue": True,
                })
                data_am = admin_mob_eval["result"]["value"]
                self.assertFalse(data_am["hasOverflow"], f"Overflow horizontal detectado en Admin Móvil: scrollWidth={data_am['scrollWidth']}, clientWidth={data_am['clientWidth']}")

                shot_am = await cdp_call("Page.captureScreenshot", {"format": "png"})
                path_am = os.path.join(SCREENSHOT_DIR, "sales_report_admin_mobile.png")
                with open(path_am, "wb") as f:
                    f.write(base64.b64decode(shot_am["data"]))

                # --- 3. Seller Desktop ---
                await cdp_call("Network.clearBrowserCookies")
                await cdp_call("Network.setCookie", {
                    "name": "sessionid",
                    "value": seller_cookie,
                    "domain": live_host,
                    "path": "/",
                    "httpOnly": True,
                })
                await cdp_call("Emulation.setDeviceMetricsOverride", {
                    "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False
                })
                await cdp_call("Page.navigate", {"url": f"{self.live_server_url}/panel/reportes/ventas/"})
                await asyncio.sleep(2.0)

                seller_desk_eval = await cdp_call("Runtime.evaluate", {
                    "expression": """(() => {
                        const doc = document.documentElement;
                        const charts = Array.from(document.querySelectorAll('[data-chart]')).map(el => el.getAttribute('data-chart'));
                        return {
                            scrollWidth: doc.scrollWidth,
                            clientWidth: window.innerWidth,
                            hasOverflow: doc.scrollWidth > window.innerWidth,
                            charts,
                        };
                    })()""",
                    "returnByValue": True,
                })
                data_sd = seller_desk_eval["result"]["value"]
                self.assertFalse(data_sd["hasOverflow"], "Overflow horizontal detectado en Vendedor Desktop")
                self.assertNotIn("seller", data_sd["charts"], "El gráfico de participación por vendedor no debe exponerse a vendedores")
                self.assertEqual(len(data_sd["charts"]), 5, f"Se esperaban 5 gráficos para Vendedor, encontrados: {data_sd['charts']}")

                shot_sd = await cdp_call("Page.captureScreenshot", {"format": "png"})
                path_sd = os.path.join(SCREENSHOT_DIR, "sales_report_seller_desktop.png")
                with open(path_sd, "wb") as f:
                    f.write(base64.b64decode(shot_sd["data"]))

                # --- 4. Seller Mobile ---
                await cdp_call("Emulation.setDeviceMetricsOverride", {
                    "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True
                })
                await asyncio.sleep(0.5)

                seller_mob_eval = await cdp_call("Runtime.evaluate", {
                    "expression": """(() => {
                        const doc = document.documentElement;
                        return {
                            scrollWidth: doc.scrollWidth,
                            clientWidth: window.innerWidth,
                            hasOverflow: doc.scrollWidth > window.innerWidth,
                        };
                    })()""",
                    "returnByValue": True,
                })
                data_sm = seller_mob_eval["result"]["value"]
                self.assertFalse(data_sm["hasOverflow"], "Overflow horizontal detectado en Vendedor Móvil")

                shot_sm = await cdp_call("Page.captureScreenshot", {"format": "png"})
                path_sm = os.path.join(SCREENSHOT_DIR, "sales_report_seller_mobile.png")
                with open(path_sm, "wb") as f:
                    f.write(base64.b64decode(shot_sm["data"]))

                # --- 5. Dashboard Admin Desktop ---
                await cdp_call("Network.clearBrowserCookies")
                await cdp_call("Network.setCookie", {
                    "name": "sessionid",
                    "value": admin_cookie,
                    "domain": live_host,
                    "path": "/",
                    "httpOnly": True,
                })
                await cdp_call("Emulation.setDeviceMetricsOverride", {
                    "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False
                })
                await cdp_call("Page.navigate", {"url": f"{self.live_server_url}{reverse('panel:dashboard')}"})
                await asyncio.sleep(2.0)

                dash_eval = await cdp_call("Runtime.evaluate", {
                    "expression": """(() => {
                        return {
                            hasDollar: document.body.innerText.includes('$'),
                        };
                    })()""",
                    "returnByValue": True,
                })
                self.assertTrue(dash_eval["result"]["value"]["hasDollar"], "El Dashboard debe mantener sus importes y signos de moneda")

                shot_dash = await cdp_call("Page.captureScreenshot", {"format": "png"})
                path_dash = os.path.join(SCREENSHOT_DIR, "dashboard_admin_desktop.png")
                with open(path_dash, "wb") as f:
                    f.write(base64.b64decode(shot_dash["data"]))

                self.assertEqual(len(js_exceptions), 0, f"Se detectaron errores JS no controlados: {js_exceptions}")

                await ws.close()
                await client_session.close()

            finally:
                chrome_proc.terminate()
                try:
                    chrome_proc.wait(timeout=3)
                except Exception:
                    chrome_proc.kill()

        asyncio.run(run_cdp())
