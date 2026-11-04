"""Módulo URL específico para la gestión de recorridos y paradas en SC Viajes.

IMPORTANTE: Según los límites estrictos del proyecto, este módulo define las
rutas específicas para recorridos y paradas. Queda documentada en el informe
la inclusión pendiente en panel/urls.py (no conectar directamente panel/urls.py).
"""

from django.urls import path
from . import route_views

app_name = "route_management"

urlpatterns = [
    path("operaciones/recorridos/", route_views.route_list, name="route_list"),
    path("operaciones/recorridos/nuevo/", route_views.route_create, name="route_create"),
    path("operaciones/recorridos/<int:pk>/", route_views.route_detail, name="route_detail"),
    path("operaciones/recorridos/<int:pk>/editar/", route_views.route_edit, name="route_edit"),
    path("operaciones/recorridos/<int:pk>/nueva-version/", route_views.route_new_version, name="route_new_version"),
    path("operaciones/recorridos/<int:pk>/activar/", route_views.route_state, {"active": True}, name="route_activate"),
    path("operaciones/recorridos/<int:pk>/desactivar/", route_views.route_state, {"active": False}, name="route_deactivate"),
    path("operaciones/paradas/nueva/", route_views.stop_create, name="stop_create"),
    path("operaciones/paradas/<int:pk>/editar/", route_views.stop_edit, name="stop_edit"),
]
