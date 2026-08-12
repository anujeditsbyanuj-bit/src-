# Akbots - Don't Remove Credit - @AkBots_Official
#
# MeowVerse content provider — ported from the meowtv project's
# src/lib/providers/meowverse.ts. Thin data layer only (fetch_home /
# search / fetch_details / fetch_stream_url); Akbots/meow_commands.py wires
# it up to Telegram commands.
#
# Needs these secrets in config.py/.env (see that project's .env.example —
# they're site-specific keys, not something this port can invent):
#   MEOWVERSE_SECRET_KEY_ENCRYPTED, MEOWVERSE_DES_KEY, MEOWVERSE_DES_IV,
#   MEOWVERSE_AES_KEY, MEOWVERSE_AES_IV, MEOWVERSE_WS_SECRET,
#   MEOWVERSE_P2P_SALT (has a default matching the TS original)

import json
import time

import aiohttp

from Akbots.meow_crypto import des3_decrypt, aes_decrypt_gzip, generate_sign, generate_p2p_token, md5_hex
from Akbots.hls_proxy import build_hls_url, build_proxy_url

try:
    from config import (
        MEOWVERSE_SECRET_KEY_ENCRYPTED, MEOWVERSE_DES_KEY, MEOWVERSE_DES_IV,
        MEOWVERSE_AES_KEY, MEOWVERSE_AES_IV, MEOWVERSE_WS_SECRET, MEOWVERSE_P2P_SALT,
    )
except ImportError:
    MEOWVERSE_SECRET_KEY_ENCRYPTED = ""
    MEOWVERSE_DES_KEY = ""
    MEOWVERSE_DES_IV = ""
    MEOWVERSE_AES_KEY = ""
    MEOWVERSE_AES_IV = ""
    MEOWVERSE_WS_SECRET = ""
    MEOWVERSE_P2P_SALT = "Zox882LYjEn4Rqpa"

MAIN_URL = "https://i6a6.t9z0.com"
DEVICE_ID = "2987149b2e2a63b2"
GAID = ""

_cached_secret: str | None = None
_cached_token: str | None = None
_token_expires_at = 0.0


def is_configured() -> bool:
    return bool(MEOWVERSE_SECRET_KEY_ENCRYPTED and MEOWVERSE_DES_KEY and MEOWVERSE_DES_IV and MEOWVERSE_AES_KEY and MEOWVERSE_AES_IV)


