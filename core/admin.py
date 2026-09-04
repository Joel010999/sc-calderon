from django.contrib import admin
from .models import Ruta, Parada, Bus, Salida, Reserva, ReservaItem

class ParadaInline(admin.TabularInline):
    model = Parada
    extra = 1

@admin.register(Ruta)
class RutaAdmin(admin.ModelAdmin):
    list_display = ('nombre',)
    search_fields = ('nombre',)
    inlines = [ParadaInline]

@admin.register(Parada)
class ParadaAdmin(admin.ModelAdmin):
    list_display = ('ruta', 'nombre', 'orden')
    list_filter = ('ruta',)
    search_fields = ('nombre',)
    ordering = ('ruta', 'orden')

@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ('patente',)
    search_fields = ('patente',)

@admin.register(Salida)
class SalidaAdmin(admin.ModelAdmin):
    list_display = ('ruta', 'fecha', 'hora_salida', 'bus', 'estado')
    list_filter = ('estado', 'fecha', 'ruta')
    search_fields = ('ruta__nombre',)
    date_hierarchy = 'fecha'

class ReservaItemInline(admin.TabularInline):
    model = ReservaItem
    extra = 0

@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ('id', 'email_contacto', 'telefono_contacto', 'estado', 'total', 'creado_en')
    list_filter = ('estado', 'creado_en')
    search_fields = ('email_contacto', 'telefono_contacto')
    inlines = [ReservaItemInline]

@admin.register(ReservaItem)
class ReservaItemAdmin(admin.ModelAdmin):
    list_display = ('reserva', 'pasajero_nombre', 'pasajero_dni', 'salida', 'asiento_id')
    list_filter = ('salida__fecha', 'salida__ruta')
    search_fields = ('pasajero_nombre', 'pasajero_dni', 'reserva__email_contacto')
