"""Comprobaciones de configuración previas a un despliegue."""

from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.color import no_style
from django.core.validators import validate_email
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@dataclass(frozen=True)
class PreflightResult:
    key: str
    status: str
    message: str


def _result(key, status, message):
    return PreflightResult(key, status, message)


def _secure_secret(value):
    return bool(value) and len(value) >= 50 and len(set(value)) >= 5 and not value.startswith("django-insecure-")


def _production():
    return not getattr(settings, "DEBUG", False) and not getattr(settings, "TESTING", False)


def _inside_base(path):
    try:
        Path(path).resolve().relative_to(Path(settings.BASE_DIR).resolve())
        return True
    except ValueError:
        return False


def collect_preflight():
    prod = _production()
    results = []
    results.append(_result("debug", "FAIL" if settings.DEBUG else "PASS", "DEBUG debe estar desactivado." if settings.DEBUG else "DEBUG está desactivado."))
    results.append(_result("secret_key", "PASS" if _secure_secret(settings.SECRET_KEY) else "FAIL", "SECRET_KEY tiene una configuración segura." if _secure_secret(settings.SECRET_KEY) else "SECRET_KEY no cumple el mínimo de seguridad."))

    hosts_ok = bool(settings.ALLOWED_HOSTS) and "*" not in settings.ALLOWED_HOSTS
    results.append(_result("allowed_hosts", "PASS" if hosts_ok else "FAIL", "ALLOWED_HOSTS está definido." if hosts_ok else "ALLOWED_HOSTS está vacío o permite cualquier host."))
    origins = getattr(settings, "CSRF_TRUSTED_ORIGINS", [])
    origins_ok = bool(origins) and all(urlparse(origin).scheme == "https" and bool(urlparse(origin).netloc) for origin in origins)
    results.append(_result("csrf_trusted_origins", "PASS" if origins_ok else ("WARNING" if not prod else "FAIL"), "CSRF_TRUSTED_ORIGINS tiene orígenes HTTPS válidos." if origins_ok else "CSRF_TRUSTED_ORIGINS requiere orígenes HTTPS válidos."))

    proxy_ok = getattr(settings, "SECURE_PROXY_SSL_HEADER", None) == ("HTTP_X_FORWARDED_PROTO", "https") and (not prod or getattr(settings, "SECURE_SSL_REDIRECT", False))
    results.append(_result("https_proxy", "PASS" if proxy_ok else "FAIL", "HTTPS y proxy seguro están configurados." if proxy_ok else "Falta configuración HTTPS/proxy segura."))
    cookies_ok = not prod or (getattr(settings, "SESSION_COOKIE_SECURE", False) and getattr(settings, "CSRF_COOKIE_SECURE", False))
    results.append(_result("secure_cookies", "PASS" if cookies_ok else "FAIL", "Las cookies de seguridad están configuradas." if cookies_ok else "SESSION_COOKIE_SECURE y CSRF_COOKIE_SECURE deben estar activos."))
    hsts_preload = getattr(settings, "SECURE_HSTS_PRELOAD", False) or getattr(settings, "SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    hsts_status = "FAIL" if hsts_preload else ("PASS" if getattr(settings, "SECURE_HSTS_SECONDS", 0) > 0 else "WARNING")
    results.append(_result("hsts", hsts_status, "HSTS respeta la decisión vigente." if hsts_status == "PASS" else "HSTS requiere una decisión explícita antes de activarse."))

    engine = settings.DATABASES.get("default", {}).get("ENGINE", "")
    db_ok = engine.endswith("postgresql") if prod else bool(engine)
    results.append(_result("database_engine", "PASS" if db_ok else ("WARNING" if not prod else "FAIL"), "La base de producción usa PostgreSQL." if db_ok else "Producción debe usar PostgreSQL."))
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        results.append(_result("database_connection", "PASS", "La base de datos responde."))
    except Exception:
        results.append(_result("database_connection", "FAIL", "La base de datos no responde."))
    try:
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        results.append(_result("migrations", "PASS" if not pending else "FAIL", "No hay migraciones pendientes." if not pending else "Hay migraciones pendientes."))
    except Exception:
        results.append(_result("migrations", "FAIL", "No se pudo verificar el estado de migraciones."))

    manifest = Path(settings.STATIC_ROOT) / "staticfiles.json"
    manifest_ok = manifest.is_file()
    results.append(_result("static_manifest", "PASS" if manifest_ok else ("WARNING" if not prod else "FAIL"), "El manifiesto de estáticos está disponible." if manifest_ok else "Falta el manifiesto de archivos estáticos."))
    static_backend = str(settings.STORAGES.get("staticfiles", {}).get("BACKEND", ""))
    whitenoise_ok = any("whitenoise.middleware.WhiteNoiseMiddleware" == item for item in settings.MIDDLEWARE) and (not prod or "whitenoise.storage.CompressedManifestStaticFilesStorage" in static_backend)
    results.append(_result("whitenoise", "PASS" if whitenoise_ok else "FAIL", "WhiteNoise está configurado." if whitenoise_ok else "WhiteNoise no está configurado correctamente."))

    backend = getattr(settings, "PRIVATE_STORAGE_BACKEND", "").lower()
    storage_ok = backend == "s3" and bool(getattr(settings, "PRIVATE_STORAGE_S3_BUCKET", "")) and bool(getattr(settings, "PRIVATE_STORAGE_S3_ENDPOINT_URL", "")) and bool(getattr(settings, "PRIVATE_STORAGE_S3_ACCESS_KEY_ID", "")) and bool(getattr(settings, "PRIVATE_STORAGE_S3_SECRET_ACCESS_KEY", ""))
    results.append(_result("private_storage", "PASS" if storage_ok else ("WARNING" if not prod else "FAIL"), "Storage privado S3-compatible configurado." if storage_ok else "Falta storage privado S3-compatible."))
    endpoint = urlparse(getattr(settings, "PRIVATE_STORAGE_S3_ENDPOINT_URL", ""))
    endpoint_ok = endpoint.scheme in {"http", "https"} and bool(endpoint.netloc) and (not prod or endpoint.scheme == "https")
    results.append(_result("storage_endpoint", "PASS" if endpoint_ok else ("WARNING" if not prod else "FAIL"), "Endpoint S3 válido." if endpoint_ok else "Endpoint S3 inválido o inseguro."))
    signed_ok = int(getattr(settings, "PRIVATE_STORAGE_S3_QUERYSTRING_EXPIRE", 0)) > 0
    results.append(_result("signed_urls", "PASS" if signed_ok else "FAIL", "Las URLs firmadas tienen expiración positiva." if signed_ok else "La expiración de URLs firmadas debe ser positiva."))

    smtp_ok = "smtp" in getattr(settings, "EMAIL_BACKEND", "").lower() and all(bool(getattr(settings, name, "")) for name in ("EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD"))
    results.append(_result("smtp", "PASS" if smtp_ok else ("WARNING" if not prod else "FAIL"), "SMTP está configurado." if smtp_ok else "Falta configuración SMTP."))
    from_ok = True
    for name in ("DEFAULT_FROM_EMAIL", "SERVER_EMAIL"):
        try:
            validate_email(getattr(settings, name, ""))
        except ValidationError:
            from_ok = False
    results.append(_result("email_addresses", "PASS" if from_ok else "FAIL", "DEFAULT_FROM_EMAIL y SERVER_EMAIL son válidos." if from_ok else "DEFAULT_FROM_EMAIL o SERVER_EMAIL no son válidos."))

    oauth_ok = (not getattr(settings, "GOOGLE_OAUTH_ENABLED", False) or bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)) and not (prod and getattr(settings, "GOOGLE_OAUTH_SIMULATION_ENABLED", False))
    results.append(_result("google_oauth", "PASS" if oauth_ok and settings.GOOGLE_OAUTH_ENABLED else ("WARNING" if oauth_ok else "FAIL"), "Google OAuth está configurado o deshabilitado explícitamente." if oauth_ok else "Google OAuth tiene una configuración inconsistente."))
    maintenance_ok = all(getattr(settings, name, 0) > 0 for name in ("OPERATIONAL_MAINTENANCE_MAX_LIMIT", "OPERATIONAL_MAINTENANCE_MAX_SECONDS", "OPERATIONAL_MAINTENANCE_LEASE_SECONDS"))
    results.append(_result("operational_maintenance", "PASS" if maintenance_ok else "FAIL", "Mantenimiento operacional tiene límites seguros." if maintenance_ok else "Mantenimiento operacional tiene límites inválidos."))
    worker_ok = (
        getattr(settings, "MAINTENANCE_WORKER_MODE", "") in {"resident", "cron"}
        and getattr(settings, "MAINTENANCE_WORKER_INTERVAL_SECONDS", 0) > 0
        and getattr(settings, "MAINTENANCE_WORKER_MAX_CYCLE_SECONDS", 0) > 0
        and 0 <= getattr(settings, "MAINTENANCE_WORKER_JITTER_SECONDS", -1) <= 3600
        and 0 <= getattr(settings, "MAINTENANCE_WORKER_LOCK_WAIT_SECONDS", -1) <= 300
    )
    results.append(_result("maintenance_worker", "PASS" if worker_ok else "FAIL", "Worker de mantenimiento tiene configuracion segura." if worker_ok else "La configuracion del worker es invalida."))
    outbox_ok = getattr(settings, "TRANSACTIONAL_NOTIFICATIONS_ENABLED", False) and all(getattr(settings, name, 0) > 0 for name in ("NOTIFICATIONS_MAX_ATTEMPTS", "NOTIFICATIONS_RETRY_DELAY_SECONDS", "NOTIFICATIONS_LEASE_SECONDS"))
    results.append(_result("notifications_outbox", "PASS" if outbox_ok else "FAIL", "Outbox transaccional está configurado." if outbox_ok else "Outbox transaccional incompleto."))

    local_dirs = [settings.MEDIA_ROOT, settings.PROTECTED_MEDIA_ROOT, settings.TICKETS_STORAGE_ROOT]
    unsafe_dirs = any(_inside_base(path) for path in local_dirs)
    results.append(_result("local_directories", "FAIL" if prod and unsafe_dirs else ("WARNING" if unsafe_dirs else "PASS"), "Los directorios sensibles están fuera del proyecto." if not unsafe_dirs else "Hay directorios sensibles dentro del proyecto."))
    base_url = urlparse(getattr(settings, "TICKETS_VERIFICATION_BASE_URL", ""))
    url_ok = base_url.scheme in {"http", "https"} and bool(base_url.netloc) and (not prod or base_url.scheme == "https")
    results.append(_result("required_variables", "PASS" if url_ok else "FAIL", "Las variables esenciales tienen valores consistentes." if url_ok else "Hay variables esenciales vacías o inconsistentes."))
    return results


def preflight_payload():
    results = [asdict(result) for result in collect_preflight()]
    return {"ok": not any(item["status"] == "FAIL" for item in results), "results": results}
