from django.conf import settings
from urllib.parse import urlparse

from django.core.checks import Error, Warning, register


@register()
def production_fulfillment_configuration(app_configs, **kwargs):
    if getattr(settings, "DEBUG", False):
        return []
    warnings = []
    if settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
        warnings.append(Warning("EMAIL_BACKEND usa console en producción; configurar un backend SMTP.", id="tickets.W001"))
    if str(settings.TICKETS_STORAGE_ROOT).startswith(str(settings.BASE_DIR)):
        warnings.append(Warning("TICKETS_STORAGE_ROOT está dentro del proyecto; configurar almacenamiento privado persistente.", id="tickets.W002"))
    if not getattr(settings, "EMAIL_HOST", "") and "smtp" in settings.EMAIL_BACKEND.lower():
        warnings.append(Warning("EMAIL_HOST no está configurado para el backend SMTP.", id="tickets.W003"))
    return warnings


@register()
def operational_maintenance_configuration(app_configs, **kwargs):
    """Detecta límites de mantenimiento peligrosamente amplios o inexistentes."""
    if getattr(settings, "OPERATIONAL_MAINTENANCE_MAX_LIMIT", 0) < 1:
        return [Warning("OPERATIONAL_MAINTENANCE_MAX_LIMIT debe ser positivo.", id="tickets.W004")]
    if getattr(settings, "OPERATIONAL_MAINTENANCE_MAX_SECONDS", 0) <= 0:
        return [Warning("OPERATIONAL_MAINTENANCE_MAX_SECONDS debe ser positivo.", id="tickets.W005")]
    if getattr(settings, "OPERATIONAL_MAINTENANCE_LEASE_SECONDS", 0) <= 0:
        return [Warning("OPERATIONAL_MAINTENANCE_LEASE_SECONDS debe ser positivo.", id="tickets.W006")]
    return []


@register()
def private_storage_and_email_configuration(app_configs, **kwargs):
    """Valida storage privado y SMTP sin imprimir credenciales ni secretos."""
    issues = []
    backend = getattr(settings, "PRIVATE_STORAGE_BACKEND", "filesystem").lower()

    if backend not in {"filesystem", "s3"}:
        issues.append(Error(
            "PRIVATE_STORAGE_BACKEND debe ser filesystem o s3.",
            id="tickets.E007",
        ))
    elif not settings.DEBUG and not getattr(settings, "TESTING", False) and backend == "filesystem":
        issues.append(Error(
            "La producción no puede usar filesystem para almacenamiento privado.",
            id="tickets.E008",
        ))
    elif backend == "s3":
        if not getattr(settings, "PRIVATE_STORAGE_S3_BUCKET", ""):
            issues.append(Error("Falta PRIVATE_STORAGE_S3_BUCKET para storage S3.", id="tickets.E009"))
        endpoint = getattr(settings, "PRIVATE_STORAGE_S3_ENDPOINT_URL", "")
        if endpoint:
            parsed = urlparse(endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                issues.append(Error("PRIVATE_STORAGE_S3_ENDPOINT_URL no es una URL válida.", id="tickets.E010"))
            elif not settings.DEBUG and parsed.scheme != "https":
                issues.append(Error("El endpoint S3 productivo debe usar HTTPS.", id="tickets.E011"))
        if not settings.DEBUG and not getattr(settings, "TESTING", False) and (
            not getattr(settings, "PRIVATE_STORAGE_S3_ACCESS_KEY_ID", "")
            or not getattr(settings, "PRIVATE_STORAGE_S3_SECRET_ACCESS_KEY", "")
        ):
            issues.append(Error("Faltan credenciales del storage S3 productivo.", id="tickets.E012"))
        if getattr(settings, "PRIVATE_STORAGE_S3_QUERYSTRING_EXPIRE", 0) <= 0:
            issues.append(Error("PRIVATE_STORAGE_S3_QUERYSTRING_EXPIRE debe ser positivo.", id="tickets.E013"))

    issues.extend(_email_configuration_issues())
    return issues


def _email_configuration_issues():
    issues = []
    issues.extend(Error(message, id="tickets.E014") for message in getattr(settings, "EMAIL_CONFIGURATION_ERRORS", []))
    if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
        issues.append(Error("EMAIL_USE_TLS y EMAIL_USE_SSL no pueden estar activos simultáneamente.", id="tickets.E015"))
    if not 1 <= settings.EMAIL_PORT <= 65535:
        issues.append(Error("EMAIL_PORT debe estar entre 1 y 65535.", id="tickets.E016"))
    if settings.EMAIL_TIMEOUT <= 0:
        issues.append(Error("EMAIL_TIMEOUT debe ser positivo.", id="tickets.E017"))
    if not settings.DEBUG and not getattr(settings, "TESTING", False) and "smtp" in settings.EMAIL_BACKEND.lower():
        missing = [name for name, value in (
            ("EMAIL_HOST", settings.EMAIL_HOST),
            ("EMAIL_HOST_USER", settings.EMAIL_HOST_USER),
            ("EMAIL_HOST_PASSWORD", settings.EMAIL_HOST_PASSWORD),
        ) if not value]
        if missing:
            issues.append(Error(
                "Falta configuración SMTP productiva: " + ", ".join(missing) + ".",
                id="tickets.E018",
            ))
    return issues
