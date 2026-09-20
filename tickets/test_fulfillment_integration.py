from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from payments.services import register_cash_payment, register_transfer_payment, review_transfer_payment
from sales.models import AssignmentStatus, BookingStatus
from sales.services import create_online_booking
from tickets.models import FulfillmentEmailStatus, FulfillmentIssueStatus, Ticket, TicketFulfillment
from tickets.services import issue_tickets_for_booking, process_booking_fulfillment, reconcile_confirmed_fulfillments
from tickets.tests import TicketBaseMixin


class TicketFulfillmentIntegrationTests(TicketBaseMixin, TransactionTestCase):
    reset_sequences = True

    def _held_booking(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        booking.status = BookingStatus.HELD
        booking.confirmed_at = None
        booking.expires_at = timezone.now() + timedelta(hours=2)
        booking.save(update_fields=["status", "confirmed_at", "expires_at", "updated_at"])
        booking.legs.all().first().seat_assignments.update(status=AssignmentStatus.HELD)
        return booking

    def _online_confirmed_booking(self):
        booking = create_online_booking(
            email="comprador@scviajes.com.ar",
            phone="3511234567",
            legs=[{"trip": self.trip_ida, "origin_stop": self.ts_cba, "destination_stop": self.ts_jujuy, "seats": [self.seat_1]}],
            passengers_data=[{"first_name": "Pasajero", "last_name": "Online", "document_type": "DNI", "document_number": "40123456", "birth_date": "1995-05-15", "nationality": "Argentina"}],
            now=self.now,
        )
        booking.status = BookingStatus.CONFIRMED
        booking.confirmed_at = self.now
        booking.save(update_fields=["status", "confirmed_at", "updated_at"])
        booking.legs.first().seat_assignments.update(status=AssignmentStatus.CONFIRMED)
        return booking

    def test_cash_confirmation_creates_one_durable_fulfillment_and_tickets(self):
        booking = self._held_booking()
        register_cash_payment(booking_or_id=booking, seller=self.seller_user)

        booking.refresh_from_db()
        job = TicketFulfillment.objects.get(booking=booking)
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 1)
        self.assertEqual(job.issue_status, FulfillmentIssueStatus.SUCCEEDED)
        self.assertEqual(job.email_status, FulfillmentEmailStatus.SENT)

        process_booking_fulfillment(job.pk, retry=True)
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 1)

    def test_pdf_failure_keeps_payment_and_reconcile_recovers(self):
        booking = self._held_booking()
        with patch("tickets.services.build_ticket_pdf", side_effect=OSError("pdf unavailable")):
            register_cash_payment(booking_or_id=booking, seller=self.seller_user)

        booking.refresh_from_db()
        job = TicketFulfillment.objects.get(booking=booking)
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(job.issue_status, FulfillmentIssueStatus.FAILED)
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 0)

        self.assertEqual(len(reconcile_confirmed_fulfillments()), 1)
        job.refresh_from_db()
        self.assertEqual(job.issue_status, FulfillmentIssueStatus.SUCCEEDED)

    def test_email_failure_is_recoverable_without_new_tickets(self):
        booking = self._held_booking()
        with patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            register_cash_payment(booking_or_id=booking, seller=self.seller_user)
        job = TicketFulfillment.objects.get(booking=booking)
        self.assertEqual(job.email_status, FulfillmentEmailStatus.FAILED)
        ticket_count = Ticket.objects.filter(booking=booking).count()
        process_booking_fulfillment(job.pk, retry=True)
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), ticket_count)
        self.assertEqual(len(mail.outbox), 1)

    def test_guest_download_requires_booking_session_and_rejects_idor(self):
        booking = self._online_confirmed_booking()
        issue_tickets_for_booking(booking)
        ticket = Ticket.objects.get(booking=booking)
        client = Client()
        denied = client.get(reverse("tickets:guest_download", args=[booking.public_id, ticket.public_id]))
        self.assertEqual(denied.status_code, 404)
        session = client.session
        session[f"booking_access_{booking.public_id}"] = "session-token"
        session.save()
        response = client.get(reverse("tickets:guest_download", args=[booking.public_id, ticket.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        other = self.create_confirmed_booking(passenger_count=1, seats_list=[self.seat_2])
        other_ticket = issue_tickets_for_booking(other)[0]
        idor = client.get(reverse("tickets:guest_download", args=[booking.public_id, other_ticket.public_id]))
        self.assertEqual(idor.status_code, 404)

    def test_panel_retry_requires_authorized_role(self):
        booking = self.create_confirmed_booking(passenger_count=1)
        client = Client()
        client.force_login(self.plain_user)
        response = client.post(reverse("panel:booking_fulfillment_retry", args=[booking.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_transfer_approval_confirms_and_fulfills_after_commit(self):
        booking = self._held_booking()
        voucher = SimpleUploadedFile("voucher.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
        payment = register_transfer_payment(booking_or_id=booking, seller=self.seller_user, voucher=voucher)
        review_transfer_payment(payment_or_id=payment, reviewer=self.admin_user, approved=True)

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CONFIRMED)
        self.assertEqual(Ticket.objects.filter(booking=booking).count(), 1)
        job = TicketFulfillment.objects.get(booking=booking)
        self.assertEqual(job.issue_status, FulfillmentIssueStatus.SUCCEEDED)
