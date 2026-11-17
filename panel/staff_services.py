import hashlib
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from customers.models import Customer, normalize_email
from .models import AuditEvent, StaffInvitation, StaffProfile
from .permissions import can_manage_staff
from core.site_config import public_url

ROLES = ("Superusuario", "Administrador", "Vendedor")


def invalidate_user_sessions(user):
    for session in Session.objects.all():
        try:
            if str(user.pk) == str(session.get_decoded().get("_auth_user_id")):
                session.delete()
        except (KeyError, TypeError, ValueError):
            pass


def _lock_active_administrators(User):
    return list(
        User.objects.select_for_update()
        .filter(is_active=True, groups__name="Administrador")
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def _audit(actor, target, action, before, after):
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity_type="auth.User",
        entity_id=str(target.pk),
        description=f"{action.label}: usuario interno",
        before=before,
        after=after,
    )


@transaction.atomic
def invite_staff(*, actor, email, role):
    if not can_manage_staff(actor):
        raise ValidationError("No tenés permiso para administrar usuarios internos.")
    if not actor.is_superuser:
        if role != "Vendedor":
            raise ValidationError("Los administradores solo pueden invitar vendedores.")
    else:
        if role not in ROLES:
            raise ValidationError("Rol inválido.")

    normalized = normalize_email(email)
    validate_email(normalized)
    User = get_user_model()
    if Customer.objects.filter(normalized_email=normalized).exists():
        raise ValidationError("El correo pertenece a una cuenta de cliente.")
    if User.objects.select_for_update().filter(email__iexact=normalized).exists():
        raise ValidationError("Ya existe un usuario con ese correo.")
    if StaffInvitation.objects.filter(
        normalized_email=normalized, used_at__isnull=True, expires_at__gt=timezone.now()
    ).exists():
        raise ValidationError("Ya existe una invitación vigente para ese correo.")

    raw = secrets.token_urlsafe(32)
    invitation = StaffInvitation.objects.create(
        email=normalized,
        normalized_email=normalized,
        role=role,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(hours=24),
    )
    user = User.objects.create(
        username=normalized[:150], email=normalized, is_active=False, is_staff=True
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    StaffProfile.objects.create(user=user)
    _audit(actor, user, AuditEvent.Action.CREATE, {}, {"role": role, "active": False})
    invitation_url = public_url(f"/panel/invitacion/{raw}/")
    send_mail(
        "Invitación al panel de SC Viajes",
        f"Establecé tu contraseña: {invitation_url}",
        settings.DEFAULT_FROM_EMAIL,
        [normalized],
    )
    return invitation, raw


@transaction.atomic
def accept_invitation(raw_token, password):
    digest = hashlib.sha256(raw_token.strip().encode()).hexdigest()
    invitation = StaffInvitation.objects.select_for_update().filter(
        token_hash=digest, used_at__isnull=True
    ).first()
    if not invitation or invitation.expires_at <= timezone.now():
        raise ValidationError("El enlace es inválido o venció.")
    User = get_user_model()
    user = User.objects.select_for_update().get(email__iexact=invitation.normalized_email)
    validate_password(password, user=user)
    if invitation.role in ("Administrador", "Superusuario"):
        _lock_active_administrators(User)
    user.set_password(password)
    user.is_active = True
    user.is_staff = True
    if invitation.role == "Superusuario":
        user.is_superuser = True
        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        user.groups.set([admin_group])
    else:
        user.is_superuser = False
        group, _ = Group.objects.get_or_create(name=invitation.role)
        user.groups.set([group])
    user.save(update_fields=["password", "is_active", "is_staff", "is_superuser"])
    StaffProfile.objects.get_or_create(user=user)
    invitation.used_at = timezone.now()
    invitation.save(update_fields=["used_at"])
    return user


@transaction.atomic
def update_staff(
    *,
    actor,
    user_id,
    role=None,
    active=None,
    first_name=None,
    last_name=None,
    dni=None,
    whatsapp=None,
):
    User = get_user_model()
    if not can_manage_staff(actor):
        raise ValidationError("No tenés permiso para administrar usuarios internos.")
    _lock_active_administrators(User)
    user = User.objects.select_for_update().get(pk=user_id)

    # Administradores gestionan vendedores pero no admins/superusuarios
    if not actor.is_superuser:
        if user.is_superuser or user.groups.filter(name="Administrador").exists():
            raise ValidationError("Los administradores no pueden gestionar administradores ni superusuarios.")
        if role and role != "Vendedor":
            raise ValidationError("Los administradores solo pueden asignar el rol Vendedor.")

    if user.is_superuser:
        old_role = "Superusuario"
    else:
        old_role = (
            user.groups.filter(name__in=["Administrador", "Vendedor"])
            .values_list("name", flat=True)
            .first()
            or "Vendedor"
        )

    new_role = role or old_role
    if actor.is_superuser:
        if new_role not in ROLES:
            raise ValidationError("Rol inválido.")
    else:
        if new_role != "Vendedor":
            raise ValidationError("Los administradores solo pueden asignar el rol Vendedor.")

    # Auto-bloqueo:
    if user.pk == actor.pk and active is False:
        raise ValidationError("No podés desactivar tu propia cuenta.")
    if user.pk == actor.pk and actor.is_superuser and role and role != "Superusuario":
        raise ValidationError("No podés quitarte el rol de superusuario a vos mismo.")

    # Preservar último superusuario activo:
    if user.is_superuser and (active is False or new_role != "Superusuario"):
        active_superusers = (
            User.objects.filter(is_active=True, is_superuser=True)
            .exclude(pk=user.pk)
            .count()
        )
        if active_superusers < 1:
            raise ValidationError("No se puede desactivar o degradar al último superusuario activo.")

    # Preservar último administrador activo:
    if old_role == "Administrador" and (active is False or new_role != "Administrador"):
        active_admins = (
            User.objects.filter(is_active=True, groups__name="Administrador")
            .exclude(pk=user.pk)
            .count()
        )
        if active_admins < 1:
            raise ValidationError("No se puede dejar el panel sin un Administrador activo.")

    before = {"role": old_role, "active": user.is_active}

    if new_role == "Superusuario":
        user.is_superuser = True
        user.is_staff = True
        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        user.groups.set([admin_group])
    elif new_role == "Administrador":
        user.is_superuser = False
        user.is_staff = True
        admin_group, _ = Group.objects.get_or_create(name="Administrador")
        user.groups.set([admin_group])
    elif new_role == "Vendedor":
        user.is_superuser = False
        user.is_staff = True
        vendedor_group, _ = Group.objects.get_or_create(name="Vendedor")
        user.groups.set([vendedor_group])

    user.is_active = user.is_active if active is None else active
    user_fields = ["is_active", "is_staff", "is_superuser"]
    if first_name is not None:
        user.first_name = first_name.strip()
        user_fields.append("first_name")
    if last_name is not None:
        user.last_name = last_name.strip()
        user_fields.append("last_name")
    user.save(update_fields=user_fields)

    profile, _ = StaffProfile.objects.get_or_create(user=user)
    profile_fields = []
    if dni is not None:
        profile.dni = dni.strip()
        profile_fields.append("dni")
    if whatsapp is not None:
        profile.whatsapp = whatsapp.strip()
        profile_fields.append("whatsapp")
    if profile_fields:
        profile.save(update_fields=profile_fields)

    invalidate_user_sessions(user)
    after = {"role": new_role, "active": user.is_active}
    if before != after:
        _audit(
            actor,
            user,
            AuditEvent.Action.UPDATE if after["active"] else AuditEvent.Action.DEACTIVATE,
            before,
            after,
        )
    return user
