# Akbots - Don't Remove Credit - @AkBots_Official
#
# MeowToon (Kartoons + Xon) content provider — ported from the meowtv
# project's src/lib/providers/meowtoon.ts. Merges two sources exactly like
# the TS original: Kartoons (api.kartoons.me, handled directly below) and
# Xon (Akbots/xon_provider.py, ported from src/lib/xon.ts) — Xon results
# carry an "xon:" id prefix throughout, same convention as the TS code.
#
# MEOWTOON_KARTOON_TOKEN in config.py/.env is optional — Kartoons' API
# works without a bearer token for browsing/search; set it if you have one
# and hit rate limits.

import logging

import aiohttp

from Akbots import xon_provider

try:
    from config import MEOWTOON_KARTOON_TOKEN
except ImportError:
    MEOWTOON_KARTOON_TOKEN = ""

logger = logging.getLogger(__name__)

MAIN_URL = "https://api.kartoons.me"
DECRYPT_BASE = "https://kartoondecrypt.vercel.app"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Referer": "https://kartoons.me/",
    "Origin": "https://kartoons.me",
}


def _headers() -> dict:
    h = dict(_HEADERS)
    if MEOWTOON_KARTOON_TOKEN:
        h["Authorization"] = f"Bearer {MEOWTOON_KARTOON_TOKEN}"
    return h


async def _get_json(session: aiohttp.ClientSession, url: str, timeout: float = 8.0):
    async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


def _to_stream_url(encoded_link: str) -> str:
    from urllib.parse import quote
    clean = "".join(str(encoded_link or "").split())
    return f"{DECRYPT_BASE}/kartoons?data={quote(clean)}"


def _parse_content_id(raw: str):
    if not raw or "-" not in raw:
        return None
    prefix, _, identifier = raw.partition("-")
    if not identifier:
        return None
    if prefix in ("movie", "series"):
        return prefix, identifier
    return None


def _derive_season_number(raw: dict, index: int) -> int:
    for key in ("seasonNumber", "season_no", "seasonNo", "number", "season", "season_id"):
        val = raw.get(key)
        try:
            n = int(val)
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return index + 1


async def _fetch_home_kartoons(page: int) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        try:
            shows_data, movies_data, pop_shows, pop_movies = [
                await _get_json(session, url) for url in (
                    f"{MAIN_URL}/api/shows?page=1&limit=20",
                    f"{MAIN_URL}/api/movies?page=1&limit=20",
                    f"{MAIN_URL}/api/popularity/shows?limit=15&period=day",
                    f"{MAIN_URL}/api/popularity/movies?limit=15&period=day",
                )
            ]

            def to_item(item: dict, kind: str) -> dict:
                return {
                    "id": f"{kind}-{item.get('slug') or item.get('id')}",
                    "title": item.get("title") or "",
                    "coverImage": item.get("image") or "",
                    "type": kind,
                }

            rows = [
                {"name": "Popular Shows", "contents": [to_item(i, "series") for i in (pop_shows.get("data") or [])]},
                {"name": "Popular Movies", "contents": [to_item(i, "movie") for i in (pop_movies.get("data") or [])]},
                {"name": "Shows", "contents": [to_item(i, "series") for i in (shows_data.get("data") or [])]},
                {"name": "Movies", "contents": [to_item(i, "movie") for i in (movies_data.get("data") or [])]},
            ]
            return [r for r in rows if r["contents"]]
        except Exception as e:
            logger.debug(f"meowtoon: kartoons home failed: {e}")
            return []


async def fetch_home(page: int = 1) -> list[dict]:
    if page > 1:
        return []

    kartoons_rows = await _fetch_home_kartoons(page)

    try:
        xon_rows = await xon_provider.fetch_home()
        xon_rows = [
            {
                "name": f"Xon • {row['name']}",
                "contents": [
                    {
                        "id": f"xon:{i['id']}",
                        "title": i.get("title") or "",
                        "coverImage": i.get("poster") or i.get("backdrop") or "",
                        "type": "movie" if i.get("type") == "movie" else "series",
                    }
                    for i in row["items"]
                ],
            }
            for row in xon_rows
        ]
    except Exception as e:
        logger.debug(f"meowtoon: xon home failed: {e}")
        xon_rows = []

    return kartoons_rows + xon_rows


