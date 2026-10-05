from django.shortcuts import render


def _error(request, status, title, message):
    return render(request, "core/error.html", {
        "status_code": status,
        "title": title,
        "message": message,
        "request_id": getattr(request, "request_id", ""),
    }, status=status)


def error_400(request, exception=None):
    return _error(request, 400, "Solicitud no válida", "Revisá los datos e intentá nuevamente.")


def error_403(request, exception=None):
    return _error(request, 403, "Acceso no autorizado", "No tenés permisos para ver este contenido.")


def error_404(request, exception=None):
    return _error(request, 404, "Página no encontrada", "La dirección solicitada no existe o ya no está disponible.")


def error_500(request):
    return _error(request, 500, "Ocurrió un error", "No pudimos completar la solicitud. Intentá nuevamente más tarde.")
