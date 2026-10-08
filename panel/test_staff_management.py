from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, StaffInvitation
from .staff_services import accept_invitation, invite_staff, update_staff


class StaffManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.admin_group = Group.objects.create(name="Administrador")
        cls.seller_group = Group.objects.create(name="Vendedor")
        cls.admin = cls.User.objects.create_user(username="admin", email="admin@sc.invalid")
        cls.admin.groups.add(cls.admin_group)
        cls.seller = cls.User.objects.create_user(username="seller", email="seller@sc.invalid")
        cls.seller.groups.add(cls.seller_group)
        cls.common = cls.User.objects.create_user(username="common", email="common@sc.invalid")
        cls.customer = cls.User.objects.create_user(username="customer", email="customer@sc.invalid")
        cls.customer.is_staff = False
        cls.customer.save(update_fields=["is_staff"])
        cls.superuser = cls.User.objects.create_superuser(username="root", email="root@sc.invalid", password="Root-password-123!")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def test_only_administrator_can_open_staff_list(self):
        for user in (self.seller, self.common, self.customer):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("panel:staff_list"), secure=True).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("panel:staff_list"), secure=True).status_code, 200)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_invitation_is_hash_only_and_acceptance_is_single_use(self):
        invitation, raw = invite_staff(actor=self.admin, email="New@SC.INVALID", role="Vendedor")
        self.assertEqual(invitation.normalized_email, "new@sc.invalid")
        self.assertNotIn(raw, invitation.token_hash)
        user = accept_invitation(raw, "A-valid-password-123!")
        self.assertTrue(user.is_active)
        self.assertTrue(user.groups.filter(name="Vendedor").exists())
        with self.assertRaises(ValidationError):
            accept_invitation(raw, "Another-password-123!")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_invitation_requires_superuser_and_smtp_failure_rolls_back(self):
        with self.assertRaises(ValidationError):
            invite_staff(actor=self.admin, email="manager@sc.invalid", role="Administrador")
        with patch("panel.staff_services.send_mail", side_effect=RuntimeError("smtp")):
            with self.assertRaises(RuntimeError):
                invite_staff(actor=self.admin, email="seller2@sc.invalid", role="Vendedor")
        self.assertFalse(self.User.objects.filter(email="seller2@sc.invalid").exists())
        self.assertFalse(StaffInvitation.objects.filter(normalized_email="seller2@sc.invalid").exists())

    def test_post_csrf_and_self_protection(self):
        self.client.force_login(self.admin)
        url = reverse("panel:staff_update", kwargs={"user_id": self.admin.pk})
        response = self.client.post(url, {"role": "Vendedor", "active": "0"}, secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.admin.is_active)
        self.assertTrue(self.admin.groups.filter(name="Administrador").exists())

    def test_last_active_administrator_cannot_be_deactivated(self):
        with self.assertRaises(ValidationError):
            update_staff(actor=self.superuser, user_id=self.admin.pk, active=False)
        self.assertTrue(self.admin.refresh_from_db() is None)
        self.assertTrue(self.admin.is_active)

    def test_superuser_cannot_be_managed_and_changes_are_audited(self):
        with self.assertRaises(ValidationError):
            update_staff(actor=self.admin, user_id=self.superuser.pk, active=False)
        update_staff(actor=self.superuser, user_id=self.seller.pk, active=False)
        self.assertTrue(AuditEvent.objects.filter(entity_type="auth.User", entity_id=str(self.seller.pk)).exists())
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.is_active)

    def test_expired_invitation_rejected(self):
        invitation, raw = invite_staff(actor=self.admin, email="expired@sc.invalid", role="Vendedor")
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=["expires_at"])
        with self.assertRaises(ValidationError):
            accept_invitation(raw, "A-valid-password-123!")
