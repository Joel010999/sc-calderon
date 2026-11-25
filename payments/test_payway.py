"""Tests del servicio Payway (Decidir2) con cliente mock.

Cubre: aprobado, rechazado, pendiente, timeout antes/después del cobro,
respuesta perdida (conciliación por site_transaction_id), reintento,
cambio de medio de pago, importe manipulado y aprobación tardía.

El entorno sandbox real permanece pendiente de credenciales; estos tests
usan un cliente simulado y no validan al proveedor.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from operations.models import (
    Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop,
)
from sales.exceptions import BookingExpiredError, InvalidBookingError
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)
from payments.exceptions import PaymentDuplicateError, PaymentError
from payments.models import (
    Payment,
    PaymentGatewayEvent,
    PaymentMethod,
    PaymentStatus,
)
from payments.payway import (
    _apply_payway_result,
    execute_payway_payment,
    payway_configured,
    payway_site_transaction_id,
    reconcile_pending_payway_payments,
    start_payway_payment,
)


PAYWAY_SETTINGS = dict(
    PAYWAY_ENABLED=True,
    PAYWAY_PUBLIC_API_KEY="pk_test",
    PAYWAY_PRIVATE_API_KEY="sk_test",
    PAYWAY_PAYMENT_METHODS={"1": "Visa crédito", "31": "Visa débito"},
)


class FakePaywayClient:
    """Cliente simulado: nunca hace red."""

    def __init__(self, create_result=None, create_error=None, get_result=None,
                 find_result=None, get_error=None, find_error=None):
        self.create_result = create_result
        self.create_error = create_error
        self.get_result = get_result
        self.find_result = find_result
        self.get_error = get_error
        self.find_error = find_error
        self.create_calls = 0
        self.find_calls = 0
        self.last_payload = None

    def create_payment(self, payload):
        self.create_calls += 1
        self.last_payload = payload
        if self.create_error:
            raise self.create_error
        return self.create_result

    def get_payment(self, payment_id):
        if self.get_error:
            raise self.get_error
        return self.get_result

    def find_by_site_operation_id(self, site_transaction_id):
        self.find_calls += 1
        if self.find_error:
            raise self.find_error
        return self.find_result


def approved_remote(payment, remote_id="9001"):
    return {
        "id": remote_id,
        "site_transaction_id": payway_site_transaction_id(payment),
        "status": "approved",
        "status_details": {},
        "amount": int(payment.amount * 100),
        "currency": "ARS",
        "payment_method_id": 1,
    }


@override_settings(**PAYWAY_SETTINGS)
class PaywayBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.stop_a = Stop.objects.create(name="Origen", code="ORG")
        cls.stop_b = Stop.objects.create(name="Destino", code="DST")
        cls.route = Route.objects.create(name="R", code="R1", is_active=True)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_a, sequence=1,
                                 allows_boarding=True, allows_alighting=False)
        RouteStop.objects.create(route=cls.route, stop=cls.stop_b, sequence=2,
                                 allows_boarding=False, allows_alighting=True)
        cls.bus = Bus.objects.create(code="B1", is_active=True)
        cls.seat = Seat.objects.create(
            bus=cls.bus, number=1, category=SeatCategory.SEMI_CAMA,
            deck=Seat.Deck.LOWER, position_x=1, position_y=1, is_active=True,
        )
        dep = timezone.now() + timedelta(days=2)
        arr = dep + timedelta(hours=4)
        cls.trip = Trip.objects.create(route=cls.route, bus=cls.bus,
                                       departure_at=dep, status=Trip.Status.SCHEDULED)
        cls.ts_a = TripStop.objects.create(trip=cls.trip, stop=cls.stop_a, sequence=1,
                                           scheduled_at=dep, allows_boarding=True,
                                           allows_alighting=False)
        cls.ts_b = TripStop.objects.create(trip=cls.trip, stop=cls.stop_b, sequence=2,
                                           scheduled_at=arr, allows_boarding=False,
                                           allows_alighting=True)
        cls.fare = TripFare.objects.create(trip=cls.trip, seat_category=SeatCategory.SEMI_CAMA,
                                           amount=Decimal("12500.00"), is_active=True)

    def create_online_booking(self, minutes=15):
        booking = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            expires_at=timezone.now() + timedelta(minutes=minutes),
        )
        leg = BookingLeg.objects.create(
            booking=booking, sequence=1, trip=self.trip,
            origin_stop=self.ts_a, destination_stop=self.ts_b,
            origin_stop_name="Origen", destination_stop_name="Destino",
            departure_at=self.ts_a.scheduled_at, arrival_at=self.ts_b.scheduled_at,
        )
        pax = BookingPassenger.objects.create(
            booking=booking, position=1, first_name="Ana", last_name="Test",
            document_type="DNI", document_number="30000001",
            birth_date=date(1990, 1, 1), nationality="Argentina",
        )
        SeatAssignment.objects.create(
            leg=leg, passenger=pax, trip=self.trip, seat=self.seat,
            status=AssignmentStatus.HELD, seat_number=self.seat.number,
            category=self.seat.category, price=self.fare.amount, currency="ARS",
        )
        return booking


class PaywayServiceTests(PaywayBase):
    def setUp(self):
        self.booking = self.create_online_booking()
        self.now = timezone.now()

    # ---------- start ----------

    def test_start_creates_pending_payment_before_provider_contact(self):
        payment = start_payway_payment(booking_or_id=self.booking, now=self.now)
        self.assertEqual(payment.status, PaymentStatus.PENDING)
        self.assertEqual(payment.method, PaymentMethod.PAYWAY)
        self.assertEqual(payment.currency, "ARS")
        self.assertEqual(payment.reference, payway_site_transaction_id(payment))
        self.assertTrue(len(payment.reference) <= 39)

    def test_start_reuses_existing_pending_payment(self):
        p1 = start_payway_payment(booking_or_id=self.booking, now=self.now)
        p2 = start_payway_payment(booking_or_id=self.booking, now=self.now)
        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(Payment.objects.count(), 1)

    def test_start_blocked_by_other_active_method(self):
        Payment.objects.create(
            booking=self.booking, method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.AWAITING_VOUCHER, amount=Decimal("12500.00"),
            currency="ARS", proof_deadline_at=self.now + timedelta(minutes=5),
        )
        with self.assertRaises(PaymentDuplicateError):
            start_payway_payment(booking_or_id=self.booking, now=self.now)

    def test_payway_pending_blocks_new_transfer(self):
        start_payway_payment(booking_or_id=self.booking, now=self.now)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    booking=self.booking, method=PaymentMethod.BANK_TRANSFER,
                    status=PaymentStatus.AWAITING_VOUCHER, amount=Decimal("12500.00"),
                    currency="ARS", proof_deadline_at=self.now + timedelta(minutes=5),
                )

    def test_start_rejects_expired_booking(self):
        self.booking.expires_at = self.now - timedelta(minutes=1)
        self.booking.save(update_fields=["expires_at"])
        with self.assertRaises(BookingExpiredError):
            start_payway_payment(booking_or_id=self.booking, now=self.now)

    def test_start_rejects_internal_booking(self):
        self.booking.channel = BookingChannel.MANUAL
        self.booking.save(update_fields=["channel"])
        with self.assertRaises(InvalidBookingError):
            start_payway_payment(booking_or_id=self.booking, now=self.now)

    @override_settings(PAYWAY_ENABLED=False)
    def test_disabled_raises(self):
        with self.assertRaises(PaymentError):
            start_payway_payment(booking_or_id=self.booking, now=self.now)

    # ---------- execute: aprobado / rechazado / pendiente ----------

    def _pending_payment(self):
        return start_payway_payment(booking_or_id=self.booking, now=self.now)

    def test_approved_confirms_booking_and_enqueues_tickets(self):
        payment = self._pending_payment()
        client = FakePaywayClient(create_result=approved_remote(payment))
        outcome, _detail = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "CONFIRMED")
        payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(payment.external_payment_id, "9001")
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.assertTrue(
            SeatAssignment.objects.filter(
                leg__booking=self.booking, status=AssignmentStatus.CONFIRMED
            ).exists()
        )
        self.assertIsNotNone(getattr(self.booking, "ticket_fulfillment", None))
        # El cargo usó el importe server-side en unidades mínimas.
        self.assertEqual(client.last_payload["amount"], int(payment.amount * 100))
        self.assertEqual(client.last_payload["installments"], 1)
        self.assertEqual(client.last_payload["payment_type"], "single")

    def test_rejected_keeps_hold_and_allows_new_attempt(self):
        payment = self._pending_payment()
        remote = approved_remote(payment)
        remote["status"] = "rejected"
        client = FakePaywayClient(create_result=remote)
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "REJECTED")
        payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.REJECTED)
        self.assertEqual(self.booking.status, BookingStatus.HELD)
        new_payment = start_payway_payment(booking_or_id=self.booking, now=self.now)
        self.assertNotEqual(new_payment.pk, payment.pk)

    def test_pending_provider_status_stays_pending(self):
        payment = self._pending_payment()
        remote = approved_remote(payment)
        remote["status"] = "pending"
        client = FakePaywayClient(create_result=remote)
        outcome, _detail = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "STILL_PENDING")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_unknown_provider_status_is_not_rejection(self):
        payment = self._pending_payment()
        remote = approved_remote(payment)
        remote["status"] = "frobnicate"
        client = FakePaywayClient(create_result=remote)
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        payment.refresh_from_db()
        self.assertNotEqual(outcome, "REJECTED")
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    # ---------- timeout / respuesta perdida / conciliación ----------

    def test_timeout_then_reconcile_finds_approved(self):
        """Timeout tras el cobro: la conciliación por site_transaction_id
        recupera el pago aprobado sin un nuevo POST."""
        payment = self._pending_payment()
        client = FakePaywayClient(
            create_error=PaymentError("timeout"),
            find_result=[approved_remote(payment, remote_id="777")],
        )
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "CONFIRMED")
        self.assertEqual(client.create_calls, 1)
        payment.refresh_from_db()
        self.assertEqual(payment.external_payment_id, "777")

    def test_timeout_then_empty_list_stays_blocked(self):
        """Lista vacía no prueba ausencia de cobro: el intento queda
        desconocido y bloquea otro cobro, sin reintentar el POST."""
        payment = self._pending_payment()
        client = FakePaywayClient(
            create_error=PaymentError("timeout"),
            find_result=[],
        )
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "STILL_UNKNOWN")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)
        # Un segundo intento NO ejecuta otro POST: sigue bloqueado.
        client2 = FakePaywayClient(create_result=approved_remote(payment))
        outcome2, _ = execute_payway_payment(
            payment_or_id=payment, token="tok2", payment_method_id="1",
            now=self.now, client=client2,
        )
        self.assertEqual(client2.create_calls, 0)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)
        # Tampoco habilita otro medio de pago: la reserva sigue con un
        # pago activo (PENDING) que la restricción de base protege.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    booking=self.booking, method=PaymentMethod.BANK_TRANSFER,
                    status=PaymentStatus.AWAITING_VOUCHER, amount=Decimal("12500.00"),
                    currency="ARS", proof_deadline_at=self.now + timedelta(minutes=5),
                )

    def test_empty_then_late_appearance_confirms_without_new_charge(self):
        """Conciliación inicialmente vacía y posterior aparición del cobro:
        se confirma sin un nuevo POST al proveedor."""
        payment = self._pending_payment()
        # Primer intento: timeout; listado vacío → desconocido, bloqueado.
        client1 = FakePaywayClient(
            create_error=PaymentError("timeout"), find_result=[],
        )
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client1,
        )
        self.assertEqual(outcome, "STILL_UNKNOWN")
        # El cobro aparece después: la conciliación lo confirma sin cobrar.
        client2 = FakePaywayClient(find_result=[approved_remote(payment)])
        outcome2, _ = execute_payway_payment(
            payment_or_id=payment, token="tok2", payment_method_id="1",
            now=self.now, client=client2,
        )
        self.assertEqual(outcome2, "CONFIRMED")
        self.assertEqual(client2.create_calls, 0)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)

    def test_execute_on_approved_does_not_charge_again(self):
        payment = self._pending_payment()
        client = FakePaywayClient(create_result=approved_remote(payment))
        execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        client2 = FakePaywayClient(create_result=approved_remote(payment))
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok3", payment_method_id="1",
            now=self.now, client=client2,
        )
        self.assertEqual(outcome, "ALREADY_CONFIRMED")
        self.assertEqual(client2.create_calls, 0)

    def test_reconcile_pending_task_processes_payway_pending(self):
        payment = self._pending_payment()
        PaymentGatewayEvent.objects.create(
            provider=PaymentGatewayEvent.Provider.PAYWAY,
            external_id=payway_site_transaction_id(payment),
            action="execute:1",
            signature_ok=True,
            outcome="UNKNOWN_RESULT",
        )
        client = FakePaywayClient(find_result=[approved_remote(payment)])
        summary = reconcile_pending_payway_payments(now=self.now, client=client)
        payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(summary.get("CONFIRMED"), 1)

    # ---------- identidad económica / estados terminales ----------

    def test_amount_mismatch_does_not_confirm(self):
        payment = self._pending_payment()
        remote = approved_remote(payment)
        remote["amount"] = int(payment.amount * 100) - 1
        client = FakePaywayClient(create_result=remote)
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "MISMATCH")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_site_transaction_id_mismatch_rejected(self):
        payment = self._pending_payment()
        remote = approved_remote(payment)
        remote["site_transaction_id"] = "scv0000000000other"
        client = FakePaywayClient(create_result=remote)
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "MISMATCH")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_late_approval_on_expired_booking_conflicts(self):
        """Aprobación tardía con reserva vencida: conflicto registrado,
        sin confirmar reserva ni emitir pasajes."""
        payment = self._pending_payment()
        # La reserva ya está vencida al aplicar la respuesta.
        self.booking.expires_at = self.now - timedelta(minutes=1)
        self.booking.save(update_fields=["expires_at"])
        outcome, _detail = _apply_payway_result(
            payment_id=payment.pk,
            remote=approved_remote(payment, remote_id="888"),
            effective_now=self.now,
        )
        self.assertEqual(outcome, "CONFLICT_LATE_APPROVAL")
        payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(payment.conflict_reason, "approved_after_expiry")
        self.assertNotEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.assertFalse(hasattr(self.booking, "ticket_fulfillment"))

    def test_expiry_during_provider_call_conflicts(self):
        """La reserva vence durante la llamada al proveedor: la hora de
        aplicación se revalida dentro de la transacción → conflicto."""
        payment = self._pending_payment()

        class ExpiringClient(FakePaywayClient):
            def create_payment(self, payload):
                Booking.objects.filter(pk=payment.booking_id).update(
                    expires_at=timezone.now() - timedelta(seconds=1)
                )
                return approved_remote(payment)

        client = ExpiringClient()
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "CONFLICT_LATE_APPROVAL")
        payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(payment.conflict_reason, "approved_after_expiry")
        self.assertNotEqual(self.booking.status, BookingStatus.CONFIRMED)
        self.assertFalse(hasattr(self.booking, "ticket_fulfillment"))

    def test_still_pending_blocks_second_charge_attempt(self):
        """Un estado pendiente del proveedor no habilita otro POST de cobro."""
        payment = self._pending_payment()
        remote = approved_remote(payment)
        remote["status"] = "pending"
        client1 = FakePaywayClient(create_result=remote, find_result=[remote])
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client1,
        )
        self.assertEqual(outcome, "STILL_PENDING")
        # Segundo intento: no hay nuevo POST; sólo se reconsulta el estado.
        client2 = FakePaywayClient(create_result=approved_remote(payment))
        outcome2, _ = execute_payway_payment(
            payment_or_id=payment, token="tok2", payment_method_id="1",
            now=self.now, client=client2,
        )
        self.assertEqual(client2.create_calls, 0)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_mismatch_blocks_second_charge_attempt(self):
        """Un resultado MISMATCH tampoco habilita otro POST."""
        payment = self._pending_payment()
        remote = approved_remote(payment)
        remote["site_transaction_id"] = "scvother"
        client1 = FakePaywayClient(create_result=remote)
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client1,
        )
        self.assertEqual(outcome, "MISMATCH")
        client2 = FakePaywayClient(create_result=approved_remote(payment))
        execute_payway_payment(
            payment_or_id=payment, token="tok2", payment_method_id="1",
            now=self.now, client=client2,
        )
        self.assertEqual(client2.create_calls, 0)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_approved_missing_required_fields_not_confirmed(self):
        """Aprobación con campos obligatorios ausentes no confirma."""
        for field in ("id", "amount", "currency"):
            with self.subTest(field=field):
                # Reiniciar el intento: eliminar pagos y eventos del caso anterior.
                Payment.objects.filter(booking=self.booking).delete()
                payment = start_payway_payment(booking_or_id=self.booking, now=self.now)
                booking = self.booking
                remote = approved_remote(payment)
                del remote[field]
                client = FakePaywayClient(create_result=remote)
                outcome, _ = execute_payway_payment(
                    payment_or_id=payment, token="tok1", payment_method_id="1",
                    now=self.now, client=client,
                )
                self.assertEqual(outcome, "INCOMPLETE")
                payment.refresh_from_db()
                booking.refresh_from_db()
                self.assertEqual(payment.status, PaymentStatus.PENDING)
                self.assertEqual(booking.status, BookingStatus.HELD)
                # Y queda bloqueado contra un nuevo cobro.
                client2 = FakePaywayClient(create_result=approved_remote(payment))
                execute_payway_payment(
                    payment_or_id=payment, token="tok2", payment_method_id="1",
                    now=self.now, client=client2,
                )
                self.assertEqual(client2.create_calls, 0)

    def test_approved_missing_site_tx_not_confirmed(self):
        payment = self._pending_payment()
        remote = approved_remote(payment)
        del remote["site_transaction_id"]
        client = FakePaywayClient(create_result=remote)
        outcome, _ = execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertEqual(outcome, "INCOMPLETE")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_reconcile_ambiguous_results_block(self):
        """Varios pagos con la misma referencia: ambiguo, bloqueado."""
        payment = self._pending_payment()
        PaymentGatewayEvent.objects.create(
            provider=PaymentGatewayEvent.Provider.PAYWAY,
            external_id=payway_site_transaction_id(payment),
            action="execute:1",
            signature_ok=True,
            outcome="UNKNOWN_RESULT",
        )
        r1 = approved_remote(payment, remote_id="111")
        r2 = approved_remote(payment, remote_id="222")
        client = FakePaywayClient(find_result=[r1, r2])
        from payments.payway import reconcile_payway_payment
        outcome, _ = reconcile_payway_payment(
            payment_or_id=payment, now=self.now, client=client,
        )
        self.assertEqual(outcome, "AMBIGUOUS")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_reconcile_row_without_matching_ref_ignored(self):
        """results[0] sin correspondencia no se aplica: queda desconocido."""
        payment = self._pending_payment()
        PaymentGatewayEvent.objects.create(
            provider=PaymentGatewayEvent.Provider.PAYWAY,
            external_id=payway_site_transaction_id(payment),
            action="execute:1",
            signature_ok=True,
            outcome="UNKNOWN_RESULT",
        )
        other = approved_remote(payment)
        other["site_transaction_id"] = "scvunrelated"
        client = FakePaywayClient(find_result=[other])
        from payments.payway import reconcile_payway_payment
        outcome, _ = reconcile_payway_payment(
            payment_or_id=payment, now=self.now, client=client,
        )
        self.assertEqual(outcome, "STILL_UNKNOWN")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_reconcile_partial_listing_fetches_detail(self):
        """Listado parcial con id: se reconsulta el detalle autenticado."""
        payment = self._pending_payment()
        PaymentGatewayEvent.objects.create(
            provider=PaymentGatewayEvent.Provider.PAYWAY,
            external_id=payway_site_transaction_id(payment),
            action="execute:1",
            signature_ok=True,
            outcome="UNKNOWN_RESULT",
        )
        partial = {"id": "9001", "site_transaction_id": payway_site_transaction_id(payment)}
        client = FakePaywayClient(
            find_result=[partial], get_result=approved_remote(payment),
        )
        from payments.payway import reconcile_payway_payment
        outcome, _ = reconcile_payway_payment(
            payment_or_id=payment, now=self.now, client=client,
        )
        self.assertEqual(outcome, "CONFIRMED")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)

    def test_execute_blocks_charge_after_expiry(self):
        """Un cobro solicitado con la reserva ya vencida no se envía al proveedor."""
        payment = self._pending_payment()
        self.booking.expires_at = self.now + timedelta(minutes=10)
        self.booking.save(update_fields=["expires_at"])
        stale_now = self.booking.expires_at + timedelta(minutes=1)
        client = FakePaywayClient(create_result=approved_remote(payment))
        with self.assertRaises(BookingExpiredError):
            execute_payway_payment(
                payment_or_id=payment, token="tok1", payment_method_id="1",
                now=stale_now, client=client,
            )
        self.assertEqual(client.create_calls, 0)

    def test_approval_after_terminal_payment_state(self):
        """Pago ya REJECTED que el proveedor luego reporta approved → conflicto."""
        payment = self._pending_payment()
        rejected = approved_remote(payment)
        rejected["status"] = "rejected"
        client = FakePaywayClient(create_result=rejected)
        execute_payway_payment(
            payment_or_id=payment, token="tok1", payment_method_id="1",
            now=self.now, client=client,
        )
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.REJECTED)
        outcome, _ = _apply_payway_result(
            payment_id=payment.pk, remote=approved_remote(payment),
            effective_now=self.now,
        )
        self.assertEqual(outcome, "CONFLICT_TERMINAL")
        payment.refresh_from_db()
        self.assertEqual(payment.conflict_reason, "approval_after_terminal_state")

    def test_execute_requires_token(self):
        payment = self._pending_payment()
        with self.assertRaises(ValidationError):
            execute_payway_payment(
                payment_or_id=payment, token="", payment_method_id="1",
                now=self.now, client=FakePaywayClient(),
            )

    def test_second_pending_payment_impossible(self):
        self._pending_payment()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Payment.objects.create(
                    booking=self.booking, method=PaymentMethod.PAYWAY,
                    status=PaymentStatus.PENDING, amount=Decimal("12500.00"),
                    currency="ARS",
                )

    def test_token_never_stored(self):
        payment = self._pending_payment()
        client = FakePaywayClient(create_result=approved_remote(payment))
        execute_payway_payment(
            payment_or_id=payment, token="SECRET-TOKEN-123", payment_method_id="1",
            now=self.now, client=client,
        )
        self.assertNotIn("SECRET-TOKEN-123", payment.reference or "")
        self.assertNotIn("SECRET-TOKEN-123", payment.external_payment_id or "")
        for event in PaymentGatewayEvent.objects.filter(
            provider=PaymentGatewayEvent.Provider.PAYWAY,
        ):
            self.assertNotIn("SECRET-TOKEN-123", event.detail or "")
            self.assertNotIn("SECRET-TOKEN-123", event.external_id or "")


@override_settings(PAYWAY_ENABLED=False, PAYWAY_PUBLIC_API_KEY="", PAYWAY_PRIVATE_API_KEY="")
class PaywayDisabledTests(TestCase):
    def test_not_configured(self):
        self.assertFalse(payway_configured())


@override_settings(**PAYWAY_SETTINGS)
class PaywayConcurrencyTests(TransactionTestCase):
    """Concurrencia entre ejecución y conciliación: un solo cobro.

    La restricción ``(provider, external_id, action)`` de PaymentGatewayEvent
    garantiza que sólo una ejecución reclama el intento. Corre en PostgreSQL
    vía CI; en SQLite los escritores se serializan y no hay concurrencia real.
    """

    @classmethod
    def setUpTestData(cls):
        PaywayBase.setUpTestData.__func__(cls)

    def create_online_booking(self, minutes=15):
        return PaywayBase.create_online_booking(self, minutes=minutes)

    def require_postgresql(self):
        from django.db import connection
        if connection.vendor != "postgresql":
            self.skipTest("Concurrencia real: sólo PostgreSQL (se ejecuta en CI).")

    def test_concurrent_execute_single_charge(self):
        self.require_postgresql()
        import threading
        booking = self.create_online_booking()
        payment = start_payway_payment(booking_or_id=booking)
        shared_client = FakePaywayClient(create_result=approved_remote(payment))
        results = []

        from django.db import connection as thread_conn

        def run():
            try:
                outcome, _ = execute_payway_payment(
                    payment_or_id=payment.pk, token="tok1",
                    payment_method_id="1", client=shared_client,
                )
            except Exception as exc:  # noqa: BLE001 - registrado en results
                outcome = f"ERR:{exc.__class__.__name__}"
            results.append(outcome)
            thread_conn.close()

        threads = [threading.Thread(target=run) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive())

        self.assertEqual(shared_client.create_calls, 1)
        self.assertIn("CONFIRMED", results)
        payment.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)

    def test_execute_and_reconcile_concurrently_single_charge(self):
        """Ejecución concurrente con la conciliación de mantenimiento:
        a lo sumo un POST de cobro."""
        self.require_postgresql()
        import threading
        booking = self.create_online_booking()
        payment = start_payway_payment(booking_or_id=booking)
        shared_client = FakePaywayClient(
            create_error=PaymentError("timeout"),
            find_result=[approved_remote(payment)],
        )
        results = []

        from django.db import connection as thread_conn

        def run_execute():
            try:
                outcome, _ = execute_payway_payment(
                    payment_or_id=payment.pk, token="tok1",
                    payment_method_id="1", client=shared_client,
                )
            except Exception as exc:  # noqa: BLE001
                outcome = f"ERR:{exc.__class__.__name__}"
            results.append(("execute", outcome))
            thread_conn.close()

        def run_reconcile():
            try:
                summary = reconcile_pending_payway_payments(client=shared_client)
            except Exception as exc:  # noqa: BLE001
                summary = {"error": exc.__class__.__name__}
            results.append(("reconcile", summary))
            thread_conn.close()

        threads = [threading.Thread(target=run_execute) for _ in range(2)]
        threads += [threading.Thread(target=run_reconcile) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive())

        self.assertEqual(shared_client.create_calls, 1)
        payment.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)


@override_settings(**PAYWAY_SETTINGS)
class PaywayViewTests(PaywayBase):
    """Integración HTTP del flujo público con token de sesión."""

    def setUp(self):
        self.booking = self.create_online_booking()
        session = self.client.session
        session[f"booking_access_{self.booking.public_id}"] = "tok"
        session.save()
        self.start_url = f"/resumen/{self.booking.public_id}/payway/iniciar/"
        self.exec_url = f"/resumen/{self.booking.public_id}/payway/ejecutar/"

    def _drop_session(self):
        session = self.client.session
        del session[f"booking_access_{self.booking.public_id}"]
        session.save()

    def test_iniciar_requires_session_token(self):
        self._drop_session()
        resp = self.client.post(self.start_url)
        self.assertEqual(resp.status_code, 403)

    def test_iniciar_rejects_get(self):
        resp = self.client.get(self.start_url)
        self.assertEqual(resp.status_code, 405)

    def test_iniciar_creates_payment_and_renders_form(self):
        resp = self.client.post(self.start_url)
        self.assertEqual(resp.status_code, 200)
        payment = Payment.objects.get(booking=self.booking)
        self.assertEqual(payment.method, PaymentMethod.PAYWAY)
        self.assertEqual(payment.status, PaymentStatus.PENDING)
        self.assertContains(resp, "decidir.js", status_code=200)
        self.assertContains(resp, 'name="token"')
        # El importe que se muestra proviene del pago server-side.
        self.assertContains(resp, "$12500")

    def test_iniciar_reuses_pending_payment(self):
        self.client.post(self.start_url)
        self.client.post(self.start_url)
        self.assertEqual(
            Payment.objects.filter(booking=self.booking).count(), 1
        )

    @override_settings(PAYWAY_ENABLED=False)
    def test_iniciar_disabled_redirects(self):
        resp = self.client.post(self.start_url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            Payment.objects.filter(booking=self.booking).exists()
        )

    def test_ejecutar_requires_session_token(self):
        self.client.post(self.start_url)
        self._drop_session()
        resp = self.client.post(self.exec_url, {"token": "t", "payment_method_id": "1"})
        self.assertEqual(resp.status_code, 403)

    def test_ejecutar_without_initiated_payment_redirects(self):
        resp = self.client.post(self.exec_url, {"token": "t", "payment_method_id": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/pago/", resp.url)

    def test_ejecutar_empty_token_redirects(self):
        self.client.post(self.start_url)
        resp = self.client.post(self.exec_url, {"token": "", "payment_method_id": "1"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("resumen", resp.url)

    def test_ejecutar_approved_confirms(self):
        from unittest.mock import patch
        self.client.post(self.start_url)
        payment = Payment.objects.get(booking=self.booking)
        client = FakePaywayClient(create_result=approved_remote(payment))
        with patch("payments.payway.PaywayClient", return_value=client):
            resp = self.client.post(self.exec_url, {"token": "tok1", "payment_method_id": "1"})
        self.assertEqual(resp.status_code, 302)
        payment.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.APPROVED)
        self.assertEqual(self.booking.status, BookingStatus.CONFIRMED)
