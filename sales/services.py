from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from operations.models import Seat, Trip, TripFare, TripStop
from .conf import (
    get_manual_hold_hours,
    get_max_passengers_per_booking,
    get_online_cutoff_minutes,
    get_online_hold_minutes,
)
from .exceptions import BookingExpiredError, InvalidBookingError, SeatUnavailableError
from .models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)
from .validators import validate_aware_datetime


def _is_seat_collision_integrity_error(exc):
    """Determina si un IntegrityError corresponde a una colisión en la asignación de butaca.

    Identifica exclusivamente la violación de la restricción 'sales_active_trip_seat_unique'
    mediante el constraint_name de PostgreSQL o el mensaje exacto de columnas de SQLite.
    """
    cause = getattr(exc, "__cause__", None)
    diag = getattr(cause, "diag", None) if cause is not None else getattr(exc, "diag", None)
    if diag is not None and getattr(diag, "constraint_name", None) == "sales_active_trip_seat_unique":
        return True

    # Mensaje exacto de columnas en SQLite para la restricción condicional
    msg = str(exc).strip().lower()
    if msg in (
        "unique constraint failed: sales_seatassignment.trip_id, sales_seatassignment.seat_id",
        "unique constraint failed: sales_seatassignment.seat_id, sales_seatassignment.trip_id",
    ):
        return True

    return False


def validate_seller(seller):
    if not seller:
        raise ValidationError("Las reservas manuales requieren un vendedor asignado.")
    if not getattr(seller, "is_authenticated", False) or not getattr(seller, "is_active", False):
        raise ValidationError("El vendedor debe ser un usuario activo.")
    is_authorized = (
        getattr(seller, "is_superuser", False)
        or seller.groups.filter(name__in=["Vendedor", "Administrador"]).exists()
    )
    if not is_authorized:
        raise ValidationError("El vendedor debe pertenecer al grupo Vendedor o Administrador, o ser superusuario.")


@transaction.atomic
def release_expired_bookings(now=None, trip_ids=None):
    """Marca como EXPIRED las reservas HELD vencidas y pasa sus butacas a RELEASED.

    Si se proporciona `trip_ids`, acota la expiración exclusivamente a las reservas que
    afectan a esos viajes específicos, evitando bloqueos globales innecesarios.
    Se ejecuta de forma atómica y ordenada por PK sin DISTINCT ni agrupaciones para plena compatibilidad con PostgreSQL.
    """
    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)

    qs = Booking.objects.select_for_update().filter(status=BookingStatus.HELD, expires_at__lte=now)
    if trip_ids is not None:
        booking_ids_in_trips = BookingLeg.objects.filter(trip_id__in=trip_ids).values("booking_id")
        qs = qs.filter(pk__in=booking_ids_in_trips)

    expired_ids = list(
        qs.order_by("pk").values_list("pk", flat=True)
    )
    if not expired_ids:
        return 0

    Booking.objects.filter(pk__in=expired_ids).update(
        status=BookingStatus.EXPIRED,
        updated_at=now,
    )
    SeatAssignment.objects.filter(
        leg__booking_id__in=expired_ids,
        status=AssignmentStatus.HELD,
    ).update(
        status=AssignmentStatus.RELEASED,
        updated_at=now,
    )
    return len(expired_ids)


