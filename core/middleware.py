import logging

from .request_context import RequestIDMiddleware, set_request_id


logger = logging.getLogger("django.request.safe")


class RequestLoggingMiddleware(RequestIDMiddleware):
    """Registra método, ruta sin query string, estado y request ID."""

    def __call__(self, request):
        request_id = None
        response = None
        try:
            response = super().__call__(request)
            request_id = getattr(request, "request_id", "")
            logger.info(
                "request completed",
                extra={"request_id": request_id, "method": request.method, "path": request.path, "status_code": response.status_code},
            )
            return response
        finally:
            # Limpiar después de registrar; nunca incluir excepciones ni payloads.
            if response is None:
                logger.warning(
                    "request failed before response",
                    extra={"request_id": request_id or getattr(request, "request_id", ""), "method": request.method, "path": request.path},
                )
            set_request_id("")
