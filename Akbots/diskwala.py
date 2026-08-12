"""
Diskwala (diskwala.com, diskwala.app, flezen.com mirror) support — ported
from TeraBox-Video-Downloader's diskwalaDL/public_api.py client. That repo
runs its own full Telethon-based download/upload/caching pipeline around
this client; rather than duplicate that (different framework, its own
Firebase cache, its own queue/flood handling), this file just does the
diskwala.com -> direct-URL resolution and then hands the resolved URL to
this bot's own generic download/upload pipeline (Akbots/urluploader.py's
_handle), which already does resumable download, upload, and link-caching.

Resolve order:
  1. DISKWALA_PROXY_URL + DISKWALA_API_KEY (config.py), if set — a private
     proxy service some operators run, known-reliable when configured.
  2. Baidu PCS direct resolve (get_diskwala_info_baidu_pcs) — DiskWala (like
     TeraBox/1024Tera) is a reskinned Baidu PCS share frontend under the
     hood: /api/shorturlinfo -> /share/list -> /api/download, using
     guest-session cookies the site itself hands out on the first request.
     No login, no API key, no proxy needed for this tier at all.
  3. Generic HTML page-scrape (get_diskwala_info_guest) — last-resort, in
     case a given link's page doesn't follow the Baidu PCS API shape.
"""

import re
import time
import gzip
import zlib
import uuid
import logging
import asyncio
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup
from config import DISKWALA_PROXY_URL, DISKWALA_API_KEY
from Akbots.direct_utils import E_CROSS, E_INFO, safe_edit
from Akbots.nova_bypasser.ss.request_queue import RequestQueue
# Same generic "find a playable URL in whatever came back" extractor
# terabox.py's third-party-API fallback chain uses — reused here instead
# of duplicating it, for the guest-mode page scrape below. make_button/
# to_small_caps/BUTTON_STYLE_SUPPORTED are the same folder-picker keyboard
# helpers terabox.py's own folder flow uses — reused for the playlist/
# creator picker below instead of a second copy of the same code.
from Akbots.terabox import (
    _generic_extract_stream_info, make_button, to_small_caps, BUTTON_STYLE_SUPPORTED, get_size,
)
try:
    from pyrogram.enums import ButtonStyle
except ImportError:
    ButtonStyle = None
# Same generic per-domain cookies store goon_provider.py auto-detects
# from (/setcookies, /cookie panel) — if an admin has uploaded cookies
# for diskwala.com, the guest fetch below uses them automatically.
from Akbots.cookies_manager import get_cookies_for_url
from Akbots.cookie_utils import parse_cookies

logger = logging.getLogger(__name__)

# Concurrency cap for diskwala downloads specifically (separate instance
# from linkbypass.py's — a full video download+upload is much heavier
# than a link resolve, so it gets its own, smaller limit rather than
# competing with /bypass for the same slots). Mirrors upstream's
# terabox_queue flood-control semaphore around _dw_helper.
_dw_queue = RequestQueue(max_concurrent=3)

