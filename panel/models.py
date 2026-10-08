from django.conf import settings
from django.db import models
from django.utils import timezone


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

class StaffInvitation(models.Model):
    email = models.EmailField("correo", db_index=True)
    normalized_email = models.EmailField("correo normalizado", unique=True)
    role = models.CharField("rol", max_length=20, choices=(("Administrador", "Administrador"), ("Vendedor", "Vendedor")))
    token_hash = models.CharField("hash del token", max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ["-created_at"]
    @property
    def is_available(self):
        return self.used_at is None and self.expires_at > timezone.now()
