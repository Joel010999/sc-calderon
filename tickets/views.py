"""Vistas de verificación pública y descarga segura de pasajes."""

import hashlib

from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import render

from .models import Ticket, TicketAuditEvent, TicketStatus
from .storage import get_ticket_storage


def get_client_ip(request) -> str:
    """Obtiene la dirección IP remota del cliente de manera confiable.

    No confía en cabeceras X-Forwarded-For arbitrarias a menos que se configure
    explícitamente un proxy inverso de confianza.
    """
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def is_authorized_internal_user(user) -> bool:
    """Determina si un usuario tiene rol interno autorizado (Administrador, Vendedor o superusuario)."""
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    return (
        getattr(user, "is_superuser", False)
        or user.groups.filter(name__in=["Administrador", "Vendedor"]).exists()
    )


def verify_ticket_view(request):
    """Vista de verificación de pasajes por código QR en modo estricto de solo lectura.

    Reglas de seguridad y privacidad:
    - Rate limiting en memoria/cache Django por hash de IP (sin registrar el token en la clave).
    - No confía en X-Forwarded-For no verificado.
    - Cabeceras estrictas: no-store, no-referrer y noindex.
    - Jamás expone precio, correo, teléfono, documento completo, identificadores internos ni pagos.
    - No realiza mutaciones de estado ni operaciones de embarque.
    """
    # 1. Rate limiting basado en IP hasheada
    client_ip = get_client_ip(request)
    ip_hash = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()[:16]
    rl_key = f"tickets_verify_rl_{ip_hash}"
    limit = getattr(settings, "TICKETS_RATE_LIMIT_PER_MINUTE", 30)

    try:
        req_count = cache.get(rl_key, 0)
        if req_count >= limit:
            response = HttpResponse(
                "Límite de solicitudes de verificación superado. Por favor, intentá nuevamente más tarde.",
                status=429,
                content_type="text/plain; charset=utf-8",
            )
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Referrer-Policy"] = "no-referrer"
            response["X-Robots-Tag"] = "noindex, nofollow"
            return response

        cache.set(rl_key, req_count + 1, timeout=60)
    except Exception:
        # En caso de fallo transitorio del cache, continuar sin interrumpir
        pass

    # 2. Búsqueda por token seguro
    token = request.GET.get("token", "").strip()
    ticket = None
    verification_status = "NOT_FOUND"

    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        ticket = Ticket.objects.filter(verification_token_hash=token_hash).first()

    if ticket:
        if ticket.status == TicketStatus.ISSUED:
            verification_status = "VALID"
        elif ticket.status == TicketStatus.VOID:
            verification_status = "VOID"
        else:
            verification_status = "NOT_FOUND"

        # Auditoría sin PII
        TicketAuditEvent.objects.create(
            actor=request.user if getattr(request.user, "is_authenticated", False) else None,
            action=TicketAuditEvent.Action.VERIFY,
            ticket=ticket,
            booking=ticket.booking,
            description=f"Verificación de pasaje {ticket.ticket_code}: {verification_status}",
            metadata={"status": ticket.status, "verification_status": verification_status},
        )

    context = {
        "verification_status": verification_status,
        "ticket": ticket,
    }

    response = render(request, "tickets/verify.html", context)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def download_ticket_view(request, public_id=None):
    """Descarga adjunta del pasaje en PDF con control de acceso estricto.

    Reglas de autorización:
    - Usuarios autenticados con rol (Administrador, Vendedor, superusuario): acceso permitido.
    - Usuarios comunes autenticados o staff sin rol: HTTP 403 Forbidden.
    - Descarga pública mediante Bearer token en Authorization header o parámetro ?token=:
      se compara el hash del token de descarga. Tokens inválidos o no coincidentes dan HTTP 404 genérico.
    - Archivo faltante en disco: HTTP 404 genérico (nunca 500).
    """
    user = request.user
    ticket = None

    # Caso 1: Usuario autenticado
    if getattr(user, "is_authenticated", False):
        if not is_authorized_internal_user(user):
            return HttpResponseForbidden("No tenés permiso para acceder a este pasaje.")

        if public_id:
            ticket = Ticket.objects.filter(public_id=public_id).first()
        else:
            raise Http404("Pasaje no encontrado.")

        if not ticket:
            raise Http404("Pasaje no encontrado.")

    # Caso 2: Acceso público mediante token seguro de descarga
    else:
        # Extraer token de query param o de cabecera Authorization: Bearer <token>
        token = request.GET.get("token", "").strip()
        if not token:
            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()

        if not token:
            raise Http404("Pasaje no encontrado.")

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        if public_id:
            ticket = Ticket.objects.filter(public_id=public_id, download_token_hash=token_hash).first()
        else:
            ticket = Ticket.objects.filter(download_token_hash=token_hash).first()

        if not ticket:
            raise Http404("Pasaje no encontrado.")

    # Verificar existencia física del archivo
    storage = get_ticket_storage()
    if not storage.exists(ticket.pdf_path):
        raise Http404("Archivo de pasaje no encontrado.")

    # Auditoría de descarga
    TicketAuditEvent.objects.create(
        actor=user if getattr(user, "is_authenticated", False) else None,
        action=TicketAuditEvent.Action.DOWNLOAD,
        ticket=ticket,
        booking=ticket.booking,
        description=f"Descarga de pasaje {ticket.ticket_code}",
        metadata={"is_internal": getattr(user, "is_authenticated", False)},
    )

    file_handle = storage.open(ticket.pdf_path, "rb")
    filename = f"pasaje-{ticket.ticket_code}.pdf"
    response = FileResponse(file_handle, as_attachment=True, filename=filename, content_type="application/pdf")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
