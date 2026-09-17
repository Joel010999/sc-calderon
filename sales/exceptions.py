class SalesError(Exception):
    """Excepción base para errores del dominio de ventas."""


class SeatUnavailableError(SalesError):
    """La butaca solicitada no está disponible o ya fue tomada por otro proceso."""


class BookingExpiredError(SalesError):
    """La reserva ha expirado y no se puede completar la operación."""


class InvalidBookingError(SalesError):
    """La reserva se encuentra en un estado incompatible con la operación solicitada."""
