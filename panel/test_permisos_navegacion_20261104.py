"""Pruebas integrales de navegación, roles, aislamiento de vendedores y pasajes esperados."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import os
import shutil
import tempfile
import uuid
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from panel.inbox_services import build_inbox
from payments.models import Payment, PaymentMethod, PaymentStatus
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)
from tickets.models import (
    FulfillmentEmailStatus,
    FulfillmentIssueStatus,
    Ticket,
    TicketFulfillment,
    TicketStatus,
)

User = get_user_model()
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class PermisosNavegacionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_group = Group.objects.create(name="Administrador")
        cls.seller_group = Group.objects.create(name="Vendedor")

        cls.superuser = User.objects.create_superuser(
            username="super_user", email="super@sc.invalid", password="password123"
        )
        cls.admin = User.objects.create_user(
            username="admin_user", email="admin@sc.invalid", password="password123"
        )
        cls.admin.groups.add(cls.admin_group)

        cls.seller_a = User.objects.create_user(
            username="seller_a", email="sellera@sc.invalid", password="password123"
        )
        cls.seller_a.groups.add(cls.seller_group)

        cls.seller_b = User.objects.create_user(
            username="seller_b", email="sellerb@sc.invalid", password="password123"
        )
        cls.seller_b.groups.add(cls.seller_group)

        # Configuración de paradas y recorridos
        cls.stop_orig = Stop.objects.create(code="CBA", name="Córdoba Capital", province="Córdoba")
        cls.stop_dest = Stop.objects.create(code="JUJ", name="Jujuy", province="Jujuy")

        cls.route = Route.objects.create(code="CBA-JUJ", name="Córdoba a Jujuy")
        cls.rs1 = RouteStop.objects.create(route=cls.route, stop=cls.stop_orig, sequence=1, allows_boarding=True, allows_alighting=False)
        cls.rs2 = RouteStop.objects.create(route=cls.route, stop=cls.stop_dest, sequence=2, allows_boarding=False, allows_alighting=True)

        cls.bus = Bus.objects.create(code="BUS1", display_name="Interno 1")
        cls.seat1 = Seat.objects.create(bus=cls.bus, number=1, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=0, position_y=0)
        cls.seat2 = Seat.objects.create(bus=cls.bus, number=2, deck=Seat.Deck.UPPER, category=SeatCategory.SEMI_CAMA, position_x=1, position_y=0)

        now = timezone.now()
        cls.trip = Trip.objects.create(
            route=cls.route,
            bus=cls.bus,
            status=Trip.Status.SCHEDULED,
            departure_at=now + timedelta(days=2),
        )
        cls.ts1 = TripStop.objects.create(trip=cls.trip, stop=cls.stop_orig, sequence=1, scheduled_at=now + timedelta(days=2), allows_boarding=True, allows_alighting=False)
        cls.ts2 = TripStop.objects.create(trip=cls.trip, stop=cls.stop_dest, sequence=2, scheduled_at=now + timedelta(days=2, hours=10), allows_boarding=False, allows_alighting=True)
        cls.fare = TripFare.objects.create(trip=cls.trip, seat_category=SeatCategory.SEMI_CAMA, amount=Decimal("15000.00"), currency="ARS")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.temp_storage = tempfile.mkdtemp()
        self.settings_override = override_settings(
            TICKETS_STORAGE_ROOT=self.temp_storage,
            PROTECTED_MEDIA_ROOT=self.temp_storage,
            MEDIA_ROOT=self.temp_storage,
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        )
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.temp_storage, ignore_errors=True)

    # =========================================================================
    # 1. Navegación, redirecciones y control de acceso a operaciones
    # =========================================================================

    def test_operations_overview_redirects_to_trips(self):
        """El resumen de Operaciones se eliminó y redirige directamente a Viajes."""
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("panel:operations"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("panel:trips"))

    def test_buses_accessible_only_to_admin_and_superuser(self):
        """Colectivos visible y accesible solo a Administrador y Superusuario."""
        self.client.force_login(self.seller_a)
        resp_list = self.client.get(reverse("panel:buses"))
        self.assertEqual(resp_list.status_code, 403)

        resp_detail = self.client.get(reverse("panel:bus_detail", kwargs={"pk": self.bus.pk}))
        self.assertEqual(resp_detail.status_code, 403)

        # Admin y Superusuario tienen acceso 200 OK
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("panel:buses")).status_code, 200)
        self.assertEqual(self.client.get(reverse("panel:bus_detail", kwargs={"pk": self.bus.pk})).status_code, 200)

        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("panel:buses")).status_code, 200)
        self.assertEqual(self.client.get(reverse("panel:bus_detail", kwargs={"pk": self.bus.pk})).status_code, 200)

    def test_sellers_cannot_modify_operations(self):
        """Vendedores no pueden escribir recorridos, paradas, colectivos, viajes, horarios ni tarifas."""
        self.client.force_login(self.seller_a)

        # Colectivos
        self.assertEqual(self.client.get(reverse("panel:bus_create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel:bus_create"), {}).status_code, 403)

        # Recorridos y paradas
        self.assertEqual(self.client.get(reverse("panel:route_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("panel:stop_create")).status_code, 403)

        # Viajes, inicio y finalización
        self.assertEqual(self.client.get(reverse("panel:trip_create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel:trip_start", kwargs={"trip_pk": self.trip.pk})).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel:trip_complete", kwargs={"trip_pk": self.trip.pk})).status_code, 403)

        # Horarios y tarifas
        self.assertEqual(self.client.get(reverse("panel:trip_stops_schedule", kwargs={"trip_pk": self.trip.pk})).status_code, 403)
        self.assertEqual(self.client.get(reverse("panel:fare_create", kwargs={"trip_pk": self.trip.pk})).status_code, 403)

    def test_trip_detail_rendered_and_pending_warning_semantics(self):
        """Verifica la compilación/render de trip_detail.html y semántica de pendientes según rol."""
        self._create_booking_and_payment(self.seller_a, status=BookingStatus.HELD)

        # Camino Vendedor: compila y renderiza (200), pero no ve aviso de pendientes ni manifiesto
        self.client.force_login(self.seller_a)
        resp_seller = self.client.get(reverse("panel:trip_detail", kwargs={"pk": self.trip.pk}))
        self.assertEqual(resp_seller.status_code, 200)
        self.assertNotContains(resp_seller, "No se liberan automáticamente.")
        self.assertNotContains(resp_seller, "Ver manifiesto de pasajeros")

        # Camino Administrador: compila y renderiza (200), viendo manifiesto y aviso de pendientes HELD
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("panel:trip_detail", kwargs={"pk": self.trip.pk}))
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, "Ver manifiesto de pasajeros")
        self.assertContains(resp_admin, "No se liberan automáticamente.")

    def test_sidebar_navigation_links_by_role(self):
        """Verifica que el menú lateral muestre solo los enlaces autorizados."""
        # Vendedor: No Nueva reserva, no Clientes, no Auditoría, no Usuarios, no Colectivos
        self.client.force_login(self.seller_a)
        resp = self.client.get(reverse("panel:dashboard"))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")

        self.assertNotIn("Nueva reserva", content)
        self.assertNotIn(reverse("panel:customer_list"), content)
        self.assertNotIn(reverse("panel:audit_list"), content)
        self.assertNotIn(reverse("panel:staff_list"), content)
        self.assertNotIn(reverse("panel:buses"), content)
        self.assertIn(reverse("panel:trips"), content)
        self.assertIn(reverse("panel:payment_list"), content)
        self.assertIn("Venta interna", content)

        # Admin: Clientes, Auditoría, Usuarios, Colectivos sí están presentes
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("panel:dashboard"))
        content_admin = resp_admin.content.decode("utf-8")
        self.assertIn(reverse("panel:customer_list"), content_admin)
        self.assertIn(reverse("panel:audit_list"), content_admin)
        self.assertIn(reverse("panel:staff_list"), content_admin)
        self.assertIn(reverse("panel:buses"), content_admin)

    def test_reservations_list_header_buttons_and_seller_dropdown(self):
        """Entrega de pasajes en Reservas; Nueva reserva manual solo admin; dropdown de vendedores aislado."""
        self.client.force_login(self.seller_a)
        resp_seller = self.client.get(reverse("panel:booking_list"))
        self.assertEqual(resp_seller.status_code, 200)
        c_seller = resp_seller.content.decode("utf-8")

        # Botón Entrega de pasajes presente
        self.assertIn(reverse("panel:fulfillment_list"), c_seller)
        self.assertIn("Entrega de pasajes", c_seller)
        # Venta interna presente
        self.assertIn("Venta interna", c_seller)
        # Nueva reserva manual NO debe aparecer para el vendedor
        self.assertNotIn("Nueva reserva manual", c_seller)
        # Dropdown de vendedores solo contiene al propio vendedor, sin nombres ajenos
        self.assertIn('name="seller"', c_seller)
        self.assertIn(f'>{self.seller_a.username}<', c_seller)
        self.assertNotIn(f'>{self.seller_b.username}<', c_seller)

        # Para el Administrador: Nueva reserva manual presente
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("panel:booking_list"))
        c_admin = resp_admin.content.decode("utf-8")
        self.assertIn("Nueva reserva manual", c_admin)
        self.assertIn(reverse("panel:fulfillment_list"), c_admin)

    def test_payments_list_header_buttons_and_seller_dropdown(self):
        """En Pagos están Ver transferencias pendientes y Reporte de ventas; dropdown aislado."""
        self.client.force_login(self.seller_a)
        resp = self.client.get(reverse("panel:payment_list"))
        self.assertEqual(resp.status_code, 200)
        c = resp.content.decode("utf-8")

        self.assertIn(reverse("panel:pending_transfers"), c)
        self.assertIn("Ver transferencias pendientes", c)
        self.assertIn(reverse("panel:sales_report"), c)
        self.assertIn("Reporte de ventas", c)
        # Dropdown de vendedores solo contiene al propio vendedor, sin nombres ajenos
        self.assertIn('name="seller"', c)
        self.assertIn(f'>{self.seller_a.username}<', c)
        self.assertNotIn(f'>{self.seller_b.username}<', c)

    # =========================================================================
    # 2. Aislamiento server-side Vendedor A vs Vendedor B
    # =========================================================================

    def _create_ticket(self, booking, leg, passenger, sa, code, pdf_name="test.pdf", status=TicketStatus.ISSUED):
        return Ticket.objects.create(
            booking=booking,
            leg=leg,
            passenger=passenger,
            seat_assignment=sa,
            ticket_code=code,
            verification_token_hash=f"v_{code}",
            download_token_hash=f"d_{code}",
            pdf_path=pdf_name,
            status=status,
            issued_at=timezone.now(),
            passenger_name=f"{passenger.first_name} {passenger.last_name}",
            passenger_document_masked=f"DNI ***{passenger.document_number[-3:]}",
            origin_stop_name=leg.origin_stop_name,
            destination_stop_name=leg.destination_stop_name,
            departure_at=leg.departure_at,
            arrival_at=leg.arrival_at,
            price=sa.price if sa else Decimal("0.00"),
            currency="ARS",
            booking_public_id=booking.public_id,
        )

    def _create_booking_and_payment(self, seller, status=BookingStatus.CONFIRMED, payment_status=PaymentStatus.APPROVED, amount=Decimal("15000.00")):
        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            status=status,
            seller=seller,
            email=f"{seller.username}@client.invalid",
            phone="351999999",
            confirmed_at=timezone.now() if status == BookingStatus.CONFIRMED else None,
            expires_at=timezone.now() + timedelta(hours=2) if status == BookingStatus.HELD else None,
        )
        leg = BookingLeg.objects.create(
            booking=booking,
            trip=self.trip,
            origin_stop=self.ts1,
            destination_stop=self.ts2,
            origin_stop_name=self.ts1.stop.name,
            destination_stop_name=self.ts2.stop.name,
            departure_at=self.ts1.scheduled_at,
            arrival_at=self.ts2.scheduled_at,
            sequence=1,
        )
        passenger = BookingPassenger.objects.create(
            booking=booking,
            first_name="Pasajero",
            last_name=seller.username,
            document_type="DNI",
            document_number=f"99{seller.pk}00",
            position=1,
            is_lap_infant=False,
        )
        seat_obj = self.seat1 if seller == self.seller_a else self.seat2
        SeatAssignment.objects.create(
            leg=leg,
            passenger=passenger,
            trip=self.trip,
            seat=seat_obj,
            seat_number=seat_obj.number,
            category=seat_obj.category,
            status=AssignmentStatus.CONFIRMED if status == BookingStatus.CONFIRMED else AssignmentStatus.HELD,
            price=amount,
            currency="ARS",
        )
        payment = Payment.objects.create(
            booking=booking,
            registered_by=seller,
            method=PaymentMethod.BANK_TRANSFER,
            status=payment_status,
            amount=amount,
            currency="ARS",
            proof_deadline_at=timezone.now() + timedelta(hours=1),
            review_deadline_at=timezone.now() + timedelta(hours=2),
        )
        return booking, payment

    def test_dashboard_metrics_isolated_by_seller(self):
        """Dashboard/métricas propias para cada vendedor; agregación global para administración."""
        self._create_booking_and_payment(self.seller_a, BookingStatus.CONFIRMED, PaymentStatus.APPROVED, Decimal("15000.00"))
        self._create_booking_and_payment(self.seller_b, BookingStatus.CONFIRMED, PaymentStatus.APPROVED, Decimal("25000.00"))

        # Vendedor A ve solo sus 15000
        self.client.force_login(self.seller_a)
        resp_a = self.client.get(reverse("panel:dashboard"))
        self.assertEqual(resp_a.context["confirmed_revenue_total"], Decimal("15000.00"))
        self.assertEqual(resp_a.context["confirmed_reservations_count"], 1)

        # Vendedor B ve solo sus 25000
        self.client.force_login(self.seller_b)
        resp_b = self.client.get(reverse("panel:dashboard"))
        self.assertEqual(resp_b.context["confirmed_revenue_total"], Decimal("25000.00"))
        self.assertEqual(resp_b.context["confirmed_reservations_count"], 1)

        # Admin ve el total global 40000
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("panel:dashboard"))
        self.assertEqual(resp_admin.context["confirmed_revenue_total"], Decimal("40000.00"))
        self.assertEqual(resp_admin.context["confirmed_reservations_count"], 2)

    def test_reservations_and_payments_detail_blocking_alien_ids(self):
        """Vendedor A no puede acceder al detalle de reservas ni pagos de Vendedor B (404)."""
        booking_b, payment_b = self._create_booking_and_payment(self.seller_b, BookingStatus.CONFIRMED, PaymentStatus.APPROVED)

        self.client.force_login(self.seller_a)
        # Detalle de reserva ajena -> 404
        resp_res = self.client.get(reverse("panel:booking_detail", kwargs={"public_id": booking_b.public_id}))
        self.assertEqual(resp_res.status_code, 404)

        # Detalle de pago ajeno -> 404
        resp_pay = self.client.get(reverse("panel:payment_detail", kwargs={"public_id": payment_b.public_id}))
        self.assertEqual(resp_pay.status_code, 404)

        # Liberación de reserva ajena -> 404
        resp_rel = self.client.post(reverse("panel:booking_release", kwargs={"public_id": booking_b.public_id}))
        self.assertEqual(resp_rel.status_code, 404)

        # Reintento de fulfillment ajeno -> 404
        resp_ret = self.client.post(reverse("panel:booking_fulfillment_retry", kwargs={"public_id": booking_b.public_id}))
        self.assertEqual(resp_ret.status_code, 404)

        # Admin puede acceder a ambos sin restricciones (200)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("panel:booking_detail", kwargs={"public_id": booking_b.public_id})).status_code, 200)
        self.assertEqual(self.client.get(reverse("panel:payment_detail", kwargs={"public_id": payment_b.public_id})).status_code, 200)

    def test_reservations_and_payments_filter_alien_seller_ignored(self):
        """Filtrar por ID de otro vendedor en URLs no filtra ni expone ventas ajenas."""
        booking_a, payment_a = self._create_booking_and_payment(self.seller_a)
        booking_b, payment_b = self._create_booking_and_payment(self.seller_b)

        self.client.force_login(self.seller_a)

        # Reservas: Vendedor A filtra por seller_b -> no devuelve reservas de B
        resp_res = self.client.get(reverse("panel:booking_list") + f"?seller={self.seller_b.pk}")
        bookings_in_page = list(resp_res.context["page_obj"])
        self.assertNotIn(booking_b, bookings_in_page)

        # Pagos: Vendedor A filtra por seller_b -> no devuelve pagos de B
        resp_pay = self.client.get(reverse("panel:payment_list") + f"?seller={self.seller_b.pk}")
        payments_in_page = list(resp_pay.context["page_obj"])
        self.assertNotIn(payment_b, payments_in_page)

    def test_transfer_review_exclusive_to_administrators(self):
        """Vendedores solo pueden revisar y aprobar sus propias ventas; ajenas aisladas."""
        booking_a, payment_a = self._create_booking_and_payment(self.seller_a, BookingStatus.HELD, PaymentStatus.UNDER_REVIEW)

        self.client.force_login(self.seller_a)
        # En pending_transfers, can_review es True para el vendedor
        resp_pend = self.client.get(reverse("panel:pending_transfers"))
        self.assertEqual(resp_pend.status_code, 200)
        self.assertTrue(resp_pend.context["can_review"])

        # Vendedor dueño puede aprobar su propia venta
        resp_rev = self.client.post(
            reverse("panel:transfer_review", kwargs={"public_id": payment_a.public_id}),
            {"action": "approve"},
        )
        self.assertEqual(resp_rev.status_code, 302)
        payment_a.refresh_from_db()
        self.assertEqual(payment_a.status, PaymentStatus.APPROVED)

        # Vendedor B intenta acceder a la venta de A -> 404 (aislamiento server-side)
        self.client.force_login(self.seller_b)
        resp_b = self.client.post(
            reverse("panel:transfer_review", kwargs={"public_id": payment_a.public_id}),
            {"action": "approve"},
        )
        self.assertEqual(resp_b.status_code, 404)

        # Administrador puede aprobar cualquier venta
        _, payment_b = self._create_booking_and_payment(self.seller_b, BookingStatus.HELD, PaymentStatus.UNDER_REVIEW)
        self.client.force_login(self.admin)
        resp_admin = self.client.post(
            reverse("panel:transfer_review", kwargs={"public_id": payment_b.public_id}),
            {"action": "approve"},
        )
        self.assertEqual(resp_admin.status_code, 302)
        payment_b.refresh_from_db()
        self.assertEqual(payment_b.status, PaymentStatus.APPROVED)

    def test_fulfillment_list_and_reconcile_isolation(self):
        """Entrega de pasajes y reconciliación server-side aisladas por vendedor."""
        booking_a, _ = self._create_booking_and_payment(self.seller_a, BookingStatus.CONFIRMED)
        booking_b, _ = self._create_booking_and_payment(self.seller_b, BookingStatus.CONFIRMED)

        job_a = TicketFulfillment.objects.create(booking=booking_a, issue_status=FulfillmentIssueStatus.FAILED)
        job_b = TicketFulfillment.objects.create(booking=booking_b, issue_status=FulfillmentIssueStatus.FAILED)

        self.client.force_login(self.seller_a)
        resp = self.client.get(reverse("panel:fulfillment_list"))
        fulfillments_shown = list(resp.context["fulfillments"])
        self.assertIn(job_a, fulfillments_shown)
        self.assertNotIn(job_b, fulfillments_shown)

        # Intento de reconciliar job_b de Vendedor B por Vendedor A
        resp_recon = self.client.post(
            reverse("panel:fulfillment_reconcile"),
            {"fulfillment_ids": [str(job_b.pk)]},
        )
        self.assertEqual(resp_recon.status_code, 302)
        # El job_b no debió ser procesado
        job_b.refresh_from_db()
        self.assertEqual(job_b.issue_status, FulfillmentIssueStatus.FAILED)

    def test_ticket_and_voucher_downloads_isolated(self):
        """Descarga de pasajes y comprobantes aisladas por vendedor (404 a pasajes/comprobantes ajenos)."""
        booking_a, payment_a = self._create_booking_and_payment(self.seller_a, BookingStatus.CONFIRMED)
        booking_b, payment_b = self._create_booking_and_payment(self.seller_b, BookingStatus.CONFIRMED)

        # Crear archivos falsos en temp_storage
        pdf_name_b = "ticket_b.pdf"
        full_pdf_path = os.path.join(self.temp_storage, pdf_name_b)
        with open(full_pdf_path, "wb") as f:
            f.write(b"%PDF-1.4 mock content")

        ticket_b = self._create_ticket(
            booking_b, booking_b.legs.first(), booking_b.passengers.first(),
            booking_b.legs.first().seat_assignments.first(), "TCK-B-1", pdf_name=pdf_name_b
        )

        voucher_file = SimpleUploadedFile("comprobante_b.jpg", b"fake image bytes", content_type="image/jpeg")
        payment_b.voucher = voucher_file
        payment_b.save(update_fields=["voucher"])

        self.client.force_login(self.seller_a)

        # Vendedor A intentando descargar pasaje de B -> 404
        resp_tck = self.client.get(reverse("tickets:download", kwargs={"public_id": ticket_b.public_id}))
        self.assertEqual(resp_tck.status_code, 404)

        # Vendedor A intentando descargar comprobante de B -> 404
        resp_vch = self.client.get(reverse("panel:payment_voucher", kwargs={"public_id": payment_b.public_id}))
        self.assertEqual(resp_vch.status_code, 404)

        # Admin puede descargar ambos
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("tickets:download", kwargs={"public_id": ticket_b.public_id})).status_code, 200)
        self.assertEqual(self.client.get(reverse("panel:payment_voucher", kwargs={"public_id": payment_b.public_id})).status_code, 200)

    def test_operational_inbox_isolated_by_seller(self):
        """Bandeja operativa solo incluye elementos propios para vendedores."""
        now = timezone.now()
        booking_a, payment_a = self._create_booking_and_payment(self.seller_a, BookingStatus.HELD, PaymentStatus.UNDER_REVIEW)
        booking_b, payment_b = self._create_booking_and_payment(self.seller_b, BookingStatus.HELD, PaymentStatus.UNDER_REVIEW)

        booking_a.expires_at = now + timedelta(minutes=60)
        booking_a.save(update_fields=["expires_at"])
        booking_b.expires_at = now + timedelta(minutes=60)
        booking_b.save(update_fields=["expires_at"])

        # Bandeja para Vendedor A
        inbox_a = build_inbox(user=self.seller_a, now=now)
        refs_a = [item.public_ref for item in inbox_a["page"]]
        self.assertIn(str(booking_a.public_id), refs_a)
        self.assertIn(str(payment_a.public_id), refs_a)
        self.assertNotIn(str(booking_b.public_id), refs_a)
        self.assertNotIn(str(payment_b.public_id), refs_a)
        # Vendedores no reciben items de tipo trip
        self.assertFalse(any(item.category == "trip" for item in inbox_a["page"]))

        # Bandeja para Admin: incluye ambos y categoría trip
        inbox_admin = build_inbox(user=self.admin, now=now)
        refs_admin = [item.public_ref for item in inbox_admin["page"]]
        self.assertIn(str(booking_a.public_id), refs_admin)
        self.assertIn(str(payment_a.public_id), refs_admin)
        self.assertIn(str(booking_b.public_id), refs_admin)
        self.assertIn(str(payment_b.public_id), refs_admin)

    def test_customers_access_forbidden_to_sellers(self):
        """Directorio global de clientes prohibido a vendedores (403)."""
        self.client.force_login(self.seller_a)
        self.assertEqual(self.client.get(reverse("panel:customer_list")).status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("panel:customer_list")).status_code, 200)

    # =========================================================================
    # 3. Pasajes esperados con menores gratuitos ida e ida/vuelta
    # =========================================================================

    def test_expected_tickets_one_way_with_lap_infant(self):
        """1 tramo, 1 adulto + 1 menor gratuito (2 esperados, 0 butaca para menor)."""
        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            status=BookingStatus.CONFIRMED,
            seller=self.seller_a,
            email="infant_oneway@test.invalid",
            confirmed_at=timezone.now(),
        )
        leg = BookingLeg.objects.create(
            booking=booking,
            trip=self.trip,
            origin_stop=self.ts1,
            destination_stop=self.ts2,
            origin_stop_name=self.ts1.stop.name,
            destination_stop_name=self.ts2.stop.name,
            departure_at=self.ts1.scheduled_at,
            arrival_at=self.ts2.scheduled_at,
            sequence=1,
        )
        adult = BookingPassenger.objects.create(
            booking=booking,
            first_name="Adulto",
            last_name="Uno",
            document_type="DNI",
            document_number="11111111",
            position=1,
            is_lap_infant=False,
        )
        infant = BookingPassenger.objects.create(
            booking=booking,
            first_name="Bebe",
            last_name="Uno",
            document_type="DNI",
            document_number="22222222",
            position=2,
            is_lap_infant=True,
            accompanied_by=adult,
        )
        SeatAssignment.objects.create(
            leg=leg,
            passenger=adult,
            trip=self.trip,
            seat=self.seat1,
            seat_number=self.seat1.number,
            category=self.seat1.category,
            status=AssignmentStatus.CONFIRMED,
            price=Decimal("15000.00"),
            currency="ARS",
        )
        # Menor gratuito no tiene SeatAssignment

        # Caso A: Solo 1 pasaje emitido (adulto). Debe reportarse incompleto (2 esperados > 1 emitido)
        self._create_ticket(booking, leg, adult, leg.seat_assignments.first(), "TCK-ADULT-1", pdf_name="adult1.pdf")
        inbox_1 = build_inbox(user=self.admin, params={"type": "tickets"})
        refs_1 = [i.public_ref for i in inbox_1["page"]]
        self.assertIn(str(booking.public_id), refs_1)

        # Caso B: Se emite el segundo pasaje (menor gratuito). Ya no es incompleto
        self._create_ticket(booking, leg, infant, None, "TCK-INFANT-1", pdf_name="infant1.pdf")
        inbox_2 = build_inbox(user=self.admin, params={"type": "tickets"})
        refs_2 = [i.public_ref for i in inbox_2["page"]]
        self.assertNotIn(str(booking.public_id), refs_2)

    def test_expected_tickets_roundtrip_with_lap_infant(self):
        """2 tramos (ida y vuelta), 1 adulto + 1 menor gratuito (4 esperados)."""
        trip_ret = Trip.objects.create(
            route=self.route,
            bus=self.bus,
            status=Trip.Status.SCHEDULED,
            departure_at=timezone.now() + timedelta(days=5),
        )
        ts_ret1 = TripStop.objects.create(trip=trip_ret, stop=self.stop_dest, sequence=1, scheduled_at=timezone.now() + timedelta(days=5), allows_boarding=True, allows_alighting=False)
        ts_ret2 = TripStop.objects.create(trip=trip_ret, stop=self.stop_orig, sequence=2, scheduled_at=timezone.now() + timedelta(days=5, hours=10), allows_boarding=False, allows_alighting=True)

        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            status=BookingStatus.CONFIRMED,
            seller=self.seller_a,
            email="infant_roundtrip@test.invalid",
            confirmed_at=timezone.now(),
        )
        leg1 = BookingLeg.objects.create(
            booking=booking, trip=self.trip, origin_stop=self.ts1, destination_stop=self.ts2,
            origin_stop_name=self.ts1.stop.name, destination_stop_name=self.ts2.stop.name,
            departure_at=self.ts1.scheduled_at, arrival_at=self.ts2.scheduled_at, sequence=1,
        )
        leg2 = BookingLeg.objects.create(
            booking=booking, trip=trip_ret, origin_stop=ts_ret1, destination_stop=ts_ret2,
            origin_stop_name=ts_ret1.stop.name, destination_stop_name=ts_ret2.stop.name,
            departure_at=ts_ret1.scheduled_at, arrival_at=ts_ret2.scheduled_at, sequence=2,
        )

        adult = BookingPassenger.objects.create(
            booking=booking, first_name="Adulto", last_name="Dos", document_type="DNI", document_number="33333333", position=1, is_lap_infant=False,
        )
        infant = BookingPassenger.objects.create(
            booking=booking, first_name="Bebe", last_name="Dos", document_type="DNI", document_number="44444444", position=2, is_lap_infant=True, accompanied_by=adult,
        )

        sa1 = SeatAssignment.objects.create(
            leg=leg1, passenger=adult, trip=self.trip, seat=self.seat1,
            seat_number=self.seat1.number, category=self.seat1.category,
            status=AssignmentStatus.CONFIRMED, price=Decimal("15000.00"), currency="ARS",
        )
        sa2 = SeatAssignment.objects.create(
            leg=leg2, passenger=adult, trip=trip_ret, seat=self.seat1,
            seat_number=self.seat1.number, category=self.seat1.category,
            status=AssignmentStatus.CONFIRMED, price=Decimal("15000.00"), currency="ARS",
        )

        # Caso A: Solo 2 pasajes emitidos (solo adulto ida y vuelta). Faltan los 2 del menor (4 esperados > 2 emitidos)
        self._create_ticket(booking, leg1, adult, sa1, "T-A-1", pdf_name="p1.pdf")
        self._create_ticket(booking, leg2, adult, sa2, "T-A-2", pdf_name="p2.pdf")

        inbox_1 = build_inbox(user=self.admin, params={"type": "tickets"})
        self.assertIn(str(booking.public_id), [i.public_ref for i in inbox_1["page"]])

        # Caso B: Se emiten los 4 pasajes completos. Ya no aparece como incompleto
        self._create_ticket(booking, leg1, infant, None, "T-I-1", pdf_name="p3.pdf")
        self._create_ticket(booking, leg2, infant, None, "T-I-2", pdf_name="p4.pdf")

        inbox_2 = build_inbox(user=self.admin, params={"type": "tickets"})
        self.assertNotIn(str(booking.public_id), [i.public_ref for i in inbox_2["page"]])
