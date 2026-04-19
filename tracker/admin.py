from django.contrib import admin

from .models import CatalogItem, LibraryEntry


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
    list_display = ("id", "title", "media_type", "external_id", "created_at")
    list_filter = ("media_type",)
    search_fields = ("title", "external_id")
    ordering = ("title",)
