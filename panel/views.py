from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm

from .permissions import can_manage_reservations, is_panel_user


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
    if can_manage_reservations(request.user):
        from sales.models import Booking, BookingStatus
        held_count = Booking.objects.filter(status=BookingStatus.HELD).count()
    return render(request, 'panel/dashboard.html', {'held_reservations_count': held_count})
