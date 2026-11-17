from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from operations.models import Trip
from panel.boarding_views import boarding_reverse, boarding_validate
from panel.trip_services import complete_trip, start_trip
from tickets.boarding import reverse_boarding, validate_boarding
from tickets.models import BoardingRecord, BoardingStatus, TicketAuditEvent
from tickets.services import issue_tickets_for_booking
from .tests import TicketBaseMixin

User = get_user_model()


class BoardingDisabledServiceTests(TicketBaseMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.booking = self.create_confirmed_booking()
        self.ticket = issue_tickets_for_booking(self.booking)[0]
        self.raw_token = self.ticket._raw_verification_token

    def test_validate_boarding_raises_validation_error(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_boarding(self.raw_token, self.trip_ida.pk, self.seller_user, now=self.now)
        self.assertIn("deshabilitado", str(ctx.exception).lower())
        self.assertEqual(BoardingRecord.objects.count(), 0)

    def test_reverse_boarding_raises_validation_error(self):
        # Crear un registro histórico directamente por ORM
        record = BoardingRecord.objects.create(
            ticket=self.ticket,
            passenger=self.ticket.passenger,
            trip=self.trip_ida,
            seat_assignment=self.ticket.seat_assignment,
            operator=self.admin_user,
            boarded_at=self.now,
            status=BoardingStatus.ACTIVE,
        )
        with self.assertRaises(ValidationError) as ctx:
            reverse_boarding(record.pk, self.admin_user, "Motivo histórico", now=self.now)
        self.assertIn("deshabilitada", str(ctx.exception).lower())
        record.refresh_from_db()
        self.assertEqual(record.status, BoardingStatus.ACTIVE)

    def test_historical_boarding_records_and_audit_preserved(self):
        BoardingRecord.objects.create(
            ticket=self.ticket,
            passenger=self.ticket.passenger,
            trip=self.trip_ida,
            seat_assignment=self.ticket.seat_assignment,
            operator=self.admin_user,
            boarded_at=self.now,
            status=BoardingStatus.ACTIVE,
        )
        TicketAuditEvent.objects.create(
            actor=self.admin_user,
            action=TicketAuditEvent.Action.BOARDING,
            ticket=self.ticket,
            booking=self.booking,
            description="Histórico de embarque",
        )
        self.assertEqual(BoardingRecord.objects.filter(ticket=self.ticket).count(), 1)
        self.assertEqual(TicketAuditEvent.objects.filter(ticket=self.ticket).count(), 1)

    def test_public_qr_verification_never_registers_boarding(self):
        response = self.client.get(
            reverse("tickets:verify") + f"?token={self.raw_token}",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BoardingRecord.objects.count(), 0)


class BoardingDisabledPanelTests(TicketBaseMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.booking = self.create_confirmed_booking()
        self.ticket = issue_tickets_for_booking(self.booking)[0]

    def test_retired_boarding_routes_return_404(self):
        # /panel/embarques/ y /panel/embarques/<id>/revertir/ deben retornar 404
        for user in [self.plain_user, self.seller_user, self.admin_user]:
            self.client.force_login(user)
            self.assertEqual(self.client.get("/panel/embarques/").status_code, 404)
            self.assertEqual(self.client.post("/panel/embarques/", {}).status_code, 404)
            self.assertEqual(self.client.get("/panel/embarques/1/revertir/").status_code, 404)
            self.assertEqual(self.client.post("/panel/embarques/1/revertir/", {}).status_code, 404)

    def test_direct_view_calls_raise_validation_error(self):
        from django.test import RequestFactory
        factory = RequestFactory()

        request = factory.post("/panel/embarques/")
        request.user = self.seller_user
        with self.assertRaises(ValidationError):
            boarding_validate(request)

        request_rev = factory.post("/panel/embarques/1/revertir/")
        request_rev.user = self.admin_user
        with self.assertRaises(ValidationError):
            boarding_reverse(request_rev, record_pk=1)

    def test_manifest_excludes_boarding_counters_and_statuses(self):
        manifest_url = reverse("panel:trip_manifest", kwargs={"trip_pk": self.trip_ida.pk})
        csv_url = reverse("panel:trip_manifest_csv", kwargs={"trip_pk": self.trip_ida.pk})

        self.client.force_login(self.seller_user)
        self.assertEqual(self.client.get(manifest_url).status_code, 403)
        self.assertEqual(self.client.get(csv_url).status_code, 403)

        self.client.force_login(self.admin_user)
        html_resp = self.client.get(manifest_url)
        self.assertEqual(html_resp.status_code, 200)
        # No debe contener contadores ni estados de embarque
        self.assertNotContains(html_resp, "Embarcados:")
        self.assertNotContains(html_resp, "Pendientes:")
        self.assertNotContains(html_resp, "Embarcado")
        self.assertContains(html_resp, "Listado de pasajeros confirmados")

        csv_resp = self.client.get(csv_url)
        self.assertEqual(csv_resp.status_code, 200)
        csv_content = csv_resp.content.decode("utf-8-sig")
        self.assertNotIn("Estado de embarque", csv_content)
        self.assertNotIn("Embarcado", csv_content)

    def test_trip_start_and_complete_without_requiring_boarding(self):
        # Admin y superusuario pueden iniciar y finalizar viaje sin exigir embarque
        self.assertEqual(self.trip_ida.status, Trip.Status.SCHEDULED)
        trip, changed = start_trip(actor=self.admin_user, trip_pk=self.trip_ida.pk)
        self.assertTrue(changed)
        self.assertEqual(trip.status, Trip.Status.STARTED)
        self.assertIsNotNone(trip.started_at)

        trip, changed = complete_trip(actor=self.admin_user, trip_pk=self.trip_ida.pk)
        self.assertTrue(changed)
        self.assertEqual(trip.status, Trip.Status.COMPLETED)
        self.assertIsNotNone(trip.completed_at)

    def test_stop_permissions_subida_bajada_preserved(self):
        # Paradas conservan permisos SUBIDA y BAJADA
        ts_cba = self.trip_ida.trip_stops.get(stop=self.stop_cba)
        ts_jujuy = self.trip_ida.trip_stops.get(stop=self.stop_jujuy)
        self.assertTrue(ts_cba.allows_boarding)
        self.assertFalse(ts_cba.allows_alighting)
        self.assertFalse(ts_jujuy.allows_boarding)
        self.assertTrue(ts_jujuy.allows_alighting)
