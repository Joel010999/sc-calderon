from django import forms

from operations.models import Trip


class BoardingValidationForm(forms.Form):
    trip = forms.ModelChoiceField(
        label="Viaje",
        queryset=Trip.objects.none(),
        empty_label="Seleccioná el viaje",
    )
    scan_value = forms.CharField(
        label="Código QR o código del pasaje",
        max_length=2048,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autofocus": True,
                "placeholder": "Escaneá el QR o ingresá el código",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["trip"].queryset = (
            Trip.objects.filter(status__in=[Trip.Status.SCHEDULED, Trip.Status.BOARDING])
            .select_related("route", "bus")
            .order_by("departure_at", "pk")
        )


class BoardingReversalForm(forms.Form):
    reason = forms.CharField(
        label="Motivo de reversión",
        min_length=3,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
