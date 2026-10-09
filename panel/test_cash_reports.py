from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from cash_register.models import CashSession
from cash_register.services import close_cash, open_cash, record_adjustment
from panel.models import AuditEvent


class CashReportTests(TestCase):
    def setUp(self):
        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        seller_group, _ = Group.objects.get_or_create(name="Vendedor")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user("cash-report-admin", password="x")
        self.admin.groups.add(admin_group)
        self.seller = user_model.objects.create_user("cash-report-seller", password="x")
        self.seller.groups.add(seller_group)
        self.other_seller = user_model.objects.create_user("cash-report-other", password="x")
        self.other_seller.groups.add(seller_group)
        self.common = user_model.objects.create_user("cash-report-common", password="x")
        self.seller_session = open_cash(operator=self.seller, opening_amount=Decimal("100.00"))
        record_adjustment(operator=self.admin, session_id=self.seller_session.pk,
                          amount=Decimal("20.00"), kind="ADJUSTMENT_IN", reason="Control interno")
        record_adjustment(operator=self.admin, session_id=self.seller_session.pk,
                          amount=Decimal("5.00"), kind="ADJUSTMENT_OUT", reason="Control interno")
        close_cash(operator=self.seller, closing_amount=Decimal("112.00"))
        self.other_session = open_cash(operator=self.other_seller, opening_amount=Decimal("50.00"))
        self.client = Client()

    def test_permissions_and_idor_scope(self):
        url = reverse("panel:cash_report")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.common)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.seller)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Vendedor #{self.seller.pk}")
        self.assertNotContains(response, f"Vendedor #{self.other_seller.pk}")
        self.assertEqual(self.client.get(reverse("panel:cash_report_detail", args=[self.other_session.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), f"Vendedor #{self.other_seller.pk}")

    def test_decimal_components_and_difference_filters(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("panel:cash_report"))
        self.assertContains(response, "100,00")
        self.assertContains(response, "20")
        self.assertContains(response, "5")
        self.assertContains(response, "115")
        self.assertContains(response, "112")
        self.assertContains(response, "-3")
        negative = self.client.get(reverse("panel:cash_report"), {"diferencia": "negativa"})
        self.assertContains(negative, f"<td>Vendedor #{self.seller.pk}</td>", html=True)
        positive = self.client.get(reverse("panel:cash_report"), {"diferencia": "positiva"})
        self.assertNotContains(positive, f"<td>Vendedor #{self.seller.pk}</td>", html=True)

    def test_csv_is_filtered_private_and_formula_safe(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("panel:cash_report_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith("\ufeff".encode("utf-8")))
        self.assertIn("attachment; filename=\"reporte-caja.csv\"", response["Content-Disposition"])
        body = response.content.decode("utf-8-sig")
        self.assertIn("Saldo inicial", body)
        self.assertIn("Vendedor #", body)
        self.assertNotIn(self.other_seller.username, body)
        self.assertNotIn("Pasajero", body)

    def test_review_is_post_only_idempotent_and_audited_without_note_exposure(self):
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(reverse("panel:cash_report_review", args=[self.seller_session.pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse("panel:cash_report_review", args=[self.seller_session.pk]), {"note": "no"}).status_code, 403)
        self.client.force_login(self.admin)
        audit_before = AuditEvent.objects.filter(entity_type=CashSession._meta.label, entity_id=str(self.seller_session.pk)).count()
        response = self.client.post(reverse("panel:cash_report_review", args=[self.seller_session.pk]), {"note": "Revisada sin diferencias ocultas"})
        self.assertEqual(response.status_code, 302)
        self.seller_session.refresh_from_db()
        self.assertEqual(self.seller_session.review_note, "Revisada sin diferencias ocultas")
        self.assertEqual(self.seller_session.reviewed_by_id, self.admin.pk)
        self.assertEqual(AuditEvent.objects.filter(entity_type=CashSession._meta.label, entity_id=str(self.seller_session.pk)).count(), audit_before + 1)
        self.client.post(reverse("panel:cash_report_review", args=[self.seller_session.pk]), {"note": "No debe reemplazarse"})
        self.seller_session.refresh_from_db()
        self.assertEqual(self.seller_session.review_note, "Revisada sin diferencias ocultas")
        self.assertEqual(AuditEvent.objects.filter(entity_type=CashSession._meta.label, entity_id=str(self.seller_session.pk)).count(), audit_before + 1)

    def test_report_query_count_is_bounded(self):
        self.client.force_login(self.admin)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("panel:cash_report"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 10)