def get_trip_availability(trip, origin_stop=None, destination_stop=None, category=None, now=None):
    """Consulta la disponibilidad de butacas en un viaje.

    Libera oportunamente reservas vencidas del viaje antes de calcular disponibilidad.
    Una butaca es para un pasajero durante todo el viaje, sin reventa por tramos.
    """
    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)

    release_expired_bookings(now=now, trip_ids=[trip.pk])

    active_seats = list(
        Seat.objects.filter(bus=trip.bus, is_active=True).order_by("number")
    )
    occupied_seat_ids = set(
        SeatAssignment.objects.filter(
            trip=trip,
            status__in=[AssignmentStatus.HELD, AssignmentStatus.CONFIRMED],
        ).values_list("seat_id", flat=True)
    )

    available_seats = [s for s in active_seats if s.pk not in occupied_seat_ids]
    if category:
        available_seats = [s for s in available_seats if s.category == category]

    fares_by_category = {}
    if bool(origin_stop) != bool(destination_stop):
        raise ValidationError("Debe especificar tanto la parada de subida como la de bajada, o ninguna de las dos.")

    if origin_stop and destination_stop:
        if origin_stop.trip_id != trip.pk:
            raise ValidationError("La parada de subida debe pertenecer al viaje indicado.")
        if destination_stop.trip_id != trip.pk:
            raise ValidationError("La parada de bajada debe pertenecer al viaje indicado.")
        if origin_stop.sequence >= destination_stop.sequence:
            raise ValidationError("La parada de bajada debe ser posterior a la de subida.")
        if not origin_stop.allows_boarding:
            raise ValidationError(f"La parada {origin_stop.stop.name} no permite subir pasajeros.")
        if not destination_stop.allows_alighting:
            raise ValidationError(f"La parada {destination_stop.stop.name} no permite bajar pasajeros.")

        fares_qs = TripFare.objects.filter(
            trip=trip,
            origin_stop=origin_stop,
            destination_stop=destination_stop,
            is_active=True,
        )
        fares_by_category = {fare.seat_category: fare.amount for fare in fares_qs}
        available_seats = [s for s in available_seats if s.category in fares_by_category]

    return {
        "trip": trip,
        "total_active_seats": len(active_seats),
        "available_seats": available_seats,
        "available_seat_count": len(available_seats),
        "occupied_seat_count": len(occupied_seat_ids),
        "fares_by_category": fares_by_category,
    }


