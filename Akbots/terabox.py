# Ported into Akbots: swapped the MN-BOTS-specific config (CHANNEL/DATABASE
# classes, a raw pymongo MongoClient that was created — but never actually
# used — at import time) for Akbots' own config.py / verify_patch.py shim,
# and re-exported TERABOX_DOMAINS since Akbots/urluploader.py and
# Akbots/ytdl.py both import it to build their own exclusion lists.
import os
import re
import tempfile
import asyncio
import time
import uuid
import mimetypes
import urllib.parse
import logging
import aiohttp
import aiofiles
from pyrogram import Client
from pyrogram import filters
from pyrogram.types import Message
from pyrogram import enums
from verify_patch import IS_VERIFY, is_verified, build_verification_link, HOW_TO_VERIFY
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from Akbots.direct_utils import safe_edit, format_progress, make_upload_progress
from Akbots.link_cache import try_send_cached, store as _cache_store

try:
    from pyrogram.enums import ButtonStyle
    BUTTON_STYLE_SUPPORTED = True
except ImportError:
    BUTTON_STYLE_SUPPORTED = False


def make_button(text, callback_data=None, url=None, style=None):
    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if BUTTON_STYLE_SUPPORTED and style is not None:
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)

try:
    from config import TERABOX_LEECH_CHANNEL
except ImportError:
    TERABOX_LEECH_CHANNEL = 0

# Single source of truth for every TeraBox / mirror domain this plugin
# handles. Akbots/urluploader.py (the generic last-resort uploader) and
# Akbots/ytdl.py both import this tuple to build their own exclusion
# lists, so those plugins never try to re-process a link this one already
# handled.
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

# Built from TERABOX_DOMAINS (above) instead of the original hand-written
# `tera...\.[a-z]+/s/...` regex, so every mirror domain Akbots already
# recognises (dubox.com, mirrobox.com, nephobox.com, etc. — none of which
# contain "tera" in the name) still gets picked up here too.
TERABOX_REGEX = (
    r"(https?://)?(www\.)?(" + "|".join(re.escape(d) for d in TERABOX_DOMAINS) + r")/\S+"
)

# Updated xAPIverse Configuration
API_BASE_URL = "https://xapiverse.com/api/terabox"
PRO_API_BASE_URL = "https://xapiverse.com/api/terabox-pro"  # returns per-quality HLS streams
API_KEY = "sk_6c79f22723800e417168d09eafa66565"

# 🎭 BROWSER SPOOFING HEADERS: Tricks the CDN into thinking the bot is Google Chrome
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1"
}

import json as _json

# Multi-connection ("parallel") ranged download — used only for large files
# where splitting the download across several concurrent HTTP Range
# requests gives a real speed win over one single aiohttp stream (CDN-served
# links are typically not bandwidth-limited per-connection, so N parallel
# connections can pull close to N times the throughput of one).
#
# Ported from the idea in Terabox_downloader_bot's downloader/parallel_downloader.py
# (Range-request chunking, per user request) but reimplemented on
# aiohttp/aiofiles - the libraries this file already depends on - instead of
# adding requests/urllib3/cloudscraper/ThreadPoolExecutor as new
# dependencies just for this. Falls back to the existing single-stream
# download for small files or whenever the server doesn't support Range
# requests (common on some Terabox CDN edge nodes) - see _process_terabox.
PARALLEL_DOWNLOAD_MIN_SIZE = 50 * 1024 * 1024  # below this, one stream is plenty
PARALLEL_DOWNLOAD_CHUNKS = 6


