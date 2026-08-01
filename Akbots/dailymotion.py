# Dailymotion Video Support
#
# Ported from a standalone "Dailymotion M3U8 Resolver" script (hits
# Dailymotion's public player-metadata endpoint to get the video's adaptive
# HLS manifest), rewritten to fit this bot's plugin style: reuses
# Akbots/direct_utils.py for download/upload instead of the script's CLI
# main(), adds a proper quality picker (the resolved "auto" stream is an
# HLS master playlist with several bitrate variants, so — same as
# mxplayer.py — yt-dlp reads that manifest to list them and does the actual
# download), and wires in Akbots/link_cache.py + Akbots/task_manager.py so
# repeat links are instant and in-progress downloads show up in
# /queue + /cancel_all like every other plugin here.

import re
import os
import uuid
import shutil
import asyncio
import aiohttp
from typing import Optional
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import YTDL_MAX_FILESIZE
from Akbots.direct_utils import upload_file, E_CHECK, E_CROSS, E_INFO, E_ROCKET, safe_edit
from Akbots.link_cache import try_send_cached
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

PATTERN = re.compile(
    r"(https?://)?(www\.|m\.|touch\.|geo\.)?"
    r"(dailymotion\.com/((video|embed/video|swf/video)/\S+|player(/\w+)?\.html\?\S*video=\S+)"
    r"|dai\.ly/\S+)",
    re.IGNORECASE,
)

METADATA_URL = (
    "https://www.dailymotion.com/player/metadata/video/{vid}"
    "?embedder=https%3A%2F%2Fwww.dailymotion.com&locale=en_US"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.dailymotion.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

DOWNLOAD_DIR = "dailymotion_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# session_id -> {url (page), stream_url, title, thumb, formats,
#                chat_id, reply_to, orig_message}
_SESSIONS = {}


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


def extract_video_id(url_or_id: str) -> Optional[str]:
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()

    if re.match(r'^x[a-zA-Z0-9]+$', url_or_id):
        return url_or_id

    match = re.search(r'/video/([a-zA-Z0-9]+)', url_or_id)
    if match:
        return match.group(1)

    match = re.search(r'[?&]video=([a-zA-Z0-9]+)', url_or_id)
    if match:
        return match.group(1)

    match = re.search(r'dai\.ly/([a-zA-Z0-9]+)', url_or_id)
    if match:
        return match.group(1)

    return None


async def _resolve(url_or_id: str) -> dict:
    """Fetches Dailymotion's player-metadata endpoint and pulls out the
    adaptive ("auto") HLS manifest plus basic video info. Retries once —
    this endpoint occasionally hiccups on the first try."""
    video_id = extract_video_id(url_or_id)
    if not video_id:
        raise ValueError(f"Could not find a Dailymotion video ID in: {url_or_id}")

    api_url = METADATA_URL.format(vid=video_id)
    data = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        for attempt in range(2):
            try:
                async with session.get(
                    api_url,
                    headers={**HEADERS, "Referer": f"https://www.dailymotion.com/video/{video_id}"}
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = await resp.json(content_type=None)
                break
            except Exception as e:
                if attempt == 1:
                    raise ValueError(f"Dailymotion metadata request failed: {e}")
                await asyncio.sleep(1)

    if data.get("error"):
        # Dailymotion returns a JSON error body (private/deleted/geo-blocked
        # videos) with a 200 status, so this needs its own check.
        msg = data["error"].get("title") or data["error"].get("raw_message") or "Unknown error"
        raise ValueError(f"Dailymotion error: {msg}")

    qualities = data.get("qualities") or {}
    auto_streams = qualities.get("auto") or []
    m3u8_url = next(
        (item.get("url") for item in auto_streams if item.get("type") == "application/x-mpegURL"),
        None
    )
    if not m3u8_url:
        raise ValueError("No M3U8 stream found — video may be private, deleted, or region-locked.")

    thumbnails = data.get("thumbnails") or {}
    thumbnail = thumbnails.get("720") or thumbnails.get("480") or thumbnails.get("360")

    return {
        "video_id": video_id,
        "title": data.get("title") or f"dailymotion_{video_id}",
        "duration": data.get("duration"),
        "channel": data.get("channel"),
        "owner": (data.get("owner") or {}).get("screenname"),
        "m3u8_url": m3u8_url,
        "thumbnail": thumbnail,
    }


def _extract_formats_sync(stream_url: str):
    """Reads the HLS master playlist to list its bitrate variants — same
    approach as Akbots/mxplayer.py. Dailymotion's variants are muxed
    audio+video, so (unlike mxplayer/MX Player) there's no separate
    audio-track step needed.

    BUG FIX: Dailymotion's CDN 403s any request that doesn't carry a
    Referer: https://www.dailymotion.com/ header (and a normal browser
    User-Agent) — including requests for the .m3u8 manifest itself, not
    just the page. Without http_headers here, yt-dlp fetched the manifest
    bare and got "[generic] <id>: Unable to download webpage: HTTP Error
    403: Forbidden" every time. Reusing this file's own HEADERS (already
    used for the metadata API call above) fixes it."""
    if yt_dlp is None:
        return []
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "http_headers": HEADERS}) as ydl:
        info = ydl.extract_info(stream_url, download=False)
    formats = info.get("formats") or []

    videos = []
    for f in formats:
        if f.get("vcodec") == "none" or not f.get("height"):
            continue
        fid = str(f.get("format_id"))
        height = f.get("height", 0)
        size = f.get("filesize") or f.get("filesize_approx") or 0
        size_str = f" (~{size / 1024 / 1024:.1f}MB)" if size else ""
        videos.append({"id": fid, "height": height, "label": f"🎬 {height}p{size_str}"})

    videos.sort(key=lambda x: x["height"], reverse=True)
    return videos