@transaction.atomic
def create_booking(*, channel, email, phone="", seller=None, legs, now=None):
    """Crea una reserva (ida o ida/vuelta) con sus tramos, pasajeros y butacas.

    Operación completamente atómica con bloqueos ordenados por PK sobre los viajes
    y butacas involucrados. Si falla cualquier tramo o butaca, la transacción se revierte por completo.
    """
    if now is not None:
        validate_aware_datetime(now)
        effective_now = now
    else:
        effective_now = timezone.now()

    if channel not in BookingChannel.values:
        raise ValidationError(f"Canal no válido: {channel}")

    if not email:
        raise ValidationError("El correo de compra es obligatorio.")
    try:
        validate_email(email)
    except ValidationError:
        raise ValidationError("Indicá una dirección de correo electrónico válida.")

    if channel == BookingChannel.ONLINE:
        if seller is not None:
            raise ValidationError("Las reservas online no llevan vendedor asignado.")
    elif channel == BookingChannel.MANUAL:
        validate_seller(seller)

    if not legs or len(legs) not in (1, 2):
        raise ValidationError("Se permiten únicamente reservas de solo ida o ida y vuelta (1 o 2 tramos).")

    if len(legs) == 2:
        leg1_trip = legs[0].get("trip")
        leg2_trip = legs[1].get("trip")
        leg1_trip_pk = leg1_trip.pk if hasattr(leg1_trip, "pk") else leg1_trip
        leg2_trip_pk = leg2_trip.pk if hasattr(leg2_trip, "pk") else leg2_trip
        if leg1_trip_pk == leg2_trip_pk:
            raise ValidationError("Una reserva de dos tramos debe corresponder a viajes distintos.")

    max_passengers = get_max_passengers_per_booking()
    passenger_count = len(legs[0].get("seats", []))
    if passenger_count < 1:
        raise ValidationError("Debe seleccionar al menos una butaca.")
    if passenger_count > max_passengers:
        raise ValidationError(f"El límite máximo de pasajeros por reserva es de {max_passengers}.")

    for idx, leg_data in enumerate(legs, start=1):
        leg_seats = leg_data.get("seats", [])
        if len(leg_seats) != passenger_count:
            raise ValidationError("Cada tramo debe tener exactamente la misma cantidad de butacas que pasajeros.")
        seat_pks = [s.pk if hasattr(s, "pk") else s for s in leg_seats]
        if len(seat_pks) != len(set(seat_pks)):
            raise ValidationError(f"No se puede asignar la misma butaca más de una vez en el tramo {idx}.")

    # 1. Bloqueo determinístico de Viajes por PK para evitar deadlocks
    trip_pks = sorted(list({leg["trip"].pk if hasattr(leg["trip"], "pk") else leg["trip"] for leg in legs}))
    locked_trips = {
        t.pk: t
        for t in Trip.objects.select_for_update().filter(pk__in=trip_pks).order_by("pk")
    }
    if len(locked_trips) != len(trip_pks):
        raise ValidationError("Uno o más viajes indicados ya no existen.")

    # 2. Expiración oportunista inicial acotada a los viajes involucrados
    release_expired_bookings(now=effective_now, trip_ids=trip_pks)

    # 3. Recarga y bloqueo determinístico de Butacas solicitadas por PK
    # Coordina con el panel operativo que bloquea Seat.objects.select_for_update() al editar
    all_seat_pks = sorted(list({
        s.pk if hasattr(s, "pk") else s
        for leg in legs
        for s in leg.get("seats", [])
    }))
    locked_seats = {
        s.pk: s
        for s in Seat.objects.select_for_update().filter(pk__in=all_seat_pks).order_by("pk")
    }
    if len(locked_seats) != len(all_seat_pks):
        raise ValidationError("Una o más butacas indicadas no existen.")

    # Releer el reloj después de adquirir los bloqueos si now no fue inyectado explícitamente en tests,
    # para evitar ventas tardías si hubo espera prolongada antes de adquirir los locks.
    if now is None:
        effective_now = timezone.now()
        release_expired_bookings(now=effective_now, trip_ids=trip_pks)

    if channel == BookingChannel.ONLINE:
        hold_minutes = get_online_hold_minutes()
        expires_at = effective_now + timedelta(minutes=hold_minutes)
    else:
        hold_hours = get_manual_hold_hours()
        expires_at = effective_now + timedelta(hours=hold_hours)

    # 4. Recarga de paradas actuales desde base de datos
    all_stop_pks = set()
    for leg_data in legs:
        orig_val = leg_data.get("origin_stop")
        dest_val = leg_data.get("destination_stop")
        orig_pk = orig_val.pk if hasattr(orig_val, "pk") else orig_val
        dest_pk = dest_val.pk if hasattr(dest_val, "pk") else dest_val
        all_stop_pks.add(orig_pk)
        all_stop_pks.add(dest_pk)

    trip_stops = {
        ts.pk: ts
        for ts in TripStop.objects.filter(pk__in=all_stop_pks).select_related("stop")
    }

    # Validación cronológica y de recorrido entre tramo de ida y vuelta antes de evaluar tarifas/butacas
    if len(legs) == 2:
        leg1_orig_val = legs[0].get("origin_stop")
        leg1_dest_val = legs[0].get("destination_stop")
        leg2_orig_val = legs[1].get("origin_stop")
        leg2_dest_val = legs[1].get("destination_stop")

        ts_orig1 = trip_stops.get(leg1_orig_val.pk if hasattr(leg1_orig_val, "pk") else leg1_orig_val)
        ts_dest1 = trip_stops.get(leg1_dest_val.pk if hasattr(leg1_dest_val, "pk") else leg1_dest_val)
        ts_orig2 = trip_stops.get(leg2_orig_val.pk if hasattr(leg2_orig_val, "pk") else leg2_orig_val)
        ts_dest2 = trip_stops.get(leg2_dest_val.pk if hasattr(leg2_dest_val, "pk") else leg2_dest_val)

        if not ts_orig1 or not ts_dest1 or not ts_orig2 or not ts_dest2:
            raise ValidationError("Una o más paradas indicadas no existen.")

        if ts_orig2.stop_id != ts_dest1.stop_id or ts_dest2.stop_id != ts_orig1.stop_id:
            raise ValidationError("El viaje de vuelta debe invertir las paradas de origen y destino de la ida.")

        if ts_orig2.scheduled_at <= ts_dest1.scheduled_at:
            raise ValidationError("El viaje de vuelta debe salir después de la llegada del viaje de ida.")

    resolved_legs = []
    for idx, leg_data in enumerate(legs, start=1):
        trip_pk = leg_data["trip"].pk if hasattr(leg_data["trip"], "pk") else leg_data["trip"]
        trip = locked_trips[trip_pk]

        orig_val = leg_data.get("origin_stop")
        dest_val = leg_data.get("destination_stop")
        orig_pk = orig_val.pk if hasattr(orig_val, "pk") else orig_val
        dest_pk = dest_val.pk if hasattr(dest_val, "pk") else dest_val
        orig = trip_stops.get(orig_pk)
        dest = trip_stops.get(dest_pk)
        if not orig or not dest:
            raise ValidationError("Una o más paradas indicadas no existen.")

        # Validar estado del viaje: sólo excluye estados iniciados y finales
        if trip.status in [Trip.Status.STARTED, Trip.Status.COMPLETED, Trip.Status.CANCELLED]:
            if channel == BookingChannel.ONLINE:
                raise ValidationError("Las reservas online no están permitidas en viajes iniciados, finalizados o cancelados.")
            else:
                raise ValidationError("Las reservas manuales no están permitidas en viajes iniciados, finalizados o cancelados.")

        # Cierre online a la hora exacta de corte
        if channel == BookingChannel.ONLINE:
            cutoff_minutes = get_online_cutoff_minutes()
            cutoff_time = orig.scheduled_at - timedelta(minutes=cutoff_minutes)
            if effective_now >= cutoff_time:
                raise ValidationError(f"La venta online cierra {cutoff_minutes} minutos antes del horario de subida.")

        # Validaciones del recorrido según el snapshot TripStop (autoridad del viaje programado)
        if orig.trip_id != trip.pk:
            raise ValidationError("La parada de subida debe pertenecer al viaje indicado.")
        if dest.trip_id != trip.pk:
            raise ValidationError("La parada de bajada debe pertenecer al viaje indicado.")
        if orig.sequence >= dest.sequence:
            raise ValidationError("La parada de bajada debe ser posterior a la de subida.")
        if not orig.allows_boarding:
            raise ValidationError(f"La parada {orig.stop.name} no permite subir pasajeros.")
        if not dest.allows_alighting:
            raise ValidationError(f"La parada {dest.stop.name} no permite bajar pasajeros.")

        # Validar butacas y tarifas actuales
        fares_for_seats = {}
        leg_locked_seats = []
        for raw_seat in leg_data["seats"]:
            s_pk = raw_seat.pk if hasattr(raw_seat, "pk") else raw_seat
            seat = locked_seats[s_pk]
            leg_locked_seats.append(seat)

            if seat.bus_id != trip.bus_id:
                raise ValidationError(f"La butaca {seat.number} no pertenece al colectivo del viaje.")
            if not seat.is_active:
                raise ValidationError(f"La butaca {seat.number} no está activa.")

            is_occupied = SeatAssignment.objects.filter(
                trip=trip,
                seat=seat,
                status__in=[AssignmentStatus.HELD, AssignmentStatus.CONFIRMED],
            ).exists()
            if is_occupied:
                raise SeatUnavailableError(f"La butaca {seat.number} no está disponible en este viaje.")

            fare = TripFare.objects.filter(
                trip=trip,
                origin_stop=orig,
                destination_stop=dest,
                seat_category=seat.category,
                is_active=True,
            ).first()
            if not fare:
                raise ValidationError(
                    f"No existe una tarifa activa para la butaca {seat.number} ({seat.get_category_display()}) "
                    f"en el tramo {orig.stop.name} → {dest.stop.name}."
                )
            fares_for_seats[seat.pk] = fare

        resolved_legs.append({
            "trip": trip,
            "origin_stop": orig,
            "destination_stop": dest,
            "seats": leg_locked_seats,
            "fares_for_seats": fares_for_seats,
        })

    try:
        booking = Booking(
            channel=channel,
            status=BookingStatus.HELD,
            email=email,
            phone=phone,
            seller=seller,
            expires_at=expires_at,
        )
        booking.full_clean()
        booking.save()

        passengers = []
        for pos in range(1, passenger_count + 1):
            passenger = BookingPassenger(booking=booking, position=pos)
            passenger.full_clean()
            passenger.save()
            passengers.append(passenger)

        for seq, r_leg in enumerate(resolved_legs, start=1):
            trip = r_leg["trip"]
            orig = r_leg["origin_stop"]
            dest = r_leg["destination_stop"]

            leg = BookingLeg(
                booking=booking,
                sequence=seq,
                trip=trip,
                origin_stop=orig,
                destination_stop=dest,
                origin_stop_name=orig.stop.name,
                destination_stop_name=dest.stop.name,
                departure_at=orig.scheduled_at,
                arrival_at=dest.scheduled_at,
            )
            leg.full_clean()
            leg.save()

            for passenger, seat in zip(passengers, r_leg["seats"]):
                fare = r_leg["fares_for_seats"][seat.pk]
                assignment = SeatAssignment(
                    leg=leg,
                    passenger=passenger,
                    trip=trip,
                    seat=seat,
                    status=AssignmentStatus.HELD,
                    seat_number=seat.number,
                    category=seat.category,
                    price=fare.amount,
                    currency=fare.currency,
                )
                assignment.full_clean()
                assignment.save()

    except IntegrityError as exc:
        if _is_seat_collision_integrity_error(exc):
            raise SeatUnavailableError("Una o más butacas ya están reservadas o no están disponibles.") from exc
        raise

    return booking


