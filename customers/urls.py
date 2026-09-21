"""Rutas URL para la aplicación de clientes."""

from django.urls import path
from . import views

urlpatterns = [
    path("registro/", views.customer_register, name="registro_cliente"),
    path("login/", views.customer_login, name="login_cliente"),
    path("logout/", views.customer_logout, name="logout_cliente"),
    path("mis-viajes/", views.mis_viajes, name="mis_viajes"),
    path("mis-viajes/reclamar/", views.claim_booking_view, name="reclamar_reserva"),
    path("mis-viajes/pasajes/<uuid:public_id>/", views.customer_ticket_download, name="customer_ticket_download"),
    path("consentimiento/", views.customer_consent_update, name="customer_consent_update"),
    path("recuperar-clave/", views.customer_password_reset, name="password_reset"),
    path("recuperar-clave/enviado/", views.customer_password_reset_done, name="password_reset_done"),
    path("recuperar-clave/<str:uidb64>/<str:token>/", views.customer_password_reset_confirm, name="password_reset_confirm"),
    path("recuperar-clave/completado/", views.customer_password_reset_complete, name="password_reset_complete"),
    path("google/login/", views.google_login, name="google_login"),
    path("google/callback/", views.google_callback, name="google_callback"),
]
