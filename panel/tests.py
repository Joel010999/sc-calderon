from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse

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

    def test_public_page_responds(self):
        response = self.client.get('/', secure=True)
        self.assertEqual(response.status_code, 200)

    def test_health_check(self):
        response = self.client.get('/health/', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'status': 'ok', 'database': 'ok'})

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
