from django.contrib import admin

from .models import CatalogItem, Friendship, LibraryEntry, UserProfile


@admin.register(LibraryEntry)
class LibraryEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "title",
        "media_type",
        "status",
        "progress",
        "rating",
        "updated_at",
    )
    list_filter = ("media_type", "status", "updated_at")
    search_fields = ("title", "external_id", "user__username", "user__email")
    ordering = ("-updated_at",)


@admin.register(CatalogItem)
class CatalogItemAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "media_type", "average_score", "format", "release_status", "created_at")
    list_filter = ("media_type", "format", "release_status")
    search_fields = ("title", "external_id")
    ordering = ("title",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "updated_at")
    search_fields = ("user__username", "user__email", "bio")
    ordering = ("user__username",)


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ("id", "requester", "addressee", "status", "updated_at")
    list_filter = ("status", "updated_at")
    search_fields = ("requester__username", "addressee__username")
    ordering = ("-updated_at",)
