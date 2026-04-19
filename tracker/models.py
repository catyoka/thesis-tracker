from django.conf import settings
from django.db import models


class CatalogItem(models.Model):
    class MediaType(models.TextChoices):
        ANIME = "ANIME", "Anime"
        MANGA = "MANGA", "Manga"

    external_id = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=255)
    media_type = models.CharField(max_length=16, choices=MediaType.choices)
    description = models.TextField(blank=True)
    cover_image_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["media_type", "title"], name="idx_catalog_type_title"),
        ]
        ordering = ["title"]

    def __str__(self) -> str:
        return f"{self.title} ({self.media_type})"


class LibraryEntry(models.Model):
    class MediaType(models.TextChoices):
        ANIME = "ANIME", "Anime"
        MANGA = "MANGA", "Manga"

    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        WATCHING = "WATCHING", "Watching/Reading"
        COMPLETED = "COMPLETED", "Completed"
        ON_HOLD = "ON_HOLD", "On hold"
        DROPPED = "DROPPED", "Dropped"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="library_entries",
    )
    catalog_item = models.ForeignKey(
        CatalogItem,
        on_delete=models.SET_NULL,
        related_name="library_entries",
        null=True,
        blank=True,
    )
    external_id = models.CharField(max_length=64)
    title = models.CharField(max_length=255)
    media_type = models.CharField(max_length=16, choices=MediaType.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    progress = models.PositiveIntegerField(default=0)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "external_id"], name="uniq_libraryentry_user_external_id"
            )
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="idx_library_user_status"),
        ]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.user} - {self.title} ({self.media_type})"