def create_online_booking(*, email, phone="", legs, now=None):
    """Crea una reserva canal ONLINE."""
    return create_booking(
        channel=BookingChannel.ONLINE,
        email=email,
        phone=phone,
        seller=None,
        legs=legs,
        now=now,
    )


def create_manual_booking(*, seller, email, phone="", legs, now=None):
    """Crea una reserva canal MANUAL con vendedor autorizado."""
    return create_booking(
        channel=BookingChannel.MANUAL,
        email=email,
        phone=phone,
        seller=seller,
        legs=legs,
        now=now,
    )


def confirm_booking(booking_or_id, now=None):
    """Confirma una reserva HELD y transiciona sus butacas a CONFIRMED.

    Operación transaccional e idempotente si ya está confirmada; rechaza reservas expiradas o liberadas.
    Bloquea primero la reserva y verifica la expiración dentro de la transacción con el reloj efectivo
    después del lock. Si está vencida, transiciona atómicamente la reserva a EXPIRED y sus asignaciones
    a RELEASED, confirma ese cambio (commit) y sólo después lanza BookingExpiredError para no revertir
    la liberación.
    """
    if now is not None:
        validate_aware_datetime(now)

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    is_expired = False

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking_id)
        effective_now = now if now is not None else timezone.now()

        if booking.status == BookingStatus.CONFIRMED:
            pass
        elif booking.status == BookingStatus.EXPIRED:
            raise BookingExpiredError("La reserva ha expirado y no se puede confirmar.")
        elif booking.status == BookingStatus.RELEASED:
            raise InvalidBookingError("La reserva ha sido liberada y no se puede confirmar.")
        elif booking.status != BookingStatus.HELD:
            raise InvalidBookingError(f"Estado de reserva inválido para confirmación: {booking.status}")
        elif booking.expires_at <= effective_now:
            booking.status = BookingStatus.EXPIRED
            booking.full_clean()
            booking.save(update_fields=["status", "updated_at"])

            SeatAssignment.objects.filter(
                leg__booking=booking,
                status=AssignmentStatus.HELD,
            ).update(
                status=AssignmentStatus.RELEASED,
                updated_at=effective_now,
            )
            is_expired = True
        else:
            booking.status = BookingStatus.CONFIRMED
            booking.confirmed_at = effective_now
            booking.full_clean()
            booking.save(update_fields=["status", "confirmed_at", "updated_at"])

            SeatAssignment.objects.filter(
                leg__booking=booking,
                status=AssignmentStatus.HELD,
            ).update(
                status=AssignmentStatus.CONFIRMED,
                updated_at=effective_now,
            )

    if isinstance(booking_or_id, Booking):
        booking_or_id.status = booking.status
        booking_or_id.confirmed_at = booking.confirmed_at
        booking_or_id.updated_at = booking.updated_at

    if is_expired:
        raise BookingExpiredError("La reserva ha expirado y no se puede confirmar.")

    return booking


