# Akbots - Don't Remove Credit - @AkBots_Official
#
# MeowTV (Castle API) content provider — ported from the meowtv project's
# src/lib/providers/meowtv.ts. This is a thin data layer only (fetch_home /
# search / fetch_details / fetch_stream_url); Akbots/meow_commands.py wires
# it up to Telegram commands.
#
# Needs CASTLE_SUFFIX in config.py/.env if the upstream API's security key
# requires the extra suffix bytes (may be left empty — see meow_crypto.py).

import json
import logging
import re

import aiohttp

from Akbots.meow_crypto import castle_decrypt

try:
    from config import CASTLE_SUFFIX
except ImportError:
    CASTLE_SUFFIX = ""

logger = logging.getLogger(__name__)

MAIN_URL = "https://api.hlowb.com"
_UA = "okhttp/4.9.0"

_BIG_INT_RE = re.compile(r'(:\s*)(\d{16,})')


def _quote_large_ints(text: str) -> str:
    """Mirrors meowtv.ts's quoteLargeInts(): wrap 16+ digit integers in
    quotes before JSON parsing so precision isn't lost."""
    return _BIG_INT_RE.sub(r'\1"\2"', text)


def _parse_json_preserve_bigint(text: str):
    return json.loads(_quote_large_ints(text))


async def _get_security_key(session: aiohttp.ClientSession, retries: int = 3):
    url = (f"{MAIN_URL}/v0.1/system/getSecurityKey/1"
           f"?channel=IndiaA&clientType=1&lang=en-US")
    last_reason = None
    for attempt in range(retries):
        try:
            async with session.get(url, headers={"User-Agent": _UA}) as r:
                cookie = r.headers.get("Set-Cookie")
                text = await r.text()
                if r.status != 200:
                    last_reason = f"HTTP {r.status}: {text[:200]!r}"
                    continue
                try:
                    data = json.loads(text)
                except Exception as e:
                    last_reason = f"getSecurityKey returned non-JSON ({e}): {text[:200]!r}"
                    continue
                if data and data.get("code") == 200 and data.get("data"):
                    return data["data"], cookie
                last_reason = f"getSecurityKey bad payload: {data!r}"
        except Exception as e:
            last_reason = f"getSecurityKey request failed: {e!r}"
    logger.warning(f"meowtv: could not obtain security key after {retries} attempts — {last_reason}")
    return None, None


async def _fetch_details_with_key(session: aiohttp.ClientSession, movie_id: str, key: str):
    url = (f"{MAIN_URL}/film-api/v1.9.9/movie?channel=IndiaA&clientType=1"
           f"&lang=en-US&movieId={movie_id}&packageName=com.external.castle")
    try:
        async with session.get(url) as r:
            text = await r.text()
        decrypted = castle_decrypt(text, key, CASTLE_SUFFIX)
        if not decrypted:
            logger.warning(
                f"meowtv: castle_decrypt failed for movie {movie_id} "
                f"(CASTLE_SUFFIX {'set' if CASTLE_SUFFIX else 'EMPTY'}); "
                f"raw response started with: {text[:200]!r}")
            return None
        return _parse_json_preserve_bigint(decrypted).get("data")
    except Exception as e:
        logger.warning(f"meowtv: _fetch_details_with_key({movie_id}) raised: {e!r}")
        return None


async def fetch_home(page: int = 1) -> list[dict]:
    """Returns a list of {"name": str, "contents": [content_item]} rows."""
    async with aiohttp.ClientSession() as session:
        key, _ = await _get_security_key(session)
        if not key:
            return []

        url = (f"{MAIN_URL}/film-api/v0.1/category/home?channel=IndiaA"
               f"&clientType=1&lang=en-US&locationId=1001&mode=1"
               f"&packageName=com.external.castle&page={page}&size=17")
        try:
            async with session.get(url) as r:
                text = await r.text()

            encrypted = text
            try:
                j = json.loads(text)
                if j.get("data"):
                    encrypted = j["data"]
            except Exception:
                pass

            decrypted = castle_decrypt(encrypted, key, CASTLE_SUFFIX)
            if not decrypted:
                return []
            data = _parse_json_preserve_bigint(decrypted).get("data") or {}
            rows_raw = data.get("rows") or []

            rows = []
            for row in rows_raw:
                contents = []
                for c in (row.get("contents") or []):
                    contents.append({
                        "title": c.get("title"),
                        "coverImage": c.get("coverImage"),
                        "id": str(c.get("redirectId")) if c.get("redirectId") is not None else None,
                        "type": "series" if c.get("movieType") in (1, 3, 5) else "movie",
                    })
                if contents:
                    rows.append({"name": row.get("name"), "contents": contents})
            return rows
        except Exception:
            return []


