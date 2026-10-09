from django.db import migrations, models


def empty_google_sub_to_null(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    Customer.objects.filter(google_sub="").update(google_sub=None)


class Migration(migrations.Migration):
    dependencies = [("customers", "0003_customer_apple_sub")]

    operations = [
        migrations.RunPython(empty_google_sub_to_null, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customer",
            name="google_sub",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=255,
                null=True,
                unique=True,
                verbose_name="identificador de Google",
            ),
        ),
    ]
