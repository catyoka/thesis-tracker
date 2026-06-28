import json
from io import BytesIO
from difflib import SequenceMatcher

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import Avg, Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from PIL import Image, UnidentifiedImageError

from .anilist import fetch_media_catalog, fetch_media_details
from .models import CatalogItem, Friendship, LibraryEntry, UserProfile


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
        "progress": entry.progress,
        "rating": entry.rating,
        "notes": entry.notes,
        "is_favorite": entry.is_favorite,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


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


def _library_overview(user) -> dict:
    user_entries = LibraryEntry.objects.filter(user=user)
    anime_count = user_entries.filter(media_type=LibraryEntry.MediaType.ANIME).count()
    manga_count = user_entries.filter(media_type=LibraryEntry.MediaType.MANGA).count()
    total_count = anime_count + manga_count
    status_counts = {
        row["status"]: row["count"]
        for row in user_entries.values("status").annotate(count=Count("id"))
    }

    return {
        "total_count": total_count,
        "anime_count": anime_count,
        "manga_count": manga_count,
        "completed_count": user_entries.filter(status=LibraryEntry.Status.COMPLETED).count(),
        "active_count": user_entries.filter(status=LibraryEntry.Status.WATCHING).count(),
        "planned_count": user_entries.filter(status=LibraryEntry.Status.PLANNED).count(),
        "favorite_count": user_entries.filter(is_favorite=True).count(),
        "rated_count": user_entries.exclude(rating__isnull=True).count(),
        "average_rating": user_entries.aggregate(value=Avg("rating"))["value"],
        "status_summary": [
            {
                "value": value,
                "label": label,
                "count": status_counts.get(value, 0),
                "percent": round((status_counts.get(value, 0) / total_count) * 100) if total_count else 0,
            }
            for value, label in LibraryEntry.Status.choices
        ],
        "recent_entries": user_entries.order_by("-updated_at")[:5],
        "favorite_entries": user_entries.filter(is_favorite=True).order_by("-updated_at")[:5],
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


def _square_avatar_upload(uploaded_file, username: str) -> ContentFile:
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
    safe_username = "".join(ch for ch in username.lower() if ch.isalnum() or ch in {"-", "_"}) or "user"
    if has_transparency:
        image.save(buffer, format="PNG", optimize=True)
        extension = "png"
    else:
        image.save(buffer, format="JPEG", quality=88, optimize=True)
        extension = "jpg"
    return ContentFile(buffer.getvalue(), name=f"{safe_username}_avatar.{extension}")


def home_page(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("login")

    overview = _library_overview(request.user)

    return render(
        request,
        "tracker/home.html",
        overview,
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
            "profile_user": request.user,
            "profile": profile,
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
        avatar_file = request.FILES.get("avatar")

        if len(bio) > 500:
            errors["bio"] = "Bio must be 500 characters or fewer."

        if not errors:
            profile.bio = bio
            if avatar_file:
                try:
                    avatar_content = _square_avatar_upload(avatar_file, request.user.username)
                except ValueError as exc:
                    errors["avatar"] = str(exc)
                else:
                    if profile.avatar:
                        profile.avatar.delete(save=False)
                    profile.avatar.save(avatar_content.name, avatar_content, save=False)
                    profile.avatar_url = ""
            if not errors:
                profile.save(update_fields=["avatar", "avatar_url", "bio", "updated_at"])
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

    return render(
        request,
        "tracker/public_profile.html",
        {
            **overview,
            "profile_user": profile_user,
            "profile": profile,
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
    qs = base_qs

    status = request.GET.get("status") or ""
    media_type = request.GET.get("media_type") or ""
    favorite_filter = request.GET.get("favorite") == "1"
    if status:
        qs = qs.filter(status=status)
    if media_type:
        qs = qs.filter(media_type=media_type)
    if favorite_filter:
        qs = qs.filter(is_favorite=True)

    status_counts = {
        row["status"]: row["count"]
        for row in base_qs.values("status").annotate(count=Count("id"))
    }
    status_tabs = [
        {"value": "", "label": "All", "count": base_qs.count()},
        *[
            {"value": value, "label": label, "count": status_counts.get(value, 0)}
            for value, label in LibraryEntry.Status.choices
        ],
    ]

    return render(
        request,
        "tracker/library.html",
        {
            "entries": qs.order_by("-updated_at"),
            "status_filter": status,
            "media_type_filter": media_type,
            "favorite_filter": favorite_filter,
            "favorite_count": base_qs.filter(is_favorite=True).count(),
            "status_tabs": status_tabs,
            "status_choices": LibraryEntry.Status.choices,
            "media_type_choices": LibraryEntry.MediaType.choices,
        },
    )


@login_required
def media_catalog_page(request: HttpRequest, media_type: str) -> HttpResponse:
    normalized_type = media_type.upper()
    if normalized_type not in {CatalogItem.MediaType.ANIME, CatalogItem.MediaType.MANGA}:
        return redirect("tracker:anime_catalog")

    query = (request.GET.get("q") or "").strip()
    source_note = "Showing Top 50 popular titles from AniList."
    source_error = ""
    preferred_external_ids: list[str] = []

    try:
        remote_items = fetch_media_catalog(
            normalized_type,
            query,
            per_page=50,
        )
        preferred_external_ids = [data["external_id"] for data in remote_items]
        for data in remote_items:
            CatalogItem.objects.update_or_create(
                external_id=data["external_id"],
                defaults={
                    "title": data["title"],
                    "media_type": data["media_type"],
                    "description": data["description"],
                    "cover_image_url": data["cover_image_url"],
                },
            )
        if query:
            source_note = "Showing AniList search results (up to 50), cached locally."
    except RuntimeError:
        source_error = "AniList API is unavailable right now, showing cached data only."
        if query:
            source_note = "Showing locally cached search results."
        else:
            source_note = "Showing locally cached popular list."

    catalog_qs = CatalogItem.objects.filter(media_type=normalized_type)
    if query:
        catalog_qs = catalog_qs.filter(title__icontains=query)
    if preferred_external_ids:
        catalog_items = list(catalog_qs.filter(external_id__in=preferred_external_ids))
        position = {external_id: idx for idx, external_id in enumerate(preferred_external_ids)}
        catalog_items.sort(key=lambda item: position.get(item.external_id, 10**9))
    else:
        catalog_items = list(catalog_qs.order_by("title")[:100])
        if query:
            catalog_items = _rank_catalog_items_by_query(catalog_items, query)[:50]
        else:
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
        }
        for item in catalog_items
    ]

    return render(
        request,
        "tracker/catalog.html",
        {
            "item_rows": item_rows,
            "status_choices": LibraryEntry.Status.choices,
            "selected_media_type": normalized_type,
            "query": query,
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
                detail_title_obj = detail_data.get("title") or {}
                item.title = (
                    detail_title_obj.get("english")
                    or detail_title_obj.get("romaji")
                    or detail_title_obj.get("native")
                    or item.title
                )
                item.description = (detail_data.get("description") or item.description).strip()
                item.cover_image_url = ((detail_data.get("coverImage") or {}).get("large") or item.cover_image_url).strip()
                item.save(update_fields=["title", "description", "cover_image_url"])
            except RuntimeError:
                detail_error = "Could not load full details from AniList right now."

    user_entry = LibraryEntry.objects.filter(user=request.user, external_id=item.external_id).first()
    return render(
        request,
        "tracker/media_detail.html",
        {
            "item": item,
            "detail_data": detail_data,
            "detail_error": detail_error,
            "user_entry": user_entry,
            "status_choices": LibraryEntry.Status.choices,
        },
    )


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
