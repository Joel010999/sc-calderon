from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATE = "CREATE", "Creación"
        UPDATE = "UPDATE", "Edición"
        ACTIVATE = "ACTIVATE", "Activación"
        DEACTIVATE = "DEACTIVATE", "Desactivación"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="usuario actor", null=True,
        on_delete=models.SET_NULL, related_name="panel_audit_events",
    )
    action = models.CharField("acción", max_length=10, choices=Action.choices)
    entity_type = models.CharField("tipo de entidad", max_length=100)
    entity_id = models.CharField("identificador de entidad", max_length=64)
    description = models.CharField("descripción", max_length=255)
    before = models.JSONField("datos anteriores", default=dict)
    after = models.JSONField("datos posteriores", default=dict)
    created_at = models.DateTimeField("fecha y hora", auto_now_add=True)

    class Meta:
        verbose_name = "evento de auditoría"
        verbose_name_plural = "eventos de auditoría"
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.description
