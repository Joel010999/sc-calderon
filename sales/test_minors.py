from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from cash_register.models import CashSession
from panel.report_services import build_sales_report
from payments.services import calculate_booking_total, register_cash_payment
from sales.minors import MINOR_FREE_AGE_YEARS, is_free_lap_infant, required_seat_count
from sales.models import AssignmentStatus, Booking, BookingStatus
from sales.services import confirm_booking, create_manual_booking, create_online_booking, get_trip_availability
from sales.tests import SalesBaseTestCase

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class MinorPassengerPolicyTests(SalesBaseTestCase):
    def _passenger(self, position, birth_date, mother=None):
        return {
            "first_name": f"Persona{position}",
            "last_name": "Prueba",
            "document_type": "DNI",
            "document_number": f"9900000{position}",
            "birth_date": birth_date.isoformat() if isinstance(birth_date, date) else birth_date,
            "nationality": "Argentina",
            "accompanied_by_position": mother,
        }

    def _leg(self, seats):
        return {
            "trip": self.trip_outbound,
            "origin_stop": self.ts_out_cba,
            "destination_stop": self.ts_out_ssj,
            "seats": seats,
        }

    def test_threshold_constant_is_five_years(self):
        self.assertEqual(MINOR_FREE_AGE_YEARS, 5)

    def test_age_boundaries_under_five_are_free_and_five_or_older_require_seat(self):
        purchase_at = datetime(2026, 10, 25, 14, 0, tzinfo=AR_TZ)
        purchase_date = date(2026, 10, 25)

        # 0, 1, 2, 3 y 4 años cumplidos (incluyendo el día anterior a cumplir 5) son gratuitos
        for years_ago in (0, 1, 2, 3, 4):
            birth = purchase_date.replace(year=purchase_date.year - years_ago)
            with self.subTest(years_ago=years_ago, birth=birth):
                self.assertTrue(is_free_lap_infant(birth, purchase_at))

        one_day_before_turning_five = purchase_date.replace(year=purchase_date.year - 5) + timedelta(days=1)
        self.assertTrue(is_free_lap_infant(one_day_before_turning_five, purchase_at))

        # Exactamente 5 años cumplidos en la fecha de compra y mayores ya no son gratuitos
        exactly_five = purchase_date.replace(year=purchase_date.year - 5)
        one_day_after_five = exactly_five - timedelta(days=1)
        six_years = purchase_date.replace(year=purchase_date.year - 6)
        for birth in (exactly_five, one_day_after_five, six_years):
            with self.subTest(non_free_birth=birth):
                self.assertFalse(is_free_lap_infant(birth, purchase_at))

    def test_age_boundary_uses_argentina_timezone_of_purchase_instant(self):
        # 2026-10-26 01:30 UTC es todavía 2026-10-25 22:30 en Argentina (-03:00)
        late_evening_ar_in_utc = datetime(2026, 10, 26, 1, 30, tzinfo=dt_timezone.utc)
        # Menor nacido el 2021-10-26: en Argentina aún tiene 4 años el 25/10 a las 22:30
        birth_oct_26 = date(2021, 10, 26)
        self.assertTrue(is_free_lap_infant(birth_oct_26, late_evening_ar_in_utc))

        # A las 00:05 del 2026-10-26 en Argentina ya cumplió 5 años
        just_after_midnight_ar = datetime(2026, 10, 26, 0, 5, tzinfo=AR_TZ)
        self.assertFalse(is_free_lap_infant(birth_oct_26, just_after_midnight_ar))

    def test_lap_infant_up_to_four_years_is_passenger_with_zero_price_zero_commission_and_no_seat(self):
        purchase_at = timezone.now()
        local_purchase_date = timezone.localtime(purchase_at, AR_TZ).date()
        # Menor de 4 años (que con el umbral anterior de 2 años pagaba y ahora es gratuito)
        four_year_old_birth = local_purchase_date.replace(year=local_purchase_date.year - 4)

        avail_before = get_trip_availability(
            self.trip_outbound, self.ts_out_cba, self.ts_out_ssj, now=purchase_at, mutate=False
        )
        booking = create_manual_booking(
            seller=self.seller_user,
            email="familia-cuatro@correo.com",
            legs=[self._leg([self.seat_cama_1])],
            passengers_data=[
                self._passenger(1, date(1990, 1, 1)),
                self._passenger(2, four_year_old_birth, mother=1),
            ],
            now=purchase_at,
        )

        adult, infant = booking.passengers.order_by("position")
        self.assertEqual(booking.passengers.count(), 2)
        self.assertFalse(adult.is_lap_infant)
        self.assertTrue(infant.is_lap_infant)
        self.assertEqual(infant.accompanied_by, adult)
        self.assertEqual(booking.legs.first().seat_assignments.count(), 1)
        self.assertEqual(booking.legs.first().seat_assignments.first().passenger, adult)
        self.assertEqual(infant.seat_assignments.count(), 0)
        self.assertEqual(calculate_booking_total(booking), Decimal("12000.00"))

        avail_after = get_trip_availability(
            self.trip_outbound, self.ts_out_cba, self.ts_out_ssj, now=purchase_at, mutate=False
        )
        self.assertEqual(avail_after["occupied_seat_count"], avail_before["occupied_seat_count"] + 1)

        # Confirmar mediante pago en efectivo y verificar precio cero y comisión cero para el menor
        CashSession.objects.create(opened_by=self.seller_user, opening_amount=Decimal("0.00"))
        payment = register_cash_payment(booking_or_id=booking, seller=self.seller_user, now=purchase_at)
        self.assertEqual(payment.amount, Decimal("12000.00"))

        report = build_sales_report({}, actor=self.admin_user)
        self.assertEqual(report["total_amount"], Decimal("12000.00"))
        self.assertEqual(report["passenger_count"], 2)
        self.assertEqual(report["commissionable_units"], 1)
        self.assertEqual(report["commission_total"], Decimal("5000.00"))

    def test_exact_fifth_birthday_and_later_purchase_require_seat_and_fare(self):
        purchase_at = timezone.now()
        local_purchase_date = timezone.localtime(purchase_at, AR_TZ).date()
        exactly_five = local_purchase_date.replace(year=local_purchase_date.year - 5)
        self.assertFalse(is_free_lap_infant(exactly_five, purchase_at))

        # Con 5 años cumplidos no puede declarar madre acompañante ni viajar sin butaca
        with self.assertRaises(ValidationError):
            create_online_booking(
                email="cumple5@correo.com",
                legs=[self._leg([self.seat_cama_1])],
                passengers_data=[self._passenger(1, date(1990, 1, 1)), self._passenger(2, exactly_five, mother=1)],
                now=purchase_at,
            )

        # Tampoco puede omitir butaca aunque no declare acompañante
        with self.assertRaises(ValidationError):
            create_online_booking(
                email="cumple5-sin-butaca@correo.com",
                legs=[self._leg([self.seat_cama_1])],
                passengers_data=[self._passenger(1, date(1990, 1, 1)), self._passenger(2, exactly_five)],
                now=purchase_at,
            )

        # Compra válida asignando butaca y tarifa propia para el niño de 5 años
        paid_booking = create_online_booking(
            email="cumple5-con-butaca@correo.com",
            legs=[self._leg([self.seat_cama_1, self.seat_cama_2])],
            passengers_data=[self._passenger(1, date(1990, 1, 1)), self._passenger(2, exactly_five)],
            now=purchase_at,
        )
        child = paid_booking.passengers.get(position=2)
        self.assertFalse(child.is_lap_infant)
        self.assertIsNone(child.accompanied_by)
        self.assertEqual(paid_booking.legs.first().seat_assignments.count(), 2)
        self.assertEqual(calculate_booking_total(paid_booking), Decimal("24000.00"))

        older_birth = exactly_five - timedelta(days=1)
        self.assertFalse(is_free_lap_infant(older_birth, purchase_at))

    def test_round_trip_purchased_before_turning_five_keeps_gratuity_in_both_legs(self):
        purchase_at = timezone.now()
        local_purchase_date = timezone.localtime(purchase_at, AR_TZ).date()
        # Cumple 5 años mañana (antes de la salida de ida y antes del regreso)
        eve_of_fifth_birthday = local_purchase_date.replace(year=local_purchase_date.year - 5) + timedelta(days=1)

        booking = create_online_booking(
            email="ida-vuelta-menor5@correo.com",
            legs=[
                self._leg([self.seat_cama_1]),
                {
                    "trip": self.trip_return,
                    "origin_stop": self.ts_ret_ssj,
                    "destination_stop": self.ts_ret_cba,
                    "seats": [self.seat_semi_3],
                },
            ],
            passengers_data=[
                self._passenger(1, date(1990, 1, 1)),
                self._passenger(2, eve_of_fifth_birthday, mother=1),
            ],
            now=purchase_at,
        )

        infant = booking.passengers.get(position=2)
        self.assertTrue(infant.is_lap_infant)
        self.assertEqual(booking.legs.count(), 2)
        self.assertEqual(sum(leg.seat_assignments.count() for leg in booking.legs.all()), 2)
        self.assertEqual(calculate_booking_total(booking), Decimal("21500.00"))

        # Al confirmarse después de cumplir 5 años, el snapshot gratuito no se recalcula
        confirmed = confirm_booking(booking, now=purchase_at + timedelta(minutes=10))
        infant.refresh_from_db()
        self.assertEqual(confirmed.status, BookingStatus.CONFIRMED)
        self.assertTrue(infant.is_lap_infant)
        self.assertEqual(infant.seat_assignments.count(), 0)

    def test_separate_return_purchased_after_turning_five_requires_seat_and_fare(self):
        outbound_purchase_at = timezone.now()
        local_outbound_date = timezone.localtime(outbound_purchase_at, AR_TZ).date()
        # En la compra de ida le falta 1 día para cumplir 5 años
        child_birth = local_outbound_date.replace(year=local_outbound_date.year - 5) + timedelta(days=1)

        outbound_booking = create_online_booking(
            email="ida-antes-de-5@correo.com",
            legs=[self._leg([self.seat_cama_1])],
            passengers_data=[
                self._passenger(1, date(1990, 1, 1)),
                self._passenger(2, child_birth, mother=1),
            ],
            now=outbound_purchase_at,
        )
        self.assertTrue(outbound_booking.passengers.get(position=2).is_lap_infant)
        self.assertEqual(outbound_booking.legs.first().seat_assignments.count(), 1)

        # Al día siguiente cumple 5 años y compra el regreso por separado
        return_purchase_at = outbound_purchase_at + timedelta(days=1)
        return_leg_one_seat = [{
            "trip": self.trip_return,
            "origin_stop": self.ts_ret_ssj,
            "destination_stop": self.ts_ret_cba,
            "seats": [self.seat_semi_3],
        }]
        with self.assertRaises(ValidationError):
            create_online_booking(
                email="vuelta-despues-de-5-invalida@correo.com",
                legs=return_leg_one_seat,
                passengers_data=[
                    self._passenger(1, date(1990, 1, 1)),
                    self._passenger(2, child_birth, mother=1),
                ],
                now=return_purchase_at,
            )

        # El regreso comprado por separado requiere 2 butacas y tarifa para ambos
        return_booking = create_online_booking(
            email="vuelta-despues-de-5-valida@correo.com",
            legs=[{
                "trip": self.trip_return,
                "origin_stop": self.ts_ret_ssj,
                "destination_stop": self.ts_ret_cba,
                "seats": [self.seat_cama_2, self.seat_semi_3],
            }],
            passengers_data=[
                self._passenger(1, date(1990, 1, 1)),
                self._passenger(2, child_birth),
            ],
            now=return_purchase_at,
        )
        self.assertFalse(return_booking.passengers.get(position=2).is_lap_infant)
        self.assertEqual(return_booking.legs.first().seat_assignments.count(), 2)
        self.assertEqual(calculate_booking_total(return_booking), Decimal("21500.00"))

        # La compra de ida original conserva intacto su snapshot gratuito
        outbound_booking.refresh_from_db()
        self.assertTrue(outbound_booking.passengers.get(position=2).is_lap_infant)
        self.assertEqual(calculate_booking_total(outbound_booking), Decimal("12000.00"))

    def test_free_minor_must_reference_a_seated_passenger_in_same_purchase(self):
        purchase_at = timezone.now()
        local_purchase_date = timezone.localtime(purchase_at, AR_TZ).date()
        infant_birth = local_purchase_date.replace(year=local_purchase_date.year - 3)
        for mother in (None, "", 0, 2, 99, "invalido"):
            with self.subTest(mother=mother), self.assertRaises(ValidationError):
                create_online_booking(
                    email="relacion-invalida@correo.com",
                    legs=[self._leg([self.seat_cama_1])],
                    passengers_data=[
                        self._passenger(1, date(1990, 1, 1)),
                        self._passenger(2, infant_birth, mother=mother),
                    ],
                    now=purchase_at,
                )

        # Dos menores gratuitos no pueden acompañarse mutuamente sin un pasajero con butaca
        with self.assertRaises(ValidationError):
            create_online_booking(
                email="dos-menores-sin-adulto@correo.com",
                legs=[self._leg([])],
                passengers_data=[
                    self._passenger(1, infant_birth, mother=2),
                    self._passenger(2, infant_birth, mother=1),
                ],
                now=purchase_at,
            )

    def test_free_lap_infant_remains_zero_amount_when_departure_fare_changes(self):
        """Los menores gratuitos sin asiento permanecen en importe cero aunque cambie la tarifa de la salida."""
        purchase_at = timezone.now()
        local_purchase_date = timezone.localtime(purchase_at, AR_TZ).date()
        infant_birth = local_purchase_date.replace(year=local_purchase_date.year - 4)
        booking = create_online_booking(
            email="menor-tarifa-cambio@correo.com",
            legs=[self._leg([self.seat_cama_1])],
            passengers_data=[
                self._passenger(1, date(1990, 1, 1)),
                self._passenger(2, infant_birth, mother=1),
            ],
            now=purchase_at,
        )

        # Se modifica la tarifa CAMA de la salida antes de partir
        self.fare_out_cba_ssj_cama.amount = Decimal("28000.00")
        self.fare_out_cba_ssj_cama.save()

        # La reserva previa mantiene el importe del adulto (12000.00) y 0.00 para el menor en brazos
        self.assertEqual(calculate_booking_total(booking), Decimal("12000.00"))
        prices = list(booking.legs.first().seat_assignments.values_list("price", flat=True))
        self.assertEqual(prices, [Decimal("12000.00")])

    def test_historical_bookings_are_not_recalculated_after_threshold_change(self):
        """Reservas históricas con menor de 3 años que abonó butaca bajo el umbral previo conservan su asiento y tarifa."""
        purchase_at = timezone.now()
        local_purchase_date = timezone.localtime(purchase_at, AR_TZ).date()
        three_year_old_birth = local_purchase_date.replace(year=local_purchase_date.year - 3)

        # Simulamos una reserva histórica creada bajo la regla anterior (menor de 3 años con butaca y tarifa pagada)
        historical_booking = create_manual_booking(
            seller=self.seller_user,
            email="historico-3-anios@correo.com",
            legs=[self._leg([self.seat_cama_1, self.seat_semi_3])],
            passengers_data=[
                self._passenger(1, date(1988, 5, 10)),
                self._passenger(2, date(1990, 5, 10)),
            ],
            now=purchase_at,
        )
        historical_child = historical_booking.passengers.get(position=2)
        historical_child.birth_date = three_year_old_birth
        historical_child.is_lap_infant = False
        historical_child.save(update_fields=["birth_date", "is_lap_infant", "updated_at"])

        # Confirmar y cobrar la reserva histórica no recalcula is_lap_infant ni elimina su butaca ni su precio
        CashSession.objects.create(opened_by=self.seller_user, opening_amount=Decimal("0.00"))
        payment = register_cash_payment(booking_or_id=historical_booking, seller=self.seller_user, now=purchase_at)
        historical_child.refresh_from_db()
        self.assertFalse(historical_child.is_lap_infant)
        self.assertEqual(historical_child.seat_assignments.filter(status=AssignmentStatus.CONFIRMED).count(), 1)
        self.assertEqual(payment.amount, Decimal("21500.00"))

        report = build_sales_report({}, actor=self.admin_user)
        self.assertEqual(report["commissionable_units"], 2)
        self.assertEqual(report["commission_total"], Decimal("10000.00"))

    def test_public_checkout_and_panel_and_manifest_respect_under_five_threshold(self):
        today = timezone.localtime(timezone.now(), AR_TZ).date()
        four_year_old_birth = today.replace(year=today.year - 4)
        exactly_five_birth = today.replace(year=today.year - 5)
        self.assertEqual(
            required_seat_count(
                [self._passenger(1, date(1990, 1, 1)), self._passenger(2, four_year_old_birth, mother=1)],
                timezone.now(),
            ),
            1,
        )

        # 1. Checkout público: menor de 4 años viaja sin butaca (1 sola butaca requerida)
        public_client = Client()
        response = public_client.post(
            reverse("crear_reserva"),
            {
                "trip_type": "oneway",
                "passengers_count": "2",
                "outbound_trip": str(self.trip_outbound.pk),
                "outbound_origin": str(self.ts_out_cba.pk),
                "outbound_destination": str(self.ts_out_ssj.pk),
                "outbound_seats": [str(self.seat_cama_1.pk)],
                "email": "checkout-menor4@correo.com",
                "phone": "3515551111",
                "passenger_1_first_name": "Laura",
                "passenger_1_last_name": "Gómez",
                "passenger_1_document_type": "DNI",
                "passenger_1_document_number": "32111222",
                "passenger_1_birth_date": "1990-04-10",
                "passenger_1_nationality": "Argentina",
                "passenger_1_gender": "Femenino",
                "passenger_1_accompanied_by_position": "",
                "passenger_2_first_name": "Mateo",
                "passenger_2_last_name": "Gómez",
                "passenger_2_document_type": "DNI",
                "passenger_2_document_number": "56111222",
                "passenger_2_birth_date": four_year_old_birth.isoformat(),
                "passenger_2_nationality": "Argentina",
                "passenger_2_gender": "Masculino",
                "passenger_2_accompanied_by_position": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        online_booking = Booking.objects.get(email="checkout-menor4@correo.com")
        self.assertTrue(online_booking.passengers.get(position=2).is_lap_infant)
        self.assertEqual(online_booking.legs.first().seat_assignments.count(), 1)

        # Checkout público rechaza declarar acompañante cuando cumple 5 años hoy
        invalid_public = public_client.post(
            reverse("crear_reserva"),
            {
                "trip_type": "oneway",
                "passengers_count": "2",
                "outbound_trip": str(self.trip_outbound.pk),
                "outbound_origin": str(self.ts_out_cba.pk),
                "outbound_destination": str(self.ts_out_ssj.pk),
                "outbound_seats": [str(self.seat_cama_2.pk)],
                "email": "checkout-cumple5@correo.com",
                "phone": "3515551111",
                "passenger_1_first_name": "Laura",
                "passenger_1_last_name": "Gómez",
                "passenger_1_document_type": "DNI",
                "passenger_1_document_number": "32111222",
                "passenger_1_birth_date": "1990-04-10",
                "passenger_1_nationality": "Argentina",
                "passenger_2_first_name": "Mateo",
                "passenger_2_last_name": "Gómez",
                "passenger_2_document_type": "DNI",
                "passenger_2_document_number": "56111222",
                "passenger_2_birth_date": exactly_five_birth.isoformat(),
                "passenger_2_nationality": "Argentina",
                "passenger_2_accompanied_by_position": "1",
            },
        )
        self.assertEqual(invalid_public.status_code, 302)
        self.assertFalse(Booking.objects.filter(email="checkout-cumple5@correo.com").exists())

        # 2. Panel interno: creación manual con menor de 4 años y visualización en manifiesto al confirmar
        panel_client = Client()
        panel_client.force_login(self.seller_user)
        panel_resp = panel_client.post(
            reverse("panel:booking_create"),
            {
                "action": "create_booking",
                "trip_type": "oneway",
                "passengers_count": "2",
                "outbound_trip": str(self.trip_outbound.pk),
                "outbound_origin": str(self.ts_out_cba.pk),
                "outbound_destination": str(self.ts_out_ssj.pk),
                "outbound_seats": [str(self.seat_semi_3.pk)],
                "contact_email": "panel-menor4@correo.com",
                "contact_phone": "3884445555",
                "p_1_first_name": "Carla",
                "p_1_last_name": "Díaz",
                "p_1_document_type": "DNI",
                "p_1_document_number": "33222333",
                "p_1_birth_date": "1991-06-15",
                "p_1_nationality": "Argentina",
                "p_1_accompanied_by_position": "",
                "p_2_first_name": "Sofía",
                "p_2_last_name": "Díaz",
                "p_2_document_type": "DNI",
                "p_2_document_number": "57222333",
                "p_2_birth_date": four_year_old_birth.isoformat(),
                "p_2_nationality": "Argentina",
                "p_2_accompanied_by_position": "1",
            },
            secure=True,
        )
        self.assertEqual(panel_resp.status_code, 302)
        panel_booking = Booking.objects.get(email="panel-menor4@correo.com")
        confirm_booking(panel_booking)

        manifest_resp = panel_client.get(
            reverse("panel:trip_manifest", kwargs={"trip_pk": self.trip_outbound.pk}),
            secure=True,
        )
        self.assertEqual(manifest_resp.status_code, 200)
        self.assertContains(manifest_resp, "Sofía Díaz")
        self.assertContains(manifest_resp, "Menor gratuito en falda")
        self.assertContains(manifest_resp, "Sin butaca")
