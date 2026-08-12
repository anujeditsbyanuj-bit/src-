# Akbots - Don't Remove Credit - @AkBots_Official
#
# Real-download stream resolvers for Akbots/meowly_provider.py.
#
# meowly_provider.embed_links() only builds *iframe* URLs for 12 public
# player sites (ported from meowly's VideoPlayer.tsx) — fine for "tap to
# watch in browser", useless for /meow-style download-to-Telegram, because
# an iframe URL isn't a fetchable video file. This module ports the actual
# scrape/decrypt logic those players use client-side (Move-main's
# peachify.js, vidrock.js, vidsrc.js, videasy.js) so meowly can resolve a
# real mp4/m3u8 the same way MeowTV/MeowVerse/MeowToon already do.
#
# Ported 1:1 from Move-main (Node/fetch) to Python (aiohttp/pycryptodome).
# Covered (self-contained, no browser/WASM needed):
#   - vidsrc   (vsembed.ru)      — plain HTML scrape, no crypto
#   - vidrock  (vidrock.ru)      — AES-256-CBC id encryption
#   - peachify (peachify.top)    — AES-256-GCM response decryption
#   - videasy  (player.videasy.net) — blob decrypted via the public
#                                      enc-dec.app helper API (same one
#                                      the videasy player itself calls)
#
# NOT ported: vidlink.pro. Move-main's vidlink.js resolves its token via a
# WASM module (extensions/fu.wasm) invoked through libsodium — that's a
# compiled browser blob with no equivalent pycryptodome/aiohttp port; doing
# it properly needs a JS/WASM runtime (e.g. shelling out to Node) which
# this project doesn't otherwise depend on. Left out rather than faked.
# meowly_provider.embed_links() still lists Vidlink as a watch-in-browser
# option — it's just not part of fetch_stream_url()'s resolver chain below.
#
# All four resolvers return the same shape (or None on failure):
#   {"videoUrl": str, "qualities": [{"quality": str, "url": str}, ...],
#    "headers": {...}, "subtitles": [{"url": str, "label": str}, ...]}

import base64
import logging
import re

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from Akbots import meowly_extra_resolvers

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=12)


def _b64url_decode(value: str) -> bytes:
    padded = value.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    return base64.b64decode(padded)


def _b64url_encode(data: bytes) -> str:
    return base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")


async def _get_text(session: aiohttp.ClientSession, url: str, headers: dict = None):
    try:
        async with session.get(url, headers=headers, timeout=_TIMEOUT) as r:
            if r.status != 200:
                return None
            return await r.text()
    except Exception:
        return None


async def _get_json(session: aiohttp.ClientSession, url: str, headers: dict = None):
    try:
        async with session.get(url, headers=headers, timeout=_TIMEOUT) as r:
            if r.status != 200:
                return None
            return await r.json(content_type=None)
    except Exception:
        return None


# ── vidsrc (vsembed.ru) — ported from Move-main/vidsrc.js ──────────────
# Plain HTML scrape chain: embed page -> rcp iframe -> prorcp player ->
# m3u8 `file:` field (with placeholder CDN domains substituted in), with a
# one-level nested-iframe fallback. No crypto involved.

_VS_BASE = "https://vsembed.ru"
_VS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/150 Safari/537.36"),
    "Referer": _VS_BASE + "/",
}
_VS_PROXY_HEADERS = {
    "User-Agent": _VS_HEADERS["User-Agent"],
    "Referer": "https://cloudnestra.com/",
    "Origin": "https://cloudnestra.com",
    "Accept": "*/*",
}
_VS_PLAYER_DOMAINS = {
    "{v1}": "neonhorizonworkshops.com",
    "{v2}": "wanderlynest.com",
    "{v3}": "orchidpixelgardens.com",
    "{v4}": "cloudnestra.com",
}


def _vs_extract_iframe_src(html: str) -> str | None:
    m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
    return m.group(1) if m else None


