from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.core.management import call_command
from django.db.utils import OperationalError
from io import StringIO

class PanelTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Crear grupos
        self.grupo_admin = Group.objects.create(name='Administrador')
        self.grupo_vendedor = Group.objects.create(name='Vendedor')

        # Crear usuarios
        self.user_comun = User.objects.create_user(username='comun', password='password123')

        self.user_vendedor = User.objects.create_user(username='vendedor', password='password123')
        self.user_vendedor.groups.add(self.grupo_vendedor)

        self.user_admin = User.objects.create_user(username='admin', password='password123')
        self.user_admin.groups.add(self.grupo_admin)

        self.user_staff = User.objects.create_user(username='staff', password='password123', is_staff=True)
        self.user_superuser = User.objects.create_superuser(username='super', password='password123', email='super@mail.com')

    def test_public_page_responds(self):
        response = self.client.get('/', secure=True)
        self.assertEqual(response.status_code, 200)

    def test_health_check(self):
        response = self.client.get('/health/', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'status': 'ok', 'database': 'ok'})

    @patch('core.views.connection.cursor')
    def test_health_check_db_failure(self, mock_cursor):
        mock_cursor.side_effect = OperationalError("Connection failed")

        with self.assertLogs('core.views', level='ERROR') as cm:
            response = self.client.get('/health/', secure=True)
            self.assertEqual(response.status_code, 503)
            self.assertJSONEqual(response.content, {'status': 'error', 'database': 'error'})
            self.assertTrue(any("Database health check failed" in log for log in cm.output))

    def test_admin_is_disabled(self):
        response = self.client.get('/admin/', secure=True)
        self.assertEqual(response.status_code, 404)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('panel:dashboard'), secure=True)
        self.assertRedirects(response, f"{reverse('panel:login')}?next={reverse('panel:dashboard')}", fetch_redirect_response=False)

    def test_common_user_forbidden(self):
        self.client.login(username='comun', password='password123')
        response = self.client.get(reverse('panel:dashboard'), secure=True)
        self.assertEqual(response.status_code, 403)

    def test_vendedor_can_access(self):
        self.client.login(username='vendedor', password='password123')
        response = self.client.get(reverse('panel:dashboard'), secure=True)
        self.assertEqual(response.status_code, 200)

    def test_admin_can_access(self):
        self.client.login(username='admin', password='password123')
        response = self.client.get(reverse('panel:dashboard'), secure=True)
        self.assertEqual(response.status_code, 200)

    def test_staff_can_access(self):
        self.client.login(username='staff', password='password123')
        response = self.client.get(reverse('panel:dashboard'), secure=True)
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access(self):
        self.client.login(username='super', password='password123')
        response = self.client.get(reverse('panel:dashboard'), secure=True)
        self.assertEqual(response.status_code, 200)

    def test_login_rejects_invalid_credentials(self):
        response = self.client.post(reverse('panel:login'), {
            'username': 'admin',
            'password': 'wrongpassword'
        }, secure=True)
        self.assertEqual(response.status_code, 200)
        messages = list(response.context['messages'])
        self.assertTrue(any("Credenciales inválidas" in str(m) for m in messages))

    def test_logout_only_post(self):
        self.client.login(username='admin', password='password123')
        response = self.client.get(reverse('panel:logout'), secure=True)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(reverse('panel:logout'), secure=True)
        self.assertRedirects(response, reverse('panel:login'), fetch_redirect_response=False)

    def test_setup_roles_is_idempotent(self):
        out = StringIO()

        # Primera ejecución
        call_command('setup_roles', stdout=out)
        self.assertIn("El rol ya existe: Administrador", out.getvalue())

        # Eliminar grupos para probar creación
        Group.objects.filter(name__in=['Administrador', 'Vendedor']).delete()
        out = StringIO()
        call_command('setup_roles', stdout=out)
        self.assertIn("Rol creado: Administrador", out.getvalue())

        # Ejecutar de nuevo
        out = StringIO()
        call_command('setup_roles', stdout=out)
        self.assertIn("El rol ya existe: Administrador", out.getvalue())
        self.assertEqual(Group.objects.filter(name='Administrador').count(), 1)
        self.assertEqual(Group.objects.filter(name='Vendedor').count(), 1)

    def test_templates_do_not_use_tailwind_cdn(self):
        response = self.client.get(reverse('panel:login'), secure=True)
        self.assertNotContains(response, 'cdn.tailwindcss.com')
        self.assertTrue(any('panel/css/panel' in str(t) for t in response.content.split()))
