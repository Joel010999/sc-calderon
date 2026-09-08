from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from operations.models import Route, RouteStop, Stop


STOPS = (
    ("CBA", "Córdoba Capital", "Córdoba", "Córdoba"),
    ("JMA", "Jesús María", "Jesús María", "Córdoba"),
    ("PER", "Perico", "Perico", "Jujuy"),
    ("PAL", "Palpalá", "Palpalá", "Jujuy"),
    ("SSJ", "San Salvador de Jujuy", "San Salvador de Jujuy", "Jujuy"),
)
ROUTES = (
    ("CBA-JUJ", "Córdoba → Jujuy", (
        ("CBA", True, False), ("JMA", True, False),
        ("PER", False, True), ("PAL", False, True), ("SSJ", False, True),
    )),
    ("JUJ-CBA", "Jujuy → Córdoba", (
        ("SSJ", True, False), ("PAL", True, False), ("PER", True, False),
        ("JMA", False, True), ("CBA", False, True),
    )),
)


class Command(BaseCommand):
    help = "Crea las cinco paradas y los dos recorridos confirmados, sin duplicar datos."

    @transaction.atomic
    def handle(self, *args, **options):
        stops = {}
        for code, name, city, province in STOPS:
            stop, _ = Stop.objects.get_or_create(
                code=code, defaults={"name": name, "city": city, "province": province}
            )
            stops[code] = stop

        for code, name, entries in ROUTES:
            route, _ = Route.objects.get_or_create(code=code, defaults={"name": name})
            expected = [(stops[stop_code].pk, sequence, boarding, alighting)
                        for sequence, (stop_code, boarding, alighting) in enumerate(entries, 1)]
            existing = list(route.route_stops.values_list(
                "stop_id", "sequence", "allows_boarding", "allows_alighting"
            ))
            if any(item not in expected for item in existing):
                raise CommandError(f"El recorrido {code} tiene una configuración diferente. Revisala antes de continuar; no se sobrescribieron datos.")
            for stop_id, sequence, boarding, alighting in expected:
                if (stop_id, sequence, boarding, alighting) not in existing:
                    item = RouteStop(route=route, stop_id=stop_id, sequence=sequence,
                                     allows_boarding=boarding, allows_alighting=alighting)
                    item.full_clean()
                    item.save()

        self.stdout.write(self.style.SUCCESS("Paradas y recorridos iniciales disponibles, sin duplicados."))
