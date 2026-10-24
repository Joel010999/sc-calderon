from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from operations.models import Route, RouteStop, Stop


STOPS = (
    ("CBA", "Córdoba Capital", "Córdoba", "Córdoba"),
    ("JMA", "Jesús María", "Jesús María", "Córdoba"),
    ("GUE", "Güemes", "Güemes", "Salta"),
    ("PER", "Perico", "Perico", "Jujuy"),
    ("PAL", "Palpalá", "Palpalá", "Jujuy"),
    ("SSJ", "San Salvador de Jujuy", "San Salvador de Jujuy", "Jujuy"),
)
ROUTES = (
    ("CBA-JUJ", "Córdoba → Jujuy", (
        ("CBA", True, False), ("JMA", True, False), ("GUE", False, True),
        ("PER", False, True), ("PAL", False, True), ("SSJ", False, True),
    )),
    ("JUJ-CBA", "Jujuy → Córdoba", (
        ("SSJ", True, False), ("PAL", True, False), ("PER", True, False),
        ("GUE", True, False), ("JMA", False, True), ("CBA", False, True),
    )),
)


class Command(BaseCommand):
    help = "Crea las seis paradas y los dos recorridos confirmados, sin duplicar datos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Muestra los cambios de configuración sin escribir datos.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        stops = {}
        for code, name, city, province in STOPS:
            stop = Stop.objects.filter(code=code).first()
            if stop is None:
                if dry_run:
                    self.stdout.write(f"CREAR parada {code}: {name} ({province})")
                    continue
                stop = Stop.objects.create(code=code, name=name, city=city, province=province)
            elif stop.province != province:
                raise CommandError(f"La parada {code} tiene provincia {stop.province!r}; se esperaba {province!r}.")
            stops[code] = stop

        for code, name, entries in ROUTES:
            route = Route.objects.filter(code=code).first()
            if route is None:
                if dry_run:
                    self.stdout.write(f"CREAR recorrido {code}: {name}")
                    continue
                route = Route.objects.create(code=code, name=name)

            expected = {
                stop_code: (sequence, boarding, alighting)
                for sequence, (stop_code, boarding, alighting) in enumerate(entries, 1)
            }
            existing = {item.stop.code: item for item in route.route_stops.select_related("stop")}
            unexpected = set(existing) - set(expected)
            if unexpected:
                raise CommandError(
                    f"El recorrido {code} tiene paradas no previstas ({', '.join(sorted(unexpected))}); "
                    "no se sobrescribieron datos."
                )
            for stop_code, item in existing.items():
                _, boarding, alighting = expected[stop_code]
                if (item.allows_boarding, item.allows_alighting) != (boarding, alighting):
                    raise CommandError(
                        f"El recorrido {code} tiene permisos diferentes en {stop_code}; no se sobrescribieron datos."
                    )

            if not existing:
                if dry_run:
                    self.stdout.write(f"CREAR paradas del recorrido {code}: {len(expected)}")
                    continue
                for stop_code, (sequence, boarding, alighting) in expected.items():
                    item = RouteStop(
                        route=route, stop=stops[stop_code], sequence=sequence,
                        allows_boarding=boarding, allows_alighting=alighting,
                    )
                    item.full_clean()
                    item.save()
                continue

            missing = [stop_code for stop_code in expected if stop_code not in existing]
            if missing:
                if route.trips.exists():
                    raise CommandError(
                        f"El recorrido {code} ya tiene viajes; configurá la nueva versión sin reescribir viajes históricos."
                    )
                if len(missing) != 1 or missing[0] != "GUE":
                    raise CommandError(f"El recorrido {code} está incompleto; revisalo antes de continuar.")
                insert_sequence = expected["GUE"][0]
                if dry_run:
                    self.stdout.write(f"INSERTAR GUE en {code}, posición {insert_sequence}")
                    continue
                for item in sorted(existing.values(), key=lambda row: row.sequence, reverse=True):
                    if item.sequence >= insert_sequence:
                        item.sequence += 1
                        item.save(update_fields=["sequence"])
                item = RouteStop(
                    route=route, stop=stops["GUE"], sequence=insert_sequence,
                    allows_boarding=expected["GUE"][1], allows_alighting=expected["GUE"][2],
                )
                item.full_clean()
                item.save()
            elif not dry_run:
                actual = {(item.stop.code, item.sequence) for item in existing.values()}
                expected_positions = {(stop_code, values[0]) for stop_code, values in expected.items()}
                if actual != expected_positions:
                    raise CommandError(f"El recorrido {code} tiene un orden diferente; no se sobrescribieron datos.")

        suffix = " (dry-run; no se escribieron datos)" if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"Paradas y recorridos iniciales disponibles, sin duplicados{suffix}."))
