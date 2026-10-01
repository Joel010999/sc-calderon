import csv

from dataclasses import dataclass

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from operations.models import Trip, TripStop
from sales.models import AssignmentStatus, BookingStatus, SeatAssignment

from .permissions import can_manage_operations, reservations_access


@dataclass(frozen=True)
class ManifestRow:
    passenger_name: str
    document_type: str
    document_number: str
    seat_number: int
    category: str
    category_display: str
    origin_stop: str
    destination_stop: str
    booking_public_id: str
    origin_sequence: int
    destination_sequence: int


@dataclass(frozen=True)
class ManifestStopSummary:
    name: str
    sequence: int
    boarding: int
    alighting: int
    continuing: int


def _manifest_data(trip_pk):
    trip = get_object_or_404(
        Trip.objects.select_related("route", "bus"),
        pk=trip_pk,
    )
    stops = list(
        TripStop.objects.filter(trip_id=trip.pk)
        .select_related("stop")
        .order_by("sequence")
    )
    assignments = (
        SeatAssignment.objects.filter(
            trip_id=trip.pk,
            leg__trip_id=trip.pk,
            status=AssignmentStatus.CONFIRMED,
            leg__booking__status=BookingStatus.CONFIRMED,
        )
        .select_related(
            "passenger",
            "leg__booking",
            "leg__origin_stop__stop",
            "leg__destination_stop__stop",
        )
        .order_by("leg__origin_stop__sequence", "category", "seat_number", "pk")
    )

    rows = [
        ManifestRow(
            passenger_name=" ".join(filter(None, [assignment.passenger.first_name, assignment.passenger.last_name])),
            document_type=assignment.passenger.document_type,
            document_number=assignment.passenger.document_number,
            seat_number=assignment.seat_number,
            category=assignment.category,
            category_display=assignment.get_category_display(),
            origin_stop=assignment.leg.origin_stop.stop.name,
            destination_stop=assignment.leg.destination_stop.stop.name,
            booking_public_id=str(assignment.leg.booking.public_id),
            origin_sequence=assignment.leg.origin_stop.sequence,
            destination_sequence=assignment.leg.destination_stop.sequence,
        )
        for assignment in assignments
    ]

    summaries = [
        ManifestStopSummary(
            name=stop.stop.name,
            sequence=stop.sequence,
            boarding=sum(row.origin_sequence == stop.sequence for row in rows),
            alighting=sum(row.destination_sequence == stop.sequence for row in rows),
            continuing=sum(
                row.origin_sequence < stop.sequence < row.destination_sequence
                for row in rows
            ),
        )
        for stop in stops
    ]
    return trip, rows, summaries


def _csv_safe(value):
    value = "" if value is None else str(value)
    if value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _csv_response(trip, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response.write("\ufeff")
    response["Content-Disposition"] = (
        f'attachment; filename="manifiesto-viaje-{trip.pk}.csv"; '
        f"filename*=UTF-8''manifiesto-viaje-{trip.pk}.csv"
    )
    writer = csv.writer(response, lineterminator="\r\n")
    writer.writerow([
        "Pasajero",
        "Tipo de documento",
        "Número de documento",
        "Butaca",
        "Categoría",
        "Parada de subida",
        "Parada de bajada",
        "Referencia pública de reserva",
    ])
    for row in rows:
        writer.writerow([
            _csv_safe(row.passenger_name),
            _csv_safe(row.document_type),
            _csv_safe(row.document_number),
            _csv_safe(row.seat_number),
            _csv_safe(row.category_display),
            _csv_safe(row.origin_stop),
            _csv_safe(row.destination_stop),
            _csv_safe(row.booking_public_id),
        ])
    return response


@reservations_access()
@require_GET
def trip_manifest(request, trip_pk, print_mode=False):
    trip, rows, summaries = _manifest_data(trip_pk)
    return render(request, "panel/manifest.html", {
        "title": f"Manifiesto de pasajeros · Viaje {trip.pk}",
        "can_manage": can_manage_operations(request.user),
        "trip": trip,
        "rows": rows,
        "summaries": summaries,
        "print_mode": print_mode,
        "display_timezone": settings.TIME_ZONE,
    })


@reservations_access()
@require_GET
def trip_manifest_print(request, trip_pk):
    return trip_manifest(request, trip_pk, print_mode=True)


@reservations_access()
@require_GET
def trip_manifest_csv(request, trip_pk):
    trip, rows, _ = _manifest_data(trip_pk)
    return _csv_response(trip, rows)
