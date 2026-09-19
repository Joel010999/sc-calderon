"""Excepciones de dominio para el módulo tickets."""


class TicketError(Exception):
    """Excepción base para el módulo tickets."""
    pass


class InvalidTicketError(TicketError):
    """La operación sobre el pasaje es inválida según las reglas de negocio."""
    pass


class TicketIssuanceError(TicketError):
    """Error durante la emisión de pasajes para una reserva."""
    pass


class TicketNotFoundError(TicketError):
    """El pasaje solicitado no existe."""
    pass


class TicketVoidError(TicketError):
    """El pasaje se encuentra anulado."""
    pass


class TicketEmailError(TicketError):
    """Error durante el proceso de envío de correos con pasajes."""
    pass


class TicketEmailPendingError(TicketEmailError):
    """Existe un intento de envío en progreso o ambiguo."""
    pass


class TicketEmailDuplicateError(TicketEmailError):
    """Los pasajes ya fueron enviados exitosamente al comprador."""
    pass


class TicketStorageError(TicketError):
    """Fallo en el almacenamiento privado de pasajes."""
    pass
