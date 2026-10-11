"""Prepara datos privados y efímeros para la suite Browser E2E.

El token QR se escribe únicamente en E2E_SECRET_FILE, fuera del repositorio.
Nunca se imprime ni se persiste en modelos, logs o artefactos de Playwright.
"""

import json
import os
import sys
from uuid import uuid4
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sslviajes.settings")

import django

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.auth.models import Group  # noqa: E402
from django.utils import timezone  # noqa: E402

from core.demo_scenario import DEMO_DOMAIN, DEMO_PREFIX, seed_demo  # noqa: E402
from operations.models import Trip  # noqa: E402
from payments.services import register_cash_payment  # noqa: E402
from sales.models import BookingStatus  # noqa: E402
from sales.services import create_manual_booking  # noqa: E402
from tickets.services import issue_tickets_for_booking  # noqa: E402
from tickets.storage import get_ticket_storage  # noqa: E402
from tickets.models import Ticket  # noqa: E402


CASH_SELLER_USERNAME = f"{DEMO_PREFIX.lower()}-cash-vendedor"
CASH_SELLER_EMAIL = f"cash-vendedor@{DEMO_DOMAIN}"


def _cash_seller():
    User = get_user_model()
    group = Group.objects.get(name="Vendedor")
    user, created = User.objects.get_or_create(
        username=CASH_SELLER_USERNAME,
        defaults={"email": CASH_SELLER_EMAIL, "is_active": True, "is_staff": False},
    )
    if created:
        user.set_password(os.environ.get("DEMO_CASH_SELLER_PASSWORD", "e2e-only-cash-seller-password-20261010"))
        user.save(update_fields=["password"])
    user.groups.add(group)
    return user


def _boarding_ticket(seller):
    booking_email = f"boarding-e2e-{uuid4().hex[:12]}@{DEMO_DOMAIN}"
    trip = (
        Trip.objects.filter(status=Trip.Status.SCHEDULED)
        .exclude(boarding_records__isnull=False)
        .order_by("departure_at", "pk")
        .first()
    )
    if trip is None:
        raise RuntimeError("No hay un viaje sintético SCHEDULED disponible para E2E.")
    origin = trip.trip_stops.filter(allows_boarding=True).order_by("sequence").first()
    destination = trip.trip_stops.filter(allows_alighting=True).order_by("-sequence").first()
    seat = trip.bus.seats.filter(is_active=True).exclude(seat_assignments__status__in=["HELD", "CONFIRMED"]).order_by("number").first()
    if not origin or not destination or not seat:
        raise RuntimeError("No hay tramo o butaca sintética disponible para E2E.")
    booking = create_manual_booking(
        email=booking_email,
        seller=seller,
        legs=[{"trip": trip, "origin_stop": origin, "destination_stop": destination, "seats": [seat]}],
        passengers_data=[{
            "first_name": "Boarding",
            "last_name": "E2E",
            "document_type": "DNI",
            "document_number": "99000101",
            "birth_date": "1990-01-01",
            "nationality": "Argentina",
        }],
        now=timezone.now(),
    )
    register_cash_payment(booking_or_id=booking, seller=seller, reference="browser-e2e")
    booking.refresh_from_db()
    if booking.status != BookingStatus.CONFIRMED:
        raise RuntimeError("El servicio de pago sintético no confirmó la reserva E2E.")
    # El pago confirma y procesa fulfillment mediante on_commit. Para conservar
    # el token efímero del emisor en este escenario aislado, se reemplaza ese
    # pasaje ya emitido por una emisión inmediata controlada.
    existing = list(Ticket.objects.filter(booking=booking))
    storage = get_ticket_storage()
    for item in existing:
        storage.delete(item.pdf_path)
        item.delete()
    ticket = issue_tickets_for_booking(booking, now=timezone.now())[0]
    return ticket, ticket._raw_verification_token


def main():
    seed_demo()
    User = get_user_model()
    seller = User.objects.get(username=f"{DEMO_PREFIX.lower()}-vendedor")
    cash_seller = _cash_seller()
    ticket, raw_token = _boarding_ticket(seller)
    qr_ticket, qr_raw_token = _boarding_ticket(seller)
    if not raw_token:
        raise RuntimeError("El ticket E2E existente no conserva el token efímero en memoria; use una base limpia.")
    output_name = os.environ.get("E2E_SECRET_FILE", "").strip()
    if not output_name:
        raise RuntimeError("E2E_SECRET_FILE es obligatorio y debe apuntar fuera del repositorio.")
    output = Path(output_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "boarding_ticket_code": ticket.ticket_code,
        "boarding_trip_id": ticket.leg.trip_id,
        "boarding_verification_url": f"/tickets/verify/?token={raw_token}",
        "qr_ticket_code": qr_ticket.ticket_code,
        "qr_trip_id": qr_ticket.leg.trip_id,
        "qr_verification_url": f"/tickets/verify/?token={qr_raw_token}",
        "cash_seller_username": cash_seller.username,
    }), encoding="utf-8")
    print("Escenario Browser E2E privado preparado.")


if __name__ == "__main__":
    main()
