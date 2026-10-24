from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("panel", "0002_staffinvitation")]

    operations = [
        migrations.CreateModel(
            name="StaffProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("dni", models.CharField(blank=True, default="", max_length=30, verbose_name="DNI")),
                ("whatsapp", models.CharField(blank=True, default="", max_length=50, verbose_name="WhatsApp")),
                ("is_owner", models.BooleanField(default=False, help_text="Solo provisionamiento explícito; no editable desde el panel.", verbose_name="capacidad propietaria")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="staff_profile", to=settings.AUTH_USER_MODEL, verbose_name="usuario interno")),
            ],
            options={"verbose_name": "perfil interno", "verbose_name_plural": "perfiles internos"},
        ),
    ]
