# Akbots - Don't Remove Credit - @AkBots_Official
#
# Xon content source — ported from the meowtv project's src/lib/xon.ts.
# This is the second half of "MeowToon" (the first half is Kartoons, in
# meowtoon_provider.py) — meowtoon.ts merges both together, and so does
# Akbots/meowtoon_provider.py in this port.
#
# Self-contained: no crypto/decrypt dependency (auth is a plain Firebase
# REST anonymous sign-up + Firestore settings doc, same as the TS
# original). Uses a process-wide in-memory cache refreshed every 24h,
# exactly like xon.ts's module-level cache.

import time
import asyncio
import logging

import aiohttp

from Akbots.hls_proxy import build_hls_url, build_proxy_url

logger = logging.getLogger(__name__)

_DEFAULT_MAIN_URL = "http://myavens18052002.xyz/nzapis"
_DEFAULT_API_KEY = "553y845hfhdlfhjkl438943943839443943fdhdkfjfj9834lnfd98"

_main_url = _DEFAULT_MAIN_URL
_api_key = _DEFAULT_API_KEY
_auth_token: str | None = None
_auth_expire_time = 0.0
_did_try_settings = False

_CACHE_REFRESH_SECONDS = 24 * 60 * 60

_cache = {
    "languages": [], "shows": [], "seasons": [], "episodes": [], "movies": [],
    "last_cache_time": 0.0,
    "by_id": {"languages": {}, "shows": {}, "seasons": {}, "episodes": {}, "movies": {}},
    "seasons_by_show": {},
    "episodes_by_season": {},
}

_refresh_lock = asyncio.Lock()


def _headers() -> dict:
    return {
        "api": _api_key,
        "caller": "vion-official-app",
        "Cache-Control": "no-cache",
        "Accept": "application/json",
        "User-Agent": "okhttp/3.14.9",
    }


def _format_media_url(u: str) -> str:
    if not u:
        return u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return f"https://archive.org/download/{u}"


def _language_name(language_id) -> str:
    lang = _cache["by_id"]["languages"].get(language_id)
    return lang.get("name") if lang else "Unknown"


def _show_name(show_id) -> str:
    show = _cache["by_id"]["shows"].get(show_id)
    return show.get("name") if show else "Unknown Show"


def _rebuild_indexes():
    _cache["by_id"]["languages"] = {l.get("id"): l for l in _cache["languages"]}
    _cache["by_id"]["shows"] = {s.get("id"): s for s in _cache["shows"]}
    _cache["by_id"]["seasons"] = {s.get("id"): s for s in _cache["seasons"]}
    _cache["by_id"]["episodes"] = {e.get("id"): e for e in _cache["episodes"]}
    _cache["by_id"]["movies"] = {m.get("id"): m for m in _cache["movies"]}

    seasons_by_show = {}
    for season in _cache["seasons"]:
        seasons_by_show.setdefault(season.get("show_id"), []).append(season)
    _cache["seasons_by_show"] = seasons_by_show

    episodes_by_season = {}
    for ep in _cache["episodes"]:
        episodes_by_season.setdefault(ep.get("season_id"), []).append(ep)
    _cache["episodes_by_season"] = episodes_by_season


async def _authenticate_and_get_settings(session: aiohttp.ClientSession):
    global _auth_token, _auth_expire_time, _did_try_settings, _main_url, _api_key
    if _did_try_settings:
        return
    _did_try_settings = True

    try:
        async with session.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
            "?key=AIzaSyAC__yhrI4ExLcqWbZjsLN33_gVgyp6w3A",
            headers={"Content-Type": "application/json"}, json={},
        ) as r:
            if not r.ok:
                raise RuntimeError(f"Firebase auth HTTP {r.status}")
            auth_data = await r.json()

        _auth_token = auth_data.get("idToken")
        try:
            expires_seconds = int(auth_data.get("expiresIn", "3600"))
        except (TypeError, ValueError):
            expires_seconds = 3600
        _auth_expire_time = time.time() + expires_seconds

        async with session.get(
            "https://firestore.googleapis.com/v1/projects/xon-app/databases/"
            "(default)/documents/settings/BvJwsNb0eaObbigSefkm",
            headers={"Authorization": f"Bearer {_auth_token}"},
        ) as r:
            if not r.ok:
                raise RuntimeError(f"Firestore settings HTTP {r.status}")
            settings = await r.json()

        fields = settings.get("fields") or {}
        api = (fields.get("api") or {}).get("stringValue")
        base = (fields.get("base") or {}).get("stringValue")
        if api:
            _api_key = api
        if base:
            _main_url = base.rstrip("/")
    except Exception as e:
        # Fall back to hardcoded defaults, same as the TS original.
        logger.warning(f"xon_provider: settings fetch failed, using defaults: {e!r}")