async def search(query: str) -> list[dict]:
    results = []
    from urllib.parse import quote
    q_encoded = quote(query)

    async with aiohttp.ClientSession() as session:
        try:
            res = await _get_json(session, f"{MAIN_URL}/api/search/suggestions?q={q_encoded}&limit=20")
            for item in (res.get("data") or []):
                t = str(item.get("type") or "").lower()
                kind = "movie" if t == "movie" else "series"
                identifier = item.get("id") or item.get("slug")
                results.append({
                    "id": f"{kind}-{identifier}",
                    "title": item.get("title") or "",
                    "coverImage": item.get("image") or "",
                    "type": kind,
                })
        except Exception as e:
            logger.warning(f"meowtoon: kartoons search failed for {query!r}: {e!r}")

    try:
        xon_results = await xon_provider.search(query)
        for i in xon_results:
            results.append({
                "id": f"xon:{i['id']}",
                "title": i.get("title") or "",
                "coverImage": i.get("poster") or i.get("backdrop") or "",
                "type": "movie" if i.get("type") == "movie" else "series",
            })
    except Exception as e:
        logger.warning(f"meowtoon: xon search failed for {query!r}: {e!r}")

    # de-dupe by id, same as meowtoon.ts
    seen = set()
    deduped = []
    for r in results:
        if not r.get("id") or r["id"] in seen:
            continue
        seen.add(r["id"])
        deduped.append(r)
    return deduped


async def fetch_details(content_id: str) -> dict | None:
    if content_id.startswith("xon:"):
        try:
            details = await xon_provider.fetch_details(content_id[len("xon:"):])
            if not details:
                return None
            episodes = [
                {
                    "id": f"xon:{ep['id']}",
                    "title": ep.get("title"),
                    "number": ep.get("episode") or ep.get("number") or 0,
                    "season": ep.get("season") or 1,
                    "coverImage": ep.get("poster"),
                    "description": ep.get("description"),
                    "sourceMovieId": content_id,
                }
                for ep in (details.get("episodes") or [])
            ]
            return {
                "id": content_id,
                "title": details.get("title"),
                "description": details.get("description"),
                "coverImage": details.get("poster") or "",
                "backgroundImage": details.get("backdrop"),
                "episodes": episodes,
            }
        except Exception as e:
            logger.debug(f"meowtoon: xon details failed: {e}")
            return None

    parsed = _parse_content_id(content_id)
    if not parsed:
        return None
    kind, identifier = parsed

    async with aiohttp.ClientSession() as session:
        try:
            url = (f"{MAIN_URL}/api/shows/{identifier}" if kind == "series"
                   else f"{MAIN_URL}/api/movies/{identifier}")
            json_resp = await _get_json(session, url)
            data = json_resp.get("data")
            if not data:
                return None

            title = data.get("title") or ""
            cover_image = data.get("image") or ""
            background_image = data.get("coverImage") or data.get("hoverImage")

            if kind == "series":
                show_slug = data.get("slug")
                seasons_raw = data.get("seasons") or []

                seasons = []
                for idx, s in enumerate(seasons_raw):
                    season_number = _derive_season_number(s, idx)
                    season_slug = s.get("slug") or s.get("_id") or s.get("id")
                    if season_slug:
                        seasons.append({"id": str(season_slug), "number": season_number,
                                         "name": f"Season {season_number}"})

                episodes = []
                for idx, season in enumerate(seasons_raw):
                    season_slug = season.get("slug") or season.get("_id") or season.get("id")
                    season_number = _derive_season_number(season, idx)
                    if not show_slug or not season_slug:
                        continue
                    try:
                        s_json = await _get_json(
                            session, f"{MAIN_URL}/api/shows/{show_slug}/season/{season_slug}/all-episodes")
                        for ep in (s_json.get("data") or []):
                            ep_id = ep.get("id") or ep.get("_id")
                            if not ep_id:
                                continue
                            try:
                                ep_number = int(ep.get("episodeNumber") or 0)
                            except (TypeError, ValueError):
                                ep_number = 0
                            episodes.append({
                                "id": f"ep-{ep_id}",
                                "title": ep.get("title") or f"Episode {ep_number}",
                                "number": ep_number,
                                "season": season_number,
                                "coverImage": ep.get("image"),
                                "description": ep.get("description"),
                                "sourceMovieId": content_id,
                            })
                    except Exception:
                        continue

                episodes.sort(key=lambda e: (e["season"], e["number"]))

                return {
                    "id": content_id, "title": title, "description": data.get("description"),
                    "coverImage": cover_image, "backgroundImage": background_image,
                    "year": data.get("startYear"), "score": data.get("rating"),
                    "episodes": episodes, "seasons": seasons,
                    "tags": data.get("tags"),
                }

            # Movie
            movie_api_id = data.get("id") or data.get("_id")
            if not movie_api_id:
                return None
            episodes = [{
                "id": f"mov-{movie_api_id}", "title": title, "number": 1, "season": 1,
                "coverImage": cover_image, "sourceMovieId": content_id,
            }]
            return {
                "id": content_id, "title": title, "description": data.get("description"),
                "coverImage": cover_image, "backgroundImage": background_image,
                "year": data.get("startYear"), "score": data.get("rating"),
                "episodes": episodes, "tags": data.get("tags"),
            }
        except Exception:
            return None


