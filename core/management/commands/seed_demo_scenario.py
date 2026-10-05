from django.core.management.base import BaseCommand, CommandError
from core.demo_scenario import reset_demo, seed_demo

class Command(BaseCommand):
    help = "Crea un escenario sintetico, idempotente y aislado para aceptacion local."
    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--reset", action="store_true")
    def handle(self, *args, **options):
        if options["dry_run"] and options["reset"]:
            raise CommandError("--dry-run y --reset son opciones excluyentes.")
        try:
            result = reset_demo() if options["reset"] else seed_demo(dry_run=options["dry_run"])
        except (RuntimeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(str(result)))
