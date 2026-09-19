"""Submódulo de renderizado y generación de pasajes en PDF."""

from .data import TicketData
from .generator import build_ticket_pdf

__all__ = ["TicketData", "build_ticket_pdf"]
