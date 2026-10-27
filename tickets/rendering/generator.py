"""Generador de pasajes PDF desacoplado de la base de datos."""

import io
from reportlab.pdfgen import canvas

from .data import TicketData
from .template import PAGESIZE, draw_ticket_page


def build_ticket_pdf(data: TicketData) -> bytes:
    """Construye el documento PDF del pasaje y retorna sus bytes.

    Recibe exclusivamente la estructura TicketData sin acoplarse a modelos Django.
    Genera un documento de una sola página y dos talones según la plantilla aprobada.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=PAGESIZE)
    c.setTitle(f"Pasaje SC Viajes - {data.ticket_code}")
    c.setAuthor("SC Viajes")
    c.setSubject(f"Pasaje electrónico {data.ticket_code}")

    draw_ticket_page(c, data)
    c.showPage()
    c.save()

    return buffer.getvalue()


def build_blank_ticket_template_pdf() -> bytes:
    """Genera la plantilla vacía oficial con código AAA-000000000 sin emitir."""
    return build_ticket_pdf(TicketData.blank_template())
