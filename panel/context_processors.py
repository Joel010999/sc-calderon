def panel_permissions(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "can_access_reservations": False,
            "can_manage_operations": False,
            "can_manage_payments": False,
            "can_access_operational_inbox": False,
            "can_manage_customers": False,
            "can_access_audit_explorer": False,
        }
    # Resolver todos los enlaces con una sola consulta de grupos. Las vistas
    # vuelven a validar el permiso; esto sólo evita N+1 en el contexto común.
    group_names = set(user.groups.values_list("name", flat=True))
    can_access_reservations = user.is_superuser or bool(group_names & {"Administrador", "Vendedor"})
    return {
        "can_access_reservations": can_access_reservations,
        "can_manage_operations": user.is_superuser or "Administrador" in group_names,
        "can_manage_payments": user.is_superuser or bool(group_names & {"Administrador", "Vendedor"}),
        "can_access_operational_inbox": can_access_reservations,
        # Es el mismo conjunto explícito de roles que las reservas; reutilizar el
        # resultado evita una consulta adicional en cada pantalla del panel.
        "can_manage_customers": can_access_reservations,
        "can_access_audit_explorer": user.is_superuser or "Administrador" in group_names,
    }
