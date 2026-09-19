"""Vistas del panel para gestión de pagos manuales y transferencias."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from django.utils import timezone

from payments.exceptions import (
    InvalidPaymentStatusError,
    PaymentDuplicateError,
    PaymentError,
    PaymentVoucherError,
)
from payments.models import Payment, PaymentMethod, PaymentStatus
from payments.services import (
    calculate_booking_total,
    expire_public_transfer_if_expired,
    register_cash_payment,
    register_transfer_payment,
    review_transfer_payment,
    validate_payment_agent,
)
from sales.exceptions import BookingExpiredError, InvalidBookingError
from sales.models import Booking, BookingStatus, normalize_document
from .models import AuditEvent
from .permissions import payments_access


@payments_access()
@require_GET
def payment_list(request):
    """Listado paginado de pagos con filtros y búsqueda."""
    queryset = Payment.objects.select_related(
        "booking", "registered_by", "reviewed_by"
    ).order_by("-created_at", "-pk")

    # Filtro por método
    method_filter = request.GET.get("method", "").strip()
    if method_filter in PaymentMethod.values:
        queryset = queryset.filter(method=method_filter)

    # Filtro por estado
    status_filter = request.GET.get("status", "").strip()
    if status_filter in PaymentStatus.values:
        queryset = queryset.filter(status=status_filter)

    # Filtro por fecha (YYYY-MM-DD)
    date_filter = request.GET.get("date", "").strip()
    if date_filter:
        try:
            parsed_date = date.fromisoformat(date_filter)
            queryset = queryset.filter(created_at__date=parsed_date)
        except ValueError:
            pass

    # Filtro por vendedor / registrador
    seller_filter = request.GET.get("seller", "").strip()
    if seller_filter:
        queryset = queryset.filter(
            Q(registered_by__username=seller_filter) | Q(registered_by_id=seller_filter)
        )

    # Búsqueda unificada: payment public_id, booking public_id, email, documento
    q = request.GET.get("q", "").strip()
    if q:
        query_filters = (
            Q(booking__email__icontains=q)
            | Q(booking__passengers__normalized_document__icontains=normalize_document(q))
            | Q(booking__passengers__document_number__icontains=q)
        )
        try:
            val_uuid = uuid.UUID(q)
            query_filters |= Q(public_id=val_uuid) | Q(booking__public_id=val_uuid)
        except ValueError:
            query_filters |= Q(public_id__icontains=q) | Q(booking__public_id__icontains=q)

        queryset = queryset.filter(query_filters).distinct()

    # Paginación a 20 registros por página
    paginator = Paginator(queryset, 20)
    page_number = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.get_page(1)

    # Construir query string para paginación
    query_params = request.GET.copy()
    if "page" in query_params:
        del query_params["page"]
    preserved_query = query_params.urlencode()

    from django.contrib.auth import get_user_model
    User = get_user_model()
    all_sellers = User.objects.filter(
        groups__name__in=["Vendedor", "Administrador"]
    ).distinct().order_by("username")

    return render(request, "panel/payments/list.html", {
        "page_obj": page_obj,
        "payment_methods": PaymentMethod.choices,
        "payment_statuses": PaymentStatus.choices,
        "selected_method": method_filter,
        "selected_status": status_filter,
        "selected_date": date_filter,
        "selected_seller": seller_filter,
        "search_q": q,
        "all_sellers": all_sellers,
        "preserved_query": preserved_query,
    })


@payments_access()
@require_GET
def pending_transfers(request):
    """Listado de transferencias bancarias pendientes de revisión."""
    now = timezone.now()
    # Expiración oportunista de transferencias pendientes cuya reserva haya vencido
    pending_to_check = Payment.objects.filter(
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.UNDER_REVIEW,
    ).select_related("booking")
    for p in pending_to_check:
        if p.booking.expires_at <= now or p.booking.status == BookingStatus.EXPIRED:
            expire_public_transfer_if_expired(p.booking, now=now)

    transfers = Payment.objects.filter(
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.UNDER_REVIEW,
    ).select_related(
        "booking", "registered_by"
    ).prefetch_related(
        "booking__legs", "booking__passengers"
    ).order_by("created_at", "pk")

    return render(request, "panel/payments/pending_transfers.html", {
        "transfers": transfers,
    })


@payments_access()
@require_GET
def payment_detail(request, public_id):
    """Detalle completo de un pago, con comprobante, auditoría y acciones si está en revisión."""
    payment = get_object_or_404(
        Payment.objects.select_related(
            "booking__seller", "registered_by", "reviewed_by"
        ).prefetch_related(
            "booking__legs__origin_stop__stop",
            "booking__legs__destination_stop__stop",
            "booking__passengers",
            "booking__legs__seat_assignments__seat",
        ),
        public_id=public_id,
    )

    # Expiración oportunista si está en revisión y venció la reserva
    now = timezone.now()
    if payment.status == PaymentStatus.UNDER_REVIEW:
        if payment.booking.expires_at <= now or payment.booking.status == BookingStatus.EXPIRED:
            expire_public_transfer_if_expired(payment.booking, now=now)
            payment.refresh_from_db()

    audit_events = AuditEvent.objects.filter(
        entity_type=payment._meta.label,
        entity_id=str(payment.pk),
    ).select_related("actor").order_by("-created_at", "-pk")

    return render(request, "panel/payments/detail.html", {
        "payment": payment,
        "audit_events": audit_events,
    })


@payments_access()
@require_POST
def transfer_review(request, public_id):
    """Revisa una transferencia bancaria en revisión: aprueba o rechaza."""
    payment = get_object_or_404(Payment.objects.select_related("booking"), public_id=public_id)
    action = request.POST.get("action", "").strip().lower()

    if action == "approve":
        try:
            review_transfer_payment(
                payment_or_id=payment,
                reviewer=request.user,
                approved=True,
            )
            messages.success(
                request,
                f"Transferencia {payment.public_id} aprobada con éxito. La reserva {payment.booking.public_id} ha sido confirmada.",
            )
        except BookingExpiredError as exc:
            messages.error(request, f"No se pudo aprobar la transferencia: {exc}")
        except (InvalidPaymentStatusError, InvalidBookingError, ValidationError) as exc:
            messages.error(request, f"Error al aprobar transferencia: {exc}")
        except Exception as exc:
            messages.error(request, f"Ocurrió un error inesperado al aprobar: {exc}")

    elif action == "reject":
        reason = request.POST.get("rejection_reason", "").strip()
        if not reason:
            messages.error(request, "Debés indicar obligatoriamente el motivo del rechazo.")
            return redirect("panel:payment_detail", public_id=payment.public_id)

        try:
            review_transfer_payment(
                payment_or_id=payment,
                reviewer=request.user,
                approved=False,
                rejection_reason=reason,
            )
            messages.success(
                request,
                f"Transferencia {payment.public_id} rechazada correctamente. Motivo: {reason}",
            )
        except (InvalidPaymentStatusError, ValidationError) as exc:
            messages.error(request, f"Error al rechazar transferencia: {exc}")
        except Exception as exc:
            messages.error(request, f"Ocurrió un error inesperado al rechazar: {exc}")
    else:
        messages.error(request, "Acción de revisión no válida.")

    # Redirigir según contexto (si viene de pending o de detail)
    next_url = request.POST.get("next")
    if next_url == "pending":
        return redirect("panel:pending_transfers")
    return redirect("panel:payment_detail", public_id=payment.public_id)


@payments_access()
@require_POST
def booking_pay_cash(request, public_id):
    """Registra pago en efectivo para una reserva HELD y la confirma atómicamente.

    No confía en importes del formulario; calcula server-side y revalida.
    """
    booking = get_object_or_404(Booking, public_id=public_id)

    if booking.status != BookingStatus.HELD:
        messages.error(
            request,
            f"No se puede registrar pago para una reserva en estado {booking.get_status_display()}.",
        )
        return redirect("panel:booking_detail", public_id=booking.public_id)

    reference = request.POST.get("reference", "").strip()

    try:
        payment = register_cash_payment(
            booking_or_id=booking,
            seller=request.user,
            reference=reference,
        )
        messages.success(
            request,
            f"Pago en efectivo registrado por $ {payment.amount}. La reserva ha sido confirmada exitosamente.",
        )
        return redirect("panel:booking_detail", public_id=booking.public_id)
    except BookingExpiredError as exc:
        messages.error(request, f"No se pudo registrar el pago: {exc}")
    except (PaymentDuplicateError, InvalidBookingError, ValidationError, PaymentError) as exc:
        messages.error(request, f"Error al registrar cobro: {exc}")
    except Exception as exc:
        messages.error(request, f"Ocurrió un error inesperado: {exc}")

    return redirect("panel:booking_detail", public_id=booking.public_id)


@payments_access()
@require_POST
def booking_pay_transfer(request, public_id):
    """Registra la presentación de una transferencia bancaria con comprobante obligatorio.

    No confía en importes del formulario; calcula server-side y revalida.
    """
    booking = get_object_or_404(Booking, public_id=public_id)

    if booking.status != BookingStatus.HELD:
        messages.error(
            request,
            f"No se puede registrar transferencia para una reserva en estado {booking.get_status_display()}.",
        )
        return redirect("panel:booking_detail", public_id=booking.public_id)

    voucher_file = request.FILES.get("voucher")
    if not voucher_file:
        messages.error(request, "El archivo de comprobante es obligatorio para transferencias bancarias.")
        return redirect("panel:booking_detail", public_id=booking.public_id)

    reference = request.POST.get("reference", "").strip()

    try:
        payment = register_transfer_payment(
            booking_or_id=booking,
            seller=request.user,
            voucher=voucher_file,
            reference=reference,
        )
        messages.success(
            request,
            f"Transferencia registrada por $ {payment.amount}. El comprobante quedó en estado de revisión.",
        )
        return redirect("panel:booking_detail", public_id=booking.public_id)
    except BookingExpiredError as exc:
        messages.error(request, f"No se pudo registrar la transferencia: {exc}")
    except (PaymentDuplicateError, ValidationError, InvalidBookingError, PaymentError) as exc:
        messages.error(request, f"Error al registrar transferencia: {exc}")
    except Exception as exc:
        messages.error(request, f"Ocurrió un error inesperado: {exc}")

    return redirect("panel:booking_detail", public_id=booking.public_id)


@payments_access()
@require_GET
def download_voucher(request, public_id):
    """Descarga autenticada y autorizada de comprobante desde el panel."""
    payment = get_object_or_404(Payment, public_id=public_id)

    if not payment.voucher:
        raise Http404("El pago no posee comprobante adjunto.")

    try:
        file_obj = payment.voucher.open("rb")
    except Exception:
        raise Http404("El archivo no se encuentra en el almacenamiento.")

    ext = Path(payment.voucher.name).suffix.lower()
    filename = f"comprobante_{payment.public_id}{ext}"

    return FileResponse(file_obj, as_attachment=True, filename=filename)