def _vs_extract_prorcp(html: str) -> str | None:
    m = re.search(r'''src:\s*['"]([^'"]*/prorcp/[^'"]+)['"]''', html, re.I)
    return m.group(1) if m else None


def _vs_extract_m3u8_urls(html: str) -> list[str] | None:
    m = re.search(r'''file\s*:\s*["']([^"']+)["']''', html, re.I)
    if not m:
        return None
    urls = []
    for template in re.split(r"\s+or\s+", m.group(1), flags=re.I):
        url = template
        for placeholder, domain in _VS_PLAYER_DOMAINS.items():
            url = url.replace(placeholder, domain)
        if "{" not in url and "}" not in url:
            urls.append(url)
    return urls or None


def _vs_extract_api_url(html: str, base_url: str) -> str | None:
    m = (re.search(r'''src=["\']([^"\']*/e/[^"\']+)["\']''', html, re.I)
         or re.search(r'''src=["\']([^"\']*/embed[^"\']+)["\']''', html, re.I)
         or re.search(r'''<iframe[^>]+src=["\']([^"\']+)["\']''', html, re.I))
    if not m:
        return None
    try:
        from urllib.parse import urljoin
        return urljoin(base_url, m.group(1))
    except Exception:
        return None


async def resolve_vidsrc(tmdb_id: str, season: int = None, episode: int = None) -> dict | None:
    async with aiohttp.ClientSession() as session:
        page_url = (f"{_VS_BASE}/embed/tv?tmdb={tmdb_id}&season={season}&episode={episode}"
                    if season else f"{_VS_BASE}/embed/movie?tmdb={tmdb_id}")

        html1 = await _get_text(session, page_url, _VS_HEADERS)
        if not html1:
            return None
        rcp_url = _vs_extract_iframe_src(html1)
        if not rcp_url:
            return None
        if rcp_url.startswith("//"):
            rcp_url = "https:" + rcp_url

        html2 = await _get_text(session, rcp_url, {**_VS_HEADERS, "Referer": _VS_BASE + "/"})
        if not html2:
            return None

        prorcp = _vs_extract_prorcp(html2)
        if prorcp:
            base = rcp_url[:rcp_url.index("/", rcp_url.index("//") + 2)]
            player_url = prorcp if prorcp.startswith("http") else base + prorcp
        else:
            player_url = rcp_url.replace("/rcp/", "/prorcp/")

        html3 = await _get_text(session, player_url, {**_VS_HEADERS, "Referer": rcp_url})
        if not html3:
            return None

        urls = _vs_extract_m3u8_urls(html3)
        if not urls:
            step4_url = _vs_extract_api_url(html3, player_url)
            if not step4_url:
                return None
            html4 = await _get_text(session, step4_url, {**_VS_HEADERS, "Referer": player_url})
            if not html4:
                return None
            urls = _vs_extract_m3u8_urls(html4)
            if not urls:
                return None

        return {
            "videoUrl": urls[0],
            "qualities": [{"quality": "HLS", "url": u} for u in urls],
            "headers": _VS_PROXY_HEADERS,
            "subtitles": [],
        }


# ── vidrock (vidrock.ru) — ported from Move-main/vidrock.js ────────────
# AES-256-CBC-encrypted item id in the API path; per-track URLs may
# themselves be JSON (array of {url,resolution}), an m3u8 playlist, or a
# direct mp4.

_VR_PASSPHRASE = "x7k9mPqT2rWvY8zA5bC3nF6hJ2lK4mN9"  # 32 chars -> AES-256 key
_VR_BASE = "https://vidrock.ru/"
_VR_PROXY_PREFIX = "https://proxy.vidrock.store/"
_VR_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/150 Safari/537.36"),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _VR_BASE,
    "Origin": _VR_BASE,
}
_VR_CDN_HEADERS_DEFAULT = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/134.0.6884.98 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _VR_BASE,
    "Origin": _VR_BASE.rstrip("/"),
}