@transaction.atomic
def release_booking(booking_or_id, now=None):
    """Libera una reserva HELD y transiciona sus butacas a RELEASED.

    Operación idempotente para reservas ya liberadas.
    Rechaza reservas CONFIRMED con InvalidBookingError para proteger pasajes confirmados.
    """
    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    booking = Booking.objects.select_for_update().get(pk=booking_id)

    if booking.status == BookingStatus.RELEASED:
        if isinstance(booking_or_id, Booking):
            booking_or_id.status = booking.status
            booking_or_id.updated_at = booking.updated_at
        return booking

    if booking.status == BookingStatus.CONFIRMED:
        raise InvalidBookingError("No se puede liberar una reserva confirmada sin una política de cancelación aprobada.")

    if booking.status != BookingStatus.HELD:
        raise InvalidBookingError(f"No se puede liberar una reserva en estado {booking.get_status_display()}.")

    booking.status = BookingStatus.RELEASED
    booking.full_clean()
    booking.save(update_fields=["status", "updated_at"])

    SeatAssignment.objects.filter(
        leg__booking=booking,
        status=AssignmentStatus.HELD,
    ).update(
        status=AssignmentStatus.RELEASED,
        updated_at=now,
    )

    if isinstance(booking_or_id, Booking):
        booking_or_id.status = booking.status
        booking_or_id.updated_at = booking.updated_at

    return booking


