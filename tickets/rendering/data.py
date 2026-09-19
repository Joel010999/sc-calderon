"""Estructura desacoplada de datos para el renderizado del pasaje PDF."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TicketData:
    ticket_code: str
    booking_code: str
    passenger_name: str
    masked_document: str
    origin_stop_name: str
    destination_stop_name: str
    travel_date: str
    departure_time: str
    boarding_place: str
    arrival_time: str
    seat_number: str
    category_display: str
    price_display: str
    issued_at: str
    qr_payload_url: str
    provisional_notice: str = "Plantilla provisional reemplazable sin cambiar dominio"
    id_requirement_notice: str = "Presentarse con documento de identidad para abordar"