def _vr_encrypt_item_id(item_id: str) -> str:
    key = _VR_PASSPHRASE.encode("utf-8")
    iv = _VR_PASSPHRASE[:16].encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(item_id.encode("utf-8"), AES.block_size))
    return _b64url_encode(encrypted)


def _vr_stream_headers(stream_url: str) -> dict:
    try:
        from urllib.parse import urlparse
        host = urlparse(stream_url).hostname or ""
        if host == "play.xpass.top" or host.endswith(".xpass.top"):
            return {"Referer": "https://play.xpass.top/", "Origin": "https://play.xpass.top"}
    except Exception:
        pass
    return dict(_VR_CDN_HEADERS_DEFAULT)


def _vr_unwrap_proxy(url: str) -> str:
    if url.startswith(_VR_PROXY_PREFIX):
        from urllib.parse import unquote
        return unquote(url[len(_VR_PROXY_PREFIX):].lstrip("/"))
    return url


async def resolve_vidrock(tmdb_id: str, season: int = None, episode: int = None) -> dict | None:
    media_type = "tv" if season else "movie"
    item_id = f"{tmdb_id}_{season}_{episode or 1}" if season else str(tmdb_id)
    encrypted = _vr_encrypt_item_id(item_id)
    page_url = f"{_VR_BASE}api/{media_type}/{encrypted}"

    async with aiohttp.ClientSession() as session:
        data = await _get_json(session, page_url, _VR_HEADERS)
        if not isinstance(data, dict):
            return None

        hls_urls, mp4_urls = [], []
        for stream in data.values():
            if not isinstance(stream, dict) or not stream.get("url"):
                continue
            stream_url = _vr_unwrap_proxy(stream["url"])

            fetched_json = await _get_json(session, stream_url)
            if isinstance(fetched_json, list):
                for obj in fetched_json:
                    if not isinstance(obj, dict) or not obj.get("url"):
                        continue
                    final_url = _vr_unwrap_proxy(obj["url"])
                    mp4_urls.append({"url": final_url, "resolution": obj.get("resolution")})
                continue

            fetched_text = None if fetched_json is not None else await _get_text(session, stream_url)
            if stream.get("type") == "hls" or (fetched_text and "#EXTM3U" in fetched_text):
                hls_urls.append({"url": stream_url})
            else:
                mp4_urls.append({"url": stream_url, "resolution": None})

        all_urls = hls_urls + mp4_urls
        if not all_urls:
            return None

        qualities = [{"quality": str(u.get("resolution") or ("HLS" if u in hls_urls else "SD")),
                      "url": u["url"]} for u in all_urls]
        primary = all_urls[0]["url"]
        return {
            "videoUrl": primary,
            "qualities": qualities,
            "headers": _vr_stream_headers(primary),
            "subtitles": [],
        }


# ── peachify (peachify.top) — ported from Move-main/peachify.js ────────
# AES-256-GCM-encrypted JSON responses from 6 mirrored backend servers,
# queried in parallel; first server(s) with usable sources win.

_PF_BASE = "https://peachify.top"
_PF_MOVIEBOX_URL = "https://uwu.eat-peach.sbs"
_PF_API_URL = "https://usa.eat-peach.sbs"
_PF_SERVERS = [
    f"{_PF_MOVIEBOX_URL}/moviebox", f"{_PF_API_URL}/holly", f"{_PF_API_URL}/air",
    f"{_PF_API_URL}/multi", f"{_PF_MOVIEBOX_URL}/net", f"{_PF_MOVIEBOX_URL}/bmb",
]
_PF_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{_PF_BASE}/",
    "Origin": _PF_BASE,
}
_PF_STREAM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Referer": f"{_PF_BASE}/",
    "Origin": _PF_BASE,
}
_PF_KEY_HEX_B64 = "YThmMmExYjVlOWM0NzA4MTRmNmIyYzNhNWQ4ZTdmOWMxYTJiM2M0ZDVlM2Y3YThiOGNhZDFlMmQwYTRkNWM1Yg=="


