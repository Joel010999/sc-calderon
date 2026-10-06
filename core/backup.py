"""Preparación segura de copias PostgreSQL, sin borrar ni restaurar datos."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.db import connection


def _production():
    return not getattr(settings, "DEBUG", False) and not getattr(settings, "TESTING", False)


def _repo_path(path):
    try:
        Path(path).resolve().relative_to(Path(settings.BASE_DIR).resolve())
        return True
    except ValueError:
        return False


def database_config():
    return connection.settings_dict


def _output_root():
    return Path(getattr(settings, "BACKUP_OUTPUT_ROOT", "") or getattr(settings, "BACKUP_OUTPUT_DIR", ""))


def _tool_version(tool):
    if not shutil.which(tool):
        return None
    try:
        result = subprocess.run([tool, "--version"], check=True, shell=False, capture_output=True, text=True, timeout=10)
        return (result.stdout or result.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _add(results, key, status, message):
    results.append({"key": key, "status": status, "message": message})


def backup_preflight():
    """Comprueba preparación sin conectar a PostgreSQL ni crear una copia."""
    prod = _production()
    postgres = str(database_config().get("ENGINE", "")).endswith("postgresql")
    results = []
    _add(results, "database_engine", "PASS" if postgres else ("FAIL" if prod else "WARNING"), "La base es PostgreSQL." if postgres else "Las copias de producción requieren PostgreSQL.")
    output = _output_root()
    outside = bool(output) and output.is_absolute() and not _repo_path(output)
    _add(results, "backup_output", "PASS" if outside else ("FAIL" if prod else "WARNING"), "La salida es absoluta y externa al repositorio." if outside else "BACKUP_OUTPUT_ROOT debe ser absoluto y externo al repositorio.")
    writable = (output.exists() and os.access(output, os.W_OK)) or (not output.exists() and output.parent.exists() and os.access(output.parent, os.W_OK))
    _add(results, "backup_output_writable", "PASS" if writable else ("FAIL" if prod else "WARNING"), "El destino de backups es escribible." if writable else "El destino de backups no es escribible.")
    try:
        free_bytes = shutil.disk_usage(output if output.exists() else output.parent).free
        _add(results, "backup_disk_space", "PASS" if free_bytes > 0 else "FAIL", "Se pudo medir espacio libre." if free_bytes > 0 else "No hay espacio libre en el destino.")
    except OSError:
        _add(results, "backup_disk_space", "WARNING", "No se pudo medir el espacio libre del destino.")
    if output.exists() and os.name != "nt":
        private = (output.stat().st_mode & 0o077) == 0
        _add(results, "backup_output_permissions", "PASS" if private else "FAIL", "El destino tiene permisos privados." if private else "El destino debe tener permisos privados.")
    dump_version = _tool_version("pg_dump")
    restore_version = _tool_version("pg_restore")
    _add(results, "pg_dump", "PASS" if dump_version else ("FAIL" if prod else "WARNING"), "pg_dump está disponible." if dump_version else "pg_dump no está disponible.")
    _add(results, "pg_restore", "PASS" if restore_version else ("FAIL" if prod else "WARNING"), "pg_restore está disponible." if restore_version else "pg_restore no está disponible.")
    compatible = bool(dump_version and restore_version) and dump_version.split()[2:3] == restore_version.split()[2:3]
    _add(results, "postgres_tools_version", "PASS" if compatible else ("WARNING" if not prod else "FAIL"), "Las versiones de pg_dump y pg_restore son compatibles." if compatible else "No se pudo comprobar compatibilidad de versiones PostgreSQL.")
    retention_days = int(getattr(settings, "BACKUP_RETENTION_DAYS", 0) or 0)
    minimum_copies = int(getattr(settings, "BACKUP_MINIMUM_COPIES", 0) or 0)
    retention_ok = retention_days > 0 and minimum_copies > 0
    _add(results, "retention", "PASS" if retention_ok else "FAIL", "La política de retención está configurada." if retention_ok else "BACKUP_RETENTION_DAYS y BACKUP_MINIMUM_COPIES deben ser positivos.")
    encryption_required = bool(getattr(settings, "BACKUP_REQUIRE_ENCRYPTION", False))
    encryption_ok = not encryption_required or bool(getattr(settings, "BACKUP_ENCRYPTION_TOOL", ""))
    _add(results, "encryption", "PASS" if encryption_ok else ("FAIL" if prod else "WARNING"), "La política de cifrado está documentada/configurada." if encryption_ok else "El cifrado requerido depende de una herramienta externa no configurada.")
    for key, raw in (("media", getattr(settings, "MEDIA_ROOT", "")), ("protected_media", getattr(settings, "PROTECTED_MEDIA_ROOT", "")), ("tickets", getattr(settings, "TICKETS_STORAGE_ROOT", ""))):
        private = Path(raw).is_absolute() and not _repo_path(raw)
        _add(results, f"private_files_{key}", "PASS" if private else ("FAIL" if prod else "WARNING"), "Directorio privado externo al repositorio." if private else "El directorio privado está dentro del repositorio o no es absoluto.")
    return {"ok": not any(item["status"] == "FAIL" for item in results), "results": results}


def private_file_inventory():
    """Inventario agregado: no devuelve nombres, rutas, emails, DNI ni tokens."""
    items = []
    for label, raw in (("media", getattr(settings, "MEDIA_ROOT", "")), ("protected_media", getattr(settings, "PROTECTED_MEDIA_ROOT", "")), ("tickets", getattr(settings, "TICKETS_STORAGE_ROOT", ""))):
        root = Path(raw)
        count = size = 0
        objects = []
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    count += 1
                    try:
                        object_size = path.stat().st_size
                        size += object_size
                        objects.append({"index": count, "bytes": object_size, "sha256": checksum(path)})
                    except OSError:
                        pass
        items.append({"scope": label, "files": count, "bytes": size, "objects": objects})
    return items


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_version():
    try:
        from django.db.migrations.recorder import MigrationRecorder
        latest = MigrationRecorder(connection).migration_qs.order_by("-applied").values_list("app", "name").first()
        return f"{latest[0]}:{latest[1]}" if latest else "unknown"
    except Exception:
        return "unknown"


def _manifest_path(backup):
    return Path(f"{backup}.manifest.json")


def _safe_error(_exc):
    return "La herramienta de backup falló; no se expone su salida ni configuración sensible."


def create_database_backup(output, *, dry_run=False):
    if not str(database_config().get("ENGINE", "")).endswith("postgresql"):
        return {"ok": False, "error": "La creación de backups requiere PostgreSQL; SQLite no está permitido."}
    destination = Path(output).expanduser()
    manifest = _manifest_path(destination)
    if not destination.is_absolute() or _repo_path(destination) or _repo_path(manifest):
        return {"ok": False, "error": "La salida debe ser un archivo absoluto fuera del repositorio."}
    if destination.exists() or manifest.exists():
        return {"ok": False, "error": "No se sobrescribe un backup ni su manifest existente."}
    if dry_run:
        return {"ok": True, "dry_run": True, "path": str(destination), "manifest": str(manifest), "format": "custom"}
    preflight = backup_preflight()
    if not preflight["ok"]:
        return {"ok": False, "error": "La configuración de backup no está lista.", "preflight": preflight["results"]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temp_path = Path(temporary)
    try:
        cfg = database_config()
        args = ["pg_dump", "--format=custom", "--file", str(temp_path)]
        for flag, key in (("--host", "HOST"), ("--port", "PORT"), ("--username", "USER"), ("--dbname", "NAME")):
            if cfg.get(key):
                args.extend([flag, str(cfg[key])])
        subprocess.run(args, check=True, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chmod(temp_path, 0o600)
        if destination.exists() or manifest.exists():
            return {"ok": False, "error": "No se sobrescribe un backup ni su manifest existente."}
        os.replace(temp_path, destination)
        payload = {"schema_version": _schema_version(), "postgres_version": (_tool_version("pg_dump") or "unknown"), "timestamp_utc": datetime.now(timezone.utc).isoformat(), "size": destination.stat().st_size, "sha256": checksum(destination), "filename": destination.name, "format": "custom"}
        manifest_fd, manifest_name = tempfile.mkstemp(prefix=f".{manifest.name}.", suffix=".tmp", dir=manifest.parent)
        os.close(manifest_fd)
        manifest_temp = Path(manifest_name)
        try:
            manifest_temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(manifest_temp, 0o600)
            if manifest.exists():
                return {"ok": False, "error": "No se sobrescribe el manifest existente."}
            os.replace(manifest_temp, manifest)
        finally:
            manifest_temp.unlink(missing_ok=True)
        return {"ok": True, "path": str(destination), "manifest": str(manifest), "sha256": payload["sha256"], "size": payload["size"], "format": "custom"}
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "error": _safe_error(None)}
    finally:
        temp_path.unlink(missing_ok=True)


def verify_database_backup(path, manifest_path=None, *, dry_run=False):
    if not str(database_config().get("ENGINE", "")).endswith("postgresql"):
        return {"ok": False, "error": "La verificación de backups de producción requiere PostgreSQL."}
    backup = Path(path).expanduser()
    manifest = Path(manifest_path).expanduser() if manifest_path else Path(f"{backup}.manifest.json")
    if not backup.is_absolute() or not backup.is_file() or backup.is_symlink() or not manifest.is_file() or manifest.is_symlink():
        return {"ok": False, "error": "El archivo de backup o su manifest no es válido."}
    try:
        expected = json.loads(manifest.read_text(encoding="utf-8"))
        actual_size = backup.stat().st_size
        actual_sha = checksum(backup)
        if expected.get("filename") != backup.name or expected.get("size") != actual_size or expected.get("sha256") != actual_sha:
            return {"ok": False, "error": "El backup no coincide con el manifest."}
    except (OSError, ValueError, TypeError):
        return {"ok": False, "error": "El manifest no es válido."}
    if dry_run:
        return {"ok": True, "dry_run": True, "path": str(backup), "manifest": str(manifest), "verification": "sha256/size/pg_restore --list (no ejecutado)"}
    try:
        subprocess.run(["pg_restore", "--list", str(backup)], check=True, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "path": str(backup), "manifest": str(manifest), "sha256": actual_sha, "size": actual_size, "verification": "list"}
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "error": "El backup no pudo verificarse con pg_restore --list."}
