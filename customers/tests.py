"""Pruebas integrales de cuentas de clientes, autenticaciÃƒÂ³n por correo normalizado,





recuperaciÃƒÂ³n de contraseÃƒÂ±a por enlace, separaciÃƒÂ³n de panel, reclamo de reservas invitadas,


descarga de pasajes, consentimiento comercial auditable y Google OAuth configurable por env.


"""





import hashlib


import json


from decimal import Decimal


from django.conf import settings


from django.contrib.auth import get_user_model


from django.contrib.auth.models import Group


from django.contrib.auth.tokens import default_token_generator


from django.core import mail


from django.test import Client, TestCase


from django.urls import reverse


from django.utils import timezone


from django.utils.encoding import force_bytes


from django.utils.http import urlsafe_base64_encode





from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripStop


from sales.models import (


    AssignmentStatus,


    Booking,


    BookingChannel,


    BookingLeg,


    BookingPassenger,


    BookingStatus,


    SeatAssignment,


)


from tickets.models import Ticket, TicketStatus


from tickets.storage import get_ticket_storage





from .models import (


    BookingClaimToken,


    Customer,


    CustomerBooking,


    CustomerConsent,


    normalize_email,


)


from .services import (


    associate_booking_with_customer,


    claim_booking_with_token,


    create_booking_claim_token,


    register_customer,


)





User = get_user_model()








class BaseCustomerTestCase(TestCase):


    """ConfiguraciÃƒÂ³n base de infraestructura de datos para pruebas de clientes."""





    def setUp(self):


        super().setUp()


        self.client = Client()





        # Paradas


        self.stop_cba = Stop.objects.create(name="CÃ³rdoba Capital", code="CBA", city="CÃ³rdoba", province="CÃ³rdoba")


        self.stop_juj = Stop.objects.create(name="San Salvador de Jujuy", code="JUJ", city="San Salvador de Jujuy", province="Jujuy")





        # Recorrido


        self.route = Route.objects.create(name="CÃƒÂ³rdoba Ã¢â€ â€™ Jujuy", code="CBA-JUJ", is_active=True)


        RouteStop.objects.create(route=self.route, stop=self.stop_cba, sequence=1, allows_boarding=True, allows_alighting=False)


        RouteStop.objects.create(route=self.route, stop=self.stop_juj, sequence=2, allows_boarding=False, allows_alighting=True)





        # Colectivo y butacas


        self.bus = Bus.objects.create(display_name="Interno 101", code="INT-101", is_active=True)


        self.seat1 = Seat.objects.create(bus=self.bus, number=1, category=SeatCategory.SEMI_CAMA, deck=Seat.Deck.UPPER, position_x=0, position_y=0, is_active=True)


        self.seat2 = Seat.objects.create(bus=self.bus, number=2, category=SeatCategory.CAMA, deck=Seat.Deck.LOWER, position_x=0, position_y=0, is_active=True)





        # Viaje programado


        now = timezone.now()


        self.departure = now + timezone.timedelta(days=2)


        self.arrival = self.departure + timezone.timedelta(hours=12)





        self.trip = Trip.objects.create(


            route=self.route,


            bus=self.bus,


            departure_at=self.departure,


            status=Trip.Status.SCHEDULED,


        )


        self.ts_orig = TripStop.objects.create(


            trip=self.trip,


            stop=self.stop_cba,


            sequence=1,


            scheduled_at=self.departure,


            allows_boarding=True,


            allows_alighting=False,


        )


        self.ts_dest = TripStop.objects.create(


            trip=self.trip,


            stop=self.stop_juj,


            sequence=2,


            scheduled_at=self.arrival,


            allows_boarding=False,


            allows_alighting=True,


        )





    def _create_confirmed_booking_with_tickets(self, customer_email="cliente@ejemplo.com"):


        """Crea una reserva confirmada con pasaje emitido y archivo fÃƒÂ­sico en storage."""


        now = timezone.now()


        booking = Booking.objects.create(


            channel=BookingChannel.ONLINE,


            status=BookingStatus.CONFIRMED,


            email=customer_email,


            phone="351 9876543",


            expires_at=now + timezone.timedelta(hours=24),


            confirmed_at=now,


        )


        leg = BookingLeg.objects.create(


            booking=booking,


            sequence=1,


            trip=self.trip,


            origin_stop=self.ts_orig,


            destination_stop=self.ts_dest,


            origin_stop_name=self.stop_cba.name,


            destination_stop_name=self.stop_juj.name,


            departure_at=self.departure,


            arrival_at=self.arrival,


        )


        passenger = BookingPassenger.objects.create(


            booking=booking,


            position=1,


            first_name="Juan",


            last_name="PÃƒÂ©rez",


            document_type="DNI",


            document_number="30123456",


        )


        assignment = SeatAssignment.objects.create(


            leg=leg,


            passenger=passenger,


            trip=self.trip,


            seat=self.seat1,


            status=AssignmentStatus.CONFIRMED,


            seat_number=1,


            category=SeatCategory.SEMI_CAMA,


            price=Decimal("15000.00"),


            currency="ARS",


        )





        storage = get_ticket_storage()


        pdf_filename = f"tickets/test_{booking.public_id}_1.pdf"


        from django.core.files.base import ContentFile
        storage.save(pdf_filename, ContentFile(b"%PDF-1.4 test ticket content"))





        ticket = Ticket.objects.create(


            booking=booking,


            leg=leg,


            passenger=passenger,


            seat_assignment=assignment,


            status=TicketStatus.ISSUED,


            ticket_code=f"TK-{booking.public_id.hex[:6].upper()}-L1-P1",


            verification_token_hash=hashlib.sha256(b"verif-token").hexdigest(),


            download_token_hash=hashlib.sha256(b"down-token").hexdigest(),


            pdf_path=pdf_filename,


            passenger_name="Juan PÃƒÂ©rez",


            passenger_document_masked="30***456",


            origin_stop_name=self.stop_cba.name,


            destination_stop_name=self.stop_juj.name,


            departure_at=self.departure,


            arrival_at=self.arrival,


            seat_number=1,


            seat_category=SeatCategory.SEMI_CAMA,


            price=Decimal("15000.00"),


            currency="ARS",


            booking_public_id=booking.public_id,


        )


        return booking, ticket








