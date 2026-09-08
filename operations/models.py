from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .validators import validate_aware_datetime, validate_code, validate_currency


class SeatCategory(models.TextChoices):
    SEMI_CAMA = "SEMI_CAMA", "Semicama"
    CAMA = "CAMA", "Cama"


class TimestampedModel(models.Model):
    created_at = models.DateTimeField("fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        abstract = True


class Stop(TimestampedModel):
    code = models.CharField("código", max_length=20, unique=True, validators=[validate_code])
    name = models.CharField("nombre", max_length=150)
    city = models.CharField("ciudad", max_length=150)
    province = models.CharField("provincia", max_length=100)
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        verbose_name = "parada"
        verbose_name_plural = "paradas"

    def __str__(self):
        return f"{self.code} — {self.name}"

    def clean(self):
        super().clean()
        if not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).values_list("code", flat=True).first()
            if previous is not None and previous != self.code:
                raise ValidationError({"code": "El código de una parada existente no se puede cambiar."})


class Route(TimestampedModel):
    code = models.CharField("código", max_length=30, unique=True, validators=[validate_code])
    name = models.CharField("nombre", max_length=150)
    is_active = models.BooleanField("activo", default=True)

    class Meta:
        verbose_name = "recorrido"
        verbose_name_plural = "recorridos"

    def __str__(self):
        return f"{self.code} — {self.name}"

    def allows_journey(self, origin, destination):
        """Acepta paradas guardadas; no depende de sus nombres ni del orden global."""
        if not self.pk or not origin.pk or not destination.pk or origin.pk == destination.pk:
            return False
        stops = {item.stop_id: item for item in self.route_stops.filter(
            stop_id__in=[origin.pk, destination.pk]
        )}
        start, end = stops.get(origin.pk), stops.get(destination.pk)
        return bool(start and end and start.sequence < end.sequence
                    and start.allows_boarding and end.allows_alighting)


class RouteStop(models.Model):
    route = models.ForeignKey(Route, verbose_name="recorrido", on_delete=models.PROTECT, related_name="route_stops")
    stop = models.ForeignKey(Stop, verbose_name="parada", on_delete=models.PROTECT, related_name="route_stops")
    sequence = models.PositiveIntegerField("orden", validators=[MinValueValidator(1)])
    allows_boarding = models.BooleanField("permite subir")
    allows_alighting = models.BooleanField("permite bajar")

    class Meta:
        verbose_name = "parada del recorrido"
        verbose_name_plural = "paradas del recorrido"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["route", "sequence"], name="ops_route_sequence_unique", violation_error_message="El orden ya existe en el recorrido."),
            models.UniqueConstraint(fields=["route", "stop"], name="ops_route_stop_unique", violation_error_message="La parada ya existe en el recorrido."),
            models.CheckConstraint(condition=Q(sequence__gte=1), name="ops_route_sequence_positive", violation_error_message="El orden debe ser mayor o igual a 1."),
            models.CheckConstraint(condition=Q(allows_boarding=True) | Q(allows_alighting=True), name="ops_route_stop_permission", violation_error_message="La parada debe permitir subir o bajar."),
        ]

    def __str__(self):
        return f"{self.route.code} · {self.sequence}: {self.stop.name}"


class Bus(TimestampedModel):
    code = models.CharField("código interno", max_length=30, unique=True, validators=[validate_code])
    display_name = models.CharField("nombre visible", max_length=150)
    license_plate = models.CharField("patente", max_length=20, blank=True, default="")
    is_active = models.BooleanField("activo", default=True)

    class Meta:
        verbose_name = "colectivo"
        verbose_name_plural = "colectivos"
        constraints = [
            models.UniqueConstraint(fields=["license_plate"], condition=~Q(license_plate=""), name="ops_bus_plate_unique", violation_error_message="La patente ya está asignada a otro colectivo."),
        ]

    def __str__(self):
        return f"{self.code} — {self.display_name}"

    @property
    def capacity(self):
        return self.seats.filter(is_active=True).count()


class Seat(TimestampedModel):
    class Deck(models.TextChoices):
        UPPER = "UPPER", "Planta alta"
        LOWER = "LOWER", "Planta baja"

    bus = models.ForeignKey(Bus, verbose_name="colectivo", on_delete=models.PROTECT, related_name="seats")
    number = models.PositiveIntegerField("número", validators=[MinValueValidator(1)])
    deck = models.CharField("planta", max_length=5, choices=Deck.choices)
    category = models.CharField("categoría", max_length=9, choices=SeatCategory.choices)
    position_x = models.PositiveIntegerField("posición horizontal", validators=[MinValueValidator(0)])
    position_y = models.PositiveIntegerField("posición vertical", validators=[MinValueValidator(0)])
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        verbose_name = "butaca"
        verbose_name_plural = "butacas"
        constraints = [
            models.UniqueConstraint(fields=["bus", "number"], name="ops_seat_number_unique", violation_error_message="El número de butaca ya existe en el colectivo."),
            models.UniqueConstraint(fields=["bus", "deck", "position_x", "position_y"], name="ops_seat_position_unique", violation_error_message="La posición ya está ocupada en esa planta."),
            models.CheckConstraint(condition=Q(number__gte=1), name="ops_seat_number_positive", violation_error_message="El número de butaca debe ser mayor o igual a 1."),
            models.CheckConstraint(condition=Q(position_x__gte=0) & Q(position_y__gte=0), name="ops_seat_position_nonnegative", violation_error_message="Las coordenadas deben ser mayores o iguales a 0."),
            models.CheckConstraint(condition=Q(deck__in=["UPPER", "LOWER"]), name="ops_seat_deck_valid", violation_error_message="La planta no es válida."),
            models.CheckConstraint(condition=Q(category__in=SeatCategory.values), name="ops_seat_category_valid", violation_error_message="La categoría no es válida."),
        ]

    def __str__(self):
        return f"{self.bus.code} · Butaca {self.number} · {self.get_category_display()}"


