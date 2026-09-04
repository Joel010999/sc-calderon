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
