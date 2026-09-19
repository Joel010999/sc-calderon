"""Plantilla visual provisional para pasajes de SC Viajes utilizando ReportLab."""

import io
import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .data import TicketData


def draw_provisional_ticket(c: canvas.Canvas, data: TicketData) -> None:
    """Dibuja el pasaje en el lienzo de ReportLab con la plantilla provisional.

    Todos los textos se posicionan de manera acotada para evitar desbordes y
    soportar caracteres acentuados y nombres largos.
    """
    width, height = A4

    # 1. Franja superior institucional
    c.setFillColor(colors.HexColor("#0f172a"))  # Slate 900
    c.rect(36, height - 85, width - 72, 50, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(52, height - 63, "SC VIAJES")
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(width - 52, height - 63, "PASAJE ELECTRÓNICO")

    # 2. Marco principal del pasaje
    c.setStrokeColor(colors.HexColor("#cbd5e1"))  # Slate 300
    c.setLineWidth(1)
    c.rect(36, height - 420, width - 72, 325, fill=0, stroke=1)

    # Sub-cabecera: Códigos y Emisión
    c.setFillColor(colors.HexColor("#f8fafc"))  # Slate 50
    c.rect(37, height - 128, width - 74, 42, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(37, height - 128, width - 37, height - 128)

    c.setFillColor(colors.HexColor("#475569"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, height - 105, "CÓDIGO DE PASAJE")
    c.drawString(220, height - 105, "CÓDIGO DE RESERVA")
    c.drawRightString(width - 52, height - 105, "EMISIÓN")

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(52, height - 120, data.ticket_code)
    c.drawString(220, height - 120, str(data.booking_code)[:8].upper())
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 52, height - 120, data.issued_at)

    # 3. Sección Izquierda: Datos de Viaje y Pasajero
    # Pasajero
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, height - 150, "PASAJERO / A")
    c.drawString(220, height - 150, "DOCUMENTO")

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 11)
    # Acotar nombre si es muy largo
    p_name = data.passenger_name[:32] if len(data.passenger_name) > 32 else data.passenger_name
    c.drawString(52, height - 165, p_name)
    c.drawString(220, height - 165, data.masked_document)

    # Separador sutil
    c.setStrokeColor(colors.HexColor("#f1f5f9"))
    c.line(52, height - 178, 360, height - 178)

    # Recorrido y Paradas
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, height - 198, "ORIGEN")
    c.drawString(220, height - 198, "DESTINO")

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(52, height - 215, data.origin_stop_name[:24])
    c.drawString(220, height - 215, data.destination_stop_name[:24])

    # Horarios y Lugares
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, height - 245, "FECHA DE VIAJE")
    c.drawString(220, height - 245, "HORA DE SUBIDA")

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica", 11)
    c.drawString(52, height - 260, data.travel_date)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(220, height - 260, data.departure_time)

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, height - 285, "LUGAR DE SUBIDA")
    c.drawString(220, height - 285, "LLEGADA ESTIMADA")

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica", 10)
    c.drawString(52, height - 300, data.boarding_place[:26])
    c.drawString(220, height - 300, data.arrival_time)

    # Separador sutil
    c.setStrokeColor(colors.HexColor("#f1f5f9"))
    c.line(52, height - 315, 360, height - 315)

    # Butaca, Categoría y Precio
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, height - 335, "BUTACA")
    c.drawString(140, height - 335, "CATEGORÍA")
    c.drawString(240, height - 335, "PRECIO HISTÓRICO")

    c.setFillColor(colors.HexColor("#1e3a8a"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(52, height - 355, str(data.seat_number))

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(140, height - 352, data.category_display)
    c.drawString(240, height - 352, data.price_display)

    # 4. Sección Derecha: QR Code y Control de Verificación
    # Generar imagen de código QR puro Python
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
    # Si devuelve wrapper PilImage, acceder a la imagen PIL subyacente
    pil_img = qr_img._img if hasattr(qr_img, "_img") else qr_img
    pil_img.save(img_buffer, format="PNG")
    img_buffer.seek(0)

    qr_x = width - 195
    qr_y = height - 300
    qr_size = 135
    c.drawImage(ImageReader(img_buffer), qr_x, qr_y, width=qr_size, height=qr_size)

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 8)
    c.drawCentredString(qr_x + (qr_size / 2), qr_y - 12, "Escanear para verificar validez")

    # 5. Avisos y Requisitos Obligatorios
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(37, height - 418, width - 74, 38, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(37, height - 380, width - 37, height - 380)

    c.setFillColor(colors.HexColor("#b91c1c"))  # Red 700
    c.setFont("Helvetica-Bold", 9)
    c.drawString(52, height - 395, f"IMPORTANTE: {data.id_requirement_notice}")

    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(52, height - 410, f"AVISO: {data.provisional_notice}")

    # Guardar / cerrar página
    c.showPage()
