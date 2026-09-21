"""Vistas para el portal de clientes: registro, autenticación, recuperación de contraseña y gestión de viajes."""

import hashlib
import json
import logging
import secrets
import urllib.parse
import urllib.request
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_http_methods, require_POST

from sales.models import Booking, BookingStatus
from tickets.models import Ticket, TicketAuditEvent, TicketStatus
from tickets.storage import get_ticket_storage

from .models import (
    BookingClaimToken,
    Customer,
    CustomerBooking,
    CustomerConsent,
    normalize_email,
)
from .services import (
    associate_booking_with_customer,
    claim_booking_with_token,
    get_client_ip,
    get_customer_for_user,
    record_consent,
    register_customer,
)

logger = logging.getLogger(__name__)

def is_customer_account_user(user):
    """Only customer profiles may use the public account; internal staff stay in the panel."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "customer_profile", None)) and not user.is_staff and not user.is_superuser



def is_safe_redirect(url: str, request) -> bool:
    """Valida que una URL de redirección sea local y segura."""
    if not url:
        return False
    return url.startswith("/") and not url.startswith("//")


# ══════════════════════════════════════════════════════════════
# REGISTRO Y AUTENTICACIÓN
# ══════════════════════════════════════════════════════════════

def customer_register(request):
    """Registro de nuevos clientes con correo normalizado y consentimiento explícito auditable."""
    if request.user.is_authenticated:
        return redirect("mis_viajes")

    next_url = request.GET.get("next", "")

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        commercial_consent = request.POST.get("commercial_consent") == "on"
        next_url = request.POST.get("next", next_url)

        if password != password_confirm:
            messages.error(request, "Las contraseñas no coinciden.")
            return render(request, "customers/register.html", {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "commercial_consent": commercial_consent,
                "next": next_url,
            })

        try:
            customer = register_customer(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                commercial_consent=commercial_consent,
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except ValidationError as ve:
            if hasattr(ve, "message_dict"):
                for field, errs in ve.message_dict.items():
                    for err in errs:
                        messages.error(request, err)
            else:
                messages.error(request, str(ve))
            return render(request, "customers/register.html", {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "commercial_consent": commercial_consent,
                "next": next_url,
            })

        # Iniciar sesión automáticamente
        login(request, customer.user, backend="customers.backends.EmailAuthBackend")

        # Si el usuario tiene una reserva reciente en sesión, asociarla automáticamente
        active_held_id = request.session.get("active_held_booking_id")
        if active_held_id:
            try:
                booking = Booking.objects.filter(public_id=active_held_id).first()
                if booking:
                    associate_booking_with_customer(customer, booking)
            except Exception:
                logger.exception("Error al auto-asociar reserva tras registro")

        messages.success(request, "¡Tu cuenta fue creada con éxito! Ya podés ver y gestionar tus viajes.")
        if is_safe_redirect(next_url, request):
            return redirect(next_url)
        return redirect("mis_viajes")

    return render(request, "customers/register.html", {"next": next_url})


def customer_login(request):
    """Inicio de sesión de clientes por correo electrónico normalizado o usuario."""
    if request.user.is_authenticated:
        return redirect("mis_viajes")

    next_url = request.GET.get("next", "")

    if request.method == "POST":
        email_or_username = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        next_url = request.POST.get("next", next_url)

        user = authenticate(request, username=email_or_username, password=password)
        if user is None:
            messages.error(request, "Credenciales incorrectas. Verificá tu correo y contraseña.")
            return render(request, "customers/login.html", {"email": email_or_username, "next": next_url})

        if not user.is_active or user.is_staff or user.is_superuser:
            messages.error(request, "No se pudo iniciar sesi?n como cliente con esas credenciales.")
            return render(request, "customers/login.html", {"email": email_or_username, "next": next_url})

        customer = get_customer_for_user(user)
        if not customer:
            messages.error(request, "No se pudo iniciar sesi?n como cliente con esas credenciales.")
            return render(request, "customers/login.html", {"email": email_or_username, "next": next_url})

        login(request, user, backend="customers.backends.EmailAuthBackend")

        # Auto-asociar reserva activa en sesión si existe
        active_held_id = request.session.get("active_held_booking_id")
        if active_held_id:
            try:
                booking = Booking.objects.filter(public_id=active_held_id).first()
                if booking and not CustomerBooking.objects.filter(booking=booking).exists():
                    associate_booking_with_customer(customer, booking)
            except Exception:
                logger.exception("Error al auto-asociar reserva tras login")

        messages.success(request, "¡Bienvenido/a de nuevo!")
        if is_safe_redirect(next_url, request):
            return redirect(next_url)
        return redirect("mis_viajes")

    return render(request, "customers/login.html", {"next": next_url})


@require_POST
def customer_logout(request):
    """Cierre de sesión del cliente."""
    logout(request)
    messages.info(request, "Cerraste sesión correctamente.")
    return redirect("home")


# ══════════════════════════════════════════════════════════════
# MIS VIAJES, PASAJES Y RECLAMO DE RESERVAS
# ══════════════════════════════════════════════════════════════

@login_required(login_url="login_cliente")
def mis_viajes(request):
    """Panel privado de viajes del cliente."""
    customer = get_customer_for_user(request.user)
    if not customer or request.user.is_staff or request.user.is_superuser:
        return HttpResponseForbidden("Esta secci?n es exclusiva para cuentas de clientes.")

    # Obtener reservas del cliente ordenadas cronológicamente
    customer_bookings = (
        CustomerBooking.objects.filter(customer=customer)
        .select_related("booking")
        .prefetch_related("booking__legs", "booking__passengers")
        .order_by("-booking__created_at")
    )

    # Estructurar información de pasajes y tramos para cada reserva
    bookings_data = []
    for cb in customer_bookings:
        booking = cb.booking
        tickets = []
        if booking.status == BookingStatus.CONFIRMED:
            tickets = list(Ticket.objects.filter(booking=booking, status=TicketStatus.ISSUED).order_by("ticket_code"))

        bookings_data.append({
            "booking": booking,
            "associated_at": cb.created_at,
            "legs": booking.legs.all(),
            "passengers": booking.passengers.all(),
            "tickets": tickets,
            "can_download_tickets": len(tickets) > 0,
        })

    # Obtener estado de consentimiento comercial actual
    latest_commercial_consent = (
        CustomerConsent.objects.filter(
            customer=customer,
            consent_type=CustomerConsent.ConsentType.COMMERCIAL_COMMUNICATIONS,
        )
        .order_by("-recorded_at")
        .first()
    )
    commercial_consent_active = latest_commercial_consent.granted if latest_commercial_consent else False

    return render(request, "customers/mis_viajes.html", {
        "customer": customer,
        "bookings_data": bookings_data,
        "commercial_consent_active": commercial_consent_active,
    })


@login_required(login_url="login_cliente")
@require_POST
def claim_booking_view(request):
    """Permite al cliente asociar una reserva adquirida como invitado mediante su claim token."""
    customer = get_customer_for_user(request.user)
    if not customer:
        messages.error(request, "Perfil de cliente no encontrado.")
        return redirect("mis_viajes")

    raw_token = request.POST.get("claim_token", "").strip()
    if not raw_token:
        messages.error(request, "Ingresá el código de reclamo de tu compra.")
        return redirect("mis_viajes")

    try:
        claim_booking_with_token(customer=customer, raw_token=raw_token)
        messages.success(request, "¡Tu viaje fue asociado exitosamente a tu cuenta!")
    except ValidationError as ve:
        messages.error(request, str(ve))
    except Exception:
        logger.exception("Error al reclamar reserva con token")
        messages.error(request, "Ocurrió un error al procesar el código de reclamo. Verificá los datos ingresados.")

    return redirect("mis_viajes")


@login_required(login_url="login_cliente")
def customer_ticket_download(request, public_id):
    """Descarga segura de pasajes en PDF para clientes autenticados propietarios de la reserva."""
    customer = get_customer_for_user(request.user)
    if not customer:
        return HttpResponseForbidden("No tenés autorización para acceder a este pasaje.")

    ticket = get_object_or_404(Ticket, public_id=public_id)

    # Verificar que la reserva pertenezca a este cliente
    is_owner = CustomerBooking.objects.filter(customer=customer, booking=ticket.booking).exists()
    if not is_owner:
        return HttpResponseForbidden("No tenés autorización para acceder al pasaje de esta reserva.")

    storage = get_ticket_storage()
    if not storage.exists(ticket.pdf_path):
        raise Http404("El archivo de pasaje no fue encontrado en el almacenamiento.")

    # Registrar auditoría de descarga
    TicketAuditEvent.objects.create(
        actor=request.user,
        action=TicketAuditEvent.Action.DOWNLOAD,
        ticket=ticket,
        booking=ticket.booking,
        description=f"Descarga de pasaje {ticket.ticket_code} por el cliente {customer.email}",
        metadata={"customer_id": customer.id, "email": customer.email},
    )

    file_handle = storage.open(ticket.pdf_path, "rb")
    response = FileResponse(file_handle, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="pasaje-{ticket.ticket_code}.pdf"'
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@login_required(login_url="login_cliente")
@require_POST
def customer_consent_update(request):
    """Actualización auditable de las preferencias de consentimiento comercial."""
    customer = get_customer_for_user(request.user)
    if not customer:
        return redirect("mis_viajes")

    granted = request.POST.get("commercial_consent") == "on"
    record_consent(
        customer=customer,
        consent_type=CustomerConsent.ConsentType.COMMERCIAL_COMMUNICATIONS,
        granted=granted,
        ip_address=get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        origin="account",
    )

    if granted:
        messages.success(request, "Preferencia actualizada: recibirás nuestras novedades y ofertas.")
    else:
        messages.info(request, "Preferencia actualizada: cancelaste la recepción de comunicaciones comerciales.")

    return redirect("mis_viajes")


# ══════════════════════════════════════════════════════════════
# RECUPERACIÓN DE CONTRASEÑA POR ENLACE
# ══════════════════════════════════════════════════════════════

def customer_password_reset(request):
    """Solicitud de restablecimiento de contraseña mediante enlace enviado por correo."""
    if request.user.is_authenticated:
        return redirect("mis_viajes")

    if request.method == "POST":
        email_raw = request.POST.get("email", "").strip()
        normalized = normalize_email(email_raw)

        if normalized:
            User = get_user_model()
            user = User.objects.filter(email__iexact=normalized, customer_profile__isnull=False, is_staff=False, is_superuser=False).first()
            if user and user.is_active:
                token = default_token_generator.make_token(user)
                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
                reset_url = request.build_absolute_uri(
                    reverse("password_reset_confirm", kwargs={"uidb64": uidb64, "token": token})
                )

                subject = "Restablecer tu contraseña — Éxodo Viajes"
                body = (
                    f"Hola {user.first_name or 'pasajero'},\n\n"
                    f"Recibimos una solicitud para restablecer tu contraseña en Éxodo Viajes.\n"
                    f"Podés generar una nueva contraseña ingresando al siguiente enlace:\n\n"
                    f"{reset_url}\n\n"
                    f"Este enlace es válido por tiempo limitado. Si vos no solicitaste este cambio, podés ignorar este correo.\n\n"
                    f"Saludos,\nEquipo de Éxodo Viajes"
                )
                try:
                    send_mail(
                        subject,
                        body,
                        settings.DEFAULT_FROM_EMAIL,
                        [user.email],
                        fail_silently=False,
                    )
                except Exception:
                    logger.exception("Error al enviar correo de restablecimiento de contraseña")

        # Siempre redirigir a 'done' para evitar enumeración de correos
        return redirect("password_reset_done")

    return render(request, "customers/password_reset.html")


def customer_password_reset_done(request):
    """Pantalla informativa indicando que el enlace de recuperación fue enviado."""
    return render(request, "customers/password_reset_done.html")


def customer_password_reset_confirm(request, uidb64, token):
    """Verificación del enlace y cambio definitivo de contraseña."""
    User = get_user_model()
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        return render(request, "customers/password_reset_confirm.html", {"valid_link": False})

    if request.method == "POST":
        new_password = request.POST.get("new_password", "")
        new_password_confirm = request.POST.get("new_password_confirm", "")

        if new_password != new_password_confirm:
            messages.error(request, "Las contraseñas no coinciden.")
            return render(request, "customers/password_reset_confirm.html", {"valid_link": True})

        try:
            validate_password(new_password, user=user)
        except ValidationError as ve:
            for err in ve.messages:
                messages.error(request, err)
            return render(request, "customers/password_reset_confirm.html", {"valid_link": True})

        user.set_password(new_password)
        user.save()
        messages.success(request, "¡Tu contraseña fue actualizada exitosamente! Ya podés iniciar sesión.")
        return redirect("password_reset_complete")

    return render(request, "customers/password_reset_confirm.html", {"valid_link": True})


def customer_password_reset_complete(request):
    """Pantalla informativa de confirmación de cambio de contraseña exitoso."""
    return render(request, "customers/password_reset_complete.html")


# ══════════════════════════════════════════════════════════════
# GOOGLE OAUTH CONFIGURABLE POR ENV Y CALLBACK SIMULADO SEGURO
# ══════════════════════════════════════════════════════════════

def google_login(request):
    """Inicia el flujo de autenticación con Google OAuth.
    
    Estrictamente configurable por variables de entorno (GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET).
    En entornos de prueba o desarrollo admite simulación controlada segura.
    """
    state = secrets.token_urlsafe(32)
    request.session["google_oauth_state"] = state
    redirect_uri = request.build_absolute_uri(reverse("google_callback"))

    if getattr(settings, "GOOGLE_OAUTH_ENABLED", False):
        params = {
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": redirect_uri,
            "state": state,
            "access_type": "online",
        }
        google_auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
        return redirect(google_auth_url)

    # Modo de simulación segura para tests o desarrollo
    if getattr(settings, "GOOGLE_OAUTH_SIMULATION_ENABLED", False) or getattr(settings, "DEBUG", False):
        # Permite probar el flujo en testing de manera simulada y segura
        sim_email = request.GET.get("simulated_email", "usuario.google@ejemplo.com")
        sim_code = f"simulated:{sim_email}:sub-{secrets.token_hex(8)}"
        return redirect(f"{redirect_uri}?code={sim_code}&state={state}")

    messages.error(request, "El inicio de sesión con Google no está configurado en este entorno.")
    return redirect("login_cliente")


def google_callback(request):
    """Callback seguro para Google OAuth con protección CSRF por state y soporte de simulación segura."""
    state = request.GET.get("state", "")
    session_state = request.session.get("google_oauth_state", "")

    # Validación CSRF estricta de state
    if not state or not session_state or state != session_state:
        return HttpResponseBadRequest("Estado OAuth no válido o expirado.")

    request.session.pop("google_oauth_state", None)

    code = request.GET.get("code", "")
    if not code:
        error = request.GET.get("error", "Acceso denegado.")
        messages.error(request, f"Error al autenticar con Google: {error}")
        return redirect("login_cliente")

    email = None
    google_sub = ""
    first_name = ""
    last_name = ""

    # Caso 1: Callback simulado seguro (para entornos de prueba / desarrollo)
    is_simulation_allowed = getattr(settings, "GOOGLE_OAUTH_SIMULATION_ENABLED", False) or getattr(settings, "DEBUG", False)
    if is_simulation_allowed and code.startswith("simulated:"):
        parts = code.split(":")
        if len(parts) >= 3:
            email = parts[1].strip()
            google_sub = parts[2].strip()
            first_name = "Usuario"
            last_name = "Google"

    # Caso 2: Google OAuth real mediante credenciales configuradas en entorno
    elif getattr(settings, "GOOGLE_OAUTH_ENABLED", False):
        try:
            token_url = "https://oauth2.googleapis.com/token"
            redirect_uri = request.build_absolute_uri(reverse("google_callback"))
            payload = urllib.parse.urlencode({
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }).encode("utf-8")

            req = urllib.request.Request(token_url, data=payload, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                tokens = json.loads(resp.read().decode("utf-8"))

            id_token = tokens.get("id_token")
            access_token = tokens.get("access_token")

            userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
            req_info = urllib.request.Request(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            with urllib.request.urlopen(req_info, timeout=10) as resp:
                info = json.loads(resp.read().decode("utf-8"))

            email = info.get("email")
            google_sub = info.get("sub", "")
            first_name = info.get("given_name", "")
            last_name = info.get("family_name", "")
        except Exception:
            logger.exception("Error al intercambiar token con Google")
            messages.error(request, "No pudimos verificar tus datos con Google. Por favor intentá nuevamente.")
            return redirect("login_cliente")
    else:
        messages.error(request, "El inicio de sesión con Google no está disponible.")
        return redirect("login_cliente")

    if not email:
        messages.error(request, "No se pudo obtener una dirección de correo de Google.")
        return redirect("login_cliente")

    normalized = normalize_email(email)
    User = get_user_model()

    # Buscar usuario existente o crearlo
    user = User.objects.filter(email__iexact=normalized, customer_profile__isnull=False, is_staff=False, is_superuser=False).first()
    if user and (user.is_staff or user.is_superuser or not getattr(user, "customer_profile", None)):
        messages.error(request, "Esta cuenta no puede usarse como cuenta de cliente.")
        return redirect("login_cliente")
    if not user:
        username = normalized[:150]
        if User.objects.filter(username=username).exists():
            username = f"{normalized[:140]}_{secrets.token_hex(4)}"

        user = User.objects.create_user(
            username=username,
            email=normalized,
            first_name=first_name,
            last_name=last_name,
            is_staff=False,
            is_superuser=False,
        )
        user.set_unusable_password()
        user.save()

    customer, _ = Customer.objects.get_or_create(
        user=user,
        defaults={
            "email": normalized,
            "normalized_email": normalized,
            "google_sub": google_sub,
        },
    )
    if google_sub and not customer.google_sub:
        customer.google_sub = google_sub
        customer.save(update_fields=["google_sub"])

    login(request, user, backend="customers.backends.EmailAuthBackend")

    # Auto-asociar reserva activa en sesión si existe
    active_held_id = request.session.get("active_held_booking_id")
    if active_held_id:
        try:
            booking = Booking.objects.filter(public_id=active_held_id).first()
            if booking and not CustomerBooking.objects.filter(booking=booking).exists():
                associate_booking_with_customer(customer, booking)
        except Exception:
            logger.exception("Error al auto-asociar reserva tras login con Google")

    messages.success(request, f"¡Bienvenido/a {user.first_name or user.email}!")
    return redirect("mis_viajes")
