from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse

from cash_register.models import CashSession
from cash_register.services import open_cash


class CashPanelTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        seller_group, _ = Group.objects.get_or_create(name="Vendedor")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user("admin-cash", password="x")
        self.admin.groups.add(admin_group)
        self.seller = user_model.objects.create_user("seller-cash", password="x")
        self.seller.groups.add(seller_group)
        self.common = user_model.objects.create_user("common-cash", password="x")
        self.session = open_cash(operator=self.seller, opening_amount=Decimal("50.00"))

    def test_permissions_and_own_scope(self):
        url = reverse("panel:cash_detail")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.common)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(url).status_code, 200)
        admin_session = open_cash(operator=self.admin)
        self.assertEqual(
            self.client.get(reverse("panel:cash_session_detail", args=[admin_session.pk])).status_code,
            403,
        )

    def test_admin_can_list_and_filter_all_sessions(self):
        admin_session = open_cash(operator=self.admin)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("panel:cash_detail"), {"status": "OPEN", "seller": "admin-cash"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(admin_session.pk))
        self.assertNotContains(response, "seller-cash")

    def test_mutating_actions_require_post_and_csrf(self):
        self.client.force_login(self.seller)
        response = self.client.post(reverse("panel:cash_close"), {"closing_amount": "50.00"})
        self.assertEqual(response.status_code, 403)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, CashSession.Status.OPEN)
