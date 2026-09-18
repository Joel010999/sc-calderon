from .permissions import can_manage_operations, can_manage_reservations


def panel_permissions(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "can_access_reservations": False,
            "can_manage_operations": False,
        }
    return {
        "can_access_reservations": can_manage_reservations(user),
        "can_manage_operations": can_manage_operations(user),
    }
