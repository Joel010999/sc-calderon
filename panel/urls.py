from django.urls import path
from . import views
from . import operations_views as operations

app_name = 'panel'

urlpatterns = [
    path('login/', views.panel_login, name='login'),
    path('logout/', views.panel_logout, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('operaciones/', operations.overview, name='operations'),
    path('operaciones/recorridos/', operations.routes, name='routes'),
    path('operaciones/colectivos/', operations.buses, name='buses'),
    path('operaciones/colectivos/nuevo/', operations.bus_form, name='bus_create'),
    path('operaciones/colectivos/<int:pk>/', operations.bus_detail, name='bus_detail'),
    path('operaciones/colectivos/<int:pk>/editar/', operations.bus_form, name='bus_edit'),
    path('operaciones/colectivos/<int:pk>/activar/', operations.bus_state, {'active': True}, name='bus_activate'),
    path('operaciones/colectivos/<int:pk>/desactivar/', operations.bus_state, {'active': False}, name='bus_deactivate'),
    path('operaciones/colectivos/<int:bus_pk>/butacas/nueva/', operations.seat_form, name='seat_create'),
    path('operaciones/colectivos/<int:bus_pk>/butacas/<int:pk>/editar/', operations.seat_form, name='seat_edit'),
    path('operaciones/colectivos/<int:bus_pk>/butacas/<int:pk>/activar/', operations.seat_state, {'active': True}, name='seat_activate'),
    path('operaciones/colectivos/<int:bus_pk>/butacas/<int:pk>/desactivar/', operations.seat_state, {'active': False}, name='seat_deactivate'),
]
