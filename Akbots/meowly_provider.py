# Akbots - Don't Remove Credit - @AkBots_Official
#
# Meowly provider — ported from the meowly project. Unlike MeowTV/MeowVerse/
# MeowToon, meowly has no proprietary scraper/decrypt logic of its own: it's
# a TMDB-metadata browser (src/lib/tmdb.ts) whose player just embeds public
# third-party iframes (src/components/VideoPlayer.tsx). Both halves are
# fully public REST/URL-template logic, so this port is a straight 1:1.
#
# Needs TMDB_API_KEY in config.py/.env (a free key from
# https://www.themoviedb.org/settings/api).

import asyncio
import logging

import aiohttp

from Akbots import meowly_resolvers

try:
    from config import TMDB_API_KEY
except ImportError:
    TMDB_API_KEY = ""

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

POSTER_SIZES = {"small": f"{IMAGE_BASE_URL}/w185", "medium": f"{IMAGE_BASE_URL}/w342", "large": f"{IMAGE_BASE_URL}/w780"}
BACKDROP_SIZES = {"small": f"{IMAGE_BASE_URL}/w300", "medium": f"{IMAGE_BASE_URL}/w780", "large": f"{IMAGE_BASE_URL}/w1280"}


def is_configured() -> bool:
    return bool(TMDB_API_KEY)


def poster_url(path: str | None, size: str = "medium") -> str | None:
    return f"{POSTER_SIZES[size]}{path}" if path else None


def backdrop_url(path: str | None, size: str = "large") -> str | None:
    return f"{BACKDROP_SIZES[size]}{path}" if path else None


