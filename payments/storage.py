"""Almacenamiento desacoplado y validación de comprobantes de pago."""

from pathlib import Path
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage, default_storage

from .exceptions import PaymentVoucherError


class ProtectedFileSystemStorage(FileSystemStorage):
    """Storage privado para comprobantes sensible fuera del directorio público.

    Garantiza base_url=None para evitar exposición directa por URL y resuelve
    dinámicamente PROTECTED_MEDIA_ROOT para facilitar overrides en pruebas.
    """

    def __init__(self, location=None, base_url=None, **kwargs):
        self._custom_location = location
        super().__init__(location=location, base_url=None, **kwargs)
        # FileSystemStorage sustituye None por MEDIA_URL; los comprobantes no
        # deben tener URL pública.
        self._base_url = None

    @property
    def base_url(self):
        return None

    @base_url.setter
    def base_url(self, value):
        self._base_url = None

    @property
    def location(self):
        if self._custom_location is not None:
            return str(self._custom_location)
        loc = getattr(settings, "PROTECTED_MEDIA_ROOT", None)
        if loc is None:
            loc = settings.BASE_DIR / "protected_media"
        return str(loc)

    @location.setter
    def location(self, value):
        self._custom_location = value


_voucher_storage = ProtectedFileSystemStorage()


def get_voucher_storage():
    """Retorna la instancia de almacenamiento privado para comprobantes."""
    return _voucher_storage


def voucher_upload_path(instance, filename):
    """Genera un nombre impredecible para el comprobante en una ruta privada.

    Usa un UUID v4 hexadecimal preservando únicamente la extensión normalizada.
    """
    ext = Path(filename).suffix.lower()
    return f"vouchers/{uuid.uuid4().hex}{ext}"


MAGIC_SIGNATURES = {
    ".pdf": (b"%PDF",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}


def validate_voucher_file(file):
    """Valida extensión, tamaño máximo y contenido real de un comprobante de transferencia.

    Solo se admiten extensiones y formatos PDF, JPG, JPEG y PNG (sin WebP).
    Tamaño máximo configurable mediante PAYMENTS_MAX_VOUCHER_SIZE_BYTES (10 MB por defecto).
    Valida contenido inspeccionando los bytes iniciales (firmas mágicas).
    """
    if not file:
        raise ValidationError("El archivo de comprobante es obligatorio.")

    filename = getattr(file, "name", "")
    ext = Path(filename).suffix.lower()
    allowed_extensions = getattr(
        settings,
        "PAYMENTS_ALLOWED_VOUCHER_EXTENSIONS",
        (".pdf", ".jpg", ".jpeg", ".png"),
    )

    if ext not in allowed_extensions or ext == ".webp":
        raise ValidationError(
            "Formato de comprobante no válido. Solo se admiten archivos PDF, JPG, JPEG o PNG."
        )

    max_size_bytes = getattr(
        settings,
        "PAYMENTS_MAX_VOUCHER_SIZE_BYTES",
        10 * 1024 * 1024,
    )

    if getattr(file, "size", 0) > max_size_bytes:
        max_mb = max_size_bytes // (1024 * 1024)
        raise ValidationError(
            f"El comprobante supera el tamaño máximo permitido de {max_mb} MB."
        )

    # Validar contenido inspeccionando los bytes mágicos iniciales
    header = b""
    try:
        if hasattr(file, "seek"):
            file.seek(0)
        if hasattr(file, "read"):
            header = file.read(16)
        if hasattr(file, "seek"):
            file.seek(0)
    except Exception:
        raise ValidationError("No se pudo leer el contenido del comprobante.")

    expected_prefixes = MAGIC_SIGNATURES.get(ext, ())
    if not any(header.startswith(prefix) for prefix in expected_prefixes):
        raise ValidationError(
            "El contenido del archivo no coincide con un formato válido de comprobante (PDF, JPG, JPEG o PNG)."
        )
