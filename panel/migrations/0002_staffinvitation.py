from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("panel", "0001_initial")]
    operations = [migrations.CreateModel(name="StaffInvitation", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("email", models.EmailField(db_index=True, max_length=254, verbose_name="correo")),
        ("normalized_email", models.EmailField(max_length=254, unique=True, verbose_name="correo normalizado")),
        ("role", models.CharField(choices=[("Administrador", "Administrador"), ("Vendedor", "Vendedor")], max_length=20, verbose_name="rol")),
        ("token_hash", models.CharField(max_length=64, unique=True, verbose_name="hash del token")),
        ("created_at", models.DateTimeField(auto_now_add=True)), ("expires_at", models.DateTimeField()), ("used_at", models.DateTimeField(blank=True, null=True))], options={"ordering": ["-created_at"]})]
