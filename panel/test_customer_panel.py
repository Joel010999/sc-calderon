from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer, CustomerBooking, CustomerConsent
from sales.models import Booking, BookingChannel, BookingStatus


User = get_user_model()


class CustomerPanelTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Vendedor")
        self.seller = User.objects.create_user(username="vendedor", password="x", email="vendedor@example.com")
        self.seller.groups.add(self.group)
        self.customer_user = User.objects.create_user(
            username="cliente", password="x", email="cliente@example.com", first_name="Ana", last_name="Pérez"
        )
        self.customer = Customer.objects.create(
            user=self.customer_user, email="Cliente@Example.com", normalized_email="cliente@example.com"
        )
        self.other_user = User.objects.create_user(username="otro", password="x", email="otro@example.com")
        self.other = Customer.objects.create(user=self.other_user, email="otro@example.com", normalized_email="otro@example.com")
        self.staff = User.objects.create_user(username="staff", password="x", is_staff=True)
        self.url = reverse("panel:customer_list")

    def login_seller(self):
        self.client.force_login(self.seller)

    def test_only_internal_role_can_access_and_customer_is_rejected(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.customer_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.login_seller()
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_list_search_filter_consent_and_metrics_use_explicit_associations(self):
        CustomerConsent.objects.create(
            customer=self.customer,
            consent_type=CustomerConsent.ConsentType.COMMERCIAL_COMMUNICATIONS,
            granted=True,
            version="v2.0",
            origin="registro",
        )
        booking = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email="different@example.com",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        CustomerBooking.objects.create(customer=self.customer, booking=booking)
        self.login_seller()
        response = self.client.get(self.url, {"q": "CLIENTE@EXAMPLE.COM", "consent": "active", "reservations": "with"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["metrics"]["consented"], 1)
        self.assertContains(response, "cliente@example.com")
        self.assertNotContains(response, "different@example.com")

    def test_detail_does_not_link_by_email_or_expose_sensitive_claim_fields(self):
        booking = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email=self.customer.email,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        CustomerBooking.objects.create(customer=self.customer, booking=booking)
        unlinked = Booking.objects.create(
            channel=BookingChannel.ONLINE,
            status=BookingStatus.HELD,
            email=self.customer.email,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.login_seller()
        response = self.client.get(reverse("panel:customer_detail", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(booking.public_id))
        self.assertNotContains(response, str(unlinked.public_id))
        self.assertNotContains(response, "token_hash")
        self.assertNotContains(response, "google_sub")
        self.assertEqual(
            self.client.get(reverse("panel:customer_detail", args=[self.other.pk])).status_code,
            200,
        )

    def test_all_customer_views_are_get_only(self):
        self.login_seller()
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(self.client.post(reverse("panel:customer_detail", args=[self.customer.pk])).status_code, 405)