async def _fetch_json(session: aiohttp.ClientSession, url: str, headers: dict):
    async with session.get(url, headers=headers) as r:
        if not r.ok:
            body = (await r.text())[:200]
            raise RuntimeError(f"HTTP {r.status} for {url}: {body}")
        return await r.json(content_type=None)


async def refresh_cache(force: bool = False):
    async with _refresh_lock:
        now = time.time()

        async with aiohttp.ClientSession() as session:
            await _authenticate_and_get_settings(session)

            if (not force and now - _cache["last_cache_time"] < _CACHE_REFRESH_SECONDS
                    and _cache["languages"] and _cache["shows"]):
                return

            headers = _headers()
            base = _main_url.rstrip("/")

            try:
                languages, shows, seasons, episodes_resp, movies = await asyncio.gather(
                    _fetch_json(session, f"{base}/nzgetlanguages.php", headers),
                    _fetch_json(session, f"{base}/nzgetshows.php", headers),
                    _fetch_json(session, f"{base}/nzgetseasons.php", headers),
                    _fetch_json(session, f"{base}/nzgetepisodes_v2.php?since=", headers),
                    _fetch_json(session, f"{base}/nzgetmovies.php", headers),
                )

                _cache["languages"] = languages if isinstance(languages, list) else []
                _cache["shows"] = shows if isinstance(shows, list) else []
                _cache["seasons"] = seasons if isinstance(seasons, list) else []
                _cache["episodes"] = (episodes_resp or {}).get("episodes") or []
                _cache["movies"] = movies if isinstance(movies, list) else []
                _rebuild_indexes()
                _cache["last_cache_time"] = now
            except Exception as e:
                logger.warning(f"xon_provider: refresh_cache fetch failed: {e!r}")
                # Keep whatever we already have instead of blanking the homepage.
                if _cache["last_cache_time"] and _cache["shows"]:
                    return
                raise


def _show_item(s: dict) -> dict:
    img = _format_media_url((s.get("cover") or s.get("thumb") or "").strip())
    return {"id": f"show:{s.get('id')}", "type": "series",
            "title": f"{s.get('name')} ({_language_name(s.get('language'))})",
            "poster": img, "backdrop": img, "description": s.get("des"), "source": "xon"}


def _episode_item(e: dict) -> dict:
    thumb = _format_media_url((e.get("thumb") or "").strip())
    cover = _format_media_url((e.get("cover") or e.get("thumb") or "").strip())
    return {"id": f"episode:{e.get('id')}", "type": "episode",
            "title": f"{_show_name(e.get('show_id'))} - {e.get('name')} ({_language_name(e.get('language'))})",
            "poster": thumb, "backdrop": cover, "description": e.get("des"), "source": "xon"}


def _movie_item(m: dict) -> dict:
    img = _format_media_url((m.get("cover") or m.get("thumb") or "").strip())
    return {"id": f"movie:{m.get('id')}", "type": "movie",
            "title": f"{m.get('name')} ({_language_name(m.get('language'))})",
            "poster": img, "backdrop": img, "description": m.get("des"), "source": "xon"}


async def fetch_home() -> list[dict]:
    await refresh_cache()
    rows = [
        {"name": "Trending Shows", "items": [_show_item(s) for s in _cache["shows"][:20]]},
        {"name": "Latest Episodes", "items": [_episode_item(e) for e in _cache["episodes"][:20]]},
        {"name": "Movies", "items": [_movie_item(m) for m in _cache["movies"][:20]]},
    ]
    return [r for r in rows if r["items"]]


async def search(query: str) -> list[dict]:
    await refresh_cache()
    q = query.strip().lower()
    if not q:
        return []

    out = []
    for s in _cache["shows"]:
        hay = f"{s.get('name') or ''} {s.get('des') or ''}".lower()
        if q in hay:
            out.append(_show_item(s))
    for e in _cache["episodes"]:
        hay = f"{e.get('name') or ''} {e.get('tags') or ''}".lower()
        if q in hay:
            out.append(_episode_item(e))
    for m in _cache["movies"]:
        hay = f"{m.get('name') or ''} {m.get('des') or ''} {m.get('tags') or ''}".lower()
        if q in hay:
            out.append(_movie_item(m))
    return out[:60]


