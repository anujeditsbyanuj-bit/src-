import os
import re
import json
import time
import hmac
import hashlib
import asyncio
import aiohttp
import httpx
from collections import OrderedDict
from urllib.parse import quote, unquote, urlparse, parse_qs
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    E_CHECK, E_CROSS, E_INFO, E_ROCKET,
    draw_bar, fmt_bytes, fmt_hms, _render_progress_box, _status_edit,
    SPLIT_SIZE, VIDEO_EXTS, AUDIO_EXTS, PHOTO_EXTS,
)
from Akbots.link_cache import try_send_cached, store as _cache_store, url_hash as _url_hash

# Optional 4th tier: transfers the shared file into a real, logged-in
# TeraBox account's own storage first, then resolves a dlink from there.
# See terabridge_account.py's module docstring for why this is a
# genuinely different failure mode from the three cookie-less tiers
# below, and what configuring it requires (NDUS cookie, optionally
# Upstash Redis for multi-account rotation).
try:
    from Akbots import terabridge_account as _tb_account
except ImportError:
    _tb_account = None


try:
    from config import DB_CHANNEL, TERABOX_SUPPORT_BOT_TOKENS, API_ID, API_HASH
except ImportError:
    DB_CHANNEL = None
    TERABOX_SUPPORT_BOT_TOKENS = []
    API_ID = API_HASH = None

# TeraBridge feature-parity layer settings (response cache / rate limiter /
# HMAC signing / Upstash Redis for a shared cache) — see the "TeraBridge
# feature-parity layer" section below for what each one drives.
try:
    from config import (
        TERABOX_CACHE_TTL_SECONDS, TERABOX_CACHE_MAX_ENTRIES,
        TERABOX_RATE_LIMIT_PER_MIN, TERABOX_HMAC_SECRET,
        UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN,
    )
except ImportError:
    TERABOX_CACHE_TTL_SECONDS = 60
    TERABOX_CACHE_MAX_ENTRIES = 500
    TERABOX_RATE_LIMIT_PER_MIN = 30
    TERABOX_HMAC_SECRET = ""
    UPSTASH_REDIS_REST_URL = ""
    UPSTASH_REDIS_REST_TOKEN = ""

# Reuses the same headless-Chromium setup as Akbots/headless.py (no new
# browser/driver dependency) to render teradownloader.com, which — like the
# JS-rendered players headless.py handles — only builds its real download
# link client-side; the raw HTML is just a "Loading..." placeholder.
try:
    from playwright.async_api import async_playwright
    from Akbots.headless import _ensure_chromium, system_chromium_path
except ImportError:
    async_playwright = None
    _ensure_chromium = None
    system_chromium_path = None

# Single source of truth for every TeraBox / mirror domain this plugin
# handles. Akbots/urluploader.py (the generic last-resort uploader) imports
# this tuple to build its own exclusion list, so the two plugins can never
# drift out of sync again — previously urluploader.py hard-coded only the
# original 6 domains and re-processed every link on this longer list a
# second time as a "raw file" after terabox.py had already delivered it.
TERABOX_DOMAINS = (
    "terabox.com", "1024terabox.com", "teraboxapp.com", "freeterabox.com",
    "nephobox.com", "4funbox.com", "4funbox.co", "4funbox.in", "terabox.app", "terabox.fun",
    "1024tera.com", "1024tera.co", "1024-terabox.com", "tera1024box.com",
    "mirrobox.com", "momerybox.com", "tibibox.com",
    "dubox.com", "terafileshare.com", "terasharelink.com", "teraboxlink.com",
    "terabox.link", "teraboxurl.com", "teraboxshare.com", "teraboxfree.com",
    "teraboxsharefile.com", "terabox.club", "terabox.click",
    "terasharefile.com", "terashareus.com", "gibibox.com", "pebibox.com",
    "fancybox.in", "bestclouddrive.com",
)

PATTERN = re.compile(
    r"(https?://)?(www\.)?("
    + "|".join(re.escape(d) for d in TERABOX_DOMAINS)
    + r")/\S+",
    re.IGNORECASE
)


# ═══════════════════════════════════════════════════════════════════════
# TeraBridge feature-parity layer
# ═══════════════════════════════════════════════════════════════════════
# Ported from TeraBridge-api (https://github.com/saahiyo-cloud/TeraBridge-api)
# on top of the extraction pipeline already in this file / in
# terabridge_account.py. Feature → implementation map:
#
#   Async-first architecture        → this whole plugin already runs on
#                                      Pyrogram's asyncio event loop; no
#                                      OS threads are spawned per request.
#   Dynamic token resolution         → terabridge_account.py's _resolve_tokens()
#   Save-location targeting          → terabridge_account.py's ROOT_PATH ("/cloudvids")
#   HLS transcoding handling         → terabridge_account.py's
#                                      resolve_stream_via_account() (errno 130)
#   Response caching (LRU + TTL)     → _ResolveCache below
#   Rate limiting                    → _RateLimiter below (per-user, since a
#                                      Telegram bot has no per-IP traffic)
#   Non-blocking HTTP client         → _http_client()/_http_get_with_retry()
#     w/ retries + HTTP/2              below (httpx.AsyncClient, http2=True,
#                                      connection pooling, retry on 5xx)
#   Single-flight request collapsing → _extract_terabox_files_singleflight()
#   HMAC-signed proxy URLs           → _sign_token()/_verify_token() below;
#                                      used to sign cache entries so a
#                                      tampered/cross-instance cache read is
#                                      detected and discarded
#   Multi-account pool over Redis    → terabridge_account.py's
#                                      get_next_healthy_account() (Upstash)
# ═══════════════════════════════════════════════════════════════════════

