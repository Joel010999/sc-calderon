from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def is_panel_user(user):
    return user.is_authenticated and user.is_active and (
        user.is_superuser or user.is_staff
        or user.groups.filter(name__in=["Administrador", "Vendedor"]).exists()
    )

def can_manage_operations(user):
    return user.is_authenticated and user.is_active and (
        user.is_superuser or user.groups.filter(name="Administrador").exists()
    )


def require_operations_manager(user):
    if not can_manage_operations(user):
        raise PermissionDenied("No tenés permiso para modificar la estructura operativa.")


def operations_access(*, write=False):
    def decorate(view):
        @login_required(login_url="panel:login")
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if write:
                require_operations_manager(request.user)
            elif not is_panel_user(request.user):
                raise PermissionDenied("No tenés permiso para ingresar al panel.")
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def can_manage_reservations(user):
    return user.is_authenticated and user.is_active and (
        user.is_superuser or user.groups.filter(name__in=["Administrador", "Vendedor"]).exists()
    )


def require_reservations_access(user):
    if not can_manage_reservations(user):
        raise PermissionDenied("No tenés permiso para acceder a las reservas.")


def reservations_access():
    def decorate(view):
        @login_required(login_url="panel:login")
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not can_manage_reservations(request.user):
                raise PermissionDenied("No tenés permiso para acceder a las reservas.")
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def can_manage_payments(user):
    return user.is_authenticated and user.is_active and (
        user.is_superuser or user.groups.filter(name__in=["Administrador", "Vendedor"]).exists()
    )


def require_payments_access(user):
    if not can_manage_payments(user):
        raise PermissionDenied("No tenés permiso para acceder al módulo de pagos.")


def payments_access():
    def decorate(view):
        @login_required(login_url="panel:login")
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not can_manage_payments(request.user):
                raise PermissionDenied("No tenés permiso para acceder al módulo de pagos.")
            return view(request, *args, **kwargs)
        return wrapped
    return decorate
