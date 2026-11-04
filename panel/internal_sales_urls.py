"""Rutas auxiliares para el flujo de ventas internas por transferencia.

Módulo auxiliar creado según la directiva:
"no editar panel/permissions.py, panel/urls.py, navegación compartida ni documentos canónicos.
Crear módulos específicos nuevos para permisos/rutas auxiliares si hace falta e informar integraciones pendientes."
"""

from django.urls import path
from . import payment_views as payments

app_name = "internal_sales"

urlpatterns = [
    # Rutas auxiliares de conveniencia para ventas internas por transferencia
    path("transferencias/pendientes/", payments.pending_transfers, name="pending_transfers"),
    path("transferencias/<uuid:public_id>/revisar/", payments.transfer_review, name="transfer_review"),
    path("transferencias/<uuid:public_id>/comprobante/", payments.download_voucher, name="voucher_download"),
]
