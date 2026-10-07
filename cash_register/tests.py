from decimal import Decimal
import threading

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase

from .models import CashMovement, CashSession
from .services import close_cash, open_cash, record_adjustment


class CashRegisterServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("cajero", password="x")
        self.other_user = get_user_model().objects.create_user("otro", password="x")
        self.admin = get_user_model().objects.create_user("admin", password="x")
        group, _ = Group.objects.get_or_create(name="Administrador")
        self.admin.groups.add(group)

    def test_open_close_and_expected_balance(self):
        session = open_cash(operator=self.user, opening_amount=Decimal("100.00"))
        self.assertEqual(session.movements.count(), 1)
        with self.assertRaises(ValidationError):
            open_cash(operator=self.user)
        closed = close_cash(operator=self.user, closing_amount=Decimal("100.00"))
        self.assertEqual(closed.expected_amount, Decimal("100.00"))
        self.assertEqual(closed.difference, Decimal("0.00"))
        self.assertEqual(closed.status, CashSession.Status.CLOSED)

    def test_each_operator_can_have_one_open_session(self):
        open_cash(operator=self.user)
        open_cash(operator=self.other_user)
        self.assertEqual(CashSession.objects.filter(status=CashSession.Status.OPEN).count(), 2)

    def test_movement_cannot_be_updated_or_deleted(self):
        session = open_cash(operator=self.user, opening_amount=Decimal("1.00"))
        movement = session.movements.get()
        movement.reference = "alterado"
        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()

    def test_admin_adjustment_requires_reason_and_closed_box_rejects_new_movement(self):
        session = open_cash(operator=self.user, opening_amount=Decimal("10.00"))
        with self.assertRaises(ValidationError):
            record_adjustment(
                operator=self.admin,
                session_id=session.pk,
                amount=Decimal("2.00"),
                kind=CashMovement.Kind.ADJUSTMENT_IN,
                reason="",
            )
        movement = record_adjustment(
            operator=self.admin,
            session_id=session.pk,
            amount=Decimal("2.00"),
            kind=CashMovement.Kind.ADJUSTMENT_IN,
            reason="Diferencia de caja autorizada",
        )
        self.assertEqual(movement.reason, "Diferencia de caja autorizada")
        close_cash(operator=self.user, closing_amount=Decimal("12.00"))
        with self.assertRaises(ValidationError):
            record_adjustment(
                operator=self.admin,
                session_id=session.pk,
                amount=Decimal("1.00"),
                kind=CashMovement.Kind.ADJUSTMENT_OUT,
                reason="Egreso posterior",
            )


class PostgreSQLCashRegisterConcurrencyTests(TransactionTestCase):
    def test_postgresql_rejects_double_open_for_same_operator(self):
        if connection.vendor != "postgresql":
            self.skipTest("requiere PostgreSQL")
        user = get_user_model().objects.create_user("concurrent-cajero", password="x")
        barrier = threading.Barrier(2)
        results = []

        def attempt():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                open_cash(operator=get_user_model().objects.get(pk=user.pk))
                results.append("ok")
            except ValidationError:
                results.append("rejected")
            finally:
                close_old_connections()

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sorted(results), ["ok", "rejected"])
        self.assertEqual(CashSession.objects.filter(opened_by=user, status=CashSession.Status.OPEN).count(), 1)
