from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group

class Command(BaseCommand):
    help = 'Crea los roles iniciales (Administrador y Vendedor) si no existen'

    def handle(self, *args, **options):
        roles = ['Administrador', 'Vendedor']

        for role_name in roles:
            group, created = Group.objects.get_or_create(name=role_name)
            if created:
                self.stdout.write(self.style.SUCCESS(f'Rol creado: {role_name}'))
            else:
                self.stdout.write(self.style.WARNING(f'El rol ya existe: {role_name}'))

        self.stdout.write(self.style.SUCCESS('Configuración de roles finalizada.'))
