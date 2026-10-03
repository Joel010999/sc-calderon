"""Consulta unificada y de solo lectura para la bandeja operativa."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.urls import reverse
from django.utils import timezone
from operations.models import Trip
from payments.models import Payment, PaymentStatus
from sales.models import Booking, BookingStatus
from tickets.models import EmailAttemptStatus, FulfillmentEmailStatus, FulfillmentIssueStatus, TicketEmailAttempt, TicketFulfillment, TicketStatus
from notifications.models import NotificationStatus, TransactionalNotification

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
CATEGORY_CHOICES = (("held", "Reservas retenidas"), ("voucher", "Comprobantes pendientes"), ("review", "Transferencias en revisión"), ("rejected", "Pagos rechazados"), ("fulfillment", "Entrega de pasajes"), ("tickets", "Tickets incompletos"), ("email", "Correo pendiente"), ("trip", "Viajes próximos"), ("lease", "Trabajos abandonados"))
PRIORITY_CHOICES = (("critical", "Crítica"), ("high", "Alta"), ("medium", "Media"), ("info", "Informativa"))
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}
CATEGORY_CHOICES = CATEGORY_CHOICES + (("notification", "Notificaciones transaccionales"),)

@dataclass(frozen=True)
class InboxItem:
    category: str; category_label: str; priority: str; priority_label: str; status: str; public_ref: str; relevant_at: datetime; timing: str; detail_url: str; trip_ids: tuple = ()

def _local(value):
    return timezone.localtime(value, AR_TZ) if value else None

def _timing(value, now):
    seconds = int((value - now).total_seconds()); prefix = "faltan" if seconds >= 0 else "vencido hace"
    days, rest = divmod(abs(seconds), 86400); hours, remainder = divmod(rest, 3600); minutes = remainder // 60
    text = f"{days} d {hours} h" if days else (f"{hours} h {minutes} min" if hours else f"{minutes} min")
    return f"{prefix} {text}"

def _item(category, priority, status, ref, relevant_at, url, now, trip_ids=()):
    labels = dict(CATEGORY_CHOICES) | dict(PRIORITY_CHOICES)
    return InboxItem(category, labels[category], priority, labels[priority], status, ref, relevant_at, _timing(relevant_at, now), url, tuple(trip_ids))

def build_inbox(*, params=None, now=None):
    now = now or timezone.now(); params = params or {}; selected_type = params.get("type", ""); soon = now + timedelta(minutes=120); items = []
    enabled = lambda *names: not selected_type or selected_type in names
    held = Booking.objects.filter(status=BookingStatus.HELD, expires_at__lte=soon).prefetch_related("legs").only("public_id", "expires_at")
    for booking in held if enabled("held") else []:
        if booking.expires_at < now: priority, status = "critical", "Vencida"
        elif booking.expires_at <= soon: priority, status = "high", "Próxima a vencer"
        else: continue
        items.append(_item("held", priority, status, str(booking.public_id), booking.expires_at, reverse("panel:booking_detail", kwargs={"public_id": booking.public_id}), now, [x.trip_id for x in booking.legs.all()]))
    pending = Payment.objects.filter(status__in=[PaymentStatus.AWAITING_VOUCHER, PaymentStatus.UNDER_REVIEW]).select_related("booking").prefetch_related("booking__legs").only("public_id", "status", "proof_deadline_at", "review_deadline_at", "updated_at", "booking__public_id")
    for payment in pending if enabled("voucher", "review") else []:
        deadline = (payment.proof_deadline_at if payment.status == PaymentStatus.AWAITING_VOUCHER else payment.review_deadline_at) or payment.updated_at; category = "voucher" if payment.status == PaymentStatus.AWAITING_VOUCHER else "review"; status = "Esperando comprobante" if category == "voucher" else "En revisión"; priority = "critical" if deadline < now else ("high" if deadline <= soon else "medium")
        items.append(_item(category, priority, status, str(payment.public_id), deadline, reverse("panel:payment_detail", kwargs={"public_id": payment.public_id}), now, [x.trip_id for x in payment.booking.legs.all()]))
    rejected = Payment.objects.filter(status=PaymentStatus.REJECTED, updated_at__gte=now - timedelta(days=7)).select_related("booking").prefetch_related("booking__legs").only("public_id", "updated_at", "booking__public_id")
    for payment in rejected if enabled("rejected") else []:
        items.append(_item("rejected", "medium", "Rechazado", str(payment.public_id), payment.updated_at, reverse("panel:payment_detail", kwargs={"public_id": payment.public_id}), now, [x.trip_id for x in payment.booking.legs.all()]))
    jobs = TicketFulfillment.objects.filter(booking__status=BookingStatus.CONFIRMED).filter(Q(issue_status__in=[FulfillmentIssueStatus.PENDING, FulfillmentIssueStatus.PROCESSING, FulfillmentIssueStatus.FAILED]) | Q(email_status__in=[FulfillmentEmailStatus.PENDING, FulfillmentEmailStatus.FAILED])).select_related("booking").prefetch_related("booking__legs").only("booking_id", "booking__public_id", "issue_status", "email_status", "lease_until", "next_attempt_at", "updated_at")
    for job in jobs if enabled("fulfillment", "email", "lease") else []:
        ref = str(job.booking.public_id); url = reverse("panel:booking_detail", kwargs={"public_id": job.booking.public_id}); trip_ids = [x.trip_id for x in job.booking.legs.all()]
        if job.issue_status == FulfillmentIssueStatus.FAILED: items.append(_item("fulfillment", "critical", "Emisión fallida", ref, job.updated_at, url, now, trip_ids))
        elif job.issue_status in [FulfillmentIssueStatus.PENDING, FulfillmentIssueStatus.PROCESSING] and not (job.lease_until and job.lease_until <= now): items.append(_item("fulfillment", "medium", "Emisión pendiente", ref, job.next_attempt_at or job.updated_at, url, now, trip_ids))
        if job.email_status == FulfillmentEmailStatus.PENDING: items.append(_item("email", "medium", "Correo pendiente", ref, job.updated_at, url, now, trip_ids))
        elif job.email_status == FulfillmentEmailStatus.FAILED: items.append(_item("email", "critical", "Correo fallido", ref, job.updated_at, url, now, trip_ids))
        if job.issue_status == FulfillmentIssueStatus.PROCESSING and job.lease_until and job.lease_until <= now: items.append(_item("lease", "critical", "Lease vencido", ref, job.lease_until, url, now, trip_ids))
    incomplete = Booking.objects.filter(status=BookingStatus.CONFIRMED).annotate(expected=Count("legs__seat_assignments", filter=Q(legs__seat_assignments__status="CONFIRMED"), distinct=True), issued=Count("tickets", filter=Q(tickets__status=TicketStatus.ISSUED), distinct=True)).filter(expected__gt=F("issued")).prefetch_related("legs").only("public_id", "confirmed_at")
    for booking in incomplete if enabled("tickets") else []: items.append(_item("tickets", "high", "Tickets incompletos", str(booking.public_id), booking.confirmed_at, reverse("panel:booking_detail", kwargs={"public_id": booking.public_id}), now, [x.trip_id for x in booking.legs.all()]))
    attempts = TicketEmailAttempt.objects.filter(status=EmailAttemptStatus.PENDING, booking__ticket_fulfillment__isnull=True).select_related("booking").prefetch_related("booking__legs").only("booking__public_id", "attempted_at")
    for attempt in attempts if enabled("email") else []: items.append(_item("email", "medium", "Correo pendiente", str(attempt.booking.public_id), attempt.attempted_at, reverse("panel:booking_detail", kwargs={"public_id": attempt.booking.public_id}), now, [x.trip_id for x in attempt.booking.legs.all()]))
    notifications = TransactionalNotification.objects.filter(status__in=[NotificationStatus.PENDING, NotificationStatus.FAILED, NotificationStatus.PROCESSING]).select_related("booking").only("pk", "status", "updated_at", "lease_until", "booking__public_id")
    for notification in notifications if enabled("notification") else []:
        if notification.status == NotificationStatus.PROCESSING and notification.lease_until and notification.lease_until > now:
            continue
        priority = "critical" if notification.status == NotificationStatus.FAILED else "medium"
        items.append(_item("notification", priority, f"Notificación {notification.get_status_display().lower()}", str(notification.booking.public_id), notification.updated_at, reverse("panel:booking_detail", kwargs={"public_id": notification.booking.public_id}), now))
    trips = Trip.objects.filter(departure_at__gte=now - timedelta(hours=24), status__in=[Trip.Status.SCHEDULED, Trip.Status.BOARDING, Trip.Status.STARTED]).annotate(held_count=Count("booking_legs", filter=Q(booking_legs__booking__status=BookingStatus.HELD), distinct=True), pending_count=Count("booking_legs__booking__payments", filter=Q(booking_legs__booking__payments__status__in=[PaymentStatus.AWAITING_VOUCHER, PaymentStatus.UNDER_REVIEW]), distinct=True)).filter(Q(held_count__gt=0) | Q(pending_count__gt=0)).select_related("route").only("departure_at", "route__code", "status", "route_id")
    for trip in trips if enabled("trip") else []:
        ref = f"{trip.route.code} · {_local(trip.departure_at).strftime('%d/%m/%Y %H:%M')}"; items.append(_item("trip", "info", "Pendientes en viaje", ref, trip.departure_at, reverse("panel:trip_manifest", kwargs={"trip_pk": trip.pk}), now, [trip.pk]))
    return _filter_and_page(items, params, now)

def _filter_and_page(items, params, now):
    categories, priorities = {x for x, _ in CATEGORY_CHOICES}, {x for x, _ in PRIORITY_CHOICES}; category = params.get("type", "") if params.get("type", "") in categories else ""; priority = params.get("priority", "") if params.get("priority", "") in priorities else ""; status, query, trip_value = params.get("status", "").strip().lower(), params.get("q", "").strip().lower(), params.get("trip", ""); trip_id = int(trip_value) if trip_value.isdigit() else None
    def parsed(value):
        try: return date.fromisoformat(value)
        except (TypeError, ValueError): return None
    lower, upper = parsed(params.get("date_from", "")), parsed(params.get("date_to", "")); filtered = [x for x in items if (not category or x.category == category) and (not priority or x.priority == priority) and (not status or status in x.status.lower()) and (not query or query in x.public_ref.lower()) and (trip_id is None or trip_id in x.trip_ids) and (not lower or _local(x.relevant_at).date() >= lower) and (not upper or _local(x.relevant_at).date() <= upper)]; filtered.sort(key=lambda x: (PRIORITY_ORDER[x.priority], x.relevant_at, x.public_ref)); page = Paginator(filtered, 25).get_page(params.get("page", 1))
    def page_url(number):
        query_params = params.copy(); query_params["page"] = number; return "?" + urlencode(query_params)
    return {"page": page, "total": len(filtered), "updated_at": _local(now), "categories": CATEGORY_CHOICES, "priorities": PRIORITY_CHOICES, "selected": params, "previous_url": page_url(page.previous_page_number()) if page.has_previous() else "", "next_url": page_url(page.next_page_number()) if page.has_next() else ""}