# Diskwala share links come in 3 flavours on diskwala.com — /app/<id>
# (single video), /creator/<id> and /playlist/<id> (both multi-file, same
# Baidu PCS share underneath, share/list just returns more than one file)
# — all with a 24-char hex ObjectId. diskwala.app is the same site on an
# alternate TLD (same path shape). flezen.com is a mirror using /s/<id>
# instead, with a looser alphanumeric id (not confirmed to be a strict
# ObjectId there, so kept permissive).
DISKWALA_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:diskwala\.(?:com|app)/(?:app|creator|playlist)/[a-fA-F0-9]{24}|flezen\.com/s/[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
DISKWALA_DOMAINS = ("diskwala.com", "diskwala.app", "flezen.com")
_LINK_ID_RE = re.compile(r"/(?:app|creator|playlist|s)/([A-Za-z0-9_-]+)", re.IGNORECASE)
# Just the multi-file kinds — used to route straight to the folder/picker
# flow instead of the single-file one without needing an extra API round
# trip first.
_MULTI_FILE_PATH_RE = re.compile(r"/(?:creator|playlist)/", re.IGNORECASE)



class DiskwalaError(Exception):
    """Raised when the Diskwala proxy fails or returns unusable data."""


def extract_diskwala_id(text: str):
    m = _LINK_ID_RE.search(text or "")
    return m.group(1) if m else None


def extract_diskwala_url(text: str):
    m = DISKWALA_URL_RE.search(text or "")
    return m.group(0).rstrip(").,]}\"'") if m else None


def extract_all_diskwala_urls(text: str) -> list:
    """All (deduped) Diskwala/Flezen URLs in a message — ported from
    diskwalaDL/public_api.py's extract_all_diskwala_urls, so pasting
    several links in one message bypasses all of them, not just the
    first (matches how telegram_logic/commands/diskwala.py's /dw
    command behaves upstream — one asyncio.gather over every link)."""
    seen, urls = set(), []
    for m in DISKWALA_URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(").,]}\"'")
        if url not in seen and extract_diskwala_id(url):
            seen.add(url)
            urls.append(url)
    return urls


def _extract_error_detail(resp: "requests.Response") -> str:
    try:
        detail = resp.json().get("detail")
    except Exception:
        return (resp.text or "")[:200] or f"HTTP {resp.status_code}"
    if isinstance(detail, list):
        return "; ".join(str(d.get("msg", d)) for d in detail)
    return str(detail) if detail else f"HTTP {resp.status_code}"


def get_diskwala_info(diskwala_url: str) -> dict:
    """Resolve a Diskwala share URL to downloadable video info. Returns
    {"filename": str, "size": int, "download_url": str}. Raises
    DiskwalaError on any failure. Sync (uses `requests`) — call via
    asyncio.to_thread, same as the original repo did."""
    if not DISKWALA_PROXY_URL:
        raise DiskwalaError("DISKWALA_PROXY_URL not set in environment")
    if not DISKWALA_API_KEY:
        raise DiskwalaError("DISKWALA_API_KEY not set in environment")

    try:
        resp = requests.post(
            DISKWALA_PROXY_URL,
            json={"url": diskwala_url},
            headers={"x-api-key": DISKWALA_API_KEY},
            timeout=600,
        )
    except requests.RequestException as e:
        raise DiskwalaError(f"Could not reach Diskwala proxy: {e}") from e

    if resp.status_code != 200:
        raise DiskwalaError(_extract_error_detail(resp))

    try:
        data = resp.json()
    except ValueError as e:
        raise DiskwalaError(f"Invalid JSON from Diskwala proxy: {e}") from e

    file_info = data.get("fileInfo") or {}
    download_url = file_info.get("url")
    if not download_url:
        raise DiskwalaError(f"No download URL in Diskwala response: {data}")

    return {
        "filename": file_info.get("name") or "diskwala_video.mp4",
        "size": int(file_info.get("size") or 0),
        "download_url": download_url,
    }


# ── Baidu PCS direct resolve (no login, no proxy) ────────────────────────
# DiskWala/TheDiskWala (like TeraBox/1024Tera) is a reskinned frontend over
# Baidu's PCS share API. Ported from a Cloudflare Worker doing the same
# thing for a Flutter/web player: hit /api/shorturlinfo to get a guest
# session (the site hands out session cookies on this very request — no
# login needed), list the share's files, then request a signed download
# link per file with type=nolimit (bypasses the free-tier speed cap).

_BAIDU_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
_SURL_RE = re.compile(r"/s/([a-zA-Z0-9_-]+)")


def _extract_surl(diskwala_url: str) -> str | None:
    """The share code Baidu's API wants. Try the /s/<code> shape the
    backend itself uses first, then fall back to whatever this bot's own
    URL patterns captured (/app/<id> on diskwala.com) — on this family of
    sites the path segment is the surl either way."""
    m = _SURL_RE.search(diskwala_url)
    if m:
        return m.group(1)
    return extract_diskwala_id(diskwala_url)


def _baidu_pcs_session_and_files(diskwala_url: str) -> dict:
    """Steps 1+2 of the Baidu PCS flow (guest session + share's file
    list), shared by both the single-file and playlist/creator resolvers
    so the shorturlinfo/share-list dance only has to be written once.
    Returns {"session", "base_origin", "surl", "referer", "shareid", "uk",
    "bdstoken", "files"}. Raises DiskwalaError on any failure. Sync —
    call via asyncio.to_thread."""
    from urllib.parse import urlparse
    parsed = urlparse(diskwala_url)
    domain = parsed.netloc.lower().split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    base_origin = f"{parsed.scheme}://{parsed.netloc}"
    # The actual page the user's link points at — used as Referer on every
    # request below instead of an assumed "/s/<surl>" path, which is wrong
    # for diskwala.com/diskwala.app links (real path is /app|creator|
    # playlist/<id>) and only happens to be right for flezen.com's own
    # /s/<id> links. A real browser's Referer always matches the page it
    # was actually on, so matching that here is both more correct and
    # less fingerprintable as a script if the site ever checks Referer.
    page_referer = diskwala_url

    surl = _extract_surl(diskwala_url)
    if not surl:
        raise DiskwalaError("Couldn't find a share code in that link.")

    session = requests.Session()
    session.headers.update({"User-Agent": _BAIDU_UA, "Accept": "application/json"})

    # An admin-uploaded cookie (via /setcookies) is optional here — the
    # site hands out a working guest session on the shorturlinfo call
    # below regardless — but if one exists, loading it first can unlock a
    # logged-in account's higher speed tier instead of the guest one.
    cookies_path = get_cookies_for_url(f"https://{domain}/")
    if cookies_path:
        try:
            with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
                for name, value in parse_cookies(f.read()).items():
                    session.cookies.set(name, value, domain=domain)
        except Exception as e:
            logger.warning(f"[diskwala] Couldn't load auto-detected cookies for {domain}: {e}")

    # Step 1: share metadata — this response's Set-Cookie headers are what
    # give us a working guest session; `requests.Session` stores them
    # automatically, every call below reuses the same session.
    try:
        meta_resp = session.get(
            f"{base_origin}/api/shorturlinfo",
            params={"shorturl": surl, "root": "1"},
            headers={"Referer": page_referer},
            timeout=20,
        )
        meta = meta_resp.json()
    except Exception as e:
        raise DiskwalaError(f"shorturlinfo request failed: {e}") from e

    errno = meta.get("errno")
    if errno:
        if errno == -12:
            raise DiskwalaError("Share is password protected (not supported yet).")
        if errno == 1:
            raise DiskwalaError("Share expired or not found.")
        raise DiskwalaError(meta.get("errmsg") or f"shorturlinfo error {errno}")

    shareid, uk, bdstoken = meta.get("shareid"), meta.get("uk"), meta.get("bdstoken")
    if not (shareid and uk and bdstoken):
        raise DiskwalaError("shorturlinfo response is missing shareid/uk/bdstoken.")

    # Step 2: file list for this share. Playlist/creator links can have
    # many files, so this deliberately isn't truncated beyond Baidu's own
    # per-request cap (num=100) — a share with more than that would need
    # its own pagination via the `page` param, not implemented here since
    # no playlist that large has come up yet.
    try:
        list_resp = session.get(
            f"{base_origin}/share/list",
            params={"shareid": shareid, "uk": uk, "bdstoken": bdstoken, "dir": "/", "num": 100},
            headers={"Referer": page_referer},
            timeout=20,
        )
        list_data = list_resp.json()
    except Exception as e:
        raise DiskwalaError(f"share/list request failed: {e}") from e

    if list_data.get("errno"):
        raise DiskwalaError(list_data.get("errmsg") or f"share/list error {list_data.get('errno')}")

    files = [f for f in (list_data.get("list") or []) if f.get("isdir") != 1]
    if not files:
        raise DiskwalaError("No downloadable files in this share (empty, or a folder with no files at top level).")

    return {
        "session": session, "base_origin": base_origin, "surl": surl, "referer": page_referer,
        "shareid": shareid, "uk": uk, "bdstoken": bdstoken, "files": files,
    }


def _baidu_pcs_resolve_dlink(entry: dict, file: dict) -> str:
    """Step 3 — signed, full-speed (type=nolimit bypasses the free-tier
    cap) download link for one file out of an already-listed share. Sync
    — call via asyncio.to_thread. Raises DiskwalaError on failure."""
    session, base_origin = entry["session"], entry["base_origin"]
    referer = entry.get("referer") or f"{base_origin}/s/{entry['surl']}"
    try:
        dl_resp = session.post(
            f"{base_origin}/api/download",
            data={
                "shareid": entry["shareid"], "uk": entry["uk"], "fs_id": file.get("fs_id"),
                "sign": file.get("sign"), "timestamp": int(time.time()),
                "bdstoken": entry["bdstoken"], "primaryid": entry["uk"], "type": "nolimit",
            },
            headers={"Referer": referer},
            timeout=20,
        )
        dl_data = dl_resp.json()
    except Exception as e:
        raise DiskwalaError(f"api/download request failed: {e}") from e

    if dl_data.get("errno"):
        raise DiskwalaError(dl_data.get("errmsg") or f"api/download error {dl_data.get('errno')}")

    dlink = dl_data.get("dlink") or (dl_data.get("list") or [{}])[0].get("dlink")
    if not dlink:
        raise DiskwalaError("api/download response had no dlink.")
    return dlink


def get_diskwala_info_baidu_pcs(diskwala_url: str) -> dict:
    """Resolves via the real Baidu PCS API the site is built on. Returns
    the same {"filename", "size", "download_url"} shape as the other
    resolvers. Only ever resolves the first file in the share — for a
    playlist/creator link with multiple files, use
    get_diskwala_playlist_baidu_pcs() (routed to automatically by
    _process_inner for those two link kinds) instead. Raises
    DiskwalaError on any failure. Sync — call via asyncio.to_thread."""
    entry = _baidu_pcs_session_and_files(diskwala_url)
    file = entry["files"][0]
    dlink = _baidu_pcs_resolve_dlink(entry, file)
    return {
        "filename": file.get("server_filename") or "diskwala_video.mp4",
        "size": int(file.get("size") or 0),
        "download_url": dlink,
    }


def get_diskwala_playlist_baidu_pcs(diskwala_url: str) -> dict:
    """Lists every file behind a /creator/ or /playlist/ share WITHOUT
    resolving a download link for each one up front (that's an extra
    api/download round trip per file — wasteful for a picker the user
    might only tap one button on). Returns {"entry": <the raw session +
    shareid/uk/bdstoken dict, needed later to actually resolve a pick>,
    "files": [{"name", "size_str", "size_bytes", "raw": <original Baidu
    file dict, kept for _baidu_pcs_resolve_dlink>}, ...]}. Sync — call via
    asyncio.to_thread."""
    entry = _baidu_pcs_session_and_files(diskwala_url)
    files = [
        {
            "name": f.get("server_filename") or "diskwala_video.mp4",
            "size_str": get_size(int(f.get("size") or 0)),
            "size_bytes": int(f.get("size") or 0),
            "raw": f,
        }
        for f in entry["files"]
    ]
    return {"entry": entry, "files": files}


# ── Generic HTML page-scrape (last-resort fallback) ──────────────────────
# Same idea as Akbots/goon_provider.py's guest fallback: no private proxy
# configured (or the proxy call failed) doesn't have to mean "give up" —
# try fetching the public share page directly and pull a download/stream
# link straight out of it. Best-effort by nature (diskwala.com's page
# markup isn't a documented API), so it's tried in addition to, not
# instead of, the proxy above; the proxy stays the primary path since
# it's a known-working structured API when configured.

_GUEST_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _decode_response(response: "requests.Response") -> str:
    """gzip/deflate/br aware body decode — some hosts return
    Content-Encoding headers `requests` doesn't always auto-unwrap
    correctly for JSON/HTML bodies. Ported from goon_provider.py."""
    try:
        content_encoding = response.headers.get("Content-Encoding", "")
        if "gzip" in content_encoding:
            try:
                return gzip.decompress(response.content).decode("utf-8", errors="ignore")
            except Exception:
                pass
        if "deflate" in content_encoding:
            try:
                return zlib.decompress(response.content).decode("utf-8", errors="ignore")
            except Exception:
                try:
                    return zlib.decompress(response.content, -zlib.MAX_WBITS).decode("utf-8", errors="ignore")
                except Exception:
                    pass
        if "br" in content_encoding:
            try:
                import brotli
                return brotli.decompress(response.content).decode("utf-8", errors="ignore")
            except ImportError:
                logger.warning("[diskwala] brotli not installed, skipping...")
            except Exception:
                pass
        return response.text
    except Exception as e:
        logger.error(f"[diskwala] Decoding error: {e}")
        return response.text if response.text else str(response.content)


def _guest_session(diskwala_url: str) -> tuple[requests.Session, str]:
    """Plain browser-like session, with cookies auto-detected from the
    generic /setcookies store for whichever domain this particular link
    is on (diskwala.com or the flezen.com mirror) — same lookup
    goon_provider.py uses for its own domain."""
    from urllib.parse import urlparse
    domain = urlparse(diskwala_url).netloc.lower().split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]

    session = requests.Session()
    session.headers.update({
        "User-Agent": _GUEST_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    cookies_path = get_cookies_for_url(f"https://{domain}/")
    if cookies_path:
        try:
            with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
                cookies = parse_cookies(f.read())
            for name, value in cookies.items():
                session.cookies.set(name, value, domain=domain)
            logger.info(f"[diskwala] Guest session: loaded {len(cookies)} auto-detected cookie(s) for {domain}.")
        except Exception as e:
            logger.warning(f"[diskwala] Couldn't load auto-detected cookies for {domain}: {e}")
    return session, domain


def get_diskwala_info_guest(diskwala_url: str) -> dict:
    """No-proxy fallback: fetch the public share page directly and pull a
    download/stream link out of it via the same schema-agnostic extractor
    terabox.py's fallback chain uses. Works for both diskwala.com and the
    flezen.com mirror. Raises DiskwalaError if nothing usable is found.
    Sync — call via asyncio.to_thread."""
    session, domain = _guest_session(diskwala_url)
    try:
        resp = session.get(diskwala_url, timeout=20, headers={"Referer": f"https://{domain}/"})
    except requests.RequestException as e:
        raise DiskwalaError(f"Guest fetch failed: {e}") from e

    if resp.status_code != 200:
        raise DiskwalaError(f"Guest fetch got HTTP {resp.status_code}")

    html = _decode_response(resp)
    found = _generic_extract_stream_info(html)
    if not found:
        raise DiskwalaError(
            "Guest mode couldn't find a download link on the share page "
            "(the page may render its link via JavaScript after load, in "
            "which case only the proxy — DISKWALA_PROXY_URL/DISKWALA_API_KEY "
            "— can resolve this link)."
        )

    return {
        "filename": found.get("name") or "diskwala_video.mp4",
        "size": 0,  # not available from the page scrape, only the proxy's structured API reports it
        "download_url": found["download_link"],
    }


async def resolve_diskwala(diskwala_url: str) -> dict:
    """Public entry point used by _process_inner for single-file links.
    Tries, in order:
      1. The private proxy (if DISKWALA_PROXY_URL/DISKWALA_API_KEY set).
      2. Baidu PCS direct resolve — no login, no proxy, works standalone.
      3. Generic HTML page-scrape — last-resort safety net.
    A truly unexpected exception (not just an anticipated DiskwalaError —
    e.g. a KeyError from a malformed API response) in one tier still
    falls through to the next rather than aborting the whole chain, since
    the entire point of having 3 tiers is resilience; it's logged so the
    underlying bug is still visible. Raises DiskwalaError with every
    tier's failure reason if all fail."""
    errors = []

    if DISKWALA_PROXY_URL and DISKWALA_API_KEY:
        try:
            return await asyncio.to_thread(get_diskwala_info, diskwala_url)
        except DiskwalaError as e:
            errors.append(f"Proxy: {e}")
            logger.warning(f"[diskwala] Proxy resolve failed, trying Baidu PCS direct: {e}")
        except Exception as e:
            errors.append(f"Proxy: unexpected error ({e})")
            logger.exception(f"[diskwala] Proxy resolve hit an unexpected error, trying Baidu PCS direct")

    try:
        return await asyncio.to_thread(get_diskwala_info_baidu_pcs, diskwala_url)
    except DiskwalaError as e:
        errors.append(f"Baidu PCS: {e}")
        logger.warning(f"[diskwala] Baidu PCS direct resolve failed, trying HTML guest-scrape: {e}")
    except Exception as e:
        errors.append(f"Baidu PCS: unexpected error ({e})")
        logger.exception(f"[diskwala] Baidu PCS direct resolve hit an unexpected error, trying HTML guest-scrape")

    try:
        return await asyncio.to_thread(get_diskwala_info_guest, diskwala_url)
    except DiskwalaError as e:
        errors.append(f"Guest scrape: {e}")
        raise DiskwalaError(" | ".join(errors)) from e
    except Exception as e:
        errors.append(f"Guest scrape: unexpected error ({e})")
        logger.exception(f"[diskwala] Guest scrape hit an unexpected error")
        raise DiskwalaError(" | ".join(errors)) from e


# ── Playlist/creator picker (multi-file shares) ──────────────────────────
# Mirrors terabox.py's own folder-picker (_TERABOX_FOLDER_CACHE +
# teraf:/terapage:/teraall: callbacks) — same pagination size, same
# button layout, same make_button/to_small_caps helpers (imported from
# terabox.py above rather than duplicated). Kept as diskwala's own cache
# + callback prefixes ("dwf:"/"dwpage:"/"dwall:") since the cached entry
# shape is different (a Baidu PCS session + shareid/uk/bdstoken, not a
# resolved-URL list) — resolving each file's actual dlink is deferred
# until the user actually picks it, not done up front for every file.

_DW_FOLDER_CACHE: dict[str, dict] = {}
_DW_CACHE_TTL = 15 * 60  # 15 minutes
_DW_PAGE_SIZE = 40


def _cache_dw_playlist(playlist: dict, url: str) -> str:
    token = uuid.uuid4().hex[:10]
    _DW_FOLDER_CACHE[token] = {
        "entry": playlist["entry"], "files": playlist["files"], "url": url, "ts": time.time(),
    }
    return token


def _cleanup_dw_cache():
    now = time.time()
    for key in [k for k, v in _DW_FOLDER_CACHE.items() if now - v["ts"] > _DW_CACHE_TTL]:
        _DW_FOLDER_CACHE.pop(key, None)


def _render_dw_playlist_page(files: list[dict], token: str, page: int):
    total = len(files)
    total_pages = max(1, (total + _DW_PAGE_SIZE - 1) // _DW_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _DW_PAGE_SIZE
    page_files = files[start:start + _DW_PAGE_SIZE]

    buttons = []
    for offset, f in enumerate(page_files):
        idx = start + offset
        label = f"{idx + 1}. {to_small_caps(f['name'][:35])} ({f['size_str']})"
        buttons.append([make_button(label, callback_data=f"dwf:{token}:{idx}",
                                     style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)])

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(make_button("⬅️ ᴘʀᴇᴠ", callback_data=f"dwpage:{token}:{page - 1}",
                                        style=ButtonStyle.SECONDARY if BUTTON_STYLE_SUPPORTED else None))
        nav_row.append(make_button(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(make_button("ɴᴇxᴛ ➡️", callback_data=f"dwpage:{token}:{page + 1}",
                                        style=ButtonStyle.SECONDARY if BUTTON_STYLE_SUPPORTED else None))
        buttons.append(nav_row)

    buttons.append([make_button("📥 ᴅᴏᴡɴʟᴏᴀᴅ ᴀʟʟ", callback_data=f"dwall:{token}",
                                 style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)])
    buttons.append([make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data="cancel_upload",
                                 style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)])

    page_note = f" — ᴘᴀɢᴇ {page + 1}/{total_pages}" if total_pages > 1 else ""
    text = (
        f"📂 **ᴘʟᴀʏʟɪsᴛ ʟɪɴᴋ ᴅᴇᴛᴇᴄᴛᴇᴅ — {total} ғɪʟᴇs ғᴏᴜɴᴅ{page_note}.**\n"
        "ᴛᴀᴘ ᴀ ғɪʟᴇ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ɪᴛ, ᴏʀ ᴅᴏᴡɴʟᴏᴀᴅ ᴀʟʟ ᴛᴏ ɢʀᴀʙ ᴇᴠᴇʀʏᴛʜɪɴɢ:"
    )
    return text, buttons


async def _dw_send_file(client: Client, status_message, url: str, entry: dict, file_meta: dict, user_id: int = 0):
    """Resolves one playlist file's actual dlink (deferred until now,
    see module docstring above) and hands it to the shared
    download+upload+cache pipeline, same as the single-file flow."""
    await safe_edit(status_message.edit_text, f"<b>🔍 Resolving <code>{to_small_caps(file_meta['name'])}</code>...</b>",
                     parse_mode=enums.ParseMode.HTML)
    try:
        dlink = await asyncio.to_thread(_baidu_pcs_resolve_dlink, entry, file_meta["raw"])
    except DiskwalaError as e:
        return await safe_edit(status_message.edit_text, f"<b>{E_CROSS} Failed to resolve file:</b> <code>{e}</code>",
                                parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        # Last step in the playlist flow — no further fallback to fall
        # through to, so at minimum surface a real error instead of
        # leaving status_message stuck on "Resolving..." forever.
        logger.exception(f"[diskwala] Unexpected error resolving playlist file {file_meta.get('name')!r}")
        return await safe_edit(status_message.edit_text, f"<b>{E_CROSS} Failed to resolve file:</b> <code>{e}</code>",
                                parse_mode=enums.ParseMode.HTML)

    from Akbots.urluploader import _handle
    # cache_key includes the file index/name — the playlist URL alone
    # isn't unique per file, and using it as-is would make every file in
    # the playlist collide on the same cache entry.
    cache_key = f"{url}#{file_meta['name']}"
    caption_builder = _make_dw_caption_builder(file_meta["name"], file_meta["size_str"], url, user_id)
    # _handle posts its own status message via message.reply_text(...), so
    # status_message is only used to get the chat/reply context from —
    # left as-is (not deleted) rather than risk Pyrogram failing to quote
    # a since-deleted message when _handle replies to it.
    await _handle(client, status_message, dlink, custom_name=file_meta["name"], cache_key=cache_key,
                  caption_builder=caption_builder)


@Client.on_callback_query(filters.regex(r"^dwpage:"))
async def dw_page_nav(client, callback_query):
    try:
        _, token, page_str = callback_query.data.split(":", 2)
        page = int(page_str)
    except (ValueError, IndexError):
        await callback_query.answer("ɪɴᴠᴀʟɪᴅ ᴘᴀɢᴇ.", show_alert=True)
        return

    entry = _DW_FOLDER_CACHE.get(token)
    if not entry:
        await callback_query.answer("⌛ ᴛʜɪs sᴇʟᴇᴄᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ — ᴘʟᴇᴀsᴇ ʀᴇsᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ.", show_alert=True)
        return

    await callback_query.answer()
    text, buttons = _render_dw_playlist_page(entry["files"], token, page)
    await safe_edit(callback_query.message.edit, text, reply_markup=InlineKeyboardMarkup(buttons))


@Client.on_callback_query(filters.regex(r"^dwf:"))
async def dw_file_selected(client, callback_query):
    try:
        _, token, idx_str = callback_query.data.split(":", 2)
        idx = int(idx_str)
    except (ValueError, IndexError):
        await callback_query.answer("ɪɴᴠᴀʟɪᴅ sᴇʟᴇᴄᴛɪᴏɴ.", show_alert=True)
        return

    cache_entry = _DW_FOLDER_CACHE.get(token)
    if not cache_entry or idx >= len(cache_entry["files"]):
        await callback_query.answer("⌛ ᴛʜɪs sᴇʟᴇᴄᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ — ᴘʟᴇᴀsᴇ ʀᴇsᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ.", show_alert=True)
        return

    await callback_query.answer()
    user_id = callback_query.from_user.id
    file_meta = cache_entry["files"][idx]
    # Same concurrency cap as the single-link flow (_process) — without
    # this, playlist picks bypass _dw_queue entirely and a full
    # download+upload can run with no limit alongside (or instead of)
    # the intended max_concurrent=3.
    await _dw_queue.acquire(callback_query.message.chat.id, user_id)
    try:
        await _dw_send_file(client, callback_query.message, cache_entry["url"], cache_entry["entry"], file_meta,
                             user_id=user_id)
    finally:
        _dw_queue.release()


@Client.on_callback_query(filters.regex(r"^dwall:"))
async def dw_download_all(client, callback_query):
    token = callback_query.data.split(":", 1)[1]
    cache_entry = _DW_FOLDER_CACHE.get(token)
    if not cache_entry:
        await callback_query.answer("⌛ ᴛʜɪs sᴇʟᴇᴄᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ — ᴘʟᴇᴀsᴇ ʀᴇsᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ.", show_alert=True)
        return

    await callback_query.answer()
    files = cache_entry["files"]
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    await safe_edit(callback_query.message.edit, f"📥 **ǫᴜᴇᴜɪɴɢ {len(files)} ғɪʟᴇs ғʀᴏᴍ ᴛʜᴇ ᴘʟᴀʏʟɪsᴛ...**")

    for i, file_meta in enumerate(files, start=1):
        status_msg = await callback_query.message.reply(
            f"🔍 [{i}/{len(files)}] ᴘʀᴇᴘᴀʀɪɴɢ **{to_small_caps(file_meta['name'])}**..."
        )
        # Same concurrency cap as the single-link flow (_process) — one
        # slot per file, acquired/released around each individual
        # download+upload so "download all" on a big playlist can't run
        # unbounded in parallel with (or instead of) the intended
        # max_concurrent=3 for this bot's Diskwala downloads.
        await _dw_queue.acquire(chat_id, user_id)
        try:
            await _dw_send_file(client, status_msg, cache_entry["url"], cache_entry["entry"], file_meta,
                                 user_id=user_id)
        finally:
            _dw_queue.release()

    try:
        await callback_query.message.delete()
    except Exception:
        pass


def _fmt_dw_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d} sec"


def _make_dw_caption_builder(name: str, size_str: str, source_url: str, user_id: int):
    """Same shape/wording as terabox.py's own build_caption() (file name,
    size, quality, downloaded-in/uploaded-in duration, uploaded-by, source
    link, powered-by credit) — DiskWala has no per-file quality info (Baidu
    PCS's share/list only gives name+size), so that line is a fixed label
    instead of a resolved value. Passed to urluploader._handle's
    caption_builder param, which calls it once after download (elapsed
    only) and again after upload (both elapsed values) exactly like
    terabox.py's own two-pass caption does."""
    def build(download_elapsed: float, upload_elapsed: float | None = None) -> str:
        lines = [
            "<blockquote>",
            f"📄 **ғɪʟᴇ ɴᴀᴍᴇ:** `{to_small_caps(name)}`\n",
            f"📦 **sɪᴢᴇ:** {size_str}\n",
            f"🎞️ **ǫᴜᴀʟɪᴛʏ:** ᴏʀɪɢɪɴᴀʟ\n",
            f"⬇️ **ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ ɪɴ:** {_fmt_dw_dur(download_elapsed)}\n",
        ]
        if upload_elapsed is not None:
            lines.append(f"⬆️ **ᴜᴘʟᴏᴀᴅᴇᴅ ɪɴ:** {_fmt_dw_dur(upload_elapsed)}\n")
        lines += [
            f"🙋 **ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ:** `{user_id}`\n",
            f"🔗 **sᴏᴜʀᴄᴇ:** [ᴅɪsᴋᴡᴀʟᴀ ʟɪɴᴋ]({source_url})\n\n",
            f"⚡ ᴘᴏᴡᴇʀᴇᴅ ʙʏ [ᴀɴᴜᴊ ᴋᴜᴍᴀʀ](https://t.me/anujedits76)",
            "</blockquote>",
        ]
        return "".join(lines)
    return build


async def _process(client: Client, message: Message, diskwala_url: str):
    user_id = message.from_user.id if message.from_user else 0
    await _dw_queue.acquire(message.chat.id, user_id)
    try:
        await _process_inner(client, message, diskwala_url)
    finally:
        _dw_queue.release()


async def _process_inner(client: Client, message: Message, diskwala_url: str):
    # Phase 1 (matches upstream's _dw_helper ordering): check our own cache
    # BEFORE spending a call against the external Diskwala proxy — no point
    # re-resolving+re-downloading+re-uploading a video we already have.
    status = await message.reply_text(f"<b>🔍 Checking cache...</b>", parse_mode=enums.ParseMode.HTML)
    from Akbots.link_cache import try_send_cached
    if await try_send_cached(client, message, diskwala_url, status):
        return

    # Phase 1.5: /creator/ and /playlist/ links are multi-file shares —
    # try the Baidu PCS listing first and, if it comes back with more than
    # one file, show the picker instead of silently only grabbing the
    # first one. If this tier fails outright (e.g. the share needs a
    # password), fall through to the normal single-file resolve chain
    # below same as any other link — the guest HTML scrape can still find
    # *a* playable link on the page even if the structured API can't.
    if _MULTI_FILE_PATH_RE.search(diskwala_url):
        await safe_edit(status.edit_text, f"<b>🔍 Listing playlist...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            playlist = await asyncio.to_thread(get_diskwala_playlist_baidu_pcs, diskwala_url)
        except DiskwalaError as e:
            logger.warning(f"[diskwala] Playlist listing failed for {diskwala_url}, falling back to single-file resolve: {e}")
        except Exception as e:
            # Same "don't let an unexpected bug break the intended
            # fallback" reasoning as resolve_diskwala's own tiers above —
            # fall through to Phase 2's full resolve chain instead of
            # leaving `status` stuck on "Listing playlist..." forever.
            logger.exception(f"[diskwala] Playlist listing hit an unexpected error for {diskwala_url}, falling back to single-file resolve")
        else:
            if len(playlist["files"]) > 1:
                _cleanup_dw_cache()
                token = _cache_dw_playlist(playlist, diskwala_url)
                text, buttons = _render_dw_playlist_page(playlist["files"], token, 0)
                await safe_edit(status.edit_text, text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
                return
            # Exactly one file — no point showing a picker for it. The
            # listing call above (shorturlinfo + share/list) already did
            # the expensive part of resolving this link, so finish it off
            # with just the remaining api/download call instead of
            # discarding that work and re-running the *entire* resolve
            # chain (proxy -> Baidu PCS listing again -> guest scrape)
            # from scratch via Phase 2 below.
            single_file = playlist["files"][0]
            await safe_edit(status.edit_text, f"<b>🔍 Resolving Diskwala link...</b>", parse_mode=enums.ParseMode.HTML)
            try:
                dlink = await asyncio.to_thread(_baidu_pcs_resolve_dlink, playlist["entry"], single_file["raw"])
            except DiskwalaError as e:
                return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to get video info:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
            except Exception as e:
                logger.exception(f"Unexpected Diskwala error resolving single-file playlist for {diskwala_url}")
                return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to get video info:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
            await status.delete()
            from Akbots.urluploader import _handle
            user_id = message.from_user.id if message.from_user else 0
            caption_builder = _make_dw_caption_builder(
                single_file["name"], single_file["size_str"], diskwala_url, user_id
            )
            await _handle(client, message, dlink, custom_name=single_file["name"],
                          cache_key=diskwala_url, caption_builder=caption_builder)
            return

    # Phase 2: not cached (and not a multi-file share, or a playlist
    # listing that failed outright above and needs the full fallback
    # chain) — resolve via the proxy (if configured), falling back to
    # guest mode (direct page scrape, no proxy needed) if that fails or
    # isn't configured at all.
    await safe_edit(status.edit_text, f"<b>🔍 Resolving Diskwala link...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        info = await resolve_diskwala(diskwala_url)
    except DiskwalaError as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to get video info:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.exception(f"Unexpected Diskwala error for {diskwala_url}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to get video info:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await status.delete()
    # Hand off to this bot's own resumable download+upload+cache pipeline
    # (Akbots/urluploader.py) with the resolved direct URL — no need to
    # reimplement download/upload/progress/caching here. cache_key is the
    # stable diskwala_url (not the temporary signed download_url) and
    # skip_cache is already False for us (see _handle's cache_key param),
    # so this stores under the same key Phase 1 just checked.
    from Akbots.urluploader import _handle
    user_id = message.from_user.id if message.from_user else 0
    caption_builder = _make_dw_caption_builder(
        info["filename"], get_size(int(info.get("size") or 0)), diskwala_url, user_id
    )
    await _handle(client, message, info["download_url"], custom_name=info["filename"],
                  cache_key=diskwala_url, caption_builder=caption_builder)


@Client.on_message(filters.command(["diskwala", "dw"]) & filters.private)
async def diskwala_cmd(client: Client, message: Message):
    args = message.text.split(maxsplit=1)
    source_text = args[1] if len(args) >= 2 else (message.reply_to_message.text if message.reply_to_message else "")
    urls = extract_all_diskwala_urls(source_text)

    if not urls:
        # A nice touch ported from the upstream /dw handler: if what they
        # pasted is actually a TeraBox link, point them at the right place
        # instead of just saying "no Diskwala link found".
        try:
            from Akbots.terabox import TERABOX_REGEX
            if re.search(TERABOX_REGEX, source_text):
                return await message.reply_text(
                    f"<b>{E_INFO} That looks like a TeraBox link, not Diskwala.</b>\n"
                    f"Use <code>/terabox &lt;link&gt;</code> instead — or just paste it directly.",
                    parse_mode=enums.ParseMode.HTML
                )
        except Exception:
            pass
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/diskwala &lt;diskwala.com link&gt;</code> (or <code>/dw</code>)\n"
            f"<i>Or reply to a message containing the link(s) with</i> <code>/diskwala</code>.\n"
            f"<i>Multiple links in one message are all processed together.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    await asyncio.gather(*(_process(client, message, url) for url in urls))


@Client.on_message(
    filters.text & filters.private & filters.regex(DISKWALA_URL_RE) & ~filters.regex(r"^/"),
    group=1,  # same priority tier as other specific-host auto-detect handlers (terabox.py etc.)
)
async def diskwala_auto_detect(client: Client, message: Message):
    urls = extract_all_diskwala_urls(message.text)
    if urls:
        await asyncio.gather(*(_process(client, message, url) for url in urls))
