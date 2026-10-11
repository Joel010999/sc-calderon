"""Restore drill PostgreSQL reproducible, synthetic and deliberately local-only."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url

from core.backup import checksum


TEMP_PREFIX = "scviajes_restore_drill_"
CONFIRMATION = "CONFIRMAR_RESTORE_DRILL_TEMPORAL"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_database_url(value, *, label):
    """Reject ambiguous, remote, non-PostgreSQL or non-drill databases."""
    parsed = urlparse(value)
    host = parsed.hostname or ""
    name = (parsed.path or "").lstrip("/")
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"{label}: se requiere una URL PostgreSQL.")
    if host not in LOCAL_HOSTS:
        raise ValueError(f"{label}: el host debe ser local/loopback.")
    if not name.startswith(TEMP_PREFIX) or name == TEMP_PREFIX:
        raise ValueError(f"{label}: la base debe tener prefijo temporal {TEMP_PREFIX}.")
    if not name.replace("_", "").isalnum():
        raise ValueError(f"{label}: nombre de base ambiguo.")
    if parsed.username is None or parsed.password is None:
        raise ValueError(f"{label}: se exige usuario y credencial temporal.")
    return value


def _database_identity(value):
    parsed = urlparse(value)
    return ("loopback", parsed.port or 5432, (parsed.path or "").lstrip("/"))


def _run(args, *, env=None, capture=False):
    kwargs = {"check": True, "shell": False, "env": env}
    if capture:
        kwargs.update(capture_output=True, text=True)
    else:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return subprocess.run(args, **kwargs)


def _django_manage(command, database_url):
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    _run([sys.executable, "manage.py", *command], env=env)


def _psql(database_url, sql):
    parsed = dj_database_url.parse(database_url)
    args = ["psql", "--no-password", "--tuples-only", "--no-align"]
    for flag, key in (("--host", "HOST"), ("--port", "PORT"), ("--username", "USER"), ("--dbname", "NAME")):
        if parsed.get(key):
            args.extend([flag, str(parsed[key])])
    args.extend(["--command", sql])
    env = os.environ.copy()
    env["PGPASSWORD"] = str(parsed.get("PASSWORD", ""))
    return _run(args, env=env, capture=True).stdout.strip()


def _pg_restore(database_url, dump):
    parsed = dj_database_url.parse(database_url)
    args = ["pg_restore", "--exit-on-error", "--no-owner"]
    for flag, key in (("--host", "HOST"), ("--port", "PORT"), ("--username", "USER"), ("--dbname", "NAME")):
        if parsed.get(key):
            args.extend([flag, str(parsed[key])])
    args.append(str(dump))
    env = os.environ.copy()
    env["PGPASSWORD"] = str(parsed.get("PASSWORD", ""))
    _run(args, env=env)


COUNT_TABLES = {
    "reservas": "sales_booking",
    "asignaciones": "sales_seatassignment",
    "pagos": "payments_payment",
    "caja": "cash_register_cashsession",
    "tickets": "tickets_ticket",
    "fulfillment": "tickets_ticketfulfillment",
    "notificaciones": "notifications_transactionalnotification",
    "clientes": "customers_customer",
    "auditorias": "panel_auditevent",
    "embarques": "tickets_boardingrecord",
    "AbuseCounter": "core_abusecounter",
}


def database_counts(database_url):
    result = {}
    for label, table in COUNT_TABLES.items():
        value = _psql(database_url, f'SELECT count(*) FROM "{table}";')
        result[label] = int(value or 0)
    return result


def schema_signature(database_url):
    sql = """SELECT table_name, column_name, data_type, is_nullable
      FROM information_schema.columns
      WHERE table_schema = 'public'
      ORDER BY table_name, ordinal_position"""
    rows = _psql(database_url, sql).splitlines()
    return rows


def private_files_drill(root):
    """Exercise inventory/copy/checksum/restore with synthetic private documents."""
    source = root / "private-source"
    copied = root / "private-copy"
    restored = root / "private-restored"
    source.mkdir()
    (source / "tickets").mkdir()
    (source / "vouchers").mkdir()
    (source / "tickets" / "synthetic-ticket.pdf").write_bytes(b"%PDF-1.4 synthetic ticket\n")
    (source / "vouchers" / "synthetic-voucher.pdf").write_bytes(b"%PDF-1.4 synthetic voucher\n")

    def inventory(path):
        return sorted(checksum(item) for item in path.rglob("*") if item.is_file())

    before = inventory(source)
    shutil.copytree(source, copied)
    shutil.copytree(copied, restored)
    after = inventory(restored)
    return {"files": len(before), "checksums_match": before == after}


def run_restore_drill(source_url, destination_url, backup_path, *, confirmation, keep=False):
    validate_database_url(source_url, label="origen")
    validate_database_url(destination_url, label="destino")
    if confirmation != CONFIRMATION:
        raise ValueError(f"se requiere confirmación explícita: {CONFIRMATION}")
    if _database_identity(source_url) == _database_identity(destination_url):
        raise ValueError("origen y destino deben ser bases temporales distintas.")
    dump = Path(backup_path).expanduser()
    if not dump.is_absolute() or dump.exists() or Path(f"{dump}.manifest.json").exists():
        raise ValueError("el backup debe ser una ruta nueva; no se sobrescriben artefactos.")
    dump = dump.resolve()
    if dump.suffix != ".dump" or TEMP_PREFIX not in dump.name:
        raise ValueError("el backup debe ser un .dump con nombre temporal explícito.")
    if _psql(destination_url, "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r','p','v','m','S');") != "0":
        raise ValueError("el destino no está vacío; se rechaza la restauración.")
    work = Path(tempfile.mkdtemp(prefix="scviajes-restore-drill-"))
    try:
        _django_manage(["migrate", "--noinput"], source_url)
        _django_manage(["seed_demo_scenario"], source_url)
        before_counts = database_counts(source_url)
        before_schema = schema_signature(source_url)
        env = os.environ.copy()
        env["DATABASE_URL"] = source_url
        _run([sys.executable, "manage.py", "create_database_backup", "--output", str(dump), "--json"], env=env)
        manifest = json.loads(Path(f"{dump}.manifest.json").read_text(encoding="utf-8"))
        if manifest.get("sha256") != checksum(dump) or manifest.get("size") != dump.stat().st_size:
            raise ValueError("el manifest SHA-256/tamaño no coincide con el backup")
        _run(["pg_restore", "--list", str(dump)])
        _pg_restore(destination_url, dump)
        _django_manage(["check"], destination_url)
        _django_manage(["makemigrations", "--check", "--dry-run"], destination_url)
        after_counts = database_counts(destination_url)
        after_schema = schema_signature(destination_url)
        file_drill = private_files_drill(work)
        return {
            "ok": before_counts == after_counts and before_schema == after_schema and file_drill["checksums_match"],
            "source": "synthetic-local-postgresql",
            "destination": "synthetic-local-postgresql",
            "counts": before_counts,
            "restored_counts": after_counts,
            "backup_sha256": manifest.get("sha256"),
            "schema_match": before_schema == after_schema,
            "private_files": file_drill,
            "cleanup": "completed",
        }
    finally:
        if not keep:
            dump.unlink(missing_ok=True)
            Path(f"{dump}.manifest.json").unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
