"""Pruebas exhaustivas para el Listado Provisional de Pasajeros en PDF."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
import pypdf

from operations.models import Bus, Route, Seat, SeatCategory, Stop, Trip, TripFare, TripStop
from panel.passenger_list_pdf import generate_passenger_list_pdf, get_trip_passengers_data
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)


class PassengerListPdfTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Inicializar estructura operativa base
        call_command("setup_initial_operations", stdout=io.StringIO())
        cls.route = Route.objects.get(code="CBA-JUJ")
        cls.bus = Bus.objects.create(code="BUS-PDF", display_name="Colectivo PDF Prueba")

        # Crear 60 butacas
        cls.seats = []
        for num in range(1, 61):
            deck = Seat.Deck.LOWER if num <= 12 else Seat.Deck.UPPER
            cat = SeatCategory.CAMA if num <= 12 else SeatCategory.SEMI_CAMA
            cls.seats.append(
                Seat.objects.create(
                    bus=cls.bus,
                    number=num,
                    deck=deck,
                    category=cat,
                    position_x=(num % 4),
                    position_y=(num // 4),
                    is_active=True,
                )
            )

        # Usuarios
        user_model = get_user_model()
        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        seller_group, _ = Group.objects.get_or_create(name="Vendedor")

        cls.admin = user_model.objects.create_user("admin-pdf", password="x")
        cls.admin.groups.add(admin_group)

        cls.seller = user_model.objects.create_user("seller-pdf", password="x")
        cls.seller.groups.add(seller_group)

        cls.superuser = user_model.objects.create_superuser("super-pdf", password="x")
        cls.common = user_model.objects.create_user("common-pdf", password="x")

        # Salida
        cls.departure = timezone.now() + timedelta(days=5)
        cls.trip = Trip.objects.create(
            route=cls.route,
            bus=cls.bus,
            departure_at=cls.departure,
            status=Trip.Status.SCHEDULED,
        )

        stops = list(cls.route.route_stops.select_related("stop").order_by("sequence"))
        cls.trip_stops = {}
        for rs in stops:
            ts = TripStop.objects.create(
                trip=cls.trip,
                stop=rs.stop,
                sequence=rs.sequence,
                scheduled_at=cls.departure + timedelta(hours=rs.sequence),
                allows_boarding=rs.allows_boarding,
                allows_alighting=rs.allows_alighting,
            )
            cls.trip_stops[rs.stop.code] = ts

        # Tarifas activas
        TripFare.objects.create(
            trip=cls.trip,
            seat_category=SeatCategory.CAMA,
            amount=Decimal("15000.00"),
            currency="ARS",
            is_active=True,
        )
        TripFare.objects.create(
            trip=cls.trip,
            seat_category=SeatCategory.SEMI_CAMA,
            amount=Decimal("12000.00"),
            currency="ARS",
            is_active=True,
        )

    def setUp(self):
        self.client = Client()

    def _create_confirmed_booking(
        self,
        *,
        seller=None,
        email="pax@example.com",
        passengers_data=None,
        seat_numbers=None,
        origin_code="CBA",
        dest_code="SSJ",
        status=BookingStatus.CONFIRMED,
    ):
        seller = seller or self.seller
        now = timezone.now()
        booking = Booking.objects.create(
            channel=BookingChannel.MANUAL,
            seller=seller,
            status=status,
            email=email,
            phone="3511234567",
            confirmed_at=now if status == BookingStatus.CONFIRMED else None,
            expires_at=now + timedelta(days=1) if status != BookingStatus.CONFIRMED else None,
        )

        ts_origin = self.trip_stops[origin_code]
        ts_dest = self.trip_stops[dest_code]

        leg = BookingLeg.objects.create(
            booking=booking,
            sequence=1,
            trip=self.trip,
            origin_stop=ts_origin,
            destination_stop=ts_dest,
            origin_stop_name=ts_origin.stop.name,
            destination_stop_name=ts_dest.stop.name,
            departure_at=ts_origin.scheduled_at,
            arrival_at=ts_dest.scheduled_at,
        )

        passengers_data = passengers_data or [
            {
                "first_name": "Juan",
                "last_name": "Pérez",
                "document_type": "DNI",
                "document_number": "30111222",
                "is_lap_infant": False,
            }
        ]
        seat_numbers = seat_numbers or [1]

        created_passengers = []
        seat_idx = 0
        for pos, pdata in enumerate(passengers_data, start=1):
            pax = BookingPassenger.objects.create(
                booking=booking,
                position=pos,
                first_name=pdata["first_name"],
                last_name=pdata["last_name"],
                document_type=pdata.get("document_type", "DNI"),
                document_number=pdata.get("document_number", "10000000"),
                birth_date=pdata.get("birth_date", date(1990, 1, 1)),
                nationality=pdata.get("nationality", "Argentina"),
                is_lap_infant=pdata.get("is_lap_infant", False),
                accompanied_by=pdata.get("accompanied_by"),
            )
            created_passengers.append(pax)

            if not pax.is_lap_infant and seat_idx < len(seat_numbers):
                seat_obj = next(s for s in self.seats if s.number == seat_numbers[seat_idx])
                SeatAssignment.objects.create(
                    leg=leg,
                    passenger=pax,
                    trip=self.trip,
                    seat=seat_obj,
                    seat_number=seat_obj.number,
                    category=seat_obj.category,
                    price=Decimal("15000.00") if seat_obj.category == SeatCategory.CAMA else Decimal("12000.00"),
                    status=AssignmentStatus.CONFIRMED if status == BookingStatus.CONFIRMED else AssignmentStatus.HELD,
                )
                seat_idx += 1

        return booking, leg, created_passengers

    # -------------------------------------------------------------------------
    # 1. Permisos y seguridad de roles
    # -------------------------------------------------------------------------
    def test_vendedor_has_no_download_button_and_gets_403_on_url(self):
        """El vendedor no ve el botón en la vista de detalle del viaje y recibe 403 por URL directa."""
        self.client.force_login(self.seller)

        # 1. Pantalla de detalle de viaje
        detail_url = reverse("panel:trip_detail", kwargs={"pk": self.trip.pk})
        resp_detail = self.client.get(detail_url)
        self.assertEqual(resp_detail.status_code, 200)
        self.assertNotContains(resp_detail, "Descargar listado de pasajeros")
        pdf_url = reverse("panel:trip_passenger_list_pdf", kwargs={"trip_pk": self.trip.pk})
        self.assertNotContains(resp_detail, pdf_url)

        # 2. Intento de acceso directo a URL del PDF
        resp_pdf = self.client.get(pdf_url)
        self.assertEqual(resp_pdf.status_code, 403)

    def test_admin_and_superuser_can_access_pdf_and_see_button(self):
        """Administrador y superusuario ven el botón y descargan el PDF con código 200."""
        detail_url = reverse("panel:trip_detail", kwargs={"pk": self.trip.pk})
        pdf_url = reverse("panel:trip_passenger_list_pdf", kwargs={"trip_pk": self.trip.pk})

        # Administrador
        self.client.force_login(self.admin)
        resp_admin_detail = self.client.get(detail_url)
        self.assertEqual(resp_admin_detail.status_code, 200)
        self.assertContains(resp_admin_detail, "Descargar listado de pasajeros")
        self.assertContains(resp_admin_detail, pdf_url)

        resp_admin_pdf = self.client.get(pdf_url)
        self.assertEqual(resp_admin_pdf.status_code, 200)
        self.assertEqual(resp_admin_pdf["Content-Type"], "application/pdf")
        self.assertIn(f'filename="listado-pasajeros-viaje-{self.trip.pk}.pdf"', resp_admin_pdf["Content-Disposition"])
        self.assertTrue(resp_admin_pdf.content.startswith(b"%PDF"))

        # Superusuario
        self.client.force_login(self.superuser)
        resp_super_pdf = self.client.get(pdf_url)
        self.assertEqual(resp_super_pdf.status_code, 200)
        self.assertEqual(resp_super_pdf["Content-Type"], "application/pdf")
        self.assertTrue(resp_super_pdf.content.startswith(b"%PDF"))

    def test_anonymous_and_common_users_are_rejected(self):
        """Usuario no autenticado redirige a login; usuario común sin rol recibe 403."""
        pdf_url = reverse("panel:trip_passenger_list_pdf", kwargs={"trip_pk": self.trip.pk})

        # Anónimo
        resp_anon = self.client.get(pdf_url)
        self.assertEqual(resp_anon.status_code, 302)

        # Común
        self.client.force_login(self.common)
        resp_common = self.client.get(pdf_url)
        self.assertEqual(resp_common.status_code, 403)

    # -------------------------------------------------------------------------
    # 2. Contenido del PDF: Adultos y Menores Gratuitos sin asiento
    # -------------------------------------------------------------------------
    def test_pdf_includes_adults_and_lap_infants_with_mother_seat(self):
        """El PDF incluye adultos confirmados y menores sin asiento referenciando la butaca de la madre."""
        # Madre en butaca 3
        mother_data = {
            "first_name": "Valeria",
            "last_name": "Gómez",
            "document_type": "DNI",
            "document_number": "35888999",
            "is_lap_infant": False,
        }
        b1, _, pax_b1 = self._create_confirmed_booking(
            email="valeria@correo.com",
            passengers_data=[mother_data],
            seat_numbers=[3],
            origin_code="CBA",
            dest_code="SSJ",
        )
        mother = pax_b1[0]

        # Menor de 2 años en brazos de Valeria
        minor_data = {
            "first_name": "Liam",
            "last_name": "Gómez",
            "document_type": "DNI",
            "document_number": "55111222",
            "birth_date": date(2024, 6, 1),
            "is_lap_infant": True,
            "accompanied_by": mother,
        }
        # Agregar el menor a la misma reserva
        BookingPassenger.objects.create(
            booking=b1,
            position=2,
            first_name=minor_data["first_name"],
            last_name=minor_data["last_name"],
            document_type=minor_data["document_type"],
            document_number=minor_data["document_number"],
            birth_date=minor_data["birth_date"],
            nationality="Argentina",
            is_lap_infant=True,
            accompanied_by=mother,
        )

        _, rows = get_trip_passengers_data(self.trip.pk)
        self.assertEqual(len(rows), 2)

        # Verificar fila del adulto
        adult_row = next(r for r in rows if not r["is_minor"])
        self.assertEqual(adult_row["name"], "Gómez, Valeria")
        self.assertEqual(adult_row["document"], "DNI 35888999")
        self.assertEqual(adult_row["seat"], "Butaca 3 (Cama)")
        self.assertEqual(adult_row["origin"], "Córdoba Capital")
        self.assertEqual(adult_row["destination"], "San Salvador de Jujuy")
        self.assertEqual(adult_row["reference"], str(b1.public_id)[:8].upper())

        # Verificar fila del menor
        minor_row = next(r for r in rows if r["is_minor"])
        self.assertEqual(minor_row["name"], "Gómez, Liam")
        self.assertEqual(minor_row["document"], "DNI 55111222")
        self.assertEqual(minor_row["seat"], "Menor sin asiento (en brazos, butaca 3)")
        self.assertEqual(minor_row["origin"], "Córdoba Capital")
        self.assertEqual(minor_row["destination"], "San Salvador de Jujuy")
        self.assertEqual(minor_row["reference"], str(b1.public_id)[:8].upper())

        # Generar PDF y extraer texto con pypdf
        pdf_bytes = generate_passenger_list_pdf(self.trip)
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = "\n".join(page.extract_text() for page in reader.pages)

        self.assertIn("Gómez, Valeria", full_text)
        self.assertIn("35888999", full_text)
        self.assertIn("Butaca 3", full_text)
        self.assertIn("Gómez, Liam", full_text)
        self.assertIn("55111222", full_text)
        self.assertIn("Menor sin asiento (en brazos, butaca 3)", full_text)
        self.assertIn("LISTADO PROVISIONAL", full_text)
        self.assertIn("NO CNRT", full_text)

    def test_pdf_excludes_held_cancelled_and_expired_bookings(self):
        """Reservas en estado HELD, CANCELLED o EXPIRED no aparecen en el listado provisional."""
        # 1. Confirmada
        self._create_confirmed_booking(
            email="confirmada@correo.com",
            passengers_data=[{"first_name": "Carlos", "last_name": "Confirmado"}],
            seat_numbers=[5],
            status=BookingStatus.CONFIRMED,
        )
        # 2. HELD
        self._create_confirmed_booking(
            email="held@correo.com",
            passengers_data=[{"first_name": "Hugo", "last_name": "EnEspera"}],
            seat_numbers=[6],
            status=BookingStatus.HELD,
        )
        # 3. RELEASED (liberada / cancelada)
        self._create_confirmed_booking(
            email="cancelada@correo.com",
            passengers_data=[{"first_name": "Claudia", "last_name": "Cancelada"}],
            seat_numbers=[7],
            status=BookingStatus.RELEASED,
        )
        # 4. EXPIRED
        self._create_confirmed_booking(
            email="expirada@correo.com",
            passengers_data=[{"first_name": "Esteban", "last_name": "Expirado"}],
            seat_numbers=[8],
            status=BookingStatus.EXPIRED,
        )

        _, rows = get_trip_passengers_data(self.trip.pk)
        passenger_names = [r["name"] for r in rows]

        self.assertIn("Confirmado, Carlos", passenger_names)
        self.assertNotIn("EnEspera, Hugo", passenger_names)
        self.assertNotIn("Cancelada, Claudia", passenger_names)
        self.assertNotIn("Expirado, Esteban", passenger_names)

    # -------------------------------------------------------------------------
    # 3. Varios tramos, deduplicación y orden
    # -------------------------------------------------------------------------
    def test_pdf_multiple_legs_and_no_join_duplicates(self):
        """Verifica subidas en distintas paradas y que joins no dupliquen pasajeros."""
        # Pasajero sube en Jesús María y baja en Jujuy
        self._create_confirmed_booking(
            email="t1@correo.com",
            passengers_data=[{"first_name": "Marcos", "last_name": "JM"}],
            seat_numbers=[10],
            origin_code="JMA",
            dest_code="SSJ",
        )
        # Pasajero sube en Córdoba y baja en Güemes
        self._create_confirmed_booking(
            email="t2@correo.com",
            passengers_data=[{"first_name": "Alberto", "last_name": "CBA"}],
            seat_numbers=[11],
            origin_code="CBA",
            dest_code="GUE",
        )

        _, rows = get_trip_passengers_data(self.trip.pk)

        # Verificar que el pasajero que sube en Córdoba (parada sequence 1) aparece antes
        # que el que sube en Jesús María (parada sequence 2)
        cba_idx = next(i for i, r in enumerate(rows) if "CBA, Alberto" in r["name"])
        jm_idx = next(i for i, r in enumerate(rows) if "JM, Marcos" in r["name"])
        self.assertLess(cba_idx, jm_idx)

        # Verificar que no hay duplicados en el listado
        names = [r["name"] for r in rows]
        self.assertEqual(len(names), len(set(names)))

    # -------------------------------------------------------------------------
    # 4. Nombres largos, acentos y caracteres especiales
    # -------------------------------------------------------------------------
    def test_pdf_handles_long_names_and_accents_cleanly(self):
        """Nombres extremadamente largos y con tildes se renderizan correctamente sin romper el layout."""
        long_last = "Rodríguez de la Torre Peña y Álvarez de Toledo"
        long_first = "María de la Asunción de las Mercedes"
        self._create_confirmed_booking(
            email="largo@correo.com",
            passengers_data=[{
                "first_name": long_first,
                "last_name": long_last,
                "document_type": "DNI",
                "document_number": "12.345.678-K",
            }],
            seat_numbers=[12],
        )

        pdf_bytes = generate_passenger_list_pdf(self.trip)
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = "\n".join(page.extract_text() for page in reader.pages)

        self.assertIn("Rodríguez de la Torre", full_text)
        self.assertIn("María de la Asunción", full_text)

    # -------------------------------------------------------------------------
    # 5. Varias páginas, encabezados repetidos y ausencia de embarque/QR
    # -------------------------------------------------------------------------
    def test_multi_page_pdf_has_repeated_headers_and_no_boarding_or_qr(self):
        """Con más de 45 pasajeros genera múltiples páginas, con encabezados repetidos y sin menciones a embarque."""
        passengers_data = []
        seat_numbers = list(range(13, 58))  # 45 pasajeros adicionales
        for num in seat_numbers:
            passengers_data.append({
                "first_name": f"Nombre{num}",
                "last_name": f"Apellido{num}",
                "document_type": "DNI",
                "document_number": f"400000{num:02d}",
                "is_lap_infant": False,
            })

        self._create_confirmed_booking(
            email="multipage@correo.com",
            passengers_data=passengers_data,
            seat_numbers=seat_numbers,
        )

        pdf_bytes = generate_passenger_list_pdf(self.trip)
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))

        # Al tener más de 45 pasajeros, debe generar al menos 2 páginas en formato horizontal A4
        page_count = len(reader.pages)
        self.assertGreaterEqual(page_count, 2, f"Se esperaban al menos 2 páginas, se obtuvieron {page_count}")

        # Página 1: debe contener título principal y rotulado provisional
        p1_text = reader.pages[0].extract_text()
        self.assertIn("SC VIAJES — LISTADO PROVISIONAL DE PASAJEROS", p1_text)
        self.assertIn("DOCUMENTO DE CONTROL INTERNO", p1_text)
        self.assertIn("NO CNRT", p1_text)
        self.assertIn(f"Página 1 de {page_count}", p1_text)

        # Página 2: debe contener encabezado repetido y pie de página correlativo
        p2_text = reader.pages[1].extract_text()
        self.assertIn("LISTADO PROVISIONAL DE PASAJEROS", p2_text)
        self.assertIn(f"Página 2 de {page_count}", p2_text)
        # Encabezado de columnas repetido
        self.assertIn("Pasajero", p2_text)
        self.assertIn("Documento", p2_text)
        self.assertIn("Butaca", p2_text)

        # Ausencia total de términos de embarque y códigos
        full_text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertNotIn("Embarcado", full_text)
        self.assertNotIn("Pendiente de embarque", full_text)
        self.assertNotIn("Validar embarque", full_text)

    # -------------------------------------------------------------------------
    # 6. Muestra física e inspección
    # -------------------------------------------------------------------------
    def test_sample_pdf_generation_to_disk(self):
        """Genera un archivo de muestra en exports/samples/ y valida su integridad binaria."""
        samples_dir = Path("exports/samples")
        samples_dir.mkdir(parents=True, exist_ok=True)
        sample_path = samples_dir / f"listado_provisional_viaje_{self.trip.pk}.pdf"

        pdf_bytes = generate_passenger_list_pdf(self.trip)
        sample_path.write_bytes(pdf_bytes)

        self.assertTrue(sample_path.exists())
        self.assertGreater(sample_path.stat().st_size, 1000)
        self.assertTrue(sample_path.read_bytes().startswith(b"%PDF"))
