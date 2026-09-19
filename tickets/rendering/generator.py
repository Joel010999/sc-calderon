"""Generador de pasajes PDF desacoplado de la base de datos."""

import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .data import TicketData
from .template import draw_provisional_ticket


def build_ticket_pdf(data: TicketData) -> bytes:
    """Construye el documento PDF del pasaje y retorna sus bytes.

    Recibe exclusivamente la estructura TicketData sin acoplarse a modelos Django.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f"Pasaje SC Viajes - {data.ticket_code}")
    c.setAuthor("SC Viajes")
    c.setSubject(f"Pasaje electrónico {data.ticket_code}")

    draw_provisional_ticket(c, data)
    c.save()

    return buffer.getvalue()
