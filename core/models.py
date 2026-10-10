"""Modelos de infraestructura compartida (sin PII)."""

from django.db import models


class AbuseCounter(models.Model):
    """Contador distribuido por ventana; sólo conserva identificadores HMAC."""

    scope = models.CharField(max_length=80)
    identifier_hash = models.CharField(max_length=64)
    window_started_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["scope", "identifier_hash", "window_started_at"],
            name="core_abuse_counter_window_unique",
        )]
        indexes = [models.Index(fields=["window_started_at"], name="core_abuse_counter_window_idx")]
