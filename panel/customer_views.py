"""Consultas de clientes para el panel interno; este módulo no realiza mutaciones."""

from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from customers.models import BookingClaimToken, Customer, CustomerBooking, CustomerConsent, normalize_email

from .permissions import customers_access


PAGE_SIZE = 25
RECENT_DAYS = 30


def _commercial_subquery(field):
    return Subquery(CustomerConsent.objects.filter(
        customer=OuterRef("pk"),
        consent_type=CustomerConsent.ConsentType.COMMERCIAL_COMMUNICATIONS,
    ).order_by("-recorded_at", "-pk").values(field)[:1])


def _customer_queryset():
    return Customer.objects.select_related("user").annotate(
        reservation_count=Count("customer_bookings", distinct=True),
        current_consent=_commercial_subquery("granted"),
        has_purchase=Exists(CustomerBooking.objects.filter(customer=OuterRef("pk"))),
    )


def _choice(value, allowed):
    return value if value in allowed else ""


@customers_access()
@require_GET
def customer_list(request):
    qs = _customer_queryset()
    raw_q = request.GET.get("q", "").strip()
    search = normalize_email(raw_q)
    active = _choice(request.GET.get("active", ""), {"active", "inactive"})
    consent = _choice(request.GET.get("consent", ""), {"active", "inactive"})
    reservations = _choice(request.GET.get("reservations", ""), {"with", "without"})
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    if search:
        qs = qs.filter(Q(normalized_email__icontains=search) | Q(user__first_name__icontains=raw_q) | Q(user__last_name__icontains=raw_q))
    if active:
        qs = qs.filter(user__is_active=(active == "active"))
    if consent:
        qs = qs.filter(current_consent=(consent == "active"))
    if reservations == "with":
        qs = qs.filter(reservation_count__gt=0)
    elif reservations == "without":
        qs = qs.filter(reservation_count=0)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    metrics_qs = _customer_queryset()
    metrics = {
        "total": metrics_qs.count(),
        "consented": metrics_qs.filter(current_consent=True).count(),
        "with_purchases": metrics_qs.filter(has_purchase=True).count(),
        "recent": metrics_qs.filter(created_at__gte=timezone.now() - timedelta(days=RECENT_DAYS)).count(),
    }
    total = qs.count()
    page = Paginator(qs.order_by("-created_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page", 1))
    selected = {"q": raw_q, "active": active, "consent": consent, "reservations": reservations, "date_from": date_from, "date_to": date_to}
    return render(request, "panel/customers/list.html", {"page": page, "total": total, "metrics": metrics, "selected": selected, "recent_days": RECENT_DAYS})


def _detail_data(customer):
    links = list(CustomerBooking.objects.filter(customer=customer).select_related("booking").prefetch_related(
        "booking__legs__trip__route", "booking__payments", "booking__tickets__leg__trip__route",
        "booking__tickets__passenger", "booking__tickets__seat_assignment", "booking__ticket_email_attempts",
        "booking__ticket_fulfillment",
    ))
    booking_ids = [link.booking_id for link in links]
    claims = {claim.booking_id: claim for claim in BookingClaimToken.objects.filter(booking_id__in=booking_ids).only("booking_id", "claimed_at")}
    data = []
    for link in links:
        booking = link.booking
        data.append({
            "booking": booking,
            "payments": list(booking.payments.all()),
            "tickets": list(booking.tickets.all()),
            "fulfillment": getattr(booking, "ticket_fulfillment", None),
            "email_attempts": list(booking.ticket_email_attempts.all()),
            "claim": claims.get(booking.pk),
        })
    consents = list(CustomerConsent.objects.filter(
        customer=customer, consent_type=CustomerConsent.ConsentType.COMMERCIAL_COMMUNICATIONS,
    ).only("granted", "version", "origin", "recorded_at").order_by("-recorded_at", "-pk"))
    return data, consents


@customers_access()
@require_GET
def customer_detail(request, customer_pk):
    customer = get_object_or_404(Customer.objects.select_related("user"), pk=customer_pk)
    bookings, consents = _detail_data(customer)
    return render(request, "panel/customers/detail.html", {
        "customer": customer, "bookings": bookings, "consents": consents,
        "current_consent": consents[0] if consents else None,
    })