class CustomerAuthenticationAndRegistrationTests(BaseCustomerTestCase):


    """Pruebas de registro, login con correo normalizado y separaciÃƒÂ³n del panel."""





    def test_registration_normalizes_email_and_authenticates_case_insensitively(self):


        """El registro guarda el correo normalizado en minÃƒÂºsculas y permite el login sin distinciÃƒÂ³n de mayÃƒÂºsculas."""


        raw_email = "  Pasajero.Ejemplo@CORREO.Com  "


        expected_normalized = "pasajero.ejemplo@correo.com"





        response = self.client.post(reverse("registro_cliente"), {


            "email": raw_email,


            "password": "Password123!Segura",


            "password_confirm": "Password123!Segura",


            "first_name": "Carlos",


            "last_name": "GÃƒÂ³mez",


            "commercial_consent": "on",


        }, follow=True)





        self.assertEqual(response.status_code, 200)


        self.assertRedirects(response, reverse("mis_viajes"))





        # Verificar normalizaciÃƒÂ³n en base de datos


        customer = Customer.objects.get(normalized_email=expected_normalized)


        self.assertEqual(customer.email, expected_normalized)


        self.assertEqual(customer.user.email, expected_normalized)


        self.assertFalse(customer.user.is_staff)


        self.assertFalse(customer.user.is_superuser)





        # Cerrar sesiÃƒÂ³n


        self.client.post(reverse("logout_cliente"))





        # Iniciar sesiÃƒÂ³n con mayÃƒÂºsculas distintas


        login_resp = self.client.post(reverse("login_cliente"), {


            "email": "PASAJERO.EJEMPLO@correo.COM",


            "password": "Password123!Segura",


        }, follow=True)





        self.assertEqual(login_resp.status_code, 200)


        self.assertRedirects(login_resp, reverse("mis_viajes"))


        self.assertEqual(int(self.client.session["_auth_user_id"]), customer.user.id)





    def test_registration_rejects_duplicate_email_case_insensitively(self):


        """No permite registrar una cuenta si ya existe el correo (case-insensitive)."""


        register_customer(


            email="ana@ejemplo.com",


            password="StrongPassword123!",


        )





        response = self.client.post(reverse("registro_cliente"), {


            "email": "ANA@EJEMPLO.COM",


            "password": "AnotherPassword456!",


            "password_confirm": "AnotherPassword456!",


        })





        self.assertEqual(response.status_code, 200)


        self.assertContains(response, "Ya existe una cuenta registrada")





    def test_customer_cannot_access_panel(self):


        """Un usuario cliente sin privilegios recibe HTTP 403 Forbidden si intenta acceder al panel interno."""


        customer = register_customer(


            email="cliente.comun@ejemplo.com",


            password="StrongPassword123!",


        )


        self.client.force_login(customer.user)





        # Intento de acceso a la raÃƒÂ­z del panel


        panel_resp = self.client.get(reverse("panel:dashboard"))


        self.assertEqual(panel_resp.status_code, 403)





        # Intento de acceso a operaciones en panel


        buses_resp = self.client.get(reverse("panel:buses"))


        self.assertEqual(buses_resp.status_code, 403)





    def test_anonymous_access_to_mis_viajes_redirects_to_customer_login(self):


        """Un usuario anÃƒÂ³nimo que intenta ver sus viajes es redirigido a login_cliente y no a panel."""


        response = self.client.get(reverse("mis_viajes"))


        self.assertEqual(response.status_code, 302)


        self.assertTrue(response.url.startswith(reverse("login_cliente")))








