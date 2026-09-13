from collections import Counter, defaultdict
import json
from difflib import SequenceMatcher
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.db.models import Avg, Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from PIL import Image, UnidentifiedImageError

from .anilist import fetch_media_catalog, fetch_media_details
from .models import CatalogItem, Friendship, LibraryEntry, MediaComment, UserProfile
from .taxonomy import ANILIST_GENRES, ANILIST_TAGS, canonical_taxonomy_value


CATALOG_CACHE_FIELDS = [
    "title",
    "media_type",
    "description",
    "cover_image_url",
    "genres",
    "tags",
    "average_score",
    "format",
    "release_status",
    "episodes",
    "chapters",
    "volumes",
    "season_year",
    "site_url",
    "trailer_site",
    "trailer_id",
    "trailer_thumbnail_url",
]

MEDIA_DETAIL_TABS = (
    ("overview", "Overview"),
    ("watch", "Watch"),
    ("characters", "Characters"),
    ("staff", "Staff"),
    ("reviews", "Reviews"),
    ("stats", "Stats"),
    ("social", "Social"),
)
MEDIA_DETAIL_TAB_KEYS = {key for key, _label in MEDIA_DETAIL_TABS}

STATUS_LABELS = {
    LibraryEntry.Status.PLANNED: {
        "default": "Planning",
        LibraryEntry.MediaType.ANIME: "Plan to watch",
        LibraryEntry.MediaType.MANGA: "Plan to read",
    },
    LibraryEntry.Status.WATCHING: {
        "default": "Watching / Reading",
        LibraryEntry.MediaType.ANIME: "Watching",
        LibraryEntry.MediaType.MANGA: "Reading",
    },
    LibraryEntry.Status.COMPLETED: {
        "default": "Completed",
        LibraryEntry.MediaType.ANIME: "Completed",
        LibraryEntry.MediaType.MANGA: "Completed",
    },
    LibraryEntry.Status.REWATCHING: {
        "default": "Rewatching / Rereading",
        LibraryEntry.MediaType.ANIME: "Rewatching",
        LibraryEntry.MediaType.MANGA: "Rereading",
    },
    LibraryEntry.Status.ON_HOLD: {
        "default": "Paused",
        LibraryEntry.MediaType.ANIME: "Paused",
        LibraryEntry.MediaType.MANGA: "Paused",
    },
    LibraryEntry.Status.DROPPED: {
        "default": "Dropped",
        LibraryEntry.MediaType.ANIME: "Dropped",
        LibraryEntry.MediaType.MANGA: "Dropped",
    },
}


def _json_error(message: str, *, status: int, errors: dict | None = None) -> JsonResponse:
    payload: dict = {"ok": False, "message": message}
    if errors:
        payload["errors"] = errors
    return JsonResponse(payload, status=status)


def _require_api_auth(request: HttpRequest) -> HttpResponse | None:
    if request.user.is_authenticated:
        return None
    return _json_error("Authentication required.", status=401)


def _require_csrf_for_api(request: HttpRequest) -> HttpResponse | None:
    """
    API endpoints return JSON errors for unauthenticated requests.
    CSRF is enforced manually for authenticated unsafe requests.
    """
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return None
    if not request.user.is_authenticated:
        return None

    failure = CsrfViewMiddleware(lambda _req: None).process_view(request, None, (), {})
    if failure is None:
        return None
    return _json_error("CSRF failed.", status=403)


def _parse_json_body(request: HttpRequest) -> tuple[dict | None, JsonResponse | None]:
    if not request.body:
        return {}, None
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _json_error("Invalid JSON body.", status=400)
    if not isinstance(data, dict):
        return None, _json_error("JSON body must be an object.", status=400)
    return data, None


