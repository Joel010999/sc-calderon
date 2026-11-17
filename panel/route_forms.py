import json
from django import forms
from django.core.exceptions import ValidationError

from operations.models import Route, Stop
from operations.services import validate_route_stops
from .forms import OperationalForm


class StopForm(OperationalForm):
    class Meta:
        model = Stop
        fields = ["code", "name", "city", "province"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.has_historical_trips:
            self.fields["code"].disabled = True
            self.fields["province"].disabled = True
            self.fields["city"].disabled = True
            self.fields["name"].disabled = True
            self.fields["province"].help_text = "Esta parada está asociada a viajes históricos; sus datos no pueden modificarse."

    def clean_code(self):
        code = self.cleaned_data.get("code", "")
        return code.strip().upper() if code else ""


class RouteForm(OperationalForm):
    class Meta:
        model = Route
        fields = ["code", "name", "is_active"]

    def clean_code(self):
        code = self.cleaned_data.get("code", "")
        return code.strip().upper() if code else ""


class RouteConfigurationForm(OperationalForm):
    stops_json = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Route
        fields = ["code", "name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cleaned_stops = []

    def clean_code(self):
        code = self.cleaned_data.get("code", "")
        return code.strip().upper() if code else ""

    def clean(self):
        cleaned_data = super().clean()
        stops_data = self._extract_stops_data()
        try:
            self.cleaned_stops = validate_route_stops(stops_data)
        except ValidationError as error:
            self.add_error(None, error)
        return cleaned_data

    def _extract_stops_data(self):
        # 1. Intentar desde stops_json
        json_val = self.data.get("stops_json", "").strip()
        if json_val:
            try:
                raw_list = json.loads(json_val)
                stops_map = {s.pk: s for s in Stop.objects.filter(pk__in=[item["stop_id"] for item in raw_list])}
                return [
                    {
                        "stop": stops_map.get(int(item["stop_id"])),
                        "sequence": int(item["sequence"]),
                        "allows_boarding": bool(item.get("allows_boarding", False)),
                        "allows_alighting": bool(item.get("allows_alighting", False)),
                    }
                    for item in raw_list
                ]
            except (ValueError, KeyError, TypeError):
                pass

        # 2. Intentar desde campos indexados stop_id_{i}, sequence_{i}, etc.
        stops_data = []
        indices = set()
        for key in self.data:
            if key.startswith("stop_id_"):
                idx = key[len("stop_id_"):]
                indices.add(idx)

        if indices:
            stop_ids = []
            for idx in indices:
                val = self.data.get(f"stop_id_{idx}")
                if val:
                    try:
                        stop_ids.append(int(val))
                    except ValueError:
                        pass
            stops_map = {s.pk: s for s in Stop.objects.filter(pk__in=stop_ids)}
            for idx in sorted(indices, key=lambda x: int(x) if x.isdigit() else x):
                stop_pk = self.data.get(f"stop_id_{idx}")
                seq = self.data.get(f"sequence_{idx}")
                boarding = self.data.get(f"allows_boarding_{idx}") in ("true", "True", "1", "on", True)
                alighting = self.data.get(f"allows_alighting_{idx}") in ("true", "True", "1", "on", True)
                if stop_pk and seq:
                    try:
                        stops_data.append({
                            "stop": stops_map.get(int(stop_pk)),
                            "sequence": int(seq),
                            "allows_boarding": boarding,
                            "allows_alighting": alighting,
                        })
                    except ValueError:
                        pass
        return stops_data
