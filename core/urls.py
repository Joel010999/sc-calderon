from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.login_cliente, name='login_cliente'),
    path('registro/', views.registro_cliente, name='registro_cliente'),
    path('checkout/', views.checkout, name='checkout'),
]
