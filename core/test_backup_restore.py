import json
import tempfile
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.core.management import call_command

from core.backup import backup_preflight, checksum, create_database_backup, verify_database_backup


class BackupReadinessTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = override_settings(
            BACKUP_OUTPUT_DIR=self.root / "backups",
            MEDIA_ROOT=self.root / "media",
            PROTECTED_MEDIA_ROOT=self.root / "protected",
            TICKETS_STORAGE_ROOT=self.root / "tickets",
            BACKUP_RETENTION_COUNT=3,
            BACKUP_MINIMUM_COPIES=3,
            BACKUP_OUTPUT_ROOT=self.root / "backups",
        )
        self.settings.enable()

    def tearDown(self):
        self.settings.disable()
        self.temp.cleanup()

    @patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.postgresql", "NAME": "db"})
    @patch("core.backup.subprocess.run")
    def test_create_is_dry_run_and_uses_argument_list(self, run, _config):
        result = create_database_backup(self.root / "backups" / "x.dump", dry_run=True)
        self.assertTrue(result["ok"])
        run.assert_not_called()

    @patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.sqlite3", "NAME": "db"})
    def test_sqlite_rejected(self, _config):
        result = create_database_backup(self.root / "backups" / "x.dump", dry_run=True)
        self.assertFalse(result["ok"])

    @patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.postgresql", "NAME": "db"})
    @patch("core.backup.subprocess.run")
    def test_verify_only_pg_restore_list(self, run, _config):
        backup = self.root / "backup.dump"
        backup.write_bytes(b"dump")
        manifest = Path(f"{backup}.manifest.json")
        manifest.write_text(json.dumps({"filename": backup.name, "size": backup.stat().st_size, "sha256": checksum(backup)}), encoding="utf-8")
        result = verify_database_backup(backup, manifest)
        self.assertTrue(result["ok"])
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["pg_restore", "--list"])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_corrupt_backup_is_rejected_before_pg_restore(self):
        backup = self.root / "backup.dump"
        backup.write_bytes(b"dump")
        manifest = Path(f"{backup}.manifest.json")
        manifest.write_text(json.dumps({"filename": backup.name, "size": 99, "sha256": "bad"}), encoding="utf-8")
        with patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.postgresql", "NAME": "db"}), patch("core.backup.subprocess.run") as run:
            result = verify_database_backup(backup, manifest)
        self.assertFalse(result["ok"])
        run.assert_not_called()

    @patch("core.backup._tool_version", return_value="pg_dump (PostgreSQL) 16.0")
    @patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.postgresql", "NAME": "db"})
    @patch("core.backup.subprocess.run")
    def test_create_writes_manifest_and_cleans_temporary_files(self, run, _config, _version):
        def dump(args, **kwargs):
            Path(args[args.index("--file") + 1]).write_bytes(b"custom dump")

        run.side_effect = dump
        result = create_database_backup(self.root / "backups" / "scviajes.dump")
        self.assertTrue(result["ok"])
        manifest = Path(result["manifest"])
        self.assertTrue(manifest.is_file())
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["size"], len(b"custom dump"))
        self.assertFalse(list(manifest.parent.glob(".*.tmp")))

    @patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.postgresql", "NAME": "db"})
    @patch("core.backup.subprocess.run", side_effect=CalledProcessError(1, ["pg_dump"]))
    def test_failed_dump_removes_temporary_file(self, _run, _config):
        result = create_database_backup(self.root / "backups" / "failed.dump")
        self.assertFalse(result["ok"])
        self.assertFalse(list((self.root / "backups").glob(".*.tmp")))

    def test_command_json_is_machine_readable(self):
        output = tempfile.SpooledTemporaryFile(mode="w+")
        call_command("backup_preflight", "--json", stdout=output)
        output.seek(0)
        payload = json.load(output)
        self.assertIn("private_files", payload)

    @patch("core.backup.subprocess.run")
    def test_development_preflight_does_not_execute_postgres_tools(self, run):
        backup_preflight()
        run.assert_not_called()

    def _preflight_result(self, results, key):
        return next(item for item in results["results"] if item["key"] == key)

    @patch("core.backup._server_pg_major_version", return_value=18)
    @patch("core.backup._tool_version", return_value="pg_dump (PostgreSQL) 17.12 (Debian 17.12-0+deb13u1)")
    @patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.postgresql", "NAME": "db"})
    def test_server_tools_version_fails_when_server_is_newer(self, _config, _tool, _server):
        with override_settings(DEBUG=False, TESTING=False):
            result = self._preflight_result(backup_preflight(probe_versions=True), "postgres_server_tools_version")
        self.assertEqual(result["status"], "FAIL")

    @patch("core.backup._server_pg_major_version", return_value=18)
    @patch("core.backup._tool_version", return_value="pg_dump (PostgreSQL) 18.6")
    @patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.postgresql", "NAME": "db"})
    def test_server_tools_version_passes_when_client_matches(self, _config, _tool, _server):
        with override_settings(DEBUG=False, TESTING=False):
            result = self._preflight_result(backup_preflight(probe_versions=True), "postgres_server_tools_version")
        self.assertEqual(result["status"], "PASS")

    @patch("core.backup._server_pg_major_version", return_value=None)
    @patch("core.backup._tool_version", return_value="pg_dump (PostgreSQL) 18.6")
    @patch("core.backup.database_config", return_value={"ENGINE": "django.db.backends.postgresql", "NAME": "db"})
    def test_server_tools_version_fails_in_prod_when_server_unknown(self, _config, _tool, _server):
        with override_settings(DEBUG=False, TESTING=False):
            result = self._preflight_result(backup_preflight(probe_versions=True), "postgres_server_tools_version")
        self.assertEqual(result["status"], "FAIL")