async def fetch_details(content_id: str) -> dict | None:
    await refresh_cache()
    kind, _, raw_id = str(content_id).partition(":")
    try:
        num_id = int(raw_id)
    except ValueError:
        return None
    if not kind:
        return None

    if kind == "show":
        show = _cache["by_id"]["shows"].get(num_id)
        if not show:
            return None

        episodes = []
        for season in _cache["seasons_by_show"].get(num_id, []):
            for ep in _cache["episodes_by_season"].get(season.get("id"), []):
                episodes.append({
                    "id": f"episode:{ep.get('id')}", "title": ep.get("name"),
                    "season": season.get("no"), "episode": ep.get("no"),
                    "poster": _format_media_url((ep.get("thumb") or "").strip()),
                    "description": ep.get("des"),
                })
        episodes.sort(key=lambda e: (e.get("season") or 0, e.get("episode") or 0))

        lang_name = _language_name(show.get("language"))
        img = _format_media_url((show.get("cover") or show.get("thumb") or "").strip())
        return {
            "id": f"show:{show.get('id')}", "type": "series",
            "title": f"{show.get('name')} ({lang_name})", "poster": img, "backdrop": img,
            "description": f"{show.get('des') or ''}\n\nLanguage: {lang_name}".strip(),
            "episodes": episodes,
        }

    if kind == "movie":
        movie = _cache["by_id"]["movies"].get(num_id)
        if not movie:
            return None
        lang_name = _language_name(movie.get("language"))
        img = _format_media_url((movie.get("cover") or movie.get("thumb") or "").strip())
        return {
            "id": f"movie:{movie.get('id')}", "type": "movie",
            "title": f"{movie.get('name')} ({lang_name})", "poster": img, "backdrop": img,
            "description": f"{movie.get('des') or ''}\n\nLanguage: {lang_name}".strip(),
        }

    if kind == "episode":
        ep = _cache["by_id"]["episodes"].get(num_id)
        if not ep:
            return None
        show = _cache["by_id"]["shows"].get(ep.get("show_id"))
        season = _cache["by_id"]["seasons"].get(ep.get("season_id"))
        lang_name = _language_name(ep.get("language"))
        thumb = _format_media_url((ep.get("thumb") or "").strip())
        cover = _format_media_url((ep.get("cover") or ep.get("thumb") or "").strip())
        return {
            "id": f"episode:{ep.get('id')}", "type": "episode",
            "title": f"{(show or {}).get('name', 'Unknown')} - {ep.get('name')} ({lang_name})",
            "poster": thumb, "backdrop": cover,
            "description": (f"{ep.get('des') or ''}\n\nSeason: {(season or {}).get('name', 'Unknown')}"
                             f"\nLanguage: {lang_name}").strip(),
        }

    return None


def _to_playable_proxy_url(source_url: str) -> str:
    u = _format_media_url(source_url.strip())
    looks_like_hls = ".m3u8" in u.lower()
    if looks_like_hls:
        return build_hls_url(u, referer=_main_url, kind="playlist")
    return build_proxy_url(u, referer=_main_url)


async def fetch_stream(content_id: str) -> dict | None:
    await refresh_cache()
    kind, _, raw_id = str(content_id).partition(":")
    try:
        num_id = int(raw_id)
    except ValueError:
        return None

    def _qualities(obj: dict) -> list[dict]:
        qualities = []
        for key, label in (("fhd", "FHD"), ("hd", "HD"), ("sd", "SD"), ("basic", "Basic")):
            if obj.get(key):
                qualities.append({"label": label, "url": _to_playable_proxy_url(obj[key])})
        if not qualities and obj.get("link"):
            qualities.append({"label": "Link", "url": _to_playable_proxy_url(obj["link"])})
        return qualities

    if kind == "episode":
        e = _cache["by_id"]["episodes"].get(num_id)
        if not e:
            return None
        return {"title": e.get("name"), "qualities": _qualities(e)}

    if kind == "movie":
        m = _cache["by_id"]["movies"].get(num_id)
        if not m:
            return None
        return {"title": m.get("name"), "qualities": _qualities(m)}

    # Shows aren't directly streamable — caller should pass an episode id.
    return None