class CustomerConsentAuditingTests(BaseCustomerTestCase):


    """Pruebas de consentimiento comercial explÃƒÂ­cito, separado y auditable."""





    def test_optional_commercial_consent_audited_on_registration(self):


        """El consentimiento es opcional y registra metadatos de auditorÃƒÂ­a completos."""


        # 1. Registro con consentimiento otorgado


        c1 = register_customer(


            email="consintio@ejemplo.com",


            password="StrongPassword123!",


            commercial_consent=True,


            ip_address="192.168.1.50",


            user_agent="Mozilla/5.0 TestBrowser",


        )


        consent1 = CustomerConsent.objects.filter(customer=c1).first()


        self.assertIsNotNone(consent1)


        self.assertTrue(consent1.granted)


        self.assertEqual(consent1.ip_address, "192.168.1.50")


        self.assertEqual(consent1.user_agent, "Mozilla/5.0 TestBrowser")


        self.assertEqual(consent1.consent_type, CustomerConsent.ConsentType.COMMERCIAL_COMMUNICATIONS)





        # 2. Registro sin tildar consentimiento (sigue siendo exitoso pero granted=False)


        c2 = register_customer(


            email="no_consintio@ejemplo.com",


            password="StrongPassword123!",


            commercial_consent=False,


            ip_address="10.0.0.1",


        )


        consent2 = CustomerConsent.objects.filter(customer=c2).first()


        self.assertIsNone(consent2)





    def test_customer_can_update_commercial_consent_auditably(self):


        """El cliente puede revocar u otorgar consentimiento y cada cambio queda auditado."""


        customer = register_customer(


            email="cambia_consentimiento@ejemplo.com",


            password="StrongPassword123!",


            commercial_consent=False,


        )


        self.client.force_login(customer.user)





        # Otorgar consentimiento


        resp = self.client.post(reverse("customer_consent_update"), {


            "commercial_consent": "on",


        }, follow=True)


        self.assertEqual(resp.status_code, 200)





        # Verificar nuevo registro auditable


        consents = list(CustomerConsent.objects.filter(customer=customer).order_by("recorded_at"))


        self.assertEqual(len(consents), 1)


        self.assertTrue(consents[0].granted)










