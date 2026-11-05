from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase

from cash_register.models import CashMovement, CashSession
from cash_register.services import close_cash, current_cash, open_cash, record_adjustment, record_cash_sale
from panel.cash_views import cash_adjustment, cash_close, cash_open


class CashDisabledPanelTests(TestCase):
    def setUp(self):
        self.client = Client()
        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        seller_group, _ = Group.objects.get_or_create(name="Vendedor")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user("admin-cash", password="x")
        self.admin.groups.add(admin_group)
        self.seller = user_model.objects.create_user("seller-cash", password="x")
        self.seller.groups.add(seller_group)
        self.common = user_model.objects.create_user("common-cash", password="x")

        # Registro histórico creado por ORM
        self.session = CashSession.objects.create(
            opened_by=self.seller,
            opening_amount=Decimal("50.00"),
            status=CashSession.Status.OPEN,
        )

    def test_retired_cash_routes_return_404(self):
        urls = [
            "/panel/caja/",
            f"/panel/caja/{self.session.pk}/",
            "/panel/caja/abrir/",
            "/panel/caja/cerrar/",
            "/panel/caja/ajuste/",
        ]
        for user in [self.common, self.seller, self.admin]:
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, {}).status_code, 404)

    def test_direct_cash_view_calls_raise_validation_error(self):
        factory = RequestFactory()

        req_open = factory.post("/panel/caja/abrir/", {"opening_amount": "100.00"})
        req_open.user = self.seller
        with self.assertRaises(ValidationError) as ctx:
            cash_open(req_open)
        self.assertIn("deshabilitada", str(ctx.exception).lower())

        req_close = factory.post("/panel/caja/cerrar/", {"closing_amount": "100.00"})
        req_close.user = self.seller
        with self.assertRaises(ValidationError) as ctx:
            cash_close(req_close)
        self.assertIn("deshabilitado", str(ctx.exception).lower())

        req_adj = factory.post("/panel/caja/ajuste/", {
            "session_id": self.session.pk,
            "amount": "10.00",
            "kind": "ADJUSTMENT_IN",
            "reason": "Ajuste prueba",
        })
        req_adj.user = self.admin
        with self.assertRaises(ValidationError) as ctx:
            cash_adjustment(req_adj)
        self.assertIn("deshabilitad", str(ctx.exception).lower())

    def test_cash_services_raise_validation_error(self):
        with self.assertRaises(ValidationError) as ctx:
            open_cash(operator=self.seller, opening_amount=Decimal("100.00"))
        self.assertIn("deshabilitada", str(ctx.exception).lower())

        with self.assertRaises(ValidationError) as ctx:
            close_cash(operator=self.seller, closing_amount=Decimal("50.00"))
        self.assertIn("deshabilitado", str(ctx.exception).lower())

        with self.assertRaises(ValidationError) as ctx:
            record_adjustment(
                operator=self.admin,
                session_id=self.session.pk,
                amount=Decimal("10.00"),
                kind="ADJUSTMENT_IN",
                reason="Test",
            )
        self.assertIn("deshabilitado", str(ctx.exception).lower())

        with self.assertRaises(ValidationError) as ctx:
            record_cash_sale(payment=None, operator=self.seller)
        self.assertIn("deshabilitado", str(ctx.exception).lower())

        self.assertIsNone(current_cash(operator=self.seller))

    def test_historical_cash_records_preserved(self):
        movement = CashMovement.objects.create(
            session=self.session,
            kind=CashMovement.Kind.OPENING,
            amount=Decimal("50.00"),
            created_by=self.seller,
            reference="Fondo inicial histórico",
            idempotency_key=f"hist-opening:{self.session.pk}",
        )
        self.assertEqual(CashSession.objects.filter(pk=self.session.pk).count(), 1)
        self.assertEqual(CashMovement.objects.filter(pk=movement.pk).count(), 1)
