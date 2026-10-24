from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm

from decimal import Decimal
from django.db.models import Sum
from .permissions import can_manage_payments, can_manage_reservations, is_panel_user


def panel_login(request):
    if request.user.is_authenticated:
        if is_panel_user(request.user):
            return redirect('panel:dashboard')
        else:
            return HttpResponseForbidden("No tienes autorización para acceder a este panel.")

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if is_panel_user(user):
                login(request, user)
                return redirect('panel:dashboard')
            else:
                messages.error(request, "No tienes autorización para acceder a este panel.")
        else:
            messages.error(request, "Credenciales inválidas.")
    else:
        form = AuthenticationForm()

    return render(request, 'panel/login.html', {'form': form})

@require_POST
def panel_logout(request):
    logout(request)
    return redirect('panel:login')

@login_required(login_url='panel:login')
def dashboard(request):
    if not is_panel_user(request.user):
        return HttpResponseForbidden("No tienes autorización para acceder a este panel.")

    held_count = 0
    confirmed_reservations_count = 0
    confirmed_sales_count = 0
    confirmed_revenue_total = Decimal("0.00")
    cash_sales_count = 0
    cash_revenue = Decimal("0.00")
    transfer_sales_count = 0
    transfer_revenue = Decimal("0.00")
    pending_transfers_count = 0

    if can_manage_reservations(request.user):
        from sales.models import Booking, BookingStatus
        reservation_scope = Booking.objects.all() if request.user.is_superuser or request.user.groups.filter(name="Administrador").exists() else Booking.objects.filter(seller=request.user)
        held_count = reservation_scope.filter(status=BookingStatus.HELD).count()
        confirmed_reservations_count = reservation_scope.filter(status=BookingStatus.CONFIRMED).count()

    if can_manage_payments(request.user):
        from payments.models import Payment, PaymentMethod, PaymentStatus
        from sales.models import BookingStatus

        # Ventas confirmadas con pago aprobado (sin contar pendientes ni rechazados)
        confirmed_payments = Payment.objects.filter(
            status=PaymentStatus.APPROVED,
            booking__status=BookingStatus.CONFIRMED,
        )
        if not request.user.is_superuser and not request.user.groups.filter(name="Administrador").exists():
            confirmed_payments = confirmed_payments.filter(booking__seller=request.user)
        confirmed_sales_count = confirmed_payments.count()
        confirmed_revenue_total = (
            confirmed_payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        )

        # Desglose Efectivo
        cash_qs = confirmed_payments.filter(method=PaymentMethod.CASH)
        cash_sales_count = cash_qs.count()
        cash_revenue = cash_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        # Desglose Transferencia bancaria
        transfer_qs = confirmed_payments.filter(method=PaymentMethod.BANK_TRANSFER)
        transfer_sales_count = transfer_qs.count()
        transfer_revenue = transfer_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        # Transferencias en revisión
        pending_transfers = Payment.objects.filter(
            method=PaymentMethod.BANK_TRANSFER,
            status=PaymentStatus.UNDER_REVIEW,
        )
        if not request.user.is_superuser and not request.user.groups.filter(name="Administrador").exists():
            pending_transfers = pending_transfers.filter(booking__seller=request.user)
        pending_transfers_count = pending_transfers.count()

        from .report_services import COMMISSION_PER_TICKET
        from sales.models import AssignmentStatus, SeatAssignment
        commissionable_units = SeatAssignment.objects.filter(
            leg__booking_id__in=confirmed_payments.values("booking_id"),
            status=AssignmentStatus.CONFIRMED,
            price__gt=Decimal("0.00"),
        ).count()
        commission_total = COMMISSION_PER_TICKET * commissionable_units
    else:
        commissionable_units = 0
        commission_total = Decimal("0.00")

    return render(request, 'panel/dashboard.html', {
        'held_reservations_count': held_count,
        'confirmed_reservations_count': confirmed_reservations_count,
        'confirmed_sales_count': confirmed_sales_count,
        'confirmed_revenue_total': confirmed_revenue_total,
        'cash_sales_count': cash_sales_count,
        'cash_revenue': cash_revenue,
        'transfer_sales_count': transfer_sales_count,
        'transfer_revenue': transfer_revenue,
        'pending_transfers_count': pending_transfers_count,
        'commissionable_units': commissionable_units,
        'commission_total': commission_total,
        'net_operating_total': confirmed_revenue_total - commission_total,
    })