async def search(query: str) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        key, _ = await _get_security_key(session)
        if not key:
            return []

        url = (f"{MAIN_URL}/film-api/v1.1.0/movie/searchByKeyword?channel=IndiaA"
               f"&clientType=1&keyword={query}&lang=en-US&mode=1"
               f"&packageName=com.external.castle&page=1&size=30")
        try:
            async with session.get(url) as r:
                payload = await r.text()
            decrypted = castle_decrypt(payload, key, CASTLE_SUFFIX)
            if not decrypted:
                return []
            rows = _parse_json_preserve_bigint(decrypted).get("data", {}).get("rows") or []
            results = []
            for row in rows:
                results.append({
                    "title": row.get("title"),
                    "coverImage": row.get("coverVerticalImage") or row.get("coverHorizontalImage"),
                    "id": str(row.get("id")) if row.get("id") is not None else None,
                    "type": "series" if row.get("movieType") in (1, 3, 5) else "movie",
                })
            return results
        except Exception:
            return []


async def fetch_details(movie_id: str) -> dict | None:
    async with aiohttp.ClientSession() as session:
        key, _ = await _get_security_key(session)
        if not key:
            return None

        url = (f"{MAIN_URL}/film-api/v1.9.9/movie?channel=IndiaA&clientType=1"
               f"&lang=en-US&movieId={movie_id}&packageName=com.external.castle")
        try:
            async with session.get(url) as r:
                text = await r.text()
            decrypted = castle_decrypt(text, key, CASTLE_SUFFIX)
            if not decrypted:
                return None
            d = _parse_json_preserve_bigint(decrypted).get("data") or {}

            episodes = []
            seasons_raw = d.get("seasons") or []
            if len(seasons_raw) > 1:
                for season in seasons_raw:
                    season_movie_id = season.get("movieId")
                    if not season_movie_id:
                        continue
                    s_details = await _fetch_details_with_key(session, str(season_movie_id), key)
                    if not s_details:
                        continue
                    for ep in (s_details.get("episodes") or []):
                        episodes.append({
                            "id": str(ep.get("id")),
                            "title": ep.get("title"),
                            "number": ep.get("number"),
                            "season": season.get("number"),
                            "coverImage": ep.get("coverImage"),
                            "sourceMovieId": str(season_movie_id),
                            "tracks": ep.get("tracks") or [],
                        })
            elif d.get("episodes"):
                for ep in d["episodes"]:
                    episodes.append({
                        "id": str(ep.get("id")),
                        "title": ep.get("title"),
                        "number": ep.get("number"),
                        "season": d.get("seasonNumber", 1),
                        "coverImage": ep.get("coverImage"),
                        "sourceMovieId": str(d.get("id")),
                        "tracks": ep.get("tracks") or [],
                    })

            episodes.sort(key=lambda e: (e.get("season") or 0, e.get("number") or 0))

            return {
                "id": str(d.get("id")),
                "title": d.get("title"),
                "description": d.get("briefIntroduction"),
                "coverImage": d.get("coverVerticalImage") or d.get("coverHorizontalImage"),
                "backgroundImage": d.get("coverHorizontalImage"),
                "score": d.get("score"),
                "episodes": episodes,
                "seasons": [{"id": str(s.get("movieId")), "number": s.get("number")} for s in seasons_raw],
                "tags": d.get("tags"),
            }
        except Exception:
            return None


