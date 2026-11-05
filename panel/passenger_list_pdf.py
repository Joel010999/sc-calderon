"""Generador de listado provisional de pasajeros en PDF para salidas operativas.

Este documento no incluye códigos QR, barras ni validaciones de embarque.
Está rotulado como listado provisional y no como manifiesto oficial CNRT.
"""

import io
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from operations.models import Trip
from sales.models import AssignmentStatus, BookingLeg, BookingPassenger, BookingStatus, SeatAssignment


class NumberedCanvas(canvas.Canvas):
    """Canvas de dos pasadas que numera páginas (Página X de Y) y dibuja encabezados/pies repetidos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 7)
        self.setFillColor(colors.HexColor("#6b7280"))

        # Encabezado repetido a partir de la página 2
        if self._pageNumber > 1:
            header_text = (
                "SC VIAJES — LISTADO PROVISIONAL DE PASAJEROS "
                "(DOCUMENTO DE USO INTERNO — NO CNRT / NO HOMOLOGADO)"
            )
            self.drawString(36, self._pagesize[1] - 25, header_text)
            self.setStrokeColor(colors.HexColor("#d1d5db"))
            self.setLineWidth(0.5)
            self.line(36, self._pagesize[1] - 28, self._pagesize[0] - 36, self._pagesize[1] - 28)

        # Pie de página en todas las páginas
        self.setFont("Helvetica", 7.5)
        footer_left = (
            "SC Viajes · Listado provisional de pasajeros de uso interno exclusivo · "
            "No homologado / No CNRT · Prohibida su exhibición oficial"
        )
        footer_right = f"Página {self._pageNumber} de {page_count}"
        self.drawString(36, 20, footer_left)
        self.drawRightString(self._pagesize[0] - 36, 20, footer_right)
        self.restoreState()


def get_trip_passengers_data(trip_pk):
    """Recupera pasajeros confirmados del viaje deduplicados y ordenados."""
    trip = Trip.objects.select_related("route", "bus").get(pk=trip_pk)

    # 1. Pasajeros con butaca confirmada
    assignments = (
        SeatAssignment.objects.filter(
            trip_id=trip.pk,
            status=AssignmentStatus.CONFIRMED,
            leg__booking__status=BookingStatus.CONFIRMED,
        )
        .select_related(
            "passenger",
            "leg__booking",
            "leg__origin_stop__stop",
            "leg__destination_stop__stop",
        )
        .order_by("leg__origin_stop__sequence", "seat_number", "passenger__last_name", "passenger__first_name")
    )

    rows = []
    seen_assignment_ids = set()
    mother_seats = {}

    for assignment in assignments:
        if assignment.pk in seen_assignment_ids:
            continue
        seen_assignment_ids.add(assignment.pk)

        pax = assignment.passenger
        mother_seats[pax.pk] = assignment.seat_number

        full_name = f"{pax.last_name}, {pax.first_name}".strip(", ")
        if not full_name:
            full_name = "Sin nombre registrado"

        doc = f"{pax.document_type} {pax.document_number}".strip() or "—"
        cat_disp = assignment.get_category_display()
        seat_disp = f"Butaca {assignment.seat_number} ({cat_disp})"
        origin = assignment.leg.origin_stop.stop.name
        dest = assignment.leg.destination_stop.stop.name
        ref = str(assignment.leg.booking.public_id)[:8].upper()

        rows.append({
            "origin_seq": assignment.leg.origin_stop.sequence,
            "seat_sort": assignment.seat_number,
            "is_minor": False,
            "name": full_name,
            "document": doc,
            "seat": seat_disp,
            "origin": origin,
            "destination": dest,
            "reference": ref,
        })

    # 2. Menores gratuitos sin asiento
    minors = list(
        BookingPassenger.objects.filter(
            booking__status=BookingStatus.CONFIRMED,
            booking__legs__trip_id=trip.pk,
            is_lap_infant=True,
        )
        .select_related("booking", "accompanied_by")
        .distinct()
    )

    legs_by_booking = {
        leg.booking_id: leg
        for leg in BookingLeg.objects.filter(
            booking_id__in={m.booking_id for m in minors},
            trip_id=trip.pk,
        ).select_related("origin_stop__stop", "destination_stop__stop")
    }

    seen_minor_ids = set()
    for minor in minors:
        if minor.pk in seen_minor_ids:
            continue
        seen_minor_ids.add(minor.pk)

        leg = legs_by_booking.get(minor.booking_id)
        if not leg:
            continue

        full_name = f"{minor.last_name}, {minor.first_name}".strip(", ")
        if not full_name:
            full_name = "Sin nombre registrado"

        doc = f"{minor.document_type} {minor.document_number}".strip() or "—"

        mother_seat_num = mother_seats.get(minor.accompanied_by_id)
        if mother_seat_num is not None:
            seat_disp = f"Menor sin asiento (en brazos, butaca {mother_seat_num})"
            sort_seat = mother_seat_num
        else:
            seat_disp = "Menor sin asiento (en brazos)"
            sort_seat = 9999

        origin = leg.origin_stop.stop.name
        dest = leg.destination_stop.stop.name
        ref = str(minor.booking.public_id)[:8].upper()

        rows.append({
            "origin_seq": leg.origin_stop.sequence,
            "seat_sort": sort_seat,
            "is_minor": True,
            "name": full_name,
            "document": doc,
            "seat": seat_disp,
            "origin": origin,
            "destination": dest,
            "reference": ref,
        })

    # Ordenar por parada de subida, luego número de asiento, luego menor y nombre
    rows.sort(key=lambda r: (r["origin_seq"], r["seat_sort"], r["is_minor"], r["name"]))
    return trip, rows


def generate_passenger_list_pdf(trip) -> bytes:
    """Genera el PDF del listado provisional de pasajeros para un viaje."""
    _, rows = get_trip_passengers_data(trip.pk)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
        title=f"Listado Provisional de Pasajeros - Viaje {trip.pk}",
        author="SC Viajes",
        subject=f"Listado provisional de pasajeros viaje #{trip.pk}",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ListTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#111827"),
    )

    badge_style = ParagraphStyle(
        "ListBadge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#b91c1c"),
        alignment=0,
    )

    info_label_style = ParagraphStyle(
        "InfoLabel",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1f2937"),
    )

    th_style = ParagraphStyle(
        "TableHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
    )

    cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#111827"),
    )

    cell_bold_style = ParagraphStyle(
        "TableCellBold",
        parent=cell_style,
        fontName="Helvetica-Bold",
    )

    cell_mono_style = ParagraphStyle(
        "TableCellMono",
        parent=cell_style,
        fontName="Courier",
        fontSize=8,
    )

    empty_style = ParagraphStyle(
        "EmptyStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#6b7280"),
        alignment=1,
    )

    story = []

    # 1. Cabecera principal
    story.append(Paragraph("SC VIAJES — LISTADO PROVISIONAL DE PASAJEROS", title_style))
    story.append(Spacer(1, 3))
    story.append(
        Paragraph(
            "DOCUMENTO DE CONTROL INTERNO — LISTADO PROVISIONAL NO HOMOLOGADO (NO VÁLIDO COMO MANIFIESTO CNRT)",
            badge_style,
        )
    )
    story.append(Spacer(1, 8))

    # 2. Metadatos del viaje
    tz_name = settings.TIME_ZONE
    departure_local = timezone.localtime(trip.departure_at)

    info_data = [
        [
            Paragraph(f"<b>Viaje:</b> #{trip.pk}", info_label_style),
            Paragraph(
                f"<b>Recorrido:</b> {escape(trip.route.name)} ({escape(trip.route.code)})",
                info_label_style,
            ),
            Paragraph(
                f"<b>Fecha y hora de salida:</b> {departure_local.strftime('%d/%m/%Y %H:%M')} ({tz_name})",
                info_label_style,
            ),
        ],
        [
            Paragraph(
                f"<b>Colectivo:</b> {escape(trip.bus.code)} — {escape(trip.bus.display_name)}",
                info_label_style,
            ),
            Paragraph(f"<b>Total pasajeros confirmados:</b> {len(rows)}", info_label_style),
            Paragraph(f"<b>Estado operativo:</b> {escape(trip.get_status_display())}", info_label_style),
        ],
    ]

    info_table = Table(info_data, colWidths=[180, 310, 278])
    info_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(info_table)
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#9ca3af"), spaceAfter=8))

    # 3. Tabla de pasajeros
    # Ancho total disponible = 841.89 - 72 = 769.89 pt
    col_widths = [26, 172, 75, 172, 120, 125, 78]

    table_data = [[
        Paragraph("<b>N°</b>", th_style),
        Paragraph("<b>Pasajero (Apellido, Nombre)</b>", th_style),
        Paragraph("<b>Documento</b>", th_style),
        Paragraph("<b>Butaca / Condición</b>", th_style),
        Paragraph("<b>Subida</b>", th_style),
        Paragraph("<b>Bajada</b>", th_style),
        Paragraph("<b>Reserva</b>", th_style),
    ]]

    if rows:
        for idx, row in enumerate(rows, start=1):
            table_data.append([
                Paragraph(str(idx), cell_style),
                Paragraph(escape(row["name"]), cell_bold_style),
                Paragraph(escape(row["document"]), cell_style),
                Paragraph(escape(row["seat"]), cell_style),
                Paragraph(escape(row["origin"]), cell_style),
                Paragraph(escape(row["destination"]), cell_style),
                Paragraph(escape(row["reference"]), cell_mono_style),
            ])
    else:
        table_data.append([
            Paragraph("No hay pasajeros confirmados para este viaje.", empty_style),
            "", "", "", "", "", ""
        ])

    t_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ]

    if not rows:
        t_style.append(("SPAN", (0, 1), (-1, 1)))
        t_style.append(("TOPPADDING", (0, 1), (-1, 1), 16))
        t_style.append(("BOTTOMPADDING", (0, 1), (-1, 1), 16))
    else:
        for r_idx in range(1, len(table_data)):
            bg = colors.HexColor("#f8fafc") if r_idx % 2 == 0 else colors.white
            t_style.append(("BACKGROUND", (0, r_idx), (-1, r_idx), bg))

    passenger_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    passenger_table.setStyle(TableStyle(t_style))
    story.append(passenger_table)

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()
