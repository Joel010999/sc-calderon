import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import OperationalError
from django.test import RequestFactory, TestCase, SimpleTestCase, override_settings

from core.errors import error_400, error_403, error_404, error_500


class HealthEndpointTests(TestCase):
    def test_liveness_does_not_query_database_and_returns_request_id(self):
        with patch("core.health.connection.cursor", side_effect=AssertionError("liveness queried database")):
            response = self.client.get("/health/live/", HTTP_X_REQUEST_ID="safe-request-01")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "request_id": "safe-request-01"})
        self.assertEqual(response["X-Request-ID"], "safe-request-01")

    def test_invalid_request_id_is_replaced(self):
        response = self.client.get("/health/live/", HTTP_X_REQUEST_ID="token?secret=bad")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("?", response["X-Request-ID"])
        self.assertEqual(response.json()["request_id"], response["X-Request-ID"])

    def test_request_logging_contains_id_without_query_secret(self):
        with self.assertLogs("django.request.safe", level="INFO") as captured:
            self.client.get("/health/live/?token=must-not-be-logged", HTTP_X_REQUEST_ID="log-check")
        self.assertEqual(captured.records[0].request_id, "log-check")
        self.assertTrue(all("must-not-be-logged" not in line for line in captured.output))

    def test_readiness_returns_503_without_leaking_database_error(self):
        with patch("core.health.connection.cursor", side_effect=OperationalError("password=not-to-leak")):
            response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"password", response.content)
        self.assertEqual(response.json()["status"], "not_ready")

    def test_readiness_uses_database_in_development(self):
        with self.settings(DEBUG=True):
            response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")


class ProductionPreflightTests(SimpleTestCase):
    def test_preflight_reports_pass_warning_and_fail_without_secrets(self):
        from core.preflight import collect_preflight

        results = {item.key: item for item in collect_preflight()}
        self.assertIn(results["debug"].status, {"PASS", "FAIL"})
        self.assertIn(results["hsts"].status, {"PASS", "WARNING", "FAIL"})
        self.assertIn(results["secret_key"].status, {"PASS", "FAIL"})
        self.assertNotIn("not-to-leak", " ".join(item.message for item in results.values()))

    def test_json_output_is_valid_and_fail_exit_code_is_nonzero(self):
        output = StringIO()
        with patch("core.management.commands.production_preflight.preflight_payload", return_value={"ok": True, "results": []}):
            call_command("production_preflight", "--json", stdout=output)
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "results": []})

        with patch("core.management.commands.production_preflight.preflight_payload", return_value={"ok": False, "results": []}):
            with self.assertRaises(SystemExit) as raised:
                call_command("production_preflight", stdout=StringIO())
        self.assertEqual(raised.exception.code, 1)

    @override_settings(DEBUG=False, DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3"}})
    def test_production_configuration_does_not_pass_with_sqlite(self):
        from core.preflight import collect_preflight

        with patch("core.preflight._production", return_value=True):
            results = {item.key: item for item in collect_preflight()}
        self.assertEqual(results["database_engine"].status, "FAIL")


class ErrorPageTests(SimpleTestCase):
    def test_custom_error_pages_are_safe_and_include_request_id(self):
        request = RequestFactory().get("/missing", HTTP_X_REQUEST_ID="error-check")
        request.request_id = "error-check"
        for handler, status in ((error_400, 400), (error_403, 403), (error_404, 404)):
            with self.subTest(status=status):
                response = handler(request)
                self.assertEqual(response.status_code, status)
                self.assertContains(response, "error-check", status_code=status)
                self.assertNotContains(response, "Traceback", status_code=status)
        response = error_500(request)
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "error-check", status_code=500)
