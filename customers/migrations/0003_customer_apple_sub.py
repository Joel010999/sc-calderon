from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("customers", "0002_add_claim_expiry_and_consent_origin")]
    operations = [migrations.AddField(
        model_name="customer", name="apple_sub", field=models.CharField(
            blank=True, db_index=True, max_length=255, null=True, unique=True,
            verbose_name="identificador de Apple",
        ),
    )]
