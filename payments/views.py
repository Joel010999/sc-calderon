from pathlib import Path

from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET

from .models import Payment
from .services import validate_payment_agent


@require_GET
def download_voucher(request, public_id):
    """Descarga autenticada y autorizada de comprobante de pago como adjunto.

    No expone rutas físicas del servidor y valida permisos de Administrador/Vendedor/superuser.
    """
    if not request.user.is_authenticated:
        return redirect("panel:login")

    try:
        validate_payment_agent(request.user)
    except Exception:
        return HttpResponseForbidden("No tenés permiso para acceder a este comprobante.")

    payment = get_object_or_404(Payment, public_id=public_id)

    if not payment.voucher:
        raise Http404("El pago no posee comprobante adjunto.")

    try:
        file_obj = payment.voucher.open("rb")
    except Exception:
        raise Http404("El archivo de comprobante no se encuentra disponible en el almacenamiento.")

    ext = Path(payment.voucher.name).suffix.lower()
    filename = f"comprobante_{payment.public_id}{ext}"

    return FileResponse(file_obj, as_attachment=True, filename=filename)