class PasswordResetLinkTests(BaseCustomerTestCase):


    """Pruebas de restablecimiento de contraseÃƒÂ±a mediante enlace seguro por correo."""





    def test_password_reset_flow_with_link(self):


        """EnvÃƒÂ­a un enlace ÃƒÂºnico por correo, valida token seguro y actualiza contraseÃƒÂ±a."""


        customer = register_customer(


            email="recuperar@ejemplo.com",


            password="OldPassword123!",


        )





        # 1. Solicitar restablecimiento


        resp = self.client.post(reverse("password_reset"), {


            "email": "  RECUPERAR@EJEMPLO.COM  ",


        }, follow=True)


        self.assertEqual(resp.status_code, 200)


        self.assertRedirects(resp, reverse("password_reset_done"))





        # 2. Verificar que se enviÃƒÂ³ exactamente un correo


        self.assertEqual(len(mail.outbox), 1)


        email_sent = mail.outbox[0]


        self.assertIn("recuperar@ejemplo.com", email_sent.to)


        self.assertIn("Restablecer tu", email_sent.subject)





        # 3. Simular link token


        token = default_token_generator.make_token(customer.user)


        uidb64 = urlsafe_base64_encode(force_bytes(customer.user.pk))


        confirm_url = reverse("password_reset_confirm", kwargs={"uidb64": uidb64, "token": token})





        # 4. Acceder al formulario de confirmaciÃƒÂ³n con token vÃƒÂ¡lido


        get_confirm = self.client.get(confirm_url)


        self.assertEqual(get_confirm.status_code, 200)


        self.assertContains(get_confirm, "Nueva")





        # 5. Establecer nueva contraseÃƒÂ±a


        post_confirm = self.client.post(confirm_url, {


            "new_password": "NewSecretPassword123!",


            "new_password_confirm": "NewSecretPassword123!",


        }, follow=True)


        self.assertEqual(post_confirm.status_code, 200)


        self.assertRedirects(post_confirm, reverse("password_reset_complete"))





        # 6. Verificar que la nueva contraseÃƒÂ±a funciona y la vieja ya no


        self.assertTrue(self.client.login(username="recuperar@ejemplo.com", password="NewSecretPassword123!"))





    def test_password_reset_rejects_invalid_token(self):


        """Rechaza tokens invÃƒÂ¡lidos o manipulados."""


        customer = register_customer(


            email="token_invalido@ejemplo.com",


            password="OldPassword123!",


        )


        uidb64 = urlsafe_base64_encode(force_bytes(customer.user.pk))


        confirm_url = reverse("password_reset_confirm", kwargs={"uidb64": uidb64, "token": "invalid-token-123"})





        resp = self.client.get(confirm_url)


        self.assertEqual(resp.status_code, 200)


        self.assertContains(resp, "Enlace no")








