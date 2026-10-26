"""Plantilla visual provisional reutilizable (dos talones) para pasajes de SC Viajes."""

import io
from pathlib import Path

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .data import PENDING_SANDRO_VALUE, TicketData

# Ruta canónica del logo institucional incorporado al proyecto desde las referencias de Sandro.
LOGO_ASSET_REL_PATH = Path("static/assets/scviajes-logo.png")


def _truncate(text: str, max_len: int) -> str:
    clean = (text or "").strip()
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"


def _resolve_seat_label(data: TicketData) -> str:
    raw = str(data.seat_number or "").strip()
    if data.is_lap_infant or not raw or raw.lower() == "none" or raw == "Sin butaca":
        return "Sin butaca"
    return raw


def draw_provisional_ticket(c: canvas.Canvas, data: TicketData) -> None:
    """Dibuja el borrador reutilizable del pasaje PDF con dos talones en una hoja A4.

    Incluye:
    - Talón superior (control / agencia).
    - Talón para el pasajero (con código QR propio de verificación).
    - Separación explícita entre Agencia (SC VIAJES) y Empresa transportista.
    - Vendedor únicamente cuando fue venta manual (omitido en ventas web).
    """
    width, height = A4

    arrival_date = (data.arrival_date or data.travel_date).strip()
    route_name = (data.route_name or f"{data.origin_stop_name} - {data.destination_stop_name}").strip()
    origin_addr = (data.origin_address or f"Dirección pendiente ({PENDING_SANDRO_VALUE})").strip()
    dest_addr = (data.destination_address or f"Dirección pendiente ({PENDING_SANDRO_VALUE})").strip()
    seat_label = _resolve_seat_label(data)
    fare_detail = (data.fare_detail_display or f"Tarifa {data.price_display}").strip()
    total_amount = (data.total_amount_display or data.price_display).strip()
    seller_clean = (data.seller_display or "").strip()
    short_booking = str(data.booking_code)[:8].upper()

    # =========================================================================
    # 1. CABECERA INSTITUCIONAL
    # =========================================================================
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(36, height - 74, width - 72, 42, fill=1, stroke=0)

    # Emblema vectorial institucional SC
    c.setFillColor(colors.HexColor("#38bdf8"))
    c.circle(54, height - 53, 11, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(54, height - 56, "SC")

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 58, data.agency_name)
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(width - 48, height - 58, "PASAJE ELECTRÓNICO")

    # =========================================================================
    # 2. TALÓN SUPERIOR (CONTROL / AGENCIA)
    # =========================================================================
    top_stub_top = height - 82
    top_stub_bottom = height - 372
    top_stub_height = top_stub_top - top_stub_bottom

    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setLineWidth(1)
    c.rect(36, top_stub_bottom, width - 72, top_stub_height, fill=0, stroke=1)

    c.setFillColor(colors.HexColor("#f1f5f9"))
    c.rect(37, top_stub_top - 24, width - 74, 23, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(48, top_stub_top - 16, "TALÓN SUPERIOR")
    c.setFont("Helvetica", 8.5)
    c.drawRightString(
        width - 48,
        top_stub_top - 16,
        f"Reserva: {short_booking}  |  Emisión: {data.issued_at}",
    )

    y = top_stub_top - 40
    line_step = 16

    # Agencia y Empresa transportista separadas
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, y, f"Agencia: {data.agency_name}")
    c.drawString(245, y, f"Empresa transportista: {_truncate(data.carrier_name, 46)}")
    y -= line_step

    # Pasajero y Documento enmascarado
    c.setFont("Helvetica-Bold", 9)
    c.drawString(48, y, f"Pasajero/a: {_truncate(data.passenger_name, 36)}")
    c.drawString(320, y, f"Documento: {data.masked_document}")
    y -= line_step

    # Boleto / Identificador y Vendedor (solo si fue venta manual)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, y, f"Boleto / Identificador: {data.ticket_code}")
    if seller_clean:
        c.drawString(320, y, f"Vendedor: {_truncate(seller_clean, 30)}")
    y -= line_step

    # Calidad / Servicio y Categoría / Butaca
    c.setFont("Helvetica", 8.5)
    c.drawString(48, y, f"Calidad / Servicio: {_truncate(data.service_quality_display, 34)}")
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(
        300,
        y,
        f"Categoría / Butaca: {data.category_display} - Butaca: {seat_label}",
    )
    y -= line_step

    if data.companion_name:
        c.setFont("Helvetica", 8)
        c.drawString(48, y, f"Adulto acompañante: {_truncate(data.companion_name, 50)}")
        y -= line_step

    # Recorrido, origen y destino con direcciones conocidas/pendientes
    c.setFont("Helvetica", 8.5)
    c.drawString(
        48,
        y,
        f"Sale de / Sube en: {_truncate(data.origin_stop_name, 28)} - {_truncate(origin_addr, 48)}",
    )
    y -= line_step
    c.drawString(
        48,
        y,
        f"A / Baja en: {_truncate(data.destination_stop_name, 28)} - {_truncate(dest_addr, 52)}",
    )
    y -= line_step

    # Fechas y horarios de salida (embarque) y arribo estimado (destino)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, y, f"Salida: {data.travel_date} - {data.departure_time}")
    c.drawString(280, y, f"Arribo estimado: {arrival_date} - {data.arrival_time}")
    y -= line_step

    # Plataforma / Aviso y Formas de pago
    c.setFont("Helvetica", 8.5)
    c.drawString(48, y, f"Plataforma / Aviso: {_truncate(data.platform_announcement, 34)}")
    c.drawString(300, y, f"Formas de pago: {_truncate(data.payment_method_display, 30)}")
    y -= line_step

    # Detalle de tarifa e importe total real en ARS
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, y, f"Detalle: {_truncate(fare_detail, 42)}")
    c.drawString(300, y, f"Importe total: {total_amount}")

    # =========================================================================
    # 3. LÍNEA DE CORTE ENTRE TALONES
    # =========================================================================
    cut_y = height - 386
    c.saveState()
    c.setDash(4, 4)
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.line(36, cut_y, width - 36, cut_y)
    c.restoreState()

    # =========================================================================
    # 4. TALÓN PARA EL PASAJERO
    # =========================================================================
    pax_stub_top = height - 398
    pax_stub_bottom = 36
    pax_stub_height = pax_stub_top - pax_stub_bottom

    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setLineWidth(1)
    c.rect(36, pax_stub_bottom, width - 72, pax_stub_height, fill=0, stroke=1)

    c.setFillColor(colors.HexColor("#f1f5f9"))
    c.rect(37, pax_stub_top - 24, width - 74, 23, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(48, pax_stub_top - 16, "TALÓN PARA EL PASAJERO")
    c.setFont("Helvetica-Bold", 8.5)
    c.drawRightString(width - 48, pax_stub_top - 16, f"Boleto / Identificador: {data.ticket_code}")

    py = pax_stub_top - 40
    p_step = 15

    # Agencia, WhatsApp y Empresa transportista
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, py, f"Agencia: {data.agency_name}")
    c.drawString(230, py, f"Contacto WhatsApp: {_truncate(data.contact_whatsapp, 40)}")
    py -= p_step

    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, py, f"Empresa transportista: {_truncate(data.carrier_name, 62)}")
    py -= p_step

    # Datos fiscales de la transportista (configuración fija pendiente de Sandro)
    c.setFillColor(colors.HexColor("#334155"))
    c.setFont("Helvetica", 7.8)
    c.drawString(
        48,
        py,
        f"Datos fiscales de la transportista: Razón social: {_truncate(data.carrier_legal_name, 28)} | CUIT: {_truncate(data.carrier_cuit, 28)}",
    )
    py -= p_step
    c.drawString(
        48,
        py,
        f"IVA: {_truncate(data.carrier_vat_condition, 28)} | Inicio de actividades: {_truncate(data.carrier_activity_start, 28)} | IIBB: {_truncate(data.carrier_iibb, 28)}",
    )
    py -= p_step
    c.drawString(
        48,
        py,
        f"Usted viaja asegurado por: {_truncate(data.insurer_name, 30)} | Póliza/Ref.: {_truncate(data.insurance_policy_ref, 28)}",
    )
    py -= 10

    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(48, py, width - 48, py)
    py -= 14

    # Columna izquierda de datos del viaje y pasajero (dejando espacio a la derecha para el QR)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(48, py, f"Pasajero/a: {_truncate(data.passenger_name, 34)}")
    py -= p_step

    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, py, f"Documento: {data.masked_document}")
    if seller_clean:
        c.drawString(210, py, f"Vendedor: {_truncate(seller_clean, 24)}")
    py -= p_step

    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, py, f"Recorrido: {_truncate(route_name, 42)}")
    py -= p_step

    c.drawString(
        48,
        py,
        f"Categoría / Butaca: {data.category_display} - Butaca: {seat_label}",
    )
    py -= p_step

    c.setFont("Helvetica", 8.2)
    c.drawString(
        48,
        py,
        f"Sale de / Sube en: {_truncate(data.origin_stop_name, 22)} - {_truncate(origin_addr, 34)}",
    )
    py -= p_step
    c.drawString(
        48,
        py,
        f"A / Baja en: {_truncate(data.destination_stop_name, 22)} - {_truncate(dest_addr, 36)}",
    )
    py -= p_step

    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(48, py, f"Salida: {data.travel_date} - {data.departure_time}")
    py -= p_step
    c.drawString(48, py, f"Arribo estimado: {arrival_date} - {data.arrival_time}")
    py -= p_step

    c.setFont("Helvetica", 8.5)
    c.drawString(48, py, f"Formas de pago: {_truncate(data.payment_method_display, 32)}")
    py -= p_step

    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(48, py, f"Importe total: {total_amount}")

    # Código QR propio de verificación del sistema SC Viajes (única imagen embebida)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=1,
    )
    qr.add_data(data.qr_payload_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    img_buffer = io.BytesIO()
    pil_img = qr_img._img if hasattr(qr_img, "_img") else qr_img
    pil_img.save(img_buffer, format="PNG")
    img_buffer.seek(0)

    qr_x = width - 185
    qr_y = pax_stub_bottom + 82
    qr_size = 128
    c.drawImage(ImageReader(img_buffer), qr_x, qr_y, width=qr_size, height=qr_size)

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 7.8)
    c.drawCentredString(qr_x + (qr_size / 2), qr_y - 11, "Escanear para verificar validez")

    # Pie de avisos y condiciones provisionales
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(37, pax_stub_bottom + 1, width - 74, 52, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(37, pax_stub_bottom + 53, width - 37, pax_stub_bottom + 53)

    c.setFillColor(colors.HexColor("#b91c1c"))
    c.setFont("Helvetica-Bold", 8.2)
    c.drawString(48, pax_stub_bottom + 38, f"IMPORTANTE: {data.id_requirement_notice}")

    c.setFillColor(colors.HexColor("#475569"))
    c.setFont("Helvetica", 7.5)
    c.drawString(48, pax_stub_bottom + 24, f"CONDICIONES: {data.conditions_notice}")

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawString(48, pax_stub_bottom + 10, f"AVISO: {data.provisional_notice}")

    c.showPage()