# ── HMAC-signed tokens ──────────────────────────────────────────────────
# Generic time-limited HMAC signer/verifier. Used below to sign cached
# resolve entries (so a poisoned/stale Redis read is rejected instead of
# served), and available for any future proxy/download-token endpoint
# this bot exposes — the same signed-token shape TeraBridge-api uses for
# its download/stream/thumbnail proxy URLs.
def _hmac_secret() -> bytes:
    return (TERABOX_HMAC_SECRET or f"{API_HASH or 'akbots'}::terabridge-fallback-secret").encode()


def _sign_token(payload: str, ttl: int = 3600) -> str:
    expires = int(time.time()) + ttl
    msg = f"{payload}:{expires}".encode()
    sig = hmac.new(_hmac_secret(), msg, hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def _verify_token(payload: str, token: str) -> bool:
    try:
        expires_s, sig = token.split(".", 1)
        expires = int(expires_s)
    except (ValueError, AttributeError):
        return False
    if time.time() > expires:
        return False
    expected = hmac.new(_hmac_secret(), f"{payload}:{expires}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


# ── Response caching: in-memory LRU (+ optional shared Upstash Redis) ───
# Repeated requests for the same link within TERABOX_CACHE_TTL_SECONDS
# skip re-extraction entirely — mirrors TeraBridge-api's in-memory
# LRU/Upstash cache with a configurable TTL (default 60s).
_CACHE_KEY_PREFIX = "terabridge:resolve_cache:"


class _ResolveCache:
    def __init__(self, ttl: int, max_entries: int):
        self.ttl = ttl
        self.max_entries = max_entries
        self._mem: "OrderedDict[str, tuple[float, list]]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._redis = None
        self._redis_tried = False

    async def _get_redis(self):
        if self._redis_tried:
            return self._redis
        self._redis_tried = True
        if not (UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN):
            return None
        try:
            from upstash_redis.asyncio import Redis
            client = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)
            await client.ping()
            self._redis = client
        except Exception:
            self._redis = None
        return self._redis

    async def get(self, url: str):
        key = _url_hash(url)

        redis = await self._get_redis()
        if redis is not None:
            try:
                raw = await redis.get(_CACHE_KEY_PREFIX + key)
                if raw:
                    payload = json.loads(raw)
                    token, files = payload.get("sig"), payload.get("files")
                    if token and files and _verify_token(key, token):
                        return [tuple(f) for f in files]
            except Exception:
                pass  # cache errors must never break real extraction

        async with self._lock:
            entry = self._mem.get(key)
            if entry:
                ts, files = entry
                if time.time() - ts <= self.ttl:
                    self._mem.move_to_end(key)
                    return files
                self._mem.pop(key, None)
        return None

    async def put(self, url: str, files: list):
        key = _url_hash(url)
        async with self._lock:
            self._mem[key] = (time.time(), files)
            self._mem.move_to_end(key)
            while len(self._mem) > self.max_entries:
                self._mem.popitem(last=False)

        redis = await self._get_redis()
        if redis is not None:
            try:
                token = _sign_token(key, ttl=self.ttl)
                payload = json.dumps({"files": [list(f) for f in files], "sig": token})
                await redis.set(_CACHE_KEY_PREFIX + key, payload, ex=self.ttl)
            except Exception:
                pass


_resolve_cache = _ResolveCache(TERABOX_CACHE_TTL_SECONDS, TERABOX_CACHE_MAX_ENTRIES)


# ── Rate limiting: per-user sliding window ───────────────────────────────
# TeraBridge-api rate-limits per-IP (default 30 req/min) to protect Terabox
# session tokens from exhaustion; a Telegram bot's real equivalent of "one
# caller" is the Telegram user id, not an IP, so this limits per user id.
class _RateLimiter:
    def __init__(self, limit_per_min: int):
        self.limit = limit_per_min
        self._hits: dict[int, list] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: int) -> tuple[bool, float]:
        async with self._lock:
            now = time.time()
            window_start = now - 60
            hits = [t for t in self._hits.get(key, []) if t > window_start]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False, max(60 - (now - hits[0]), 1.0)
            hits.append(now)
            self._hits[key] = hits
            return True, 0.0


_rate_limiter = _RateLimiter(TERABOX_RATE_LIMIT_PER_MIN)


# ── Non-blocking HTTP client: pooled httpx.AsyncClient, HTTP/2, retries ──
# Shared across requests instead of opening a fresh aiohttp session per
# call — connection pooling + HTTP/2 multiplexing + automatic retry on
# 5xx, mirroring TeraBridge-api's httpx.AsyncClient usage.
_shared_http_client: httpx.AsyncClient | None = None


def _http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(
            http2=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": _TERADOWNLOADER_UA},
        )
    return _shared_http_client


async def _http_get_with_retry(url: str, *, retries: int = 3, backoff: float = 0.6, **kwargs) -> httpx.Response:
    """GET with automatic retry on 5xx / transport errors (connection
    refused, timeout, etc.), exponential backoff between attempts."""
    client = _http_client()
    last_exc = None
    for attempt in range(retries):
        try:
            resp = await client.get(url, **kwargs)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            last_exc = e
            if attempt < retries - 1:
                await asyncio.sleep(backoff * (2 ** attempt))
    raise last_exc


