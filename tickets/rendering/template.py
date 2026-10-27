"""Renderizado fiel de la plantilla oficial aprobada del pasaje de SC Viajes."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from .data import PENDING_SANDRO_VALUE, TicketData

PAGESIZE = (596.0, 842.0)
LOGO_ASSET_REL_PATH = Path("static/assets/scviajes-logo.png")


def _register_fonts() -> tuple[str, str]:
    """Usa Vera si está disponible para soporte tipográfico completo, o Helvetica estándar."""
    try:
        import reportlab

        font_dir = Path(reportlab.__file__).resolve().parent / "fonts"
        vera_regular = font_dir / "Vera.ttf"
        vera_bold = font_dir / "VeraBd.ttf"
        if vera_regular.is_file() and vera_bold.is_file():
            pdfmetrics.registerFont(TTFont("SCVera", str(vera_regular)))
            pdfmetrics.registerFont(TTFont("SCVera-Bold", str(vera_bold)))
            pdfmetrics.registerFontFamily("SCVera", normal="SCVera", bold="SCVera-Bold")
            return "SCVera", "SCVera-Bold"
    except Exception:
        pass
    return "Helvetica", "Helvetica-Bold"


FONT_REGULAR, FONT_BOLD = _register_fonts()

CONDITIONS_LEFT = (
    ("VALIDEZ: ", "El pasaje es válido solo para el día, hora y servicios seleccionados. Se aceptarán devoluciones conforme a las deducciones y retenciones que se indican según la empresa transportista."),
    ("DEVOLUCIONES EN SERVICIOS NACIONALES: ", "Retenciones del ejemplo (Res. Nro. 1317/53 del Ministerio de Transporte de la Nación): a) Desde las 24 horas anteriores y hasta una hora antes de la salida del servicio: 30%. b) Desde las 48 horas y hasta las 24 horas anteriores a la salida del servicio: 20%. c) Más de 48 horas antes de la salida del servicio: 10%."),
    ("DEVOLUCIONES EN SERVICIOS PROVINCIALES: ", "a) Antes de las 24 horas: 20%. b) Dentro de las 24 horas de iniciación del servicio, no hay reintegros."),
    ("REAJUSTE Y CONSERVACIÓN: ", "Resolución SETOP 03/90: pasaje sujeto a reajuste al momento del viaje. El presente pasaje deberá conservarlo el pasajero y será intransferible."),
    ("EQUIPAJE: ", "Este pasaje da derecho a transportar hasta 15 kilos de equipaje sin cargo (Res. 47/95 de la ST, modificada por Res. 212/2002). La empresa no se responsabiliza por los equipajes transportados y no despachados."),
    ("MENORES Y ACOMPAÑAMIENTO: ", "Los menores de DOCE (12) años, inclusive, no podrán viajar solos; podrán viajar acompañados por al menos UN (1) representante legal o por un tercero autorizado por al menos UN (1) representante legal. Los menores de entre TRECE (13) y DIECISIETE (17) años podrán viajar solos si contaren con autorización legal. Todos los pasajeros menores de DIECIOCHO (18) años, viajen solos o no, deberán cumplimentar en un todo con la Resolución 43 E/2016. Caso contrario, no podrán viajar, perdiendo todo derecho a reclamo."),
)

CONDITIONS_RIGHT = (
    ("NIÑOS SIN ASIENTO: ", "Los pasajeros que no hayan cumplido los 5 años de edad no pagarán pasaje, pero no tendrán derecho a asiento. Únicamente podrá viajar un menor con seguro por cada mayor que lo acompañe. Salvo servicios provinciales en Mendoza, es hasta los 3 años."),
    ("SEGURO: ", "Este pasaje incluye un seguro de responsabilidad hacia pasajeros transportados de acuerdo a disposiciones legales vigentes. El seguro no es canjeable ni puede ser devuelto. Los niños menores de 5 años deben abonar el seguro de responsabilidad obligatorio."),
    ("CANJE: ", "No se admitirá en ningún caso el canje de pasajes por cambio de fecha y/u hora de viaje; solo se aceptará la devolución en las condiciones establecidas precedentemente."),
    ("EXTRAVÍO: ", "La pérdida o extravío del pasaje no genera derecho a reclamación, devolución o reintegro alguno."),
    ("DOCUMENTACIÓN: ", "En los servicios internacionales, el pasajero deberá satisfacer las exigencias gubernamentales en materia de documentos personales exigidos para viajar, tanto en la salida como en la entrada de ambos países. La empresa no se responsabiliza por los gastos o inconvenientes ocasionados por el incumplimiento por parte del pasajero."),
    ("NORMAS VIGENTES: ", "La compra del pasaje y su utilización implican la aceptación de todas las condiciones antes expuestas y de las normas vigentes en materia de transporte de pasajeros."),
    ("RESPONSABILIDAD: ", "El único responsable frente al pasajero es la transportista que efectúa el traslado, careciendo quien expide el pasaje de toda responsabilidad frente al mismo."),
    ("AVISOS: ", "Prohibido fumar. Ley 10.600. La explotación sexual de niños, niñas y adolescentes, y la trata de personas en la Argentina es un delito severamente penado. Denuncialo al 145. Línea GRATUITA, ANÓNIMA Y NACIONAL."),
)


def _logo_path() -> Path:
    try:
        from django.conf import settings

        base_dir = Path(settings.BASE_DIR)
    except Exception:
        base_dir = Path(__file__).resolve().parents[2]
    return base_dir / LOGO_ASSET_REL_PATH


def _draw_logo(c: canvas.Canvas, x: float, y: float, size: float = 13.0) -> None:
    path = _logo_path()
    if path.is_file():
        c.drawImage(ImageReader(str(path)), x, y, width=size, height=size, preserveAspectRatio=True, mask="auto")


def _display_text(value: object) -> str:
    """Normaliza separadores que algunas fuentes PDF muestran como cuadrados."""
    return str(value or "").replace("→", " - ").replace("\u00e2\u2020\u2019", " - ").strip()


def _document_value(masked_document: object) -> str:
    """Evita repetir el tipo de documento junto a la etiqueta DNI del casillero."""
    value = _display_text(masked_document)
    if value.upper().startswith("DNI "):
        return value[4:].strip()
    return value


def _draw_text_box(
    c: canvas.Canvas,
    text: object,
    x: float,
    y_top: float,
    max_width: float,
    max_height: float,
    font_name: str = FONT_REGULAR,
    font_size: float = 7.2,
    leading: float | None = None,
    align: str = "left",
    bold: bool = False,
    color=None,
) -> None:
    """Dibuja texto envuelto sin ellipsis ni desborde del casillero."""
    clean_text = _display_text(text)
    if not clean_text or max_width <= 0 or max_height <= 0:
        return
    actual_font = FONT_BOLD if bold else font_name
    size = font_size
    min_size = 4.4
    while True:
        words = clean_text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if pdfmetrics.stringWidth(candidate, actual_font, size) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        line_leading = leading or size * 1.12
        if len(lines) * line_leading <= max_height or size <= min_size:
            break
        size = max(min_size, size - 0.25)

    c.saveState()
    c.setFont(actual_font, size)
    c.setFillColor(color or colors.HexColor("#111111"))
    for index, line in enumerate(lines):
        y = y_top - (index + 1) * line_leading + (line_leading - size) * 0.35
        if align == "center":
            c.drawCentredString(x, y, line)
        elif align == "right":
            c.drawRightString(x, y, line)
        else:
            c.drawString(x, y, line)
    c.restoreState()


def _draw_text_auto(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    font_name: str = FONT_REGULAR,
    font_size: float = 7.2,
    align: str = "left",
    bold: bool = False,
    color=None,
) -> None:
    if not text:
        return
    clean_text = _display_text(text)
    if not clean_text:
        return

    actual_font = FONT_BOLD if bold else font_name
    current_size = font_size
    text_w = c.stringWidth(clean_text, actual_font, current_size)
    if text_w > max_width and max_width > 0:
        current_size = max(4.4, font_size * (max_width / text_w))
    c.setFont(actual_font, current_size)
    if color:
        c.setFillColor(color)
    else:
        c.setFillColor(colors.HexColor("#111111"))

    if align == "center":
        c.drawCentredString(x, y, clean_text)
    elif align == "right":
        c.drawRightString(x, y, clean_text)
    else:
        c.drawString(x, y, clean_text)


def _resolve_seat_display(data: TicketData) -> str:
    if data.is_lap_infant:
        return "Sin butaca"
    raw = str(data.seat_number or "").strip()
    if not raw or raw.lower() == "none":
        return "Sin butaca"
    return raw


def draw_ticket_page(c: canvas.Canvas, data: TicketData) -> None:
    """Renderiza el pasaje de SC Viajes según la plantilla aprobada.

    - Una sola página.
    - Dos talones: Talón de la empresa / agencia (superior) y Talón para el pasajero (inferior).
    - Mismo número de pasaje en ambos talones.
    - Plantilla vacía: 'AAA-000000000' con campos en blanco.
    - Primera emisión real: 'AAA-000000001' (secuencia de 9 dígitos).
    - Sin códigos QR ni códigos de barras visibles.
    - WhatsApp exacto: +5493513954420.
    """
    stroke_color = colors.HexColor("#cccccc")
    c.setStrokeColor(stroke_color)
    c.setLineWidth(0.6)

    is_blank = getattr(data, "is_blank_template", False) or data.ticket_code == "AAA-000000000"
    ticket_number = "AAA-000000000" if is_blank else data.ticket_code

    # ========================================================
    # 1. TALÓN SUPERIOR
    # ========================================================
    # Header: Logo, Agencia, Vendedor
    _draw_logo(c, 28.5, 795.0, size=23.0)

    c.setFillColor(colors.HexColor("#111111"))
    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(57.0, 806.0, "Agencia:")
    c.setFont(FONT_BOLD, 7.2)
    c.drawString(89.0, 806.0, "SC VIAJES")
    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(304.0, 806.0, "Vendedor:")
    if not is_blank and data.seller_display:
        _draw_text_auto(c, data.seller_display, 343.0, 806.0, max_width=200, font_size=7.2)

    # Box 1: Empresa transportista | Pasaje
    c.roundRect(28.5, 766.69, 537.75, 30.61, 5, stroke=1, fill=0)
    c.line(429.41, 766.69, 429.41, 797.3)
    c.drawString(35.7, 785.0, "Empresa transportista")
    if not is_blank and data.carrier_name:
        _draw_text_auto(c, data.carrier_name, 35.7, 774.0, max_width=380, font_size=8.4, bold=True)
    c.drawString(436.0, 785.0, "Pasaje")
    _draw_text_auto(c, ticket_number, 479.0, 774.0, max_width=80, font_size=9.0, bold=True)

    # Box 2: Pasajero | Categoría
    c.roundRect(28.5, 733.08, 268.88, 30.61, 5, stroke=1, fill=0)
    c.roundRect(303.98, 733.08, 262.27, 30.61, 5, stroke=1, fill=0)
    c.drawString(35.7, 753.0, "Pasajero")
    c.setFont(FONT_REGULAR, 7.0)
    c.drawString(35.7, 741.0, "DNI:")
    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(311.2, 753.0, "Categoría - Calidad y tipo de servicio")

    if not is_blank:
        _draw_text_box(c, data.passenger_name, 106.0, 762.0, 184, 10, font_size=7.2, bold=True)
        doc_text = _document_value(data.masked_document)
        if data.is_lap_infant and data.companion_name:
            doc_text = f"{doc_text} (Acomp: {data.companion_name})"
        _draw_text_auto(c, doc_text, 58.0, 741.0, max_width=230, font_size=7.0)

        cat_text = data.category_display
        if data.service_quality_display and data.service_quality_display != PENDING_SANDRO_VALUE:
            cat_text = f"{cat_text} - {data.service_quality_display}"
        _draw_text_box(c, cat_text, 311.2, 750.0, 245, 10, font_size=7.2)

    # Box 3: Origen | Destino | Asiento
    c.roundRect(28.5, 701.27, 268.88, 28.81, 5, stroke=1, fill=0)
    c.roundRect(303.98, 701.27, 174.05, 28.81, 5, stroke=1, fill=0)
    c.roundRect(484.63, 701.27, 81.62, 28.81, 5, stroke=1, fill=0)
    c.drawString(35.7, 719.0, "Origen")
    c.drawString(311.2, 719.0, "Destino")
    c.drawString(491.8, 719.0, "Asiento")

    if not is_blank:
        _draw_text_auto(c, data.origin_stop_name, 68.0, 719.0, max_width=220, font_size=7.2, bold=True)
        dep_dt = f"{data.travel_date} {data.departure_time}".strip()
        _draw_text_auto(c, dep_dt, 35.7, 708.0, max_width=250, font_size=7.0)

        _draw_text_box(c, data.destination_stop_name, 345.0, 727.0, 130, 10, font_size=7.2, bold=True)
        arr_date = data.arrival_date or data.travel_date
        arr_dt = f"{arr_date} {data.arrival_time}".strip()
        _draw_text_auto(c, arr_dt, 311.2, 708.0, max_width=160, font_size=7.0)

        seat_str = _resolve_seat_display(data)
        _draw_text_auto(c, seat_str, 525.4, 708.0, max_width=75, font_size=8.5, align="center", bold=True)

    # Box 4: Formas de pago | Detalle | Importe total
    c.roundRect(28.5, 659.26, 268.88, 39.01, 5, stroke=1, fill=0)
    c.roundRect(303.98, 659.26, 160.24, 39.01, 5, stroke=1, fill=0)
    c.roundRect(470.82, 659.26, 95.43, 39.01, 5, stroke=1, fill=0)
    c.drawString(35.7, 687.0, "Formas de pago")
    c.drawString(311.2, 687.0, "Detalle")
    c.drawString(311.2, 676.0, "Tarifa:")
    c.drawString(311.2, 666.0, "Descuento:")
    c.drawString(477.0, 687.0, "Importe total")

    if not is_blank:
        p_method = "Pasaje gratuito — sin butaca" if data.is_lap_infant else data.payment_method_display
        _draw_text_auto(c, p_method, 35.7, 673.0, max_width=255, font_size=7.2)
        _draw_text_auto(c, data.price_display, 348.0, 676.0, max_width=110, font_size=7.2)
        _draw_text_auto(c, "$ 0,00", 390.0, 666.0, max_width=70, font_size=7.2)
        _draw_text_auto(c, data.total_amount_display, 518.5, 673.0, max_width=85, font_size=10.0, align="center", bold=True)

    # ========================================================
    # 2. LÍNEA DE CORTE INTERMEDIA
    # ========================================================
    c.saveState()
    c.setDash(3, 3)
    c.setStrokeColor(stroke_color)
    c.line(34.5, 649.65, 560.25, 649.65)
    c.restoreState()

    c.setFont(FONT_BOLD, 8.4)
    c.drawCentredString(297.0, 637.0, "Talón para el pasajero")

    # ========================================================
    # 3. TALÓN PARA EL PASAJERO (INFERIOR)
    # ========================================================
    _draw_logo(c, 28.5, 609.0, size=23.0)

    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(57.0, 619.0, "Agencia:")
    c.setFont(FONT_BOLD, 7.2)
    c.drawString(89.0, 619.0, "SC VIAJES")
    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(304.0, 619.0, "Vendedor:")
    if not is_blank and data.seller_display:
        _draw_text_auto(c, data.seller_display, 343.0, 619.0, max_width=200, font_size=7.2)

    # Box 5: Empresa transportista | Datos fiscales | Pasaje
    c.roundRect(28.5, 548.83, 537.75, 58.21, 5, stroke=1, fill=0)
    c.line(293.0, 548.83, 293.0, 607.04)
    c.line(480.43, 548.83, 480.43, 607.04)
    c.drawString(35.7, 600.0, "Empresa transportista")
    if not is_blank and data.carrier_name:
        _draw_text_auto(c, data.carrier_name, 35.7, 586.0, max_width=250, font_size=8.4, bold=True)

    c.setFont(FONT_REGULAR, 6.4)
    c.drawString(297.4, 602.0, "Razón social:")
    c.drawString(297.4, 593.0, "CUIT:")
    c.drawString(297.4, 584.0, "Inicio de actividades:                     IIBB:")
    c.drawString(297.4, 575.0, "Usted viaja asegurado por:")
    c.drawString(297.4, 566.0, "Póliza o referencia:")

    if not is_blank:
        _draw_text_auto(c, data.carrier_legal_name, 345.0, 602.0, max_width=130, font_size=6.4)
        cuit_iva = f"{data.carrier_cuit} · IVA: {data.carrier_vat_condition}"
        _draw_text_auto(c, cuit_iva, 322.0, 593.0, max_width=155, font_size=6.4)
        _draw_text_auto(c, data.carrier_activity_start, 372.0, 584.0, max_width=60, font_size=6.4)
        _draw_text_auto(c, data.carrier_iibb, 445.0, 584.0, max_width=32, font_size=6.4)
        _draw_text_auto(c, data.insurer_name, 388.0, 575.0, max_width=90, font_size=6.4)
        _draw_text_auto(c, data.insurance_policy_ref, 365.0, 566.0, max_width=112, font_size=6.4)

    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(487.0, 600.0, "Pasaje")
    _draw_text_auto(c, ticket_number, 482.0, 586.0, max_width=80, font_size=9.0, bold=True)

    # Box 6: Pasajero | Categoría
    c.roundRect(28.5, 499.61, 268.88, 39.39, 5, stroke=1, fill=0)
    c.roundRect(303.98, 499.61, 262.27, 39.39, 5, stroke=1, fill=0)
    c.drawString(35.7, 530.0, "Pasajero")
    c.setFont(FONT_REGULAR, 7.0)
    c.drawString(35.7, 519.0, "DNI:")
    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(311.2, 530.0, "Categoría - Calidad y tipo de servicio")

    if not is_blank:
        _draw_text_box(c, data.passenger_name, 106.0, 539.0, 184, 11, font_size=7.2, bold=True)
        doc_text = _document_value(data.masked_document)
        if data.is_lap_infant and data.companion_name:
            doc_text = f"{doc_text} (Acomp: {data.companion_name})"
        _draw_text_auto(c, doc_text, 58.0, 519.0, max_width=230, font_size=7.0)

        cat_text = data.category_display
        if data.service_quality_display and data.service_quality_display != PENDING_SANDRO_VALUE:
            cat_text = f"{cat_text} - {data.service_quality_display}"
        _draw_text_box(c, cat_text, 311.2, 530.0, 245, 11, font_size=7.2)

    # Box 7: Origen | Destino
    c.roundRect(28.5, 467.8, 268.88, 26.2, 5, stroke=1, fill=0)
    c.roundRect(303.98, 467.8, 262.27, 26.2, 5, stroke=1, fill=0)
    c.drawString(35.7, 485.0, "Origen")
    c.drawString(311.2, 485.0, "Destino")

    if not is_blank:
        _draw_text_auto(c, data.origin_stop_name, 68.0, 485.0, max_width=220, font_size=7.2, bold=True)
        _draw_text_box(c, data.origin_address, 35.7, 480.0, 255, 11, font_size=6.5, leading=5.6)

        # Destino inferior usa tres bandas independientes: el nombre no comparte
        # línea ni área de pintura con la etiqueta o la dirección.
        _draw_text_box(c, data.destination_stop_name, 345.0, 487.0, 215, 8, font_size=6.2, bold=True)
        _draw_text_box(c, data.destination_address, 311.2, 475.0, 250, 6, font_size=5.8, leading=5.5)

    # Box 8: Asiento | Salida | Se anuncia a | Arribo estimado | Plataforma
    c.roundRect(28.5, 415.59, 537.75, 45.01, 5, stroke=1, fill=0)
    c.line(135.0, 415.59, 135.0, 460.6)
    c.line(240.0, 415.59, 240.0, 460.6)
    c.line(350.0, 415.59, 350.0, 460.6)
    c.line(460.0, 415.59, 460.0, 460.6)

    c.setFont(FONT_BOLD, 7.2)
    c.drawCentredString((28.5 + 135.0) / 2, 454.0, "Asiento")
    c.drawCentredString((135.0 + 240.0) / 2, 454.0, "Salida")
    c.drawCentredString((240.0 + 350.0) / 2, 454.0, "Se anuncia a")
    c.drawCentredString((350.0 + 460.0) / 2, 454.0, "Arribo estimado")
    c.drawCentredString((460.0 + 566.25) / 2, 454.0, "Plataforma")

    if not is_blank:
        seat_str = _resolve_seat_display(data)
        seat_fsize = 9.0 if seat_str == "Sin butaca" else 12.0
        _draw_text_auto(c, seat_str, (28.5 + 135.0) / 2, 432.0, max_width=100, font_size=seat_fsize, align="center", bold=True)

        _draw_text_auto(c, data.departure_time, (135.0 + 240.0) / 2, 436.0, max_width=100, font_size=12.0, align="center", bold=True)
        _draw_text_auto(c, data.travel_date, (135.0 + 240.0) / 2, 423.0, max_width=100, font_size=7.2, align="center")

        announcement = data.destination_stop_name or data.route_name
        _draw_text_box(c, announcement, (240.0 + 350.0) / 2, 445.0, 105, 18, font_size=8.0, align="center", bold=True)

        arr_date = data.arrival_date or data.travel_date
        _draw_text_auto(c, data.arrival_time, (350.0 + 460.0) / 2, 436.0, max_width=100, font_size=12.0, align="center", bold=True)
        _draw_text_auto(c, arr_date, (350.0 + 460.0) / 2, 423.0, max_width=100, font_size=7.2, align="center")

        platform_val = data.platform_announcement if data.platform_announcement != PENDING_SANDRO_VALUE else "A confirmar"
        _draw_text_box(c, platform_val, (460.0 + 566.25) / 2, 445.0, 100, 18, font_size=8.0, align="center", bold=True)

    # Box 9: Formas de pago | Detalle | Importe total
    c.roundRect(28.5, 373.58, 268.88, 38.42, 5, stroke=1, fill=0)
    c.roundRect(303.98, 373.58, 160.24, 38.42, 5, stroke=1, fill=0)
    c.roundRect(470.82, 373.58, 95.43, 38.42, 5, stroke=1, fill=0)
    c.setFont(FONT_REGULAR, 7.2)
    c.drawString(35.7, 402.0, "Formas de pago")
    c.drawString(311.2, 402.0, "Detalle")
    c.drawString(311.2, 391.0, "Tarifa:")
    c.drawString(311.2, 381.0, "Tasa y cargos:")
    c.drawString(477.0, 402.0, "Importe total")

    if not is_blank:
        p_method = "Pasaje gratuito — sin butaca" if data.is_lap_infant else data.payment_method_display
        _draw_text_auto(c, p_method, 35.7, 388.0, max_width=255, font_size=7.2)
        _draw_text_auto(c, data.price_display, 348.0, 391.0, max_width=110, font_size=7.2)
        fee_label = "$ 0,00"
        _draw_text_auto(c, fee_label, 380.0, 381.0, max_width=80, font_size=7.2)
        _draw_text_auto(c, data.total_amount_display, 518.5, 388.0, max_width=85, font_size=10.0, align="center", bold=True)

    # WhatsApp banner
    whatsapp_str = f"Contacto WhatsApp: {data.contact_whatsapp}"
    c.setFont(FONT_BOLD, 9.0)
    c.drawCentredString(297.0, 358.37, whatsapp_str)

    # Conditions Header
    c.setFont(FONT_BOLD, 5.4)
    c.drawString(34.5, 304.5, "Condiciones generales del pasaje")

    # Conditions in 2 columns
    style_l = ParagraphStyle("CondL", fontName=FONT_REGULAR, fontSize=5.2, leading=6.45, textColor=colors.HexColor("#111111"))
    y_l = 299.0
    for title, body in CONDITIONS_LEFT:
        p = Paragraph(f"<b>{title}</b>{body}", style_l)
        w, h = p.wrap(255, 200)
        p.drawOn(c, 34.5, y_l - h)
        y_l -= (h + 2.5)

    y_r = 299.0
    for title, body in CONDITIONS_RIGHT:
        p = Paragraph(f"<b>{title}</b>{body}", style_l)
        w, h = p.wrap(255, 200)
        p.drawOn(c, 303.4, y_r - h)
        y_r -= (h + 2.5)


draw_provisional_ticket = draw_ticket_page
