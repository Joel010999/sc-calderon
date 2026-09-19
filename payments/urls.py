from django.urls import path
from . import views

app_name = "payments"

urlpatterns = [
    path("<uuid:public_id>/comprobante/", views.download_voucher, name="download_voucher"),
]
