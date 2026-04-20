from django.urls import path

from . import views

app_name = "tracker"

urlpatterns = [
    # HTML pages
    path("", views.home_page, name="home"),
    path("signup/", views.signup_page, name="signup"),
    path("anime/", views.media_catalog_page, {"media_type": "ANIME"}, name="anime_catalog"),
    path("manga/", views.media_catalog_page, {"media_type": "MANGA"}, name="manga_catalog"),
    path("catalog/<int:item_id>/add/", views.add_catalog_item_to_library, name="catalog_add"),
    path("library/", views.library_page, name="library"),
    # JSON API
    path("api/library/", views.api_library_list_create, name="api_library_list_create"),
    path("api/library/<int:entry_id>/", views.api_library_detail, name="api_library_detail"),
]

