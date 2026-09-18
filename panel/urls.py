from django.urls import path
from . import views
from . import operations_views as operations
from . import trip_views as trips
from . import reservation_views as reservations

app_name = 'panel'

urlpatterns = [
    path('login/', views.panel_login, name='login'),
    path('logout/', views.panel_logout, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('reservas/', reservations.booking_list, name='booking_list'),
    path('reservas/nueva/', reservations.booking_create, name='booking_create'),
    path('reservas/<uuid:public_id>/', reservations.booking_detail, name='booking_detail'),
    path('reservas/<uuid:public_id>/liberar/', reservations.booking_release, name='booking_release'),
    path('operaciones/', operations.overview, name='operations'),
    path('operaciones/viajes/', trips.trips, name='trips'),
    path('operaciones/viajes/nuevo/', trips.trip_select, name='trip_create'),
    path('operaciones/viajes/nuevo/<int:route_pk>/<int:bus_pk>/horarios/', trips.trip_schedule, name='trip_schedule'),
    path('operaciones/viajes/<int:pk>/', trips.trip_detail, name='trip_detail'),
    path('operaciones/viajes/<int:trip_pk>/tarifas/nueva/', trips.fare_form, name='fare_create'),
    path('operaciones/viajes/<int:trip_pk>/tarifas/<int:pk>/editar/', trips.fare_form, name='fare_edit'),
    path('operaciones/viajes/<int:trip_pk>/tarifas/<int:pk>/activar/', trips.fare_state, {'active': True}, name='fare_activate'),
    path('operaciones/viajes/<int:trip_pk>/tarifas/<int:pk>/desactivar/', trips.fare_state, {'active': False}, name='fare_deactivate'),
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
