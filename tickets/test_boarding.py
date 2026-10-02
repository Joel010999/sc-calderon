from datetime import timedelta
import threading
import unittest

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sales.models import AssignmentStatus, BookingStatus

from .boarding import (
    BoardingCode,
    reverse_boarding,
    validate_boarding,
)
from .models import BoardingRecord, BoardingStatus, TicketAuditEvent, TicketStatus
from .services import build_verification_url, issue_tickets_for_booking
from .tests import TicketBaseMixin


User = get_user_model()


class BoardingServiceTests(TicketBaseMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.booking = self.create_confirmed_booking()
        self.ticket = issue_tickets_for_booking(self.booking)[0]
        self.raw_token = self.ticket._raw_verification_token

    def test_valid_qr_registers_boarding_with_all_relations(self):
        result = validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user, now=self.now)

        self.assertEqual(result.code, BoardingCode.VALID)
        record = BoardingRecord.objects.get()
        self.assertEqual(record.ticket_id, self.ticket.pk)
        self.assertEqual(record.passenger_id, self.ticket.passenger_id)
        self.assertEqual(record.trip_id, self.trip_ida.pk)
        self.assertEqual(record.seat_assignment_id, self.ticket.seat_assignment_id)
        self.assertEqual(record.operator_id, self.seller_user.pk)
        self.assertEqual(record.boarded_at, self.now)
        self.assertTrue(TicketAuditEvent.objects.filter(action=TicketAuditEvent.Action.BOARDING).exists())

    def test_qr_url_and_ticket_code_are_accepted(self):
        result_from_url = validate_boarding(
            build_verification_url(self.raw_token), self.trip_ida.pk, self.seller_user, now=self.now
        )
        self.assertEqual(result_from_url.code, BoardingCode.VALID)

        second_ticket = issue_tickets_for_booking(self.create_confirmed_booking(seats_list=[self.seat_2]))[0]
        result_from_code = validate_boarding(second_ticket.ticket_code, self.trip_ida.pk, self.seller_user, now=self.now)
        self.assertEqual(result_from_code.code, BoardingCode.VALID)

    def test_second_scan_is_idempotent_and_does_not_duplicate(self):
        first = validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user, now=self.now)
        second = validate_boarding(self.raw_token, self.trip_ida.pk, self.admin_user, now=self.now + timedelta(minutes=1))

        self.assertEqual(first.code, BoardingCode.VALID)
        self.assertEqual(second.code, BoardingCode.ALREADY_BOARDED)
        self.assertEqual(BoardingRecord.objects.filter(ticket=self.ticket, status=BoardingStatus.ACTIVE).count(), 1)
        self.assertEqual(second.record.operator_id, self.seller_user.pk)

    def test_started_trip_accepts_new_boarding(self):
        self.trip_ida.status = self.trip_ida.Status.STARTED
        self.trip_ida.save(update_fields=["status", "updated_at"])

        result = validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user, now=self.now)

        self.assertEqual(result.code, BoardingCode.VALID)
        self.assertEqual(BoardingRecord.objects.count(), 1)

    def test_completed_trip_rejects_new_boarding_and_preserves_idempotency(self):
        self.trip_ida.status = self.trip_ida.Status.COMPLETED
        self.trip_ida.save(update_fields=["status", "updated_at"])
        rejected = validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user, now=self.now)

        self.assertEqual(rejected.code, BoardingCode.TICKET_NOT_VALID)
        self.assertEqual(BoardingRecord.objects.count(), 0)

        self.trip_ida.status = self.trip_ida.Status.SCHEDULED
        self.trip_ida.save(update_fields=["status", "updated_at"])
        boarded = validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user, now=self.now)
        self.trip_ida.status = self.trip_ida.Status.COMPLETED
        self.trip_ida.save(update_fields=["status", "updated_at"])
        second = validate_boarding(self.raw_token, self.trip_ida.pk, self.admin_user, now=self.now)

        self.assertEqual(boarded.code, BoardingCode.VALID)
        self.assertEqual(second.code, BoardingCode.ALREADY_BOARDED)
        self.assertEqual(BoardingRecord.objects.filter(status=BoardingStatus.ACTIVE).count(), 1)

    def test_round_trip_ticket_only_works_for_its_own_trip(self):
        booking = self.create_confirmed_booking(round_trip=True, seats_list=[self.seat_2])
        tickets = issue_tickets_for_booking(booking)
        outbound = next(ticket for ticket in tickets if ticket.leg.trip_id == self.trip_ida.pk)
        returning = next(ticket for ticket in tickets if ticket.leg.trip_id == self.trip_vuelta.pk)

        wrong = validate_boarding(outbound._raw_verification_token, self.trip_vuelta.pk, self.seller_user, now=self.now)
        right = validate_boarding(returning._raw_verification_token, self.trip_vuelta.pk, self.seller_user, now=self.now)

        self.assertEqual(wrong.code, BoardingCode.WRONG_TRIP)
        self.assertEqual(right.code, BoardingCode.VALID)
        self.assertEqual(BoardingRecord.objects.filter(ticket=outbound).count(), 0)
        self.assertEqual(BoardingRecord.objects.filter(ticket=returning).count(), 1)

    def test_invalid_and_manipulated_codes_are_rejected(self):
        self.assertEqual(
            validate_boarding("TK-NOEXISTE-L1-P1", self.trip_ida.pk, self.seller_user).code,
            BoardingCode.INVALID_TICKET,
        )
        self.assertEqual(BoardingRecord.objects.count(), 0)

    def test_booking_assignment_and_ticket_state_are_revalidated(self):
        self.booking.status = BookingStatus.HELD
        self.booking.save(update_fields=["status", "updated_at"])
        self.assertEqual(
            validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user).code,
            BoardingCode.BOOKING_NOT_CONFIRMED,
        )

        self.booking.status = BookingStatus.CONFIRMED
        self.booking.save(update_fields=["status", "updated_at"])
        self.ticket.seat_assignment.status = AssignmentStatus.HELD
        self.ticket.seat_assignment.save(update_fields=["status", "updated_at"])
        self.assertEqual(
            validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user).code,
            BoardingCode.ASSIGNMENT_NOT_CONFIRMED,
        )

        self.ticket.status = TicketStatus.PENDING
        self.ticket.save(update_fields=["status", "updated_at"])
        self.ticket.seat_assignment.status = AssignmentStatus.CONFIRMED
        self.ticket.seat_assignment.save(update_fields=["status", "updated_at"])
        self.assertEqual(
            validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user).code,
            BoardingCode.TICKET_NOT_VALID,
        )

    def test_only_authorized_roles_can_validate_and_only_admin_can_reverse(self):
        with self.assertRaises(PermissionDenied):
            validate_boarding(self.raw_token, self.trip_ida.pk, self.plain_user)

        result = validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user, now=self.now)
        with self.assertRaises(PermissionDenied):
            reverse_boarding(result.record.pk, self.seller_user, "Corrección")
        with self.assertRaises(ValueError):
            reverse_boarding(result.record.pk, self.admin_user, "")

        reversed_record = reverse_boarding(result.record.pk, self.admin_user, "Corrección de control", now=self.now)
        self.assertEqual(reversed_record.status, BoardingStatus.REVERSED)
        self.assertEqual(BoardingRecord.objects.count(), 1)
        self.assertEqual(TicketAuditEvent.objects.filter(action=TicketAuditEvent.Action.BOARDING_REVERSAL).count(), 1)

    def test_active_boarding_has_database_unique_constraint(self):
        result = validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user, now=self.now)
        with self.assertRaises(IntegrityError):
            BoardingRecord.objects.create(
                ticket=self.ticket,
                passenger=self.ticket.passenger,
                trip=self.trip_ida,
                seat_assignment=self.ticket.seat_assignment,
                operator=self.admin_user,
                boarded_at=self.now,
            )
        self.assertEqual(result.record.pk, BoardingRecord.objects.get().pk)


