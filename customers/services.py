"""Servicios de dominio para la gestión de clientes, reclamo de reservas y auditoría de consentimientos."""

import hashlib
import secrets
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from .models import (
    BookingClaimToken,
    Customer,
    CustomerBooking,
    CustomerConsent,
    normalize_email,
)


def get_client_ip(request) -> str:
    """Obtiene la dirección IP remota del cliente de forma segura."""
    if not request:
        return ""
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def get_customer_for_user(user) -> Customer | None:
    """Obtiene el perfil Customer asociado a un usuario si existe."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "customer_profile", None)


@transaction.atomic
def register_customer(
    email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    commercial_consent: bool = False,
    ip_address: str | None = None,
    user_agent: str = "",
) -> Customer:
    """Registra una nueva cuenta de cliente con correo normalizado y contraseña.
    
    Reglas:
    - Normaliza el correo electrónico eliminando espacios y convirtiendo a minúsculas.
    - Valida el formato del correo y la solidez de la contraseña según los validadores configurados.
    - El cliente queda estrictamente separado del panel (is_staff=False, is_superuser=False).
    - Registra el consentimiento comercial de forma explícita, separada y auditable.
    """
    normalized = normalize_email(email)
    if not normalized:
        raise ValidationError({"email": "El correo electrónico es obligatorio."})

    try:
        validate_email(normalized)
    except ValidationError:
        raise ValidationError({"email": "El formato del correo electrónico no es válido."})

    User = get_user_model()
    if User.objects.filter(email__iexact=normalized).exists() or Customer.objects.filter(normalized_email=normalized).exists():
        raise ValidationError({"email": "Ya existe una cuenta registrada con este correo electrónico."})

    # Validar robustez de la contraseña
    validate_password(password)

    # Crear el usuario Django manteniendo AUTH_USER_MODEL intacto
    username = normalized[:150]
    # Si por alguna razón el username ya existe pero con otro email (caso raro), desambiguar
    if User.objects.filter(username=username).exists():
        username = f"{normalized[:140]}_{secrets.token_hex(4)}"

    user = User.objects.create_user(
        username=username,
        email=normalized,
        password=password,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        is_staff=False,
        is_superuser=False,
    )

    customer = Customer.objects.create(
        user=user,
        email=normalized,
        normalized_email=normalized,
        phone=phone.strip(),
    )

    if commercial_consent:
        record_consent(
            customer=customer,
            consent_type=CustomerConsent.ConsentType.COMMERCIAL_COMMUNICATIONS,
            granted=True,
            ip_address=ip_address,
            user_agent=user_agent,
            origin="registration",
        )

    return customer


def create_booking_claim_token(booking) -> str:
    """Genera y almacena un token criptográfico de alta entropía para compra como invitado.
    
    Retorna el token en plano para ser entregado temporalmente al comprador,
    mientras la base de datos almacena exclusivamente su hash SHA-256.
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    BookingClaimToken.objects.update_or_create(
        booking=booking,
        defaults={
            "token_hash": token_hash,
            "expires_at": timezone.now() + timedelta(hours=getattr(settings, "CUSTOMERS_CLAIM_TOKEN_HOURS", 24)),
            "claimed_at": None,
            "claimed_by": None,
        },
    )
    return raw_token


@transaction.atomic
def claim_booking_with_token(customer: Customer, raw_token: str) -> CustomerBooking:
    """Asocia de forma segura una reserva comprada como invitado a una cuenta de cliente.
    
    Validaciones de seguridad:
    - Verifica que el token de reclamo exista y no haya sido utilizado previamente.
    - Garantiza que la reserva no pertenezca ya a otra cuenta de cliente.
    - Marca el token como reclamado y asocia la reserva atómicamente.
    """
    token_clean = raw_token.strip()
    if not token_clean:
        raise ValidationError("El código de reclamo es obligatorio.")

    token_hash = hashlib.sha256(token_clean.encode("utf-8")).hexdigest()
    claim = (
        BookingClaimToken.objects.select_for_update()
        .filter(token_hash=token_hash)
        .first()
    )

    if not claim or claim.is_claimed:
        raise ValidationError("El código de reclamo es inválido o ya fue utilizado.")
    if claim.expires_at <= timezone.now():
        raise ValidationError("El enlace de reclamo venci?. Solicit? uno nuevo.")

    # Verificar que la reserva no esté ya asociada
    if CustomerBooking.objects.filter(booking=claim.booking).exists():
        raise ValidationError("Esta reserva ya se encuentra asociada a una cuenta.")

    # Asociar la reserva
    customer_booking = CustomerBooking.objects.create(
        customer=customer,
        booking=claim.booking,
    )

    # Actualizar estado del claim token
    claim.claimed_at = timezone.now()
    claim.claimed_by = customer
    claim.save(update_fields=["claimed_at", "claimed_by"])

    return customer_booking


@transaction.atomic
def associate_booking_with_customer(customer: Customer, booking) -> CustomerBooking:
    """Asocia directamente una reserva a un cliente autenticado al momento de la compra."""
    if getattr(booking, "channel", None) != "ONLINE":
        raise ValidationError("Solo se pueden asociar reservas online a una cuenta de cliente.")
    existing = CustomerBooking.objects.filter(booking=booking).first()
    if existing:
        if existing.customer_id == customer.id:
            return existing
        raise ValidationError("La reserva ya está vinculada a otra cuenta de cliente.")

    return CustomerBooking.objects.create(
        customer=customer,
        booking=booking,
    )


def record_consent(
    customer: Customer,
    consent_type: str,
    granted: bool,
    ip_address: str | None = None,
    user_agent: str = "",
    version: str = "v1.0",
    origin: str = "web",
) -> CustomerConsent:
    """Registra un evento auditable de consentimiento con timestamp, IP y User-Agent."""
    return CustomerConsent.objects.create(
        customer=customer,
        consent_type=consent_type,
        granted=granted,
        ip_address=ip_address,
        user_agent=user_agent[:500] if user_agent else "",
        version=version,
        origin=origin[:50],
    )
