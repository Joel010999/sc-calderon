import json
from django.core.management.base import BaseCommand
from core.backup import backup_preflight, private_file_inventory


class Command(BaseCommand):
    help = "Verifica preparación de backups sin ejecutar pg_dump ni acceder a proveedores."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        payload = backup_preflight()
        payload["private_files"] = private_file_inventory()
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for item in payload["results"]:
                self.stdout.write(f"{item['status']:<7} {item['key']}: {item['message']}")
            self.stdout.write("Archivos privados: " + json.dumps(payload["private_files"], ensure_ascii=False))
            self.stdout.write("Resultado: " + ("PASS" if payload["ok"] else "FAIL"))
        if not payload["ok"]:
            raise SystemExit(1)
