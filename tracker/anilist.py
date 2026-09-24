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
            # I send my own user agent because AniList can reject urllib's default one.
            "User-Agent": "Yulhaverse/0.1 (+https://github.com/catyoka/thesis-tracker)",
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


def _media_tag_names(media: dict) -> list[str]:
    tag_names = []
    seen = set()
    for tag in media.get("tags") or []:
        if not isinstance(tag, dict) or tag.get("isAdult"):
            continue
        name = str(tag.get("name") or "").strip()
        if not name or name in seen:
            continue
        tag_names.append(name)
        seen.add(name)
    return tag_names


def fetch_media_catalog(
    media_type: str,
    query: str,
    *,
    per_page: int = 25,
    genres: list[str] | tuple[str, ...] | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """I fetch AniList catalog results and simplify them for my local cache."""
    gql_query = """
    query (
      $type: MediaType,
      $search: String,
      $sort: [MediaSort],
      $perPage: Int,
      $genres: [String],
      $tags: [String]
    ) {
      Page(page: 1, perPage: $perPage) {
        media(type: $type, search: $search, genre_in: $genres, tag_in: $tags, sort: $sort) {
          id
          title {
            romaji
            english
            native
          }
          description(asHtml: false)
          siteUrl
          coverImage {
            medium
            large
          }
          trailer {
            id
            site
            thumbnail
          }
          genres
          tags {
            name
            rank
            isAdult
          }
          averageScore
          episodes
          chapters
          volumes
          format
          status
          seasonYear
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
            "genres": list(genres or []) or None,
            "tags": list(tags or []) or None,
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
        cover_url = cover_obj.get("large") or cover_obj.get("medium") or ""
        trailer_obj = media.get("trailer") or {}

        results.append(
            {
                "external_id": f"anilist:{media_id}",
                "title": title.strip(),
                "media_type": media_type,
                "description": description.strip(),
                "cover_image_url": cover_url,
                "trailer_id": trailer_obj.get("id") or "",
                "trailer_site": trailer_obj.get("site") or "",
                "trailer_thumbnail_url": trailer_obj.get("thumbnail") or "",
                "genres": media.get("genres") or [],
                "tags": _media_tag_names(media),
                "average_score": media.get("averageScore"),
                "episodes": media.get("episodes"),
                "chapters": media.get("chapters"),
                "volumes": media.get("volumes"),
                "format": media.get("format") or "",
                "release_status": media.get("status") or "",
                "season_year": media.get("seasonYear"),
                "site_url": media.get("siteUrl") or "",
            }
        )

    return results


def _character_connection_query() -> str:
    return """
    query ($id: Int, $page: Int) {
      Media(id: $id) {
        characters(sort: [ROLE, RELEVANCE, ID], page: $page, perPage: 50) {
          pageInfo {
            currentPage
            hasNextPage
          }
          edges {
            role
            node {
              name {
                full
              }
              image {
                medium
                large
              }
            }
            voiceActors(language: JAPANESE, sort: [RELEVANCE, ID]) {
              name {
                full
              }
              image {
                medium
                large
              }
            }
          }
        }
      }
    }
    """


def _append_remaining_character_pages(anilist_id: int, media: dict) -> None:
    characters = (media.get("characters") or {})
    page_info = characters.get("pageInfo") or {}
    edges = characters.get("edges") or []

    while page_info.get("hasNextPage"):
        next_page = (page_info.get("currentPage") or 1) + 1
        parsed = _anilist_request(_character_connection_query(), {"id": anilist_id, "page": next_page})
        next_characters = (((parsed.get("data") or {}).get("Media") or {}).get("characters")) or {}
        next_edges = next_characters.get("edges") or []
        if not next_edges:
            break
        edges.extend(next_edges)
        page_info = next_characters.get("pageInfo") or {}

    characters["edges"] = edges
    characters["pageInfo"] = page_info
    media["characters"] = characters


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
        siteUrl
        coverImage {
          large
          medium
        }
        trailer {
          id
          site
          thumbnail
        }
        recommendations(sort: [RATING_DESC], page: 1, perPage: 12) {
          nodes {
            rating
            mediaRecommendation {
              id
              type
              title {
                romaji
                english
                native
              }
              description(asHtml: false)
              siteUrl
              coverImage {
                large
                medium
              }
              genres
              tags {
                name
                rank
                isAdult
              }
              averageScore
              episodes
              chapters
              volumes
              format
              status
              seasonYear
            }
          }
        }
        staff(sort: [RELEVANCE, ID], perPage: 30) {
          edges {
            role
            node {
              name {
                full
              }
              image {
                medium
                large
              }
            }
          }
        }
        characters(sort: [ROLE, RELEVANCE, ID], page: 1, perPage: 50) {
          pageInfo {
            currentPage
            hasNextPage
          }
          edges {
            role
            node {
              name {
                full
              }
              image {
                medium
                large
              }
            }
            voiceActors(language: JAPANESE, sort: [RELEVANCE, ID]) {
              name {
                full
              }
              image {
                medium
                large
              }
            }
          }
        }
        genres
        tags {
          name
          rank
          isAdult
        }
        averageScore
        episodes
        chapters
        volumes
        format
        status
        seasonYear
      }
    }
    """
    parsed = _anilist_request(gql_query, {"id": anilist_id})
    media = (parsed.get("data") or {}).get("Media") or {}
    if not media:
        raise RuntimeError("AniList media details not found.")
    _append_remaining_character_pages(anilist_id, media)
    return media
