import json
from io import StringIO
from unittest import SkipTest
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test import TransactionTestCase

from .management.commands.run_maintenance_worker import Command, _lock_key, advisory_session_lock


class MaintenanceWorkerTests(SimpleTestCase):
    @override_settings(MAINTENANCE_WORKER_INTERVAL_SECONDS=0.001, MAINTENANCE_WORKER_JITTER_SECONDS=0)
    @patch.object(Command, "_run_cycle", return_value={"processed": 1, "succeeded": 1})
    def test_once_executes_one_cycle_and_emits_sanitized_metrics(self, cycle):
        output = StringIO()
        call_command("run_maintenance_worker", "--once", stdout=output)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["cycles"], 1)
        self.assertEqual(payload["metrics"]["succeeded"], 1)
        self.assertNotIn("error", payload)

    @override_settings(MAINTENANCE_WORKER_INTERVAL_SECONDS=0)
    def test_invalid_interval_is_fatal_before_processing(self):
        with self.assertRaises(CommandError):
            call_command("run_maintenance_worker", "--once")

    @override_settings(MAINTENANCE_WORKER_INTERVAL_SECONDS=0.001, MAINTENANCE_WORKER_JITTER_SECONDS=0)
    @patch.object(Command, "_run_cycle", return_value={"processed": 1, "succeeded": 1})
    def test_max_cycles_limits_resident_worker(self, cycle):
        output = StringIO()
        call_command("run_maintenance_worker", "--max-cycles", "3", stdout=output)
        self.assertEqual(json.loads(output.getvalue())["cycles"], 3)
        self.assertEqual(cycle.call_count, 3)

    @override_settings(MAINTENANCE_WORKER_MODE="resident")
    def test_cron_mode_requires_once(self):
        with self.assertRaises(CommandError):
            with override_settings(MAINTENANCE_WORKER_MODE="cron"):
                call_command("run_maintenance_worker")

    @override_settings(MAINTENANCE_WORKER_ENABLED=False)
    def test_disabled_worker_does_not_process(self):
        output = StringIO()
        call_command("run_maintenance_worker", "--once", stdout=output)
        self.assertFalse(json.loads(output.getvalue())["enabled"])

    def test_sqlite_lock_is_local_noop(self):
        with advisory_session_lock() as acquired:
            self.assertTrue(acquired)


class PostgreSQLMaintenanceWorkerLockTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise SkipTest("requiere PostgreSQL")

    def test_advisory_lock_contends_and_releases_between_sessions(self):
        import psycopg2

        secondary = psycopg2.connect(**connection.get_connection_params())
        try:
            with secondary.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s)", [_lock_key()])
            with advisory_session_lock(0) as acquired:
                self.assertFalse(acquired)
            with secondary.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_lock_key()])
            with advisory_session_lock(0) as acquired:
                self.assertTrue(acquired)
        finally:
            secondary.close()