async def fetch_stream_url(movie_id: str, episode_id: str, language_id=None) -> dict | None:
    async with aiohttp.ClientSession() as session:
        key, cookie = await _get_security_key(session)
        if not key:
            logger.warning(f"meowtv: fetch_stream_url({movie_id}, {episode_id}) aborted — no security key")
            return None

        details = await _fetch_details_with_key(session, movie_id, key)
        if not details:
            logger.warning(f"meowtv: fetch_stream_url({movie_id}, {episode_id}) — could not fetch movie details")
        episodes = (details or {}).get("episodes") or []

        target_episode = next((ep for ep in episodes if str(ep.get("id")) == str(episode_id)), None)
        if not target_episode and episodes:
            target_episode = episodes[0]
            episode_id = str(target_episode.get("id"))

        tracks = (target_episode or {}).get("tracks") or []
        has_individual = any(t.get("existIndividualVideo") for t in tracks)

        track_plan = []
        if language_id:
            track_plan.append({"languageId": language_id})
        elif not has_individual and tracks:
            track_plan.append({"languageId": tracks[0].get("languageId")})
        elif tracks:
            for t in tracks:
                track_plan.append({"languageId": t.get("languageId")})
        else:
            track_plan.append({"languageId": None})

        resolutions = [3, 2, 1]
        collected_qualities = []
        best_video_url = None
        best_subtitles = []
        cookie_header = cookie or "hd=on"

        for track in track_plan:
            for resolution in resolutions:
                url = (f"{MAIN_URL}/film-api/v2.0.1/movie/getVideo2?clientType=1"
                       f"&packageName=com.external.castle&channel=IndiaA&lang=en-US")
                body = {
                    "mode": "1", "appMarket": "GuanWang", "clientType": "1", "woolUser": "false",
                    "apkSignKey": "ED0955EB04E67A1D9F3305B95454FED485261475", "androidVersion": "13",
                    "movieId": movie_id, "episodeId": episode_id, "isNewUser": "true",
                    "resolution": str(resolution), "packageName": "com.external.castle",
                }
                if track.get("languageId"):
                    body["languageId"] = str(track["languageId"])

                try:
                    async with session.post(
                        url,
                        json=body,
                        headers={"User-Agent": _UA, "Cookie": cookie_header,
                                 "Content-Type": "application/json; charset=utf-8"},
                    ) as r:
                        payload = await r.text()
                    decrypted = castle_decrypt(payload, key, CASTLE_SUFFIX)
                    if not decrypted:
                        logger.warning(
                            f"meowtv: getVideo2 decrypt failed for {movie_id}/{episode_id} "
                            f"res={resolution} lang={track.get('languageId')} "
                            f"raw response started with: {payload[:200]!r}")
                        continue
                    data = _parse_json_preserve_bigint(decrypted).get("data")
                    if data and data.get("videoUrl"):
                        quality_label = {3: "1080p", 2: "720p", 1: "480p"}.get(resolution, f"{resolution}p")
                        collected_qualities.append({"quality": quality_label, "url": data["videoUrl"]})
                        if not best_video_url:
                            best_video_url = data["videoUrl"]
                            best_subtitles = [
                                {
                                    "language": s.get("abbreviate") or s.get("title") or "Unknown",
                                    "label": s.get("title") or "Subtitles",
                                    "url": s.get("url"),
                                }
                                for s in (data.get("subtitles") or []) if s.get("url")
                            ]
                    else:
                        logger.warning(
                            f"meowtv: getVideo2 decrypted OK but no videoUrl for {movie_id}/{episode_id} "
                            f"res={resolution} lang={track.get('languageId')} — decrypted data: "
                            f"{str(data)[:200]!r}")
                except Exception as e:
                    logger.warning(
                        f"meowtv: getVideo2 request raised for {movie_id}/{episode_id} "
                        f"res={resolution} lang={track.get('languageId')}: {e!r}")
                    continue

        if not best_video_url:
            logger.warning(
                f"meowtv: fetch_stream_url({movie_id}, {episode_id}) — exhausted all "
                f"tracks/resolutions with no playable videoUrl")
            return None

        return {
            "videoUrl": best_video_url,
            "subtitles": best_subtitles,
            "qualities": collected_qualities,
            "headers": {"Referer": MAIN_URL},
        }
