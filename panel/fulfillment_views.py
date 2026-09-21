from datetime import date
import uuid

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from sales.models import Booking, BookingStatus
from tickets.models import FulfillmentEmailStatus, FulfillmentIssueStatus, TicketFulfillment
from tickets.services import process_booking_fulfillment
from .models import AuditEvent
from .permissions import reservations_access


@reservations_access()
@require_GET
def fulfillment_list(request):
    qs = TicketFulfillment.objects.select_related("booking").prefetch_related("booking__payments").order_by("-updated_at", "pk")
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
    ids = request.POST.getlist("fulfillment_ids")[:20]
    processed = 0
    for value in ids:
        try:
            if value.startswith("booking:"):
                booking = Booking.objects.get(pk=int(value.split(":", 1)[1]), status=BookingStatus.CONFIRMED)
                job, _ = TicketFulfillment.objects.get_or_create(booking=booking)
            else:
                job = TicketFulfillment.objects.get(pk=int(value))
            if job.booking.status != BookingStatus.CONFIRMED:
                continue
            process_booking_fulfillment(job.pk, retry=True, actor=request.user)
            processed += 1
        except (ValueError, TicketFulfillment.DoesNotExist):
            continue
        except Exception:
            continue
    AuditEvent.objects.create(actor=request.user, action=AuditEvent.Action.UPDATE, entity_type="tickets.TicketFulfillment", entity_id="batch", description=f"Reconciliación manual de {processed} trabajos de pasajes", after={"processed": processed, "requested": len(ids)})
    messages.success(request, f"Se procesaron {processed} trabajos de pasajes.")
    return redirect("panel:fulfillment_list")
