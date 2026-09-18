"""Excepciones de dominio para el módulo de pagos."""


class PaymentError(Exception):
    """Excepción base para errores del dominio de pagos."""
    pass


class PaymentDuplicateError(PaymentError):
    """Se lanza cuando ya existe un pago activo (UNDER_REVIEW o APPROVED) para la reserva."""
    pass


class InvalidPaymentStatusError(PaymentError):
    """Se lanza cuando el estado del pago no permite la acción solicitada."""
    pass


class PaymentAmountMismatchError(PaymentError):
    """Se lanza cuando el importe informado no coincide con el total de la reserva."""
    pass


class PaymentVoucherError(PaymentError):
    """Se lanza cuando el comprobante no cumple con los requisitos requeridos."""
    pass