async def _fetch_tmdb(endpoint: str, params: dict = None, retries: int = 3):
    if not TMDB_API_KEY:
        return None
    params = dict(params or {})
    params["api_key"] = TMDB_API_KEY
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    url = f"{BASE_URL}{endpoint}"

    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params,
                    headers={"Accept": "application/json", "User-Agent": "MeowlyApp/1.0"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    if r.status == 401:
                        return None
                    if r.status == 429:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    if not r.ok:
                        return None
                    return await r.json()
        except Exception:
            await asyncio.sleep(2)
    return None


async def search(query: str) -> list[dict]:
    """Movies + TV + people + companies — matches meowly's src/lib/tmdb.ts
    tmdb.search(), which runs /search/multi (filtered to movie/tv/person)
    and /search/company in parallel and concatenates them."""
    multi_task = _fetch_tmdb("/search/multi", {"query": query})
    company_task = _fetch_tmdb("/search/company", {"query": query})
    multi_data, company_data = await asyncio.gather(multi_task, company_task)

    multi_results = [
        r for r in ((multi_data or {}).get("results") or [])
        if r.get("media_type") in ("movie", "tv", "person")
    ]
    company_results = [
        {**r, "media_type": "company"} for r in ((company_data or {}).get("results") or [])
    ]
    return multi_results + company_results


async def get_trending(kind: str = "all") -> list[dict]:
    data = await _fetch_tmdb(f"/trending/{kind}/day")
    return (data or {}).get("results") or []


async def get_top_rated(kind: str) -> list[dict]:
    data = await _fetch_tmdb(f"/{kind}/top_rated")
    return [{**r, "media_type": kind} for r in ((data or {}).get("results") or [])]


async def get_popular(kind: str) -> list[dict]:
    data = await _fetch_tmdb(f"/{kind}/popular")
    return [{**r, "media_type": kind} for r in ((data or {}).get("results") or [])]


async def get_details(kind: str, item_id: str) -> dict | None:
    data = await _fetch_tmdb(f"/{kind}/{item_id}", {
        "append_to_response": "videos,credits,recommendations,similar,release_dates,"
                               "content_ratings,images,keywords,external_ids",
        "include_image_language": "en,null",
    })
    return data or {}


async def get_season_details(tv_id: str, season_number: int) -> dict | None:
    data = await _fetch_tmdb(f"/tv/{tv_id}/season/{season_number}")
    return data or {}


async def get_trailer(kind: str, item_id: str) -> str | None:
    data = await _fetch_tmdb(f"/{kind}/{item_id}/videos")
    videos = (data or {}).get("results") or []
    trailer = (
        next((v for v in videos if v.get("type") == "Trailer" and v.get("site") == "YouTube"), None)
        or next((v for v in videos if v.get("type") == "Teaser" and v.get("site") == "YouTube"), None)
        or next((v for v in videos if v.get("site") == "YouTube"), None)
    )
    return f"https://www.youtube.com/embed/{trailer['key']}?autoplay=1" if trailer else None


async def get_genre_list(kind: str) -> list[dict]:
    data = await _fetch_tmdb(f"/genre/{kind}/list")
    return (data or {}).get("genres") or []


async def get_discover(kind: str, genre_id: str = None, year: str = None,
                        sort_by: str = "popularity.desc") -> list[dict]:
    params = {"sort_by": sort_by, "include_adult": "false", "vote_count.gte": "100"}
    if genre_id:
        params["with_genres"] = genre_id
    if year:
        params["primary_release_year" if kind == "movie" else "first_air_date_year"] = year
    data = await _fetch_tmdb(f"/discover/{kind}", params)
    return [{**r, "media_type": kind} for r in ((data or {}).get("results") or [])]


async def get_person_details(person_id: str) -> dict:
    data = await _fetch_tmdb(f"/person/{person_id}", {"append_to_response": "external_ids,images"})
    return data or {}


async def get_person_credits(person_id: str) -> dict:
    data = await _fetch_tmdb(f"/person/{person_id}/combined_credits")
    return data or {}


async def get_popular_people(page: int = 1) -> dict:
    data = await _fetch_tmdb("/person/popular", {"page": str(page)})
    return data or {}


async def get_collection(collection_id: str) -> dict:
    data = await _fetch_tmdb(f"/collection/{collection_id}")
    return data or {}


async def get_list_details(list_id: str) -> list[dict]:
    data = await _fetch_tmdb(f"/list/{list_id}")
    if not data:
        return []

    all_items = list(data.get("items") or [])
    total_pages = data.get("total_pages") or 1
    if total_pages > 1:
        pages = await asyncio.gather(*[
            _fetch_tmdb(f"/list/{list_id}", {"page": str(p)}) for p in range(2, total_pages + 1)
        ])
        for res in pages:
            if res and res.get("items"):
                all_items.extend(res["items"])

    return [{**item, "media_type": item.get("media_type") or "movie"} for item in all_items]


async def get_upcoming() -> list[dict]:
    data = await _fetch_tmdb("/movie/upcoming")
    return [{**r, "media_type": "movie"} for r in ((data or {}).get("results") or [])]


async def get_now_playing() -> list[dict]:
    data = await _fetch_tmdb("/movie/now_playing")
    return [{**r, "media_type": "movie"} for r in ((data or {}).get("results") or [])]


async def get_airing_today() -> list[dict]:
    data = await _fetch_tmdb("/tv/airing_today")
    return [{**r, "media_type": "tv"} for r in ((data or {}).get("results") or [])]


async def get_on_the_air() -> list[dict]:
    data = await _fetch_tmdb("/tv/on_the_air")
    return [{**r, "media_type": "tv"} for r in ((data or {}).get("results") or [])]


async def get_random_content() -> dict | None:
    import random
    kind = random.choice(["movie", "tv"])
    page = random.randint(1, 5)
    data = await _fetch_tmdb(f"/{kind}/popular", {"page": str(page)})
    results = (data or {}).get("results") or []
    if not results:
        return None
    return {**random.choice(results), "media_type": kind}


async def get_company_details(company_id: str) -> dict | None:
    return await _fetch_tmdb(f"/company/{company_id}")


async def get_network_details(network_id: str) -> dict | None:
    return await _fetch_tmdb(f"/network/{network_id}")


async def get_discover_by_company(company_id: str, kind: str = "movie", page: int = 1) -> dict:
    data = await _fetch_tmdb(f"/discover/{kind}", {
        "with_companies": company_id, "sort_by": "popularity.desc",
        "include_adult": "false", "page": str(page),
    })
    results = [{**r, "media_type": kind} for r in ((data or {}).get("results") or [])]
    return {
        "results": results,
        "total_pages": (data or {}).get("total_pages") or 1,
        "total_results": (data or {}).get("total_results") or 0,
    }


# ── Curated genre rows (ported from meowly's app/actions.ts getNextGenresAction) ──
# Hardcoded title/genreId lists the homepage uses for its infinite-scrolling
# genre rows (src/components/InfiniteGenres.tsx). Same three lists, same order.

_GENRE_LISTS = {
    "all": [
        {"title": "Sci-Fi & Fantasy", "type": "tv", "genreId": "10765"},
        {"title": "Adventure Quests", "type": "movie", "genreId": "12"},
        {"title": "Gripping Dramas", "type": "movie", "genreId": "18"},
        {"title": "Chilling Horror", "type": "movie", "genreId": "27"},
        {"title": "Crime Thrillers", "type": "movie", "genreId": "80"},
        {"title": "Animated Wonders", "type": "movie", "genreId": "16"},
        {"title": "Mystery & Suspense", "type": "movie", "genreId": "9648"},
        {"title": "Romantic Getaways", "type": "movie", "genreId": "10749"},
        {"title": "Reality Obsessions", "type": "tv", "genreId": "10764"},
        {"title": "Insightful Documentaries", "type": "movie", "genreId": "99"},
        {"title": "Wild West Tales", "type": "movie", "genreId": "37"},
        {"title": "Musical Journeys", "type": "movie", "genreId": "10402"},
        {"title": "Historic Wars", "type": "movie", "genreId": "36"},
    ],
    "movie": [
        {"title": "Sci-Fi & Fantasy", "type": "movie", "genreId": "878"},
        {"title": "Adventure Quests", "type": "movie", "genreId": "12"},
        {"title": "Animated Wonders", "type": "movie", "genreId": "16"},
        {"title": "Comedy Hits", "type": "movie", "genreId": "35"},
        {"title": "Crime Thrillers", "type": "movie", "genreId": "80"},
        {"title": "Insightful Documentaries", "type": "movie", "genreId": "99"},
        {"title": "Romantic Getaways", "type": "movie", "genreId": "10749"},
        {"title": "Mystery & Suspense", "type": "movie", "genreId": "9648"},
        {"title": "Historic Wars", "type": "movie", "genreId": "36"},
        {"title": "Wild West Tales", "type": "movie", "genreId": "37"},
        {"title": "Musical Journeys", "type": "movie", "genreId": "10402"},
    ],
    "tv": [
        {"title": "Action & Adventure", "type": "tv", "genreId": "10759"},
        {"title": "Animated Wonders", "type": "tv", "genreId": "16"},
        {"title": "Comedy Hits", "type": "tv", "genreId": "35"},
        {"title": "Crime Thrillers", "type": "tv", "genreId": "80"},
        {"title": "Mystery & Suspense", "type": "tv", "genreId": "9648"},
        {"title": "Reality Obsessions", "type": "tv", "genreId": "10764"},
        {"title": "Insightful Documentaries", "type": "tv", "genreId": "99"},
        {"title": "Historic Wars", "type": "tv", "genreId": "10768"},
        {"title": "Wild West Tales", "type": "tv", "genreId": "37"},
        {"title": "Musical Journeys", "type": "tv", "genreId": "10402"},
    ],
}


# ── Award-winner lists (ported from app/awards/page.tsx) ────────────────
# Curated TMDB list IDs the website's Awards page fetches via getListDetails.

AWARD_LISTS = {
    "oscars": {"name": "Academy Awards — Best Picture", "listId": 28},
    "globes_drama": {"name": "Golden Globes — Best Drama", "listId": 234},
    "globes_comedy": {"name": "Golden Globes — Best Comedy/Musical", "listId": 235},
    "cannes": {"name": "Cannes Film Festival — Palme d'Or", "listId": 229},
    "venice": {"name": "Venice Film Festival — Golden Lion", "listId": 230},
    "berlin": {"name": "Berlin Film Festival — Golden Bear", "listId": 267},
    "oscars_animated": {"name": "Academy Awards — Best Animated Feature", "listId": 265},
    "oscars_foreign": {"name": "Academy Awards — Best International Feature", "listId": 264},
    "oscars_documentary": {"name": "Academy Awards — Best Documentary Feature", "listId": 266},
    "filmfare": {"name": "Filmfare Awards", "listId": 365},
}


async def get_awards() -> dict[str, dict]:
    """Fetch all 10 award lists concurrently — mirrors AwardsPage's Promise.all."""
    keys = list(AWARD_LISTS.keys())

    async def _one(key):
        try:
            return await get_list_details(AWARD_LISTS[key]["listId"])
        except Exception:
            return []

    results = await asyncio.gather(*[_one(k) for k in keys])
    return {
        key: {"name": AWARD_LISTS[key]["name"], "movies": movies}
        for key, movies in zip(keys, results)
    }


async def get_next_genres(start_index: int, limit: int = 3, kind: str = "all") -> list[dict]:
    genre_list = _GENRE_LISTS.get(kind, _GENRE_LISTS["all"])
    slice_ = genre_list[start_index:start_index + limit]
    if not slice_:
        return []

    async def _row(g):
        try:
            movies = await get_discover(g["type"], genre_id=g["genreId"])
        except Exception:
            movies = []
        return {"title": g["title"], "movies": movies or []}

    rows = await asyncio.gather(*[_row(g) for g in slice_])
    return [r for r in rows if r["movies"]]


async def get_discover_by_network(network_id: str, page: int = 1) -> dict:
    data = await _fetch_tmdb("/discover/tv", {
        "with_networks": network_id, "sort_by": "popularity.desc",
        "include_adult": "false", "page": str(page),
    })
    results = [{**r, "media_type": "tv"} for r in ((data or {}).get("results") or [])]
    return {
        "results": results,
        "total_pages": (data or {}).get("total_pages") or 1,
        "total_results": (data or {}).get("total_results") or 0,
    }


# ── Public embed servers (ported from VideoPlayer.tsx) ─────────────────
# These are third-party embed sites, same ones the original Next.js site
# links to — no scraping/decryption involved, just URL templates. Full
# 12-server list, same order as meowly's src/components/VideoPlayer.tsx.

def embed_links(kind: str, item_id: str, season: int = None, episode: int = None) -> list[dict]:
    if kind == "movie":
        return [
            {"name": "APIPlayer", "url": f"https://apiplayer.ru/embed/movie/{item_id}"},
            {"name": "PrimeSRC", "url": f"https://primesrc.me/embed/movie?tmdb={item_id}"},
            {"name": "Vidfast", "url": f"https://vidfast.net/movie/{item_id}"},
            {"name": "VidSrc PM", "url": f"https://vidsrc.pm/embed/movie/{item_id}"},
            {"name": "Peachify", "url": f"https://peachify.top/embed/movie/{item_id}"},
            {"name": "VidSrc SU", "url": f"https://vidsrcme.su/embed/movie?tmdb={item_id}"},
            {"name": "Videasy", "url": f"https://player.videasy.net/movie/{item_id}"},
            {"name": "Vidking", "url": f"https://www.vidking.net/embed/movie/{item_id}"},
            {"name": "2Embed", "url": f"https://www.2embed.skin/embed/{item_id}"},
            {"name": "AutoEmbed", "url": f"https://autoembed.co/movie/{item_id}"},
            {"name": "Vidlink", "url": f"https://vidlink.pro/movie/{item_id}"},
            {"name": "Vidrock", "url": f"https://vidrock.net/embed/movie/{item_id}"},
        ]
    # tv
    s, e = season or 1, episode or 1
    return [
        {"name": "APIPlayer", "url": f"https://apiplayer.ru/embed/tv/{item_id}/{s}/{e}"},
        {"name": "PrimeSRC", "url": f"https://primesrc.me/embed/tv?tmdb={item_id}&season={s}&episode={e}"},
        {"name": "Vidfast", "url": f"https://vidfast.net/tv/{item_id}/{s}/{e}"},
        {"name": "VidSrc PM", "url": f"https://vidsrc.pm/embed/tv/{item_id}/{s}/{e}"},
        {"name": "Peachify", "url": f"https://peachify.top/embed/tv/{item_id}/{s}/{e}"},
        {"name": "VidSrc SU", "url": f"https://vidsrcme.su/embed/tv?tmdb={item_id}&season={s}&episode={e}"},
        {"name": "Videasy", "url": f"https://player.videasy.net/tv/{item_id}/{s}/{e}"},
        {"name": "Vidking", "url": f"https://www.vidking.net/embed/tv/{item_id}/{s}/{e}"},
        {"name": "2Embed", "url": f"https://www.2embed.skin/embedtv/{item_id}&s={s}&e={e}"},
        {"name": "AutoEmbed", "url": f"https://autoembed.co/tv/{item_id}/{s}/{e}"},
        {"name": "Vidlink", "url": f"https://vidlink.pro/tv/{item_id}/{s}/{e}"},
        {"name": "Vidrock", "url": f"https://vidrock.net/embed/tv/{item_id}/{s}/{e}"},
    ]


# ── Real stream resolving (for /meow-style download-to-Telegram) ───────
# embed_links() above only gives iframe URLs to third-party players — fine
# for "watch in browser", but not a fetchable file. This calls
# meowly_resolvers.resolve(), which ports the actual scrape/decrypt logic
# 4 of those 12 players use client-side (vidsrc/vidrock/peachify/videasy),
# and returns a stream dict shaped like meowtv/meowverse/meowtoon's
# fetch_stream_url() so Akbots/meow_downloader.py & meow_commands.py's
# download flow can drive it unmodified.

async def fetch_stream_url(media_type: str, item_id, season: int = None, episode: int = None,
                            title: str = "") -> dict | None:
    return await meowly_resolvers.resolve(str(item_id), season, episode, title)


# ── Moctale reviews (ported from meowly's app/actions.ts) ──────────────
# Optional add-on: pulls community review summaries for a Moctale content
# slug. Needs MOCTALE_COOKIE in config.py/.env (a session cookie/token from
# a logged-in moctale.in account) — without it this just returns None.

def get_moctale_slug(title: str, date: str = None) -> str:
    """Ported 1:1 from MoctaleReviews.tsx's getMoctaleSlug()."""
    import re
    import unicodedata
    if not title:
        return ""
    normalized = unicodedata.normalize("NFD", title.lower())
    stripped = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    slug = re.sub(r"[^a-z0-9\s-]", "", stripped).strip()
    slug = re.sub(r"\s+", "-", slug)
    if date:
        year = date.split("-")[0]
        if len(year) == 4 and not slug.endswith(year):
            slug = f"{slug}-{year}"
    return slug


async def get_moctale_reviews(slug: str) -> dict | None:
    try:
        from config import MOCTALE_COOKIE
    except ImportError:
        MOCTALE_COOKIE = ""
    if not MOCTALE_COOKIE:
        return None

    cookie = MOCTALE_COOKIE
    if "auth_token=" in cookie:
        import re
        m = re.search(r"auth_token=([^;]+)", cookie)
        if m:
            cookie = f"auth_token={m.group(1)}"
    elif "=" not in cookie:
        cookie = f"auth_token={cookie}"

    url = f"https://www.moctale.in/api/activity/content/{slug}/reviews-summary"
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,hi;q=0.8",
        "cache-control": "no-cache",
        "dnt": "1",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": f"https://www.moctale.in/content/{slug}",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "cookie": cookie,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    logger.debug(f"meowly: moctale reviews HTTP {r.status} for {slug}")
                    return None
                return await r.json(content_type=None)
    except Exception as e:
        logger.debug(f"meowly: moctale reviews failed for {slug}: {e}")
        return None
