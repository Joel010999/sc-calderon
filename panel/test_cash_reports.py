from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from cash_register.models import CashMovement, CashSession
from cash_register.report_services import build_cash_report


class CashReportDisabledTests(TestCase):
    def setUp(self):
        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        seller_group, _ = Group.objects.get_or_create(name="Vendedor")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user("cash-report-admin", password="x")
        self.admin.groups.add(admin_group)
        self.seller = user_model.objects.create_user("cash-report-seller", password="x")
        self.seller.groups.add(seller_group)
        self.common = user_model.objects.create_user("cash-report-common", password="x")

        # Registro histórico creado por ORM
        self.seller_session = CashSession.objects.create(
            opened_by=self.seller,
            opening_amount=Decimal("100.00"),
            status=CashSession.Status.CLOSED,
            closing_amount=Decimal("112.00"),
            expected_amount=Decimal("115.00"),
        )
        self.movement = CashMovement.objects.create(
            session=self.seller_session,
            kind=CashMovement.Kind.OPENING,
            amount=Decimal("100.00"),
            created_by=self.seller,
            reference="Fondo inicial histórico",
            idempotency_key=f"rep-hist-opening:{self.seller_session.pk}",
        )
        self.client = Client()

    def test_retired_cash_report_routes_return_404(self):
        urls = [
            "/panel/reportes/caja/",
            "/panel/reportes/caja.csv",
            f"/panel/reportes/caja/{self.seller_session.pk}/",
            f"/panel/reportes/caja/{self.seller_session.pk}/revisar/",
        ]
        for user in [self.common, self.seller, self.admin]:
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, {}).status_code, 404)

    def test_historical_cash_data_queryable_via_services(self):
        report = build_cash_report({}, self.admin)
        self.assertIn("page", report)
        session_ids = [item.pk for item in report["page"].object_list]
        self.assertIn(self.seller_session.pk, session_ids)
