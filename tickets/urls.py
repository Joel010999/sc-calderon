"""Configuración de URLs para la aplicación tickets."""

from django.urls import path
from . import views

app_name = "tickets"

urlpatterns = [
    path("verify/", views.verify_ticket_view, name="verify"),
    path("download/<uuid:public_id>/", views.download_ticket_view, name="download"),
    path("download/", views.download_ticket_view, name="download_token"),
]