def _headers(cur_time: str, secret: str, token: str) -> dict:
    return {
        "androidid": DEVICE_ID,
        "app_id": "cinetvin",
        "app_language": "en",
        "channel_code": "cinetvin_3001",
        "cur_time": cur_time,
        "device_id": DEVICE_ID,
        "en_al": "0",
        "gaid": GAID,
        "Host": "i6a6.t9z0.com",
        "is_display": "GMT+05:30",
        "is_language": "en",
        "is_vvv": "0",
        "log-header": "I am the log request header.",
        "mob_mfr": "google",
        "mobmodel": "Pixel 5",
        "package_name": "com.cti.cinetvin",
        "sign": generate_sign(secret, cur_time, DEVICE_ID),
        "sys_platform": "2",
        "sysrelease": "13",
        "token": token or "",
        "User-Agent": "okhttp/4.11.0",
        "version": "30000",
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def _ensure_token(session: aiohttp.ClientSession):
    global _cached_secret, _cached_token, _token_expires_at

    if not _cached_secret:
        _cached_secret = des3_decrypt(MEOWVERSE_SECRET_KEY_ENCRYPTED, MEOWVERSE_DES_KEY, MEOWVERSE_DES_IV)

    if _cached_token and time.time() < _token_expires_at:
        return _cached_secret, _cached_token

    cur_time = str(int(time.time() * 1000))
    headers = _headers(cur_time, _cached_secret, "")
    body = {"invited_by": "", "is_install": "1"}

    try:
        async with session.post(f"{MAIN_URL}/api/public/init", headers=headers, data=body) as r:
            text = await r.text()
        json_text = text if text.startswith("{") else aes_decrypt_gzip(text.strip(), MEOWVERSE_AES_KEY, MEOWVERSE_AES_IV)
        data = json.loads(json_text.strip() or "{}")
        _cached_token = (data.get("result") or {}).get("user_info", {}).get("token", "")
        _token_expires_at = time.time() + 3600
    except Exception:
        pass

    return _cached_secret, _cached_token or ""


async def _search_recommend(session, page: int) -> list[dict]:
    secret, token = await _ensure_token(session)
    cur_time = str(int(time.time() * 1000))
    headers = _headers(cur_time, secret, token)
    body = {"pn": str(page)}
    try:
        async with session.post(f"{MAIN_URL}/api/search/recommend", headers=headers, data=body) as r:
            text = await r.text()
        decrypted = aes_decrypt_gzip(text.strip(), MEOWVERSE_AES_KEY, MEOWVERSE_AES_IV)
        data = json.loads(decrypted.strip() or "{}")
        results = data.get("result") or []
        return [
            {"id": str(i.get("id")), "title": i.get("vod_name"), "coverImage": i.get("vod_pic"),
             "type": "series" if i.get("type_pid") == 2 else "movie"}
            for i in results
        ]
    except Exception:
        return []


async def _topic_vod_list(session, topic_id: str, page: int) -> list[dict]:
    secret, token = await _ensure_token(session)
    cur_time = str(int(time.time() * 1000))
    headers = _headers(cur_time, secret, token)
    body = {"topic_id": topic_id, "pn": str(page)}
    try:
        async with session.post(f"{MAIN_URL}/api/topic/vod_list", headers=headers, data=body) as r:
            text = await r.text()
        decrypted = aes_decrypt_gzip(text.strip(), MEOWVERSE_AES_KEY, MEOWVERSE_AES_IV)
        data = json.loads(decrypted.strip() or "{}")
        results = (data.get("result") or {}).get("vod_list") or []
        return [
            {"id": str(i.get("id")), "title": i.get("vod_name"), "coverImage": i.get("vod_pic"),
             "type": "series" if i.get("type_pid") == 2 else "movie"}
            for i in results
        ]
    except Exception:
        return []


_HOME_CATEGORIES = [
    ("1", "Recommended"),
    ("4008", "Trending Now"),
    ("4464", "Most Popular"),
    ("4009", "Hottest International Films"),
    ("4134", "This Month: You Can't Miss"),
    ("4004", "Top Series This Week"),
]


async def fetch_home(page: int = 1) -> list[dict]:
    if page > 1:
        return []
    async with aiohttp.ClientSession() as session:
        rows = []
        for cat_id, name in _HOME_CATEGORIES:
            items = await (_search_recommend(session, page) if cat_id == "1" else _topic_vod_list(session, cat_id, page))
            rows.append({"name": name, "contents": items})
        return rows


async def search(query: str) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        secret, token = await _ensure_token(session)
        cur_time = str(int(time.time() * 1000))
        headers = _headers(cur_time, secret, token)
        body = {"kw": query, "pn": "1"}
        try:
            async with session.post(f"{MAIN_URL}/api/search/result", headers=headers, data=body) as r:
                text = await r.text()
            decrypted = aes_decrypt_gzip(text.strip(), MEOWVERSE_AES_KEY, MEOWVERSE_AES_IV)
            data = json.loads(decrypted.strip() or "{}")
            results = data.get("result") or []
            return [
                {"id": str(i.get("id")), "title": i.get("vod_name"), "coverImage": i.get("vod_pic"), "type": "movie"}
                for i in results
            ]
        except Exception:
            return []


async def fetch_details(vod_id: str) -> dict | None:
    async with aiohttp.ClientSession() as session:
        secret, token = await _ensure_token(session)
        cur_time = str(int(time.time() * 1000))
        p2p_token = generate_p2p_token(vod_id, cur_time, DEVICE_ID, MEOWVERSE_P2P_SALT)
        headers = _headers(cur_time, secret, token)
        body = {"sign": p2p_token, "vod_id": vod_id, "cur_time": cur_time, "audio_type": "0"}

        try:
            async with session.post(f"{MAIN_URL}/api/vod/info_new", headers=headers, data=body) as r:
                text = await r.text()
            decrypted = aes_decrypt_gzip(text.strip(), MEOWVERSE_AES_KEY, MEOWVERSE_AES_IV)
            data = json.loads(decrypted.strip() or "{}")
            info = data.get("result")
            if not info:
                return None

            audio_options = info.get("audio_type_option") or []
            if audio_options:
                audio_tracks = [{"name": o.get("title") or o.get("type_name") or "Language",
                                  "languageId": o.get("type")} for o in audio_options]
            else:
                writers = (info.get("vod_writer") or "").split(",")
                audio_tracks = []
                for idx, w in enumerate(writers):
                    w = w.strip()
                    if not w:
                        continue
                    name = "Hindi" if w.upper() == "HIN" else "English" if w.upper() == "ENG" else w
                    audio_tracks.append({"name": name, "languageId": idx + 1})

            episodes = []
            for col in (info.get("vod_collection") or []):
                episodes.append({
                    "id": str(col.get("id") or f"{vod_id}:{col.get('title') or col.get('episode_no')}"),
                    "title": col.get("title") or f"Episode {col.get('episode_no')}",
                    "season": 1,
                    "number": int(col.get("episode_no") or 1),
                    "sourceMovieId": vod_id,
                    "description": col.get("vod_name"),
                    "tracks": audio_tracks,
                })
            if not episodes:
                episodes.append({
                    "id": vod_id, "title": info.get("vod_name"), "season": 1, "number": 1,
                    "sourceMovieId": vod_id, "tracks": audio_tracks,
                })

            seasons = [
                {"id": str(s.get("vod_id")), "number": int(str(s.get("series", "")).replace("Season ", "") or 1)}
                for s in (info.get("series_info") or [])
            ]

            return {
                "id": vod_id,
                "title": info.get("vod_name"),
                "description": info.get("vod_blurb"),
                "coverImage": info.get("vod_pic"),
                "backgroundImage": info.get("vod_pic_bg"),
                "year": int(info.get("vod_year") or 0) or None,
                "score": float(info.get("vod_score") or 0) or None,
                "episodes": episodes,
                "seasons": seasons or [{"id": vod_id, "number": 1}],
            }
        except Exception:
            return None


async def fetch_stream_url(movie_id: str, episode_id: str, language_id=None) -> dict | None:
    async with aiohttp.ClientSession() as session:
        try:
            secret, token = await _ensure_token(session)
            cur_time = str(int(time.time() * 1000))
            p2p_token = generate_p2p_token(movie_id, cur_time, DEVICE_ID, MEOWVERSE_P2P_SALT)
            headers = _headers(cur_time, secret, token)
            body = {"sign": p2p_token, "vod_id": movie_id, "cur_time": cur_time,
                    "audio_type": str(language_id or "0")}

            async with session.post(f"{MAIN_URL}/api/vod/info_new", headers=headers, data=body) as r:
                text = await r.text()
            decrypted = aes_decrypt_gzip(text.strip(), MEOWVERSE_AES_KEY, MEOWVERSE_AES_IV)
            data = json.loads(decrypted.strip() or "{}")
            info = data.get("result")
            if not info:
                return None

            collections = info.get("vod_collection") or []
            raw_url = info.get("vod_url")
            if episode_id != movie_id:
                ep = next((c for c in collections if str(c.get("id")) == str(episode_id)), None)
                if ep:
                    raw_url = ep.get("vod_url")
            if not raw_url:
                return None

            # wsSecret/wsTime signing
            from urllib.parse import urlparse
            path = urlparse(raw_url).path
            expiry = int(time.time()) + (5 * 60 * 60)
            ws_time = format(expiry, "x")
            ws_secret = md5_hex(MEOWVERSE_WS_SECRET + path + ws_time)
            signed_url = f"{raw_url}?wsSecret={ws_secret}&wsTime={ws_time}"

            is_m3u8 = ".m3u8" in signed_url
            final_url = (build_hls_url(signed_url, ua="okhttp/4.11.0") if is_m3u8
                         else build_proxy_url(signed_url, ua="okhttp/4.11.0"))

            return {
                "videoUrl": final_url,
                "qualities": [{"quality": "Auto", "url": final_url}],
                "subtitles": [],
                "headers": {"User-Agent": "okhttp/4.11.0"},
            }
        except Exception:
            return None
