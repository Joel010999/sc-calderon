"""
URL configuration for sslviajes project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, include
from core.health import live_check, ready_check
from core.views import health_check

urlpatterns = [
    path('health/live/', live_check, name='health_live'),
    path('health/ready/', ready_check, name='health_ready'),
    path('health/', health_check, name='health_check'),
    path('panel/', include('panel.urls')),
    path('payments/', include('payments.urls')),
    path('tickets/', include('tickets.urls')),
    path('cliente/', include('customers.urls')),
    path('', include('core.urls')),
]

handler400 = 'core.errors.error_400'
handler403 = 'core.errors.error_403'
handler404 = 'core.errors.error_404'
handler500 = 'core.errors.error_500'
