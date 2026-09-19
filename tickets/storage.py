"""Almacenamiento privado e independiente para pasajes PDF."""

from pathlib import Path
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateTicketFileSystemStorage(FileSystemStorage):
    """Storage privado para pasajes PDF fuera del directorio público.

    Garantiza base_url=None para impedir exposición directa por URL y resuelve
    dinámicamente TICKETS_STORAGE_ROOT para facilitar overrides seguros en tests.
    """

    def __init__(self, location=None, base_url=None, **kwargs):
        self._custom_location = location
        super().__init__(location=location, base_url=None, **kwargs)
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
        loc = getattr(settings, "TICKETS_STORAGE_ROOT", None)
        if loc is None:
            loc = settings.BASE_DIR / "private_tickets"
        return str(loc)

    @location.setter
    def location(self, value):
        self._custom_location = value


_ticket_storage = PrivateTicketFileSystemStorage()


def get_ticket_storage():
    """Retorna la instancia de almacenamiento privado para pasajes PDF."""
    return _ticket_storage


def ticket_upload_path(instance, filename):
    """Genera un nombre impredecible para el pasaje en la ruta privada de almacenamiento.

    Usa un UUID v4 hexadecimal preservando únicamente la extensión .pdf normalizada.
    """
    ext = Path(filename).suffix.lower() or ".pdf"
    return f"tickets/{uuid.uuid4().hex}{ext}"
