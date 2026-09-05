from django.shortcuts import render

def home(request):
    return render(request, 'core/base.html')

def login_cliente(request):
    """
    Vista preparada para el login de clientes (pasajeros).
    """
    # TODO: Implementar lógica de autenticación
    return render(request, 'core/base.html') # Usando base.html temporalmente

def registro_cliente(request):
    """
    Vista preparada para el registro de clientes (pasajeros).
    """
    # TODO: Implementar lógica de registro
    return render(request, 'core/base.html') # Usando base.html temporalmente

def checkout(request):
    """
    Vista para procesar la compra.
    Debe permitir compras como invitado o con sesión iniciada.
    """
    # TODO: Implementar checkout (invitado/logueado)
    return render(request, 'core/base.html') # Usando base.html temporalmente

from django.http import JsonResponse
from django.db import connection
from django.db.utils import OperationalError

def health_check(request):
    db_ok = False
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        pass
    
    if db_ok:
        return JsonResponse({'status': 'ok', 'database': 'ok'}, status=200)
    else:
        return JsonResponse({'status': 'error', 'database': 'error'}, status=503)