# ── Single-flight request collapsing ─────────────────────────────────────
# If several users (or a double-tap on the same link) trigger extraction
# for the same URL at the same moment, only the first actually hits
# Terabox/teradownloader.com — every concurrent duplicate awaits that same
# in-flight call instead of firing its own, then all share the (cached)
# result. Mirrors TeraBridge-api's single-flight request collapsing.
_inflight: dict[str, asyncio.Future] = {}
_inflight_lock = asyncio.Lock()


async def _extract_terabox_files_singleflight(url: str):
    key = _url_hash(url)
    async with _inflight_lock:
        fut = _inflight.get(key)
        created = False
        if fut is None:
            fut = asyncio.get_event_loop().create_future()
            _inflight[key] = fut
            created = True

    if not created:
        return await fut

    try:
        result = await _extract_terabox_files(url)
        fut.set_result(result)
    except Exception as e:
        fut.set_exception(e)
    finally:
        async with _inflight_lock:
            _inflight.pop(key, None)
    return await fut


async def _resolve_with_cache(url: str):
    """Public entry point _handle() should call: cache → single-flight
    extraction → cache the result, in that order."""
    cached = await _resolve_cache.get(url)
    if cached is not None:
        return cached
    files = await _extract_terabox_files_singleflight(url)
    await _resolve_cache.put(url, files)
    return files


# NOTE: savetube.me (the previous extraction API) has been permanently
# retired. Two extraction methods now run in order:
#   1. A direct scrape of the TeraBox share page itself (no browser needed,
#      fast) — ported from teradownloader-main's server/services/
#      teraboxService.js (Node/cheerio) to aiohttp + regex below.
#   2. If that finds nothing (TeraBox's page markup shifts often), the
#      existing teradownloader.com + headless Chromium fallback further
#      down in this file kicks in.
# Running both means a change on either side (TeraBox's own site, or
# teradownloader.com) doesn't take the whole plugin down at once.

_TERADOWNLOADER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_DIRECT_SCRAPE_HEADERS_EXTRA = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Patterns for pulling a real download link out of a TeraBox share page's
# raw HTML/inline <script> content. Checked in order; first match wins.
_DIRECT_DLINK_PATTERNS = (
    re.compile(r'"dlink"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"downloadUrl"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"download_url"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'downloadUrl["\s]*:["\s]*["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'https?://[^"\'\s]*d\.terabox[^"\'\s]+', re.IGNORECASE),
    re.compile(r'https?://[^"\'\s]*terabox[^"\'\s]*/[^"\'\s]+\.(?:mp4|mp3|pdf|zip|rar|avi|mkv|mov|jpg|png|gif|doc|docx|txt|jpeg|webp)', re.IGNORECASE),
)

_DIRECT_FILENAME_PATTERNS = (
    re.compile(r'"filename"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"file_name"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'"server_filename"\s*:\s*"([^"]+)"', re.IGNORECASE),
)


def _extract_filename_from_html(html: str, page_url: str) -> str | None:
    """Best-effort filename lookup straight from a TeraBox share page's
    HTML — mirrors extractFileName() in teraboxService.js (og:title meta
    tag, then inline JSON, then the URL itself), stripped down to what's
    reusable outside a browser DOM."""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    name = m.group(1) if m else None

    if not name or "terabox" in name.lower():
        for pat in _DIRECT_FILENAME_PATTERNS:
            m = pat.search(html)
            if m:
                name = m.group(1)
                break

    if not name:
        base = os.path.basename(urlparse(page_url).path)
        name = unquote(base) if base else None

    if name:
        name = re.sub(r"\s*-\s*Tera[Bb]ox.*$", "", name).strip()
        return safe_filename(name, "terabox_download")
    return None


def _extract_download_url_from_html(html: str) -> str | None:
    """Mirrors getDirectDownloadUrl()'s script-tag regex scan in
    teraboxService.js — TeraBox share pages often embed the eventual dlink
    (or enough of the page's own API JSON) directly in inline <script>
    content, avoiding the need to render the page in a real browser."""
    for pat in _DIRECT_DLINK_PATTERNS:
        m = pat.search(html)
        if m:
            candidate = m.group(1) if m.groups() else m.group(0)
            if candidate.startswith("http"):
                return candidate.encode().decode("unicode_escape") if "\\u" in candidate else candidate
    return None


async def _fetch_via_direct_scrape(link: str, timeout: int = 20):
    """Attempts to extract a direct download link straight from the
    TeraBox share page's own HTML/inline scripts — no browser required.
    Ported from teradownloader-main's teraboxService.js. Raises ValueError
    if no usable link is found, so the caller can fall back to the
    Playwright + teradownloader.com method.
    """
    parsed = urlparse(link)
    domain = parsed.netloc
    headers = {"User-Agent": _TERADOWNLOADER_UA, "Referer": f"https://{domain}/", **_DIRECT_SCRAPE_HEADERS_EXTRA}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.get(link, headers=headers, allow_redirects=True) as resp:
            html = await resp.text(errors="ignore")
            final_url = str(resp.url)

    download_url = _extract_download_url_from_html(html)
    if not download_url:
        raise ValueError("direct scrape: no dlink found in share page markup")

    if not await _head_ok(download_url):
        raise ValueError("direct scrape: candidate dlink did not resolve")

    filename = _extract_filename_from_html(html, final_url) or safe_filename(None, "terabox_file")
    return [(download_url, filename)]


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


