from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('buscar/', views.search_trips, name='buscar_viajes'),
    path('checkout/', views.checkout_view, name='checkout'),
    path('checkout/crear/', views.create_public_booking, name='crear_reserva'),
    path('resumen/<uuid:public_id>/', views.booking_summary, name='resumen_reserva'),
    path('resumen/<uuid:public_id>/pago/', views.payment_pending, name='pago_pendiente'),
    path('resumen/<uuid:public_id>/expirar/', views.expire_public_booking, name='expirar_reserva'),
    path('login/', views.login_cliente, name='login_cliente'),
    path('registro/', views.registro_cliente, name='registro_cliente'),
]
