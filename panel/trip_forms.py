from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from operations.models import Bus, Route, TripFare
from operations.validators import validate_aware_datetime
from .forms import OperationalForm


class TripSelectionForm(forms.Form):
    route = forms.ModelChoiceField(
        label="Recorrido", queryset=Route.objects.none(), empty_label="Seleccioná un recorrido",
        error_messages={"invalid_choice": "Seleccioná un recorrido activo válido."},
    )
    bus = forms.ModelChoiceField(
        label="Colectivo", queryset=Bus.objects.none(), empty_label="Seleccioná un colectivo",
        error_messages={"invalid_choice": "Seleccioná un colectivo activo con butacas activas."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["route"].queryset = Route.objects.filter(is_active=True).order_by("code")
        self.fields["bus"].queryset = Bus.objects.filter(
            is_active=True, seats__is_active=True
        ).distinct().order_by("code")
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-input"


class LocalDateTimeField(forms.DateTimeField):
    def to_python(self, value):
        # datetime-local no envía un offset: Django interpreta la entrada local
        # en la zona configurada y entrega un datetime consciente al dominio.
        with timezone.override(settings.TIME_ZONE):
            return super().to_python(value)


class TripScheduleForm(forms.Form):
    def __init__(self, *args, route, **kwargs):
        super().__init__(*args, **kwargs)
        self.route_stops = list(route.route_stops.select_related("stop").order_by("sequence"))
        for item in self.route_stops:
            boarding = "sí" if item.allows_boarding else "no"
            alighting = "sí" if item.allows_alighting else "no"
            self.fields[f"stop_{item.stop_id}"] = LocalDateTimeField(
                label=f"{item.sequence}. {item.stop.name}",
                help_text=f"Subida: {boarding}. Bajada: {alighting}. Horario argentino.",
                widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={
                    "type": "datetime-local", "class": "form-input", "step": "60",
                }),
            )

    def clean(self):
        cleaned = super().clean()
        allowed = set(self.fields) | {"csrfmiddlewaretoken", "action", "confirmation"}
        if set(self.data) - allowed:
            raise ValidationError("Se recibieron campos adicionales. Indicá solo un horario por parada.")
        if hasattr(self.data, "getlist") and any(len(self.data.getlist(key)) != 1 for key in self.fields):
            raise ValidationError("Indicá exactamente un horario por parada, sin faltantes ni duplicados.")
        if not self.route_stops:
            raise ValidationError("El recorrido todavía no tiene paradas configuradas.")
        previous = None
        for key in self.fields:
            value = cleaned.get(key)
            if value is None:
                continue
            validate_aware_datetime(value)
            if previous is not None and value <= previous:
                self.add_error(key, "El horario debe ser posterior al de la parada anterior.")
            previous = value
        first_key = next(iter(self.fields))
        if cleaned.get(first_key) and cleaned[first_key] <= timezone.now():
            self.add_error(first_key, "La salida debe estar en el futuro.")
        return cleaned

    def schedules(self):
        return {item.stop_id: self.cleaned_data[f"stop_{item.stop_id}"] for item in self.route_stops}

    def confirmation_data(self, route, bus):
        return {
            "route": route.pk, "bus": bus.pk,
            "stops": [
                [item.stop_id, item.sequence, item.allows_boarding, item.allows_alighting,
                 self.cleaned_data[f"stop_{item.stop_id}"].isoformat()]
                for item in self.route_stops
            ],
        }


class TripFareForm(OperationalForm):
    class Meta:
        model = TripFare
        fields = ["origin_stop", "destination_stop", "seat_category", "amount", "currency"]

    def __init__(self, *args, trip, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.trip = trip
        stops = trip.trip_stops.select_related("stop").order_by("sequence")
        self.fields["origin_stop"].queryset = stops.filter(allows_boarding=True)
        self.fields["destination_stop"].queryset = stops.filter(allows_alighting=True)
        self.fields["origin_stop"].empty_label = "Seleccioná una parada de subida"
        self.fields["destination_stop"].empty_label = "Seleccioná una parada de bajada"
        for name in ("origin_stop", "destination_stop"):
            self.fields[name].error_messages["invalid_choice"] = "Seleccioná una parada válida de este viaje."
        self.fields["seat_category"].choices = [
            (value, label if value else "Seleccioná una categoría")
            for value, label in self.fields["seat_category"].choices
        ]

    def _post_clean(self):
        super()._post_clean()
        if not self.errors:
            # trip se fija en el servidor y se excluye de ModelForm; incluirlo
            # explícitamente en las restricciones compuestas de unicidad.
            try:
                self.instance.validate_constraints()
            except ValidationError as error:
                self.add_error(None, error)