def _pf_decrypt(payload: str) -> dict | None:
    try:
        parts = payload.split(".")
        if len(parts) != 3:
            return None
        iv = _b64url_decode(parts[0])
        ciphertext = _b64url_decode(parts[1])
        auth_tag = _b64url_decode(parts[2])
        key_hex = base64.b64decode(_PF_KEY_HEX_B64).decode()
        key = bytes.fromhex(key_hex)
        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
        decrypted = cipher.decrypt_and_verify(ciphertext, auth_tag)
        import json
        return json.loads(decrypted.decode("utf-8"))
    except Exception:
        return None


def _pf_pick_str(obj: dict, keys: list[str]) -> str:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


async def _pf_fetch_server(session: aiohttp.ClientSession, server_base: str, tmdb_id: str,
                            season, episode) -> tuple[list, list] | None:
    url = (f"{server_base}/tv/{tmdb_id}/{season}/{episode or 1}" if season
           else f"{server_base}/movie/{tmdb_id}")
    body = await _get_json(session, url, _PF_HEADERS)
    if not isinstance(body, dict):
        return None
    if body.get("isEncrypted") and body.get("data"):
        body = _pf_decrypt(body["data"])
        if not body:
            return None
    raw_sources = body.get("sources") or []
    if not raw_sources:
        return None
    raw_subtitles = body.get("subtitles") or []
    return raw_sources, raw_subtitles


async def resolve_peachify(tmdb_id: str, season: int = None, episode: int = None) -> dict | None:
    import asyncio
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[_pf_fetch_server(session, srv, tmdb_id, season, episode) for srv in _PF_SERVERS],
            return_exceptions=True,
        )

        all_sources, all_subs = [], []
        for res in results:
            if isinstance(res, Exception) or not res:
                continue
            raw_sources, raw_subtitles = res
            for raw in raw_sources:
                url = _pf_pick_str(raw, ["url", "src", "file", "stream", "streamUrl", "playbackUrl"])
                if not url:
                    continue
                raw_type = _pf_pick_str(raw, ["type", "format", "container"]).lower()
                is_hls = "hls" in raw_type or "m3u8" in raw_type or ".m3u8" in url.lower()
                quality = raw.get("quality") or raw.get("resolution") or raw.get("height") or raw.get("res")
                all_sources.append({"url": url, "type": "hls" if is_hls else "mp4", "quality": quality})
            for raw in raw_subtitles:
                sub_url = raw.get("url") or raw.get("file") or raw.get("src")
                if sub_url:
                    all_subs.append({"url": sub_url, "label": raw.get("label") or raw.get("name") or "Auto"})

        if not all_sources:
            return None

        all_sources.sort(key=lambda s: 0 if s["type"] == "hls" else 1)
        return {
            "videoUrl": all_sources[0]["url"],
            "qualities": [{"quality": str(s["quality"]) if s["quality"] else s["type"].upper(),
                           "url": s["url"]} for s in all_sources],
            "headers": _PF_STREAM_HEADERS,
            "subtitles": all_subs,
        }


# ── videasy (player.videasy.net) — ported from Move-main/videasy.js ────
# Each backend returns an opaque blob that's decrypted via the public
# enc-dec.app helper endpoint (same one the videasy web player calls
# client-side) rather than any crypto we do locally.

