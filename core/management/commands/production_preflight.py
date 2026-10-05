import json

from django.core.management.base import BaseCommand

from core.preflight import preflight_payload


class Command(BaseCommand):
    help = "Verifica la configuración necesaria antes de producción sin imprimir secretos."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json", help="Emite JSON válido.")

    def handle(self, *args, **options):
        payload = preflight_payload()
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for item in payload["results"]:
                self.stdout.write(f"{item['status']:<7} {item['key']}: {item['message']}")
            self.stdout.write("Resultado: " + ("PASS" if payload["ok"] else "FAIL"))
        if not payload["ok"]:
            raise SystemExit(1)