# ── Guest-session extraction (no NDUS login needed at all) ─────────────────
# Ported from the Cloudflare Worker (tera_api_final-4.js's "Mode 1", itself
# ported from TeraDL's terabox1.py): for a PUBLIC share, TeraBox's mobile/wap
# endpoints (wap/share/filelist, api/shorturlinfo, share/download) work with
# a fresh, anonymous guest session — no logged-in NDUS cookie required at
# all (ours or any public pool's). This is a genuinely independent fallback:
# it doesn't depend on any account being valid/not-rate-limited, only on the
# share itself being public. Trade-off: no HLS streaming link this way, only
# a raw direct-file dlink — which is exactly what stream_download()/
# upload_file() below already use anyway, so nothing is lost for this bot.
GUEST_UA = ("Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36")


def _resolve_surl(text: str) -> str | None:
    """Mirrors the Worker's resolveSurl(): pulls the share-id ('surl') out
    of a full TeraBox URL (either /s/<surl> path or a ?surl= query param),
    or passes a bare surl/ID straight through. The guest-session endpoints
    below talk to TeraBox by surl directly, not the full share URL."""
    text = text.strip()
    if text.startswith("http"):
        try:
            parsed = urlparse(text)
            m = re.search(r"/s/([a-zA-Z0-9_-]+)", parsed.path)
            if m:
                return m.group(1)
            q = parse_qs(parsed.query).get("surl")
            if q:
                return q[0]
        except Exception:
            pass
        return None
    if "/s/" in text:
        m = re.search(r"/s/([a-zA-Z0-9_-]+)", text)
        if m:
            return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]+", text):
        return text
    return None


def _extract_set_cookies(resp) -> str:
    """Mirrors the Worker's extractSetCookies(): joins every Set-Cookie
    response header's name=value part (dropping Path/Expires/etc.
    attributes) into one Cookie-header-ready string."""
    return "; ".join(v.split(";")[0] for v in resp.headers.getall("Set-Cookie", []))


async def _fetch_guest_session(surl: str):
    """Ported from the Worker's fetchGuestSession(). Returns None if the
    share isn't public or TeraBox's wap endpoint has changed shape (caller
    treats that as "this method doesn't work for this link" and moves on
    to the next fallback, same as the other extraction methods here)."""
    short_url = surl[1:] if (surl.startswith("1") and len(surl) > 20) else surl

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        fl_url = f"https://www.terabox.app/wap/share/filelist?surl={short_url}"
        async with session.get(fl_url, headers={"User-Agent": GUEST_UA}) as fl_resp:
            fl_text = (await fl_resp.text(errors="ignore")).replace("\\", "")
            guest_cookie = _extract_set_cookies(fl_resp)

        m = re.search(r'%28%22(.*?)%22%29', fl_text)
        if not m or not guest_cookie:
            return None
        js_token = m.group(1)

        info_url = f"https://www.terabox.com/api/shorturlinfo?app_id=250528&shorturl=1{short_url}&root=1"
        async with session.get(info_url, headers={"User-Agent": GUEST_UA, "Cookie": guest_cookie}) as info_resp:
            try:
                info = await info_resp.json(content_type=None)
            except Exception:
                return None

    if not info or info.get("errno") or not info.get("list"):
        return None
    return {"info": info, "js_token": js_token, "guest_cookie": guest_cookie}


