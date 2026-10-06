"""Ejecuta ciclos de mantenimiento sin duplicar la lógica de dominio."""

import hashlib
import json
import random
import signal
import time
from contextlib import contextmanager
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


LOCK_NAME = "scviajes:operational-maintenance-worker"
MAX_JITTER_SECONDS = 3600
MAX_LOCK_WAIT_SECONDS = 300


def _lock_key(name=LOCK_NAME):
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big", signed=True)


@contextmanager
def advisory_session_lock(wait_seconds=0):
    """Adquiere un lock de sesión PostgreSQL y lo libera al salir.

    SQLite no tiene un equivalente entre procesos; allí el contexto es un
    no-op deliberado para desarrollo y tests, sin prometer exclusión.
    """
    if connection.vendor != "postgresql":
        yield True
        return
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    acquired = False
    while True:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [_lock_key()])
            acquired = bool(cursor.fetchone()[0])
        if acquired or time.monotonic() >= deadline:
            break
        time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_lock_key()])


class Command(BaseCommand):
    help = "Ejecuta mantenimiento operativo en ciclos separados del proceso web."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Ejecuta exactamente un ciclo y sale.")
        parser.add_argument("--max-cycles", type=int, help="Máximo de ciclos antes de salir.")
        parser.add_argument("--max-seconds", type=float, help="Tiempo total máximo del worker.")

    def handle(self, *args, **options):
        self._validate_configuration(options)
        if options["once"] and options["max_cycles"] not in (None, 1):
            raise CommandError("--once no puede combinarse con --max-cycles distinto de 1.")
        if not settings.MAINTENANCE_WORKER_ENABLED:
            self.stdout.write(json.dumps({"maintenance": "worker", "enabled": False}, sort_keys=True))
            return

        stop = {"requested": False}
        previous = {}

        def request_stop(signum, frame):
            stop["requested"] = True

        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, request_stop)

        started = time.monotonic()
        cycles = 0
        aggregate = {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0, "candidates": 0}
        try:
            with advisory_session_lock(settings.MAINTENANCE_WORKER_LOCK_WAIT_SECONDS) as acquired:
                if not acquired:
                    self.stdout.write(json.dumps({
                        "maintenance": "worker", "lock_acquired": False,
                        "lock_wait_seconds": settings.MAINTENANCE_WORKER_LOCK_WAIT_SECONDS,
                    }, sort_keys=True))
                    return
                while not stop["requested"]:
                    if options["max_seconds"] is not None and time.monotonic() - started >= options["max_seconds"]:
                        break
                    if options["max_cycles"] is not None and cycles >= options["max_cycles"]:
                        break
                    cycle = self._run_cycle()
                    cycles += 1
                    for key in aggregate:
                        aggregate[key] += int(cycle.get(key, 0))
                    if options["once"]:
                        break
                    delay = settings.MAINTENANCE_WORKER_INTERVAL_SECONDS
                    delay += random.uniform(0, settings.MAINTENANCE_WORKER_JITTER_SECONDS)
                    deadline = time.monotonic() + delay
                    while not stop["requested"] and time.monotonic() < deadline:
                        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

        self.stdout.write(json.dumps({
            "maintenance": "worker", "enabled": True,
            "mode": settings.MAINTENANCE_WORKER_MODE, "lock_acquired": True,
            "cycles": cycles, "stopped_by_signal": stop["requested"],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "metrics": aggregate,
        }, sort_keys=True))

    @staticmethod
    def _validate_configuration(options):
        positive = (
            settings.MAINTENANCE_WORKER_INTERVAL_SECONDS,
            settings.MAINTENANCE_WORKER_MAX_CYCLE_SECONDS,
        )
        if settings.MAINTENANCE_WORKER_MODE not in {"resident", "cron"}:
            raise CommandError("MAINTENANCE_WORKER_MODE debe ser resident o cron.")
        if settings.MAINTENANCE_WORKER_MODE == "cron" and not options["once"]:
            raise CommandError("El modo cron requiere --once para evitar un loop residente accidental.")
        if any(float(value) <= 0 for value in positive):
            raise CommandError("La configuración del worker debe usar valores positivos.")
        if not 0 <= settings.MAINTENANCE_WORKER_JITTER_SECONDS <= MAX_JITTER_SECONDS:
            raise CommandError("MAINTENANCE_WORKER_JITTER_SECONDS excede el límite seguro.")
        if not 0 <= settings.MAINTENANCE_WORKER_LOCK_WAIT_SECONDS <= MAX_LOCK_WAIT_SECONDS:
            raise CommandError("MAINTENANCE_WORKER_LOCK_WAIT_SECONDS excede el límite seguro.")
        if options["max_cycles"] is not None and options["max_cycles"] < 1:
            raise CommandError("--max-cycles debe ser mayor que cero.")
        if options["max_seconds"] is not None and options["max_seconds"] <= 0:
            raise CommandError("--max-seconds debe ser mayor que cero.")

    @staticmethod
    def _run_cycle():
        output = StringIO()
        try:
            call_command(
                "run_operational_maintenance",
                "--max-seconds", str(settings.MAINTENANCE_WORKER_MAX_CYCLE_SECONDS),
                stdout=output, stderr=StringIO(),
            )
        except CommandError:
            pass
        except Exception:
            return {"failed": 1}
        lines = [line for line in output.getvalue().splitlines() if line.strip()]
        if not lines:
            return {"failed": 1}
        try:
            payload = json.loads(lines[-1])
        except (TypeError, ValueError):
            return {"failed": 1}
        return {key: payload.get(key, 0) for key in ("processed", "succeeded", "failed", "skipped", "candidates")}
