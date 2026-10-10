from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0002_remove_salida_bus_remove_parada_ruta_and_more")]
    operations = [migrations.CreateModel(
        name="AbuseCounter",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("scope", models.CharField(max_length=80)),
            ("identifier_hash", models.CharField(max_length=64)),
            ("window_started_at", models.DateTimeField()),
            ("count", models.PositiveIntegerField(default=0)),
            ("updated_at", models.DateTimeField(auto_now=True)),
        ],
        options={"constraints": [models.UniqueConstraint(fields=("scope", "identifier_hash", "window_started_at"), name="core_abuse_counter_window_unique")], "indexes": [models.Index(fields=("window_started_at",), name="core_abuse_counter_window_idx")]},
    )]
