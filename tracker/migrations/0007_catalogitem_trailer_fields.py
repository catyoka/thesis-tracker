from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0006_catalogitem_average_score_catalogitem_chapters_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogitem",
            name="trailer_site",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="trailer_id",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="catalogitem",
            name="trailer_thumbnail_url",
            field=models.URLField(blank=True),
        ),
    ]