@override_settings(CSRF_TRUSTED_ORIGINS=["https://testserver"])
class BoardingPanelTests(TicketBaseMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client(enforce_csrf_checks=True)
        self.booking = self.create_confirmed_booking()
        self.ticket = issue_tickets_for_booking(self.booking)[0]
        self.raw_token = self.ticket._raw_verification_token
        self.url = reverse("panel:boarding_validate")

    def _csrf(self):
        self.client.get(self.url, secure=True)
        return self.client.cookies["csrftoken"].value

    def test_panel_permissions_and_csrf(self):
        self.client.force_login(self.seller_user)
        response = self.client.post(self.url, {"trip": self.trip_ida.pk, "scan_value": self.raw_token}, secure=True)
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            self.url,
            {"trip": self.trip_ida.pk, "scan_value": self.raw_token, "csrfmiddlewaretoken": self._csrf()},
            HTTP_REFERER="https://testserver/panel/embarques/",
            secure=True,
        )
        self.assertEqual(response.status_code, 200, response.content.decode("utf-8", errors="replace"))
        self.assertContains(response, "Pasaje válido y embarque registrado")

        self.client.force_login(self.plain_user)
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url, secure=True).status_code, 302)

    def test_reversal_is_post_admin_only_and_requires_reason(self):
        record = validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user).record
        reverse_url = reverse("panel:boarding_reverse", kwargs={"record_pk": record.pk})

        self.client.force_login(self.seller_user)
        self.assertEqual(
            self.client.post(reverse_url, {"reason": "No autorizado", "csrfmiddlewaretoken": self._csrf()}, secure=True).status_code,
            403,
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse_url,
            {"reason": "Corrección administrativa", "csrfmiddlewaretoken": self._csrf()},
            HTTP_REFERER="https://testserver/panel/embarques/",
            secure=True,
        )
        self.assertEqual(response.status_code, 302, response.content.decode("utf-8", errors="replace"))
        self.assertEqual(BoardingRecord.objects.get(pk=record.pk).status, BoardingStatus.REVERSED)

    def test_manifest_shows_boarding_totals_without_writes(self):
        manifest_url = reverse("panel:trip_manifest", kwargs={"trip_pk": self.trip_ida.pk})
        before = (BoardingRecord.objects.count(), TicketAuditEvent.objects.count())
        self.client.force_login(self.seller_user)
        response = self.client.get(manifest_url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Embarcados:</strong> 0")
        self.assertContains(response, "Pendientes:</strong> 1")
        self.assertEqual(before, (BoardingRecord.objects.count(), TicketAuditEvent.objects.count()))

        validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user)
        response = self.client.get(manifest_url, secure=True)
        self.assertContains(response, "Embarcados:</strong> 1")
        self.assertContains(response, "Embarcado")

    def test_public_qr_verification_never_registers_boarding(self):
        response = self.client.get(
            reverse("tickets:verify") + f"?token={self.raw_token}",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BoardingRecord.objects.count(), 0)

    @override_settings(PANEL_BOARDING_INVALID_ATTEMPTS_PER_MINUTE=1)
    def test_invalid_attempts_are_rate_limited_without_storing_scan_value(self):
        self.client.force_login(self.seller_user)
        token = self._csrf()
        data = {"trip": self.trip_ida.pk, "scan_value": "TK-NOEXISTE-L1-P1", "csrfmiddlewaretoken": token}
        first = self.client.post(
            self.url, data, HTTP_REFERER="https://testserver/panel/embarques/", secure=True
        )
        second = self.client.post(
            self.url, data, HTTP_REFERER="https://testserver/panel/embarques/", secure=True
        )
        self.assertEqual(first.context["outcome"].code, BoardingCode.INVALID_TICKET)
        self.assertEqual(
            second.context["outcome"].message,
            "Se alcanzó el límite de intentos inválidos. Probá nuevamente más tarde.",
        )
        self.assertEqual(BoardingRecord.objects.count(), 0)


@unittest.skipUnless(connection.vendor == "postgresql", "La concurrencia real se verifica en PostgreSQL.")
class BoardingConcurrencyTests(TicketBaseMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.booking = self.create_confirmed_booking()
        self.ticket = issue_tickets_for_booking(self.booking)[0]
        self.raw_token = self.ticket._raw_verification_token

    def test_two_concurrent_scans_create_one_active_record(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def scan():
            try:
                barrier.wait(timeout=10)
                results.append(validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user))
            except Exception as exc:  # pragma: no cover - solo informa fallos del worker de prueba
                errors.append(exc)

        threads = [threading.Thread(target=scan) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(errors)
        self.assertEqual({result.code for result in results}, {BoardingCode.VALID, BoardingCode.ALREADY_BOARDED})
        self.assertEqual(BoardingRecord.objects.filter(ticket=self.ticket, status=BoardingStatus.ACTIVE).count(), 1)
