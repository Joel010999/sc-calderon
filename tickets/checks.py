from django.conf import settings
from django.core.checks import Warning, register


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
