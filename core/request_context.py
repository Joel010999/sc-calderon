"""Contexto de request seguro para trazabilidad sin datos sensibles."""

import json
import logging
import re
import uuid

from asgiref.local import Local


_REQUEST_ID = Local()
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def get_request_id():
    return getattr(_REQUEST_ID, "value", "") or ""


def set_request_id(value):
    _REQUEST_ID.value = value


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        request = getattr(record, "request", None)
        record.request_id = (
            getattr(record, "request_id", "")
            or getattr(request, "request_id", "")
            or get_request_id()
        )
        return True


def _request_id(request):
    candidate = request.META.get("HTTP_X_REQUEST_ID", "")
    return candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else uuid.uuid4().hex


class RequestIDMiddleware:
    """Acepta un identificador seguro o genera uno, y lo devuelve en la respuesta."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = _request_id(request)
        request.request_id = request_id
        set_request_id(request_id)
        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response


class RequestJSONFormatter(logging.Formatter):
    """Formato JSON mínimo: nunca serializa request.POST, query strings ni payloads."""

    def format(self, record):
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "") or get_request_id(),
        }
        if hasattr(record, "status_code"):
            payload["status_code"] = record.status_code
        if hasattr(record, "method"):
            payload["method"] = record.method
        if hasattr(record, "path"):
            payload["path"] = record.path
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class RequestTextFormatter(logging.Formatter):
    def format(self, record):
        request_id = getattr(record, "request_id", "") or get_request_id()
        return f"{record.levelname} [{request_id}] {record.getMessage()}"