async def _download_parallel(url: str, dest_path: str, size_hint: int, headers: dict,
                              num_chunks: int, on_progress=None) -> bool:
    """Attempts a multi-connection Range-request download of `url` into
    `dest_path`. Returns True on full success, False if anything about this
    approach doesn't pan out (no Range support, a chunk request fails,
    unknown size, ...) - callers should fall back to a normal single-stream
    download in that case, same as if this function had never been tried.
    Always leaves dest_path either fully correct or removed - never a
    partial/corrupt file for the caller to trip over."""
    try:
        # Probe Range support and get an authoritative size from the server
        # itself rather than trusting size_hint (which comes from the
        # extractor API and can be stale/wrong) - a wrong total_size would
        # otherwise corrupt the byte-range math below.
        async with aiohttp.ClientSession() as probe:
            probe_headers = {**headers, "Range": "bytes=0-0"}
            async with probe.get(url, headers=probe_headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 206:
                    return False  # server ignored the Range header - no parallel support
                content_range = r.headers.get("Content-Range", "")
                total_size = int(content_range.rsplit("/", 1)[-1]) if "/" in content_range else 0

        if total_size <= 0:
            total_size = size_hint
        if total_size <= 0:
            return False

        # Pre-allocate the full-size file so each chunk task can seek to
        # its own byte offset and write independently - safe because the
        # ranges never overlap, each task opens its own file descriptor.
        with open(dest_path, "wb") as f:
            f.truncate(total_size)

        chunk_size = -(-total_size // num_chunks)  # ceil division
        ranges = []
        start = 0
        while start < total_size:
            end = min(start + chunk_size - 1, total_size - 1)
            ranges.append((start, end))
            start = end + 1

        downloaded_total = 0
        progress_lock = asyncio.Lock()

        async def fetch_range(session, start, end):
            nonlocal downloaded_total
            range_headers = {**headers, "Range": f"bytes={start}-{end}"}
            async with session.get(url, headers=range_headers, timeout=aiohttp.ClientTimeout(total=None)) as resp:
                if resp.status not in (200, 206):
                    raise RuntimeError(f"chunk {start}-{end} failed: HTTP {resp.status}")
                async with aiofiles.open(dest_path, "r+b") as f:
                    await f.seek(start)
                    async for data in resp.content.iter_chunked(1024 * 1024):
                        if not data:
                            continue
                        await f.write(data)
                        async with progress_lock:
                            downloaded_total += len(data)
                            current = downloaded_total
                        if on_progress:
                            await on_progress(current, total_size)

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(*(fetch_range(session, s, e) for s, e in ranges))

        return True
    except Exception:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return False


def _generic_extract_stream_info(raw_text: str) -> dict | None:
    """Schema-agnostic last-resort extractor — tries every response shape
    seen across TeraBox third-party APIs before giving up entirely. Used as
    a fallback inside _get_folder_info_xapiverse (and available to any
    other resolver) when the endpoint's usual JSON schema doesn't match,
    e.g. after an API silently changes its field names.

    Order: JSON dict, common top-level url-ish keys -> same keys nested one
    level under 'data'/'result' -> any string value in the dict that looks
    like a URL -> raw-text regex scan for an .m3u8/.mp4 URL (handles both
    plain and JSON-escaped strings).
    """
    url_keys = ("url", "stream_url", "play_url", "video_url", "normal_dlink", "dlink")
    name_keys = ("filename", "title", "name", "file_name")

    data = None
    try:
        data = _json.loads(raw_text)
    except Exception:
        data = None

    if isinstance(data, dict):
        stream_url, filename = None, None
        for key in url_keys:
            if data.get(key):
                stream_url = data[key]
                break
        nested = data.get("data") if isinstance(data.get("data"), dict) else (
            data.get("result") if isinstance(data.get("result"), dict) else None)
        if not stream_url and nested:
            for key in url_keys:
                if nested.get(key):
                    stream_url = nested[key]
                    break
        if not stream_url:
            for value in data.values():
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    stream_url = value
                    break
        for key in name_keys:
            if data.get(key):
                filename = data[key]
                break
        if stream_url:
            return {"download_link": stream_url, "name": filename or "download"}

    # Not JSON, or JSON had nothing usable — scan the raw text for a
    # playable URL directly (handles HTML pages / JS blobs with an embedded
    # link, and JSON-escaped "\/" style strings).
    text = (raw_text or "").replace("\\/", "/")
    for pattern in (
        r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
    ):
        m = re.search(pattern, text)
        if m:
            return {"download_link": m.group(1), "name": "download"}

    return None


async def _get_folder_info_xapiverse(share_url: str, password: str | None = None) -> list[dict]:
    """
    Fetch file information from the xAPIverse API and normalise every entry
    in the response's "list" array — not just the first one. TeraBox share
    links can point at a single file OR a whole folder; the API already
    returns every file in the folder here, this just stops throwing the
    rest away, so folder links can be handled (file picker) instead of
    silently grabbing only the first file.
    """
    try:
        payload = {"url": share_url}
        if password:
            # Best-effort: some xAPIverse-style wrappers accept a password
            # field for extraction-code-protected shares. Harmless no-op if
            # this particular deployment ignores unknown fields.
            payload["pwd"] = password
            payload["password"] = password

        headers = {
            "Content-Type": "application/json",
            "xAPIverse-Key": API_KEY
        }

        async with aiohttp.ClientSession() as session:
            raw_text = None
            for attempt in range(2):
                async with session.post(API_BASE_URL, json=payload, headers=headers, timeout=30) as response:
                    raw_text = await response.text()
                    # Cloudflare/anti-bot interstitial instead of the real
                    # API response — one short retry before giving up, same
                    # anti-bot handling terabox.beer's resolver already does.
                    if response.status in (403, 429, 503) or "Bot Verification" in raw_text or "recaptcha" in raw_text.lower():
                        if attempt == 0:
                            await asyncio.sleep(1.5)
                            continue
                    break

            try:
                data = _json.loads(raw_text)
            except Exception:
                data = None

            # ✅ Targets the "list" array inside the API response schema
            if isinstance(data, dict) and data.get("status") == "success" and "list" in data and len(data["list"]) > 0:
                files = []
                for file_info in data["list"]:
                    # Target 'normal_dlink' explicitly as requested
                    download_link = file_info.get("normal_dlink", "")
                    if not download_link:
                        continue
                    files.append({
                        "name": file_info.get("name", "download"),
                        "download_link": download_link,
                        "size_str": file_info.get("size_formatted", "Unknown"),
                        "size_bytes": file_info.get("size", 0),
                        "thumb": file_info.get("thumbnail", ""),
                        "stream_link": file_info.get("stream_url", ""),
                        "qualities": {},  # this endpoint doesn't return per-quality streams
                    })
                if files:
                    return files

            # Strict schema didn't match (API changed field names, or this
            # deployment returns a different shape) — fall back to a
            # schema-agnostic scan of the same response before giving up.
            generic = _generic_extract_stream_info(raw_text or "")
            if generic:
                return [{
                    "name": generic["name"],
                    "download_link": generic["download_link"],
                    "size_str": "Unknown",
                    "size_bytes": 0,
                    "thumb": "",
                    "stream_link": "",
                    "qualities": {},
                }]

            raise ValueError("Invalid API response or missing normal_dlink")

    except aiohttp.ClientError as e:
        raise ValueError(f"API request failed: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error parsing API response: {str(e)}")


async def _get_file_info_xapiverse(share_url: str) -> dict:
    """Back-compat single-file wrapper — still used by /terastream."""
    return (await _get_folder_info_xapiverse(share_url))[0]


async def _get_folder_info_xapiverse_pro(share_url: str, password: str | None = None) -> list[dict]:
    """
    Same as _get_folder_info_xapiverse but hits the Pro tier
    (`/api/terabox-pro`), which additionally returns a `fast_stream_url`
    map of `{"360p": "...m3u8", "720p": "...m3u8", ...}` per video file —
    this is what powers the quality-selection menu. Requires a Pro-tier
    API key; if the key isn't provisioned for Pro, xAPIverse will error
    and the caller falls back to the plain endpoint (single quality).
    """
    try:
        payload = {"url": share_url}
        if password:
            payload["pwd"] = password
            payload["password"] = password

        headers = {
            "Content-Type": "application/json",
            "xAPIverse-Key": API_KEY
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(PRO_API_BASE_URL, json=payload, headers=headers, timeout=30) as response:
                response.raise_for_status()
                data = await response.json()

                if data.get("status") == "success" and "list" in data and len(data["list"]) > 0:
                    files = []
                    for file_info in data["list"]:
                        download_link = file_info.get("fast_dlink") or file_info.get("normal_dlink", "")
                        qualities = file_info.get("fast_stream_url") or {}
                        if not download_link and not qualities:
                            continue
                        files.append({
                            "name": file_info.get("name", "download"),
                            "download_link": download_link or next(iter(qualities.values()), ""),
                            "size_str": file_info.get("size_formatted", "Unknown"),
                            "size_bytes": file_info.get("size", 0),
                            "thumb": file_info.get("thumbnail", ""),
                            "stream_link": file_info.get("stream_url", ""),
                            "qualities": qualities,  # {"360p": m3u8_url, "720p": m3u8_url, ...}
                        })
                    if files:
                        return files

                raise ValueError("Invalid Pro API response")

    except aiohttp.ClientError as e:
        raise ValueError(f"Pro API request failed: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error parsing Pro API response: {str(e)}")


# =========================================================
# Fallback resolver — terabox.beer (no API key needed)
# =========================================================
# xAPIverse (above) is the primary resolver, but it's a paid third-party
# API keyed to one hardcoded key — if that key gets rate-limited, revoked,
# or the service goes down, every TeraBox link fails with no recourse.
# This is a second, independent resolver (ported from a standalone script)
# that walks terabox.beer's own watch-page + API + redirect chain instead,
# so /terabox and /terastream keep working even if xAPIverse doesn't.
import requests as _requests
import urllib3 as _urllib3
_urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)

_BEER_BASE_URL = "https://terabox.beer"
_BEER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-MM,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="137", "Not/A)Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?1",
    "Sec-Ch-Ua-Platform": '"Android"',
}


def _beer_extract_video_id(url: str):
    for pattern in (r"/s/([a-zA-Z0-9_-]+)", r"share\.com/s/([a-zA-Z0-9_-]+)", r"file\.com/s/([a-zA-Z0-9_-]+)"):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _beer_extract_m3u8_url(text: str):
    for pattern in (
        r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/playlist\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.m3u8\?[^\s"\'<>]*)',
    ):
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def _beer_follow_redirects(session, url: str, max_redirects: int = 5) -> dict:
    current_url = url
    redirect_count = 0
    while redirect_count < max_redirects:
        try:
            response = session.get(
                current_url, headers=_BEER_HEADERS | {"Referer": _BEER_BASE_URL + "/"},
                allow_redirects=False, timeout=30, verify=False,
            )
        except Exception:
            return {"final_url": current_url, "m3u8_url": None}
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if location:
                if location.startswith("/"):
                    parsed = urllib.parse.urlparse(current_url)
                    location = f"{parsed.scheme}://{parsed.netloc}{location}"
                elif not location.startswith("http"):
                    parsed = urllib.parse.urlparse(current_url)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    if not location.startswith("/"):
                        base += "/" + "/".join(parsed.path.split("/")[:-1])
                    location = base + "/" + location.lstrip("/")
                current_url = location
                redirect_count += 1
                continue
        m3u8_url = _beer_extract_m3u8_url(response.text) if response.text else None
        return {"final_url": current_url, "m3u8_url": m3u8_url}
    return {"final_url": current_url, "m3u8_url": None}


def _beer_resolve_sync(share_url: str) -> dict:
    """Blocking (requests-based) resolve — always called via asyncio.to_thread,
    never directly, so it can't stall the bot's event loop."""
    video_id = _beer_extract_video_id(share_url)
    if not video_id:
        raise ValueError("Could not extract video ID from the link")

    session = _requests.Session()
    session.verify = False

    # Warm the session + trigger cookie/anti-bot setup, same 2-hop sequence
    # (home page, then the actual watch page) the site itself does.
    session.get(_BEER_BASE_URL, headers=_BEER_HEADERS | {"Referer": "https://www.google.com/"}, timeout=30, verify=False)
    watch_url = f"{_BEER_BASE_URL}/watch/{video_id}"
    session.get(watch_url, headers=_BEER_HEADERS | {"Referer": _BEER_BASE_URL + "/"}, timeout=30, verify=False)

    encoded_url = urllib.parse.quote(share_url, safe="")
    api_url = f"{_BEER_BASE_URL}/api/terabox-new?link={encoded_url}"
    response = session.get(api_url, headers=_BEER_HEADERS | {"Referer": watch_url}, timeout=30, verify=False)

    try:
        api_result = response.json()
    except Exception:
        # Not JSON at all (HTML error page, plain text, etc.) — try the
        # schema-agnostic scan directly on the raw body before giving up.
        generic = _generic_extract_stream_info(response.text or "")
        if generic:
            return {
                "name": generic["name"], "download_link": generic["download_link"],
                "size_str": "Unknown", "size_bytes": 0, "thumb": "", "stream_link": "", "qualities": {},
            }
        raise ValueError("Failed to parse terabox.beer API response")

    if not isinstance(api_result, dict) or api_result.get("error") is not False:
        error_msg = (isinstance(api_result, dict) and (api_result.get("error") or api_result.get("message"))) or "Unknown error"
        raise ValueError(f"terabox.beer API request failed: {error_msg}")

    video_url = None
    for field in ("stream_download_url", "download_link", "fallback_url", "proxy_url", "url", "video_url"):
        if api_result.get(field):
            video_url = api_result[field]
            break
    if not video_url:
        for value in api_result.values():
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                video_url = value
                break
    if not video_url:
        # Last resort — same schema-agnostic scan, run against the raw
        # response body (catches m3u8/mp4 URLs buried in a field shape the
        # checks above don't recognise).
        generic = _generic_extract_stream_info(response.text or "")
        if generic:
            video_url = generic["download_link"]
    if not video_url:
        raise ValueError("No video URL found in terabox.beer API response")

    file_name = api_result.get("file_name", "download")
    file_size = api_result.get("file_size", "Unknown")

    redirect_result = _beer_follow_redirects(session, video_url)
    final_url = redirect_result["m3u8_url"] or video_url

    return {
        "name": file_name,
        "download_link": final_url,
        "size_str": file_size if isinstance(file_size, str) else str(file_size),
        "size_bytes": file_size if isinstance(file_size, int) else 0,
        "thumb": "",
        "stream_link": redirect_result["m3u8_url"] or "",
        "qualities": {},
    }


async def _get_file_info_beer(share_url: str) -> dict:
    try:
        return await asyncio.to_thread(_beer_resolve_sync, share_url)
    except Exception as e:
        raise ValueError(f"terabox.beer fallback failed: {str(e)}")


_ANSH_API_BASE = "https://terabox.anshapi.workers.dev/api/terabox-down"


async def _get_file_info_ansh(share_url: str) -> dict:
    """Fallback resolver — anshapi.workers.dev's free Cloudflare Worker
    (no API key needed, same tier as terabox.beer). Schema isn't
    documented anywhere, so this tries the field names seen across similar
    TeraBox worker APIs first, then falls back to the schema-agnostic
    scanner used elsewhere in this file for anything it doesn't recognise."""
    api_url = f"{_ANSH_API_BASE}?url={urllib.parse.quote(share_url, safe='')}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                raw_text = await resp.text()
    except Exception as e:
        raise ValueError(f"anshapi fallback request failed: {e}")

    try:
        data = _json.loads(raw_text)
    except Exception:
        data = None

    def _pick(d: dict) -> dict | None:
        link = None
        for key in ("download_link", "download_url", "direct_link", "url", "dlink"):
            if d.get(key):
                link = d[key]
                break
        if not link:
            return None
        name = None
        for key in ("file_name", "filename", "name", "title"):
            if d.get(key):
                name = d[key]
                break
        size_str = None
        size_bytes = 0
        for key in ("file_size", "size"):
            if d.get(key):
                size_str = str(d[key])
                break
        if isinstance(d.get("size_bytes"), int):
            size_bytes = d["size_bytes"]
        return {
            "name": name or "download",
            "download_link": link,
            "size_str": size_str or "Unknown",
            "size_bytes": size_bytes,
            "thumb": d.get("thumbnail") or d.get("thumb") or "",
            "stream_link": d.get("streaming_url") or d.get("stream_url") or "",
            "qualities": {},
        }

    if isinstance(data, dict):
        # Direct object, or the common {"files": [...]} / {"data": {...}} wrappers.
        result = _pick(data)
        if not result and isinstance(data.get("files"), list) and data["files"]:
            result = _pick(data["files"][0])
        if not result and isinstance(data.get("data"), dict):
            result = _pick(data["data"])
        if result:
            return result

    generic = _generic_extract_stream_info(raw_text or "")
    if generic:
        return {
            "name": generic["name"], "download_link": generic["download_link"],
            "size_str": "Unknown", "size_bytes": 0, "thumb": "", "stream_link": "", "qualities": {},
        }

    raise ValueError("anshapi fallback: no usable download link in response")


_AZHAWASADDA_API_BASE = "https://azhawasadda.in/api/extract"


async def _get_file_info_azhawasadda(share_url: str) -> dict:
    """Fallback resolver — azhawasadda.in's free extractor API (no API key
    needed, same tier as terabox.beer/anshapi). Response schema is
    documented (captured from a live HAR): {"errno":0,"data":{"file":{
    "download_url","direct_link","stream_url","file_name","size_readable",
    "fast_stream_url":{"360p":...,"480p":...,"720p":...,"1080p":...},
    "duration","quality","thumbnail"}},"total_size"}. direct_link/
    fast_stream_url point at a Cloudflare Worker (dusuweko.workers.dev)
    that proxies the actual TeraBox file, so no further auth is needed to
    use the returned links."""
    api_url = f"{_AZHAWASADDA_API_BASE}?url={urllib.parse.quote(share_url, safe='')}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "*/*",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                raw_text = await resp.text()
    except Exception as e:
        raise ValueError(f"azhawasadda fallback request failed: {e}")

    try:
        data = _json.loads(raw_text)
    except Exception:
        raise ValueError("azhawasadda fallback: couldn't parse API response")

    if data.get("errno"):
        raise ValueError(data.get("errmsg") or f"azhawasadda fallback: errno {data.get('errno')}")

    file_info = ((data.get("data") or {}).get("file")) or {}
    download_link = file_info.get("direct_link") or file_info.get("download_url")
    if not download_link:
        raise ValueError("azhawasadda fallback: no usable download link in response")

    qualities = file_info.get("fast_stream_url") or {}
    stream_link = (
        qualities.get(file_info.get("quality"))
        or qualities.get("1080p") or qualities.get("720p")
        or qualities.get("480p") or qualities.get("360p")
        or ""
    )

    return {
        "name": file_info.get("file_name") or "download",
        "download_link": download_link,
        "size_str": file_info.get("size_readable") or data.get("total_size") or "Unknown",
        "size_bytes": 0,
        "thumb": file_info.get("thumbnail") or "",
        "stream_link": stream_link,
        "qualities": qualities,
    }


async def _get_file_info_direct(share_url: str) -> dict:
    from Akbots import terabox_lib
    if not terabox_lib.is_configured():
        raise ValueError("direct-API tier not configured (TERABOX_NDUS unset)")
    return (await terabox_lib.resolve(share_url))[0]


# =========================================================
# Baidu PCS guest resolve (no login, no proxy, no API key)
# =========================================================
# terabox.py had xAPIverse -> terabox.beer -> anshapi -> direct-API
# (needs TERABOX_NDUS) — three third-party-hosted resolvers plus one that
# needs an owned account's cookie, but nothing that talks to TeraBox's own
# backend directly. Akbots/diskwala.py already has this
# (get_diskwala_info_baidu_pcs) because DiskWala/TheDiskWala is a reskinned
# frontend over the same Baidu PCS share API that TeraBox itself runs on —
# /api/shorturlinfo hands out a guest session with no login needed, then
# /share/list + /api/download (type=nolimit, bypasses the free-tier speed
# cap) get the real file + a signed direct link. Ported here 1:1, just
# pointed at the TeraBox share URL's own domain instead of a DiskWala one,
# and reshaped into the {"name","download_link","size_str","size_bytes",
# "thumb","stream_link","qualities"} dict every other tier in this file
# returns (instead of diskwala's {"filename","size","download_url"}).
from Akbots.cookies_manager import get_cookies_for_url as _tb_get_cookies_for_url
from Akbots.cookie_utils import parse_cookies as _tb_parse_cookies

_BAIDU_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
_BAIDU_SURL_RE = re.compile(r"/s/([a-zA-Z0-9_-]+)")


def _baidu_pcs_extract_surl(share_url: str) -> str | None:
    m = _BAIDU_SURL_RE.search(share_url)
    if m:
        return m.group(1)
    return _beer_extract_video_id(share_url)


def _get_file_info_baidu_pcs_sync(share_url: str) -> dict:
    """Blocking (requests-based) — always called via asyncio.to_thread."""
    parsed = urllib.parse.urlparse(share_url)
    domain = parsed.netloc.lower().split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    base_origin = f"{parsed.scheme}://{parsed.netloc}"

    surl = _baidu_pcs_extract_surl(share_url)
    if not surl:
        raise ValueError("Baidu PCS: couldn't find a share code in that link.")

    session = _requests.Session()
    session.headers.update({"User-Agent": _BAIDU_UA, "Accept": "application/json"})

    # Optional: an admin-uploaded cookie can unlock the logged-in account's
    # higher speed tier, but /api/shorturlinfo hands out a working guest
    # session on its own regardless — so this isn't required to succeed.
    cookies_path = _tb_get_cookies_for_url(f"https://{domain}/")
    if cookies_path:
        try:
            with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
                for name, value in _tb_parse_cookies(f.read()).items():
                    session.cookies.set(name, value, domain=domain)
        except Exception as e:
            logging.warning(f"[terabox] Baidu PCS: couldn't load auto-detected cookies for {domain}: {e}")

    try:
        meta_resp = session.get(
            f"{base_origin}/api/shorturlinfo",
            params={"shorturl": surl, "root": "1"},
            headers={"Referer": f"{base_origin}/s/{surl}"},
            timeout=20, verify=False,
        )
        meta = meta_resp.json()
    except Exception as e:
        raise ValueError(f"Baidu PCS: shorturlinfo request failed: {e}") from e

    errno = meta.get("errno")
    if errno:
        if errno == -12:
            raise ValueError("Baidu PCS: share is password protected (not supported by this tier).")
        if errno == 1:
            raise ValueError("Baidu PCS: share expired or not found.")
        raise ValueError(meta.get("errmsg") or f"Baidu PCS: shorturlinfo error {errno}")

    shareid, uk, bdstoken = meta.get("shareid"), meta.get("uk"), meta.get("bdstoken")
    if not (shareid and uk and bdstoken):
        raise ValueError("Baidu PCS: shorturlinfo response is missing shareid/uk/bdstoken.")

    try:
        list_resp = session.get(
            f"{base_origin}/share/list",
            params={"shareid": shareid, "uk": uk, "bdstoken": bdstoken, "dir": "/", "num": 100},
            headers={"Referer": f"{base_origin}/s/{surl}"},
            timeout=20, verify=False,
        )
        list_data = list_resp.json()
    except Exception as e:
        raise ValueError(f"Baidu PCS: share/list request failed: {e}") from e

    if list_data.get("errno"):
        raise ValueError(list_data.get("errmsg") or f"Baidu PCS: share/list error {list_data.get('errno')}")

    files = [f for f in (list_data.get("list") or []) if f.get("isdir") != 1]
    if not files:
        raise ValueError("Baidu PCS: no downloadable files in this share.")
    file = files[0]

    try:
        dl_resp = session.post(
            f"{base_origin}/api/download",
            data={
                "shareid": shareid, "uk": uk, "fs_id": file.get("fs_id"),
                "sign": file.get("sign"), "timestamp": int(time.time()),
                "bdstoken": bdstoken, "primaryid": uk, "type": "nolimit",
            },
            headers={"Referer": f"{base_origin}/s/{surl}"},
            timeout=20, verify=False,
        )
        dl_data = dl_resp.json()
    except Exception as e:
        raise ValueError(f"Baidu PCS: api/download request failed: {e}") from e

    if dl_data.get("errno"):
        raise ValueError(dl_data.get("errmsg") or f"Baidu PCS: api/download error {dl_data.get('errno')}")

    dlink = dl_data.get("dlink") or (dl_data.get("list") or [{}])[0].get("dlink")
    if not dlink:
        raise ValueError("Baidu PCS: api/download response had no dlink.")

    size = int(file.get("size") or 0)
    return {
        "name": file.get("server_filename") or "terabox_video.mp4",
        "download_link": dlink,
        "size_str": get_size(size),
        "size_bytes": size,
        "thumb": (file.get("thumbs") or {}).get("url3") or "",
        "stream_link": "",
        "qualities": {},
    }


async def _get_file_info_baidu_pcs(share_url: str) -> dict:
    return await asyncio.to_thread(_get_file_info_baidu_pcs_sync, share_url)


# =========================================================
# Guest HTML page-scrape (last-resort, no key, no login, no proxy)
# =========================================================
# Every tier above talks to some API (a third-party one, or TeraBox's own
# Baidu PCS backend). This one is the actual last resort: fetch the public
# share page itself — https://<domain>/s/<surl> — as a plain browser would,
# and pull a download/stream link out of whatever HTML/JS comes back using
# the same schema-agnostic extractor (_generic_extract_stream_info,
# defined above) the beer/ansh tiers already use to parse odd-shaped API
# responses. Ported 1:1 from Akbots/diskwala.py's get_diskwala_info_guest +
# _guest_session, which exists there for exactly this reason (diskwala.com
# runs the same kind of share page). Only catches cases where the page
# embeds a real link server-side — a page that renders it via client-side
# JS after load needs a real browser and can't be caught here either.

_GUEST_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _tb_guest_session(share_url: str):
    domain = urllib.parse.urlparse(share_url).netloc.lower().split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]

    session = _requests.Session()
    session.headers.update({
        "User-Agent": _GUEST_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    cookies_path = _tb_get_cookies_for_url(f"https://{domain}/")
    if cookies_path:
        try:
            with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
                cookies = _tb_parse_cookies(f.read())
            for name, value in cookies.items():
                session.cookies.set(name, value, domain=domain)
        except Exception as e:
            logging.warning(f"[terabox] Guest scrape: couldn't load auto-detected cookies for {domain}: {e}")
    return session, domain


def _get_file_info_guest_sync(share_url: str) -> dict:
    """Blocking — always called via asyncio.to_thread. Raises ValueError
    on any failure, same as every other tier in this chain."""
    session, domain = _tb_guest_session(share_url)
    try:
        resp = session.get(share_url, timeout=20, headers={"Referer": f"https://{domain}/"}, verify=False)
    except Exception as e:
        raise ValueError(f"Guest scrape: page fetch failed: {e}") from e

    if resp.status_code != 200:
        raise ValueError(f"Guest scrape: page fetch got HTTP {resp.status_code}")

    found = _generic_extract_stream_info(resp.text or "")
    if not found:
        raise ValueError(
            "Guest scrape: couldn't find a download link on the share page "
            "(it may render the link via client-side JS after load, which "
            "this tier can't execute)."
        )

    return {
        "name": found.get("name") or "terabox_video.mp4",
        "download_link": found["download_link"],
        "size_str": "Unknown",  # not available from a raw page scrape
        "size_bytes": 0,
        "thumb": "",
        "stream_link": "",
        "qualities": {},
    }


async def _get_file_info_guest(share_url: str) -> dict:
    return await asyncio.to_thread(_get_file_info_guest_sync, share_url)


async def get_file_info_from_api(share_url: str) -> dict:
    """Public entry point used by terastream_command — tries the primary
    xAPIverse resolver first, falls back to terabox.beer, then to the
    anshapi.workers.dev worker, then azhawasadda.in's extractor (none of
    these three need an API key), then the Baidu PCS guest resolver (talks
    to TeraBox's own backend directly, no key or login needed either),
    then a guest HTML page-scrape of the share page itself (true
    last-resort, no API at all), and finally to the direct-API tier
    (Akbots/terabox_lib/, requires TERABOX_NDUS) if all six fail, so a
    dead/rate-limited key doesn't take TeraBox support down entirely."""
    try:
        return await _get_file_info_xapiverse(share_url)
    except Exception as primary_err:
        try:
            return await _get_file_info_beer(share_url)
        except Exception as beer_err:
            try:
                return await _get_file_info_ansh(share_url)
            except Exception as ansh_err:
                try:
                    return await _get_file_info_azhawasadda(share_url)
                except Exception as azh_err:
                    try:
                        return await _get_file_info_baidu_pcs(share_url)
                    except Exception as baidu_err:
                        try:
                            return await _get_file_info_guest(share_url)
                        except Exception as guest_err:
                            try:
                                return await _get_file_info_direct(share_url)
                            except Exception as direct_err:
                                raise ValueError(
                                    f"All resolvers failed — xAPIverse: {primary_err} | "
                                    f"terabox.beer: {beer_err} | anshapi: {ansh_err} | "
                                    f"azhawasadda: {azh_err} | Baidu PCS: {baidu_err} | "
                                    f"Guest scrape: {guest_err} | direct-API: {direct_err}"
                                )


async def get_folder_info_from_api(share_url: str, password: str | None = None) -> list[dict]:
    """Folder-aware public entry point used by _process_terabox — returns
    every file behind the share link (one item for a normal file link,
    several for a folder link) instead of only the first.

    Resolver order: Pro (per-quality HLS streams, needs a Pro-tier key) ->
    plain xAPIverse (single quality, works on any key) -> terabox.beer ->
    anshapi.workers.dev -> azhawasadda.in -> Baidu PCS guest resolve ->
    guest HTML page-scrape (none of these five need a key or login) ->
    direct-API (Akbots/terabox_lib/, needs TERABOX_NDUS — transfers into
    an owned account as a last resort). Each one is only tried if the
    previous one fails, so a missing Pro key just means no quality
    picker — not a broken /terabox command."""
    try:
        return await _get_folder_info_xapiverse_pro(share_url, password)
    except Exception:
        pass  # no Pro access — fall through to the plain resolvers below

    try:
        return await _get_folder_info_xapiverse(share_url, password)
    except Exception as primary_err:
        try:
            single = await _get_file_info_beer(share_url)
            return [single]
        except Exception as beer_err:
            try:
                single = await _get_file_info_ansh(share_url)
                return [single]
            except Exception as ansh_err:
                try:
                    single = await _get_file_info_azhawasadda(share_url)
                    return [single]
                except Exception as azh_err:
                    try:
                        single = await _get_file_info_baidu_pcs(share_url)
                        return [single]
                    except Exception as baidu_err:
                        try:
                            single = await _get_file_info_guest(share_url)
                            return [single]
                        except Exception as guest_err:
                            try:
                                from Akbots import terabox_lib
                                if not terabox_lib.is_configured():
                                    raise ValueError("direct-API tier not configured (TERABOX_NDUS unset)")
                                return await terabox_lib.resolve(share_url, password=password)
                            except Exception as direct_err:
                                raise ValueError(
                                    f"All resolvers failed — xAPIverse: {primary_err} | "
                                    f"terabox.beer: {beer_err} | anshapi: {ansh_err} | "
                                    f"azhawasadda: {azh_err} | Baidu PCS: {baidu_err} | "
                                    f"Guest scrape: {guest_err} | direct-API: {direct_err}"
                                )


# In-memory cache mapping a short token -> the file list behind a resolved
# folder link, so the inline "pick a file" keyboard doesn't have to stuff
# every file's full name/URL into callback_data (Telegram caps that at 64
# bytes). Mirrors what NEO-WZML's web-based folder selection store does,
# just kept in-process since this bot has no selection webserver.
_TERABOX_FOLDER_CACHE: dict[str, dict] = {}
_TERABOX_QUALITY_CACHE: dict[str, dict] = {}
_TERABOX_CACHE_TTL = 15 * 60  # 15 minutes


def _cache_terabox_folder(files: list[dict], url: str, password: str | None) -> str:
    token = uuid.uuid4().hex[:10]
    _TERABOX_FOLDER_CACHE[token] = {
        "files": files, "url": url, "password": password, "ts": time.time(),
    }
    return token


def _cache_terabox_quality(info: dict, qualities: dict, url: str, password: str | None) -> str:
    token = uuid.uuid4().hex[:10]
    _TERABOX_QUALITY_CACHE[token] = {
        "info": info, "qualities": qualities, "url": url, "password": password, "ts": time.time(),
    }
    return token


def _cleanup_terabox_cache():
    now = time.time()
    for cache in (_TERABOX_FOLDER_CACHE, _TERABOX_QUALITY_CACHE):
        for key in [k for k, v in cache.items() if now - v["ts"] > _TERABOX_CACHE_TTL]:
            cache.pop(key, None)


# Telegram hard-caps a single message's inline keyboard at 100 buttons total
# (see core.telegram.org/bots/api#inlinekeyboardmarkup) — stuffing more than
# that in one go gets the whole message rejected with "reply markup is too
# long". So instead of truncating the file list (old behaviour: first 50
# only, rest inaccessible), we paginate it — every file is reachable, just
# a page at a time. 40 files/page leaves headroom for the nav/download-all/
# cancel rows below it.
_TERABOX_PAGE_SIZE = 40


def _render_terabox_folder_page(files: list[dict], token: str, page: int):
    """Builds the (text, buttons) for one page of a folder's file list."""
    total = len(files)
    total_pages = max(1, (total + _TERABOX_PAGE_SIZE - 1) // _TERABOX_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _TERABOX_PAGE_SIZE
    page_files = files[start:start + _TERABOX_PAGE_SIZE]

    buttons = []
    for offset, f in enumerate(page_files):
        idx = start + offset
        label = f"{idx + 1}. {to_small_caps(f['name'][:35])} ({f['size_str']})"
        buttons.append([make_button(label, callback_data=f"teraf:{token}:{idx}",
                                     style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)])

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(make_button("⬅️ ᴘʀᴇᴠ", callback_data=f"terapage:{token}:{page - 1}",
                                        style=ButtonStyle.SECONDARY if BUTTON_STYLE_SUPPORTED else None))
        nav_row.append(make_button(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(make_button("ɴᴇxᴛ ➡️", callback_data=f"terapage:{token}:{page + 1}",
                                        style=ButtonStyle.SECONDARY if BUTTON_STYLE_SUPPORTED else None))
        buttons.append(nav_row)

    buttons.append([make_button("📥 ᴅᴏᴡɴʟᴏᴀᴅ ᴀʟʟ", callback_data=f"teraall:{token}",
                                 style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)])
    buttons.append([make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data="cancel_upload",
                                 style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)])

    page_note = f" — ᴘᴀɢᴇ {page + 1}/{total_pages}" if total_pages > 1 else ""
    text = (
        f"📂 **ғᴏʟᴅᴇʀ ʟɪɴᴋ ᴅᴇᴛᴇᴄᴛᴇᴅ — {total} ғɪʟᴇs ғᴏᴜɴᴅ{page_note}.**\n"
        "ᴛᴀᴘ ᴀ ғɪʟᴇ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ɪᴛ, ᴏʀ ᴅᴏᴡɴʟᴏᴀᴅ ᴀʟʟ ᴛᴏ ɢʀᴀʙ ᴇᴠᴇʀʏᴛʜɪɴɢ:"
    )
    return text, buttons


def _is_hls_url(url: str) -> bool:
    """The Pro endpoint's per-quality streams are HLS (.m3u8) playlists,
    not a single downloadable file — those need ffmpeg to remux into an
    actual video file, unlike the plain endpoint's normal_dlink."""
    return ".m3u8" in url.lower()


def _probe_hls_duration(url: str, headers: dict) -> float:
    """Best-effort ffprobe duration lookup so the live progress bar below
    can render a real percentage instead of just raw bytes/speed. Never
    raises — on any failure the caller just falls back to a byte-only
    progress view (percent stays 0, ETA stays blank)."""
    import subprocess
    header_lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    cmd = [
        "ffprobe", "-v", "error",
        "-headers", header_lines,
        "-show_entries", "format=duration",
        "-of", "default=nk=1:nw=1",
        url,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=25)
        dur = float(out.decode(errors="ignore").strip())
        return dur if 0 < dur < 1e7 else 0.0
    except Exception:
        return 0.0


async def _download_hls_via_ffmpeg(url: str, out_path: str, status_msg: Message, name: str, total_size_hint: int = 0, quality: str = None) -> float:
    """Remux an HLS quality stream straight to an mp4 via `ffmpeg -c copy`
    (no re-encode, so it's just as fast as a plain download once the CDN
    serves the segments), with:
    - a live progress bar (%, speed, ETA) parsed from ffmpeg's own
      `-progress pipe:1` machine-readable output (uses the same boxed
      style as every other downloader via format_progress), and
    - `-movflags +faststart` so the resulting mp4 has its moov atom moved
      to the front — instantly seekable/streamable in Telegram and
      /filetolink instead of needing the whole file buffered first."""
    short_name = to_small_caps(f"{name[:30]}{'...' if len(name) > 30 else ''}")
    try:
        await safe_edit(status_msg.edit, 
            f"📥 **ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ (ᴛᴜʀʙᴏ)**\n\n**ғɪʟᴇ ɴᴀᴍᴇ:** `{short_name}`\n\n"
            "ғᴇᴛᴄʜɪɴɢ ʜʟs sᴛʀᴇᴀᴍ ᴠɪᴀ ғғᴍᴘᴇɢ, ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ..."
        )
    except Exception:
        pass

    header_lines = "".join(f"{k}: {v}\r\n" for k, v in BROWSER_HEADERS.items())

    loop = asyncio.get_event_loop()
    duration = await loop.run_in_executor(None, _probe_hls_duration, url, BROWSER_HEADERS)

    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-threads", "4",
        "-headers", header_lines,
        "-i", url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-loglevel", "error",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    start_time = time.time()
    last_edit = 0.0
    bytes_written = 0
    percent = 0.0

    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                if proc.returncode is not None:
                    break
                await asyncio.sleep(0.2)
                continue

            line = line.decode(errors="ignore").strip()
            if not line:
                continue

            if line.startswith("out_time_ms="):
                try:
                    out_ms = float(line.split("=", 1)[1] or 0)
                    if duration > 0:
                        percent = min(100.0, (out_ms / 1_000_000.0 / duration) * 100.0)
                except Exception:
                    pass
            elif line.startswith("total_size="):
                try:
                    bytes_written = int(line.split("=", 1)[1] or 0)
                except Exception:
                    pass

            now = time.time()
            if now - last_edit >= 2.5:
                last_edit = now
                elapsed = now - start_time
                speed_bps = (bytes_written / elapsed) if elapsed > 0 else 0
                eta_secs = ((elapsed / percent) * (100.0 - percent)) if percent > 0 else None
                progress_text = format_progress(
                    percent,
                    speed_bps=speed_bps,
                    done_bytes=bytes_written,
                    total_bytes=total_size_hint or None,
                    elapsed_secs=elapsed,
                    eta_secs=eta_secs,
                    file_name=short_name,
                    duration=duration or None,
                    quality=quality,
                )
                try:
                    await safe_edit(status_msg.edit, progress_text, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
    except Exception:
        try:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        except Exception:
            pass
        raise

    rc = await proc.wait()
    if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        stderr = (await proc.stderr.read()).decode(errors="ignore").strip()[-500:]
        raise RuntimeError(stderr or "ffmpeg failed to produce output")

    return duration


_SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ', 'h': 'ʜ',
    'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ',
    'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x',
    'y': 'ʏ', 'z': 'ᴢ',
}


def to_small_caps(text: str) -> str:
    """Display-only stylization — converts letters to small-caps unicode
    for showing file names in captions/status messages. Never applied to
    the real file name used for temp paths, os.path operations, or the
    file_name= sent to Telegram, so downloads/uploads still work with the
    original, untouched name."""
    return "".join(_SMALL_CAPS_MAP.get(ch.lower(), ch) for ch in text)


def get_size(bytes_len: int) -> str:
    if bytes_len >= 1024 ** 3:
        return f"{bytes_len / 1024**3:.2f} GB"
    if bytes_len >= 1024 ** 2:
        return f"{bytes_len / 1024**2:.2f} MB"
    if bytes_len >= 1024:
        return f"{bytes_len / 1024:.2f} KB"
    return f"{bytes_len} bytes"


def _make_telegram_thumb(raw_path: str, out_path: str) -> bool:
    """Re-encode a downloaded thumbnail into what Telegram actually
    requires for `thumb=`: JPEG, longest side <= 320px, under 200KB.
    Skipping this step is why raw TeraBox thumbnails (often large PNG/
    WebP/high-res JPEG) show up blank, white, or blurry in Telegram —
    it silently drops thumbs that don't meet these limits. Runs in a
    thread via asyncio.to_thread since Pillow is sync/CPU-bound."""
    from PIL import Image
    try:
        with Image.open(raw_path) as img:
            img = img.convert("RGB")
            img.thumbnail((320, 320))  # in-place, preserves aspect ratio
            quality = 90
            while quality >= 40:
                img.save(out_path, "JPEG", quality=quality)
                if os.path.getsize(out_path) <= 200 * 1024:
                    return True
                quality -= 10
        return os.path.exists(out_path)
    except Exception:
        return False


def _probe_video_metadata(path: str) -> tuple[int, int, int]:
    """ffprobe the *actual downloaded/muxed file* for duration/width/height.
    This is what send_video needs to show a real length instead of the
    "0:00" Telegram displays when duration isn't passed — probing the
    local output (rather than trusting only the pre-download HLS probe)
    also works for the plain aiohttp download path, which never had any
    duration info at all before. Never raises; returns (0, 0, 0) only if
    BOTH ffprobe and the mediainfo fallback fail, so a bad probe never
    blocks the actual upload — but a missing duration/width/height is
    also exactly what makes Telegram show a bare "download" icon instead
    of a scrubbable, streamable video player, so it's worth a real retry
    before giving up.
    """
    import subprocess, json

    def _try_ffprobe(timeout: int) -> tuple[int, int, int] | None:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json",
            path,
        ]
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=timeout)
            data = json.loads(out.decode(errors="ignore"))
            duration = int(round(float(data.get("format", {}).get("duration", 0) or 0)))
            streams = data.get("streams") or [{}]
            width = int(streams[0].get("width", 0) or 0)
            height = int(streams[0].get("height", 0) or 0)
            if duration or width or height:
                return duration, width, height
        except Exception:
            pass
        return None

    # First attempt — generous 60s timeout instead of the old 25s, since a
    # large (300MB+) file on a slow/shared VPS can legitimately take that
    # long just to open and index, especially under concurrent load.
    result = _try_ffprobe(timeout=60)
    if result:
        return result

    # ffprobe failed or returned all-zero — try mediainfo as a second,
    # independently-implemented prober (already an installed dependency
    # per /diagnostics) before giving up entirely.
    try:
        out = subprocess.check_output(
            ["mediainfo", "--Output=JSON", path], stderr=subprocess.STDOUT, timeout=30
        )
        data = json.loads(out.decode(errors="ignore"))
        tracks = data.get("media", {}).get("track", [])
        duration, width, height = 0, 0, 0
        for t in tracks:
            if t.get("@type") == "Video":
                duration = int(round(float(t.get("Duration", 0) or 0)))
                width = int(float(t.get("Width", 0) or 0))
                height = int(float(t.get("Height", 0) or 0))
                break
        if duration or width or height:
            return duration, width, height
    except Exception:
        pass

    return 0, 0, 0


def _extract_video_thumb(video_path: str, out_path: str, at_secs: float) -> bool:
    """Fallback thumbnail: grab a real frame from the downloaded video
    itself via ffmpeg. Used whenever TeraBox didn't provide a thumbnail
    URL at all, or `_download_thumb` failed (dead link/timeout/bad
    image) — previously that meant send_video/send_document went out
    with no thumb= at all, which is why Telegram shows the blank white
    placeholder with just a play button seen in the screenshot."""
    import subprocess
    ts = max(0.0, at_secs)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(ts), "-i", video_path,
        "-vframes", "1", "-vf", "scale=320:-2",
        out_path,
    ]
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=25)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        return False


async def _download_thumb(thumb_url: str) -> str | None:
    """Best-effort download of the TeraBox-provided thumbnail image,
    re-encoded to Telegram's required format, so it can be passed as
    `thumb=` to send_video / send_document. Returns None (not an
    exception) on any failure — a missing/bad thumbnail should never
    block the actual file upload."""
    if not thumb_url:
        return None
    raw_path = os.path.join(tempfile.gettempdir(), f"tb_thumb_raw_{uuid.uuid4().hex}")
    out_path = os.path.join(tempfile.gettempdir(), f"tb_thumb_{uuid.uuid4().hex}.jpg")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(thumb_url, headers=BROWSER_HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return None
                async with aiofiles.open(raw_path, "wb") as f:
                    async for chunk in r.content.iter_chunked(65536):
                        await f.write(chunk)

        ok = await asyncio.to_thread(_make_telegram_thumb, raw_path, out_path)
        return out_path if ok else None
    except Exception:
        return None
    finally:
        if os.path.exists(raw_path):
            try:
                os.remove(raw_path)
            except Exception:
                pass


def detect_file_type(filename: str) -> str:
    mime_type, _ = mimetypes.guess_type(filename)

    if mime_type:
        if mime_type.startswith('video/'):
            return 'video'
        elif mime_type.startswith('image/'):
            return 'photo'

    video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.m4v', '.3gp']
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff']
    ext = os.path.splitext(filename.lower())[1]

    if ext in video_extensions:
        return 'video'
    elif ext in image_extensions:
        return 'photo'
    else:
        return 'document'


def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins}m {secs}s"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        return f"{hours}h {mins}m"


def calculate_speed(downloaded: int, elapsed_time: float) -> str:
    if elapsed_time == 0:
        return "0 B/s"
    speed = downloaded / elapsed_time
    if speed >= 1024 ** 3:
        return f"{speed / 1024**3:.2f} GB/s"
    elif speed >= 1024 ** 2:
        return f"{speed / 1024**2:.2f} MB/s"
    elif speed >= 1024:
        return f"{speed / 1024:.2f} KB/s"
    else:
        return f"{speed:.2f} B/s"


async def _process_terabox(
    client, user_id: int, status_msg: Message, url: str,
    password: str | None = None, preselected: dict | None = None,
):
    """Shared core: used by the plain-link auto-detect handler, the
    explicit /terabox command, and the folder file-picker callbacks below.
    `status_msg` is the message that gets edited throughout (progress,
    errors, final result) — callers create/own it so this same function can
    be driven either by a fresh command or by tapping a button on an
    existing message.
    `preselected`, if given, is an already-resolved file dict (picked from
    a folder listing), so the API doesn't get hit a second time."""

    # Fast-DB: if this exact link was already downloaded & uploaded before,
    # re-send the cached file_id instantly instead of hitting the
    # extraction API / re-downloading. Skipped for preselected calls
    # (folder-file-pick, quality-pick, download-all) since `url` there is
    # the parent folder link shared by many different files, not a 1:1 key.
    if preselected is None:
        if await try_send_cached(client, status_msg, url, status_msg):
            return

    if preselected is not None:
        info = preselected
    else:
        await safe_edit(status_msg.edit, "🔍 ғᴇᴛᴄʜɪɴɢ ғɪʟᴇ ɪɴғᴏ...")
        try:
            files = await get_folder_info_from_api(url, password)
        except Exception as e:
            await safe_edit(status_msg.edit, f"❌ ғᴀɪʟᴇᴅ ᴛᴏ ɢᴇᴛ ғɪʟᴇ ɪɴғᴏ:\n`{e}`")
            return

        if len(files) > 1:
            # Folder link — let the user pick a file (or grab all of them)
            # instead of silently downloading only the first one.
            _cleanup_terabox_cache()
            token = _cache_terabox_folder(files, url, password)
            text, buttons = _render_terabox_folder_page(files, token, page=0)
            await safe_edit(status_msg.edit, text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        info = files[0]
        qualities = info.get("qualities") or {}
        if len(qualities) > 1:
            # Pro-endpoint resolver returned multiple HLS renditions for
            # this single file — let the user pick instead of silently
            # grabbing whichever one happened to be default.
            _cleanup_terabox_cache()
            token = _cache_terabox_quality(info, qualities, url, password)
            _QUALITY_ORDER = ["4k", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]
            sorted_qualities = sorted(
                qualities.keys(),
                key=lambda q: _QUALITY_ORDER.index(q.lower()) if q.lower() in _QUALITY_ORDER else 99,
                reverse=True,
            )
            buttons = [
                [make_button(f"🎬 {q}", callback_data=f"teraq:{token}:{q}", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]
                for q in sorted_qualities
            ]
            buttons.append([make_button("⬇️ ᴏʀɪɢɪɴᴀʟ (ʙᴇsᴛ ᴀᴠᴀɪʟᴀʙʟᴇ)", callback_data=f"teraq:{token}:__original__", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)])
            buttons.append([make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data="cancel_upload", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)])
            await safe_edit(status_msg.edit, 
                f"🎞️ **{to_small_caps(info['name'])}**\n📦 {info['size_str']}\n\nᴄʜᴏᴏsᴇ ᴀ ǫᴜᴀʟɪᴛʏ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

    if not info["download_link"]:
        await safe_edit(status_msg.edit, "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇᴛʀɪᴇᴠᴇ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋ")
        return

    is_hls = _is_hls_url(info["download_link"])
    if is_hls:
        # A quality-selected stream is an .m3u8 playlist, not a file —
        # force a real .mp4 name/extension for the muxed output.
        base_name, _ext = os.path.splitext(info["name"])
        info = {**info, "name": f"{base_name}.mp4"}

    temp_path = os.path.join(tempfile.gettempdir(), info["name"])
    thumb_path = None
    file_type = detect_file_type(info["name"])
    quality_display = info.get("quality") or "sᴛʀᴇᴀᴍɪɴɢ ǫᴜᴀʟɪᴛʏ"

    start_time = time.time()
    last_update_time = start_time
    last_percentage = 0
    downloaded = 0

    from Akbots import task_manager
    task_id = None
    try:
        task_id = task_manager.register(
            user_id, asyncio.current_task(), f"TeraBox: {to_small_caps(info['name'][:40])}"
        )
    except Exception:
        task_id = None

    # Manually entered (not `async with`) so the slot can be released right
    # after upload finishes, instead of being held for the 12h auto-delete
    # sleep further down — that sleep shouldn't count against the user's
    # concurrent-download limit.
    _slot = task_manager.queue_slot(user_id, status_msg=status_msg)
    await _slot.__aenter__()
    _slot_open = True

    try:
        probed_duration = 0
        if is_hls:
            probed_duration = await _download_hls_via_ffmpeg(
                info["download_link"], temp_path, status_msg, info["name"],
                total_size_hint=int(info.get("size_bytes") or 0),
                quality=quality_display,
            )
        else:
            total_size_hint = int(info.get("size_bytes") or 0)
            used_parallel = False

            if total_size_hint >= PARALLEL_DOWNLOAD_MIN_SIZE:
                async def _on_parallel_progress(done: int, total: int):
                    nonlocal downloaded, last_update_time, last_percentage
                    downloaded = done
                    current_time = time.time()
                    elapsed = current_time - start_time
                    percentage = (done / total * 100) if total > 0 else 0
                    if (current_time - last_update_time >= 4) or (percentage - last_percentage >= 5):
                        last_update_time = current_time
                        last_percentage = percentage
                        speed_bps = (done / elapsed) if elapsed > 0 else 0
                        eta_secs = ((total - done) / speed_bps) if total > 0 and speed_bps > 0 else None
                        progress_text = format_progress(
                            percentage, speed_bps=speed_bps, done_bytes=done, total_bytes=total,
                            elapsed_secs=elapsed, eta_secs=eta_secs,
                            file_name=to_small_caps(info["name"]), quality=quality_display,
                        )
                        try:
                            await safe_edit(status_msg.edit, progress_text, parse_mode=enums.ParseMode.HTML)
                        except Exception:
                            pass

                used_parallel = await _download_parallel(
                    info["download_link"], temp_path, total_size_hint, BROWSER_HEADERS,
                    PARALLEL_DOWNLOAD_CHUNKS, _on_parallel_progress,
                )
                if used_parallel:
                    downloaded = total_size_hint

            if not used_parallel:
                # Download using aiohttp for async speed
                async with aiohttp.ClientSession() as session:
                    # 🚀 INJECT BROWSER HEADERS HERE
                    async with session.get(info["download_link"], headers=BROWSER_HEADERS, timeout=aiohttp.ClientTimeout(total=None)) as r:
                        r.raise_for_status()
                        total_size = int(r.headers.get('content-length', 0)) or int(info.get('size_bytes') or 0)

                        async with aiofiles.open(temp_path, "wb") as f:
                            chunk_size = 8 * 1024 * 1024  # 8MB chunks for fast high-throughput performance

                            async for chunk in r.content.iter_chunked(chunk_size):
                                if chunk:
                                    await f.write(chunk)
                                    downloaded += len(chunk)

                                    current_time = time.time()
                                    elapsed = current_time - start_time
                                    percentage = (downloaded / total_size * 100) if total_size > 0 else 0

                                    # DOUBLE THROTTLING: Update progress every 4 seconds OR if progress jumps 5%
                                    if (current_time - last_update_time >= 4) or (percentage - last_percentage >= 5):
                                        last_update_time = current_time
                                        last_percentage = percentage

                                        speed_bps = (downloaded / elapsed) if elapsed > 0 else 0
                                        eta_secs = (
                                            (total_size - downloaded) / speed_bps
                                            if total_size > 0 and speed_bps > 0 else None
                                        )
                                        progress_text = format_progress(
                                            percentage,
                                            speed_bps=speed_bps,
                                            done_bytes=downloaded,
                                            total_bytes=total_size,
                                            elapsed_secs=elapsed,
                                            eta_secs=eta_secs,
                                            file_name=to_small_caps(info["name"]),
                                            quality=quality_display,
                                        )

                                        try:
                                            await safe_edit(status_msg.edit, progress_text, parse_mode=enums.ParseMode.HTML)
                                        except Exception:
                                            pass

        download_elapsed = time.time() - start_time
        await safe_edit(status_msg.edit, "📤 **ᴘʀᴇᴘᴀʀɪɴɢ ᴛᴏ ᴜᴘʟᴏᴀᴅ ᴛᴏ ᴛᴇʟᴇɢʀᴀᴍ...**")

        def _fmt_dur(seconds: float) -> str:
            seconds = int(round(seconds))
            h, rem = divmod(seconds, 3600)
            m, s = divmod(rem, 60)
            return f"{h}:{m:02d}:{s:02d} sec"

        quality_caption_display = info.get("quality") or "sᴛʀᴇᴀᴍɪɴɢ ǫᴜᴀʟɪᴛʏ (sᴀᴍᴇ ᴄᴏɴᴛᴇɴᴛ ɪɴ ʟᴇss ᴍʙ ᴡɪᴛʜᴏᴜᴛ ᴄᴏᴍᴘʀᴇssɪᴏɴ)"

        def build_caption(upload_elapsed: float | None = None) -> str:
            lines = [
                "<blockquote>",
                f"📄 **ғɪʟᴇ ɴᴀᴍᴇ:** `{to_small_caps(info['name'])}`\n",
                f"📦 **sɪᴢᴇ:** {info['size_str']}\n",
                f"🎞️ **ǫᴜᴀʟɪᴛʏ:** {quality_caption_display}\n",
                f"⬇️ **ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ ɪɴ:** {_fmt_dur(download_elapsed)}\n",
            ]
            if upload_elapsed is not None:
                lines.append(f"⬆️ **ᴜᴘʟᴏᴀᴅᴇᴅ ɪɴ:** {_fmt_dur(upload_elapsed)}\n")
            lines += [
                f"🙋 **ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ:** `{user_id}`\n",
                f"🔗 **sᴏᴜʀᴄᴇ:** [ᴛᴇʀᴀʙᴏx ʟɪɴᴋ]({url})\n\n",
                f"⚡ ᴘᴏᴡᴇʀᴇᴅ ʙʏ [ᴀɴᴜᴊ ᴋᴜᴍᴀʀ](https://t.me/anujedits76)",
                "</blockquote>",
            ]
            return "".join(lines)

        caption = build_caption()

        cancel_button = InlineKeyboardMarkup([
            [make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data="cancel_upload", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]
        ])

        # Grab the TeraBox-provided thumbnail (if any) so send_video /
        # send_document show it instead of Telegram's auto-generated one.
        thumb_path = await _download_thumb(info.get("thumb", ""))

        duration, vid_width, vid_height = 0, 0, 0
        if file_type == 'video':
            duration, vid_width, vid_height = await asyncio.to_thread(_probe_video_metadata, temp_path)
            if not duration:
                duration = int(probed_duration or 0)

            if not thumb_path:
                # TeraBox gave no thumbnail URL, or it failed to download —
                # grab a real frame from the file we already have on disk
                # instead of sending no thumb= at all.
                gen_thumb = os.path.join(tempfile.gettempdir(), f"tb_thumb_gen_{uuid.uuid4().hex}.jpg")
                seek_at = min(duration / 2, 5) if duration else 1.0
                ok = await asyncio.to_thread(_extract_video_thumb, temp_path, gen_thumb, seek_at)
                if ok:
                    thumb_path = gen_thumb

        if TERABOX_LEECH_CHANNEL:
            if file_type == 'video':
                await client.send_video(
                    chat_id=TERABOX_LEECH_CHANNEL,
                    video=temp_path,
                    caption=caption,
                    file_name=info["name"],
                    supports_streaming=True,
                    thumb=thumb_path,
                    duration=duration or None,
                    width=vid_width or None,
                    height=vid_height or None,
                )
            elif file_type == 'photo':
                await client.send_photo(
                    chat_id=TERABOX_LEECH_CHANNEL,
                    photo=temp_path,
                    caption=caption
                )
            else:
                await client.send_document(
                    chat_id=TERABOX_LEECH_CHANNEL,
                    document=temp_path,
                    caption=caption,
                    file_name=info["name"],
                    thumb=thumb_path
                )

        upload_progress = make_upload_progress(status_msg, file_name=to_small_caps(info["name"]), quality=quality_display)
        upload_start_time = time.time()

        if file_type == 'video':
            sent_msg = await client.send_video(
                chat_id=status_msg.chat.id,
                video=temp_path,
                caption=caption,
                file_name=info["name"],
                supports_streaming=True,
                thumb=thumb_path,
                duration=duration or None,
                width=vid_width or None,
                height=vid_height or None,
                progress=upload_progress
            )
        elif file_type == 'photo':
            sent_msg = await client.send_photo(
                chat_id=status_msg.chat.id,
                photo=temp_path,
                caption=caption,
                progress=upload_progress
            )
        else:
            sent_msg = await client.send_document(
                chat_id=status_msg.chat.id,
                document=temp_path,
                caption=caption,
                file_name=info["name"],
                thumb=thumb_path,
                progress=upload_progress
            )

        upload_elapsed = time.time() - upload_start_time
        try:
            await sent_msg.edit_caption(build_caption(upload_elapsed=upload_elapsed))
        except Exception:
            pass

        if preselected is None:
            try:
                await _cache_store(url, sent_msg, caption=build_caption(upload_elapsed=upload_elapsed))
            except Exception:
                pass

        await safe_edit(status_msg.edit, 
            f"✅ **ғɪʟᴇ ᴜᴘʟᴏᴀᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ ᴀs {file_type.upper()}!**\n\n"
            "⏰ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇᴅ ɪɴ 12 ʜᴏᴜʀs."
        )

        if _slot_open:
            try:
                await _slot.__aexit__(None, None, None)
            except Exception:
                pass
            _slot_open = False

        # The 12h auto-delete must NOT block this coroutine's return — when
        # called from a folder's "Download All" loop, every file waited
        # here in turn, so only the first file would ever be processed
        # (the rest sat stuck behind a 12-hour sleep). Fire-and-forget it
        # as its own task instead so the caller can move on immediately.
        async def _auto_delete_later():
            await asyncio.sleep(43200)
            try:
                await sent_msg.delete()
                await status_msg.delete()
            except Exception:
                pass
        asyncio.create_task(_auto_delete_later())

    except aiohttp.ClientError as e:
        await safe_edit(status_msg.edit, f"❌ **ᴅᴏᴡɴʟᴏᴀᴅ ғᴀɪʟᴇᴅ:**\n`{str(e)}`")
    except RuntimeError as e:
        await safe_edit(status_msg.edit, f"❌ **ʜʟs ᴅᴏᴡɴʟᴏᴀᴅ ғᴀɪʟᴇᴅ (ғғᴍᴘᴇɢ):**\n`{str(e)}`")
    except Exception as e:
        await safe_edit(status_msg.edit, f"❌ **ᴜᴘʟᴏᴀᴅ ғᴀɪʟᴇᴅ:**\n`{str(e)}`")
    finally:
        if _slot_open:
            try:
                await _slot.__aexit__(None, None, None)
            except Exception:
                pass
        if task_id is not None:
            try:
                task_manager.unregister(user_id, task_id)
            except Exception:
                pass
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass


def _extract_url_arg(message: Message) -> str | None:
    """Pull a TeraBox link out of `/terabox <link>` or a replied-to message,
    for the two explicit commands below."""
    if len(message.command) > 1:
        candidate = message.command[1].strip()
    elif message.reply_to_message and message.reply_to_message.text:
        candidate = message.reply_to_message.text.strip()
    else:
        candidate = None
    if candidate and re.search(TERABOX_REGEX, candidate, re.IGNORECASE):
        return candidate
    return None


def _extract_password_arg(message: Message) -> str | None:
    """`/terabox <link> <password>` — grabs the extraction-code/password
    for a protected share, if one was given as a second argument."""
    if len(message.command) > 2:
        return message.command[2].strip()
    return None


@Client.on_message(
    filters.text & filters.private & filters.regex(TERABOX_REGEX) & ~filters.regex(r"^/"),
    group=1,  # same priority as the other dedicated site handlers in ytdl.py
)
async def handle_terabox(client, message: Message):
    """Fires on any bare TeraBox link pasted into the chat."""
    status_msg = await message.reply("🔍 ғᴇᴛᴄʜɪɴɢ ғɪʟᴇ ɪɴғᴏ...")
    await _process_terabox(client, message.from_user.id, status_msg, message.text.strip())


@Client.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client, message: Message):
    """/terabox <link> [password] — same as pasting the link directly, plus
    an optional password for protected shares. Kept as an explicit command
    since start.py's help menu lists it."""
    url = _extract_url_arg(message)
    if not url:
        await message.reply_text(
            "ᴜsᴀɢᴇ: `/terabox <link> [password]` (ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴄᴏɴᴛᴀɪɴɪɴɢ ᴏɴᴇ)."
        )
        return
    password = _extract_password_arg(message)
    status_msg = await message.reply("🔍 ғᴇᴛᴄʜɪɴɢ ғɪʟᴇ ɪɴғᴏ...")
    await _process_terabox(client, message.from_user.id, status_msg, url, password=password)


@Client.on_callback_query(filters.regex(r"^terapage:"))
async def terabox_page_nav(client, callback_query):
    """User tapped ⬅️ Prev / Next ➡️ on a paginated folder listing."""
    try:
        _, token, page_str = callback_query.data.split(":", 2)
        page = int(page_str)
    except (ValueError, IndexError):
        await callback_query.answer("ɪɴᴠᴀʟɪᴅ ᴘᴀɢᴇ.", show_alert=True)
        return

    entry = _TERABOX_FOLDER_CACHE.get(token)
    if not entry:
        await callback_query.answer("⌛ ᴛʜɪs sᴇʟᴇᴄᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ — ᴘʟᴇᴀsᴇ ʀᴇsᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ.", show_alert=True)
        return

    await callback_query.answer()
    text, buttons = _render_terabox_folder_page(entry["files"], token, page)
    await safe_edit(callback_query.message.edit, text, reply_markup=InlineKeyboardMarkup(buttons))


@Client.on_callback_query(filters.regex(r"^teraf:"))
async def terabox_file_selected(client, callback_query):
    """User tapped one specific file from a folder listing."""
    try:
        _, token, idx_str = callback_query.data.split(":", 2)
        idx = int(idx_str)
    except (ValueError, IndexError):
        await callback_query.answer("ɪɴᴠᴀʟɪᴅ sᴇʟᴇᴄᴛɪᴏɴ.", show_alert=True)
        return

    entry = _TERABOX_FOLDER_CACHE.get(token)
    if not entry or idx >= len(entry["files"]):
        await callback_query.answer("⌛ ᴛʜɪs sᴇʟᴇᴄᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ — ᴘʟᴇᴀsᴇ ʀᴇsᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ.", show_alert=True)
        return

    await callback_query.answer()
    file_info = entry["files"][idx]
    await safe_edit(callback_query.message.edit, f"🔍 ᴘʀᴇᴘᴀʀɪɴɢ **{to_small_caps(file_info['name'])}**...")
    await _process_terabox(
        client, callback_query.from_user.id, callback_query.message,
        entry["url"], preselected=file_info,
    )


@Client.on_callback_query(filters.regex(r"^teraq:"))
async def terabox_quality_selected(client, callback_query):
    """User tapped a quality button (e.g. 720p) from the quality picker."""
    try:
        _, token, quality = callback_query.data.split(":", 2)
    except ValueError:
        await callback_query.answer("ɪɴᴠᴀʟɪᴅ sᴇʟᴇᴄᴛɪᴏɴ.", show_alert=True)
        return

    entry = _TERABOX_QUALITY_CACHE.get(token)
    if not entry:
        await callback_query.answer("⌛ ᴛʜɪs sᴇʟᴇᴄᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ — ᴘʟᴇᴀsᴇ ʀᴇsᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ.", show_alert=True)
        return

    await callback_query.answer()
    info = dict(entry["info"])  # shallow copy — don't mutate the cached entry
    if quality != "__original__":
        stream_url = entry["qualities"].get(quality)
        if stream_url:
            info["download_link"] = stream_url
            info["quality"] = quality
            base_name, ext = os.path.splitext(info["name"])
            info["name"] = f"{base_name} [{quality}]{ext or '.mp4'}"

    await safe_edit(callback_query.message.edit, f"🔍 ᴘʀᴇᴘᴀʀɪɴɢ **{to_small_caps(info['name'])}**...")
    await _process_terabox(
        client, callback_query.from_user.id, callback_query.message,
        entry["url"], preselected=info,
    )


@Client.on_callback_query(filters.regex(r"^teraall:"))
async def terabox_download_all(client, callback_query):
    """User tapped "Download All" on a folder listing — processes every
    file one after another, each getting its own progress message."""
    token = callback_query.data.split(":", 1)[1]
    entry = _TERABOX_FOLDER_CACHE.get(token)
    if not entry:
        await callback_query.answer("⌛ ᴛʜɪs sᴇʟᴇᴄᴛɪᴏɴ ᴇxᴘɪʀᴇᴅ — ᴘʟᴇᴀsᴇ ʀᴇsᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ.", show_alert=True)
        return

    await callback_query.answer()
    files = entry["files"]
    await safe_edit(callback_query.message.edit, f"📥 **ǫᴜᴇᴜɪɴɢ {len(files)} ғɪʟᴇs ғʀᴏᴍ ᴛʜᴇ ғᴏʟᴅᴇʀ...**")

    for i, file_info in enumerate(files, start=1):
        status_msg = await callback_query.message.reply(
            f"🔍 [{i}/{len(files)}] ᴘʀᴇᴘᴀʀɪɴɢ **{to_small_caps(file_info['name'])}**..."
        )
        await _process_terabox(
            client, callback_query.from_user.id, status_msg,
            entry["url"], preselected=file_info,
        )

    try:
        await callback_query.message.delete()
    except Exception:
        pass


@Client.on_message(filters.command("terastream") & filters.private)
async def terastream_command(client, message: Message):
    """/terastream <link> — returns the resolved direct/stream URL instead
    of downloading and re-uploading the file."""
    url = _extract_url_arg(message)
    if not url:
        await message.reply_text(
            "ᴜsᴀɢᴇ: `/terastream <link>` (ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴄᴏɴᴛᴀɪɴɪɴɢ ᴏɴᴇ)."
        )
        return

    status_msg = await message.reply("🔍 ʀᴇsᴏʟᴠɪɴɢ sᴛʀᴇᴀᴍ ʟɪɴᴋ...")
    try:
        info = await get_file_info_from_api(url)
    except Exception as e:
        await safe_edit(status_msg.edit, f"❌ ғᴀɪʟᴇᴅ ᴛᴏ ɢᴇᴛ ғɪʟᴇ ɪɴғᴏ:\n`{e}`")
        return

    stream_url = info.get("stream_link") or info.get("download_link")
    if not stream_url:
        await safe_edit(status_msg.edit, "❌ ᴄᴏᴜʟᴅ ɴᴏᴛ ʀᴇsᴏʟᴠᴇ ᴀ sᴛʀᴇᴀᴍ ʟɪɴᴋ ғᴏʀ ᴛʜɪs ғɪʟᴇ.")
        return

    await safe_edit(status_msg.edit, 
        f"📄 **ғɪʟᴇ ɴᴀᴍᴇ:** `{to_small_caps(info['name'])}`\n"
        f"📦 **sɪᴢᴇ:** {info['size_str']}\n\n"
        f"🎞️ **sᴛʀᴇᴀᴍ/ᴅɪʀᴇᴄᴛ ʟɪɴᴋ:**\n`{stream_url}`\n\n"
        f"⚠️ ᴛʜɪs ʟɪɴᴋ ᴍᴀʏ ᴇxᴘɪʀᴇ — ʀᴇ-ʀᴜɴ `/terastream` ɪғ ɪᴛ sᴛᴏᴘs ᴡᴏʀᴋɪɴɢ."
    )
