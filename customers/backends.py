"""Backend de autenticación por correo electrónico normalizado para clientes y usuarios."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailAuthBackend(ModelBackend):
    """Permite la autenticación indistinta por correo electrónico normalizado o nombre de usuario."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        email_or_username = kwargs.get("email") or username
        if not email_or_username or not password:
            return None

        normalized = email_or_username.strip().lower()
        try:
            user = user_model.objects.filter(
                Q(email__iexact=normalized) | Q(username__iexact=normalized)
            ).first()
            if user and user.check_password(password) and self.user_can_authenticate(user):
                return user
        except Exception:
            return None
        return None
