"""Construccion controlada de un escenario sintetico, aislado y repetible."""
import os
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q
from django.utils import timezone

from customers.models import Customer, CustomerBooking, CustomerConsent
from customers.services import register_customer
from notifications.models import NotificationStatus, TransactionalNotification
from operations.models import Bus, Route, RouteStop, Seat, SeatCategory, Stop, Trip, TripFare
from operations.services import schedule_trip
from payments.models import PaymentStatus
from payments.services import initiate_public_transfer_payment, register_cash_payment, register_transfer_payment, review_transfer_payment
from sales.models import Booking, BookingChannel, BookingStatus, SeatAssignment
from sales.services import create_manual_booking, create_online_booking
from tickets.models import BoardingRecord, BoardingStatus, FulfillmentEmailStatus, FulfillmentIssueStatus, Ticket, TicketFulfillment
from tickets.services import issue_tickets_for_booking

DEMO_PREFIX = "DEMO-20261005"
DEMO_DOMAIN = "demo.scviajes.invalid"
DEMO_STOPS = {"CBA": "Cordoba Demo", "JMA": "Jesus Maria Demo", "PER": "Perico Demo", "PAL": "P al pala Demo", "SSJ": "Jujuy Demo"}

def assert_demo_environment():
    environment = os.getenv("SCVIAJES_ENVIRONMENT", "development").lower()
    explicit = os.getenv("DEMO_SCENARIO_ENABLED", "").lower() in {"1", "true", "yes"}
    if environment == "production" or not (settings.DEBUG or getattr(settings, "TESTING", False) or (explicit and environment in {"demo", "test"})):
        raise RuntimeError("seed_demo_scenario solo esta disponible con DEBUG=True o en un entorno test/demo explicito.")

def _user(suffix, email, group_name):
    User = get_user_model()
    group, _ = Group.objects.get_or_create(name=group_name)
    user, created = User.objects.get_or_create(username=f"{DEMO_PREFIX.lower()}-{suffix}", defaults={"email": email, "is_active": True})
    if created:
        variable = "DEMO_ADMIN_PASSWORD" if group_name == "Administrador" else "DEMO_SELLER_PASSWORD"
        user.set_password(os.getenv(variable) or secrets.token_urlsafe(32))
        user.email, user.is_staff = email, group_name == "Administrador"
        user.save(update_fields=["password", "email", "is_staff"])
    user.groups.add(group)
    return user

def _stops():
    return {code: Stop.objects.get_or_create(code=f"{DEMO_PREFIX}-{code}", defaults={"name": name, "city": name, "province": "Demo"})[0] for code, name in DEMO_STOPS.items()}

def _route(code, entries):
    route, _ = Route.objects.get_or_create(code=f"{DEMO_PREFIX}-{code}", defaults={"name": f"{DEMO_PREFIX} {code}"})
    stops = _stops()
    for sequence, (stop_code, boarding, alighting) in enumerate(entries, 1):
        RouteStop.objects.get_or_create(route=route, sequence=sequence, defaults={"stop": stops[stop_code], "allows_boarding": boarding, "allows_alighting": alighting})
    return route

