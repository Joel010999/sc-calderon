"""Renderizado legible y estable del pasaje electrónico de SC Viajes."""

import io
from pathlib import Path
from xml.sax.saxutils import escape

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from .data import PENDING_SANDRO_VALUE, TicketData

LOGO_ASSET_REL_PATH = Path("static/assets/scviajes-logo.png")


def _register_fonts() -> tuple[str, str]:
    """Usa Vera, distribuida con ReportLab, para embebido Unicode estable."""
    try:
        import reportlab

        font_dir = Path(reportlab.__file__).resolve().parent / "fonts"
        pdfmetrics.registerFont(TTFont("SCVera", str(font_dir / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("SCVera-Bold", str(font_dir / "VeraBd.ttf")))
        pdfmetrics.registerFontFamily("SCVera", normal="SCVera", bold="SCVera-Bold")
        return "SCVera", "SCVera-Bold"
    except Exception:
        return "Helvetica", "Helvetica-Bold"


FONT_REGULAR, FONT_BOLD = _register_fonts()

BODY = ParagraphStyle("TicketBody", fontName=FONT_REGULAR, fontSize=8.2, leading=10.4, textColor=colors.HexColor("#0f172a"))
SMALL = ParagraphStyle("TicketSmall", parent=BODY, fontSize=7.2, leading=9.0, textColor=colors.HexColor("#334155"))
CONDITION = ParagraphStyle("TicketCondition", parent=BODY, fontSize=8.4, leading=11.3, spaceAfter=7)

# Texto de condiciones del Word editado. Las preguntas, notas y datos a
# completar no se imprimen. La leyenda de validación evita presentar normas
# como verificadas por el sistema.
PASSENGER_CONDITIONS = (
    ("VALIDEZ", "El pasaje es válido solo para el día, hora y servicios seleccionados. Se aceptarán devoluciones conforme a las deducciones y retenciones que se indican según la empresa transportista."),
    ("DEVOLUCIONES EN SERVICIOS NACIONALES", "Retenciones del texto de referencia: a) desde las 24 horas anteriores y hasta una hora antes de la salida del servicio: 30%; b) desde las 48 horas y hasta las 24 horas anteriores a la salida del servicio: 20%; c) más de 48 horas antes de la salida del servicio: 10%."),
    ("DEVOLUCIONES EN SERVICIOS PROVINCIALES", "a) antes de las 24 horas: 20%; b) dentro de las 24 horas de iniciación del servicio, no hay reintegros."),
    ("REAJUSTE Y CONSERVACIÓN", "Texto de referencia: pasaje sujeto a reajuste al momento del viaje. El presente pasaje deberá conservarlo el pasajero y será intransferible."),
    ("EQUIPAJE", "Este pasaje da derecho a transportar hasta 15 kilos de equipaje sin cargo. La empresa no se responsabiliza por los equipajes transportados y no despachados."),
    ("MENORES Y ACOMPAÑAMIENTO", "Los menores de DOCE (12) años, inclusive, no podrán viajar solos; podrán viajar acompañados por al menos UN (1) representante legal o por un tercero autorizado por al menos UN (1) representante legal. Los menores de entre TRECE (13) y DIECISIETE (17) años podrán viajar solos si contaren con autorización legal. Todos los pasajeros menores de DIECIOCHO (18) años deberán cumplir la documentación aplicable para viajar."),
    ("NIÑOS SIN ASIENTO", "Los pasajeros que no hayan cumplido los 5 años de edad no pagarán pasaje, pero no tendrán derecho a asiento. Únicamente podrá viajar un menor sin asiento por cada mayor que lo acompañe."),
    ("SEGURO", "La cobertura, el importe y las condiciones del seguro de responsabilidad obligatorio quedan pendientes de confirmación comercial. Esta versión no calcula ni cobra un seguro adicional."),
    ("CANJE", "No se admitirá el canje de pasajes por cambio de fecha u hora de viaje; solo se aceptará la devolución en las condiciones establecidas."),
    ("EXTRAVÍO", "La pérdida o extravío del pasaje no genera derecho a reclamación, devolución o reintegro alguno."),
    ("DOCUMENTACIÓN", "En los servicios internacionales, el pasajero deberá satisfacer las exigencias gubernamentales en materia de documentos personales exigidos para viajar, tanto en la salida como en la entrada de ambos países."),
    ("NORMAS VIGENTES", "La compra del pasaje y su utilización implican la aceptación de las condiciones aplicables y de las normas vigentes en materia de transporte de pasajeros."),
    ("RESPONSABILIDAD", "La responsabilidad frente al pasajero corresponde a la transportista que efectúa el traslado, conforme a las condiciones que se confirmen."),
    ("AVISOS", "Prohibido fumar. La explotación sexual de niños, niñas y adolescentes y la trata de personas en la Argentina es un delito severamente penado."),
)


def _logo_path() -> Path:
    try:
        from django.conf import settings

        base_dir = Path(settings.BASE_DIR)
    except Exception:
        base_dir = Path(__file__).resolve().parents[2]
    return base_dir / LOGO_ASSET_REL_PATH


def _resolve_seat_label(data: TicketData) -> str:
    raw = str(data.seat_number or "").strip()
    if data.is_lap_infant or not raw or raw.lower() == "none" or raw == "Sin butaca":
        return "Sin butaca"
    return raw


def _value(value: str, fallback: str = PENDING_SANDRO_VALUE) -> str:
    clean = str(value or "").strip()
    return clean or fallback


def _draw_paragraph(c: canvas.Canvas, text: str, x: float, y: float, width: float, style=BODY) -> float:
    paragraph = Paragraph(text, style)
    _, height = paragraph.wrap(width, 1000)
    paragraph.drawOn(c, x, y - height)
    return height


def _draw_field(c: canvas.Canvas, y: float, label: str, value: str, width: float, style=BODY) -> float:
    content = f"<b>{escape(label)}</b> {escape(_value(value))}"
    return y - _draw_paragraph(c, content, 48, y, width, style) - 3


def _draw_logo(c: canvas.Canvas, x: float, y: float, size: float) -> None:
    path = _logo_path()
    if path.is_file():
        c.drawImage(ImageReader(str(path)), x, y, width=size, height=size, preserveAspectRatio=True, anchor="c", mask="auto")


def _draw_header(c: canvas.Canvas, width: float, height: float, title: str) -> None:
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(36, height - 74, width - 72, 42, fill=1, stroke=0)
    _draw_logo(c, 45, height - 68, 30)
    c.setFillColor(colors.white)
    c.setFont(FONT_BOLD, 16)
    c.drawString(82, height - 58, "SC VIAJES")
    c.setFont(FONT_BOLD, 10)
    c.drawRightString(width - 48, height - 58, title)


def _draw_box_title(c: canvas.Canvas, top: float, width: float, title: str, right: str = "") -> None:
    c.setFillColor(colors.HexColor("#f1f5f9"))
    c.rect(37, top - 24, width - 74, 23, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont(FONT_BOLD, 9.5)
    c.drawString(48, top - 16, title)
    if right:
        c.setFont(FONT_REGULAR, 8)
        c.drawRightString(width - 48, top - 16, right)


def _draw_qr(c: canvas.Canvas, data: TicketData, x: float, y: float, size: float) -> None:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=1)
    qr.add_data(data.qr_payload_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    pil_img = qr_img._img if hasattr(qr_img, "_img") else qr_img
    pil_img.save(buffer, format="PNG")
    buffer.seek(0)
    c.drawImage(ImageReader(buffer), x, y, width=size, height=size)
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont(FONT_REGULAR, 7.3)
    c.drawCentredString(x + size / 2, y - 11, "Escanear para verificar validez")


def _draw_conditions_page(c: canvas.Canvas, data: TicketData, width: float, height: float) -> None:
    c.showPage()
    _draw_header(c, width, height, "CONDICIONES DEL PASAJE")
    y = height - 96
    c.setFillColor(colors.HexColor("#7f1d1d"))
    y -= _draw_paragraph(c, "<b>Texto pendiente de validación comercial y legal.</b> Las referencias normativas del material de trabajo no fueron verificadas por el sistema.", 48, y, width - 96, SMALL) + 10
    for heading, body in PASSENGER_CONDITIONS:
        if y < 74:
            c.showPage()
            _draw_header(c, width, height, "CONDICIONES DEL PASAJE")
            y = height - 96
        c.setFillColor(colors.HexColor("#0f172a"))
        y -= _draw_paragraph(c, f"<b>{escape(heading)}</b>", 48, y, width - 96, BODY) + 2
        y -= _draw_paragraph(c, escape(body), 48, y, width - 96, CONDITION)
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(48, 42, f"Pasaje {data.ticket_code} - conservar junto con el talón del pasajero")


def draw_provisional_ticket(c: canvas.Canvas, data: TicketData) -> None:
    """Dibuja dos talones legibles y las condiciones completas en páginas extra."""
    width, height = A4
    arrival_date = _value(data.arrival_date or data.travel_date)
    route_name = _value(data.route_name or f"{data.origin_stop_name} - {data.destination_stop_name}")
    origin_addr = _value(data.origin_address, f"Dirección pendiente ({PENDING_SANDRO_VALUE})")
    destination_addr = _value(data.destination_address, f"Dirección pendiente ({PENDING_SANDRO_VALUE})")
    seat_label = _resolve_seat_label(data)
    seller = data.seller_display.strip()
    companion = data.companion_name.strip()
    booking_short = str(data.booking_code)[:8].upper()
    payment = "Pasaje gratuito — sin butaca" if data.is_lap_infant else _value(data.payment_method_display)
    price = _value(data.price_display, "$ 0,00")
    total = _value(data.total_amount_display, price)
    fare = _value(data.fare_detail_display, f"Tarifa {price}")

    _draw_header(c, width, height, "PASAJE ELECTRÓNICO")

    top = height - 82
    bottom = height - 372
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.rect(36, bottom, width - 72, top - bottom, fill=0, stroke=1)
    _draw_box_title(c, top, width, "TALÓN SUPERIOR", f"Reserva: {booking_short} | Emisión: {data.issued_at}")
    y = top - 40
    y = _draw_field(c, y, "Agencia:", data.agency_name, width - 96)
    y = _draw_field(c, y, "Empresa transportista:", data.carrier_name, width - 96)
    y = _draw_field(c, y, "Pasajero/a:", data.passenger_name, width - 96)
    y = _draw_field(c, y, "Documento:", data.masked_document, width - 96)
    y = _draw_field(c, y, "Boleto / Identificador:", data.ticket_code, width - 96)
    if seller:
        y = _draw_field(c, y, "Vendedor:", seller, width - 96)
    y = _draw_field(c, y, "Categoría / Butaca:", f"{data.category_display} - Butaca: {seat_label}", width - 96)
    if companion:
        y = _draw_field(c, y, "Adulto acompañante:", companion, width - 96)
    y = _draw_field(c, y, "Sale de / Sube en:", f"{data.origin_stop_name} - {origin_addr}", width - 96, SMALL)
    y = _draw_field(c, y, "A / Baja en:", f"{data.destination_stop_name} - {destination_addr}", width - 96, SMALL)
    y = _draw_field(c, y, "Salida:", f"{data.travel_date} - {data.departure_time}", width - 96)
    y = _draw_field(c, y, "Arribo estimado:", f"{arrival_date} - {data.arrival_time}", width - 96)
    y = _draw_field(c, y, "Plataforma / Aviso:", data.platform_announcement, width - 96, SMALL)
    _draw_field(c, y, "Importe total:", total, width - 96)

    cut_y = height - 386
    c.saveState()
    c.setDash(4, 4)
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.line(36, cut_y, width - 36, cut_y)
    c.restoreState()

    pax_top = height - 398
    pax_bottom = 36
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.rect(36, pax_bottom, width - 72, pax_top - pax_bottom, fill=0, stroke=1)
    _draw_box_title(c, pax_top, width, "TALÓN PARA EL PASAJERO", f"Boleto: {data.ticket_code}")
    left_width = 326
    py = pax_top - 40
    py = _draw_field(c, py, "Agencia:", data.agency_name, left_width)
    py = _draw_field(c, py, "Empresa transportista:", data.carrier_name, left_width, SMALL)
    py = _draw_field(c, py, "Razón social:", data.carrier_legal_name, left_width, SMALL)
    py = _draw_field(c, py, "CUIT:", data.carrier_cuit, left_width, SMALL)
    py = _draw_field(c, py, "IVA:", data.carrier_vat_condition, left_width, SMALL)
    py = _draw_field(c, py, "Inicio de actividades:", data.carrier_activity_start, left_width, SMALL)
    py = _draw_field(c, py, "IIBB:", data.carrier_iibb, left_width, SMALL)
    py = _draw_field(c, py, "Usted viaja asegurado por:", data.insurer_name, left_width, SMALL)
    py = _draw_field(c, py, "Póliza/Ref.:", data.insurance_policy_ref, left_width, SMALL)
    py = _draw_field(c, py, "Pasajero/a:", data.passenger_name, left_width)
    py = _draw_field(c, py, "Documento:", data.masked_document, left_width)
    if seller:
        py = _draw_field(c, py, "Vendedor:", seller, left_width, SMALL)
    if companion:
        py = _draw_field(c, py, "Adulto acompañante:", companion, left_width, SMALL)
    py = _draw_field(c, py, "Recorrido:", route_name, left_width, SMALL)
    py = _draw_field(c, py, "Categoría / Butaca:", f"{data.category_display} - Butaca: {seat_label}", left_width, SMALL)
    py = _draw_field(c, py, "Sale de / Sube en:", f"{data.origin_stop_name} - {origin_addr}", left_width, SMALL)
    py = _draw_field(c, py, "A / Baja en:", f"{data.destination_stop_name} - {destination_addr}", left_width, SMALL)
    py = _draw_field(c, py, "Salida:", f"{data.travel_date} - {data.departure_time}", left_width, SMALL)
    py = _draw_field(c, py, "Arribo estimado:", f"{arrival_date} - {data.arrival_time}", left_width, SMALL)
    py = _draw_field(c, py, "Formas de pago:", payment, left_width, SMALL)
    py = _draw_field(c, py, "Detalle:", fare, left_width, SMALL)
    _draw_field(c, py, "Importe total:", total, left_width)

    _draw_qr(c, data, width - 174, pax_bottom + 94, 116)
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(37, pax_bottom + 1, width - 74, 52, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(37, pax_bottom + 53, width - 37, pax_bottom + 53)
    c.setFillColor(colors.HexColor("#b91c1c"))
    c.setFont(FONT_BOLD, 8)
    c.drawString(48, pax_bottom + 38, _value(data.id_requirement_notice))
    c.setFillColor(colors.HexColor("#475569"))
    c.setFont(FONT_REGULAR, 7.4)
    c.drawString(48, pax_bottom + 24, "Las condiciones generales y devoluciones se encuentran en la página siguiente.")
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(48, pax_bottom + 10, "Transportista, datos fiscales, seguro y contacto: pendientes de confirmación.")

    _draw_conditions_page(c, data, width, height)
