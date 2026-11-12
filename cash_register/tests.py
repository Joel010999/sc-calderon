from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import CashMovement, CashSession
from .services import close_cash, current_cash, open_cash, record_adjustment, record_cash_sale


class CashRegisterDisabledServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("cajero", password="x")
        self.admin = get_user_model().objects.create_user("admin", password="x")
        group, _ = Group.objects.get_or_create(name="Administrador")
        self.admin.groups.add(group)

    def test_open_cash_is_disabled(self):
        with self.assertRaises(ValidationError) as ctx:
            open_cash(operator=self.user, opening_amount=Decimal("100.00"))
        self.assertIn("deshabilitada", str(ctx.exception).lower())

    def test_close_cash_is_disabled(self):
        with self.assertRaises(ValidationError) as ctx:
            close_cash(operator=self.user, closing_amount=Decimal("100.00"))
        self.assertIn("deshabilitado", str(ctx.exception).lower())

    def test_record_adjustment_is_disabled(self):
        with self.assertRaises(ValidationError) as ctx:
            record_adjustment(
                operator=self.admin,
                session_id=1,
                amount=Decimal("10.00"),
                kind="ADJUSTMENT_IN",
                reason="Test",
            )
        self.assertIn("deshabilitado", str(ctx.exception).lower())

    def test_record_cash_sale_is_disabled(self):
        with self.assertRaises(ValidationError) as ctx:
            record_cash_sale(payment=None, operator=self.user)
        self.assertIn("deshabilitado", str(ctx.exception).lower())

    def test_current_cash_returns_none(self):
        self.assertIsNone(current_cash(operator=self.user))

    def test_historical_movement_cannot_be_updated_or_deleted(self):
        session = CashSession.objects.create(
            opened_by=self.user,
            opening_amount=Decimal("1.00"),
            status=CashSession.Status.OPEN,
        )
        movement = CashMovement.objects.create(
            session=session,
            kind=CashMovement.Kind.OPENING,
            amount=Decimal("1.00"),
            created_by=self.user,
            reference="Fondo inicial histórico",
            idempotency_key=f"hist:{session.pk}",
        )
        movement.reference = "alterado"
        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            movement.delete()
