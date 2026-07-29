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
API_KEY = "sk_f3be9c2f948678a13a1e5238c9f46019"

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

async def _get_file_info_xapiverse(share_url: str) -> dict:
    """
    Fetch file information from the xAPIverse API and process its structural format
    """
    try:
        payload = {
            "url": share_url
        }
        headers = {
            "Content-Type": "application/json",
            "xAPIverse-Key": API_KEY
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(API_BASE_URL, json=payload, headers=headers, timeout=30) as response:
                response.raise_for_status()
                data = await response.json()

                # ✅ Targets the "list" array inside your new API response schema
                if data.get("status") == "success" and "list" in data and len(data["list"]) > 0:
                    file_info = data["list"][0]

                    # Target 'normal_dlink' explicitly as requested
                    download_link = file_info.get("normal_dlink", "")

                    if download_link:
                        return {
                            "name": file_info.get("name", "download"),
                            "download_link": download_link,
                            "size_str": file_info.get("size_formatted", "Unknown"),
                            "size_bytes": file_info.get("size", 0),
                            "thumb": file_info.get("thumbnail", ""),
                            "stream_link": file_info.get("stream_url", "")
                        }

                raise ValueError("Invalid API response or missing normal_dlink")

    except aiohttp.ClientError as e:
        raise ValueError(f"API request failed: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error parsing API response: {str(e)}")


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
    }


async def _get_file_info_beer(share_url: str) -> dict:
    try:
        return await asyncio.to_thread(_beer_resolve_sync, share_url)
    except Exception as e:
        raise ValueError(f"terabox.beer fallback failed: {str(e)}")


async def get_file_info_from_api(share_url: str) -> dict:
    """Public entry point used by _process_terabox/terastream_command —
    tries the primary xAPIverse resolver first, and falls back to the
    terabox.beer resolver (no API key needed) if that fails for any
    reason, so a dead/rate-limited key doesn't take TeraBox support down
    entirely."""
    try:
        return await _get_file_info_xapiverse(share_url)
    except Exception as primary_err:
        try:
            return await _get_file_info_beer(share_url)
        except Exception as fallback_err:
            raise ValueError(
                f"Both resolvers failed — xAPIverse: {primary_err} | terabox.beer: {fallback_err}"
            )


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


async def _process_terabox(client, message: Message, url: str):
    """Shared core: used by the plain-link auto-detect handler below as
    well as the explicit /terabox command (start.py advertises both in
    its help menu, so both need to actually work)."""
    user_id = message.from_user.id

    if IS_VERIFY and not await is_verified(user_id):
        verify_url = await build_verification_link(client.me.username, user_id)
        buttons = [
            [
                make_button("✅ Verify Now", url=verify_url, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
                make_button("📖 Tutorial", url=HOW_TO_VERIFY, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)
            ]
        ]
        await message.reply_text(
            "🔐 You must verify before using this command.\n\n⏳ Verification lasts for 12 hours.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    status_msg = await message.reply("🔍 Fetching file info...")

    try:
        info = await get_file_info_from_api(url)
    except Exception as e:
        await status_msg.edit(f"❌ Failed to get file info:\n`{e}`")
        return

    if not info["download_link"]:
        await status_msg.edit("❌ Could not retrieve download link")
        return

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
            f"📄 **File Name:** `{info['name']}`\n"
            f"📦 **File Size:** {info['size_str']}\n"
            f"🔗 **Source:** [TeraBox Link]({url})\n\n"
            f"⚡ Powered by @MrMNTG"
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
                chat_id=message.chat.id,
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
                chat_id=message.chat.id,
                photo=temp_path,
                caption=caption,
                protect_content=True,
                has_spoiler=True,
                progress=upload_progress
            )
        else:
            sent_msg = await client.send_document(
                chat_id=message.chat.id,
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


@Client.on_message(
    filters.text & filters.private & filters.regex(TERABOX_REGEX) & ~filters.regex(r"^/"),
    group=1,  # same priority as the other dedicated site handlers in ytdl.py
)
async def handle_terabox(client, message: Message):
    """Fires on any bare TeraBox link pasted into the chat."""
    await _process_terabox(client, message, message.text.strip())


@Client.on_message(filters.command("terabox") & filters.private)
async def terabox_command(client, message: Message):
    """/terabox <link> — same as pasting the link directly, kept as an
    explicit command since start.py's help menu lists it."""
    url = _extract_url_arg(message)
    if not url:
        await message.reply_text(
            "Usage: `/terabox <link>` (or reply to a message containing one)."
        )
        return
    await _process_terabox(client, message, url)


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
