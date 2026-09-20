from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('buscar/', views.search_trips, name='buscar_viajes'),
    path('checkout/', views.checkout_view, name='checkout'),
    path('checkout/crear/', views.create_public_booking, name='crear_reserva'),
    path('resumen/<uuid:public_id>/', views.booking_summary, name='resumen_reserva'),
    path('resumen/<uuid:public_id>/pago/', views.payment_pending, name='pago_pendiente'),
    path('resumen/<uuid:public_id>/transferencia/iniciar/', views.iniciar_transferencia, name='iniciar_transferencia'),
    path('resumen/<uuid:public_id>/transferencia/', views.pantalla_transferencia, name='pantalla_transferencia'),
    path('resumen/<uuid:public_id>/transferencia/comprobante/', views.subir_comprobante, name='subir_comprobante'),
    path('resumen/<uuid:public_id>/expirar/', views.expire_public_booking, name='expirar_reserva'),
    path('login/', views.login_cliente, name='login_cliente'),
    path('registro/', views.registro_cliente, name='registro_cliente'),
]
