"""Estructura desacoplada de datos para el renderizado del pasaje PDF."""

from dataclasses import dataclass

PENDING_SANDRO_VALUE = "Pendiente de confirmación con Sandro"


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
    arrival_date: str = ""
    route_name: str = ""
    origin_address: str = ""
    destination_address: str = ""
    seller_display: str = ""
    payment_method_display: str = "Sin pago registrado"
    fare_detail_display: str = ""
    total_amount_display: str = ""
    is_lap_infant: bool = False
    companion_name: str = ""
    agency_name: str = "SC VIAJES"
    carrier_name: str = PENDING_SANDRO_VALUE
    carrier_legal_name: str = PENDING_SANDRO_VALUE
    carrier_cuit: str = PENDING_SANDRO_VALUE
    carrier_vat_condition: str = PENDING_SANDRO_VALUE
    carrier_activity_start: str = PENDING_SANDRO_VALUE
    carrier_iibb: str = PENDING_SANDRO_VALUE
    insurer_name: str = PENDING_SANDRO_VALUE
    insurance_policy_ref: str = PENDING_SANDRO_VALUE
    service_quality_display: str = PENDING_SANDRO_VALUE
    platform_announcement: str = PENDING_SANDRO_VALUE
    contact_whatsapp: str = PENDING_SANDRO_VALUE
    conditions_notice: str = "Condiciones generales de transporte sujetas a validación legal de Sandro."
