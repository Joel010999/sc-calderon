"""Límites distribuidos hash-only, sin Redis ni almacenamiento de PII."""

import hashlib
import hmac
import ipaddress
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AbuseCounter


def client_ip(request):
    remote = request.META.get("REMOTE_ADDR", "127.0.0.1").strip()
    trusted = set(getattr(settings, "ABUSE_TRUSTED_PROXY_IPS", ()))
    if remote in trusted:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        for candidate in (item.strip() for item in forwarded.split(",")):
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
    try:
        ipaddress.ip_address(remote)
        return remote
    except ValueError:
        return "invalid-client"


def request_identifier(request, subject=None):
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None) or "no-session"
    suffix = "" if subject is None else f"|{subject}"
    return f"{client_ip(request)}|{session_key}{suffix}"


def hashed_identifier(value):
    secret = getattr(settings, "ABUSE_HASH_SECRET", "") or settings.SECRET_KEY
    return hmac.new(
        str(secret).encode(), str(value).encode(), hashlib.sha256
    ).hexdigest()


def consume(request, *, scope, limit, window_seconds, identifier=None):
    """Consume one slot atomically. Returns False when the configured limit is hit."""
    if limit <= 0 or window_seconds <= 0:
        return False
    now = timezone.now()
    epoch = int(now.timestamp())
    start = timezone.datetime.fromtimestamp(
        epoch - (epoch % int(window_seconds)), tz=dt_timezone.utc
    )
    key = hashed_identifier(identifier if identifier is not None else client_ip(request))
    lookup = {"scope": scope, "identifier_hash": key, "window_started_at": start}
    for attempt in range(2):
        try:
            with transaction.atomic():
                row = AbuseCounter.objects.select_for_update().filter(**lookup).first()
                if row is None:
                    row = AbuseCounter.objects.create(**lookup, count=1)
                    return True
                if row.count >= limit:
                    return False
                row.count += 1
                row.save(update_fields=["count", "updated_at"])
                return True
        except IntegrityError:
            if attempt:
                return False
    return False


def prune(*, before=None, limit=1000):
    before = before or timezone.now()
    ids = list(AbuseCounter.objects.filter(window_started_at__lt=before).order_by("pk").values_list("pk", flat=True)[:limit])
    if ids:
        AbuseCounter.objects.filter(pk__in=ids).delete()
    return len(ids)
