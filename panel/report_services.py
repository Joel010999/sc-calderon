"""Consultas de ventas y balance con alcance server-side."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.utils import timezone

from operations.models import Seat, SeatCategory
from payments.models import Payment, PaymentMethod, PaymentStatus
from sales.models import (
    AssignmentStatus,
    Booking,
    BookingChannel,
    BookingLeg,
    BookingPassenger,
    BookingStatus,
    SeatAssignment,
)


DEFAULT_MAX_RANGE_DAYS = 366
REPORT_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")
COMMISSION_PER_TICKET = Decimal("5000.00")


def get_commissionable_assignments(booking_ids_or_qs, *, seller_id=None):
    """
    Retorna el QuerySet de asignaciones de butaca comisionables:
    - ARS 5.000 por pasajero con tarifa positiva y por tramo vendido.
    - Únicamente venta interna atribuida a usuario (channel=MANUAL, seller__isnull=False).
    - Solo reservas confirmadas con pago aprobado.
    - ONLINE siempre comisión cero, aun con seller asociado.
    - Menores gratuitos sin butaca comisión cero.
    - Excluye pendientes, rechazadas, vencidas y canceladas.
    - Evita duplicaciones por joins, reintentos o pagos mediante distinct().
    """
    qs = SeatAssignment.objects.filter(
        leg__booking_id__in=booking_ids_or_qs,
        leg__booking__status=BookingStatus.CONFIRMED,
        leg__booking__channel=BookingChannel.MANUAL,
        leg__booking__seller__isnull=False,
        leg__booking__payments__status=PaymentStatus.APPROVED,
        status=AssignmentStatus.CONFIRMED,
        price__gt=Decimal("0.00"),
    ).distinct()
    if seller_id is not None:
        qs = qs.filter(leg__booking__seller_id=seller_id)
    return qs


def _local_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _is_admin(actor):
    return bool(actor and actor.is_authenticated and actor.is_active and (
        actor.is_superuser or actor.groups.filter(name="Administrador").exists()
    ))


def build_sales_charts(booking_ids, *, actor, admin_view, leg_ids=None):
    """
    Construye las estructuras de datos para los seis gráficos analíticos de ventas:
    1) Canal web vs interna
    2) Participación por vendedor (solo administración)
    3) Categoría cama vs semicama (pasajeros/tramos con asiento)
    4) Sentido del recorrido (Córdoba → Jujuy vs Jujuy → Córdoba)
    5) Tipo solo ida vs ida/vuelta
    6) Salida concreta identificada por Trip.pk, fecha/hora y colectivo
    """
    total_bookings = len(booking_ids)

    # Pre-cálculo eficiente de personas y menores en falda (1 sola query)
    if total_bookings > 0:
        p_agg = BookingPassenger.objects.filter(booking_id__in=booking_ids).aggregate(
            total=Count("pk"),
            infants=Count("pk", filter=Q(is_lap_infant=True)),
        )
        total_persons = p_agg["total"] or 0
        lap_infants = p_agg["infants"] or 0
    else:
        total_persons = 0
        lap_infants = 0
    seated_persons = total_persons - lap_infants

    # Pre-cálculo eficiente de tramos (1 sola query para gráficos 4, 5 y 6)
    if total_bookings > 0:
        leg_filter = Q(booking_id__in=booking_ids)
        if leg_ids is not None:
            leg_filter &= Q(pk__in=leg_ids)
        all_legs = list(
            BookingLeg.objects.filter(leg_filter).select_related(
                "trip__bus", "trip__route", "origin_stop__stop", "destination_stop__stop"
            )
        )
    else:
        all_legs = []

    # 1. Canal web vs interna
    web_count = 0
    internal_count = 0
    other_channel_count = 0
    if total_bookings > 0:
        channel_rows = Booking.objects.filter(pk__in=booking_ids).values("channel").annotate(cnt=Count("pk"))
        for cr in channel_rows:
            ch = cr["channel"]
            cnt = cr["cnt"]
            if ch == BookingChannel.ONLINE:
                web_count += cnt
            elif ch == BookingChannel.MANUAL:
                internal_count += cnt
            else:
                other_channel_count += cnt

    c1_denom = total_bookings
    c1_items = []
    if c1_denom > 0:
        c1_items.append({
            "key": "web",
            "label": "Web (Online)",
            "count": web_count,
            "percentage": round((web_count / c1_denom) * 100, 1),
            "percentage_exact": (web_count / c1_denom) * 100,
            "color": "#dc2626",
        })
        c1_items.append({
            "key": "internal",
            "label": "Venta interna",
            "count": internal_count,
            "percentage": round((internal_count / c1_denom) * 100, 1),
            "percentage_exact": (internal_count / c1_denom) * 100,
            "color": "#991b1b",
        })
        if other_channel_count > 0:
            c1_items.append({
                "key": "other",
                "label": "Canal no especificado",
                "count": other_channel_count,
                "percentage": round((other_channel_count / c1_denom) * 100, 1),
                "percentage_exact": (other_channel_count / c1_denom) * 100,
                "color": "#64748b",
            })

    c1_note = (
        f"Personas registradas en las compras: {total_persons} "
        f"({seated_persons} con butaca asignada, {lap_infants} menor(es) de 5 años en brazos/falda sin butaca)."
        if total_bookings > 0 else ""
    )

    chart_channel = {
        "id": "canal",
        "title": "1. Canal de venta",
        "subtitle": "Web (online) vs. Venta interna",
        "unit": "Compras confirmadas",
        "denominator": c1_denom,
        "denominator_label": f"Total de compras confirmadas ({c1_denom})",
        "has_data": c1_denom > 0,
        "empty_message": "Sin datos",
        "items": c1_items,
        "notes": c1_note,
    }

    # 2. Participación por vendedor (solo administración)
    chart_seller = None
    if admin_view:
        c2_denom = total_bookings
        c2_items = []
        if c2_denom > 0:
            seller_rows = Booking.objects.filter(pk__in=booking_ids).values(
                "seller_id", "seller__username"
            ).annotate(cnt=Count("pk")).order_by("-cnt", "seller__username")
            palette = ["#b91c1c", "#dc2626", "#ef4444", "#7f1d1d", "#991b1b", "#450a0a", "#f87171", "#64748b"]
            for idx, sr in enumerate(seller_rows):
                username = sr["seller__username"] or "Sin vendedor (Web / No asignado)"
                cnt = sr["cnt"]
                pct = (cnt / c2_denom) * 100
                c2_items.append({
                    "key": str(sr["seller_id"] or "none"),
                    "label": username,
                    "count": cnt,
                    "percentage": round(pct, 1),
                    "percentage_exact": pct,
                    "color": palette[idx % len(palette)],
                })
        chart_seller = {
            "id": "vendedor",
            "title": "2. Participación por vendedor",
            "subtitle": "Distribución de ventas por usuario vendedor (exclusivo administración)",
            "unit": "Compras confirmadas",
            "denominator": c2_denom,
            "denominator_label": f"Total de compras confirmadas ({c2_denom})",
            "has_data": c2_denom > 0,
            "empty_message": "Sin datos",
            "items": c2_items,
            "notes": "Participación sobre compras confirmadas del período. La venta online figura como 'Sin vendedor'.",
        }

    # 3. Categoría cama vs semicama (pasajeros/tramos con asiento)
    c3_items = []
    c3_denom = 0
    c3_note = ""
    if total_bookings > 0:
        assignment_filter = Q(
            leg__booking_id__in=booking_ids,
            leg__booking__status=BookingStatus.CONFIRMED,
            status=AssignmentStatus.CONFIRMED,
        )
        if leg_ids is not None:
            assignment_filter &= Q(leg_id__in=leg_ids)
        confirmed_assignments = SeatAssignment.objects.filter(assignment_filter)
        cat_agg = confirmed_assignments.aggregate(
            cama=Count("pk", filter=Q(category=SeatCategory.CAMA)),
            semicama=Count("pk", filter=Q(category=SeatCategory.SEMI_CAMA)),
            other=Count("pk", filter=~Q(category__in=[SeatCategory.CAMA, SeatCategory.SEMI_CAMA])),
        )
        cama_count = cat_agg["cama"] or 0
        semicama_count = cat_agg["semicama"] or 0
        unknown_cat_count = cat_agg["other"] or 0
        c3_denom = cama_count + semicama_count + unknown_cat_count

        if c3_denom > 0:
            c3_items.append({
                "key": "cama",
                "label": "Cama",
                "count": cama_count,
                "percentage": round((cama_count / c3_denom) * 100, 1),
                "percentage_exact": (cama_count / c3_denom) * 100,
                "color": "#b91c1c",
            })
            c3_items.append({
                "key": "semicama",
                "label": "Semicama",
                "count": semicama_count,
                "percentage": round((semicama_count / c3_denom) * 100, 1),
                "percentage_exact": (semicama_count / c3_denom) * 100,
                "color": "#ef4444",
            })
            if unknown_cat_count > 0:
                c3_items.append({
                    "key": "unknown",
                    "label": "Sin categoría / Desconocida",
                    "count": unknown_cat_count,
                    "percentage": round((unknown_cat_count / c3_denom) * 100, 1),
                    "percentage_exact": (unknown_cat_count / c3_denom) * 100,
                    "color": "#64748b",
                })
        c3_note = (
            f"Menores de 5 años en brazos/falda: {lap_infants} menor(es). "
            "Por normativa comercial y operativa no ocupan butaca ni computan en categorías de asiento."
        )

    chart_category = {
        "id": "categoria",
        "title": "3. Categoría de butaca",
        "subtitle": "Cama vs. Semicama (pasajeros/tramos con asiento)",
        "unit": "Pasajeros/tramos con asiento",
        "denominator": c3_denom,
        "denominator_label": f"Total de pasajeros/tramos con asiento ({c3_denom})",
        "has_data": c3_denom > 0,
        "empty_message": "Sin datos",
        "items": c3_items,
        "notes": c3_note,
    }

    # 4. Sentido del recorrido (usando all_legs en memoria, 0 queries extra)
    c4_items = []
    c4_denom = len(all_legs)
    if c4_denom > 0:
        cba_juj_cnt = 0
        juj_cba_cnt = 0
        other_dir_counts = {}
        for leg in all_legs:
            route = getattr(leg.trip, "route", None) if leg.trip else None
            code = (route.code if route else "") or ""
            name = (route.name if route else "") or ""
            orig_stop = getattr(leg.origin_stop, "stop", None) if leg.origin_stop else None
            dest_stop = getattr(leg.destination_stop, "stop", None) if leg.destination_stop else None
            orig_code = orig_stop.code if orig_stop else ""
            dest_code = dest_stop.code if dest_stop else ""
            orig_name = leg.origin_stop_name or (orig_stop.name if orig_stop else "")
            dest_name = leg.destination_stop_name or (dest_stop.name if dest_stop else "")

            if (
                code == "CBA-JUJ"
                or "Córdoba → Jujuy" in name
                or (orig_code in ("CBA", "JMA") and dest_code in ("GUE", "PER", "PAL", "SSJ"))
                or ("Córdoba" in orig_name and "Jujuy" in dest_name)
            ):
                cba_juj_cnt += 1
            elif (
                code == "JUJ-CBA"
                or "Jujuy → Córdoba" in name
                or (orig_code in ("SSJ", "PAL", "PER", "GUE") and dest_code in ("JMA", "CBA"))
                or ("Jujuy" in orig_name and "Córdoba" in dest_name)
            ):
                juj_cba_cnt += 1
            else:
                label = name if name else f"{orig_name} → {dest_name}".strip(" →") or "Ruta no asignada / Desconocido"
                other_dir_counts[label] = other_dir_counts.get(label, 0) + 1

        c4_items.append({
            "key": "cba_juj",
            "label": "Córdoba → Jujuy",
            "count": cba_juj_cnt,
            "percentage": round((cba_juj_cnt / c4_denom) * 100, 1),
            "percentage_exact": (cba_juj_cnt / c4_denom) * 100,
            "color": "#dc2626",
        })
        c4_items.append({
            "key": "juj_cba",
            "label": "Jujuy → Córdoba",
            "count": juj_cba_cnt,
            "percentage": round((juj_cba_cnt / c4_denom) * 100, 1),
            "percentage_exact": (juj_cba_cnt / c4_denom) * 100,
            "color": "#991b1b",
        })
        for label, cnt in other_dir_counts.items():
            c4_items.append({
                "key": "other",
                "label": label,
                "count": cnt,
                "percentage": round((cnt / c4_denom) * 100, 1),
                "percentage_exact": (cnt / c4_denom) * 100,
                "color": "#64748b",
            })

    chart_direction = {
        "id": "sentido",
        "title": "4. Sentido del recorrido",
        "subtitle": "Orientación del viaje según los tramos vendidos",
        "unit": "Tramos vendidos",
        "denominator": c4_denom,
        "denominator_label": f"Total de tramos vendidos ({c4_denom})",
        "has_data": c4_denom > 0,
        "empty_message": "Sin datos",
        "items": c4_items,
        "notes": "Sentido clasificado a partir del recorrido oficial o del par de paradas de subida y bajada del tramo.",
    }

    # 5. Tipo solo ida vs ida/vuelta (clasificado según los tramos de cada compra confirmada)
    c5_denom = total_bookings
    c5_items = []
    if c5_denom > 0:
        legs_per_booking = dict(
            BookingLeg.objects.filter(booking_id__in=booking_ids)
            .values("booking_id")
            .annotate(cnt=Count("pk"))
            .values_list("booking_id", "cnt")
        )

        one_way = 0
        round_trip = 0
        incomplete = 0
        for bid in booking_ids:
            cnt = legs_per_booking.get(bid, 0)
            if cnt == 1:
                one_way += 1
            elif cnt >= 2:
                round_trip += 1
            else:
                incomplete += 1

        c5_items.append({
            "key": "one_way",
            "label": "Solo ida",
            "count": one_way,
            "percentage": round((one_way / c5_denom) * 100, 1),
            "percentage_exact": (one_way / c5_denom) * 100,
            "color": "#dc2626",
        })
        c5_items.append({
            "key": "round_trip",
            "label": "Ida y vuelta",
            "count": round_trip,
            "percentage": round((round_trip / c5_denom) * 100, 1),
            "percentage_exact": (round_trip / c5_denom) * 100,
            "color": "#7f1d1d",
        })
        if incomplete > 0:
            c5_items.append({
                "key": "incomplete",
                "label": "Histórico incompleto / Sin tramos",
                "count": incomplete,
                "percentage": round((incomplete / c5_denom) * 100, 1),
                "percentage_exact": (incomplete / c5_denom) * 100,
                "color": "#64748b",
            })

    chart_trip_type = {
        "id": "tipo",
        "title": "5. Tipo de viaje",
        "subtitle": "Solo ida vs. Ida y vuelta",
        "unit": "Compras confirmadas",
        "denominator": c5_denom,
        "denominator_label": f"Total de compras confirmadas ({c5_denom})",
        "has_data": c5_denom > 0,
        "empty_message": "Sin datos",
        "items": c5_items,
        "notes": "Compras catalogadas según la cantidad de tramos emitidos en la misma reserva.",
    }

    # 6. Salida concreta identificada por Trip.pk, fecha/hora y colectivo (agrupación en memoria + queries batch)
    c6_items = []
    c6_denom = len(all_legs)
    if c6_denom > 0:
        legs_by_trip = {}
        for leg in all_legs:
            tid = leg.trip_id
            if tid not in legs_by_trip:
                legs_by_trip[tid] = {"trip": leg.trip, "count": 0}
            legs_by_trip[tid]["count"] += 1

        valid_trips = [entry["trip"] for entry in legs_by_trip.values() if entry["trip"]]
        bus_ids = list({t.bus_id for t in valid_trips if t.bus_id})
        trip_ids = [t.pk for t in valid_trips]

        bus_seats_map = dict(
            Seat.objects.filter(bus_id__in=bus_ids, is_active=True)
            .values("bus_id")
            .annotate(cnt=Count("pk"))
            .values_list("bus_id", "cnt")
        ) if bus_ids else {}

        occupied_seats_map = dict(
            SeatAssignment.objects.filter(
                leg__booking_id__in=booking_ids,
                leg__in=all_legs,
                trip_id__in=trip_ids,
                status=AssignmentStatus.CONFIRMED,
            )
            .values("trip_id")
            .annotate(cnt=Count("pk", distinct=True))
            .values_list("trip_id", "cnt")
        ) if trip_ids else {}

        infants_per_trip_map = dict(
            BookingPassenger.objects.filter(
                booking__legs__in=all_legs,
                booking__legs__trip_id__in=trip_ids,
                booking_id__in=booking_ids,
                is_lap_infant=True,
            )
            .values("booking__legs__trip_id")
            .annotate(cnt=Count("pk", distinct=True))
            .values_list("booking__legs__trip_id", "cnt")
        ) if trip_ids else {}

        palette = ["#dc2626", "#b91c1c", "#991b1b", "#7f1d1d", "#ef4444", "#f87171", "#450a0a"]
        sorted_trips = sorted(legs_by_trip.items(), key=lambda x: (-x[1]["count"], str(x[0])))

        for idx, (tid, entry) in enumerate(sorted_trips):
            trip = entry["trip"]
            cnt = entry["count"]
            pct = (cnt / c6_denom) * 100 if c6_denom > 0 else 0.0

            if trip is None:
                label = "Salida no registrada / Histórico incompleto"
                dep_str = "Fecha desconocida"
                bus_code = "Sin colectivo"
                occ_note = "Capacidad no disponible"
                inf_note = ""
            else:
                bus_code = trip.bus.code if trip.bus else "Sin colectivo asignado"
                local_dep = timezone.localtime(trip.departure_at, REPORT_TIMEZONE)
                dep_str = local_dep.strftime("%d/%m/%Y %H:%M")
                route_str = f" · {trip.route.name}" if trip.route else ""
                label = f"Viaje #{trip.pk} · {dep_str} · Colectivo {bus_code}{route_str}"

                if trip.bus and trip.bus_id in bus_seats_map:
                    active_seats = bus_seats_map[trip.bus_id]
                    if active_seats > 0:
                        total_occupied = occupied_seats_map.get(trip.pk, 0)
                        occ_pct = (total_occupied / active_seats) * 100
                        occ_note = f"Ocupación del colectivo: {total_occupied}/{active_seats} butacas ({occ_pct:.1f}%)"
                    else:
                        occ_note = "Capacidad no configurada (no inventar ocupación)"
                else:
                    occ_note = "Sin colectivo asignado (no inventar ocupación)"

                inf_count = infants_per_trip_map.get(trip.pk, 0)
                inf_note = f"{inf_count} menor(es) en brazos sin butaca" if inf_count > 0 else ""

            c6_items.append({
                "key": str(tid or "none"),
                "label": label,
                "trip_pk": trip.pk if trip else None,
                "departure_str": dep_str,
                "bus_code": bus_code,
                "count": cnt,
                "percentage": round(pct, 1),
                "percentage_exact": pct,
                "color": palette[idx % len(palette)],
                "occupancy_note": occ_note,
                "infants_note": inf_note,
            })

    chart_departure = {
        "id": "salida",
        "title": "6. Salida concreta",
        "subtitle": "Identificada individualmente por viaje, fecha/hora y colectivo (sin consolidar refuerzos)",
        "unit": "Tramos vendidos",
        "denominator": c6_denom,
        "denominator_label": f"Total de tramos vendidos ({c6_denom})",
        "has_data": c6_denom > 0,
        "empty_message": "Sin datos",
        "items": c6_items,
        "notes": "Cada refuerzo con mismo horario se desagrega como salida independiente identificada por su colectivo y número de viaje.",
    }

    return {
        "chart_channel": chart_channel,
        "chart_seller": chart_seller,
        "chart_category": chart_category,
        "chart_direction": chart_direction,
        "chart_trip_type": chart_trip_type,
        "chart_departure": chart_departure,
        "_total_persons": total_persons,
    }


def build_sales_report(params, *, actor):
    """Construye primero el alcance del actor y después todos los filtros."""
    start_date = _local_date(params.get("desde"))
    end_date = _local_date(params.get("hasta"))
    trip_start_date = _local_date(params.get("viaje_desde"))
    trip_end_date = _local_date(params.get("viaje_hasta"))
    max_days = int(getattr(settings, "PANEL_REPORTS_MAX_RANGE_DAYS", DEFAULT_MAX_RANGE_DAYS))
    error = ""
    for label, raw_start, raw_end, parsed_start, parsed_end in (
        ("confirmación", params.get("desde"), params.get("hasta"), start_date, end_date),
        ("viaje", params.get("viaje_desde"), params.get("viaje_hasta"), trip_start_date, trip_end_date),
    ):
        if (raw_start and parsed_start is None) or (raw_end and parsed_end is None):
            error = f"La fecha de {label} no es válida."
    for label, first, last in (("confirmación", start_date, end_date), ("viaje", trip_start_date, trip_end_date)):
        if first and last and last < first:
            error = f"La fecha hasta de {label} debe ser igual o posterior a la fecha desde."
        elif first and last and (last - first).days + 1 > max_days:
            error = f"El rango de {label} no puede superar {max_days} días."

    base_qs = Payment.objects.filter(
        status=PaymentStatus.APPROVED,
        booking__status=BookingStatus.CONFIRMED,
    ).select_related("booking", "booking__seller")
    admin_view = _is_admin(actor)
    if not admin_view:
        base_qs = base_qs.filter(booking__seller_id=getattr(actor, "pk", None))

    tz = REPORT_TIMEZONE
    if start_date and not error:
        base_qs = base_qs.filter(booking__confirmed_at__gte=timezone.make_aware(datetime.combine(start_date, time.min), tz))
    if end_date and not error:
        base_qs = base_qs.filter(booking__confirmed_at__lt=timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz))
    if trip_start_date and not error:
        base_qs = base_qs.filter(booking__legs__trip__departure_at__gte=timezone.make_aware(datetime.combine(trip_start_date, time.min), tz))
    if trip_end_date and not error:
        base_qs = base_qs.filter(booking__legs__trip__departure_at__lt=timezone.make_aware(datetime.combine(trip_end_date + timedelta(days=1), time.min), tz))
    if params.get("medio") in PaymentMethod.values:
        base_qs = base_qs.filter(method=params["medio"])
    if params.get("canal") in BookingChannel.values:
        base_qs = base_qs.filter(booking__channel=params["canal"])

    # Solo un administrador puede seleccionar otro vendedor o «sin vendedor».
    seller_value = (params.get("vendedor") or "").strip()
    if admin_view:
        if seller_value == "sin-vendedor":
            base_qs = base_qs.filter(booking__seller__isnull=True)
        elif seller_value.isdigit():
            base_qs = base_qs.filter(booking__seller_id=int(seller_value))

    # El filtro por viaje puede unir dos tramos; distinct evita duplicar pagos.
    base_qs = base_qs.distinct()
    qs = base_qs.annotate(
        passenger_count=Count("booking__passengers", distinct=True),
        leg_count=Count("booking__legs", distinct=True),
        commissionable_count=Count(
            "booking__legs__seat_assignments",
            filter=Q(
                booking__legs__seat_assignments__status=AssignmentStatus.CONFIRMED,
                booking__legs__seat_assignments__price__gt=Decimal("0.00"),
                booking__channel=BookingChannel.MANUAL,
                booking__seller__isnull=False,
            ),
            distinct=True,
        ),
    ).order_by("-booking__confirmed_at", "-pk")
    total = base_qs.aggregate(
        gross=Sum("amount"),
        payments=Count("pk", distinct=True),
        sales=Count("booking_id", distinct=True),
        cash_amount=Sum("amount", filter=Q(method=PaymentMethod.CASH)),
        transfer_amount=Sum("amount", filter=Q(method=PaymentMethod.BANK_TRANSFER)),
    )
    booking_ids = list(base_qs.values_list("booking_id", flat=True).distinct())

    if admin_view:
        if seller_value == "sin-vendedor":
            commissionable = SeatAssignment.objects.none()
        elif seller_value.isdigit():
            commissionable = get_commissionable_assignments(booking_ids, seller_id=int(seller_value))
        else:
            commissionable = get_commissionable_assignments(booking_ids)
    else:
        commissionable = get_commissionable_assignments(booking_ids, seller_id=getattr(actor, "pk", None))

    commissionable_units = commissionable.count()
    commission_total = COMMISSION_PER_TICKET * commissionable_units
    gross = total["gross"] or Decimal("0.00")
    by_departure = commissionable.values("leg__trip__departure_at", "leg__trip__route__code").annotate(
        units=Count("pk"), amount=Sum("price")
    ).order_by("leg__trip__departure_at")
    by_seller = base_qs.values("booking__seller__username").annotate(
        count=Count("pk", distinct=True), amount=Sum("amount")
    ).order_by("booking__seller__username") if admin_view else []
    sellers = get_user_model().objects.filter(
        is_active=True, groups__name="Vendedor"
    ).distinct().order_by("username") if admin_view else []

    leg_qs = BookingLeg.objects.filter(booking_id__in=booking_ids)
    has_trip_filter = False
    if trip_start_date and not error:
        leg_qs = leg_qs.filter(trip__departure_at__gte=timezone.make_aware(datetime.combine(trip_start_date, time.min), tz))
        has_trip_filter = True
    if trip_end_date and not error:
        leg_qs = leg_qs.filter(trip__departure_at__lt=timezone.make_aware(datetime.combine(trip_end_date + timedelta(days=1), time.min), tz))
        has_trip_filter = True
    leg_ids = list(leg_qs.values_list("pk", flat=True)) if has_trip_filter else None

    charts = build_sales_charts(booking_ids, actor=actor, admin_view=admin_view, leg_ids=leg_ids)
    passenger_total = charts.pop("_total_persons", 0)
    leg_total = charts["chart_direction"]["denominator"]

    return {
        "queryset": qs,
        "total_amount": gross,
        "payment_count": total["payments"] or 0,
        "confirmed_sales_count": total["sales"] or 0,
        "passenger_count": passenger_total,
        "leg_count": leg_total,
        "cash_amount": total["cash_amount"] or Decimal("0.00"),
        "transfer_amount": total["transfer_amount"] or Decimal("0.00"),
        "commissionable_units": commissionable_units,
        "commission_total": commission_total,
        "net_operating": gross - commission_total,
        "by_departure": by_departure,
        "error": error,
        "start_date": start_date,
        "end_date": end_date,
        "trip_start_date": trip_start_date,
        "trip_end_date": trip_end_date,
        "by_method": base_qs.values("method").annotate(count=Count("pk", distinct=True), amount=Sum("amount")).order_by("method"),
        "by_channel": base_qs.values("booking__channel").annotate(count=Count("pk", distinct=True), amount=Sum("amount")).order_by("booking__channel"),
        "by_seller": by_seller,
        "sellers": sellers,
        "is_admin": admin_view,
        **charts,
    }
