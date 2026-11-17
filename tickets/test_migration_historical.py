"""Pruebas de migración de Tickets sobre registros históricos en base de datos temporal."""

import uuid
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from tickets.tests import TicketBaseMixin


class TicketCompanionMigrationHistoricalTests(TicketBaseMixin, TransactionTestCase):
    """Verifica que la migración 0008 sea 100% compatible con registros históricos sin rellenarlos."""

    def test_migration_compatible_with_historical_records(self):
        executor = MigrationExecutor(connection)
        target_0007 = [("tickets", "0007_ticketsequence")]
        target_0008 = [("tickets", "0008_ticket_companion_seat_reference")]

        # Crear una reserva confirmada con el esquema actual para tener los IDs de tramo y pasajero
        booking = self.create_confirmed_booking(passenger_count=1)
        leg = booking.legs.first()
        passenger = booking.passengers.first()

        try:
            # 1. Migrar hacia atrás a 0007 (sin la columna companion_seat_reference)
            executor.migrate(target_0007)

            # Verificar a nivel de base de datos que la columna NO existe en la tabla
            with connection.cursor() as cursor:
                columns = [c.name for c in connection.introspection.get_table_description(cursor, "tickets_ticket")]
            self.assertNotIn("companion_seat_reference", columns)

            # Insertar registro histórico mediante SQL puro (simulando datos existentes antes de 0008)
            now_iso = timezone.now().isoformat()
            hist_public_id = str(uuid.uuid4())
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tickets_ticket (
                        public_id, booking_id, leg_id, passenger_id, seat_assignment_id,
                        ticket_code, verification_token_hash, download_token_hash, status,
                        pdf_path, issued_at, passenger_name, passenger_document_masked,
                        origin_stop_name, destination_stop_name, departure_at, arrival_at,
                        seat_number, seat_category, seat_category_display, price, currency,
                        booking_public_id, void_reason, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, NULL,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    [
                        hist_public_id,
                        booking.pk,
                        leg.pk,
                        passenger.pk,
                        "TCK-HIST-001",
                        "hash_v_001",
                        "hash_d_001",
                        "ISSUED",
                        "tickets/hist.pdf",
                        now_iso,
                        "Pasajero Histórico",
                        "DNI ***0001",
                        leg.origin_stop_name,
                        leg.destination_stop_name,
                        leg.departure_at.isoformat(),
                        leg.arrival_at.isoformat(),
                        10,
                        "CAMA",
                        "Cama Ejecutivo",
                        Decimal("15000.00"),
                        "ARS",
                        str(booking.public_id),
                        "",
                        now_iso,
                        now_iso,
                    ],
                )

            # 2. Aplicar la migración 0008
            executor.loader.build_graph()
            executor.migrate(target_0008)

            # Verificar que la columna ahora existe
            with connection.cursor() as cursor:
                columns_0008 = [c.name for c in connection.introspection.get_table_description(cursor, "tickets_ticket")]
            self.assertIn("companion_seat_reference", columns_0008)

            # Importar modelo actual y cargar el registro histórico
            from tickets.models import Ticket

            reloaded_hist = Ticket.objects.get(ticket_code="TCK-HIST-001")
            self.assertEqual(reloaded_hist.companion_seat_reference, "")
            self.assertEqual(reloaded_hist.seat_number, 10)
            self.assertEqual(reloaded_hist.passenger_name, "Pasajero Histórico")

            # 3. Crear un nuevo pasaje para menor con companion_seat_reference poblado
            infant_passenger = booking.passengers.create(
                position=2,
                first_name="Bebé",
                last_name="Histórico",
                document_type="DNI",
                document_number="58111222",
                birth_date=timezone.now().date() - timezone.timedelta(days=200),
                nationality="Argentina",
                is_lap_infant=True,
                accompanied_by=passenger,
            )
            infant_ticket = Ticket.objects.create(
                booking=booking,
                leg=leg,
                passenger=infant_passenger,
                seat_assignment=None,
                ticket_code="TCK-INFANT-001",
                verification_token_hash="hash_v_002",
                download_token_hash="hash_d_002",
                status="ISSUED",
                passenger_name="Menor Histórico",
                passenger_document_masked="DNI ***0001",
                origin_stop_name=leg.origin_stop_name,
                destination_stop_name=leg.destination_stop_name,
                departure_at=leg.departure_at,
                arrival_at=leg.arrival_at,
                seat_number=None,
                seat_category="LAP_INFANT",
                seat_category_display="Sin butaca",
                companion_seat_reference="10",
                price=Decimal("0.00"),
                currency="ARS",
                booking_public_id=booking.public_id,
            )
            reloaded_infant = Ticket.objects.get(pk=infant_ticket.pk)
            self.assertEqual(reloaded_infant.companion_seat_reference, "10")
            self.assertIsNone(reloaded_infant.seat_number)

            # 4. Probar reversión de migración hacia 0007 (deshacer de forma segura)
            executor.loader.build_graph()
            executor.migrate(target_0007)
            with connection.cursor() as cursor:
                columns_rollback = [c.name for c in connection.introspection.get_table_description(cursor, "tickets_ticket")]
            self.assertNotIn("companion_seat_reference", columns_rollback)

        finally:
            # Restaurar a las migraciones finales del proyecto
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())
