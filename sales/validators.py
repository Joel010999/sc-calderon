from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.utils import timezone


validate_currency = RegexValidator(
    regex=r"\A[A-Z]{3}\Z",
    message="La moneda debe tener exactamente tres letras mayúsculas.",
)


def validate_aware_datetime(value):
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValidationError("Indicá una fecha y hora con zona horaria.")