_VY_DEC_API = "https://enc-dec.app/api/dec-videasy"
_VY_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, */*; q=0.01",
    "Referer": "https://player.videasy.net/",
    "Origin": "https://player.videasy.net",
}
_VY_SERVERS = [
    "https://api2.videasy.net/cuevana/sources-with-title",
    "https://api.videasy.net/mb-flix/sources-with-title",
    "https://api.videasy.net/1movies/sources-with-title",
    "https://api.videasy.net/cdn/sources-with-title",
    "https://api.videasy.net/superflix/sources-with-title",
    "https://api.videasy.net/lamovie/sources-with-title",
]
_VY_BLOCKED_DOMAINS = ["easy.speedsterwave.app"]


def _vy_is_blocked(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return any(d in host for d in _VY_BLOCKED_DOMAINS)
    except Exception:
        return False


async def _vy_decrypt(session: aiohttp.ClientSession, blob: str, tmdb_id: str) -> dict | None:
    if not blob or len(blob) < 10:
        return None
    try:
        async with session.post(_VY_DEC_API, json={"text": blob, "id": tmdb_id},
                                 headers={"Content-Type": "application/json"},
                                 timeout=_TIMEOUT) as r:
            if r.status != 200:
                return None
            payload = await r.json(content_type=None)
    except Exception:
        return None
    if payload.get("status") != 200 or not (payload.get("result") or {}).get("sources"):
        return None
    return payload["result"]


async def _vy_fetch_server(session: aiohttp.ClientSession, server_url: str, tmdb_id: str,
                            season, episode, title: str) -> dict | None:
    params = {
        "title": title or "", "mediaType": "tv" if season else "movie",
        "tmdbId": str(tmdb_id), "imdbId": "",
        "episodeId": str(episode or 1), "seasonId": str(season or 1),
    }
    blob = await _get_text(session, server_url + "?" + "&".join(f"{k}={v}" for k, v in params.items()),
                            _VY_HEADERS)
    if not blob or len(blob) < 10:
        return None
    return await _vy_decrypt(session, blob, str(tmdb_id))


async def resolve_videasy(tmdb_id: str, season: int = None, episode: int = None,
                           title: str = "") -> dict | None:
    import asyncio
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[_vy_fetch_server(session, srv, tmdb_id, season, episode, title) for srv in _VY_SERVERS],
            return_exceptions=True,
        )
        for res in results:
            if isinstance(res, Exception) or not res:
                continue
            sources = [s for s in (res.get("sources") or []) if s.get("url") and not _vy_is_blocked(s["url"])]
            if not sources:
                continue
            subs = [{"url": s.get("url") or s.get("file"), "label": s.get("label") or "Auto"}
                    for s in (res.get("subtitles") or []) if s.get("url") or s.get("file")]
            return {
                "videoUrl": sources[0]["url"],
                "qualities": [{"quality": s.get("quality") or "Auto", "url": s["url"]} for s in sources],
                "headers": _VY_HEADERS,
                "subtitles": subs,
            }
    return None


# ── Dispatcher ───────────────────────────────────────────────────────────
# Tried in this order: vidsrc first (no crypto, most reliable historically),
# then vidrock, peachify, videasy. First one that returns sources wins.

RESOLVERS = (
    ("VidSrc", resolve_vidsrc),
    ("VidRock", resolve_vidrock),
    ("Peachify", resolve_peachify),
    ("Videasy", resolve_videasy),
)


async def resolve(tmdb_id: str, season: int = None, episode: int = None, title: str = "",
                   audio: str = "sub") -> dict | None:
    """Try each resolver in RESOLVERS order, return the first success.

    Falls through to meowly_extra_resolvers.EXTRA_RESOLVERS (the other 22
    Move-main sources — vidify, vixsrc, nhdapi, vidzee, vidnest, cinezo,
    meowtv-gate, toustream, flixtrz, moviebox, vaplayer, vapor, icefy,
    movsrc, cinesu, flaxmovies, lookmovie, fsonic, fsharetv, miruro,
    tryembed, flixhq, 02movie) if none of the original four succeed.
    """
    for name, fn in RESOLVERS:
        try:
            if fn is resolve_videasy:
                result = await fn(tmdb_id, season, episode, title)
            else:
                result = await fn(tmdb_id, season, episode)
        except Exception as e:
            logger.debug(f"meowly_resolvers: {name} failed for {tmdb_id}: {e}")
            continue
        if result and result.get("videoUrl"):
            logger.info(f"meowly_resolvers: resolved {tmdb_id} via {name}")
            return result

    return await meowly_extra_resolvers.resolve_extra(tmdb_id, season, episode, title, audio)
