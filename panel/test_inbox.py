from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from payments.models import Payment, PaymentMethod, PaymentStatus
from sales.models import BookingStatus
from sales.services import create_manual_booking

from .test_reservations import ReservationPanelBaseTestCase


class OperationalInboxTests(ReservationPanelBaseTestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse("panel:operational_inbox")

    def held_booking(self, *, expires_at):
        booking = create_manual_booking(
            seller=self.user_seller,
            email="inbox@example.com",
            legs=[{"trip": self.trip_outbound, "origin_stop": self.ts_out_cba,
                   "destination_stop": self.ts_out_ssj, "seats": [self.seat_cama_1]}],
            passengers_data=[{"first_name": "Ana", "last_name": "Prueba", "document_type": "DNI",
                              "document_number": "30111222", "birth_date": "1990-01-01",
                              "nationality": "Argentina", "gender": ""}],
        )
        booking.expires_at = expires_at
        booking.save(update_fields=["expires_at", "updated_at"])
        return booking

    def test_admin_and_seller_can_read_and_common_user_is_rejected(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.user_seller)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.user_common)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_expired_hold_and_review_appear_without_pii(self):
        now = timezone.now()
        booking = self.held_booking(expires_at=now - timedelta(minutes=5))
        Payment.objects.create(booking=booking, method=PaymentMethod.BANK_TRANSFER,
                               status=PaymentStatus.UNDER_REVIEW, amount="100.00", currency="ARS",
                               review_deadline_at=now + timedelta(hours=1))
        self.client.force_login(self.user_seller)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vencida")
        self.assertContains(response, "En revisión")
        self.assertNotContains(response, "inbox@example.com")

    def test_filter_and_pagination_count_before_page(self):
        now = timezone.now()
        booking = self.held_booking(expires_at=now + timedelta(minutes=30))
        self.client.force_login(self.user_seller)
        response = self.client.get(self.url, {"type": "held", "q": str(booking.public_id), "page": "1"})
        self.assertEqual(response.context["total"], 1)
        self.assertContains(response, str(booking.public_id))
