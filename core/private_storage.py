"""Constructores de almacenamiento privado para archivos sensibles.

El backend se selecciona exclusivamente por configuración de entorno. Las
descargas siguen pasando por las vistas autorizadas; este módulo no expone URLs.
"""

from django.conf import settings
from django.core.exceptions import SuspiciousOperation


_storage_cache = {}


def get_private_storage(*, scope, filesystem_class):
    """Devuelve el storage privado configurado para ``scope``."""
    backend = getattr(settings, "PRIVATE_STORAGE_BACKEND", "filesystem").lower()
    if backend == "filesystem":
        return filesystem_class()
    if backend != "s3":
        raise SuspiciousOperation("Backend de almacenamiento privado no permitido.")

    cache_key = (
        scope,
        backend,
        getattr(settings, "PRIVATE_STORAGE_S3_BUCKET", ""),
        getattr(settings, "PRIVATE_STORAGE_S3_ENDPOINT_URL", ""),
        getattr(settings, "PRIVATE_STORAGE_S3_REGION_NAME", ""),
    )
    if cache_key not in _storage_cache:
        try:
            from storages.backends.s3 import S3Storage
        except ImportError:  # pragma: no cover - check de configuración lo informa
            from storages.backends.s3boto3 import S3Boto3Storage as S3Storage

        class PrivateS3Storage(S3Storage):
            """S3 privado: no permite generar enlaces desde el modelo."""

            def url(self, name, parameters=None, expire=None, http_method=None):
                raise SuspiciousOperation("Los archivos privados no tienen URL pública.")

        _storage_cache[cache_key] = PrivateS3Storage(
            bucket_name=settings.PRIVATE_STORAGE_S3_BUCKET,
            endpoint_url=settings.PRIVATE_STORAGE_S3_ENDPOINT_URL or None,
            region_name=settings.PRIVATE_STORAGE_S3_REGION_NAME or None,
            access_key=settings.PRIVATE_STORAGE_S3_ACCESS_KEY_ID or None,
            secret_key=settings.PRIVATE_STORAGE_S3_SECRET_ACCESS_KEY or None,
            location=scope,
            default_acl=None,
            file_overwrite=False,
            querystring_auth=True,
            querystring_expire=settings.PRIVATE_STORAGE_S3_QUERYSTRING_EXPIRE,
            custom_domain=None,
        )
    return _storage_cache[cache_key]
