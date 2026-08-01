#please give credits https://github.com/MN-BOTS
#  @MrMNTG @MusammilN
#
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
import aiohttp
import aiofiles
from pyrogram import Client
from pyrogram import filters
from pyrogram.types import Message
from verify_patch import IS_VERIFY, is_verified, build_verification_link, HOW_TO_VERIFY
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
            async with session.post(API_BASE_URL, json=payload, headers=headers, timeout=30) as response:
                response.raise_for_status()
                data = await response.json()

                # ✅ Targets the "list" array inside the API response schema
                if data.get("status") == "success" and "list" in data and len(data["list"]) > 0:
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


async def get_file_info_from_api(share_url: str) -> dict:
    """Public entry point used by terastream_command — tries the primary
    xAPIverse resolver first, and falls back to the terabox.beer resolver
    (no API key needed) if that fails for any reason, so a dead/rate-limited
    key doesn't take TeraBox support down entirely."""
    try:
        return await _get_file_info_xapiverse(share_url)
    except Exception as primary_err:
        try:
            return await _get_file_info_beer(share_url)
        except Exception as fallback_err:
            raise ValueError(
                f"Both resolvers failed — xAPIverse: {primary_err} | terabox.beer: {fallback_err}"
            )


async def get_folder_info_from_api(share_url: str, password: str | None = None) -> list[dict]:
    """Folder-aware public entry point used by _process_terabox — returns
    every file behind the share link (one item for a normal file link,
    several for a folder link) instead of only the first.

    Resolver order: Pro (per-quality HLS streams, needs a Pro-tier key) ->
    plain xAPIverse (single quality, works on any key) -> terabox.beer
    (no key needed at all). Each one is only tried if the previous one
    fails, so a missing Pro key just means no quality picker — not a
    broken /terabox command."""
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
        except Exception as fallback_err:
            raise ValueError(
                f"Both resolvers failed — xAPIverse: {primary_err} | terabox.beer: {fallback_err}"
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


def _is_hls_url(url: str) -> bool:
    """The Pro endpoint's per-quality streams are HLS (.m3u8) playlists,
    not a single downloadable file — those need ffmpeg to remux into an
    actual video file, unlike the plain endpoint's normal_dlink."""
    return ".m3u8" in url.lower()


async def _download_hls_via_ffmpeg(url: str, out_path: str, status_msg: Message, name: str):
    """Remux an HLS quality stream straight to an mp4 via `ffmpeg -c copy`
    (no re-encode, so it's just as fast as a plain download once the CDN
    serves the segments)."""
    short_name = f"{name[:30]}{'...' if len(name) > 30 else ''}"
    try:
        await status_msg.edit(
            f"📥 **DOWNLOADING (Turbo)**\n\n**FILE NAME:** `{short_name}`\n\n"
            "Fetching HLS stream via ffmpeg, please wait..."
        )
    except Exception:
        pass

    header_lines = "".join(f"{k}: {v}\r\n" for k, v in BROWSER_HEADERS.items())
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-headers", header_lines,
        "-i", url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        err_tail = (stderr or b"").decode(errors="ignore").strip()[-500:]
        raise RuntimeError(err_tail or "ffmpeg failed to produce output")


def get_size(bytes_len: int) -> str:
    if bytes_len >= 1024 ** 3:
        return f"{bytes_len / 1024**3:.2f} GB"
    if bytes_len >= 1024 ** 2:
        return f"{bytes_len / 1024**2:.2f} MB"
    if bytes_len >= 1024:
        return f"{bytes_len / 1024:.2f} KB"
    return f"{bytes_len} bytes"


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


def progress_bar(percentage: float) -> str:
    filled_blocks = int(percentage / 5)
    empty_blocks = 20 - filled_blocks
    bar = "█" * filled_blocks + "░" * empty_blocks
    return f"[{bar}]"


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

    if IS_VERIFY and not await is_verified(user_id):
        verify_url = await build_verification_link(client.me.username, user_id)
        buttons = [
            [
                make_button("✅ Verify Now", url=verify_url, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
                make_button("📖 Tutorial", url=HOW_TO_VERIFY, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)
            ]
        ]
        await status_msg.edit(
            "🔐 You must verify before using this command.\n\n⏳ Verification lasts for 12 hours.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if preselected is not None:
        info = preselected
    else:
        await status_msg.edit("🔍 Fetching file info...")
        try:
            files = await get_folder_info_from_api(url, password)
        except Exception as e:
            await status_msg.edit(f"❌ Failed to get file info:\n`{e}`")
            return

        if len(files) > 1:
            # Folder link — let the user pick a file (or grab all of them)
            # instead of silently downloading only the first one.
            _cleanup_terabox_cache()
            token = _cache_terabox_folder(files, url, password)
            buttons = []
            for idx, f in enumerate(files[:50]):  # keep the keyboard sane
                label = f"{idx + 1}. {f['name'][:35]} ({f['size_str']})"
                buttons.append([make_button(label, callback_data=f"teraf:{token}:{idx}")])
            buttons.append([make_button("📥 Download All", callback_data=f"teraall:{token}")])
            buttons.append([make_button("❌ Cancel", callback_data="cancel_upload")])
            note = " (more than 50 — only the first 50 are listed)" if len(files) > 50 else ""
            await status_msg.edit(
                f"📂 **Folder link detected — {len(files)} files found{note}.**\n"
                "Tap a file to download it, or Download All to grab everything:",
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
                [make_button(f"🎬 {q}", callback_data=f"teraq:{token}:{q}")]
                for q in sorted_qualities
            ]
            buttons.append([make_button("⬇️ Original (best available)", callback_data=f"teraq:{token}:__original__")])
            buttons.append([make_button("❌ Cancel", callback_data="cancel_upload")])
            await status_msg.edit(
                f"🎞️ **{info['name']}**\n📦 {info['size_str']}\n\nChoose a quality to download:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

    if not info["download_link"]:
        await status_msg.edit("❌ Could not retrieve download link")
        return

    is_hls = _is_hls_url(info["download_link"])
    if is_hls:
        # A quality-selected stream is an .m3u8 playlist, not a file —
        # force a real .mp4 name/extension for the muxed output.
        base_name, _ext = os.path.splitext(info["name"])
        info = {**info, "name": f"{base_name}.mp4"}

    temp_path = os.path.join(tempfile.gettempdir(), info["name"])
    file_type = detect_file_type(info["name"])

    start_time = time.time()
    last_update_time = start_time
    last_percentage = 0
    downloaded = 0

    from Akbots import task_manager
    task_id = None
    try:
        task_id = task_manager.register(
            user_id, asyncio.current_task(), f"TeraBox: {info['name'][:40]}"
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
        if is_hls:
            await _download_hls_via_ffmpeg(info["download_link"], temp_path, status_msg, info["name"])
        else:
            # Download using aiohttp for async speed
            async with aiohttp.ClientSession() as session:
                # 🚀 INJECT BROWSER HEADERS HERE
                async with session.get(info["download_link"], headers=BROWSER_HEADERS, timeout=aiohttp.ClientTimeout(total=None)) as r:
                    r.raise_for_status()
                    total_size = int(r.headers.get('content-length', 0))

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
                                    speed = calculate_speed(downloaded, elapsed)

                                    if downloaded > 0 and elapsed > 0:
                                        remaining_bytes = total_size - downloaded
                                        bytes_per_second = downloaded / elapsed
                                        eta_seconds = int(remaining_bytes / bytes_per_second) if bytes_per_second > 0 else 0
                                        eta_str = format_time(eta_seconds)
                                    else:
                                        eta_str = "calculating..."

                                    progress_text = (
                                        f"📥 **DOWNLOADING (Turbo)**\n\n"
                                        f"**FILE NAME:** `{info['name'][:30]}{'...' if len(info['name']) > 30 else ''}`\n"
                                        f"**SIZE:** {get_size(total_size)}\n\n"
                                        f"**PROCESS:**\n"
                                        f"{progress_bar(percentage)}\n\n"
                                        f"**SPEED:** {speed}\n"
                                        f"**PROGRESS:** {percentage:.1f}%\n\n"
                                        f"**Downloaded:** {get_size(downloaded)}\n"
                                        f"**ETA:** {eta_str}"
                                    )

                                    try:
                                        await status_msg.edit(progress_text)
                                    except Exception:
                                        pass

        await status_msg.edit("📤 **Preparing to upload to Telegram...**")

        caption = (
            "<blockquote>"
            f"📄 **File Name:** `{info['name']}`\n"
            f"📦 **File Size:** {info['size_str']}\n"
            f"🔗 **Source:** [TeraBox Link]({url})\n\n"
            f"⚡ Powered by @MrMNTG"
            "</blockquote>"
        )

        cancel_button = InlineKeyboardMarkup([
            [make_button("❌ CANCEL", callback_data="cancel_upload", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]
        ])

        if TERABOX_LEECH_CHANNEL:
            if file_type == 'video':
                await client.send_video(
                    chat_id=TERABOX_LEECH_CHANNEL,
                    video=temp_path,
                    caption=caption,
                    file_name=info["name"],
                    has_spoiler=True,
                    supports_streaming=True
                )
            elif file_type == 'photo':
                await client.send_photo(
                    chat_id=TERABOX_LEECH_CHANNEL,
                    photo=temp_path,
                    caption=caption,
                    has_spoiler=True
                )
            else:
                await client.send_document(
                    chat_id=TERABOX_LEECH_CHANNEL,
                    document=temp_path,
                    caption=caption,
                    file_name=info["name"]
                )

        upload_start = time.time()
        last_upload_update = upload_start
        last_upload_percentage = 0

        async def upload_progress(current, total):
            nonlocal last_upload_update, last_upload_percentage
            current_time = time.time()
            percentage = (current / total) * 100

            # DOUBLE THROTTLING FOR UPLOADS: 4 seconds OR 5% jump
            if (current_time - last_upload_update >= 4) or (percentage - last_upload_percentage >= 5):
                last_upload_update = current_time
                last_upload_percentage = percentage

                elapsed = current_time - upload_start
                speed = calculate_speed(current, elapsed)

                if current > 0 and elapsed > 0:
                    remaining = total - current
                    rate = current / elapsed
                    eta = int(remaining / rate) if rate > 0 else 0
                    eta_str = format_time(eta)
                else:
                    eta_str = "calculating..."

                progress_text = (
                    f"📤 **UPLOADING (Turbo)**\n\n"
                    f"**FILE NAME:** `{info['name'][:30]}{'...' if len(info['name']) > 30 else ''}`\n"
                    f"**SIZE:** {get_size(total)}\n\n"
                    f"**PROCESS:**\n"
                    f"{progress_bar(percentage)}\n\n"
                    f"**SPEED:** {speed}\n"
                    f"**PROGRESS:** {percentage:.1f}%\n\n"
                    f"**Uploaded:** {get_size(current)}\n"
                    f"**ETA:** {eta_str}"
                )

                try:
                    await status_msg.edit(progress_text, reply_markup=cancel_button)
                except Exception:
                    pass

        if file_type == 'video':
            sent_msg = await client.send_video(
                chat_id=status_msg.chat.id,
                video=temp_path,
                caption=caption,
                file_name=info["name"],
                protect_content=True,
                has_spoiler=True,
                supports_streaming=True,
                progress=upload_progress
            )
        elif file_type == 'photo':
            sent_msg = await client.send_photo(
                chat_id=status_msg.chat.id,
                photo=temp_path,
                caption=caption,
                protect_content=True,
                has_spoiler=True,
                progress=upload_progress
            )
        else:
            sent_msg = await client.send_document(
                chat_id=status_msg.chat.id,
                document=temp_path,
                caption=caption,
                file_name=info["name"],
                protect_content=True,
                progress=upload_progress
            )

        await status_msg.edit(
            f"✅ **File uploaded successfully as {file_type.upper()}!**\n\n"
            "⏰ Will be auto-deleted in 12 hours."
        )

        if _slot_open:
            try:
                await _slot.__aexit__(None, None, None)
            except Exception:
                pass
            _slot_open = False

        await asyncio.sleep(43200)
        try:
            await sent_msg.delete()
            await status_msg.delete()
        except Exception:
            pass

    except aiohttp.ClientError as e:
        await status_msg.edit(f"❌ **Download failed:**\n`{str(e)}`")
    except RuntimeError as e:
        await status_msg.edit(f"❌ **HLS download failed (ffmpeg):**\n`{str(e)}`")
    except Exception as e:
        await status_msg.edit(f"❌ **Upload failed:**\n`{str(e)}`")
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
    status_msg = await message.reply("🔍 Fetching file info...")
    await _process_terabox(client, message.from_user.id, status_msg, message.text.strip())


@Client.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client, message: Message):
    """/terabox <link> [password] — same as pasting the link directly, plus
    an optional password for protected shares. Kept as an explicit command
    since start.py's help menu lists it."""
    url = _extract_url_arg(message)
    if not url:
        await message.reply_text(
            "Usage: `/terabox <link> [password]` (or reply to a message containing one)."
        )
        return
    password = _extract_password_arg(message)
    status_msg = await message.reply("🔍 Fetching file info...")
    await _process_terabox(client, message.from_user.id, status_msg, url, password=password)


@Client.on_callback_query(filters.regex(r"^teraf:"))
async def terabox_file_selected(client, callback_query):
    """User tapped one specific file from a folder listing."""
    try:
        _, token, idx_str = callback_query.data.split(":", 2)
        idx = int(idx_str)
    except (ValueError, IndexError):
        await callback_query.answer("Invalid selection.", show_alert=True)
        return

    entry = _TERABOX_FOLDER_CACHE.get(token)
    if not entry or idx >= len(entry["files"]):
        await callback_query.answer("⌛ This selection expired — please resend the link.", show_alert=True)
        return

    await callback_query.answer()
    file_info = entry["files"][idx]
    await callback_query.message.edit(f"🔍 Preparing **{file_info['name']}**...")
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
        await callback_query.answer("Invalid selection.", show_alert=True)
        return

    entry = _TERABOX_QUALITY_CACHE.get(token)
    if not entry:
        await callback_query.answer("⌛ This selection expired — please resend the link.", show_alert=True)
        return

    await callback_query.answer()
    info = dict(entry["info"])  # shallow copy — don't mutate the cached entry
    if quality != "__original__":
        stream_url = entry["qualities"].get(quality)
        if stream_url:
            info["download_link"] = stream_url
            base_name, ext = os.path.splitext(info["name"])
            info["name"] = f"{base_name} [{quality}]{ext or '.mp4'}"

    await callback_query.message.edit(f"🔍 Preparing **{info['name']}**...")
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
        await callback_query.answer("⌛ This selection expired — please resend the link.", show_alert=True)
        return

    await callback_query.answer()
    files = entry["files"]
    await callback_query.message.edit(f"📥 **Queuing {len(files)} files from the folder...**")

    for i, file_info in enumerate(files, start=1):
        status_msg = await callback_query.message.reply(
            f"🔍 [{i}/{len(files)}] Preparing **{file_info['name']}**..."
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
            "Usage: `/terastream <link>` (or reply to a message containing one)."
        )
        return

    status_msg = await message.reply("🔍 Resolving stream link...")
    try:
        info = await get_file_info_from_api(url)
    except Exception as e:
        await status_msg.edit(f"❌ Failed to get file info:\n`{e}`")
        return

    stream_url = info.get("stream_link") or info.get("download_link")
    if not stream_url:
        await status_msg.edit("❌ Could not resolve a stream link for this file.")
        return

    await status_msg.edit(
        f"📄 **File Name:** `{info['name']}`\n"
        f"📦 **Size:** {info['size_str']}\n\n"
        f"🎞️ **Stream/Direct Link:**\n`{stream_url}`\n\n"
        f"⚠️ This link may expire — re-run `/terastream` if it stops working."
    )
