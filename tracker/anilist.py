import json
from urllib import error, request


ANILIST_API_URL = "https://graphql.anilist.co"


def _anilist_request(gql_query: str, variables: dict) -> dict:
    payload = {
        "query": gql_query,
        "variables": variables,
    }

    req = request.Request(
        ANILIST_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # AniList may reject generic urllib clients without an explicit UA.
            "User-Agent": "betteranilist/0.1 (+https://github.com/catyoka/thesis-tracker)",
            "Origin": "https://anilist.co",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"AniList request failed: {exc}") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AniList returned invalid JSON.") from exc

    if parsed.get("errors"):
        raise RuntimeError("AniList API returned an error response.")
    return parsed


def fetch_media_catalog(media_type: str, query: str, *, per_page: int = 25) -> list[dict]:
    """
    Fetch anime/manga list from AniList GraphQL API.
    Returns simplified dictionaries for local caching.
    """
    gql_query = """
    query ($type: MediaType, $search: String, $sort: [MediaSort], $perPage: Int) {
      Page(page: 1, perPage: $perPage) {
        media(type: $type, search: $search, sort: $sort) {
          id
          title {
            romaji
            english
            native
          }
          description(asHtml: false)
          coverImage {
            medium
          }
        }
      }
    }
    """

    parsed = _anilist_request(
        gql_query,
        {
            "type": media_type,
            "search": query or None,
            "sort": ["POPULARITY_DESC"],
            "perPage": per_page,
        },
    )

    media_items = (((parsed.get("data") or {}).get("Page") or {}).get("media")) or []
    results: list[dict] = []
    for media in media_items:
        media_id = media.get("id")
        if not media_id:
            continue

        title_obj = media.get("title") or {}
        title = (
            title_obj.get("english")
            or title_obj.get("romaji")
            or title_obj.get("native")
            or f"AniList {media_id}"
        )
        description = media.get("description") or ""
        cover_obj = media.get("coverImage") or {}
        cover_url = cover_obj.get("medium") or ""

        results.append(
            {
                "external_id": f"anilist:{media_id}",
                "title": title.strip(),
                "media_type": media_type,
                "description": description.strip(),
                "cover_image_url": cover_url,
            }
        )

    return results


def fetch_media_details(anilist_id: int) -> dict:
    gql_query = """
    query ($id: Int) {
      Media(id: $id) {
        id
        siteUrl
        title {
          romaji
          english
          native
        }
        description(asHtml: false)
        coverImage {
          large
        }
        genres
        averageScore
        episodes
        chapters
        volumes
        format
        status
      }
    }
    """
    parsed = _anilist_request(gql_query, {"id": anilist_id})
    media = (parsed.get("data") or {}).get("Media") or {}
    if not media:
        raise RuntimeError("AniList media details not found.")
    return media
