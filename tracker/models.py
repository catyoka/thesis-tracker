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
    genres = models.JSONField(default=list, blank=True)
    average_score = models.PositiveSmallIntegerField(null=True, blank=True)
    format = models.CharField(max_length=64, blank=True)
    release_status = models.CharField(max_length=64, blank=True)
    episodes = models.PositiveIntegerField(null=True, blank=True)
    chapters = models.PositiveIntegerField(null=True, blank=True)
    volumes = models.PositiveIntegerField(null=True, blank=True)
    season_year = models.PositiveIntegerField(null=True, blank=True)
    site_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["media_type", "title"], name="idx_catalog_type_title"),
        ]
        ordering = ["title"]

    def __str__(self) -> str:
        return f"{self.title} ({self.media_type})"

    @property
    def display_format(self) -> str:
        return self.format.replace("_", " ").title()

    @property
    def display_release_status(self) -> str:
        return self.release_status.replace("_", " ").title()


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tracker_profile",
    )
    avatar = models.ImageField(upload_to="avatars/", blank=True)
    avatar_url = models.URLField(blank=True)
    bio = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def display_avatar_url(self) -> str:
        if self.avatar:
            return self.avatar.url
        return self.avatar_url

    def __str__(self) -> str:
        return f"{self.user} profile"


class Friendship(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_friendships",
    )
    addressee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_friendships",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["requester", "addressee"], name="uniq_friendship_requester_addressee"
            ),
        ]
        indexes = [
            models.Index(fields=["requester", "status"], name="idx_friend_requester_status"),
            models.Index(fields=["addressee", "status"], name="idx_friend_addressee_status"),
        ]

    def __str__(self) -> str:
        return f"{self.requester} -> {self.addressee} ({self.status})"


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
    is_favorite = models.BooleanField(default=False)
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
            models.Index(fields=["user", "is_favorite"], name="idx_library_user_favorite"),
        ]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.user} - {self.title} ({self.media_type})"
