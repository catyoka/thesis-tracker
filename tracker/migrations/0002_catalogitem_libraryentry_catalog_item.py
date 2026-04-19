from django.db import migrations, models
import django.db.models.deletion


def seed_catalog(apps, schema_editor):
    CatalogItem = apps.get_model("tracker", "CatalogItem")
    seed_items = [
        ("anilist:1", "Cowboy Bebop", "ANIME"),
        ("anilist:20", "Naruto", "ANIME"),
        ("anilist:16498", "Attack on Titan", "ANIME"),
        ("anilist:11757", "Sword Art Online", "ANIME"),
        ("anilist:1535", "Death Note", "ANIME"),
        ("anilist:1735", "Naruto: Shippuden", "ANIME"),
        ("anilist:30013", "One Punch Man", "ANIME"),
        ("anilist:9253", "Steins;Gate", "ANIME"),
        ("anilist:164", "Monogatari Series", "ANIME"),
        ("anilist:15125", "Magi: The Labyrinth of Magic", "ANIME"),
        ("anilist:30017", "One Piece", "MANGA"),
        ("anilist:30018", "Berserk", "MANGA"),
        ("anilist:30019", "Vagabond", "MANGA"),
        ("anilist:30020", "Tokyo Ghoul", "MANGA"),
        ("anilist:30021", "Vinland Saga", "MANGA"),
        ("anilist:30022", "Chainsaw Man", "MANGA"),
        ("anilist:30023", "Dandadan", "MANGA"),
        ("anilist:30024", "Blue Lock", "MANGA"),
        ("anilist:30025", "Jujutsu Kaisen", "MANGA"),
        ("anilist:30026", "Haikyuu!!", "MANGA"),
    ]
    for external_id, title, media_type in seed_items:
        CatalogItem.objects.get_or_create(
            external_id=external_id,
            defaults={
                "title": title,
                "media_type": media_type,
                "description": "",
                "cover_image_url": "",
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CatalogItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_id", models.CharField(max_length=64, unique=True)),
                ("title", models.CharField(max_length=255)),
                ("media_type", models.CharField(choices=[("ANIME", "Anime"), ("MANGA", "Manga")], max_length=16)),
                ("description", models.TextField(blank=True)),
                ("cover_image_url", models.URLField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["title"],
                "indexes": [models.Index(fields=["media_type", "title"], name="idx_catalog_type_title")],
            },
        ),
        migrations.AddField(
            model_name="libraryentry",
            name="catalog_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="library_entries",
                to="tracker.catalogitem",
            ),
        ),
        migrations.RunPython(seed_catalog, migrations.RunPython.noop),
    ]
