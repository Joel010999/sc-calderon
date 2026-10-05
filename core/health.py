"""Health checks públicos con respuestas deliberadamente mínimas."""

from urllib.parse import urlparse

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


def _request_id(request):
    return getattr(request, "request_id", "")


def _production_configuration_ready():
    if getattr(settings, "DEBUG", False):
        return True
    if not getattr(settings, "SECRET_KEY", "") or len(settings.SECRET_KEY) < 50:
        return False
    if not getattr(settings, "ALLOWED_HOSTS", None) or "*" in settings.ALLOWED_HOSTS:
        return False
    if not getattr(settings, "CSRF_TRUSTED_ORIGINS", None):
        return False
    if not getattr(settings, "SECURE_SSL_REDIRECT", False):
        return False
    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None) != ("HTTP_X_FORWARDED_PROTO", "https"):
        return False
    if not getattr(settings, "SESSION_COOKIE_SECURE", False) or not getattr(settings, "CSRF_COOKIE_SECURE", False):
        return False
    if getattr(settings, "SECURE_HSTS_PRELOAD", False) or getattr(settings, "SECURE_HSTS_INCLUDE_SUBDOMAINS", False):
        return False
    if getattr(settings, "PRIVATE_STORAGE_BACKEND", "") != "s3":
        return False
    if not getattr(settings, "PRIVATE_STORAGE_S3_BUCKET", "") or not getattr(settings, "PRIVATE_STORAGE_S3_ACCESS_KEY_ID", ""):
        return False
    endpoint = urlparse(getattr(settings, "PRIVATE_STORAGE_S3_ENDPOINT_URL", ""))
    if not endpoint.netloc or endpoint.scheme != "https":
        return False
    return "smtp" in getattr(settings, "EMAIL_BACKEND", "").lower() and bool(getattr(settings, "EMAIL_HOST", ""))


@require_GET
def live_check(request):
    return JsonResponse({"status": "ok", "request_id": _request_id(request)})


@require_GET
def ready_check(request):
    ready = _production_configuration_ready()
    if ready:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                ready = cursor.fetchone() == (1,)
        except Exception:
            ready = False
    payload = {"status": "ready" if ready else "not_ready", "request_id": _request_id(request)}
    return JsonResponse(payload, status=200 if ready else 503)