async def _get_guest_download_link(fs_id, uk, shareid, timestamp, sign, js_token, guest_cookie) -> str | None:
    """Ported from the Worker's getGuestDownloadLink()."""
    params = {
        "uk": str(uk), "sign": str(sign or ""), "shareid": str(shareid), "primaryid": str(shareid),
        "timestamp": str(timestamp or ""), "jsToken": js_token, "fid_list": f"[{fs_id}]",
        "app_id": "250528", "channel": "dubox", "product": "share", "clienttype": "0",
        "dp-logid": "", "nozip": "0", "web": "1",
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(
                "https://www.terabox.com/share/download", params=params,
                headers={"User-Agent": GUEST_UA, "Cookie": guest_cookie},
            ) as resp:
                data = await resp.json(content_type=None)
        if not data or data.get("errno"):
            return None
        return data.get("dlink") or None
    except Exception:
        return None


async def _fetch_via_guest_mode(url: str):
    """Cookie-free fallback for any PUBLIC TeraBox share — no NDUS login
    cookie needed at all, ours or a public pool's; see the module docstring
    above. Returns a list of (url, filename) tuples, same shape as the
    other two extraction methods, so it drops straight into
    _extract_terabox_files's fallback chain. Raises ValueError if the
    share isn't public, or files were found but no dlink could be
    resolved for any of them."""
    surl = _resolve_surl(url)
    if not surl:
        raise ValueError("couldn't read a share ID (surl) from this link")

    session_data = await _fetch_guest_session(surl)
    if not session_data:
        raise ValueError("guest session extraction failed (share may be private, or TeraBox changed its wap endpoint)")

    info = session_data["info"]
    js_token, guest_cookie = session_data["js_token"], session_data["guest_cookie"]
    raw_files = [f for f in (info.get("list") or []) if str(f.get("isdir")) != "1"]
    if not raw_files:
        raise ValueError("no files found via guest session")

    results = []
    for item in raw_files:
        dlink = await _get_guest_download_link(
            item.get("fs_id"), info.get("uk"), info.get("shareid"),
            info.get("timestamp"), info.get("sign"), js_token, guest_cookie,
        )
        if dlink:
            filename = safe_filename(item.get("server_filename"), "terabox_file")
            results.append((dlink, filename))

    if not results:
        raise ValueError("guest session found files but couldn't get any download link")
    return results



async def _filename_from_headers(url: str) -> str | None:
    """Best-effort filename lookup via a HEAD request — teradownloader's
    scraped CDN links don't come with a filename attached the way the
    savetube API response does, but the CDN itself usually reveals one
    through Content-Disposition (or, failing that, the URL path)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.head(url, allow_redirects=True) as resp:
                cd = resp.headers.get("Content-Disposition", "")
                m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
                if m:
                    return safe_filename(unquote(m.group(1)), "terabox_file")
                base = os.path.basename(urlparse(str(resp.url)).path)
                if base:
                    return safe_filename(base, "terabox_file")
    except Exception:
        pass
    return None


# Known TeraBox CDN domains — used to tell the real file link apart from
# teradownloader.com's own nav/preview/ad links when the primary selector
# below doesn't match cleanly.
_CDN_DOMAIN_HINTS = (
    "1024tera", "freeterabox", "terabox.app", "terabox.com",
    "4funbox", "nephobox", "terabox.link",
)


async def _collect_candidate_hrefs(page):
    """Gathers every plausible download link on the rendered page, along with
    a best-effort display name for each (used for folder links, where the
    page renders one row per file — a single-file link just ends up as a
    list of one). Tries the specific selector the site currently uses
    first, then falls back to scanning every anchor if that selector
    doesn't match (site markup may have shifted slightly) — better to
    over-collect here and filter/verify below than to miss a real link
    because of one narrow selector.

    Returns a list of (href, display_name_or_None) tuples, in document
    order, with hrefs de-duplicated (first name seen wins)."""
    items = []
    seen = set()

    def _add(href, name):
        if href and href.startswith("http") and href not in seen:
            seen.add(href)
            items.append((href, name or None))

    try:
        # Folder pages render one block per file; grabbing the block's own
        # text alongside its link lets us recover a filename per row instead
        # of just one link for the whole page.
        rows = await page.query_selector_all("div.p-5")
        for row in rows:
            anchors = await row.query_selector_all("a")
            if not anchors:
                continue
            row_text = None
            try:
                row_text = (await row.inner_text() or "").strip() or None
            except Exception:
                pass
            for a in anchors:
                href = await a.get_attribute("href")
                # Prefer the anchor's own visible text as the name; fall
                # back to the row's text if the anchor itself has none.
                a_text = None
                try:
                    a_text = (await a.inner_text() or "").strip() or None
                except Exception:
                    pass
                _add(href, a_text or row_text)
    except Exception:
        pass

    if not items:
        try:
            anchors = await page.query_selector_all("a")
            for a in anchors:
                href = await a.get_attribute("href")
                if href and "teradownloader.com" not in href:
                    a_text = None
                    try:
                        a_text = (await a.inner_text() or "").strip() or None
                    except Exception:
                        pass
                    _add(href, a_text)
        except Exception:
            pass

    return items


def _rank_candidates(items):
    """Puts items whose href matches a known TeraBox CDN domain first (most
    likely to be real files), keeping the rest as a lower-priority
    fallback. Operates on (href, name) tuples and preserves order within
    each group."""
    preferred = [it for it in items if any(hint in it[0] for hint in _CDN_DOMAIN_HINTS)]
    rest = [it for it in items if it not in preferred]
    return preferred + rest


async def _head_ok(url: str) -> bool:
    """Confirms a candidate link actually resolves to a downloadable
    response before we commit to it and start streaming it to the user."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.head(url, allow_redirects=True) as resp:
                return resp.status in (200, 206)
    except Exception:
        return False


async def _render_and_collect(page_url: str, timeout: int):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=system_chromium_path() if system_chromium_path else None,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            context = await browser.new_context(user_agent=_TERADOWNLOADER_UA)
            page = await context.new_page()
            await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            try:
                await page.wait_for_selector("div.p-5 a", timeout=timeout * 1000)
            except Exception:
                pass  # fall through to the broader any-anchor scan below regardless
            # The link is sometimes populated a beat after the selector
            # first appears (async JS finishing up) — give it a moment.
            await page.wait_for_timeout(1500)
            return await _collect_candidate_hrefs(page)
        finally:
            await browser.close()


def _name_to_filename(name: str) -> str | None:
    """Turns a row's visible text (which may include size/date noise
    alongside the actual filename) into a clean filename, if the text
    looks like it contains one."""
    if not name:
        return None
    # The filename is usually the first "word group" on the row; take the
    # longest whitespace-delimited chunk that contains a dot-extension,
    # since surrounding text (file size, date, "Download" button label) is
    # what's most likely to lack one.
    candidates = re.findall(r"[^\s]+\.[A-Za-z0-9]{2,5}", name)
    if candidates:
        return safe_filename(max(candidates, key=len), None)
    return None


async def _fetch_all_via_teradownloader(link: str, timeout: int = 25):
    """Extracts direct TeraBox CDN download link(s) via teradownloader.com.

    teradownloader.com resolves the actual TeraBox CDN link(s) entirely in
    client-side JS, so this renders the page in headless Chromium via
    Playwright rather than scraping static HTML (which only ever shows a
    "Loading..." placeholder). For a single-file share link this yields one
    file; for a folder share link the same rendered page lists one row per
    file, so every verified candidate is returned rather than just the
    first — this is what gives us folder support.

    Every candidate link found is verified with a HEAD request before being
    accepted, and the whole render is retried once if the first attempt
    turns up nothing valid (the site is occasionally slow to populate
    links). Raises ValueError if nothing usable is found after that, so the
    caller can report the failure.

    Returns a list of (url, filename) tuples — one entry for a single file,
    multiple for a folder.
    """
    if async_playwright is None:
        raise ValueError("Playwright not installed — teradownloader unavailable.")

    if _ensure_chromium is not None:
        try:
            # Hard cap: on hosts where the browser binary/deps are missing
            # or broken (e.g. Replit's default Nix env, which the
            # Dockerfile's build-time `playwright install --with-deps
            # chromium` never runs on), this call can otherwise hang far
            # longer than any user will wait, with the status message
            # stuck on "extracting..." forever and no error ever shown.
            await asyncio.wait_for(_ensure_chromium(), timeout=60)
        except asyncio.TimeoutError:
            raise ValueError(
                "Chromium setup timed out on this host. If you're on Replit, "
                "the Dockerfile's 'playwright install --with-deps chromium' step "
                "never runs there — the browser's system libraries are likely missing."
            )

    page_url = f"https://teradownloader.com/download?l={quote(link, safe='')}"

    last_error = None
    for attempt in range(2):  # one retry if the page was just slow
        try:
            # Same reasoning as above: cap each render attempt so a broken/
            # missing Chromium install fails fast with a real error instead
            # of hanging indefinitely on browser.launch().
            items = await asyncio.wait_for(_render_and_collect(page_url, timeout), timeout=timeout + 20)
        except asyncio.TimeoutError:
            last_error = "browser render timed out (chromium may be missing its system libraries on this host)"
            continue
        except Exception as e:
            msg = str(e)
            if "Executable doesn't exist" in msg or "missing dependencies" in msg.lower():
                raise ValueError(
                    "Chromium isn't properly installed on this host (browser binary or its "
                    "system libraries are missing). This commonly happens on Replit's default "
                    "environment, which skips the Dockerfile's chromium setup step."
                )
            last_error = msg
            continue

        results = []
        for href, name in _rank_candidates(items):
            if await _head_ok(href):
                filename = _name_to_filename(name) or await _filename_from_headers(href) or safe_filename(None, "terabox_file")
                results.append((href, filename))

        if results:
            return results

        last_error = "page rendered but no candidate link responded to a HEAD request"

    raise ValueError(
        f"teradownloader: no working download link found after 2 attempts "
        f"(site markup may have changed, or the link is invalid/private). Last issue: {last_error}"
    )


async def _extract_terabox_files(url: str):
    """Tries progressively heavier/more-dependent methods, stopping at the
    first one that returns something usable:
      0. Account-transfer bridge (terabridge_account.py) — only attempted
         when a TeraBox account is configured (TERABOX_NDUS_COOKIE /
         TERABOX_ACCOUNTS). Genuinely different failure mode from the
         three below: logs in, transfers the file into that account's own
         storage, then resolves a dlink from there. Tried first when
         available since a real (ideally premium) account is generally
         the most reliable path and survives share-page markup changes
         that break the scrape tier.
      1. Direct scrape of the share page's own HTML (fastest, no browser
         or session needed at all).
      2. Guest-session mode — still no browser and no login cookie needed,
         just TeraBox's own public wap/api endpoints (see
         _fetch_via_guest_mode's docstring). No HLS this way, but that's
         irrelevant here since we only ever need a direct dlink.
      3. teradownloader.com rendered via headless Chromium (heaviest, and
         known-fragile on hosts without a working browser install, e.g.
         Replit's default environment) — last resort.
    """
    errors = []

    if _tb_account is not None and _tb_account.is_configured():
        try:
            return await _tb_account.resolve_via_account(url)
        except Exception as e:
            errors.append(f"account-transfer bridge failed ({e})")

    try:
        return await _fetch_via_direct_scrape(url)
    except Exception as e:
        errors.append(f"direct scrape failed ({e})")

    try:
        return await _fetch_via_guest_mode(url)
    except Exception as e:
        errors.append(f"guest mode failed ({e})")

    try:
        return await _fetch_all_via_teradownloader(url)
    except Exception as e:
        errors.append(f"teradownloader.com fallback also failed ({e})")

    raise ValueError("; ".join(errors))


# ── Support-bot upload pool ─────────────────────────────────────────────
# Ported from terabnr.py's Bot_1..Bot_4 relay system. When
# TERABOX_SUPPORT_BOT_TOKENS is configured, finished files are uploaded to
# DB_CHANNEL by whichever support bot is free (instead of the main bot
# uploading straight to the user), a small MongoDB record is kept (see
# database/db.py's set_terabox_relay/get_terabox_relay), and the main bot
# then instantly copies that DB_CHANNEL message to the user. This keeps the
# main bot's own upload slot free for other plugins under heavy TeraBox
# traffic. If no tokens are configured, or every support bot is busy past
# the wait window, this transparently falls back to the normal single-bot
# upload_file() path — nothing breaks with an empty pool.
_support_clients: dict[str, Client] = {}
_support_busy: dict[str, bool] = {}
_support_lock = asyncio.Lock()
_support_pool_ready = False


async def _ensure_support_pool():
    global _support_pool_ready
    if _support_pool_ready or not TERABOX_SUPPORT_BOT_TOKENS or not API_ID:
        return
    async with _support_lock:
        if _support_pool_ready:
            return
        for idx, token in enumerate(TERABOX_SUPPORT_BOT_TOKENS, start=1):
            name = f"terabox_support_{idx}"
            try:
                cli = Client(name, api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
                await cli.start()
                _support_clients[name] = cli
                _support_busy[name] = False
            except Exception:
                continue  # a bad/dead token just means one fewer helper, not a crash
        _support_pool_ready = True


async def _get_free_support_bot(wait_seconds: int = 12):
    """Returns (name, client) for a free support bot, waiting briefly for
    one to free up. Returns (None, None) if the pool is empty/unconfigured
    or nobody frees up in time, so the caller can fall back to the main
    bot's own upload."""
    await _ensure_support_pool()
    if not _support_clients:
        return None, None

    deadline = asyncio.get_event_loop().time() + wait_seconds
    while True:
        async with _support_lock:
            for name, busy in _support_busy.items():
                if not busy:
                    _support_busy[name] = True
                    return name, _support_clients[name]
        if asyncio.get_event_loop().time() >= deadline:
            return None, None
        await asyncio.sleep(0.8)


async def _release_support_bot(name: str):
    if name is None:
        return
    async with _support_lock:
        _support_busy[name] = False


def _relay_progress(status, uploader_label: str):
    state = {"last_edit": 0.0, "last_pct": -1, "last_bytes": 0, "last_time": time.time()}

    async def _progress(current, total):
        now = time.time()
        pct = (current * 100 / total) if total else 0
        finished = total and current >= total
        if not finished and (now - state["last_edit"] < 2.5 or int(pct) == state["last_pct"]):
            return
        state["last_edit"] = now
        state["last_pct"] = int(pct)
        interval = now - state["last_time"]
        speed_bps = ((current - state["last_bytes"]) / interval) if interval > 0 else 0
        state["last_bytes"] = current
        state["last_time"] = now
        eta_secs = ((total - current) / speed_bps) if speed_bps > 0 else None

        bar = draw_bar(pct, length=10, filled="⬢", empty="⬡")
        rows = [
            f"[{bar}]",
            f"✅ {pct:.1f}%",
            f"💾 {fmt_bytes(current)} / {fmt_bytes(total)}",
            f"⚡ {fmt_bytes(speed_bps)}/s",
            f"⏳ {fmt_hms(eta_secs)}",
        ]
        await _status_edit(status, _render_progress_box(
            f"<b>{E_ROCKET} Uploading via {uploader_label}</b>", rows, footer="🌩 Support-bot relay"
        ))

    return _progress


async def _relay_upload(client: Client, message: Message, path: str, status,
                         caption: str, file_name: str, cache_url: str = None):
    """Uploads `path` through a free support bot into DB_CHANNEL, records
    it in MongoDB, then copies it from DB_CHANNEL to the user via the main
    bot. Falls back to the normal direct upload_file() path if no support
    bot is free (or none configured) or the file is large enough to need
    splitting (upload_file already handles splitting; duplicating that
    here isn't worth it for the relay path)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    if size > SPLIT_SIZE:
        return await upload_file(client, message, path, status, caption, file_name=file_name, cache_url=cache_url)

    support_name, support_client = await _get_free_support_bot()
    if support_client is None or DB_CHANNEL is None:
        return await upload_file(client, message, path, status, caption, file_name=file_name, cache_url=cache_url)

    ext = os.path.splitext(path)[1].lower()
    progress = _relay_progress(status, support_name.replace("terabox_", "").replace("_", "-").title())

    try:
        if ext in VIDEO_EXTS:
            sent = await support_client.send_video(
                chat_id=DB_CHANNEL, video=path, caption=caption,
                supports_streaming=True, parse_mode=enums.ParseMode.HTML, progress=progress
            )
        elif ext in AUDIO_EXTS:
            sent = await support_client.send_audio(
                chat_id=DB_CHANNEL, audio=path, caption=caption,
                parse_mode=enums.ParseMode.HTML, progress=progress
            )
        elif ext in PHOTO_EXTS:
            sent = await support_client.send_photo(
                chat_id=DB_CHANNEL, photo=path, caption=caption,
                parse_mode=enums.ParseMode.HTML, progress=progress
            )
        else:
            sent = await support_client.send_document(
                chat_id=DB_CHANNEL, document=path, caption=caption,
                parse_mode=enums.ParseMode.HTML, progress=progress
            )
    except Exception as e:
        # Support bot failed mid-upload (flood-wait, banned from
        # DB_CHANNEL, etc.) — fall back to the main bot rather than
        # losing the file the user already waited for.
        await _release_support_bot(support_name)
        return await upload_file(client, message, path, status, caption, file_name=file_name, cache_url=cache_url)

    try:
        uid = _url_hash(cache_url) if cache_url else _url_hash(f"{message.chat.id}:{message.id}:{file_name}")
        from database.db import db
        await db.set_terabox_relay(uid, {
            "msg_id": sent.id,
            "db_channel": DB_CHANNEL,
            "filename": file_name,
            "size_mb": round(size / (1024 * 1024), 2),
            "uploaded_via": support_name,
        })
    except Exception:
        pass  # relay bookkeeping is best-effort, must never block delivery

    try:
        delivered = await client.copy_message(
            chat_id=message.chat.id, from_chat_id=DB_CHANNEL, message_id=sent.id,
            reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML,
        )
        if cache_url:
            try:
                await _cache_store(cache_url, delivered, caption=caption)
            except Exception:
                pass
        try:
            from Akbots.user_stats import record_usage
            await record_usage(message.from_user.id, uploaded_bytes=size, success_count=1)
        except Exception:
            pass
    finally:
        await _release_support_bot(support_name)
        try:
            await status.delete()
        except Exception:
            pass
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


async def _handle(client: Client, message: Message, url: str):
    allowed, retry_after = await _rate_limiter.allow(message.from_user.id)
    if not allowed:
        return await message.reply_text(
            f"<b>{E_CROSS} Slow down:</b> TeraBox is limited to {TERABOX_RATE_LIMIT_PER_MIN} requests/minute per user. "
            f"Try again in {retry_after:.0f}s.",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(f"<b>{E_INFO} TeraBox link detected — extracting...</b>", parse_mode=enums.ParseMode.HTML)
    if await try_send_cached(client, message, url, status):
        return
    try:
        files = await _resolve_with_cache(url)
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    folder = make_output_folder("terabox")
    total = len(files)
    is_folder = total > 1
    ok_count = 0

    for idx, (direct_url, filename) in enumerate(files, start=1):
        prefix = f"[{idx}/{total}] " if is_folder else ""
        try:
            # message.id is only unique WITHIN a single chat, not globally,
            # so two users whose messages happen to share an id would
            # otherwise collide on the same filename in this shared
            # folder; include chat.id, and the file index for folders, to
            # keep every destination path globally unique.
            dest = f"{folder}/{message.chat.id}_{message.id}_{idx}_{filename}"
            await stream_download(
                direct_url, dest, status, f"{prefix}Downloading from TeraBox",
                user_id=message.from_user.id, file_name=filename
            )
            caption = f"<b>{E_CHECK} TeraBox File</b>\n<code>{filename}</code>"
            if is_folder:
                caption += f"\n<i>{idx}/{total}</i>"
            await _relay_upload(client, message, dest, status, caption, filename, cache_url=(url if not is_folder else None))
            ok_count += 1
        except Exception as e:
            # One bad file in a folder shouldn't stop the rest from being
            # fetched — report it and continue to the next.
            await message.reply_text(
                f"<b>{E_CROSS} Failed:</b> <code>{filename}</code>\n<code>{e}</code>",
                parse_mode=enums.ParseMode.HTML
            )

    if is_folder:
        await status.edit_text(
            f"<b>{E_CHECK} TeraBox folder done:</b> {ok_count}/{total} file(s) delivered.",
            parse_mode=enums.ParseMode.HTML
        )


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def terabox_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/terabox &lt;terabox URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)


@Client.on_message(filters.command("terastream") & filters.private)
async def terastream_command(client: Client, message: Message):
    """Returns the raw HLS (.m3u8) manifest link for a TeraBox video,
    handling TeraBox's own transcoding delay (errno 130) automatically —
    see terabridge_account.py's resolve_stream_via_account(). Requires
    the account-transfer bridge to be configured (TERABOX_NDUS_COOKIE /
    TERABOX_ACCOUNTS); this doesn't fall back to the cookie-less tiers,
    which can't produce an HLS manifest at all."""
    if _tb_account is None or not _tb_account.is_configured():
        return await message.reply_text(
            f"<b>{E_CROSS}</b> HLS streaming needs a TeraBox account configured "
            f"(<code>TERABOX_NDUS_COOKIE</code> / <code>TERABOX_ACCOUNTS</code>).",
            parse_mode=enums.ParseMode.HTML,
        )
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/terastream &lt;terabox URL&gt; [quality]</code>\n"
            f"<i>quality: M3U8_AUTO_1080 / 720 / 480 / 360 (default: tries all, highest first)</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    allowed, retry_after = await _rate_limiter.allow(message.from_user.id)
    if not allowed:
        return await message.reply_text(
            f"<b>{E_CROSS} Slow down:</b> try again in {retry_after:.0f}s.",
            parse_mode=enums.ParseMode.HTML,
        )

    url = extract_url(message.command[1]) or message.command[1]
    quality = message.command[2] if len(message.command) > 2 else None
    status = await message.reply_text(
        f"<b>{E_INFO} Resolving HLS stream...</b>", parse_mode=enums.ParseMode.HTML
    )
    try:
        res = await _tb_account.resolve_stream_via_account(url, quality=quality, wait=False)
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if res["status"] == "transcoding":
        return await status.edit_text(
            f"<b>{E_INFO} Still transcoding:</b> <code>{res['filename']}</code>\n{res['message']}",
            parse_mode=enums.ParseMode.HTML,
        )

    # HMAC-signed integrity token for this manifest — same signing utility
    # (_sign_token/_verify_token) TeraBridge-api's proxy URLs use, kept
    # here as a verifiable receipt in case this manifest is later relayed
    # through a local proxy route.
    signed = _sign_token(res["m3u8"][:64], ttl=3600)
    await status.edit_text(
        f"<b>{E_CHECK} HLS ready</b> (<code>{res['quality']}</code>)\n"
        f"<code>{res['filename']}</code>\n"
        f"<i>integrity token:</i> <code>{signed}</code>\n\n"
        f"<code>{res['m3u8'][:3500]}</code>",
        parse_mode=enums.ParseMode.HTML,
    )
