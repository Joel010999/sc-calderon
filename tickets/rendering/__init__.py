"""Submódulo de renderizado y generación de pasajes en PDF."""

from .data import PENDING_SANDRO_VALUE, TicketData
from .generator import build_blank_ticket_template_pdf, build_ticket_pdf
from .template import PAGESIZE, draw_provisional_ticket, draw_ticket_page

__all__ = [
    "PENDING_SANDRO_VALUE",
    "PAGESIZE",
    "TicketData",
    "build_blank_ticket_template_pdf",
    "build_ticket_pdf",
    "draw_provisional_ticket",
    "draw_ticket_page",
]
