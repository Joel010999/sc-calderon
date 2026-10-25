from django.db import migrations, models

from operations.services import validate_no_trip_fare_conflicts


def consolidate_or_reject_ambiguous_trip_fares(apps, schema_editor):
    """Consolida tarifas idénticas por salida y categoría, o aborta si hay precios ambiguos."""
    TripFare = apps.get_model("operations", "TripFare")
    db_alias = schema_editor.connection.alias
    rows = list(TripFare.objects.using(db_alias).order_by("trip_id", "seat_category", "pk"))
    if not rows:
        return

    validate_no_trip_fare_conflicts(rows)

    seen_keys = set()
    duplicate_pks = []
    for row in rows:
        key = (row.trip_id, row.seat_category)
        if key in seen_keys:
            duplicate_pks.append(row.pk)
        else:
            seen_keys.add(key)

    if duplicate_pks:
        TripFare.objects.using(db_alias).filter(pk__in=duplicate_pks).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0002_trip_completed_at_trip_started_at"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="tripfare",
            name="ops_fare_segment_unique",
        ),
        migrations.RemoveConstraint(
            model_name="tripfare",
            name="ops_fare_distinct_stops",
        ),
        migrations.RunPython(
            consolidate_or_reject_ambiguous_trip_fares,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="tripfare",
            name="origin_stop",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="origin_fares",
                to="operations.tripstop",
                verbose_name="origen",
            ),
        ),
        migrations.AlterField(
            model_name="tripfare",
            name="destination_stop",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="destination_fares",
                to="operations.tripstop",
                verbose_name="destino",
            ),
        ),
        migrations.AddConstraint(
            model_name="tripfare",
            constraint=models.UniqueConstraint(
                fields=("trip", "seat_category"),
                name="ops_fare_trip_category_unique",
                violation_error_message="Ya existe una tarifa para ese viaje y categoría.",
            ),
        ),
    ]
