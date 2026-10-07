"""Lectura normalizada de auditorías existentes, sin generar eventos nuevos."""
from dataclasses import dataclass
from collections import Counter
from datetime import datetime
from uuid import UUID

from django.core import signing
from django.utils import timezone

from .models import AuditEvent
from tickets.models import TicketAuditEvent
from customers.models import CustomerConsent

_SIGNER = signing.Signer(salt="panel.audit-explorer")


@dataclass(frozen=True)
class AuditRecord:
    key: str
    source: str
    pk: int
    created_at: datetime
    actor: str
    module: str
    action: str
    action_label: str
    result: str
    reference: str
    description: str

    @property
    def token(self):
        return _SIGNER.sign(f"{self.source}:{self.pk}")


def _actor(user):
    # No exponer usernames, que en este proyecto pueden ser emails.
    return "Usuario interno" if user else "Sistema"


def _safe_reference(value):
    if not value:
        return ""
    try:
        return str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return ""


def _panel_record(event):
    after = event.after if isinstance(event.after, dict) else {}
    ref = _safe_reference(after.get("public_id") or after.get("booking_public_id"))
    module = (event.entity_type or "panel").split(".", 1)[0]
    return AuditRecord(f"panel:{event.pk}", "panel", event.pk, event.created_at, _actor(event.actor),
                       module, event.action, event.get_action_display(), "OK", ref,
                       f"{event.get_action_display()} · {module}")


def _ticket_record(event):
    ref = ""
    if event.ticket_id and event.ticket:
        ref = str(event.ticket.public_id)
    elif event.booking_id and event.booking:
        ref = str(event.booking.public_id)
    return AuditRecord(f"tickets:{event.pk}", "tickets", event.pk, event.created_at, _actor(event.actor),
                       "tickets", event.action, event.get_action_display(), "OK", ref,
                       f"{event.get_action_display()} · pasaje")


def _consent_record(event):
    return AuditRecord(f"customers:{event.pk}", "customers", event.pk, event.recorded_at, "Cliente",
                       "customers", "CONSENT", "Consentimiento", "OK", "",
                       f"Consentimiento {'otorgado' if event.granted else 'revocado'}")


def _parse_date(value, end=False):
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        if end:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    except (TypeError, ValueError):
        return None


def build_audit_explorer(params):
    """Devuelve eventos, contadores globales y filtros aplicados; sólo consulta."""
    start, finish = _parse_date(params.get("desde")), _parse_date(params.get("hasta"), True)
    actor = (params.get("actor") or "").strip().lower()
    module = (params.get("modulo") or "").strip().lower()
    action = (params.get("accion") or "").strip().upper()
    result = (params.get("resultado") or "").strip().upper()
    reference = (params.get("referencia") or "").strip().lower()
    panel_qs = AuditEvent.objects.select_related("actor").all()
    ticket_qs = TicketAuditEvent.objects.select_related("actor", "ticket", "booking").all()
    consent_qs = CustomerConsent.objects.all()
    for qs_name, qs in (("panel", panel_qs), ("tickets", ticket_qs), ("customers", consent_qs)):
        if start:
            qs = qs.filter(**({"created_at__gte": start} if qs_name != "customers" else {"recorded_at__gte": start}))
        if finish:
            qs = qs.filter(**({"created_at__lte": finish} if qs_name != "customers" else {"recorded_at__lte": finish}))
        if qs_name == "panel": panel_qs = qs
        elif qs_name == "tickets": ticket_qs = qs
        else: consent_qs = qs
    records = [_panel_record(e) for e in panel_qs] + [_ticket_record(e) for e in ticket_qs] + [_consent_record(e) for e in consent_qs]
    records = [r for r in records if (not actor or actor in r.actor.lower()) and (not module or r.module == module)
               and (not action or r.action == action) and (not result or r.result == result)
               and (not reference or reference in r.reference.lower())]
    records.sort(key=lambda item: (item.created_at, item.pk), reverse=True)
    return {"records": records, "total": len(records),
            "modules": sorted({r.module for r in records}),
            "actions": sorted({(r.action, r.action_label) for r in records}),
            "results": sorted({r.result for r in records}),
            "module_counts": Counter(r.module for r in records),
            "action_counts": Counter(r.action for r in records),
            "result_counts": Counter(r.result for r in records)}


def resolve_audit_token(token):
    try:
        raw = _SIGNER.unsign(token)
        source, raw_pk = raw.split(":", 1)
        pk = int(raw_pk)
    except (signing.BadSignature, ValueError, TypeError):
        return None
    if source == "panel":
        return _panel_record(AuditEvent.objects.select_related("actor").get(pk=pk))
    if source == "tickets":
        return _ticket_record(TicketAuditEvent.objects.select_related("actor", "ticket", "booking").get(pk=pk))
    if source == "customers":
        return _consent_record(CustomerConsent.objects.get(pk=pk))
    return None
