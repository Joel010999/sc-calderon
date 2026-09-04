from django.db import models
from django.conf import settings

class Ruta(models.Model):
    nombre = models.CharField(max_length=255)

    def __str__(self):
        return self.nombre

class Parada(models.Model):
    ruta = models.ForeignKey(Ruta, on_delete=models.CASCADE, related_name='paradas')
    nombre = models.CharField(max_length=255)
    orden = models.PositiveIntegerField()

    class Meta:
        ordering = ['orden']

    def __str__(self):
        return f"{self.ruta.nombre} - {self.nombre}"

class Bus(models.Model):
    patente = models.CharField(max_length=20, unique=True)
    layout_asientos = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.patente

class Salida(models.Model):
    ESTADO_CHOICES = [
        ('programada', 'Programada'),
        ('en_curso', 'En Curso'),
        ('finalizada', 'Finalizada'),
        ('cancelada', 'Cancelada'),
    ]

    ruta = models.ForeignKey(Ruta, on_delete=models.CASCADE, related_name='salidas')
    fecha = models.DateField()
    hora_salida = models.TimeField()
    bus = models.ForeignKey(Bus, on_delete=models.SET_NULL, null=True, related_name='salidas')
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='programada')

    def __str__(self):
        return f"{self.ruta.nombre} - {self.fecha} {self.hora_salida}"

class Reserva(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('pagada', 'Pagada'),
        ('cancelada', 'Cancelada'),
    ]

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reservas')
    email_contacto = models.EmailField()
    telefono_contacto = models.CharField(max_length=50)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    expira_en = models.DateTimeField(null=True, blank=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Reserva {self.id} - {self.email_contacto}"

class ReservaItem(models.Model):
    reserva = models.ForeignKey(Reserva, on_delete=models.CASCADE, related_name='items')
    salida = models.ForeignKey(Salida, on_delete=models.PROTECT, related_name='reserva_items')
    asiento_id = models.CharField(max_length=20)
    parada_subida = models.ForeignKey(Parada, on_delete=models.PROTECT, related_name='subidas')
    parada_bajada = models.ForeignKey(Parada, on_delete=models.PROTECT, related_name='bajadas')
    pasajero_nombre = models.CharField(max_length=255)
    pasajero_dni = models.CharField(max_length=50)

    def __str__(self):
        return f"Item {self.id} - {self.pasajero_nombre}"