@transaction.atomic
def expire_booking(booking_or_id, now=None):
    """Marca individualmente una reserva vencida como EXPIRED y libera sus butacas.

    Mantiene semántica segura: rechaza con InvalidBookingError reservas CONFIRMED o RELEASED,
    sin liberar butacas confirmadas. Si ya está EXPIRED es idempotente.
    """
    if now is None:
        now = timezone.now()
    validate_aware_datetime(now)

    booking_id = booking_or_id.pk if isinstance(booking_or_id, Booking) else booking_or_id
    booking = Booking.objects.select_for_update().get(pk=booking_id)

    if booking.status == BookingStatus.CONFIRMED:
        raise InvalidBookingError("No se puede expirar una reserva confirmada.")

    if booking.status == BookingStatus.RELEASED:
        raise InvalidBookingError("No se puede expirar una reserva que ya ha sido liberada.")

    if booking.status == BookingStatus.EXPIRED:
        if isinstance(booking_or_id, Booking):
            booking_or_id.status = booking.status
            booking_or_id.updated_at = booking.updated_at
        return booking

    if booking.status != BookingStatus.HELD:
        raise InvalidBookingError(f"No se puede expirar una reserva en estado {booking.get_status_display()}.")

    if booking.expires_at <= now:
        booking.status = BookingStatus.EXPIRED
        booking.full_clean()
        booking.save(update_fields=["status", "updated_at"])
        SeatAssignment.objects.filter(
            leg__booking=booking,
            status=AssignmentStatus.HELD,
        ).update(
            status=AssignmentStatus.RELEASED,
            updated_at=now,
        )
    else:
        raise ValidationError("La reserva aún no ha alcanzado su horario de vencimiento.")

    if isinstance(booking_or_id, Booking):
        booking_or_id.status = booking.status
        booking_or_id.updated_at = booking.updated_at

    return booking
