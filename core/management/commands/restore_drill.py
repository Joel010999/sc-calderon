import json
import subprocess

from django.core.management.base import BaseCommand, CommandError

from core.restore_drill import CONFIRMATION, run_restore_drill


class Command(BaseCommand):
    help = "Ejecuta un restore drill PostgreSQL temporal, sintético y sanitizado."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--source-url", required=True)
        parser.add_argument("--destination-url", required=True)
        parser.add_argument("--backup", required=True)
        parser.add_argument("--confirm", required=True)
        parser.add_argument("--keep-artifacts", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            result = run_restore_drill(
                options["source_url"], options["destination_url"], options["backup"],
                confirmation=options["confirm"], keep=options["keep_artifacts"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise CommandError("No se pudo completar una herramienta del restore drill.") from exc
        if options["as_json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write("PASS: restore drill temporal" if result["ok"] else "FAIL: conteos o esquema no coinciden")
        if not result["ok"]:
            raise CommandError("el restore drill no coincidió")


__all__ = ["CONFIRMATION"]
