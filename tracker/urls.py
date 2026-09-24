from django.urls import path

from . import views

app_name = "tracker"

urlpatterns = [
    # These are the pages I render for the website.
    path("", views.home_page, name="home"),
    path("profile/", views.profile_page, name="profile"),
    path("profile/edit/", views.profile_edit_page, name="profile_edit"),
    path("users/", views.user_directory_page, name="users"),
    path("users/<str:username>/", views.public_profile_page, name="public_profile"),
    path("users/<str:username>/friend/", views.friend_action, name="friend_action"),
    path("friends/", views.friends_page, name="friends"),
    path("signup/", views.signup_page, name="signup"),
    path("anime/", views.media_catalog_page, {"media_type": "ANIME"}, name="anime_catalog"),
    path("anime/<int:item_id>/", views.media_detail_page, {"media_type": "ANIME"}, name="anime_detail"),
    path("manga/", views.media_catalog_page, {"media_type": "MANGA"}, name="manga_catalog"),
    path("manga/<int:item_id>/", views.media_detail_page, {"media_type": "MANGA"}, name="manga_detail"),
    path("catalog/<int:item_id>/comments/", views.media_comment_action, name="media_comment"),
    path("catalog/<int:item_id>/add/", views.add_catalog_item_to_library, name="catalog_add"),
    path("library/", views.library_page, name="library"),
    # I keep the library API under its own routes.
    path("api/library/", views.api_library_list_create, name="api_library_list_create"),
    path("api/library/<int:entry_id>/", views.api_library_detail, name="api_library_detail"),
]
