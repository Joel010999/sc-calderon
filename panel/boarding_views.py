from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from tickets.boarding import (
    BoardingCode,
    BoardingOutcome,
    reverse_boarding,
    validate_boarding,
)
from tickets.models import BoardingRecord

from .boarding_forms import BoardingReversalForm, BoardingValidationForm
from .permissions import can_manage_operations, require_operations_manager, reservations_access


@reservations_access()
@require_http_methods(["GET", "POST"])
def boarding_validate(request):
    form = BoardingValidationForm(request.POST or None)
    outcome = None
    reversal_form = BoardingReversalForm()
    if request.method == "POST" and form.is_valid():
        outcome = validate_boarding(
            form.cleaned_data["scan_value"],
            form.cleaned_data["trip"].pk,
            request.user,
        )
        if outcome.code == BoardingCode.VALID:
            messages.success(request, outcome.message)

    return render(
        request,
        "panel/boarding_validate.html",
        {
            "title": "Validar embarque",
            "form": form,
            "outcome": outcome,
            "reversal_form": reversal_form,
            "can_reverse": can_manage_operations(request.user),
        },
    )


@login_required(login_url="panel:login")
@require_POST
def boarding_reverse(request, record_pk):
    require_operations_manager(request.user)
    form = BoardingReversalForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indicá un motivo válido para revertir el embarque.")
        return redirect("panel:boarding_validate")

    record = get_object_or_404(BoardingRecord, pk=record_pk)
    try:
        reverse_boarding(record.pk, request.user, form.cleaned_data["reason"])
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "El embarque fue revertido y su historial quedó conservado.")
    return redirect("panel:boarding_validate")
