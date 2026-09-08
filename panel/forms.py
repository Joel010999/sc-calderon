from django import forms
from django.core.exceptions import ValidationError

from operations.models import Bus, Seat


class OperationalForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-input"


class BusForm(OperationalForm):
    class Meta:
        model = Bus
        fields = ["code", "display_name", "license_plate"]
        error_messages = {"code": {"unique": "Ya existe un colectivo con ese código."}}


class SeatForm(OperationalForm):
    class Meta:
        model = Seat
        fields = ["number", "deck", "category", "position_x", "position_y"]

    def __init__(self, *args, bus, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.bus = bus

    def _post_clean(self):
        super()._post_clean()
        if not self.errors:
            # ModelForm excluye bus por no ser editable. Validamos también
            # las restricciones compuestas con el colectivo resuelto en el servidor.
            try:
                self.instance.validate_constraints()
            except ValidationError as error:
                self.add_error(None, error)
