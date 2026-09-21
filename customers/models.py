"""Modelos para la gestión de cuentas de clientes, asociaciones de reservas y consentimientos auditables."""

import hashlib
import secrets
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def normalize_email(email: str) -> str:
    """Normaliza una dirección de correo eliminando espacios y convirtiendo a minúsculas."""
    if not email:
        return ""
    return email.strip().lower()


class Customer(models.Model):
    """Perfil de cliente (pasajero) asociado a un usuario del sistema (AUTH_USER_MODEL).
    
    Mantiene separación estricta respecto a los usuarios con permisos de panel interno.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="usuario",
        on_delete=models.CASCADE,
        related_name="customer_profile",
    )
    email = models.EmailField("correo electrónico", unique=True, db_index=True)
    normalized_email = models.EmailField("correo normalizado", unique=True, db_index=True)
    phone = models.CharField("teléfono", max_length=50, blank=True, default="")
    google_sub = models.CharField(
        "identificador de Google",
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )
    created_at = models.DateTimeField("fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["-created_at"]

    def __str__(self):
        full_name = self.user.get_full_name()
        if full_name:
            return f"{full_name} <{self.email}>"
        return self.email

    def clean(self):
        super().clean()
        if self.email:
            self.normalized_email = normalize_email(self.email)

    def save(self, *args, **kwargs):
        if self.email:
            self.normalized_email = normalize_email(self.email)
        super().save(*args, **kwargs)


class CustomerBooking(models.Model):
    """Asociación segura entre un cliente y una reserva (Booking).
    
    Garantiza mediante OneToOneField que una reserva pertenezca exclusivamente a una cuenta de cliente.
    """
    customer = models.ForeignKey(
        Customer,
        verbose_name="cliente",
        on_delete=models.CASCADE,
        related_name="customer_bookings",
    )
    booking = models.OneToOneField(
        "sales.Booking",
        verbose_name="reserva",
        on_delete=models.CASCADE,
        related_name="customer_booking",
    )
    created_at = models.DateTimeField("fecha de asociación", auto_now_add=True)

    class Meta:
        verbose_name = "reserva de cliente"
        verbose_name_plural = "reservas de clientes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Reserva {self.booking.public_id} asociada a {self.customer.email}"


class BookingClaimToken(models.Model):
    """Token de alta entropía para permitir que un comprador invitado reclame su reserva.
    
    Almacena únicamente el hash SHA-256 del token plano entregado al comprador.
    """
    booking = models.OneToOneField(
        "sales.Booking",
        verbose_name="reserva",
        on_delete=models.CASCADE,
        related_name="claim_token",
    )
    token_hash = models.CharField("hash del token de reclamo", max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField("fecha de creación", auto_now_add=True)
    expires_at = models.DateTimeField("fecha de vencimiento", default=timezone.now)
    claimed_at = models.DateTimeField("fecha de reclamo", null=True, blank=True)
    claimed_by = models.ForeignKey(
        Customer,
        verbose_name="reclamado por",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="claimed_tokens",
    )

    class Meta:
        verbose_name = "token de reclamo de reserva"
        verbose_name_plural = "tokens de reclamo de reservas"
        ordering = ["-created_at"]

    @property
    def is_claimed(self) -> bool:
        return self.claimed_at is not None

    def __str__(self):
        return f"Claim token para reserva {self.booking.public_id} ({'Reclamado' if self.is_claimed else 'Disponible'})"


class CustomerConsent(models.Model):
    """Registro auditable de consentimientos otorgados o revocados por un cliente."""

    class ConsentType(models.TextChoices):
        COMMERCIAL_COMMUNICATIONS = "COMMERCIAL_COMMUNICATIONS", "Comunicaciones comerciales y promociones"
        PRIVACY_POLICY = "PRIVACY_POLICY", "Política de privacidad"

    customer = models.ForeignKey(
        Customer,
        verbose_name="cliente",
        on_delete=models.CASCADE,
        related_name="consents",
    )
    consent_type = models.CharField(
        "tipo de consentimiento",
        max_length=50,
        choices=ConsentType.choices,
        default=ConsentType.COMMERCIAL_COMMUNICATIONS,
    )
    granted = models.BooleanField("otorgado", default=False)
    ip_address = models.GenericIPAddressField("dirección IP", null=True, blank=True)
    user_agent = models.TextField("agente de usuario", blank=True, default="")
    version = models.CharField("versión del texto", max_length=20, default="v1.0")
    origin = models.CharField("origen", max_length=50, default="web")
    recorded_at = models.DateTimeField("fecha y hora de registro", auto_now_add=True)

    class Meta:
        verbose_name = "registro de consentimiento"
        verbose_name_plural = "registros de consentimiento"
        ordering = ["-recorded_at"]

    def __str__(self):
        estado = "Otorgado" if self.granted else "Revocado"
        return f"{self.get_consent_type_display()}: {estado} ({self.customer.email}) en {self.recorded_at}"
