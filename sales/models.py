import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from operations.models import Seat, SeatCategory, Trip, TripStop
from .conf import get_max_passengers_per_booking
from .validators import validate_aware_datetime, validate_currency


class BookingChannel(models.TextChoices):
    ONLINE = "ONLINE", "Online"
    MANUAL = "MANUAL", "Manual"


class BookingStatus(models.TextChoices):
    HELD = "HELD", "Retenida"
    CONFIRMED = "CONFIRMED", "Confirmada"
    EXPIRED = "EXPIRED", "Expirada"
    RELEASED = "RELEASED", "Liberada"


class AssignmentStatus(models.TextChoices):
    HELD = "HELD", "Retenida"
    CONFIRMED = "CONFIRMED", "Confirmada"
    RELEASED = "RELEASED", "Liberada"


class TimestampedModel(models.Model):
    created_at = models.DateTimeField("fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        abstract = True


class Booking(TimestampedModel):
    public_id = models.UUIDField("identificador público", default=uuid.uuid4, editable=False, unique=True, db_index=True)
    channel = models.CharField("canal", max_length=10, choices=BookingChannel.choices)
    status = models.CharField("estado", max_length=10, choices=BookingStatus.choices, default=BookingStatus.HELD)
    email = models.EmailField("correo electrónico")
    phone = models.CharField("teléfono", max_length=50, blank=True, default="")
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="vendedor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="manual_bookings",
    )
    expires_at = models.DateTimeField("fecha de vencimiento", validators=[validate_aware_datetime])
    confirmed_at = models.DateTimeField("fecha de confirmación", null=True, blank=True, validators=[validate_aware_datetime])

    class Meta:
        verbose_name = "reserva"
        verbose_name_plural = "reservas"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(channel__in=BookingChannel.values),
                name="sales_booking_channel_valid",
                violation_error_message="El canal de venta no es válido.",
            ),
            models.CheckConstraint(
                condition=Q(status__in=BookingStatus.values),
                name="sales_booking_status_valid",
                violation_error_message="El estado de la reserva no es válido.",
            ),
            models.CheckConstraint(
                condition=Q(channel=BookingChannel.MANUAL) | Q(seller__isnull=True),
                name="sales_booking_seller_only_manual",
                violation_error_message="Las reservas online no pueden tener vendedor asignado.",
            ),
            models.CheckConstraint(
                condition=~Q(status=BookingStatus.CONFIRMED) | Q(confirmed_at__isnull=False),
                name="sales_booking_confirmed_at_required",
                violation_error_message="Las reservas confirmadas deben registrar fecha de confirmación.",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="sales_bk_status_exp_idx"),
            models.Index(fields=["created_at"], name="sales_bk_created_idx"),
        ]

    def __str__(self):
        return f"Reserva {self.public_id} · {self.get_channel_display()} · {self.get_status_display()}"

    def clean(self):
        super().clean()
        errors = {}
        if self.channel == BookingChannel.ONLINE and self.seller_id is not None:
            errors["seller"] = "Las reservas online no llevan vendedor asignado."
        if self.status == BookingStatus.CONFIRMED and self.confirmed_at is None:
            errors["confirmed_at"] = "Las reservas confirmadas deben registrar fecha de confirmación."
        if errors:
            raise ValidationError(errors)


class BookingLeg(TimestampedModel):
    booking = models.ForeignKey(Booking, verbose_name="reserva", on_delete=models.PROTECT, related_name="legs")
    sequence = models.PositiveSmallIntegerField("secuencia", validators=[MinValueValidator(1), MaxValueValidator(2)])
    trip = models.ForeignKey(Trip, verbose_name="viaje", on_delete=models.PROTECT, related_name="booking_legs")
    origin_stop = models.ForeignKey(TripStop, verbose_name="parada de subida", on_delete=models.PROTECT, related_name="origin_booking_legs")
    destination_stop = models.ForeignKey(TripStop, verbose_name="parada de bajada", on_delete=models.PROTECT, related_name="destination_booking_legs")
    origin_stop_name = models.CharField("nombre de parada de subida", max_length=150)
    destination_stop_name = models.CharField("nombre de parada de bajada", max_length=150)
    departure_at = models.DateTimeField("horario de subida", validators=[validate_aware_datetime])
    arrival_at = models.DateTimeField("horario de bajada", validators=[validate_aware_datetime])

    class Meta:
        verbose_name = "tramo de reserva"
        verbose_name_plural = "tramos de reserva"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "sequence"],
                name="sales_leg_booking_seq_unique",
                violation_error_message="El orden del tramo ya existe en la reserva.",
            ),
            models.CheckConstraint(
                condition=Q(sequence__in=[1, 2]),
                name="sales_leg_sequence_valid",
                violation_error_message="La secuencia del tramo debe ser 1 o 2.",
            ),
            models.CheckConstraint(
                condition=~Q(origin_stop=F("destination_stop")),
                name="sales_leg_distinct_stops",
                violation_error_message="La parada de subida y bajada deben ser diferentes.",
            ),
        ]

    def __str__(self):
        return f"Tramo {self.sequence} · {self.origin_stop_name} → {self.destination_stop_name} ({self.booking.public_id})"

    def clean(self):
        super().clean()
        errors = {}
        if self.origin_stop_id and self.trip_id and self.origin_stop.trip_id != self.trip_id:
            errors["origin_stop"] = "La parada de subida debe pertenecer al viaje indicado."
        if self.destination_stop_id and self.trip_id and self.destination_stop.trip_id != self.trip_id:
            errors["destination_stop"] = "La parada de bajada debe pertenecer al viaje indicado."
        if self.origin_stop_id and self.destination_stop_id:
            if self.origin_stop_id == self.destination_stop_id or self.origin_stop.sequence >= self.destination_stop.sequence:
                errors["destination_stop"] = "La parada de bajada debe ser posterior a la parada de subida."
            if not self.origin_stop.allows_boarding:
                errors["origin_stop"] = "La parada de subida debe permitir el ascenso de pasajeros."
            if not self.destination_stop.allows_alighting:
                errors["destination_stop"] = "La parada de bajada debe permitir el descenso de pasajeros."
        if errors:
            raise ValidationError(errors)


