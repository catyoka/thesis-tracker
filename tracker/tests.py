import json
import shutil
import tempfile
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .anilist import fetch_media_details
from .models import CatalogItem, Friendship, LibraryEntry, MediaComment, UserProfile


User = get_user_model()


def image_upload(name="avatar.png", size=(640, 320), mode="RGBA", color=(20, 150, 220, 0)):
    buffer = BytesIO()
    image = Image.new(mode, size, color)
    image.save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class AniListIntegrationTests(TestCase):
    def test_media_details_fetches_all_character_pages(self):
        first_page_character = {
            "role": "MAIN",
            "node": {"name": {"full": "First Character"}, "image": {}},
            "voiceActors": [],
        }
        second_page_character = {
            "role": "SUPPORTING",
            "node": {"name": {"full": "Second Page Character"}, "image": {}},
            "voiceActors": [],
        }

        with patch("tracker.anilist._anilist_request") as anilist_request:
            anilist_request.side_effect = [
                {
                    "data": {
                        "Media": {
                            "id": 300,
                            "title": {"english": "Paged Title"},
                            "characters": {
                                "pageInfo": {"currentPage": 1, "hasNextPage": True},
                                "edges": [first_page_character],
                            },
                        }
                    }
                },
                {
                    "data": {
                        "Media": {
                            "characters": {
                                "pageInfo": {"currentPage": 2, "hasNextPage": False},
                                "edges": [second_page_character],
                            }
                        }
                    }
                },
            ]

            media = fetch_media_details(300)

        names = [
            edge["node"]["name"]["full"]
            for edge in media["characters"]["edges"]
        ]
        self.assertEqual(names, ["First Character", "Second Page Character"])
        self.assertEqual(anilist_request.call_count, 2)


class LibraryApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="hamur", password="password123")
        self.client.force_login(self.user)

    def test_manual_entry_can_be_created_and_updated(self):
        response = self.client.post(
            reverse("tracker:api_library_list_create"),
            data=json.dumps(
                {
                    "external_id": "anilist:16498",
                    "title": "Attack on Titan",
                    "media_type": LibraryEntry.MediaType.ANIME,
                    "status": LibraryEntry.Status.PLANNED,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        entry_id = response.json()["result"]["id"]

        response = self.client.patch(
            reverse("tracker:api_library_detail", args=[entry_id]),
            data=json.dumps(
                {
                    "status": LibraryEntry.Status.WATCHING,
                    "progress": 3,
                    "rating": 9,
                    "notes": "First season notes",
                    "is_favorite": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["result"]
        self.assertEqual(payload["status"], LibraryEntry.Status.WATCHING)
        self.assertEqual(payload["progress"], 3)
        self.assertEqual(payload["rating"], 9)
        self.assertEqual(payload["notes"], "First season notes")
        self.assertTrue(payload["is_favorite"])

    def test_duplicate_external_id_is_rejected_for_same_user(self):
        LibraryEntry.objects.create(
            user=self.user,
            external_id="anilist:1",
            title="Existing",
            media_type=LibraryEntry.MediaType.ANIME,
            status=LibraryEntry.Status.PLANNED,
        )

        response = self.client.post(
            reverse("tracker:api_library_list_create"),
            data=json.dumps(
                {
                    "external_id": "anilist:1",
                    "title": "Duplicate",
                    "media_type": LibraryEntry.MediaType.ANIME,
                    "status": LibraryEntry.Status.PLANNED,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)

    def test_user_cannot_read_or_edit_another_users_entry(self):
        other = User.objects.create_user(username="other", password="password123")
        entry = LibraryEntry.objects.create(
            user=other,
            external_id="anilist:2",
            title="Private Entry",
            media_type=LibraryEntry.MediaType.MANGA,
            status=LibraryEntry.Status.PLANNED,
        )

        response = self.client.get(reverse("tracker:api_library_detail", args=[entry.id]))

        self.assertEqual(response.status_code, 404)


class LibraryPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="hamur", password="password123")
        self.client.force_login(self.user)

    def test_favorites_filter_can_be_toggled_on_and_off(self):
        LibraryEntry.objects.create(
            user=self.user,
            external_id="anilist:10",
            title="Favorite Title",
            media_type=LibraryEntry.MediaType.ANIME,
            status=LibraryEntry.Status.WATCHING,
            is_favorite=True,
        )
        LibraryEntry.objects.create(
            user=self.user,
            external_id="anilist:11",
            title="Regular Title",
            media_type=LibraryEntry.MediaType.MANGA,
            status=LibraryEntry.Status.PLANNED,
            is_favorite=False,
        )

        response = self.client.get(reverse("tracker:library"), {"favorite": "1"})
        self.assertContains(response, "Favorite Title")
        self.assertNotContains(response, "Regular Title")

        response = self.client.get(reverse("tracker:library"))
        self.assertContains(response, "Favorite Title")
        self.assertContains(response, "Regular Title")

    def test_library_status_tabs_include_rewatching_and_filter_entries(self):
        LibraryEntry.objects.create(
            user=self.user,
            external_id="anilist:12",
            title="Rewatch Title",
            media_type=LibraryEntry.MediaType.ANIME,
            status=LibraryEntry.Status.REWATCHING,
        )
        LibraryEntry.objects.create(
            user=self.user,
            external_id="anilist:13",
            title="Planning Title",
            media_type=LibraryEntry.MediaType.MANGA,
            status=LibraryEntry.Status.PLANNED,
        )

        response = self.client.get(reverse("tracker:library"))
        self.assertContains(response, "Rewatching / Rereading")

        response = self.client.get(reverse("tracker:library"), {"status": LibraryEntry.Status.REWATCHING})
        self.assertContains(response, "Rewatch Title")
        self.assertNotContains(response, "Planning Title")

    def test_catalog_add_creates_or_updates_library_entry(self):
        item = CatalogItem.objects.create(
            external_id="anilist:120",
            title="Catalog Title",
            media_type=CatalogItem.MediaType.ANIME,
        )

        response = self.client.post(
            reverse("tracker:catalog_add", args=[item.id]),
            {"status": LibraryEntry.Status.WATCHING},
        )

        self.assertEqual(response.status_code, 302)
        entry = LibraryEntry.objects.get(user=self.user, external_id=item.external_id)
        self.assertEqual(entry.status, LibraryEntry.Status.WATCHING)

    def test_catalog_fetch_caches_richer_metadata(self):
        with patch("tracker.views.fetch_media_catalog") as fetch_media_catalog:
            fetch_media_catalog.return_value = [
                {
                    "external_id": "anilist:1",
                    "title": "Richer Catalog Title",
                    "media_type": CatalogItem.MediaType.ANIME,
                    "description": "A sharper catalog record.",
                    "cover_image_url": "https://img.example/cover.jpg",
                    "genres": ["Action", "Drama"],
                    "tags": ["Female Protagonist", "Swordplay"],
                    "average_score": 91,
                    "format": "TV",
                    "release_status": "FINISHED",
                    "episodes": 24,
                    "chapters": None,
                    "volumes": None,
                    "season_year": 2024,
                    "site_url": "https://anilist.co/anime/1",
                    "trailer_site": "youtube",
                    "trailer_id": "trailer_123",
                    "trailer_thumbnail_url": "https://img.example/trailer.jpg",
                }
            ]

            response = self.client.get(reverse("tracker:anime_catalog"))

        self.assertContains(response, "Richer Catalog Title")
        self.assertContains(response, "Score 91/100")
        self.assertContains(response, "Action")
        item = CatalogItem.objects.get(external_id="anilist:1")
        self.assertEqual(item.genres, ["Action", "Drama"])
        self.assertEqual(item.tags, ["Female Protagonist", "Swordplay"])
        self.assertEqual(item.average_score, 91)
        self.assertEqual(item.episodes, 24)
        self.assertEqual(item.site_url, "https://anilist.co/anime/1")
        self.assertEqual(item.trailer_site, "youtube")
        self.assertEqual(item.trailer_id, "trailer_123")
        self.assertEqual(item.trailer_thumbnail_url, "https://img.example/trailer.jpg")

    def test_catalog_filters_can_use_genre_and_tag(self):
        with patch("tracker.views.fetch_media_catalog") as fetch_media_catalog:
            fetch_media_catalog.return_value = [
                {
                    "external_id": "anilist:50",
                    "title": "Filtered Action Title",
                    "media_type": CatalogItem.MediaType.ANIME,
                    "description": "Filtered by AniList.",
                    "cover_image_url": "",
                    "genres": ["Action"],
                    "tags": ["Female Protagonist"],
                    "average_score": 88,
                    "format": "TV",
                    "release_status": "FINISHED",
                    "episodes": 12,
                    "chapters": None,
                    "volumes": None,
                    "season_year": 2024,
                    "site_url": "",
                    "trailer_site": "",
                    "trailer_id": "",
                    "trailer_thumbnail_url": "",
                }
            ]

            response = self.client.get(
                reverse("tracker:anime_catalog"),
                {"genre": "action", "tag": "female protagonist"},
            )

        fetch_media_catalog.assert_called_once_with(
            CatalogItem.MediaType.ANIME,
            "",
            per_page=50,
            genre="Action",
            tag="Female Protagonist",
        )
        self.assertContains(response, "Filtered Action Title")
        self.assertContains(response, "Female Protagonist")

    def test_catalog_filters_cached_items_when_anilist_is_unavailable(self):
        CatalogItem.objects.create(
            external_id="anilist:51",
            title="Cached Match",
            media_type=CatalogItem.MediaType.ANIME,
            genres=["Action"],
            tags=["Magic"],
        )
        CatalogItem.objects.create(
            external_id="anilist:52",
            title="Cached Miss",
            media_type=CatalogItem.MediaType.ANIME,
            genres=["Romance"],
            tags=["Magic"],
        )

        with patch("tracker.views.fetch_media_catalog", side_effect=RuntimeError):
            response = self.client.get(
                reverse("tracker:anime_catalog"),
                {"genre": "Action", "tag": "Magic"},
            )

        self.assertContains(response, "Cached Match")
        self.assertNotContains(response, "Cached Miss")

    def test_home_recommends_unseen_titles_from_user_genres(self):
        seed = CatalogItem.objects.create(
            external_id="anilist:200",
            title="Seed Favorite",
            media_type=CatalogItem.MediaType.ANIME,
            genres=["Mystery", "Drama"],
            average_score=86,
        )
        recommended = CatalogItem.objects.create(
            external_id="anilist:201",
            title="Genre Match",
            media_type=CatalogItem.MediaType.ANIME,
            genres=["Mystery"],
            average_score=88,
        )
        CatalogItem.objects.create(
            external_id="anilist:202",
            title="Unrelated Popular Title",
            media_type=CatalogItem.MediaType.ANIME,
            genres=["Sports"],
            average_score=99,
        )
        LibraryEntry.objects.create(
            user=self.user,
            catalog_item=seed,
            external_id=seed.external_id,
            title=seed.title,
            media_type=LibraryEntry.MediaType.ANIME,
            status=LibraryEntry.Status.COMPLETED,
            rating=9,
            is_favorite=True,
        )

        response = self.client.get(reverse("tracker:home"))

        self.assertContains(response, "Recommended next")
        self.assertContains(response, recommended.title)
        self.assertContains(response, "Matches Mystery")
        self.assertNotContains(response, "Unrelated Popular Title")

    def test_media_detail_refreshes_cached_metadata(self):
        item = CatalogItem.objects.create(
            external_id="anilist:300",
            title="Old Title",
            media_type=CatalogItem.MediaType.ANIME,
        )
        with patch("tracker.views.fetch_media_details") as fetch_media_details:
            fetch_media_details.return_value = {
                "title": {"english": "Updated Detail Title"},
                "description": "Fresh detail copy.",
                "siteUrl": "https://anilist.co/anime/300",
                "coverImage": {"large": "https://img.example/detail.jpg"},
                "genres": ["Thriller", "Sci-Fi"],
                "tags": [
                    {"name": "Time Skip", "rank": 90, "isAdult": False},
                    {"name": "Adult Only", "rank": 80, "isAdult": True},
                ],
                "averageScore": 92,
                "episodes": 12,
                "format": "TV",
                "status": "FINISHED",
                "seasonYear": 2025,
                "trailer": {
                    "id": "detailTrailer_1",
                    "site": "youtube",
                    "thumbnail": "https://img.example/detail-trailer.jpg",
                },
                "staff": {
                    "edges": [
                        {
                            "role": "Director",
                            "node": {
                                "name": {"full": "Tetsurou Araki"},
                                "image": {"medium": "https://img.example/director.jpg"},
                            },
                        }
                    ]
                },
                "characters": {
                    "edges": [
                        {
                            "role": "MAIN",
                            "node": {
                                "name": {"full": "Levi Ackerman"},
                                "image": {"medium": "https://img.example/levi.jpg"},
                            },
                            "voiceActors": [
                                {
                                    "name": {"full": "Hiroshi Kamiya"},
                                    "image": {"medium": "https://img.example/va.jpg"},
                                }
                            ],
                        },
                        *[
                            {
                                "role": "SUPPORTING",
                                "node": {
                                    "name": {"full": f"Background Character {index}"},
                                    "image": {"medium": f"https://img.example/character-{index}.jpg"},
                                },
                                "voiceActors": [],
                            }
                            for index in range(2, 26)
                        ],
                        *[
                            {
                                "role": "BACKGROUND",
                                "node": {
                                    "name": {"full": f"Extra Character {index}"},
                                    "image": {"medium": f"https://img.example/extra-character-{index}.jpg"},
                                },
                                "voiceActors": [],
                            }
                            for index in range(26, 61)
                        ],
                    ]
                },
            }

            detail_url = reverse("tracker:anime_detail", args=[item.id])
            overview_response = self.client.get(detail_url)
            watch_response = self.client.get(f"{detail_url}?tab=watch")
            characters_response = self.client.get(f"{detail_url}?tab=characters")
            staff_response = self.client.get(f"{detail_url}?tab=staff")
            stats_response = self.client.get(f"{detail_url}?tab=stats")

        self.assertContains(overview_response, "Updated Detail Title")
        self.assertContains(overview_response, "Fresh detail copy.")
        self.assertNotContains(overview_response, "Levi Ackerman")
        self.assertContains(watch_response, "https://www.youtube.com/watch?v=detailTrailer_1")
        self.assertContains(staff_response, "Staff & Cast")
        self.assertContains(staff_response, "Tetsurou Araki")
        self.assertContains(characters_response, "Levi Ackerman")
        self.assertContains(characters_response, "Hiroshi Kamiya")
        self.assertContains(characters_response, "Background Character 25")
        self.assertContains(characters_response, "Extra Character 60")
        self.assertContains(stats_response, "Thriller")
        self.assertContains(stats_response, "Time Skip")
        self.assertNotContains(stats_response, "Adult Only")
        item.refresh_from_db()
        self.assertEqual(item.title, "Updated Detail Title")
        self.assertEqual(item.genres, ["Thriller", "Sci-Fi"])
        self.assertEqual(item.tags, ["Time Skip"])
        self.assertEqual(item.average_score, 92)
        self.assertEqual(item.season_year, 2025)
        self.assertEqual(item.trailer_site, "youtube")
        self.assertEqual(item.trailer_id, "detailTrailer_1")

    def test_media_detail_allows_item_comments(self):
        item = CatalogItem.objects.create(
            external_id="anilist:301",
            title="Commentable Title",
            media_type=CatalogItem.MediaType.ANIME,
        )

        response = self.client.post(
            reverse("tracker:media_comment", args=[item.id]),
            {"body": "This episode was so good."},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("?tab=social#social", response["Location"])
        self.assertEqual(MediaComment.objects.filter(catalog_item=item, user=self.user).count(), 1)

        with patch("tracker.views.fetch_media_details") as fetch_media_details:
            fetch_media_details.return_value = {}
            response = self.client.get(f"{reverse('tracker:anime_detail', args=[item.id])}?tab=social")

        self.assertContains(response, "This episode was so good.")


class ProfileAndFriendshipTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        self.user = User.objects.create_user(username="hamur", password="password123")
        self.friend = User.objects.create_user(username="friend", password="password123")

    def test_profile_upload_is_saved_as_square_image_with_transparency(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("tracker:profile_edit"),
            {
                "bio": "Anime and manga library notes.",
                "avatar": image_upload(),
            },
        )

        self.assertEqual(response.status_code, 302)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.bio, "Anime and manga library notes.")
        self.assertTrue(profile.avatar.name.endswith(".png"))
        self.assertTrue(profile.avatar_has_transparency)

        with Image.open(profile.avatar.path) as saved_image:
            self.assertEqual(saved_image.size, (512, 512))
            self.assertIn(saved_image.mode, {"RGBA", "P"})

        response = self.client.get(reverse("tracker:profile"))
        self.assertContains(response, "profile-avatar is-floating")

    def test_profile_background_upload_is_saved_and_displayed(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("tracker:profile_edit"),
            {
                "bio": "Anime and manga library notes.",
                "banner": image_upload(name="banner.png", size=(1200, 420), color=(90, 20, 160, 255)),
            },
        )

        self.assertEqual(response.status_code, 302)
        profile = UserProfile.objects.get(user=self.user)
        self.assertTrue(profile.banner.name.endswith(".png"))
        self.assertTrue(profile.display_banner_url)

        response = self.client.get(reverse("tracker:profile"))
        self.assertContains(response, 'class="profile-banner-image"')
        self.assertNotContains(response, '<div class="profile-banner-strip"')

    def test_profile_background_url_can_replace_uploaded_background(self):
        self.client.force_login(self.user)
        profile = UserProfile.objects.create(
            user=self.user,
            banner=image_upload(name="old-banner.png", size=(1200, 420), color=(90, 20, 160, 255)),
        )

        response = self.client.post(
            reverse("tracker:profile_edit"),
            {
                "bio": "Linked background.",
                "banner_url": "https://img.example/profile-banner.jpg",
            },
        )

        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertFalse(profile.banner)
        self.assertEqual(profile.banner_url, "https://img.example/profile-banner.jpg")

    def test_friend_request_can_be_sent_accepted_and_removed(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("tracker:friend_action", args=[self.friend.username]),
            {"action": "send"},
        )

        self.assertEqual(response.status_code, 302)
        friendship = Friendship.objects.get(requester=self.user, addressee=self.friend)
        self.assertEqual(friendship.status, Friendship.Status.PENDING)

        self.client.force_login(self.friend)
        response = self.client.post(
            reverse("tracker:friend_action", args=[self.user.username]),
            {"action": "accept"},
        )

        self.assertEqual(response.status_code, 302)
        friendship.refresh_from_db()
        self.assertEqual(friendship.status, Friendship.Status.ACCEPTED)

        response = self.client.get(reverse("tracker:friends"))
        self.assertContains(response, self.user.username)

        response = self.client.post(
            reverse("tracker:friend_action", args=[self.user.username]),
            {"action": "remove"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Friendship.objects.exists())

    def test_public_profile_shows_comparison_and_suggestions(self):
        shared_item = CatalogItem.objects.create(
            external_id="anilist:400",
            title="Shared Favorite",
            media_type=CatalogItem.MediaType.ANIME,
            genres=["Drama"],
            average_score=90,
        )
        friend_pick = CatalogItem.objects.create(
            external_id="anilist:401",
            title="Friend Pick",
            media_type=CatalogItem.MediaType.MANGA,
            genres=["Drama"],
            average_score=87,
        )
        LibraryEntry.objects.create(
            user=self.user,
            catalog_item=shared_item,
            external_id=shared_item.external_id,
            title=shared_item.title,
            media_type=LibraryEntry.MediaType.ANIME,
            status=LibraryEntry.Status.COMPLETED,
            rating=9,
            is_favorite=True,
        )
        LibraryEntry.objects.create(
            user=self.friend,
            catalog_item=shared_item,
            external_id=shared_item.external_id,
            title=shared_item.title,
            media_type=LibraryEntry.MediaType.ANIME,
            status=LibraryEntry.Status.COMPLETED,
            rating=8,
            is_favorite=True,
        )
        LibraryEntry.objects.create(
            user=self.friend,
            catalog_item=friend_pick,
            external_id=friend_pick.external_id,
            title=friend_pick.title,
            media_type=LibraryEntry.MediaType.MANGA,
            status=LibraryEntry.Status.COMPLETED,
            rating=10,
            is_favorite=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("tracker:public_profile", args=[self.friend.username]))

        self.assertContains(response, "Compared with you")
        self.assertContains(response, "Based on 1 shared rated title.")
        self.assertContains(response, "Shared Favorite")
        self.assertContains(response, "Friend Pick")

    def test_public_profile_library_lists_filter_by_status(self):
        LibraryEntry.objects.create(
            user=self.friend,
            external_id="anilist:410",
            title="Friend Reread",
            media_type=LibraryEntry.MediaType.MANGA,
            status=LibraryEntry.Status.REWATCHING,
        )
        LibraryEntry.objects.create(
            user=self.friend,
            external_id="anilist:411",
            title="Friend Completed",
            media_type=LibraryEntry.MediaType.ANIME,
            status=LibraryEntry.Status.COMPLETED,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("tracker:public_profile", args=[self.friend.username]),
            {"list": LibraryEntry.Status.REWATCHING},
        )

        self.assertContains(response, "Library lists")
        self.assertContains(response, "Rewatching / Rereading")
        list_section = response.content.decode().split('<section class="dashboard-grid" id="social">', 1)[0]
        self.assertIn("Friend Reread", list_section)
        self.assertNotIn("Friend Completed", list_section)