def _bus(code, plate):
    bus, _ = Bus.objects.get_or_create(code=f"{DEMO_PREFIX}-{code}", defaults={"display_name": f"Colectivo demo {code}", "license_plate": plate})
    for number in range(1, 61):
        Seat.objects.get_or_create(bus=bus, number=number, defaults={"deck": Seat.Deck.UPPER if number <= 48 else Seat.Deck.LOWER, "category": SeatCategory.SEMI_CAMA if number <= 48 else SeatCategory.CAMA, "position_x": (number - 1) % 4, "position_y": (number - 1) // 4, "is_active": number != 60})
    return bus

def _trip(route, bus, days):
    existing = Trip.objects.filter(route=route, bus=bus, departure_at__gt=timezone.now() + timedelta(days=days - 1), departure_at__lt=timezone.now() + timedelta(days=days + 1)).first()
    if existing:
        return existing
    start = timezone.now() + timedelta(days=days)
    route_stops = list(route.route_stops.order_by("sequence"))
    trip = schedule_trip(route=route, bus=bus, schedules={item.stop_id: start + timedelta(hours=index * 2) for index, item in enumerate(route_stops)})
    trip_stops = list(trip.trip_stops.order_by("sequence"))
    for origin in trip_stops:
        for destination in trip_stops:
            if origin.sequence >= destination.sequence or not origin.allows_boarding or not destination.allows_alighting:
                continue
            distance = destination.sequence - origin.sequence
            for category, amount in ((SeatCategory.SEMI_CAMA, Decimal("10000.00") + distance * Decimal("500.00")), (SeatCategory.CAMA, Decimal("15000.00") + distance * Decimal("750.00"))):
                TripFare.objects.get_or_create(trip=trip, origin_stop=origin, destination_stop=destination, seat_category=category, defaults={"amount": amount})
    return trip

def _passenger(number):
    return {"first_name": f"Demo{number}", "last_name": "Pasajero", "document_type": "DNI", "document_number": f"990000{number}", "birth_date": "1990-01-01", "nationality": "Argentina"}

def _booking(email, trip, seat_number, *, channel=BookingChannel.ONLINE, seller=None, now=None):
    existing = Booking.objects.filter(email=email, channel=channel).order_by("-pk").first()
    if existing:
        return existing
    origin, destination = trip.trip_stops.first(), trip.trip_stops.last()
    kwargs = {"email": email, "legs": [{"trip": trip, "origin_stop": origin, "destination_stop": destination, "seats": [trip.bus.seats.get(number=seat_number)]}], "passengers_data": [_passenger(seat_number)], "now": now}
    if channel == BookingChannel.MANUAL:
        kwargs["seller"] = seller
        return create_manual_booking(**kwargs)
    return create_online_booking(**kwargs)

def _voucher():
    return SimpleUploadedFile("demo-voucher.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")

def seed_demo(*, dry_run=False):
    assert_demo_environment()
    if dry_run:
        return {"dry_run": True, "would_create": ["operaciones", "usuarios", "reservas", "pagos", "pasajes", "fulfillment", "notificaciones"]}
    now = timezone.now()
    admin = _user("admin", f"admin@{DEMO_DOMAIN}", "Administrador")
    seller = _user("vendedor", f"vendedor@{DEMO_DOMAIN}", "Vendedor")
    customer = Customer.objects.filter(normalized_email=f"cliente@{DEMO_DOMAIN}").first()
    if not customer:
        customer = register_customer(f"cliente@{DEMO_DOMAIN}", os.getenv("DEMO_CUSTOMER_PASSWORD") or secrets.token_urlsafe(32), "Cliente Demo", "Sintetico", commercial_consent=True)
    CustomerConsent.objects.get_or_create(customer=customer, consent_type=CustomerConsent.ConsentType.COMMERCIAL_COMMUNICATIONS, granted=True, defaults={"version": "demo-1", "origin": "demo"})
    _stops()
    route_out = _route("IDA", [("CBA", True, False), ("JMA", True, False), ("PER", False, True), ("PAL", False, True), ("SSJ", False, True)])
    route_back = _route("VUELTA", [("SSJ", True, False), ("PAL", True, False), ("PER", True, False), ("JMA", False, True), ("CBA", False, True)])
    trip_out, trip_back = _trip(route_out, _bus("IDA", "DEMO001"), 7), _trip(route_back, _bus("VUELTA", "DEMO002"), 10)
    held = _booking(f"held@{DEMO_DOMAIN}", trip_out, 1, now=now)
    round_trip = Booking.objects.filter(email=f"roundtrip@{DEMO_DOMAIN}").first()
    if not round_trip:
        round_trip = create_online_booking(
            email=f"roundtrip@{DEMO_DOMAIN}",
            legs=[
                {"trip": trip_out, "origin_stop": trip_out.trip_stops.first(), "destination_stop": trip_out.trip_stops.last(), "seats": [trip_out.bus.seats.get(number=6)]},
                {"trip": trip_back, "origin_stop": trip_back.trip_stops.first(), "destination_stop": trip_back.trip_stops.last(), "seats": [trip_back.bus.seats.get(number=6)]},
            ],
            passengers_data=[_passenger(6)],
            now=now,
        )
    awaiting = _booking(f"awaiting@{DEMO_DOMAIN}", trip_out, 2, now=now)
    if not awaiting.payments.filter(status=PaymentStatus.AWAITING_VOUCHER).exists():
        initiate_public_transfer_payment(booking_or_id=awaiting, now=now)
    review_booking = _booking(f"review@{DEMO_DOMAIN}", trip_out, 3, channel=BookingChannel.MANUAL, seller=seller, now=now)
    if not review_booking.payments.filter(status=PaymentStatus.UNDER_REVIEW).exists():
        register_transfer_payment(booking_or_id=review_booking, seller=seller, voucher=_voucher(), now=now)
    rejected_booking = _booking(f"rejected@{DEMO_DOMAIN}", trip_out, 4, channel=BookingChannel.MANUAL, seller=seller, now=now)
    rejected_payment = rejected_booking.payments.filter(status=PaymentStatus.REJECTED).first()
    if not rejected_payment:
        rejected_payment = rejected_booking.payments.filter(status=PaymentStatus.UNDER_REVIEW).first() or register_transfer_payment(booking_or_id=rejected_booking, seller=seller, voucher=_voucher(), now=now)
        review_transfer_payment(payment_or_id=rejected_payment, reviewer=admin, approved=False, rejection_reason="Demo sintetico", now=now)
    confirmed = _booking(f"confirmed@{DEMO_DOMAIN}", trip_back, 1, now=now)
    if confirmed.status != BookingStatus.CONFIRMED:
        confirmed.status, confirmed.confirmed_at = BookingStatus.CONFIRMED, now
        confirmed.save(update_fields=["status", "confirmed_at", "updated_at"])
        SeatAssignment.objects.filter(leg__booking=confirmed).update(status="CONFIRMED", updated_at=now)
    CustomerBooking.objects.get_or_create(customer=customer, booking=confirmed)
    manual = _booking(f"cash@{DEMO_DOMAIN}", trip_back, 2, channel=BookingChannel.MANUAL, seller=seller, now=now)
    if manual.status != BookingStatus.CONFIRMED:
        register_cash_payment(booking_or_id=manual, seller=seller, reference="demo-cash", now=now)
    expired = _booking(f"expired@{DEMO_DOMAIN}", trip_out, 5, now=now - timedelta(days=1))
    if expired.status != BookingStatus.EXPIRED:
        expired.status, expired.expires_at = BookingStatus.EXPIRED, now - timedelta(minutes=1)
        expired.save(update_fields=["status", "expires_at", "updated_at"])
    if not Ticket.objects.filter(booking=confirmed, status="ISSUED").exists():
        issue_tickets_for_booking(confirmed, now=now)
    for booking in (confirmed, manual):
        job, _ = TicketFulfillment.objects.get_or_create(booking=booking)
        job.issue_status = FulfillmentIssueStatus.PENDING if booking == confirmed else FulfillmentIssueStatus.FAILED
        job.email_status = FulfillmentEmailStatus.PENDING if booking == confirmed else FulfillmentEmailStatus.FAILED
        job.issue_error = "Demo sintetico" if booking == manual else ""
        job.email_error = "Demo sintetico" if booking == manual else ""
        job.save()
    for booking in (held, round_trip, awaiting, review_booking, rejected_booking, confirmed, manual, expired):
        TransactionalNotification.objects.get_or_create(booking=booking, notification_type="BOOKING_HELD", event_key=f"{DEMO_PREFIX}:{booking.pk}", recipient_email=booking.email, defaults={"payload": {"demo": True}, "status": NotificationStatus.PENDING})
    ticket = Ticket.objects.filter(booking=confirmed, status="ISSUED").first()
    if ticket:
        BoardingRecord.objects.get_or_create(ticket=ticket, defaults={"passenger": ticket.passenger, "trip": ticket.leg.trip, "seat_assignment": ticket.seat_assignment, "operator": admin, "status": BoardingStatus.ACTIVE})
    return {"dry_run": False, "routes": 2, "trips": 2, "bookings": 8, "users": 3, "message": "Escenario demo creado o reutilizado; no se muestran contrasenas ni tokens."}

def reset_demo():
    assert_demo_environment()
    booking_qs = Booking.objects.filter(email__endswith=f"@{DEMO_DOMAIN}")
    user_qs = get_user_model().objects.filter(
        Q(username__startswith=DEMO_PREFIX.lower()) | Q(email__iexact=f"cliente@{DEMO_DOMAIN}")
    )
    route_qs = Route.objects.filter(code__startswith=f"{DEMO_PREFIX}-")
    bus_qs = Bus.objects.filter(code__startswith=f"{DEMO_PREFIX}-")
    result = {"bookings": booking_qs.count(), "users": user_qs.count(), "routes": route_qs.count(), "buses": bus_qs.count()}
    booking_ids = list(booking_qs.values_list("pk", flat=True))
    from customers.models import BookingClaimToken
    from notifications.models import TransactionalNotification
    from panel.models import AuditEvent
    from payments.models import Payment
    from sales.models import BookingLeg, BookingPassenger, SeatAssignment
    from tickets.models import BoardingRecord, Ticket, TicketAuditEvent, TicketEmailAttempt, TicketFulfillment
    BoardingRecord.objects.filter(ticket__booking_id__in=booking_ids).delete()
    TicketAuditEvent.objects.filter(booking_id__in=booking_ids).delete()
    TicketEmailAttempt.objects.filter(booking_id__in=booking_ids).delete()
    Ticket.objects.filter(booking_id__in=booking_ids).delete()
    TicketFulfillment.objects.filter(booking_id__in=booking_ids).delete()
    TransactionalNotification.objects.filter(booking_id__in=booking_ids).delete()
    Payment.objects.filter(booking_id__in=booking_ids).delete()
    CustomerBooking.objects.filter(booking_id__in=booking_ids).delete()
    BookingClaimToken.objects.filter(booking_id__in=booking_ids).delete()
    SeatAssignment.objects.filter(leg__booking_id__in=booking_ids).delete()
    BookingPassenger.objects.filter(booking_id__in=booking_ids).delete()
    BookingLeg.objects.filter(booking_id__in=booking_ids).delete()
    AuditEvent.objects.filter(entity_id__in=[str(item) for item in booking_ids]).delete()
    booking_qs.delete()
    CustomerConsent.objects.filter(customer__normalized_email=f"cliente@{DEMO_DOMAIN}").delete()
    Customer.objects.filter(normalized_email=f"cliente@{DEMO_DOMAIN}").delete()
    user_qs.delete()
    route_ids = list(route_qs.values_list("pk", flat=True))
    trip_ids = list(Trip.objects.filter(route_id__in=route_ids).values_list("pk", flat=True))
    TripFare.objects.filter(trip_id__in=trip_ids).delete()
    from operations.models import TripStop
    TripStop.objects.filter(trip_id__in=trip_ids).delete()
    Trip.objects.filter(pk__in=trip_ids).delete()
    RouteStop.objects.filter(route_id__in=route_ids).delete()
    route_qs.delete()
    bus_ids = list(bus_qs.values_list("pk", flat=True))
    Seat.objects.filter(bus_id__in=bus_ids).delete()
    bus_qs.delete()
    Stop.objects.filter(code__startswith=f"{DEMO_PREFIX}-").delete()
    return {"reset": True, "deleted": result}