def _quality_keyboard(session_id: str, formats):
    rows = [
        [make_button(f["label"], callback_data=f"dmq:{session_id}:{f['id']}", style=_BS.PRIMARY if _BS else None)]
        for f in formats[:20]
    ]
    if not formats:
        rows.append([make_button("🚀 Download (best available)", callback_data=f"dmq:{session_id}:best", style=_BS.SUCCESS if _BS else None)])
    rows.append([make_button("❌ Cancel", callback_data=f"dmcancel:{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} Dailymotion link detected — resolving...</b>", parse_mode=enums.ParseMode.HTML
    )
    if await try_send_cached(client, message, url, status):
        return

    if yt_dlp is None:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} yt-dlp is required for Dailymotion links (pip install yt-dlp).</b>",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        data = await _resolve(url)
        formats = await asyncio.to_thread(_extract_formats_sync, data["m3u8_url"])
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _SESSIONS[session_id] = {
        "page_url": url, "stream_url": data["m3u8_url"], "title": data["title"],
        "thumb": data.get("thumbnail"), "formats": formats,
        "chat_id": message.chat.id, "reply_to": message.id, "orig_message": message,
    }

    text = (
        "<blockquote>"
        f"<b>{E_ROCKET} {data['title'][:80]}</b>\n\n"
        + (f"<b>Available qualities:</b>\n" + "\n".join(f"✅ {f['label']}" for f in formats[:15])
           if formats else "<i>Couldn't list separate qualities — will grab the best stream.</i>")
        + "\n\n<i>Tap a quality below:</i>"
        "</blockquote>"
    )
    keyboard = _quality_keyboard(session_id, formats)

    await status.delete()
    thumb = data.get("thumbnail")
    if thumb:
        try:
            await message.reply_photo(thumb, caption=text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
            return
        except Exception:
            pass
    await message.reply_text(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)


def _download_sync(session: dict, out_dir: str, fmt_id: str):
    fmt = "best" if fmt_id == "best" else f"{fmt_id}+bestaudio/best"
    opts = {
        "quiet": True, "no_warnings": True, "format": fmt,
        "outtmpl": os.path.join(out_dir, "%(title).70s.%(ext)s"),
        "max_filesize": YTDL_MAX_FILESIZE, "merge_output_format": "mp4",
        "http_headers": HEADERS,  # same CDN-403 fix as _extract_formats_sync above
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(session["stream_url"], download=True)
        path = ydl.prepare_filename(info)
        path = os.path.splitext(path)[0] + "." + (info.get("ext") or "mp4")
    return path


async def _start_download(client: Client, cq: CallbackQuery, session_id: str, fmt_id: str):
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Session expired, send the link again.", show_alert=True)

    status = cq.message
    # message.id is only unique within a single chat, not globally — mix in
    # chat.id too so two users' sessions never collide on the same folder.
    session_dir = os.path.join(DOWNLOAD_DIR, f"{cq.message.chat.id}_{session_id}")
    os.makedirs(session_dir, exist_ok=True)

    task_id = None
    try:
        from Akbots import task_manager
        task_id = task_manager.register(
            cq.from_user.id, asyncio.current_task(),
            f"Dailymotion: {session.get('title', 'video')[:40]}"
        )
    except Exception:
        task_id = None

    try:
        async with task_manager.queue_slot(cq.from_user.id, status_msg=status):
            if status.photo:
                await status.edit_caption(f"<b>{E_ROCKET} Downloading...</b>", parse_mode=enums.ParseMode.HTML)
            else:
                await safe_edit(status.edit_text, f"<b>{E_ROCKET} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

            # BUG FIX: Dailymotion's m3u8 manifest URL is a time-limited signed
            # link. It was resolved once (in _handle) when the quality picker
            # was first shown, and if the user takes even a little while to tap
            # a quality button, that cached URL can expire by download time —
            # surfacing as "unable to download video data: HTTP Error 404: Not
            # Found" even though the SAME link worked fine seconds earlier for
            # listing qualities. Re-resolving right before the real download
            # gets a fresh, valid signed URL every time instead of reusing a
            # possibly-stale one from the session.
            try:
                fresh = await _resolve(session["page_url"])
                session["stream_url"] = fresh["m3u8_url"]
            except Exception:
                pass  # fall back to the cached URL if a fresh resolve fails

            filepath = await asyncio.to_thread(_download_sync, session, session_dir, fmt_id)
            if not os.path.exists(filepath):
                raise FileNotFoundError("Download finished but file was not found (likely size limit).")

            title = session["title"]
            quality_label = next(
                (f["label"] for f in session.get("formats", []) if f["id"] == fmt_id),
                "Best available"
            )
            caption = f"<blockquote><b>{E_CHECK} {title[:100]}</b></blockquote>"

            if status.photo:
                text_status = await client.send_message(
                    session["chat_id"], "<b>Preparing upload...</b>",
                    reply_to_message_id=session["reply_to"], parse_mode=enums.ParseMode.HTML
                )
                try:
                    await status.delete()
                except Exception:
                    pass
                status = text_status

            await upload_file(
                client, session["orig_message"], filepath, status, caption,
                file_name=title, quality=quality_label, cache_url=session.get("page_url"),
            )
    except Exception as e:
        err_text = f"<b>{E_CROSS} Download failed:</b>\n<code>{e}</code>"
        try:
            if status.photo:
                await status.edit_caption(err_text, parse_mode=enums.ParseMode.HTML)
            else:
                await safe_edit(status.edit_text, err_text, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(cq.from_user.id, task_id)
            except Exception:
                pass
        _SESSIONS.pop(session_id, None)
        shutil.rmtree(session_dir, ignore_errors=True)


@Client.on_callback_query(filters.regex(r"^dmq:([a-f0-9]+):(\S+)$"))
async def _pick_quality(client: Client, cq: CallbackQuery):
    session_id, fmt_id = cq.matches[0].group(1), cq.matches[0].group(2)
    await cq.answer("Starting download...")
    await _start_download(client, cq, session_id, fmt_id)


@Client.on_callback_query(filters.regex(r"^dmcancel:([a-f0-9]+)$"))
async def _cancel(client: Client, cq: CallbackQuery):
    _SESSIONS.pop(cq.matches[0].group(1), None)
    await cq.message.delete()
    await cq.answer("Cancelled.")


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def dailymotion_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("dailymotion") & filters.private)
async def dailymotion_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/dailymotion &lt;Dailymotion or dai.ly link&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    raw = message.command[1]
    url = extract_url(raw) or raw
    await _handle(client, message, url)