class BookingPassenger(TimestampedModel):
    booking = models.ForeignKey(Booking, verbose_name="reserva", on_delete=models.PROTECT, related_name="passengers")
    position = models.PositiveSmallIntegerField("posición", validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "pasajero de reserva"
        verbose_name_plural = "pasajeros de reserva"
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "position"],
                name="sales_passenger_booking_pos_unique",
                violation_error_message="La posición del pasajero ya existe en la reserva.",
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1),
                name="sales_passenger_pos_positive",
                violation_error_message="La posición del pasajero debe ser mayor o igual a 1.",
            ),
        ]

    def __str__(self):
        return f"Pasajero {self.position} ({self.booking.public_id})"

    def clean(self):
        super().clean()
        max_allowed = get_max_passengers_per_booking()
        if self.position is not None and (self.position < 1 or self.position > max_allowed):
            raise ValidationError({"position": f"La posición debe estar entre 1 y {max_allowed}."})


class SeatAssignment(TimestampedModel):
    leg = models.ForeignKey(BookingLeg, verbose_name="tramo", on_delete=models.PROTECT, related_name="seat_assignments")
    passenger = models.ForeignKey(BookingPassenger, verbose_name="pasajero", on_delete=models.PROTECT, related_name="seat_assignments")
    trip = models.ForeignKey(Trip, verbose_name="viaje", on_delete=models.PROTECT, related_name="seat_assignments")
    seat = models.ForeignKey(Seat, verbose_name="butaca", on_delete=models.PROTECT, related_name="seat_assignments")
    status = models.CharField("estado", max_length=10, choices=AssignmentStatus.choices, default=AssignmentStatus.HELD)
    seat_number = models.PositiveIntegerField("número de butaca", validators=[MinValueValidator(1)])
    category = models.CharField("categoría", max_length=9, choices=SeatCategory.choices)
    price = models.DecimalField("precio", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField("moneda", max_length=3, default="ARS", validators=[validate_currency])

    class Meta:
        verbose_name = "asignación de butaca"
        verbose_name_plural = "asignaciones de butaca"
        constraints = [
            models.UniqueConstraint(
                fields=["leg", "passenger"],
                name="sales_assignment_leg_passenger_unique",
                violation_error_message="El pasajero ya tiene una butaca asignada en este tramo.",
            ),
            models.UniqueConstraint(
                fields=["trip", "seat"],
                condition=Q(status__in=[AssignmentStatus.HELD, AssignmentStatus.CONFIRMED]),
                name="sales_active_trip_seat_unique",
                violation_error_message="La butaca ya está ocupada o retenida en este viaje.",
            ),
            models.CheckConstraint(
                condition=Q(status__in=AssignmentStatus.values),
                name="sales_assignment_status_valid",
                violation_error_message="El estado de la asignación no es válido.",
            ),
            models.CheckConstraint(
                condition=Q(price__gt=0),
                name="sales_assignment_price_positive",
                violation_error_message="El precio debe ser mayor que cero.",
            ),
            models.CheckConstraint(
                condition=Q(seat_number__gte=1),
                name="sales_assignment_seat_num_positive",
                violation_error_message="El número de butaca debe ser mayor o igual a 1.",
            ),
            models.CheckConstraint(
                condition=Q(category__in=SeatCategory.values),
                name="sales_assignment_category_valid",
                violation_error_message="La categoría de butaca no es válida.",
            ),
        ]

    def __str__(self):
        return f"Butaca {self.seat_number} ({self.get_category_display()}) · {self.get_status_display()} · {self.currency} {self.price}"

    def clean_fields(self, exclude=None):
        if "price" not in (exclude or ()) and isinstance(self.price, float):
            raise ValidationError({"price": "Usá Decimal para el importe, nunca float."})
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        errors = {}
        if self.leg_id and self.trip_id:
            if self.leg.trip_id != self.trip_id:
                errors["trip"] = "El viaje de la asignación debe coincidir con el viaje del tramo."
        if self.seat_id and self.trip_id:
            if self.seat.bus_id != self.trip.bus_id:
                errors["seat"] = "La butaca debe pertenecer al colectivo del viaje."
        if self.leg_id and self.passenger_id:
            if self.leg.booking_id != self.passenger.booking_id:
                errors["passenger"] = "El pasajero y el tramo deben pertenecer a la misma reserva."
        if errors:
            raise ValidationError(errors)
