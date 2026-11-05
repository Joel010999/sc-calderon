from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, StaffInvitation, StaffProfile
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
        cls.superuser = cls.User.objects.create_superuser(
            username="root", email="root@sc.invalid", password="Root-password-123!"
        )

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def test_only_administrator_and_superuser_can_open_staff_list(self):
        for user in (self.seller, self.common, self.customer):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("panel:staff_list"), secure=True).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("panel:staff_list"), secure=True).status_code, 200)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("panel:staff_list"), secure=True).status_code, 200)

    def test_staff_list_scope_admin_vs_superuser(self):
        # Administrador solo ve vendedores, no admins ni superusuarios
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("panel:staff_list"), secure=True)
        self.assertEqual(resp.status_code, 200)
        emails = [u.email for u in resp.context["users"]]
        self.assertIn("seller@sc.invalid", emails)
        self.assertNotIn("admin@sc.invalid", emails)
        self.assertNotIn("root@sc.invalid", emails)
        self.assertEqual(resp.context["roles"], ("Vendedor",))

        # Superusuario ve todo: administradores, vendedores y superusuarios
        self.client.force_login(self.superuser)
        resp_su = self.client.get(reverse("panel:staff_list"), secure=True)
        self.assertEqual(resp_su.status_code, 200)
        emails_su = [u.email for u in resp_su.context["users"]]
        self.assertIn("seller@sc.invalid", emails_su)
        self.assertIn("admin@sc.invalid", emails_su)
        self.assertIn("root@sc.invalid", emails_su)
        self.assertIn("Superusuario", resp_su.context["roles"])
        self.assertIn("Administrador", resp_su.context["roles"])
        self.assertIn("Vendedor", resp_su.context["roles"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_invitation_is_hash_only_and_acceptance_is_single_use(self):
        invitation, raw = invite_staff(actor=self.admin, email="New@SC.INVALID", role="Vendedor")
        self.assertEqual(invitation.normalized_email, "new@sc.invalid")
        self.assertNotIn(raw, invitation.token_hash)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("https://scviajes.test/panel/invitacion/", mail.outbox[0].body)
        self.assertNotIn(invitation.token_hash, mail.outbox[0].body)
        user = accept_invitation(raw, "A-valid-password-123!")
        self.assertTrue(user.is_active)
        self.assertTrue(user.groups.filter(name="Vendedor").exists())
        with self.assertRaises(ValidationError):
            accept_invitation(raw, "Another-password-123!")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_invitation_requires_superuser_and_smtp_failure_rolls_back(self):
        with self.assertRaises(ValidationError):
            invite_staff(actor=self.admin, email="manager@sc.invalid", role="Administrador")
        with self.assertRaises(ValidationError):
            invite_staff(actor=self.admin, email="manager2@sc.invalid", role="Superusuario")
        with patch("panel.staff_services.send_mail", side_effect=RuntimeError("smtp")):
            with self.assertRaises(RuntimeError):
                invite_staff(actor=self.admin, email="seller2@sc.invalid", role="Vendedor")
        self.assertFalse(self.User.objects.filter(email="seller2@sc.invalid").exists())
        self.assertFalse(StaffInvitation.objects.filter(normalized_email="seller2@sc.invalid").exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_superuser_authority_replaces_is_owner(self):
        # Administrador no puede invitar ni promover a Administrador ni Superusuario
        with self.assertRaises(ValidationError):
            invite_staff(actor=self.admin, email="manager@sc.invalid", role="Administrador")
        with self.assertRaises(ValidationError):
            update_staff(actor=self.admin, user_id=self.seller.pk, role="Administrador")

        # Superusuario no requiere ningún flag is_owner en perfil: su autoridad es is_superuser
        invitation, _ = invite_staff(actor=self.superuser, email="manager@sc.invalid", role="Administrador")
        self.assertEqual(invitation.role, "Administrador")
        update_staff(actor=self.superuser, user_id=self.seller.pk, role="Administrador")
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.groups.filter(name="Administrador").exists())

    def test_staff_profile_fields_are_updated_without_replacing_customer(self):
        update_staff(
            actor=self.admin,
            user_id=self.seller.pk,
            first_name="Ana",
            last_name="Vendedora",
            dni="30123456",
            whatsapp="3515551234",
        )
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.get_full_name(), "Ana Vendedora")
        profile = StaffProfile.objects.get(user=self.seller)
        self.assertEqual(profile.dni, "30123456")
        self.assertEqual(profile.whatsapp, "3515551234")

    def test_post_csrf_and_self_protection(self):
        self.client.force_login(self.admin)
        url = reverse("panel:staff_update", kwargs={"user_id": self.admin.pk})
        self.client.post(url, {"role": "Vendedor", "active": "0"}, secure=True)
        # Debe fallar porque admin no puede auto-desactivarse ni degradarse
        self.assertTrue(self.admin.refresh_from_db() is None)
        self.assertTrue(self.admin.is_active)
        self.assertTrue(self.admin.groups.filter(name="Administrador").exists())

    def test_last_active_administrator_cannot_be_deactivated(self):
        with self.assertRaises(ValidationError):
            update_staff(actor=self.superuser, user_id=self.admin.pk, active=False)
        self.assertTrue(self.admin.refresh_from_db() is None)
        self.assertTrue(self.admin.is_active)

    def test_last_active_superuser_cannot_be_deactivated(self):
        with self.assertRaises(ValidationError):
            update_staff(actor=self.superuser, user_id=self.superuser.pk, active=False)
        with self.assertRaises(ValidationError):
            update_staff(actor=self.superuser, user_id=self.superuser.pk, role="Administrador")
        self.assertTrue(self.superuser.refresh_from_db() is None)
        self.assertTrue(self.superuser.is_active)
        self.assertTrue(self.superuser.is_superuser)

    def test_superuser_can_be_managed_by_superuser_and_audited(self):
        # Admin no puede gestionar superusuario
        with self.assertRaises(ValidationError):
            update_staff(actor=self.admin, user_id=self.superuser.pk, active=False)

        # Crear segundo superusuario para poder desactivar uno
        second_su = self.User.objects.create_superuser(
            username="second_su", email="second_su@sc.invalid", password="Root-password-123!"
        )
        update_staff(actor=self.superuser, user_id=second_su.pk, active=False)
        second_su.refresh_from_db()
        self.assertFalse(second_su.is_active)
        self.assertTrue(AuditEvent.objects.filter(entity_type="auth.User", entity_id=str(second_su.pk)).exists())

    def test_expired_invitation_rejected(self):
        invitation, raw = invite_staff(actor=self.admin, email="expired@sc.invalid", role="Vendedor")
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=["expires_at"])
        with self.assertRaises(ValidationError):
            accept_invitation(raw, "A-valid-password-123!")
