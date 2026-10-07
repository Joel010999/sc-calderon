import csv
import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .models import AuditEvent
from .audit_views import _csv_safe


class AuditExplorerTests(TestCase):
    def setUp(self):
        self.admin_group = Group.objects.create(name="Administrador")
        self.seller_group = Group.objects.create(name="Vendedor")
        self.admin = get_user_model().objects.create_user(username="audit-admin", password="pass")
        self.admin.groups.add(self.admin_group)
        self.seller = get_user_model().objects.create_user(username="audit-seller", password="pass")
        self.seller.groups.add(self.seller_group)
        self.common = get_user_model().objects.create_user(username="audit-common", password="pass")
        self.event = AuditEvent.objects.create(actor=self.admin, action=AuditEvent.Action.UPDATE,
                                               entity_type="sales.Booking", entity_id="7",
                                               description="interno", after={"public_id": "11111111-1111-4111-8111-111111111111"})

    def test_only_administrator_or_superuser_can_read(self):
        self.assertEqual(self.client.get(reverse("panel:audit_list")).status_code, 302)
        self.client.login(username="audit-seller", password="pass")
        self.assertEqual(self.client.get(reverse("panel:audit_list")).status_code, 403)
        self.client.login(username="audit-common", password="pass")
        self.assertEqual(self.client.get(reverse("panel:audit_list")).status_code, 403)
        self.client.login(username="audit-admin", password="pass")
        self.assertEqual(self.client.get(reverse("panel:audit_list")).status_code, 200)

    def test_filters_and_pagination_keep_query_parameters(self):
        for index in range(26):
            AuditEvent.objects.create(actor=self.admin, action=AuditEvent.Action.UPDATE,
                                      entity_type="sales.Booking", entity_id=str(index),
                                      description="interno", after={"public_id": f"11111111-1111-4111-8111-{index + 1:012d}"})
        self.client.login(username="audit-admin", password="pass")
        response = self.client.get(reverse("panel:audit_list"), {"modulo": "sales", "accion": "UPDATE", "pagina": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 27)
        self.assertContains(response, "pagina=1&amp;modulo=sales&amp;accion=UPDATE")

    def test_csv_formula_guard_and_actor_privacy(self):
        self.assertEqual(_csv_safe("=SUM(A1:A2)"), "'=SUM(A1:A2)")
        self.client.login(username="audit-admin", password="pass")
        response = self.client.get(reverse("panel:audit_csv"))
        body = response.content.decode("utf-8-sig")
        self.assertIn("Usuario interno", body)
        self.assertNotIn("audit-admin", body)

    def test_get_and_csv_are_read_only_and_csv_has_bom(self):
        self.client.login(username="audit-admin", password="pass")
        count = AuditEvent.objects.count()
        response = self.client.get(reverse("panel:audit_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(AuditEvent.objects.count(), count)
        self.assertIn("11111111-1111-4111-8111-111111111111", response.content.decode("utf-8-sig"))

    def test_signed_detail_does_not_expose_description_or_internal_id(self):
        self.client.login(username="audit-admin", password="pass")
        listing = self.client.get(reverse("panel:audit_list"))
        token = listing.context["page"].object_list[0].token
        detail = self.client.get(reverse("panel:audit_detail", args=[token]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "<dd>interno</dd>")
        self.assertNotContains(detail, ">7<")