class GuestCheckoutAndClaimTokenTests(BaseCustomerTestCase):


    """Pruebas de checkout como invitado, generaciÃƒÂ³n de claim tokens y asociaciÃƒÂ³n segura."""





    def test_guest_checkout_creates_claim_token_and_keeps_working(self):


        """La compra como invitado ('checkout invitado') continÃƒÂºa funcionando y genera un claim token seguro."""


        now = timezone.now()


        booking = Booking.objects.create(


            channel=BookingChannel.ONLINE,


            status=BookingStatus.HELD,


            email="invitado@ejemplo.com",


            phone="351 1234567",


            expires_at=now + timezone.timedelta(minutes=15),


        )





        # Generar token de reclamo


        raw_token = create_booking_claim_token(booking)


        self.assertTrue(len(raw_token) >= 32)





        # En la base solo se almacena el hash SHA-256


        claim_db = BookingClaimToken.objects.get(booking=booking)


        expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


        self.assertEqual(claim_db.token_hash, expected_hash)


        self.assertFalse(claim_db.is_claimed)





    def test_customer_can_claim_guest_booking_with_token(self):


        """Un cliente registrado puede asociar de forma segura una reserva invitada mediante su claim token."""


        customer = register_customer(


            email="comprador@ejemplo.com",


            password="StrongPassword123!",


        )


        now = timezone.now()


        booking = Booking.objects.create(


            channel=BookingChannel.ONLINE,


            status=BookingStatus.HELD,


            email="invitado_antiguo@ejemplo.com",


            phone="351 1234567",


            expires_at=now + timezone.timedelta(minutes=15),


        )


        raw_token = create_booking_claim_token(booking)





        # Iniciar sesiÃƒÂ³n y asociar reserva


        self.client.force_login(customer.user)


        response = self.client.post(reverse("reclamar_reserva"), {


            "claim_token": raw_token,


        }, follow=True)





        self.assertEqual(response.status_code, 200)


        self.assertContains(response, "Tu viaje fue asociado exitosamente a tu cuenta")





        # Verificar asociaciÃƒÂ³n en base de datos


        cb = CustomerBooking.objects.get(booking=booking)


        self.assertEqual(cb.customer, customer)





        # El token queda marcado como reclamado


        claim_db = BookingClaimToken.objects.get(booking=booking)


        self.assertTrue(claim_db.is_claimed)


        self.assertEqual(claim_db.claimed_by, customer)





        # Intento de volver a reclamar el mismo token es rechazado


        dup_resp = self.client.post(reverse("reclamar_reserva"), {


            "claim_token": raw_token,


        }, follow=True)


        self.assertEqual(dup_resp.status_code, 200)





    def test_cannot_claim_booking_already_associated_to_another_customer(self):


        """Garantiza que una reserva no pueda ser asociada a dos clientes distintos."""


        c1 = register_customer(email="dueno1@ejemplo.com", password="Password123!")


        c2 = register_customer(email="dueno2@ejemplo.com", password="Password123!")





        now = timezone.now()


        booking = Booking.objects.create(


            channel=BookingChannel.ONLINE,


            status=BookingStatus.HELD,


            email="reserva@ejemplo.com",


            expires_at=now + timezone.timedelta(minutes=15),


        )


        raw_token = create_booking_claim_token(booking)





        # Asociar con el primer cliente


        associate_booking_with_customer(c1, booking)





        # Segundo cliente intenta reclamar


        with self.assertRaises(Exception):


            claim_booking_with_token(c2, raw_token)








    def test_claim_token_expires_and_cannot_be_reused(self):


        customer = register_customer(email="vence@ejemplo.com", password="StrongPassword123!")


        booking = Booking.objects.create(channel=BookingChannel.ONLINE, status=BookingStatus.HELD, email="guest@ejemplo.com", expires_at=timezone.now() + timezone.timedelta(minutes=15))


        raw_token = create_booking_claim_token(booking)


        BookingClaimToken.objects.filter(booking=booking).update(expires_at=timezone.now() - timezone.timedelta(seconds=1))


        with self.assertRaises(Exception):


            claim_booking_with_token(customer, raw_token)








