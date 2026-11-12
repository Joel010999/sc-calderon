from datetime import date
import logging
import uuid

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from sales.models import Booking, BookingStatus
from tickets.models import FulfillmentEmailStatus, FulfillmentIssueStatus, TicketFulfillment
from tickets.services import process_booking_fulfillment
from .models import AuditEvent
from .permissions import reservations_access


logger = logging.getLogger(__name__)


def _is_admin(user):
    return user.is_authenticated and user.is_active and (
        user.is_superuser or user.groups.filter(name="Administrador").exists()
    )


@reservations_access()
@require_GET
def fulfillment_list(request):
    is_admin = _is_admin(request.user)
    qs = TicketFulfillment.objects.select_related("booking").prefetch_related("booking__payments").order_by("-updated_at", "pk")
    if not is_admin:
        qs = qs.filter(booking__seller=request.user)

    status = request.GET.get("status", "").strip()
    date_filter = request.GET.get("date", "").strip()
    query = request.GET.get("q", "").strip()
    if status == "pending":
        qs = qs.filter(Q(issue_status__in=[FulfillmentIssueStatus.PENDING, FulfillmentIssueStatus.PROCESSING]) | Q(email_status=FulfillmentEmailStatus.PENDING))
    elif status == "failed":
        qs = qs.filter(Q(issue_status=FulfillmentIssueStatus.FAILED) | Q(email_status=FulfillmentEmailStatus.FAILED))
    elif status in FulfillmentIssueStatus.values:
        qs = qs.filter(issue_status=status)
    elif status in FulfillmentEmailStatus.values:
        qs = qs.filter(email_status=status)
    if date_filter:
        try:
            qs = qs.filter(updated_at__date=date.fromisoformat(date_filter))
        except ValueError:
            pass
    if query:
        filters = Q(booking__email__icontains=query)
        try:
            filters |= Q(booking__public_id=uuid.UUID(query))
        except ValueError:
            pass
        qs = qs.filter(filters)

    missing = Booking.objects.filter(status=BookingStatus.CONFIRMED, ticket_fulfillment__isnull=True).select_related("seller").order_by("-confirmed_at")
    if not is_admin:
        missing = missing.filter(seller=request.user)
    if query:
        missing = missing.filter(Q(email__icontains=query))
    if date_filter:
        try:
            missing = missing.filter(confirmed_at__date=date.fromisoformat(date_filter))
        except ValueError:
            pass
    return render(request, "panel/fulfillment/list.html", {
        "fulfillments": qs[:200],
        "missing_bookings": missing[:50],
        "selected_status": status,
        "selected_date": date_filter,
        "search_q": query,
        "issue_statuses": FulfillmentIssueStatus.choices,
        "email_statuses": FulfillmentEmailStatus.choices,
    })


@reservations_access()
@require_POST
def fulfillment_reconcile(request):
    is_admin = _is_admin(request.user)
    ids = request.POST.getlist("fulfillment_ids")[:20]
    processed = 0
    failed = 0
    for value in ids:
        try:
            if value.startswith("booking:"):
                booking = Booking.objects.get(pk=int(value.split(":", 1)[1]), status=BookingStatus.CONFIRMED)
                if not is_admin and booking.seller_id != request.user.id:
                    failed += 1
                    continue
                job, _ = TicketFulfillment.objects.get_or_create(booking=booking)
            else:
                job = TicketFulfillment.objects.select_related("booking").get(pk=int(value))
                if not is_admin and job.booking.seller_id != request.user.id:
                    failed += 1
                    continue
            if job.booking.status != BookingStatus.CONFIRMED:
                continue
            process_booking_fulfillment(job.pk, retry=True, actor=request.user)
            processed += 1
        except (ValueError, TicketFulfillment.DoesNotExist, Booking.DoesNotExist):
            failed += 1
            continue
        except Exception as exc:
            failed += 1
            logger.warning("Fulfillment reconcile failed id=%s error_type=%s", value[:80], type(exc).__name__)
            continue
    AuditEvent.objects.create(actor=request.user, action=AuditEvent.Action.UPDATE, entity_type="tickets.TicketFulfillment", entity_id="batch", description=f"Reconciliación manual de {processed} trabajos de pasajes", after={"processed": processed, "failed": failed, "requested": len(ids)})
    messages.success(request, f"Se procesaron {processed} trabajos de pasajes. Fallos: {failed}.")
    return redirect("panel:fulfillment_list")