class Trip(TimestampedModel):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Programado"
        BOARDING = "BOARDING", "En embarque"
        STARTED = "STARTED", "Iniciado"
        COMPLETED = "COMPLETED", "Finalizado"
        CANCELLED = "CANCELLED", "Cancelado"

    route = models.ForeignKey(Route, verbose_name="recorrido", on_delete=models.PROTECT, related_name="trips")
    bus = models.ForeignKey(Bus, verbose_name="colectivo", on_delete=models.PROTECT, related_name="trips")
    departure_at = models.DateTimeField("salida", validators=[validate_aware_datetime])
    status = models.CharField("estado", max_length=9, choices=Status.choices, default=Status.SCHEDULED)

    class Meta:
        verbose_name = "viaje"
        verbose_name_plural = "viajes"
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["SCHEDULED", "BOARDING", "STARTED", "COMPLETED", "CANCELLED"]), name="ops_trip_status_valid", violation_error_message="El estado del viaje no es válido."),
        ]

    def __str__(self):
        departure = timezone.localtime(self.departure_at) if self.departure_at and timezone.is_aware(self.departure_at) else self.departure_at
        return f"{self.route.code} · {departure} · {self.get_status_display()}"

    def clean(self):
        super().clean()
        if not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).values_list("bus_id", flat=True).first()
            if previous is not None and previous != self.bus_id:
                raise ValidationError({"bus": "No se puede cambiar el colectivo de un viaje programado."})


class TripStop(models.Model):
    trip = models.ForeignKey(Trip, verbose_name="viaje", on_delete=models.PROTECT, related_name="trip_stops")
    stop = models.ForeignKey(Stop, verbose_name="parada", on_delete=models.PROTECT, related_name="trip_stops")
    sequence = models.PositiveIntegerField("orden", validators=[MinValueValidator(1)])
    scheduled_at = models.DateTimeField("horario programado", validators=[validate_aware_datetime])
    allows_boarding = models.BooleanField("permite subir")
    allows_alighting = models.BooleanField("permite bajar")

    class Meta:
        verbose_name = "parada del viaje"
        verbose_name_plural = "paradas del viaje"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["trip", "sequence"], name="ops_trip_sequence_unique", violation_error_message="El orden ya existe en el viaje."),
            models.UniqueConstraint(fields=["trip", "stop"], name="ops_trip_stop_unique", violation_error_message="La parada ya existe en el viaje."),
            models.CheckConstraint(condition=Q(sequence__gte=1), name="ops_trip_sequence_positive", violation_error_message="El orden debe ser mayor o igual a 1."),
        ]

    def __str__(self):
        return f"Viaje {self.trip_id} · {self.sequence}: {self.stop.name}"


class TripFare(TimestampedModel):
    trip = models.ForeignKey(Trip, verbose_name="viaje", on_delete=models.PROTECT, related_name="fares")
    origin_stop = models.ForeignKey(TripStop, verbose_name="origen", on_delete=models.PROTECT, related_name="origin_fares")
    destination_stop = models.ForeignKey(TripStop, verbose_name="destino", on_delete=models.PROTECT, related_name="destination_fares")
    seat_category = models.CharField("categoría", max_length=9, choices=SeatCategory.choices)
    amount = models.DecimalField("importe", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField("moneda", max_length=3, default="ARS", validators=[validate_currency])
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        verbose_name = "tarifa del viaje"
        verbose_name_plural = "tarifas del viaje"
        constraints = [
            models.UniqueConstraint(fields=["trip", "origin_stop", "destination_stop", "seat_category"], name="ops_fare_segment_unique", violation_error_message="Ya existe una tarifa para ese viaje, tramo y categoría."),
            models.CheckConstraint(condition=Q(amount__gt=0), name="ops_fare_amount_positive", violation_error_message="El importe debe ser mayor que cero."),
            models.CheckConstraint(condition=~Q(origin_stop=F("destination_stop")), name="ops_fare_distinct_stops", violation_error_message="El origen y el destino deben ser diferentes."),
            models.CheckConstraint(condition=Q(seat_category__in=SeatCategory.values), name="ops_fare_category_valid", violation_error_message="La categoría no es válida."),
        ]

    def __str__(self):
        return f"Viaje {self.trip_id} · {self.origin_stop.stop.name} → {self.destination_stop.stop.name} · {self.get_seat_category_display()}: {self.currency} {self.amount}"

    def clean_fields(self, exclude=None):
        if "amount" not in (exclude or ()) and isinstance(self.amount, float):
            raise ValidationError({"amount": "Usá Decimal para el importe, nunca float."})
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        stops = {item.pk: item for item in TripStop.objects.filter(
            pk__in=[self.origin_stop_id, self.destination_stop_id]
        )}
        origin, destination = stops.get(self.origin_stop_id), stops.get(self.destination_stop_id)
        errors = {}
        for field, stop in [("origin_stop", origin), ("destination_stop", destination)]:
            if stop is not None and stop.trip_id != self.trip_id:
                errors[field] = "La parada debe pertenecer al viaje indicado."
        if origin and destination:
            if origin.pk == destination.pk or origin.sequence >= destination.sequence:
                errors["destination_stop"] = "El destino debe ser una parada posterior y distinta del origen."
            if not origin.allows_boarding:
                errors["origin_stop"] = "El origen debe permitir subir pasajeros."
            if not destination.allows_alighting:
                errors["destination_stop"] = "El destino debe permitir bajar pasajeros."
        if errors:
            raise ValidationError(errors)