class CustomerTripsAndTicketsTests(BaseCustomerTestCase):


    """Pruebas de visualizaciÃƒÂ³n de 'Mis viajes' y descarga segura de pasajes."""





    def test_customer_can_view_trips_and_download_issued_ticket(self):


        """El cliente puede ver sus viajes confirmados y descargar sus pasajes PDF."""


        customer = register_customer(


            email="titular@ejemplo.com",


            password="StrongPassword123!",


        )


        booking, ticket = self._create_confirmed_booking_with_tickets(customer_email=customer.email)


        associate_booking_with_customer(customer, booking)





        self.client.force_login(customer.user)





        # 1. Ver 'Mis viajes'


        resp = self.client.get(reverse("mis_viajes"))


        self.assertEqual(resp.status_code, 200)


        self.assertContains(resp, str(booking.public_id))


        self.assertContains(resp, ticket.ticket_code)





        # 2. Descargar pasaje PDF


        download_url = reverse("customer_ticket_download", kwargs={"public_id": ticket.public_id})


        pdf_resp = self.client.get(download_url)


        self.assertEqual(pdf_resp.status_code, 200)


        self.assertEqual(pdf_resp["Content-Type"], "application/pdf")


        self.assertIn(f"attachment; filename=\"pasaje-{ticket.ticket_code}.pdf\"", pdf_resp["Content-Disposition"])





    def test_customer_cannot_download_other_customers_ticket(self):


        """Un cliente no puede descargar el pasaje de una reserva que no le pertenece."""


        c1 = register_customer(email="cliente1@ejemplo.com", password="Password123!")


        c2 = register_customer(email="cliente2@ejemplo.com", password="Password123!")





        booking, ticket = self._create_confirmed_booking_with_tickets(customer_email=c1.email)


        associate_booking_with_customer(c1, booking)





        # c2 intenta descargar el pasaje de c1


        self.client.force_login(c2.user)


        download_url = reverse("customer_ticket_download", kwargs={"public_id": ticket.public_id})


        forbidden_resp = self.client.get(download_url)


        self.assertEqual(forbidden_resp.status_code, 403)








class GoogleOAuthConfigurationAndSimulationTests(BaseCustomerTestCase):


    """Pruebas de inicio de sesiÃƒÂ³n con Google: solo configurable por env y callback simulado seguro."""





    def test_google_login_not_configured_gives_friendly_message(self):


        """Si Google OAuth no estÃƒÂ¡ configurado en env y simulaciÃƒÂ³n desactivada, no rompe y avisa amistosamente."""


        with self.settings(GOOGLE_OAUTH_ENABLED=False, GOOGLE_OAUTH_SIMULATION_ENABLED=False, DEBUG=False):


            resp = self.client.get(reverse("google_login"), follow=True)


            self.assertEqual(resp.status_code, 200)


            self.assertContains(resp, "El inicio")





    def test_google_oauth_callback_simulated_securely(self):


        """En entorno con simulaciÃƒÂ³n habilitada, procesa el callback simulado seguro verificando state CSRF."""


        with self.settings(GOOGLE_OAUTH_SIMULATION_ENABLED=True):


            # 1. Iniciar login de Google para generar state en sesiÃƒÂ³n


            init_resp = self.client.get(reverse("google_login"))


            self.assertEqual(init_resp.status_code, 302)





            state = self.client.session.get("google_oauth_state")


            self.assertTrue(state)





            # 2. Callback con state incorrecto es rechazado


            bad_state_resp = self.client.get(reverse("google_callback"), {


                "state": "bad-state-token",


                "code": "simulated:carlos@ejemplo.com:google-sub-456",


            })


            self.assertEqual(bad_state_resp.status_code, 400)





            # 3. Callback con state correcto autentica y crea perfil Customer


            good_resp = self.client.get(reverse("google_callback"), {


                "state": state,


                "code": "simulated:carlos@ejemplo.com:google-sub-456",


            }, follow=True)





            self.assertEqual(good_resp.status_code, 200)


            self.assertRedirects(good_resp, reverse("mis_viajes"))





            # Verificar creaciÃƒÂ³n del usuario y perfil Customer


            customer = Customer.objects.get(normalized_email="carlos@ejemplo.com")


            self.assertEqual(customer.google_sub, "google-sub-456")


            self.assertFalse(customer.user.is_staff)