def _serialize_entry(entry: LibraryEntry) -> dict:
    return {
        "id": entry.id,
        "external_id": entry.external_id,
        "title": entry.title,
        "media_type": entry.media_type,
        "status": entry.status,
        "status_label": _status_label(entry.status, entry.media_type),
        "progress": entry.progress,
        "rating": entry.rating,
        "notes": entry.notes,
        "is_favorite": entry.is_favorite,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


def _status_label(status: str, media_type: str | None = None) -> str:
    labels = STATUS_LABELS.get(status)
    if not labels:
        return str(status or "").replace("_", " ").title()
    return labels.get(media_type) or labels["default"]


def _status_choice_options(media_type: str | None = None) -> list[dict]:
    return [
        {
            "value": value,
            "label": _status_label(value, media_type),
            "anime_label": _status_label(value, LibraryEntry.MediaType.ANIME),
            "manga_label": _status_label(value, LibraryEntry.MediaType.MANGA),
            "default_label": _status_label(value),
        }
        for value, _label in LibraryEntry.Status.choices
    ]


def _attach_status_labels(entries) -> None:
    for entry in entries:
        entry.status_label = _status_label(entry.status, entry.media_type)


def _query_url(path: str, params: dict) -> str:
    clean_params = {
        key: value
        for key, value in params.items()
        if value not in {"", None, False}
    }
    if not clean_params:
        return path
    return f"{path}?{urlencode(clean_params)}"


def _status_tabs(
    base_qs,
    active_status: str,
    *,
    base_path: str,
    param_name: str = "status",
    media_type: str | None = None,
    extra_params: dict | None = None,
) -> list[dict]:
    extra_params = extra_params or {}
    status_counts = {
        row["status"]: row["count"]
        for row in base_qs.values("status").annotate(count=Count("id"))
    }
    tabs = [
        {
            "value": "",
            "label": "All",
            "count": base_qs.count(),
            "active": not active_status,
            "url": _query_url(base_path, {**extra_params, param_name: ""}),
        }
    ]
    for value, _label in LibraryEntry.Status.choices:
        tabs.append(
            {
                "value": value,
                "label": _status_label(value, media_type),
                "count": status_counts.get(value, 0),
                "active": active_status == value,
                "url": _query_url(base_path, {**extra_params, param_name: value}),
            }
        )
    return tabs


def _entry_catalog_item(entry: LibraryEntry, catalog_lookup: dict[str, CatalogItem]) -> CatalogItem | None:
    return entry.catalog_item or catalog_lookup.get(entry.external_id)


def _entry_detail_url(entry: LibraryEntry, catalog_lookup: dict[str, CatalogItem]) -> str:
    item = _entry_catalog_item(entry, catalog_lookup)
    return _catalog_detail_url(item) if item else ""


def _entry_rows(entries: list[LibraryEntry]) -> list[dict]:
    _attach_status_labels(entries)
    missing_catalog_ids = [
        entry.external_id
        for entry in entries
        if entry.catalog_item_id is None
    ]
    catalog_lookup = CatalogItem.objects.in_bulk(missing_catalog_ids, field_name="external_id")
    rows = []
    for entry in entries:
        item = _entry_catalog_item(entry, catalog_lookup)
        rows.append(
            {
                "entry": entry,
                "item": item,
                "detail_url": _catalog_detail_url(item) if item else "",
                "cover_image_url": item.cover_image_url if item else "",
            }
        )
    return rows


def _profile_banner_images(user, *, limit: int = 6) -> list[str]:
    entries = (
        LibraryEntry.objects.filter(user=user)
        .select_related("catalog_item")
        .exclude(catalog_item__cover_image_url="")
        .order_by("-is_favorite", "-updated_at")[:limit]
    )
    images = []
    seen = set()
    for entry in entries:
        item = entry.catalog_item
        if item is None or not item.cover_image_url or item.cover_image_url in seen:
            continue
        seen.add(item.cover_image_url)
        images.append(item.cover_image_url)
    return images


def _clean_positive_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _clean_token(value) -> str:
    return str(value or "").strip()[:64]


def _clean_url(value) -> str:
    return str(value or "").strip()


def _clean_label_list(value, *, limit: int, max_length: int = 64) -> list[str]:
    if not isinstance(value, list):
        return []

    labels = []
    seen = set()
    for item in value:
        if isinstance(item, dict):
            if item.get("isAdult"):
                continue
            raw_label = item.get("name")
        else:
            raw_label = item
        if not isinstance(raw_label, str):
            continue
        cleaned = raw_label.strip()
        if not cleaned or cleaned in seen:
            continue
        labels.append(cleaned[:max_length])
        seen.add(cleaned)
    return labels[:limit]


def _clean_genres(value) -> list[str]:
    return _clean_label_list(value, limit=12)


def _clean_tags(value) -> list[str]:
    return _clean_label_list(value, limit=40)


def _best_media_title(title_data, fallback: str = "") -> str:
    if isinstance(title_data, dict):
        return (
            title_data.get("english")
            or title_data.get("romaji")
            or title_data.get("native")
            or fallback
        ).strip()
    return str(title_data or fallback).strip()


def _catalog_defaults_from_media_data(data: dict, media_type: str) -> dict:
    cover_data = data.get("coverImage") or {}
    trailer_data = data.get("trailer") or {}
    raw_title = data.get("title")
    title_fallback = "" if isinstance(raw_title, dict) else str(raw_title or "")
    title = _best_media_title(raw_title, title_fallback)
    return {
        "title": title,
        "media_type": media_type,
        "description": str(data.get("description") or "").strip(),
        "cover_image_url": _clean_url(
            data.get("cover_image_url")
            or cover_data.get("large")
            or cover_data.get("medium")
        ),
        "genres": _clean_genres(data.get("genres")),
        "tags": _clean_tags(data.get("tags")),
        "average_score": _clean_positive_int(data.get("average_score", data.get("averageScore"))),
        "format": _clean_token(data.get("format")),
        "release_status": _clean_token(data.get("release_status", data.get("status"))),
        "episodes": _clean_positive_int(data.get("episodes")),
        "chapters": _clean_positive_int(data.get("chapters")),
        "volumes": _clean_positive_int(data.get("volumes")),
        "season_year": _clean_positive_int(data.get("season_year", data.get("seasonYear"))),
        "site_url": _clean_url(data.get("site_url") or data.get("siteUrl")),
        "trailer_site": _clean_token(data.get("trailer_site") or trailer_data.get("site")),
        "trailer_id": str(data.get("trailer_id") or trailer_data.get("id") or "").strip()[:128],
        "trailer_thumbnail_url": _clean_url(
            data.get("trailer_thumbnail_url") or trailer_data.get("thumbnail")
        ),
    }


def _catalog_detail_url(item: CatalogItem) -> str:
    if item.media_type == CatalogItem.MediaType.MANGA:
        return reverse("tracker:manga_detail", args=[item.id])
    return reverse("tracker:anime_detail", args=[item.id])


def _catalog_detail_tab_url(item: CatalogItem, tab: str) -> str:
    return f"{_catalog_detail_url(item)}?tab={tab}"


def _media_detail_tabs(item: CatalogItem, active_tab: str) -> list[dict]:
    return [
        {
            "key": key,
            "label": label,
            "url": _catalog_detail_tab_url(item, key),
            "active": key == active_tab,
        }
        for key, label in MEDIA_DETAIL_TABS
    ]


def _media_metadata_items(item: CatalogItem) -> list[dict]:
    metadata = []
    if item.average_score is not None:
        metadata.append({"label": "Average score", "value": f"{item.average_score}/100"})
    if item.display_format:
        metadata.append({"label": "Format", "value": item.display_format})
    if item.display_release_status:
        metadata.append({"label": "Release status", "value": item.display_release_status})
    if item.season_year:
        metadata.append({"label": "Year", "value": item.season_year})
    if item.episodes:
        metadata.append({"label": "Episodes", "value": item.episodes})
    if item.chapters:
        metadata.append({"label": "Chapters", "value": item.chapters})
    if item.volumes:
        metadata.append({"label": "Volumes", "value": item.volumes})
    return metadata


def _safe_youtube_trailer_id(item: CatalogItem) -> str:
    if item.trailer_site.lower() != "youtube" or not item.trailer_id:
        return ""
    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if any(char not in allowed_chars for char in item.trailer_id):
        return ""
    return item.trailer_id


def _youtube_trailer_watch_url(item: CatalogItem) -> str:
    trailer_id = _safe_youtube_trailer_id(item)
    if not trailer_id:
        return ""
    return f"https://www.youtube.com/watch?v={trailer_id}"


def _anilist_person_name(node: dict) -> str:
    name_data = (node or {}).get("name") or {}
    return str(name_data.get("full") or "").strip()


def _anilist_person_image(node: dict) -> str:
    image_data = (node or {}).get("image") or {}
    return _clean_url(image_data.get("large") or image_data.get("medium"))


def _character_items_from_detail(detail_data: dict | None) -> list[dict]:
    if not detail_data:
        return []

    edges = ((detail_data.get("characters") or {}).get("edges")) or []
    characters = []
    for edge in edges:
        node = edge.get("node") or {}
        name = _anilist_person_name(node)
        if not name:
            continue

        voice_actor_names = [
            _anilist_person_name(voice_actor)
            for voice_actor in (edge.get("voiceActors") or [])
            if _anilist_person_name(voice_actor)
        ]
        characters.append(
            {
                "name": name,
                "role": str(edge.get("role") or "Character").replace("_", " ").title(),
                "image_url": _anilist_person_image(node),
                "voice_actors": voice_actor_names,
            }
        )
    return characters


def _staff_items_from_detail(detail_data: dict | None, *, limit: int = 36) -> list[dict]:
    if not detail_data:
        return []

    staff_items = []
    seen = set()

    for edge in ((detail_data.get("staff") or {}).get("edges") or []):
        node = edge.get("node") or {}
        name = _anilist_person_name(node)
        role = str(edge.get("role") or "Staff").strip()
        key = (name.casefold(), role.casefold())
        if not name or key in seen:
            continue
        seen.add(key)
        staff_items.append(
            {
                "name": name,
                "role": role,
                "image_url": _anilist_person_image(node),
            }
        )
        if len(staff_items) >= limit:
            return staff_items

    for edge in ((detail_data.get("characters") or {}).get("edges") or []):
        character_node = edge.get("node") or {}
        character_name = _anilist_person_name(character_node)
        for voice_actor in (edge.get("voiceActors") or [])[:1]:
            name = _anilist_person_name(voice_actor)
            role = f"Voice Actor{f' as {character_name}' if character_name else ''}"
            key = (name.casefold(), role.casefold())
            if not name or key in seen:
                continue
            seen.add(key)
            staff_items.append(
                {
                    "name": name,
                    "role": role,
                    "image_url": _anilist_person_image(voice_actor),
                }
            )
            if len(staff_items) >= limit:
                return staff_items

    return staff_items


def _rank_catalog_items_by_query(items: list[CatalogItem], query: str) -> list[CatalogItem]:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return items

    def score(item: CatalogItem) -> tuple[int, float, str]:
        title = item.title.lower()
        if title == normalized_query:
            bucket = 0
        elif title.startswith(normalized_query):
            bucket = 1
        elif normalized_query in title:
            bucket = 2
        else:
            bucket = 3
        similarity = SequenceMatcher(None, normalized_query, title).ratio()
        return (bucket, -similarity, title)

    return sorted(items, key=score)


def _catalog_filter_value(value: str, options: tuple[str, ...]) -> str:
    return canonical_taxonomy_value(value, options)


def _catalog_item_matches_filters(item: CatalogItem, *, genre: str = "", tag: str = "") -> bool:
    if genre and genre not in (item.genres or []):
        return False
    if tag and tag not in (item.tags or []):
        return False
    return True


def _catalog_filter_url(
    base_path: str,
    *,
    query: str = "",
    genre: str = "",
    tag: str = "",
) -> str:
    return _query_url(base_path, {"q": query, "genre": genre, "tag": tag})


def _catalog_genre_links(base_path: str, *, query: str, active_genre: str, tag: str) -> list[dict]:
    return [
        {
            "label": genre,
            "active": genre == active_genre,
            "url": _catalog_filter_url(
                base_path,
                query=query,
                genre="" if genre == active_genre else genre,
                tag=tag,
            ),
        }
        for genre in ANILIST_GENRES
    ]


def _genre_preferences_for(user) -> Counter:
    preferences = Counter()
    entries = LibraryEntry.objects.filter(user=user).select_related("catalog_item")

    for entry in entries:
        item = entry.catalog_item
        if item is None or not item.genres:
            continue

        weight = 1
        if entry.is_favorite:
            weight += 5
        if entry.rating:
            if entry.rating < 6:
                continue
            weight += entry.rating - 5
        if entry.status in {LibraryEntry.Status.WATCHING, LibraryEntry.Status.REWATCHING}:
            weight += 2
        elif entry.status == LibraryEntry.Status.COMPLETED:
            weight += 1

        preferences.update({genre: weight for genre in item.genres})

    return preferences


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def _recommendations_for(user, *, limit: int = 6) -> tuple[list[dict], list[str]]:
    user_external_ids = set(
        LibraryEntry.objects.filter(user=user).values_list("external_id", flat=True)
    )
    genre_preferences = _genre_preferences_for(user)
    signals = defaultdict(lambda: {"item": None, "score": 0.0, "reasons": []})

    for item in CatalogItem.objects.exclude(external_id__in=user_external_ids).order_by("-average_score", "title")[:300]:
        item_genres = [genre for genre in item.genres if genre in genre_preferences]
        if not item_genres:
            continue

        candidate = signals[item.external_id]
        candidate["item"] = item
        candidate["score"] += sum(genre_preferences[genre] for genre in item_genres) * 12
        candidate["score"] += (item.average_score or 0) / 10
        _add_reason(candidate["reasons"], f"Matches {', '.join(item_genres[:2])}")

    friends = _friends_for(user)
    if friends:
        friend_entries = list(
            LibraryEntry.objects.filter(user__in=friends)
            .exclude(external_id__in=user_external_ids)
            .filter(Q(is_favorite=True) | Q(rating__gte=8))
            .select_related("catalog_item", "user")
        )
        missing_catalog_ids = [
            entry.external_id
            for entry in friend_entries
            if entry.catalog_item_id is None
        ]
        catalog_lookup = CatalogItem.objects.in_bulk(missing_catalog_ids, field_name="external_id")

        for entry in friend_entries:
            item = entry.catalog_item or catalog_lookup.get(entry.external_id)
            if item is None:
                continue

            candidate = signals[item.external_id]
            candidate["item"] = item
            candidate["score"] += 35
            candidate["score"] += (entry.rating or 0) * 2
            if entry.is_favorite:
                candidate["score"] += 15
            if entry.rating:
                _add_reason(candidate["reasons"], f"{entry.user.username} rated it {entry.rating}/10")
            elif entry.is_favorite:
                _add_reason(candidate["reasons"], f"{entry.user.username} favorited it")

    if not signals:
        for item in CatalogItem.objects.exclude(external_id__in=user_external_ids).order_by("-average_score", "title")[:limit]:
            candidate = signals[item.external_id]
            candidate["item"] = item
            candidate["score"] = item.average_score or 0
            _add_reason(candidate["reasons"], "Popular in your cached catalog")

    ranked = sorted(
        (candidate for candidate in signals.values() if candidate["item"] is not None),
        key=lambda candidate: (
            -candidate["score"],
            -(candidate["item"].average_score or 0),
            candidate["item"].title,
        ),
    )

    recommendations = [
        {
            "item": candidate["item"],
            "detail_url": _catalog_detail_url(candidate["item"]),
            "reason": " + ".join(candidate["reasons"][:2]),
        }
        for candidate in ranked[:limit]
    ]
    top_genres = [genre for genre, _count in genre_preferences.most_common(4)]
    return recommendations, top_genres


def _comparison_entry_row(
    entry: LibraryEntry,
    *,
    catalog_lookup: dict[str, CatalogItem],
    my_entry: LibraryEntry | None = None,
) -> dict:
    item = _entry_catalog_item(entry, catalog_lookup)
    if item is None and my_entry is not None:
        item = _entry_catalog_item(my_entry, catalog_lookup)
    return {
        "title": entry.title,
        "media_type": entry.get_media_type_display(),
        "status": _status_label(entry.status, entry.media_type),
        "rating": entry.rating,
        "is_favorite": entry.is_favorite,
        "my_status": _status_label(my_entry.status, my_entry.media_type) if my_entry else "",
        "my_rating": my_entry.rating if my_entry else None,
        "my_is_favorite": my_entry.is_favorite if my_entry else False,
        "detail_url": _catalog_detail_url(item) if item else "",
    }


def _profile_comparison(current_user, profile_user, *, limit: int = 5) -> dict | None:
    if current_user == profile_user:
        return None

    my_entries = list(
        LibraryEntry.objects.filter(user=current_user).select_related("catalog_item")
    )
    their_entries = list(
        LibraryEntry.objects.filter(user=profile_user).select_related("catalog_item")
    )

    if not my_entries and not their_entries:
        return None

    my_by_external_id = {entry.external_id: entry for entry in my_entries}
    their_by_external_id = {entry.external_id: entry for entry in their_entries}
    shared_external_ids = set(my_by_external_id) & set(their_by_external_id)
    comparison_external_ids = {
        entry.external_id
        for entry in [*my_entries, *their_entries]
        if entry.catalog_item_id is None
    }
    catalog_lookup = CatalogItem.objects.in_bulk(comparison_external_ids, field_name="external_id")

    shared_rows = [
        _comparison_entry_row(
            their_by_external_id[external_id],
            catalog_lookup=catalog_lookup,
            my_entry=my_by_external_id[external_id],
        )
        for external_id in shared_external_ids
    ]
    shared_rows.sort(
        key=lambda row: (
            row["my_is_favorite"] or row["is_favorite"],
            row["my_rating"] or 0,
            row["rating"] or 0,
            row["title"],
        ),
        reverse=True,
    )

    suggested_entries = [
        entry
        for entry in their_entries
        if entry.external_id not in my_by_external_id
        and (entry.is_favorite or (entry.rating is not None and entry.rating >= 8))
    ]
    suggested_entries.sort(
        key=lambda entry: (
            entry.is_favorite,
            entry.rating or 0,
            entry.updated_at,
        ),
        reverse=True,
    )
    suggestion_rows = [
        _comparison_entry_row(entry, catalog_lookup=catalog_lookup)
        for entry in suggested_entries[:limit]
    ]

    rated_pairs = [
        (my_by_external_id[external_id].rating, their_by_external_id[external_id].rating)
        for external_id in shared_external_ids
        if my_by_external_id[external_id].rating is not None
        and their_by_external_id[external_id].rating is not None
    ]
    compatibility_score = None
    compatibility_note = "Add more shared ratings to unlock a tighter score."
    if rated_pairs:
        average_gap = sum(abs(my_rating - their_rating) for my_rating, their_rating in rated_pairs) / len(rated_pairs)
        compatibility_score = round(max(0, 100 - average_gap * 12))
        compatibility_note = f"Based on {len(rated_pairs)} shared rated title{'s' if len(rated_pairs) != 1 else ''}."
    elif my_entries and their_entries:
        overlap_ratio = len(shared_external_ids) / max(1, min(len(my_entries), len(their_entries)))
        compatibility_score = round(overlap_ratio * 100)
        compatibility_note = "Based on list overlap."

    mutual_highlights_count = sum(
        1
        for external_id in shared_external_ids
        if (
            my_by_external_id[external_id].is_favorite
            or (my_by_external_id[external_id].rating is not None and my_by_external_id[external_id].rating >= 8)
        )
        and (
            their_by_external_id[external_id].is_favorite
            or (their_by_external_id[external_id].rating is not None and their_by_external_id[external_id].rating >= 8)
        )
    )

    return {
        "shared_count": len(shared_external_ids),
        "mutual_highlights_count": mutual_highlights_count,
        "suggested_count": len(suggested_entries),
        "compatibility_score": compatibility_score,
        "compatibility_label": f"{compatibility_score}%" if compatibility_score is not None else "-",
        "compatibility_note": compatibility_note,
        "shared_entries": shared_rows[:limit],
        "suggested_entries": suggestion_rows,
    }


def _library_overview(user) -> dict:
    user_entries = LibraryEntry.objects.filter(user=user)
    anime_count = user_entries.filter(media_type=LibraryEntry.MediaType.ANIME).count()
    manga_count = user_entries.filter(media_type=LibraryEntry.MediaType.MANGA).count()
    total_count = anime_count + manga_count
    status_counts = {
        row["status"]: row["count"]
        for row in user_entries.values("status").annotate(count=Count("id"))
    }

    recent_entries = list(user_entries.order_by("-updated_at")[:5])
    favorite_entries = list(user_entries.filter(is_favorite=True).order_by("-updated_at")[:5])
    _attach_status_labels(recent_entries)
    _attach_status_labels(favorite_entries)

    return {
        "total_count": total_count,
        "anime_count": anime_count,
        "manga_count": manga_count,
        "completed_count": user_entries.filter(status=LibraryEntry.Status.COMPLETED).count(),
        "active_count": user_entries.filter(
            status__in=[LibraryEntry.Status.WATCHING, LibraryEntry.Status.REWATCHING]
        ).count(),
        "planned_count": user_entries.filter(status=LibraryEntry.Status.PLANNED).count(),
        "favorite_count": user_entries.filter(is_favorite=True).count(),
        "rated_count": user_entries.exclude(rating__isnull=True).count(),
        "average_rating": user_entries.aggregate(value=Avg("rating"))["value"],
        "status_summary": [
            {
                "value": value,
                "label": _status_label(value),
                "count": status_counts.get(value, 0),
                "percent": round((status_counts.get(value, 0) / total_count) * 100) if total_count else 0,
            }
            for value, _label in LibraryEntry.Status.choices
        ],
        "recent_entries": recent_entries,
        "favorite_entries": favorite_entries,
    }


def _profile_list_context(request: HttpRequest, profile_user, *, limit: int = 80) -> dict:
    active_status = (request.GET.get("list") or "").strip().upper()
    valid_statuses = {value for value, _label in LibraryEntry.Status.choices}
    if active_status not in valid_statuses:
        active_status = ""

    active_media_type = (request.GET.get("media") or "").strip().upper()
    valid_media_types = {value for value, _label in LibraryEntry.MediaType.choices}
    if active_media_type not in valid_media_types:
        active_media_type = ""

    favorite_filter = request.GET.get("favorite") == "1"
    base_qs = LibraryEntry.objects.filter(user=profile_user).select_related("catalog_item")
    tab_count_qs = base_qs
    if active_media_type:
        tab_count_qs = tab_count_qs.filter(media_type=active_media_type)
    if favorite_filter:
        tab_count_qs = tab_count_qs.filter(is_favorite=True)

    qs = tab_count_qs
    if active_status:
        qs = qs.filter(status=active_status)
    entries = list(qs.order_by("-updated_at")[:limit])

    base_path = request.path
    list_params = {
        "media": active_media_type,
        "favorite": "1" if favorite_filter else "",
    }
    if favorite_filter:
        active_label = "Favorites"
    elif active_media_type == LibraryEntry.MediaType.ANIME:
        active_label = "Anime List"
    elif active_media_type == LibraryEntry.MediaType.MANGA:
        active_label = "Manga List"
    elif active_status:
        active_label = _status_label(active_status)
    else:
        active_label = "All"

    return {
        "profile_media_nav": [
            {
                "label": "Overview",
                "url": _query_url(base_path, {}),
                "active": not active_media_type and not favorite_filter,
            },
            {
                "label": "Anime List",
                "url": _query_url(base_path, {"media": LibraryEntry.MediaType.ANIME}),
                "active": active_media_type == LibraryEntry.MediaType.ANIME and not favorite_filter,
            },
            {
                "label": "Manga List",
                "url": _query_url(base_path, {"media": LibraryEntry.MediaType.MANGA}),
                "active": active_media_type == LibraryEntry.MediaType.MANGA and not favorite_filter,
            },
            {
                "label": "Favorites",
                "url": _query_url(base_path, {"favorite": "1"}),
                "active": favorite_filter,
            },
        ],
        "profile_list_tabs": _status_tabs(
            tab_count_qs,
            active_status,
            base_path=base_path,
            param_name="list",
            media_type=active_media_type,
            extra_params=list_params,
        ),
        "profile_list_rows": _entry_rows(entries),
        "active_profile_list_label": active_label,
        "profile_media_filter": active_media_type,
        "profile_favorite_filter": favorite_filter,
    }


def _ensure_profile(user) -> UserProfile:
    profile, _created = UserProfile.objects.get_or_create(user=user)
    return profile


def _friendship_between(user_a, user_b) -> Friendship | None:
    return Friendship.objects.filter(requester=user_a, addressee=user_b).first() or Friendship.objects.filter(
        requester=user_b,
        addressee=user_a,
    ).first()


def _friendship_context(current_user, target_user) -> dict:
    if current_user == target_user:
        return {"relation": "self", "friendship": None}

    friendship = _friendship_between(current_user, target_user)
    if friendship is None:
        return {"relation": "none", "friendship": None}
    if friendship.status == Friendship.Status.ACCEPTED:
        return {"relation": "friends", "friendship": friendship}
    if friendship.requester == current_user:
        return {"relation": "pending_sent", "friendship": friendship}
    return {"relation": "pending_received", "friendship": friendship}


def _friends_for(user):
    accepted = Friendship.objects.filter(status=Friendship.Status.ACCEPTED).filter(
        Q(requester=user) | Q(addressee=user)
    ).select_related("requester", "addressee")
    return [
        friendship.addressee if friendship.requester == user else friendship.requester
        for friendship in accepted
    ]


def _safe_username_slug(username: str) -> str:
    return "".join(ch for ch in username.lower() if ch.isalnum() or ch in {"-", "_"}) or "user"


def _image_extension(image_format: str | None) -> str:
    if image_format == "JPEG":
        return "jpg"
    return str(image_format or "png").lower()


def _square_avatar_upload(uploaded_file, username: str) -> tuple[ContentFile, bool]:
    if uploaded_file.size > 5 * 1024 * 1024:
        raise ValueError("Avatar image must be 5 MB or smaller.")

    try:
        image = Image.open(uploaded_file)
        image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Upload a valid image file.") from exc

    uploaded_file.seek(0)
    image = Image.open(uploaded_file)
    if image.format not in {"JPEG", "PNG", "GIF", "WEBP"}:
        raise ValueError("Avatar must be a PNG, JPG, JPEG, GIF, or WebP image.")

    has_transparency = image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    image = image.convert("RGBA" if has_transparency else "RGB")
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    image = image.resize((512, 512), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    safe_username = _safe_username_slug(username)
    if has_transparency:
        image.save(buffer, format="PNG", optimize=True)
        extension = "png"
    else:
        image.save(buffer, format="JPEG", quality=88, optimize=True)
        extension = "jpg"
    return ContentFile(buffer.getvalue(), name=f"{safe_username}_avatar.{extension}"), has_transparency


def _profile_banner_upload(uploaded_file, username: str) -> ContentFile:
    if uploaded_file.size > 8 * 1024 * 1024:
        raise ValueError("Background image must be 8 MB or smaller.")

    try:
        image = Image.open(uploaded_file)
        image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Upload a valid background image.") from exc

    if image.format not in {"JPEG", "PNG", "GIF", "WEBP"}:
        raise ValueError("Background must be a PNG, JPG, JPEG, GIF, or WebP image.")

    uploaded_file.seek(0)
    safe_username = _safe_username_slug(username)
    extension = _image_extension(image.format)
    return ContentFile(uploaded_file.read(), name=f"{safe_username}_banner.{extension}")


def _clean_profile_url(value: str, *, field_label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) > 200:
        raise ValueError(f"{field_label} URL must be 200 characters or fewer.")
    try:
        URLValidator()(cleaned)
    except ValidationError as exc:
        raise ValueError(f"Enter a valid {field_label.lower()} URL.") from exc
    return cleaned


def home_page(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("login")

    overview = _library_overview(request.user)
    recommendations, recommendation_genres = _recommendations_for(request.user)

    return render(
        request,
        "tracker/home.html",
        {
            **overview,
            "recommendations": recommendations,
            "recommendation_genres": recommendation_genres,
        },
    )


@login_required
def profile_page(request: HttpRequest) -> HttpResponse:
    overview = _library_overview(request.user)
    profile = _ensure_profile(request.user)
    friends = _friends_for(request.user)
    incoming_requests = Friendship.objects.filter(
        addressee=request.user,
        status=Friendship.Status.PENDING,
    ).select_related("requester")[:5]
    return render(
        request,
        "tracker/profile.html",
        {
            **overview,
            **_profile_list_context(request, request.user),
            "profile_user": request.user,
            "profile": profile,
            "profile_banner_images": _profile_banner_images(request.user),
            "friends": friends[:6],
            "friend_count": len(friends),
            "incoming_requests": incoming_requests,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def profile_edit_page(request: HttpRequest) -> HttpResponse:
    profile = _ensure_profile(request.user)
    errors: dict[str, str] = {}

    if request.method == "POST":
        bio = (request.POST.get("bio") or "").strip()
        banner_url = (request.POST.get("banner_url") or "").strip()
        clear_banner = request.POST.get("clear_banner") == "1"
        avatar_file = request.FILES.get("avatar")
        banner_file = request.FILES.get("banner")

        if len(bio) > 500:
            errors["bio"] = "Bio must be 500 characters or fewer."
        try:
            banner_url = _clean_profile_url(banner_url, field_label="Background image")
        except ValueError as exc:
            errors["banner_url"] = str(exc)

        if not errors:
            profile.bio = bio
            save_fields = ["bio", "updated_at"]
            if avatar_file:
                try:
                    avatar_content, avatar_has_transparency = _square_avatar_upload(
                        avatar_file,
                        request.user.username,
                    )
                except ValueError as exc:
                    errors["avatar"] = str(exc)
                else:
                    if profile.avatar:
                        profile.avatar.delete(save=False)
                    profile.avatar.save(avatar_content.name, avatar_content, save=False)
                    profile.avatar_url = ""
                    profile.avatar_has_transparency = avatar_has_transparency
                    save_fields.extend(["avatar", "avatar_url", "avatar_has_transparency"])
            if banner_file and not errors:
                try:
                    banner_content = _profile_banner_upload(banner_file, request.user.username)
                except ValueError as exc:
                    errors["banner"] = str(exc)
                else:
                    if profile.banner:
                        profile.banner.delete(save=False)
                    profile.banner.save(banner_content.name, banner_content, save=False)
                    profile.banner_url = ""
                    save_fields.extend(["banner", "banner_url"])
            elif banner_url:
                if profile.banner:
                    profile.banner.delete(save=False)
                profile.banner_url = banner_url
                save_fields.extend(["banner", "banner_url"])
            elif clear_banner:
                if profile.banner:
                    profile.banner.delete(save=False)
                profile.banner_url = ""
                save_fields.extend(["banner", "banner_url"])
            if not errors:
                profile.save(update_fields=sorted(set(save_fields)))
                messages.success(request, "Profile updated.")
                return redirect("tracker:profile")

    return render(
        request,
        "tracker/profile_edit.html",
        {
            "profile": profile,
            "errors": errors,
        },
    )


@login_required
def user_directory_page(request: HttpRequest) -> HttpResponse:
    User = get_user_model()
    query = (request.GET.get("q") or "").strip()
    users_qs = User.objects.exclude(id=request.user.id).order_by("username")
    if query:
        users_qs = users_qs.filter(username__icontains=query)

    user_cards = []
    for listed_user in users_qs[:60]:
        user_cards.append(
            {
                "user": listed_user,
                "profile": _ensure_profile(listed_user),
                "overview": _library_overview(listed_user),
                **_friendship_context(request.user, listed_user),
            }
        )

    return render(
        request,
        "tracker/users.html",
        {
            "query": query,
            "user_cards": user_cards,
        },
    )


@login_required
def public_profile_page(request: HttpRequest, username: str) -> HttpResponse:
    User = get_user_model()
    profile_user = get_object_or_404(User, username=username)
    profile = _ensure_profile(profile_user)
    overview = _library_overview(profile_user)
    friendship = _friendship_context(request.user, profile_user)
    comparison = _profile_comparison(request.user, profile_user)

    return render(
        request,
        "tracker/public_profile.html",
        {
            **overview,
            **_profile_list_context(request, profile_user),
            "profile_user": profile_user,
            "profile": profile,
            "profile_banner_images": _profile_banner_images(profile_user),
            "comparison": comparison,
            **friendship,
        },
    )


@login_required
@require_http_methods(["POST"])
def friend_action(request: HttpRequest, username: str) -> HttpResponse:
    User = get_user_model()
    target_user = get_object_or_404(User, username=username)
    action = request.POST.get("action") or ""

    if target_user == request.user:
        messages.error(request, "You cannot add yourself.")
        return redirect("tracker:profile")

    friendship = _friendship_between(request.user, target_user)

    if action == "send":
        if friendship is None:
            Friendship.objects.create(requester=request.user, addressee=target_user)
            messages.success(request, f"Friend request sent to {target_user.username}.")
        elif friendship.status == Friendship.Status.PENDING and friendship.addressee == request.user:
            friendship.status = Friendship.Status.ACCEPTED
            friendship.save(update_fields=["status", "updated_at"])
            messages.success(request, f"You are now friends with {target_user.username}.")
        elif friendship.status == Friendship.Status.ACCEPTED:
            messages.info(request, f"You are already friends with {target_user.username}.")
        else:
            messages.info(request, "Friend request already sent.")
    elif action == "accept" and friendship and friendship.addressee == request.user:
        friendship.status = Friendship.Status.ACCEPTED
        friendship.save(update_fields=["status", "updated_at"])
        messages.success(request, f"You are now friends with {target_user.username}.")
    elif action in {"decline", "cancel"} and friendship and friendship.status == Friendship.Status.PENDING:
        friendship.delete()
        messages.info(request, "Friend request removed.")
    elif action == "remove" and friendship:
        friendship.delete()
        messages.info(request, f"Removed {target_user.username} from friends.")
    else:
        messages.error(request, "Friend action could not be completed.")

    next_path = request.POST.get("next") or ""
    if next_path.startswith("/"):
        return redirect(next_path)
    return redirect("tracker:public_profile", username=target_user.username)


@login_required
def friends_page(request: HttpRequest) -> HttpResponse:
    friends = [
        {
            "user": friend,
            "profile": _ensure_profile(friend),
            "overview": _library_overview(friend),
        }
        for friend in _friends_for(request.user)
    ]
    incoming = Friendship.objects.filter(
        addressee=request.user,
        status=Friendship.Status.PENDING,
    ).select_related("requester")
    outgoing = Friendship.objects.filter(
        requester=request.user,
        status=Friendship.Status.PENDING,
    ).select_related("addressee")

    return render(
        request,
        "tracker/friends.html",
        {
            "friends": friends,
            "incoming_requests": incoming,
            "outgoing_requests": outgoing,
        },
    )


@require_http_methods(["GET", "POST"])
def signup_page(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("tracker:anime_catalog")
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Account created. You can login now.")
            return redirect("login")
    else:
        form = UserCreationForm()
    return render(request, "registration/signup.html", {"form": form})


@login_required
def library_page(request: HttpRequest) -> HttpResponse:
    base_qs = LibraryEntry.objects.filter(user=request.user)
    status = request.GET.get("status") or ""
    media_type = request.GET.get("media_type") or ""
    favorite_filter = request.GET.get("favorite") == "1"
    valid_statuses = {value for value, _label in LibraryEntry.Status.choices}
    valid_media_types = {value for value, _label in LibraryEntry.MediaType.choices}
    if status not in valid_statuses:
        status = ""
    if media_type not in valid_media_types:
        media_type = ""

    qs = base_qs
    tab_count_qs = base_qs
    if media_type:
        tab_count_qs = tab_count_qs.filter(media_type=media_type)
    if favorite_filter:
        tab_count_qs = tab_count_qs.filter(is_favorite=True)

    if status:
        qs = qs.filter(status=status)
    if media_type:
        qs = qs.filter(media_type=media_type)
    if favorite_filter:
        qs = qs.filter(is_favorite=True)
    entries = list(qs.order_by("-updated_at"))
    _attach_status_labels(entries)

    return render(
        request,
        "tracker/library.html",
        {
            "entries": entries,
            "status_filter": status,
            "media_type_filter": media_type,
            "favorite_filter": favorite_filter,
            "favorite_count": base_qs.filter(is_favorite=True).count(),
            "favorite_url": _query_url(
                request.path,
                {
                    "status": status,
                    "media_type": media_type,
                    "favorite": "" if favorite_filter else "1",
                },
            ),
            "status_tabs": _status_tabs(
                tab_count_qs,
                status,
                base_path=request.path,
                media_type=media_type,
                extra_params={
                    "media_type": media_type,
                    "favorite": "1" if favorite_filter else "",
                },
            ),
            "status_choices": _status_choice_options(),
            "media_type_choices": LibraryEntry.MediaType.choices,
        },
    )


@login_required
def media_catalog_page(request: HttpRequest, media_type: str) -> HttpResponse:
    normalized_type = media_type.upper()
    if normalized_type not in {CatalogItem.MediaType.ANIME, CatalogItem.MediaType.MANGA}:
        return redirect("tracker:anime_catalog")

    query = (request.GET.get("q") or "").strip()
    active_genre = _catalog_filter_value(request.GET.get("genre") or "", ANILIST_GENRES)
    active_tag = _catalog_filter_value(request.GET.get("tag") or "", ANILIST_TAGS)
    source_note = "Showing Top 50 popular titles from AniList."
    source_error = ""
    preferred_external_ids: list[str] = []
    base_path = reverse(
        "tracker:manga_catalog" if normalized_type == CatalogItem.MediaType.MANGA else "tracker:anime_catalog"
    )

    try:
        remote_items = fetch_media_catalog(
            normalized_type,
            query,
            per_page=50,
            genre=active_genre,
            tag=active_tag,
        )
        preferred_external_ids = [data["external_id"] for data in remote_items]
        for data in remote_items:
            media_type_from_response = data.get("media_type") or normalized_type
            CatalogItem.objects.update_or_create(
                external_id=data["external_id"],
                defaults=_catalog_defaults_from_media_data(data, media_type_from_response),
            )
        if query:
            source_note = "Showing AniList search results (up to 50), cached locally."
        if active_genre or active_tag:
            filters = ", ".join(label for label in [active_genre, active_tag] if label)
            source_note = f"Showing AniList results filtered by {filters}, cached locally."
    except RuntimeError:
        source_error = "AniList API is unavailable right now, showing cached data only."
        if query:
            source_note = "Showing locally cached search results."
        else:
            source_note = "Showing locally cached popular list."
        if active_genre or active_tag:
            filters = ", ".join(label for label in [active_genre, active_tag] if label)
            source_note = f"Showing locally cached results filtered by {filters}."

    catalog_qs = CatalogItem.objects.filter(media_type=normalized_type)
    if query:
        catalog_qs = catalog_qs.filter(title__icontains=query)
    if preferred_external_ids:
        catalog_items = list(catalog_qs.filter(external_id__in=preferred_external_ids))
        position = {external_id: idx for idx, external_id in enumerate(preferred_external_ids)}
        catalog_items.sort(key=lambda item: position.get(item.external_id, 10**9))
    else:
        catalog_items = list(catalog_qs.order_by("title")[:300])
        if query:
            catalog_items = _rank_catalog_items_by_query(catalog_items, query)[:50]
        else:
            catalog_items = catalog_items[:100]

    if active_genre or active_tag:
        catalog_items = [
            item
            for item in catalog_items
            if _catalog_item_matches_filters(item, genre=active_genre, tag=active_tag)
        ][:50]
    elif not preferred_external_ids:
        catalog_items = catalog_items[:50]

    user_entries = LibraryEntry.objects.filter(
        user=request.user,
        media_type=normalized_type,
    ).values("external_id", "status")
    user_status_by_external_id = {
        row["external_id"]: row["status"]
        for row in user_entries
    }

    item_rows = [
        {
            "item": item,
            "user_status": user_status_by_external_id.get(item.external_id),
            "user_status_label": _status_label(
                user_status_by_external_id.get(item.external_id),
                item.media_type,
            ) if user_status_by_external_id.get(item.external_id) else "",
        }
        for item in catalog_items
    ]

    return render(
        request,
        "tracker/catalog.html",
        {
            "item_rows": item_rows,
            "status_choices": _status_choice_options(normalized_type),
            "selected_media_type": normalized_type,
            "query": query,
            "genre_options": ANILIST_GENRES,
            "tag_options": ANILIST_TAGS,
            "genre_links": _catalog_genre_links(
                base_path,
                query=query,
                active_genre=active_genre,
                tag=active_tag,
            ),
            "selected_genre": active_genre,
            "selected_tag": active_tag,
            "active_filter_count": len([value for value in (active_genre, active_tag) if value]),
            "clear_filters_url": base_path,
            "source_note": source_note,
            "source_error": source_error,
        },
    )


@login_required
def media_detail_page(request: HttpRequest, media_type: str, item_id: int) -> HttpResponse:
    item = get_object_or_404(CatalogItem, id=item_id)
    normalized_type = media_type.upper()
    if normalized_type not in {CatalogItem.MediaType.ANIME, CatalogItem.MediaType.MANGA}:
        return redirect("tracker:home")
    if item.media_type != normalized_type:
        if item.media_type == CatalogItem.MediaType.MANGA:
            return redirect("tracker:manga_detail", item_id=item.id)
        return redirect("tracker:anime_detail", item_id=item.id)

    detail_data: dict | None = None
    detail_error = ""

    if item.external_id.startswith("anilist:"):
        raw_id = item.external_id.split(":", 1)[1].strip()
        if raw_id.isdigit():
            try:
                detail_data = fetch_media_details(int(raw_id))
                defaults = _catalog_defaults_from_media_data(detail_data, item.media_type)
                if not defaults["title"]:
                    defaults["title"] = item.title
                if not defaults["description"]:
                    defaults["description"] = item.description
                if not defaults["cover_image_url"]:
                    defaults["cover_image_url"] = item.cover_image_url
                for field, value in defaults.items():
                    setattr(item, field, value)
                item.save(update_fields=CATALOG_CACHE_FIELDS)
            except RuntimeError:
                detail_error = "Could not load full details from AniList right now."

    user_entry = LibraryEntry.objects.filter(user=request.user, external_id=item.external_id).first()
    metadata_items = _media_metadata_items(item)
    trailer_watch_url = _youtube_trailer_watch_url(item)
    character_items = _character_items_from_detail(detail_data)
    staff_items = _staff_items_from_detail(detail_data)
    comments = item.comments.select_related("user").order_by("-created_at")[:30]
    active_tab = (request.GET.get("tab") or "overview").strip().lower()
    if active_tab not in MEDIA_DETAIL_TAB_KEYS:
        active_tab = "overview"
    return render(
        request,
        "tracker/media_detail.html",
        {
            "item": item,
            "detail_data": detail_data,
            "detail_error": detail_error,
            "metadata_items": metadata_items,
            "trailer_watch_url": trailer_watch_url,
            "character_items": character_items,
            "staff_items": staff_items,
            "comments": comments,
            "active_tab": active_tab,
            "detail_tabs": _media_detail_tabs(item, active_tab),
            "user_entry": user_entry,
            "user_entry_status_label": _status_label(
                user_entry.status,
                user_entry.media_type,
            ) if user_entry else "",
            "status_choices": _status_choice_options(item.media_type),
        },
    )


@login_required
@require_http_methods(["POST"])
def media_comment_action(request: HttpRequest, item_id: int) -> HttpResponse:
    item = get_object_or_404(CatalogItem, id=item_id)
    action = request.POST.get("action") or "create"

    if action == "delete":
        comment_id = request.POST.get("comment_id")
        deleted, _rows = MediaComment.objects.filter(
            id=comment_id,
            catalog_item=item,
            user=request.user,
        ).delete()
        if deleted:
            messages.info(request, "Comment deleted.")
        else:
            messages.error(request, "Comment could not be deleted.")
        return redirect(f"{_catalog_detail_tab_url(item, 'social')}#social")

    body = (request.POST.get("body") or "").strip()
    if not body:
        messages.error(request, "Write a comment before posting.")
        return redirect(f"{_catalog_detail_tab_url(item, 'social')}#social")
    if len(body) > 500:
        messages.error(request, "Comments must be 500 characters or fewer.")
        return redirect(f"{_catalog_detail_tab_url(item, 'social')}#social")

    MediaComment.objects.create(catalog_item=item, user=request.user, body=body)
    messages.success(request, "Comment posted.")
    return redirect(f"{_catalog_detail_tab_url(item, 'social')}#social")


@login_required
@require_http_methods(["POST"])
def add_catalog_item_to_library(request: HttpRequest, item_id: int) -> HttpResponse:
    item = get_object_or_404(CatalogItem, id=item_id)
    status = request.POST.get("status")
    valid_statuses = {c[0] for c in LibraryEntry.Status.choices}
    if status not in valid_statuses:
        status = LibraryEntry.Status.PLANNED

    entry, created = LibraryEntry.objects.get_or_create(
        user=request.user,
        external_id=item.external_id,
        defaults={
            "catalog_item": item,
            "title": item.title,
            "media_type": item.media_type,
            "status": status,
            "progress": 0,
            "rating": None,
            "notes": "",
        },
    )
    if created:
        messages.success(request, f'Added "{item.title}" to your list.')
    else:
        entry.status = status
        entry.catalog_item = item
        entry.title = item.title
        entry.media_type = item.media_type
        entry.save(update_fields=["status", "catalog_item", "title", "media_type", "updated_at"])
        messages.info(request, f'Updated "{item.title}" in your list.')

    next_path = request.POST.get("next") or ""
    if next_path.startswith("/"):
        return redirect(next_path)

    if item.media_type == CatalogItem.MediaType.MANGA:
        return redirect("tracker:manga_catalog")
    return redirect("tracker:anime_catalog")

@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_library_list_create(request: HttpRequest) -> HttpResponse:
    auth_resp = _require_api_auth(request)
    if auth_resp:
        return auth_resp
    csrf_resp = _require_csrf_for_api(request)
    if csrf_resp:
        return csrf_resp

    if request.method == "GET":
        qs = LibraryEntry.objects.filter(user=request.user)

        status = request.GET.get("status")
        media_type = request.GET.get("media_type")
        if status:
            qs = qs.filter(status=status)
        if media_type:
            qs = qs.filter(media_type=media_type)

        qs = qs.order_by("-updated_at")
        return JsonResponse({"ok": True, "results": [_serialize_entry(e) for e in qs]})

    data, err = _parse_json_body(request)
    if err:
        return err
    assert data is not None

    external_id = (data.get("external_id") or "").strip()
    title = (data.get("title") or "").strip()
    media_type = data.get("media_type")
    status = data.get("status")

    field_errors: dict[str, str] = {}
    if not external_id:
        field_errors["external_id"] = "This field is required."
    if not title:
        field_errors["title"] = "This field is required."
    if media_type not in {c[0] for c in LibraryEntry.MediaType.choices}:
        field_errors["media_type"] = "Invalid media_type."
    if status not in {c[0] for c in LibraryEntry.Status.choices}:
        field_errors["status"] = "Invalid status."
    if field_errors:
        return _json_error("Validation error.", status=400, errors=field_errors)

    if LibraryEntry.objects.filter(user=request.user, external_id=external_id).exists():
        return _json_error(
            "Entry with this external_id already exists for this user.",
            status=409,
            errors={"external_id": "Duplicate for this user."},
        )

    entry = LibraryEntry.objects.create(
        user=request.user,
        external_id=external_id,
        title=title,
        media_type=media_type,
        status=status,
        progress=0,
        rating=None,
        notes="",
    )
    return JsonResponse({"ok": True, "result": _serialize_entry(entry)}, status=201)

@csrf_exempt
@require_http_methods(["GET", "PATCH", "DELETE"])
def api_library_detail(request: HttpRequest, entry_id: int) -> HttpResponse:
    auth_resp = _require_api_auth(request)
    if auth_resp:
        return auth_resp
    csrf_resp = _require_csrf_for_api(request)
    if csrf_resp:
        return csrf_resp

    try:
        entry = LibraryEntry.objects.get(id=entry_id, user=request.user)
    except LibraryEntry.DoesNotExist:
        return _json_error("Not found.", status=404)

    if request.method == "GET":
        return JsonResponse({"ok": True, "result": _serialize_entry(entry)})

    if request.method == "DELETE":
        entry.delete()
        return HttpResponse(status=204)

    data, err = _parse_json_body(request)
    if err:
        return err
    assert data is not None

    allowed_fields = {"status", "progress", "rating", "notes", "is_favorite"}
    unknown_fields = sorted(set(data.keys()) - allowed_fields)
    if unknown_fields:
        return _json_error(
            "Unknown field(s).",
            status=400,
            errors={"fields": f"Only {sorted(allowed_fields)} are allowed."},
        )

    field_errors: dict[str, str] = {}

    if "status" in data:
        status = data.get("status")
        if status not in {c[0] for c in LibraryEntry.Status.choices}:
            field_errors["status"] = "Invalid status."
        else:
            entry.status = status

    if "progress" in data:
        progress = data.get("progress")
        if not isinstance(progress, int):
            field_errors["progress"] = "Must be an integer."
        elif progress < 0:
            field_errors["progress"] = "Must be >= 0."
        else:
            entry.progress = progress

    if "rating" in data:
        rating = data.get("rating")
        if rating is None or rating == "":
            entry.rating = None
        elif not isinstance(rating, int):
            field_errors["rating"] = "Must be an integer between 1 and 10, or null."
        elif not (1 <= rating <= 10):
            field_errors["rating"] = "Must be between 1 and 10."
        else:
            entry.rating = rating

    if "notes" in data:
        notes = data.get("notes")
        if notes is None:
            notes = ""
        if not isinstance(notes, str):
            field_errors["notes"] = "Must be a string."
        else:
            entry.notes = notes

    if "is_favorite" in data:
        is_favorite = data.get("is_favorite")
        if not isinstance(is_favorite, bool):
            field_errors["is_favorite"] = "Must be true or false."
        else:
            entry.is_favorite = is_favorite

    if field_errors:
        return _json_error("Validation error.", status=400, errors=field_errors)

    entry.save(update_fields=["status", "progress", "rating", "notes", "is_favorite", "updated_at"])
    return JsonResponse({"ok": True, "result": _serialize_entry(entry)})
