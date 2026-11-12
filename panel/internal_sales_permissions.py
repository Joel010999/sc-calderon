"""Permisos específicos para el flujo interno de ventas por transferencia.

Módulo auxiliar creado según la directiva:
"no editar panel/permissions.py, panel/urls.py, navegación compartida ni documentos canónicos.
Crear módulos específicos nuevos para permisos/rutas auxiliares si hace falta e informar integraciones pendientes."

Reglas:
(1) vendedores no pueden crear reservas manuales sin pago/sin vencimiento; solo administradores/superusuarios.
(5) vendedor verifica externamente y aprueba solo sus ventas; admin/superuser global.
"""

from django.core.exceptions import PermissionDenied


def can_create_manual_unexpired_reservation(user) -> bool:
    """Indica si el usuario tiene permiso para crear una reserva manual indefinida (sin vencimiento).

    Regla (1): Exclusivo para administradores o superusuarios. Vendedores rechazados.
    """
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    return bool(user.is_superuser or user.groups.filter(name="Administrador").exists())


def require_can_create_manual_unexpired_reservation(user) -> None:
    if not can_create_manual_unexpired_reservation(user):
        raise PermissionDenied(
            "Los vendedores no pueden crear reservas manuales sin pago ni vencimiento. "
            "Esta acción está reservada exclusivamente a administradores."
        )


def can_register_internal_transfer(user) -> bool:
    """Indica si el usuario puede registrar ventas internas pendientes por transferencia."""
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    return bool(user.is_superuser or user.groups.filter(name__in=["Administrador", "Vendedor"]).exists())


def can_review_transfer_payment(user, payment=None) -> bool:
    """Indica si el usuario puede revisar (aprobar/rechazar) una transferencia.

    Regla (5):
    - Administrador / Superusuario: global (cualquier venta propia o ajena).
    - Vendedor: exclusivamente sus propias ventas (booking.seller == user o registered_by == user).
    """
    if not user or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False

    if user.is_superuser or user.groups.filter(name="Administrador").exists():
        return True

    if user.groups.filter(name="Vendedor").exists():
        if payment is None:
            return True
        seller_id = getattr(payment.booking, "seller_id", None)
        registered_by_id = getattr(payment, "registered_by_id", None)
        return bool(user.pk in (seller_id, registered_by_id))

    return False


def require_can_review_transfer_payment(user, payment=None) -> None:
    if not can_review_transfer_payment(user, payment):
        if user.groups.filter(name="Vendedor").exists():
            raise PermissionDenied("Los vendedores solo pueden revisar y aprobar sus propias ventas.")
        raise PermissionDenied("No tenés permiso para revisar transferencias.")


def can_view_payment_voucher(user, payment) -> bool:
    """Indica si el usuario puede consultar o descargar el comprobante adjunto."""
    return can_review_transfer_payment(user, payment)
