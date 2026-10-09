import hashlib, secrets
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
from .models import AuditEvent, StaffInvitation
from core.site_config import public_url

ROLES = ("Administrador", "Vendedor")

def invalidate_user_sessions(user):
    for session in Session.objects.all():
        try:
            if str(user.pk) == str(session.get_decoded().get("_auth_user_id")): session.delete()
        except (KeyError, TypeError, ValueError): pass

def _lock_active_administrators(User):
    return list(
        User.objects.select_for_update()
        .filter(is_active=True, groups__name="Administrador")
        .order_by("pk")
        .values_list("pk", flat=True)
    )

def _audit(actor, target, action, before, after):
    return AuditEvent.objects.create(actor=actor, action=action, entity_type="auth.User", entity_id=str(target.pk), description=f"{action.label}: usuario interno", before=before, after=after)

@transaction.atomic
def invite_staff(*, actor, email, role):
    if role not in ROLES: raise ValidationError("Rol inválido.")
    if role == "Administrador" and not actor.is_superuser:
        raise ValidationError("La invitación de Administrador requiere un superusuario autorizado.")
    normalized = normalize_email(email); validate_email(normalized)
    User = get_user_model()
    if Customer.objects.filter(normalized_email=normalized).exists(): raise ValidationError("El correo pertenece a una cuenta de cliente.")
    if User.objects.select_for_update().filter(email__iexact=normalized).exists(): raise ValidationError("Ya existe un usuario con ese correo.")
    if StaffInvitation.objects.filter(normalized_email=normalized, used_at__isnull=True, expires_at__gt=timezone.now()).exists(): raise ValidationError("Ya existe una invitación vigente para ese correo.")
    raw = secrets.token_urlsafe(32)
    invitation = StaffInvitation.objects.create(email=normalized, normalized_email=normalized, role=role, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=timezone.now()+timedelta(hours=24))
    user = User.objects.create(username=normalized[:150], email=normalized, is_active=False, is_staff=True)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    _audit(actor, user, AuditEvent.Action.CREATE, {}, {"role": role, "active": False})
    invitation_url = public_url(f"/panel/invitacion/{raw}/")
    send_mail("Invitación al panel de SC Viajes", f"Establecé tu contraseña: {invitation_url}", settings.DEFAULT_FROM_EMAIL, [normalized])
    return invitation, raw

@transaction.atomic
def accept_invitation(raw_token, password):
    digest = hashlib.sha256(raw_token.strip().encode()).hexdigest()
    invitation = StaffInvitation.objects.select_for_update().filter(token_hash=digest, used_at__isnull=True).first()
    if not invitation or invitation.expires_at <= timezone.now(): raise ValidationError("El enlace es inválido o venció.")
    User = get_user_model()
    user = User.objects.select_for_update().get(email__iexact=invitation.normalized_email)
    validate_password(password, user=user)
    if invitation.role == "Administrador":
        _lock_active_administrators(User)
    user.set_password(password); user.is_active=True; user.is_staff=True; user.save(update_fields=["password", "is_active", "is_staff"])
    group, _ = Group.objects.get_or_create(name=invitation.role); user.groups.set([group])
    invitation.used_at=timezone.now(); invitation.save(update_fields=["used_at"])
    return user

@transaction.atomic
def update_staff(*, actor, user_id, role=None, active=None):
    User = get_user_model()
    _lock_active_administrators(User)
    user = User.objects.select_for_update().get(pk=user_id)
    if user.is_superuser: raise ValidationError("Los superusuarios no se administran desde esta interfaz.")
    old_role = user.groups.filter(name__in=ROLES).values_list("name", flat=True).first(); new_role = role or old_role
    if new_role not in ROLES: raise ValidationError("El usuario no tiene un rol válido.")
    admins = User.objects.filter(is_active=True, groups__name="Administrador").exclude(pk=user.pk).distinct().count()
    if user.pk == actor.pk and (active is False or (role and role != "Administrador")): raise ValidationError("No podés desactivarte ni quitarte el rol Administrador.")
    if user.is_active and old_role == "Administrador" and (active is False or new_role != "Administrador") and admins < 1: raise ValidationError("No se puede dejar el panel sin un Administrador activo.")
    before={"role":old_role,"active":user.is_active}; group,_=Group.objects.get_or_create(name=new_role); user.groups.set([group]); user.is_active=user.is_active if active is None else active; user.save(update_fields=["is_active"]); invalidate_user_sessions(user); after={"role":new_role,"active":user.is_active}
    if before != after: _audit(actor,user,AuditEvent.Action.UPDATE if after["active"] else AuditEvent.Action.DEACTIVATE,before,after)
    return user
