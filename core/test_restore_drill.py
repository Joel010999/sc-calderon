from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from core.restore_drill import (
    CONFIRMATION,
    private_files_drill,
    run_restore_drill,
    validate_database_url,
)


class RestoreDrillSafetyTests(SimpleTestCase):
    def test_rejects_remote_or_ambiguous_database(self):
        for value in (
            "postgresql://u:p@example.com/scviajes_restore_drill_source",
            "postgresql://u:p@127.0.0.1/production",
            "sqlite:///scviajes_restore_drill_source",
        ):
            with self.assertRaises(ValueError):
                validate_database_url(value, label="test")

    def test_accepts_only_explicit_temporary_database(self):
        value = "postgresql://u:p@127.0.0.1/scviajes_restore_drill_source"
        self.assertEqual(validate_database_url(value, label="test"), value)

    def test_private_files_are_copied_and_checksummed(self):
        with TemporaryDirectory() as directory:
            result = private_files_drill(Path(directory))
        self.assertEqual(result, {"files": 2, "checksums_match": True})

    def test_confirmation_constant_is_not_empty(self):
        self.assertTrue(CONFIRMATION.startswith("CONFIRMAR_"))

    def test_rejects_invalid_confirmation(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run_restore_drill(
                    "postgresql://u:p@127.0.0.1/scviajes_restore_drill_source",
                    "postgresql://u:p@127.0.0.1/scviajes_restore_drill_destination",
                    Path(directory) / "scviajes_restore_drill_test.dump",
                    confirmation="NO",
                )

    def test_rejects_same_database_through_loopback_aliases(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run_restore_drill(
                    "postgresql://u:p@127.0.0.1/scviajes_restore_drill_same",
                    "postgresql://u:p@localhost/scviajes_restore_drill_same",
                    Path(directory) / "scviajes_restore_drill_test.dump",
                    confirmation=CONFIRMATION,
                )

    def test_rejects_existing_backup(self):
        with TemporaryDirectory() as directory:
            backup = Path(directory) / "scviajes_restore_drill_existing.dump"
            backup.write_bytes(b"synthetic")
            with self.assertRaises(ValueError):
                run_restore_drill(
                    "postgresql://u:p@127.0.0.1/scviajes_restore_drill_source",
                    "postgresql://u:p@127.0.0.1/scviajes_restore_drill_destination",
                    backup,
                    confirmation=CONFIRMATION,
                )

    @patch("core.restore_drill._psql", return_value="1")
    def test_rejects_non_empty_destination(self, _psql):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run_restore_drill(
                    "postgresql://u:p@127.0.0.1/scviajes_restore_drill_source",
                    "postgresql://u:p@127.0.0.1/scviajes_restore_drill_destination",
                    Path(directory) / "scviajes_restore_drill_test.dump",
                    confirmation=CONFIRMATION,
                )