async def fetch_stream_url(_movie_id: str, episode_id: str, _language_id=None) -> dict | None:
    if episode_id.startswith("xon:"):
        try:
            stream = await xon_provider.fetch_stream(episode_id[len("xon:"):])
            if not stream or not stream.get("qualities"):
                return None
            qualities = [{"quality": q["label"], "url": q["url"]} for q in stream["qualities"]]
            return {"videoUrl": qualities[0]["url"], "qualities": qualities, "headers": {}}
        except Exception as e:
            logger.debug(f"meowtoon: xon stream failed: {e}")
            return None

    async with aiohttp.ClientSession() as session:
        try:
            if episode_id.startswith("ep-"):
                url = f"{MAIN_URL}/api/shows/episode/{episode_id[len('ep-'):]}/links"
            elif episode_id.startswith("mov-"):
                url = f"{MAIN_URL}/api/movies/{episode_id[len('mov-'):]}/links"
            else:
                return None

            json_resp = await _get_json(session, url, timeout=8.0)
            links = (json_resp.get("data") or {}).get("links")
            if not links:
                return None

            for link in links:
                encoded = link.get("url")
                if not encoded:
                    continue
                # The decrypt service (DECRYPT_BASE) returns a real HLS
                # master playlist over plain HTTPS when you GET this URL —
                # it's a fully fetchable link, not a browser-only blob.
                #
                # The original web frontend prefixed this with "blob:" as a
                # signal to itself (fetch the playlist, wrap it in a
                # same-origin Blob, hand that to the <video> player) — a
                # trick that only makes sense inside a browser. Nothing in
                # the bot (Telegram's URL buttons, which reject non-http(s)
                # schemes, or yt-dlp/meow_downloader.py, which has no
                # "blob:" handler) understands that prefix, so both the
                # ▶️ Play button and the ⬇️ Download button silently failed
                # for any non-Xon MeowToon title. Returning the plain
                # https:// decrypt URL fixes both.
                decrypt_url = _to_stream_url(str(encoded))
                return {"videoUrl": decrypt_url, "headers": {}, "qualities": []}
            return None
        except Exception:
            return None
