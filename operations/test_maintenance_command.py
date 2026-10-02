import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

from tickets.models import FulfillmentEmailStatus, FulfillmentIssueStatus
from .management.commands.run_operational_maintenance import Command


class OperationalMaintenanceCommandTests(SimpleTestCase):
    @patch.object(Command, "_expired", return_value=False)
    @patch.object(Command, "_process", return_value=None)
    @patch.object(Command, "_candidates", return_value=[1, 2])
    def test_limit_is_not_reported_as_timeout(self, candidates, process, expired):
        output = StringIO()
        call_command("run_operational_maintenance", "--task", "expire", "--limit", "1", stdout=output)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["processed"], 1)
        self.assertTrue(payload["limit_reached"])
        self.assertFalse(payload["timed_out"])

    @patch.object(Command, "_expired", return_value=False)
    @patch.object(
        Command,
        "_process",
        return_value=SimpleNamespace(
            issue_status=FulfillmentIssueStatus.FAILED,
            email_status=FulfillmentEmailStatus.PENDING,
        ),
    )
    @patch.object(Command, "_candidates", return_value=[1])
    def test_failed_fulfillment_returns_nonzero(self, candidates, process, expired):
        with self.assertRaises(CommandError):
            call_command("run_operational_maintenance", "--task", "fulfillment", "--limit", "1")

