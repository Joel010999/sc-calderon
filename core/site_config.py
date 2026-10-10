"""Configuración pública no sensible de SC Viajes."""

import re
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


BANK_SETTING_NAMES = (
    "BANK_TRANSFER_ACCOUNT_HOLDER",
    "BANK_TRANSFER_ALIAS",
    "BANK_TRANSFER_CVU",
    "BANK_TRANSFER_CUIT",
    "BANK_TRANSFER_ENTITY",
)
_PLACEHOLDER_MARKERS = ("EJEMPLO", "EXAMPLE", "PLACEHOLDER", "REPLACE", "TODO", "CAMBIAR", "XXX")
_ALIAS_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


def bank_transfer_configuration():
    return {name: str(getattr(settings, name, "")).strip() for name in BANK_SETTING_NAMES}


def _placeholder(value):
    normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
    return not normalized or any(marker in normalized for marker in _PLACEHOLDER_MARKERS) or len(set(normalized)) == 1


def _valid_cuit(value):
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    total = sum(int(digit) * weight for digit, weight in zip(digits[:10], (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)))
    remainder = 11 - (total % 11)
    check = 0 if remainder == 11 else 9 if remainder == 10 else remainder
    return check == int(digits[-1])


def validate_bank_transfer_configuration(config=None):
    values = config or bank_transfer_configuration()
    errors = []
    for name in BANK_SETTING_NAMES:
        if not values.get(name, "").strip() or _placeholder(values[name]):
            errors.append(name)
    alias = values.get("BANK_TRANSFER_ALIAS", "").strip().lower()
    if alias and (not 6 <= len(alias) <= 32 or not _ALIAS_RE.fullmatch(alias)):
        errors.append("BANK_TRANSFER_ALIAS_FORMAT")
    cvu = re.sub(r"\D", "", values.get("BANK_TRANSFER_CVU", ""))
    if cvu and (len(cvu) != 22 or len(set(cvu)) == 1):
        errors.append("BANK_TRANSFER_CVU_FORMAT")
    if values.get("BANK_TRANSFER_CUIT", "").strip() and not _valid_cuit(values["BANK_TRANSFER_CUIT"]):
        errors.append("BANK_TRANSFER_CUIT_FORMAT")
    return errors


def bank_transfer_configured():
    return not validate_bank_transfer_configuration()


def public_base_url():
    value = str(getattr(settings, "PUBLIC_BASE_URL", "")).strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ImproperlyConfigured("PUBLIC_BASE_URL debe ser una URL HTTPS sin credenciales.")
    return value.rstrip("/") + "/"


def public_url(path):
    return urljoin(public_base_url(), str(path).lstrip("/"))


def site_branding():
    return {
        "name": getattr(settings, "SITE_BRAND_NAME", "SC Viajes"),
        "tagline": getattr(settings, "SITE_BRAND_TAGLINE", "Pasajes entre Córdoba y Jujuy"),
        "support_email": getattr(settings, "SITE_SUPPORT_EMAIL", ""),
        "whatsapp_url": getattr(settings, "SITE_WHATSAPP_URL", ""),
    }
