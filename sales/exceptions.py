from django.core.exceptions import ValidationError


class SalesError(Exception):
    """Excepción base para errores del dominio de ventas."""


class SeatUnavailableError(SalesError):
    """La butaca solicitada no está disponible o ya fue tomada por otro proceso."""


class PrioritySeatUnavailableError(SeatUnavailableError, ValidationError):
    """La butaca prioritaria aún no está habilitada para venta general."""

    def __str__(self):
        if getattr(self, "messages", None) and len(self.messages) == 1:
            return str(self.messages[0])
        return super().__str__()


class BookingExpiredError(SalesError):
    """La reserva ha expirado y no se puede completar la operación."""


class InvalidBookingError(SalesError):
    """La reserva se encuentra en un estado incompatible con la operación solicitada."""
