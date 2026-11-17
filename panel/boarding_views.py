from django.core.exceptions import ValidationError
from django.views.decorators.http import require_http_methods, require_POST

from .permissions import reservations_access


@reservations_access()
@require_http_methods(["GET", "POST"])
def boarding_validate(request):
    raise ValidationError("El control de embarque se encuentra deshabilitado.")


@reservations_access()
@require_POST
def boarding_reverse(request, record_pk):
    raise ValidationError("La reversión de embarque se encuentra deshabilitada.")
