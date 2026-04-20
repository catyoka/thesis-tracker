import json

from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .anilist import fetch_media_catalog
from .models import CatalogItem, LibraryEntry


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
    We want `/api/*` to return JSON 401 for unauthenticated users.
    Django's CSRF middleware runs before auth and would otherwise return 403 HTML/CSRF
    for unauthenticated unsafe methods. To avoid that, API views are `csrf_exempt`
    and we enforce CSRF manually for authenticated unsafe requests.
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
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }

def home_page(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("login")

    user_entries = LibraryEntry.objects.filter(user=request.user)
    anime_count = user_entries.filter(media_type=LibraryEntry.MediaType.ANIME).count()
    manga_count = user_entries.filter(media_type=LibraryEntry.MediaType.MANGA).count()
    total_count = anime_count + manga_count
    recent_entries = user_entries.order_by("-updated_at")[:5]

    return render(
        request,
        "tracker/home.html",
        {
            "total_count": total_count,
            "anime_count": anime_count,
            "manga_count": manga_count,
            "recent_entries": recent_entries,
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
    qs = LibraryEntry.objects.filter(user=request.user)

    status = request.GET.get("status") or ""
    media_type = request.GET.get("media_type") or ""
    if status:
        qs = qs.filter(status=status)
    if media_type:
        qs = qs.filter(media_type=media_type)

    return render(
        request,
        "tracker/library.html",
        {
            "entries": qs.order_by("-updated_at"),
            "status_filter": status,
            "media_type_filter": media_type,
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
        catalog_items = list(catalog_qs.order_by("title")[:50])

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

    allowed_fields = {"status", "progress", "rating", "notes"}
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

    if field_errors:
        return _json_error("Validation error.", status=400, errors=field_errors)

    entry.save(update_fields=["status", "progress", "rating", "notes", "updated_at"])
    return JsonResponse({"ok": True, "result": _serialize_entry(entry)})
