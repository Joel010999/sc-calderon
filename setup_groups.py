import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sslviajes.settings')
django.setup()

from django.contrib.auth.models import Group

def create_groups():
    Group.objects.get_or_create(name='Admin')
    Group.objects.get_or_create(name='Vendedor')
    print("Grupos creados con éxito.")

if __name__ == '__main__':
    create_groups()
