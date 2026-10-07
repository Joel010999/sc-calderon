import json
from django.core.management.base import BaseCommand
from core.backup import verify_database_backup


class Command(BaseCommand):
    help = "Verifica un backup usando únicamente pg_restore --list."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--backup", required=True)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        result = verify_database_backup(options["backup"], options["manifest"], dry_run=options["dry_run"])
        if options["as_json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False))
        else:
            self.stdout.write(("PASS: " if result["ok"] else "FAIL: ") + (result.get("path") or result.get("error", "")))
        if not result["ok"]:
            raise SystemExit(1)
