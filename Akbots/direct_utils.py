"""
Shared helpers for direct-link downloader plugins (catbox, pixeldrain, gofile,
mediafire, streamtape, terabox, mega, gdrive, torrent, gallery).

Every plugin extracts a direct download URL (or list of files) on its own,
then calls into this module to actually stream the download to disk,
build a thumbnail/metadata for videos, and upload the result to Telegram.
"""

import os
import re
import html
import time
import uuid
import random
import asyncio
import contextlib
import subprocess
import aiohttp
import requests
from pyrogram import Client, enums, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_BOLT   = '⚡️'
E_SIZE   = '💯'
E_CLOCK  = '⌛'
E_ETA    = '💡'


async def wait_for_reply(client, chat_id: int, user_id: int, timeout: int = 60):
    """Waits for the next message from `user_id` in `chat_id`.

    This replaces `client.listen()` (a pyromod/kurigram-only conversation
    helper) with plain event-handler logic that works on ANY Pyrogram-
    compatible client — kurigram, pyrogram, pyrofork, whichever happens to
    be installed. `client.listen()` throwing "'Bot' object has no attribute
    'listen'" means the installed library build doesn't have that
    extension; this function needs nothing extra from the library at all.

    Raises asyncio.TimeoutError if nothing arrives within `timeout` seconds
    — same contract client.listen() had, so existing `except
    asyncio.TimeoutError:` blocks around the old calls don't need to change.
    """
    future = asyncio.get_event_loop().create_future()

    async def _on_reply(_, message):
        if not future.done():
            future.set_result(message)

    handler = MessageHandler(
        _on_reply, filters.chat(chat_id) & filters.user(user_id)
    )
    client.add_handler(handler, group=-1)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    finally:
        with contextlib.suppress(Exception):
            client.remove_handler(handler, group=-1)

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}


class _AntiBotBlocked(ValueError):
    """Raised internally by _stream_download_inner when a download link
    returns an anti-bot challenge page (Cloudflare, etc.) instead of the
    actual file. A ValueError subclass so every existing `except ValueError`
    cleanup path (removing the partial file) still works unchanged; caught
    separately by stream_download() to trigger one automatic bypass+retry
    before it's ever shown to the user."""
    pass


def _is_antibot_block(status: int, headers: dict, body_sample: str) -> bool:
    """Lazily defers to Akbots.cf_bypass's challenge-detection so this
    module doesn't hard-depend on DrissionPage being installed — if
    cf_bypass isn't importable for any reason, this just says 'no', and
    downloads behave exactly as before this feature existed."""
    try:
        from Akbots.cf_bypass import looks_like_challenge
    except ImportError:
        return False
    return looks_like_challenge(status, headers, body_sample)

def fmt_bytes(n):
    if not n:
        return "0B"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def fmt_duration(seconds):
    if seconds is None:
        return "0s"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def draw_bar(percent, length=12, filled="▓", empty="░"):
    percent = max(0, min(100, percent or 0))
    filled_n = round(length * percent / 100)
    return filled * filled_n + empty * (length - filled_n)


def fmt_hms(seconds):
    """MM:SS, or H:MM:SS once it runs past an hour. Used for the boxed
    progress card's Duration/ETA rows instead of fmt_duration()'s '1m 2s'
    style, since the box wants fixed-width clock formatting."""
    if seconds is None:
        return "--:--"
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _render_progress_box(header, rows, footer=None):
    lines = [header, "", "╭━━━━❰Progress❱━➣"]
    lines += [f"┣⪼ {row}" for row in rows]
    lines.append("╰━━━━━━━━━━━━━━━➣")
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def make_upload_progress(status, label="Uploading to Telegram", task_id=None,
                          file_name=None, duration=None, quality=None):
    """Returns an async progress callback for pyrogram's send_video/send_audio/
    send_document/send_photo (they accept `progress=`). Pyrogram awaits it
    directly since it's a coroutine function, calling it with (current, total)
    throughout the upload. Throttled by time + whole-percent change so it
    doesn't hit Telegram's edit rate limit on fast/local uploads.

    duration can be given as seconds (int/float) or an already-formatted
    'HH:MM:SS' string.
    """
    state = {"last_edit": 0.0, "last_pct": -1, "last_bytes": 0, "last_time": time.time(),
              "start_time": time.time()}

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
        elapsed_secs = now - state["start_time"]

        bar = draw_bar(pct, length=10, filled="⬢", empty="⬡")
        header = "<b>📤 Fast Upload via Main Engine</b>"
        if task_id:
            header += f"  •  <code>{task_id}</code>"

        rows = []
        if file_name:
            rows.append(f"🎬 <b>File:</b> {file_name}")
        if duration:
            rows.append(f"⏱ <b>Duration:</b> {duration if isinstance(duration, str) else fmt_hms(duration)}")
        if quality:
            rows.append(f"🎞 <b>Quality:</b> {quality}")
        rows.append(f"[{bar}]")
        rows.append(f"✅ {pct:.1f}%")
        rows.append(f"💾 {fmt_bytes(current)} / {fmt_bytes(total)}")
        rows.append(f"⚡ {fmt_bytes(speed_bps)}/s")
        rows.append(f"🕐 Elapsed: {fmt_hms(elapsed_secs)}")
        rows.append(f"⏳ ETA: {fmt_hms(eta_secs)}")

        await _status_edit(status, _render_progress_box(header, rows, footer="⚡ Hyper Upload Connections Active"))

    return _progress


def format_progress(percent, speed_bps=None, done_bytes=None, total_bytes=None,
                     elapsed_secs=None, eta_secs=None, title="Downloading Video",
                     file_name=None, duration=None, quality=None) -> str:
    """Renders the standard boxed progress card used across every downloader:

    📥 Fast downloading via Main Engine

    ╭━━━━❰Progress❱━➣
    ┣⪼ 🎬 File: ...
    ┣⪼ ⏱ Duration: 01:14:09
    ┣⪼ 🎞 Quality: 1080p
    ┣⪼ [⬢⬢⬢⬢⬢⬢⬢⬡⬡⬡]
    ┣⪼ ✅ 76.6%
    ┣⪼ 💾 done / total
    ┣⪼ ⚡ 11.16 MB/s
    ┣⪼ ⏳ 00:34
    ╰━━━━━━━━━━━━━━━➣

    ⚡ Hyper Download Connections Active

    file_name/duration/quality rows are only shown when the caller passes
    them; existing callers that don't know these yet keep working unchanged.
    duration can be seconds (int/float) or an already-formatted string.
    title is accepted for backward compatibility but no longer shown - the
    header is always the generic "Fast downloading via Main Engine" line
    now, same across every plugin.
    """
    pct = percent if percent is not None else 0.0
    bar = draw_bar(pct, length=10, filled="⬢", empty="⬡")
    speed = f"{fmt_bytes(speed_bps)}/s" if speed_bps else "0B/s"

    header = "<b>📥 Fast downloading via Main Engine</b>"
    rows = []
    if file_name:
        rows.append(f"🎬 <b>File:</b> {file_name}")
    if duration:
        rows.append(f"⏱ <b>Duration:</b> {duration if isinstance(duration, str) else fmt_hms(duration)}")
    if quality:
        rows.append(f"🎞 <b>Quality:</b> {quality}")
    rows.append(f"[{bar}]")
    rows.append(f"✅ {pct:.1f}%")
    if done_bytes is not None or total_bytes is not None:
        rows.append(f"💾 {fmt_bytes(done_bytes) if done_bytes is not None else '?'} / "
                    f"{fmt_bytes(total_bytes) if total_bytes else '?'}")
    rows.append(f"⚡ {speed}")
    if elapsed_secs is not None:
        rows.append(f"🕐 Elapsed: {fmt_hms(elapsed_secs)}")
    rows.append(f"⏳ ETA: {fmt_hms(eta_secs)}")

    return _render_progress_box(header, rows, footer="⚡ Hyper Download Connections Active")
def fmt_count(n):
    if n is None:
        return None
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return None


def fmt_upload_date(date_str):
    if not date_str or len(date_str) != 8:
        return None
    return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"


def format_forward_caption(msg, credit: str = None) -> str:
    """Builds the same style of rich metadata caption as format_media_caption(),
    but sourced from a Pyrogram Message (used for the restricted-content /save
    flow, which forwards real Telegram posts rather than yt-dlp results).

    📹 <first line of original caption, if any>
    👤 <source chat/channel name>
    ⏱ <duration>          (video/audio only)
    👁 <channel post views>  (only present on channel posts)
    📅 <post date>

    Downloaded by @<credit>
    """
    lines = []

    title = (msg.caption or "").strip().split("\n")[0][:100] if msg.caption else None
    if title:
        lines.append(f"📹 <b>{title}</b>")

    uploader = None
    if getattr(msg, "forward_from_chat", None):
        uploader = msg.forward_from_chat.title
    elif getattr(msg, "chat", None):
        uploader = msg.chat.title or msg.chat.username
    if uploader:
        lines.append(f"👤 {uploader}")

    duration = None
    if getattr(msg, "video", None):
        duration = msg.video.duration
    elif getattr(msg, "audio", None):
        duration = msg.audio.duration
    if duration:
        lines.append(f"⏱ {fmt_duration(duration)}")

    views = fmt_count(getattr(msg, "views", None))
    if views:
        lines.append(f"👁 {views}")

    post_date = getattr(msg, "forward_date", None) or getattr(msg, "date", None)
    if post_date:
        try:
            lines.append(f"📅 {post_date.strftime('%Y-%m-%d')}")
        except Exception:
            pass

    caption = "\n".join(lines) if lines else ""
    if credit:
        caption = (caption + "\n\n" if caption else "") + f"Downloaded by @{credit}"
    return caption


def format_media_caption(info: dict, credit: str = None) -> str:
    """Builds the rich metadata caption (title/channel/views/etc.) from a
    yt-dlp info dict. Only fields that are actually present are shown, since
    not every site (or every video) exposes all of them.

    📹 <title>
    👤 <uploader> @<handle>
    👥 <follower count>
    ⏱ <duration>
    👁 <views> | 👍 <likes> | 💬 <comments>
    🏷 <category>
    📅 <upload date>

    Downloaded by @<credit>

    This stays short by design (Telegram media captions are capped at 1024
    characters). The full raw description/caption text (Instagram/Facebook
    posts especially can run to a full paragraph) is NOT included here —
    it's sent as a separate reply message by send_full_description() below,
    which has room for up to 4096 characters.
    """
    if not info:
        info = {}
    lines = []

    description = (info.get("description") or "").strip()

    # Different extractors populate different fields — Instagram/Facebook
    # often leave "title" empty and put the caption text in "description"
    # instead, so fall through several fields rather than showing nothing.
    title = (
        info.get("title")
        or info.get("fulltitle")
        or (description.split("\n")[0][:100] if description else None)
        or None
    )
    if title:
        lines.append(f"📹 <b>{title}</b>")

    uploader = (
        info.get("uploader") or info.get("channel") or info.get("uploader_id")
    )
    handle = info.get("uploader_id") or info.get("channel_id") or info.get("channel_handle")
    if uploader or handle:
        who = uploader or ""
        if handle and str(handle) != str(uploader):
            handle_str = f"@{handle}" if not str(handle).startswith("@") else str(handle)
            who = f"{who} {handle_str}".strip()
        lines.append(f"👤 {who}")

    followers = fmt_count(info.get("channel_follower_count"))
    if followers:
        lines.append(f"👥 {followers}")

    duration = info.get("duration")
    if duration:
        lines.append(f"⏱ {fmt_duration(duration)}")

    views = fmt_count(info.get("view_count"))
    likes = fmt_count(info.get("like_count"))
    comments = fmt_count(info.get("comment_count"))
    stats = [f"👁 {v}" for v in [views] if v]
    if likes:
        stats.append(f"👍 {likes}")
    if comments:
        stats.append(f"💬 {comments}")
    if stats:
        lines.append(" | ".join(stats))

    categories = info.get("categories") or ([info["category"]] if info.get("category") else None)
    if categories:
        lines.append(f"🏷 {categories[0]}")

    date_str = fmt_upload_date(info.get("upload_date"))
    if date_str:
        lines.append(f"📅 {date_str}")

    caption = "\n".join(lines) if lines else f"<b>{E_CHECK} Downloaded via yt-dlp</b>"
    if credit:
        caption += f"\n\nDownloaded by @{credit}"
    return f"<blockquote>{caption}</blockquote>"


# In-memory store for descriptions waiting to be revealed by a button tap.
# Keyed by a short random token (rather than chat_id:message_id) so the
# button can be built and included in the *original* send_video/send_audio
# call — no separate edit_message_reply_markup round-trip after upload.
# Popped (removed) the moment it's shown, so this never grows unbounded.
_PENDING_DESCRIPTIONS = {}


def build_description_button(info: dict, credit: str = None):
    """Returns an InlineKeyboardMarkup with a single '📄 Full Description'
    button to pass straight into send_video/send_audio/send_photo's
    reply_markup= — the button then rides along on the very same message
    as the media, right below the 📅 upload-date line of the caption,
    instead of being bolted on afterward.

    Tapping it reveals the full raw description/caption text (Instagram/
    Facebook posts especially can run to a full paragraph) as a reply,
    split across multiple <blockquote> messages of up to 4096 characters
    each if it's that long — since Telegram media captions themselves are
    capped at 1024 characters.

    Returns None if there's no description, or it's trivially short
    (already fully covered by the title line in the media caption) — in
    that case, just pass reply_markup=None / omit it as normal.
    """
    description = ((info or {}).get("description") or "").strip()
    if not description or len(description) < 100:
        return None

    text = description
    if credit:
        text += f"\n\nDownloaded by @{credit}"

    token = uuid.uuid4().hex[:12]
    _PENDING_DESCRIPTIONS[token] = text

    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📄 Full Description", callback_data=f"showdesc:{token}")
    ]])


async def send_full_description(client, chat_id, reply_to_message_id, info: dict, credit: str = None):
    """Back-compat wrapper: attaches the description button to an
    already-sent message via an edit, for any caller that hasn't switched
    to build_description_button() + reply_markup= at send time yet.
    Prefer build_description_button() for new call sites — it avoids this
    extra edit round-trip entirely.
    """
    markup = build_description_button(info, credit=credit)
    if not markup:
        return
    try:
        await client.edit_message_reply_markup(chat_id, reply_to_message_id, reply_markup=markup)
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^showdesc:"))
async def _show_full_description_cb(client, callback_query):
    token = callback_query.data.split(":", 1)[1]
    text = _PENDING_DESCRIPTIONS.pop(token, None)
    if not text:
        return await callback_query.answer(
            "Description no longer available (bot may have restarted).", show_alert=True
        )

    # Raw caption text can contain <, >, & etc. — escape before it goes
    # inside HTML tags, then wrap each chunk in its own <blockquote> so
    # every message stands as a self-contained quoted block.
    escaped = html.escape(text)
    tag_overhead = len("<blockquote></blockquote>")
    budget = 4096 - tag_overhead
    chunks = [escaped[i:i + budget] for i in range(0, len(escaped), budget)] or [escaped]
    for i, chunk in enumerate(chunks):
        try:
            await client.send_message(
                callback_query.message.chat.id, f"<blockquote>{chunk}</blockquote>",
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=callback_query.message.id if i == 0 else None,
            )
        except Exception:
            pass

    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback_query.answer()



VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.webm', '.3gp', '.flv', '.m4v')
AUDIO_EXTS = ('.mp3', '.m4a', '.wav', '.flac', '.ogg', '.opus')
PHOTO_EXTS = ('.jpg', '.jpeg', '.png', '.webp')

# Telegram bot accounts can upload up to 2GB per file (the 4GB cap is only
# for Premium *user* accounts, not bots). Leave a safety margin below that
# so a part with metadata/rounding never tips over the real server limit.
SPLIT_SIZE = int(1.9 * 1024 * 1024 * 1024)  # 1.9GB


async def _status_edit(status, text, parse_mode=enums.ParseMode.HTML):
    """Edits a status message whether it's a text message or a photo
    message (in which case the caption has to be edited instead)."""
    if status is None:
        return
    try:
        if getattr(status, "photo", None):
            await status.edit_caption(text, parse_mode=parse_mode)
        else:
            await status.edit_text(text, parse_mode=parse_mode)
    except Exception:
        pass  # e.g. "message not modified" — safe to ignore


E_CLOUD = "☁️"


async def play_download_intro(status, frames: int = 4, delay: float = 0.5):
    """Briefly cycles a 'Downloading...' animation on the status message —
    a short branded intro shown the instant a link is accepted, BEFORE the
    real byte-level progress card (format_progress) takes over. Purely
    cosmetic and self-contained (no external stickers/GIFs/file_ids
    needed), so it works out of the box on any deployment.

    Called automatically by stream_download() — plugins don't need to call
    this themselves unless they want the same effect somewhere else (e.g.
    right before upload_file(), as a matching 'Uploading...' intro — pass
    label=\"Uploading\" for that case).
    """
    if status is None:
        return
    try:
        for i in range(frames):
            dots = "." * ((i % 3) + 1)
            await _status_edit(
                status,
                f"<blockquote>{E_CLOUD} <b>Downloading{dots}</b>\n"
                f"<i>Starting download, please wait</i></blockquote>",
            )
            await asyncio.sleep(delay)
    except Exception:
        pass


async def play_upload_intro(status, frames: int = 4, delay: float = 0.5):
    """Same as play_download_intro() but for the upload side — plugins that
    call upload_file() directly (without a preceding stream_download())
    can call this first for a matching 'Uploading...' intro."""
    if status is None:
        return
    try:
        for i in range(frames):
            dots = "." * ((i % 3) + 1)
            await _status_edit(
                status,
                f"<blockquote>{E_CLOUD} <b>Uploading{dots}</b>\n"
                f"<i>Starting upload, please wait</i></blockquote>",
            )
            await asyncio.sleep(delay)
    except Exception:
        pass


async def split_file(path: str, status=None, chunk_size: int = SPLIT_SIZE):
    """Splits `path` into <=chunk_size parts, returning a list of part paths
    (the original file is left untouched — caller is responsible for
    removing it once all parts are uploaded).

    Video files are split with ffmpeg's segment muxer using stream copy, so
    each part stays a valid, independently playable video (much nicer than a
    raw byte-split, which would corrupt every part except the first). Any
    other file type is split as raw bytes, since there's no container to
    keep valid — the parts just get concatenated back on the receiving end.
    """
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)
    if size <= chunk_size:
        return [path]

    if ext in VIDEO_EXTS:
        parts = await asyncio.to_thread(_split_video_ffmpeg, path, chunk_size, ext)
        if parts:
            return parts
        # fall through to raw split if ffmpeg segmenting failed for some reason

    return await asyncio.to_thread(_split_raw_bytes, path, chunk_size)


def _split_video_ffmpeg(path: str, chunk_size: int, ext: str):
    """Uses ffmpeg's segment muxer (stream copy, no re-encode) to cut a video
    into roughly chunk_size-sized, independently playable parts. Segment
    duration is estimated from the average bitrate so parts land close to
    chunk_size rather than being wildly over/under."""
    size = os.path.getsize(path)
    duration, _, _ = get_video_metadata(path)
    if not duration:
        return None

    bitrate = size / duration  # bytes/sec
    segment_time = max(30, int(chunk_size / bitrate * 0.97))  # 3% safety margin

    base, _ = os.path.splitext(path)
    out_pattern = f"{base}.part%03d{ext}"
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
             "-c", "copy", "-map", "0", "-f", "segment",
             "-segment_time", str(segment_time), "-reset_timestamps", "1",
             "-y", out_pattern],
            timeout=1800, check=True
        )
    except Exception:
        return None

    parts = sorted(
        f for f in os.listdir(os.path.dirname(path) or ".")
        if os.path.basename(f).startswith(os.path.basename(base) + ".part")
    )
    parts = [os.path.join(os.path.dirname(path), p) for p in parts]
    parts = [p for p in parts if os.path.exists(p) and os.path.getsize(p) > 0]

    # Any single part still over the limit (e.g. one huge keyframe interval)
    # falls back to a raw byte split for just that part — better an oversized
    # container split than a failed upload.
    fixed = []
    for p in parts:
        if os.path.getsize(p) > chunk_size:
            fixed.extend(_split_raw_bytes(p, chunk_size))
            os.remove(p)
        else:
            fixed.append(p)
    return fixed or None


def _split_raw_bytes(path: str, chunk_size: int):
    parts = []
    base = path
    idx = 0
    with open(path, "rb") as src:
        while True:
            data = src.read(chunk_size)
            if not data:
                break
            idx += 1
            part_path = f"{base}.{idx:03d}"
            with open(part_path, "wb") as dst:
                dst.write(data)
            parts.append(part_path)
    return parts


def make_output_folder(service: str) -> str:
    folder = os.path.join("downloads", service)
    os.makedirs(folder, exist_ok=True)
    return folder


def safe_filename(name: str, fallback: str) -> str:
    name = (name or "").strip().strip("/\\")
    if not name:
        return fallback
    # strip characters that break filesystem paths
    return "".join(c for c in name if c not in '\\/:*?"<>|') or fallback


def make_ffmpeg_progress_parser(total_duration: float, title: str = "Processing..."):
    """Returns a parse_line(line, elapsed) function for run_subprocess_with_progress
    that reads ffmpeg's own stderr progress ('time=00:01:23.45 speed=1.2x ...')
    and renders it with format_progress(), using total_duration (seconds,
    from get_video_metadata()) to compute a percentage."""
    time_re = re.compile(r'time=(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)')
    speed_re = re.compile(r'speed=\s*([\d.]+)x')

    def parse_line(line, elapsed):
        m = time_re.search(line)
        if not m:
            return None
        h, mi, s = m.groups()
        done_secs = int(h) * 3600 + int(mi) * 60 + float(s)
        pct = max(0, min(100, (done_secs / total_duration * 100) if total_duration else 0))
        speed_m = speed_re.search(line)
        speed_x = float(speed_m.group(1)) if speed_m else None
        eta = ((total_duration - done_secs) / speed_x) if (speed_x and total_duration and speed_x > 0) else None
        bar = draw_bar(pct)
        text = (
            f"<b>{E_BOLT} {title}</b>\n\n"
            f"<b>Progress:</b> <code>{bar}</code> {pct:.1f}%\n"
            f"{E_CLOCK} <b>Processed:</b> {fmt_duration(done_secs)} of {fmt_duration(total_duration)}\n"
            f"{E_ROCKET} <b>Speed:</b> {speed_x or 0:.2f}x\n"
            f"{E_ETA} <b>ETA:</b> {fmt_duration(eta)}"
        )
        return text

    return parse_line


async def run_subprocess_with_progress(cmd, status, label, parse_line, interval: float = 3.0,
                                        user_id: int = None, queue_label: str = None):
    """Runs `cmd`, streaming its combined stdout live instead of buffering the
    whole output with communicate() (which is why mega/torrent/gallery had no
    progress before — communicate() only returns once the process exits).

    Tools like aria2c/megatools redraw their progress line in place using
    carriage returns ('\\r') rather than newlines, so we read raw bytes and
    split on both '\\r' and '\\n' to catch every update.

    parse_line(line: str, elapsed: float) -> str | None
        Given one output line and seconds elapsed since the process started,
        return an HTML status text to show (typically built with
        format_progress()), or None to ignore that line. Edits are throttled
        to `interval` seconds so we don't hit Telegram's rate limit.

    user_id / queue_label: if user_id is given, this call registers itself
    with task_manager (same as stream_download()) so it shows up in /queue
    and can be stopped via /cancel_all.

    If the awaiting asyncio.Task gets cancelled (e.g. via /cancel_all, or
    the bot shutting down) the child process is explicitly killed instead of
    being left running as an orphan in the background — cancelling the
    *coroutine* alone does not stop a subprocess it spawned.

    Returns (returncode, last_stderr_tail).
    """
    task_id = None
    _slot = None
    if user_id is not None:
        try:
            from Akbots import task_manager
            task_id = task_manager.register(user_id, asyncio.current_task(), queue_label or label)
        except Exception:
            task_id = None
        try:
            _slot = task_manager.queue_slot(user_id, status_msg=status)
            await _slot.__aenter__()
        except Exception:
            _slot = None

    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )

    buf = b""
    last_edit = 0.0
    tail = b""

    try:
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            tail = (tail + chunk)[-2000:]  # keep a tail in case we need it for an error message

            while True:
                cut = -1
                for sep in (b"\r", b"\n"):
                    idx = buf.find(sep)
                    if idx != -1 and (cut == -1 or idx < cut):
                        cut = idx
                if cut == -1:
                    break
                line, buf = buf[:cut], buf[cut + 1:]
                elapsed = time.monotonic() - start
                try:
                    text = parse_line(line.decode(errors="replace").strip(), elapsed)
                except Exception:
                    text = None
                if text:
                    now = time.monotonic()
                    if status is not None and now - last_edit >= interval:
                        last_edit = now
                        await _status_edit(status, text)

        await proc.wait()
        return proc.returncode, tail.decode(errors="replace").strip()
    except asyncio.CancelledError:
        # The task was cancelled (e.g. /cancel_all) — without this, aria2c/
        # the child process keeps running in the background forever since
        # cancelling our coroutine does nothing to the OS process it spawned.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    finally:
        if proc.returncode is None:
            # Any other unexpected exit from this function (e.g. status.edit
            # raising) — make sure we never leak a running child process.
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        if _slot is not None:
            try:
                await _slot.__aexit__(None, None, None)
            except Exception:
                pass
        if task_id is not None:
            from Akbots import task_manager
            task_manager.unregister(user_id, task_id)


_HTML_SIGNATURES = ("<!doctype", "<html", "<head", "<body", "<?xml")


def _looks_like_html_error(first_bytes: bytes) -> bool:
    """Detects an HTML/error page masquerading as a downloaded file — the
    root cause of 'file saves as HTML' reports. A real direct-file host
    virtually never returns a payload starting with these tags."""
    head = first_bytes[:300].lstrip().lower()
    return any(head.startswith(sig.encode()) or sig.encode() in head[:150] for sig in _HTML_SIGNATURES)


def _extract_html_reason(first_bytes: bytes, http_status: int = None) -> str:
    """Pulls the most useful one-line reason out of an HTML error page,
    instead of always showing the same generic guess. Tries, in order:
    the page's <title>, a common status phrase (404/403/expired/etc found
    in the body), then falls back to the HTTP status if given, then a
    generic message as the last resort — so the user sees exactly what
    the server said instead of the same boilerplate every time."""
    import re as _re
    text = first_bytes[:4096].decode("utf-8", errors="ignore")

    m = _re.search(r"<title[^>]*>(.*?)</title>", text, _re.IGNORECASE | _re.DOTALL)
    if m:
        title = _re.sub(r"\s+", " ", m.group(1)).strip()
        if title and title.lower() not in ("", "untitled", "document"):
            return f"server returned a page titled \"{title[:120]}\""

    lower = text.lower()
    phrase_map = [
        ("access denied", "access denied"),
        ("forbidden", "access forbidden (403)"),
        ("not found", "file not found (404)"),
        ("link has expired", "link has expired"),
        ("expired", "link has expired"),
        ("has been removed", "file has been removed"),
        ("log in", "requires login"),
        ("login required", "requires login"),
        ("captcha", "blocked by a CAPTCHA/anti-bot check"),
        ("rate limit", "rate-limited by the server"),
        ("too many requests", "rate-limited by the server"),
    ]
    for needle, reason in phrase_map:
        if needle in lower:
            return reason

    if http_status:
        return f"server returned HTTP {http_status} with an HTML page instead of the file"

    return "server returned an HTML/error page instead of the actual file"


async def stream_download(url: str, dest: str, status, label: str,
                           headers: dict = None, timeout: int = 300,
                           user_id: int = None, file_name: str = None,
                           auth: tuple = None, show_intro: bool = True) -> int:
    """Streams url -> dest with periodic status.edit_text progress updates.
    Returns total bytes downloaded. Raises ValueError on HTTP/network failure
    OR when the response is clearly an HTML/error page rather than the file
    that was requested (expired link, login wall, host down, wrong URL,
    rate-limited, etc.) — previously that page got saved as-is with the
    target filename, which is why downloads were coming out as HTML.

    If the link turns out to be sitting behind Cloudflare/an anti-bot
    challenge instead of a normal error, this automatically calls
    Akbots.cf_bypass to get past it (browser bypass, cached per hostname)
    and retries the download once with the resulting cookies before
    raising anything — most callers never need to know this happened.
    If cf_bypass isn't available or the bypass fails, this falls back to
    raising the same ValueError as before, so nothing regresses.

    If user_id is given, this registers the currently-running task with
    task_manager so it shows up in /queue and can be stopped via
    /cancel_all — callers just need to pass their message.from_user.id,
    no extra bookkeeping required.

    file_name is optional — pass it when already known (most callers parse
    it out before downloading) so the progress box shows a File row.

    auth is an optional (username, password) tuple for HTTP Basic Auth —
    for private/login-walled direct links (e.g. a seedbox's HTTP file
    browser) that 401/403 without credentials."""
    if show_intro:
        await play_download_intro(status)

    headers = dict(headers or DEFAULT_HEADERS)
    start = time.monotonic()
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    auth_obj = aiohttp.BasicAuth(*auth) if auth and auth[0] else None

    task_id = None
    _slot = None
    if user_id is not None:
        try:
            from Akbots import task_manager
            task_id = task_manager.register(user_id, asyncio.current_task(), label)
        except Exception:
            task_id = None
        try:
            _slot = task_manager.queue_slot(user_id, status_msg=status)
            await _slot.__aenter__()
        except Exception:
            _slot = None

    try:
        try:
            return await _stream_download_inner(
                url, dest, status, label, headers, timeout_cfg, start, file_name, auth_obj
            )
        except _AntiBotBlocked:
            clearance = None
            try:
                from Akbots.cf_bypass import get_clearance
                clearance = await get_clearance(url)
            except Exception:
                clearance = None

            if not clearance:
                raise ValueError(
                    "This link is behind anti-bot protection (Cloudflare) and "
                    "the automatic bypass isn't available or failed — try "
                    "again later or get a fresh link."
                )

            if status is not None:
                await _status_edit(
                    status,
                    f"{E_INFO} <b>Anti-bot protection detected</b> — bypassing "
                    f"before download starts..."
                )

            retry_headers = dict(headers)
            retry_headers["User-Agent"] = clearance["user_agent"]
            return await _stream_download_inner(
                url, dest, status, label, retry_headers, timeout_cfg, start,
                file_name, auth_obj, cookies=clearance["cookies"], _retried=True,
            )
    finally:
        if _slot is not None:
            try:
                await _slot.__aexit__(None, None, None)
            except Exception:
                pass
        if task_id is not None:
            from Akbots import task_manager
            task_manager.unregister(user_id, task_id)


async def _stream_download_inner(url, dest, status, label, headers, timeout_cfg, start,
                                  file_name=None, auth_obj=None, cookies=None, _retried=False) -> int:
    downloaded = 0
    last_edit = 0.0
    first_chunk = None

    async with aiohttp.ClientSession(timeout=timeout_cfg, cookies=cookies) as session:
        async with session.get(url, headers=headers, auth=auth_obj) as resp:
            content_type = resp.headers.get("Content-Type", "").lower()

            if resp.status not in (200, 206) or "text/html" in content_type:
                # Could be a real error (expired/private/wrong link) OR an
                # anti-bot challenge page — peek at a small body sample to
                # tell the two apart before deciding how to fail.
                try:
                    sample_bytes = await resp.content.read(4096)
                    sample = sample_bytes.decode("utf-8", errors="ignore")
                except Exception:
                    sample_bytes, sample = b"", ""

                if not _retried and _is_antibot_block(resp.status, dict(resp.headers), sample):
                    raise _AntiBotBlocked(f"Anti-bot protection detected (HTTP {resp.status})")

                if resp.status not in (200, 206):
                    raise ValueError(f"HTTP {resp.status} while downloading")
                reason = _extract_html_reason(sample_bytes, resp.status)
                raise ValueError(f"Download failed — {reason}.")

            total = int(resp.headers.get("Content-Length", 0))

            try:
                with open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        if not chunk:
                            continue
                        if first_chunk is None:
                            first_chunk = chunk
                            if not _retried and _is_antibot_block(
                                resp.status, dict(resp.headers),
                                chunk[:4096].decode("utf-8", errors="ignore")
                            ):
                                raise _AntiBotBlocked("Anti-bot protection detected in response body")
                            if _looks_like_html_error(first_chunk):
                                reason = _extract_html_reason(first_chunk, resp.status)
                                raise ValueError(f"Download failed — {reason}.")
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.monotonic()
                        if status is not None and now - last_edit >= 3:
                            last_edit = now
                            elapsed = now - start
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            pct = (downloaded / total * 100) if total else 0
                            eta = ((total - downloaded) / speed) if (total and speed > 0) else None
                            await _status_edit(
                                status,
                                format_progress(pct, speed_bps=speed, done_bytes=downloaded,
                                                 total_bytes=total or None, elapsed_secs=elapsed,
                                                 eta_secs=eta, title=label, file_name=file_name)
                            )
            except ValueError:
                try:
                    os.remove(dest)
                except Exception:
                    pass
                raise

    if downloaded == 0:
        try:
            os.remove(dest)
        except Exception:
            pass
        raise ValueError("Downloaded 0 bytes — server sent an empty response.")

    return downloaded


def download_official_thumbnail(info: dict, thumb_path: str) -> bool:
    """Downloads the actual thumbnail image the site provides (from yt-dlp's
    info dict) and converts/scales it to a Telegram-friendly JPEG, instead of
    grabbing a random frame out of the video with ffmpeg.

    Picks the highest-resolution thumbnail listed, since yt-dlp's "thumbnails"
    list is ordered from lowest to highest quality.
    """
    if not info:
        return False
    thumb_url = None
    thumbs = info.get("thumbnails") or []
    if thumbs:
        thumb_url = thumbs[-1].get("url")
    thumb_url = thumb_url or info.get("thumbnail")
    if not thumb_url:
        return False

    raw_path = thumb_path + ".src"
    try:
        resp = requests.get(thumb_url, timeout=20)
        resp.raise_for_status()
        with open(raw_path, "wb") as f:
            f.write(resp.content)
    except Exception:
        return False

    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", raw_path,
             "-vf", "scale=320:-1", "-y", thumb_path],
            timeout=30, check=True
        )
        return os.path.exists(thumb_path)
    except Exception:
        return False
    finally:
        try:
            os.remove(raw_path)
        except Exception:
            pass


def extract_thumbnail(video_path: str, thumb_path: str) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = float(probe.stdout.strip() or "10")
    except ValueError:
        duration = 10.0
    seek = random.uniform(duration * 0.1, duration * 0.8) if duration > 1 else 0
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(seek),
             "-i", video_path, "-vframes", "1", "-vf", "scale=320:-1", "-y", thumb_path],
            timeout=30, check=True
        )
        return os.path.exists(thumb_path)
    except Exception:
        return False


def get_video_metadata(video_path: str):
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = int(float(dur.stdout.strip() or "0"))
    except ValueError:
        duration = 0
    dim = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        w, h = dim.stdout.strip().split(",")
        width, height = int(w), int(h)
    except Exception:
        width, height = 1280, 720
    return duration, width, height


def _probe_stream_type(path: str) -> str:
    """Generic-URL downloads (urluploader.py / aria2_dl.py) often have no
    extension at all, or a wrong one — the last URL path segment isn't
    reliable (dynamic CDN links, query-string based file IDs, etc). Unlike
    yt-dlp based plugins, there's no site metadata to fall back on here, so
    when the extension doesn't tell us anything, ask ffprobe what's
    actually inside the file. Returns 'video', 'audio', or '' (unknown/
    probe failed) — never raises."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30
        )
        types = probe.stdout.split()
        if "video" in types:
            return "video"
        if "audio" in types:
            return "audio"
    except Exception:
        pass
    return ""


def remux_to_mp4(in_path: str, out_path: str, progress_status=None) -> bool:
    """Convert any ffmpeg-readable video container to .mp4. Tries a fast
    stream-copy remux first (no quality loss, seconds not minutes); only
    falls back to a full re-encode if the source codec genuinely can't be
    packaged into an mp4 container as-is (rare, but some old codecs like
    WMV/VP6 aren't mp4-compatible without transcoding)."""
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", in_path,
             "-c", "copy", "-movflags", "+faststart", "-y", out_path],
            timeout=600, check=True
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            return True
    except Exception:
        pass

    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", in_path,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-movflags", "+faststart", "-y", out_path],
            timeout=1800, check=True
        )
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1024
    except Exception:
        return False


def extract_screenshots(video_path: str, out_dir: str, count: int = 4) -> list:
    """Grab `count` evenly-spaced frames across the middle 80% of the
    video's duration (avoids intro/outro black frames and end credits) and
    save them as JPEGs in out_dir. Returns the list of paths actually
    created — skips a spot ffmpeg fails to grab rather than raising, so a
    handful of bad frames don't kill the whole set."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = float(probe.stdout.strip() or "0")
    except ValueError:
        duration = 0.0
    if duration <= 1:
        return []

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i in range(count):
        frac = (i + 1) / (count + 1)
        seek = duration * (0.1 + frac * 0.8)
        shot_path = os.path.join(out_dir, f"screenshot_{i + 1}.jpg")
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(seek),
                 "-i", video_path, "-vframes", "1", "-vf", "scale=1280:-1", "-y", shot_path],
                timeout=30, check=True
            )
            if os.path.exists(shot_path):
                paths.append(shot_path)
        except Exception:
            continue
    return paths


def make_sample_clip(video_path: str, out_path: str, clip_seconds: int = 20) -> bool:
    """Cut a short preview clip starting a little into the video (skips the
    first ~10% so it's not just a black intro/logo card) — stream-copy
    first since it's fast and lossless, falling back to a quick re-encode
    if the copy lands on a bad boundary and comes out empty/broken (some
    codecs can't be cut cleanly on a non-keyframe with -c copy)."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = float(probe.stdout.strip() or "0")
    except ValueError:
        duration = 0.0
    if duration <= 1:
        return False

    clip_seconds = min(clip_seconds, max(int(duration) - 1, 1))
    start = duration * 0.1 if duration > clip_seconds * 2 else 0

    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(start),
             "-i", video_path, "-t", str(clip_seconds), "-c", "copy", "-y", out_path],
            timeout=60, check=True
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            return True
    except Exception:
        pass

    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(start),
             "-i", video_path, "-t", str(clip_seconds),
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-y", out_path],
            timeout=120, check=True
        )
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1024
    except Exception:
        return False


async def _send_one(client, message, path: str, caption: str, progress, force_document: bool = False,
                     disable_notification: bool = False):
    """Sends a single file (one part, or the whole file if not split) as
    video/audio/photo/document based on its extension, then cleans up.
    Returns the thumb path it created (if any) so the caller can clean it up.

    Two per-user settings (set via /settings or /set_thumb, /upload_mode)
    are applied here so they take effect for every downloader plugin that
    goes through this shared upload path — not just the specific flow they
    were originally introduced for:
      - upload_mode == "document": forces everything out as a plain
        document, skipping the video/audio/photo branches below entirely.
      - a saved custom thumbnail overrides the auto-extracted one.
    """
    from database.db import db
    user_id = message.from_user.id if message.from_user else None

    upload_mode = "auto"
    custom_thumb = None
    spoiler_mode = False
    auto_screenshots = False
    if user_id:
        try:
            upload_mode = await db.get_upload_mode(user_id)
        except Exception:
            pass
        try:
            custom_thumb = await db.get_thumbnail(user_id)
        except Exception:
            pass
        try:
            spoiler_mode = await db.get_spoiler_mode(user_id)
        except Exception:
            pass
        try:
            auto_screenshots = await db.get_auto_screenshots(user_id)
        except Exception:
            pass

    ext = os.path.splitext(path)[1].lower()
    thumb = None
    force_document = force_document or (upload_mode == "document")

    # Extension missing/unrecognised (common for generic direct-link
    # downloads where the URL itself has no filename with an extension) —
    # fall back to sniffing the real content with ffprobe so video files
    # still get a thumbnail + duration instead of silently going out as a
    # plain document.
    if not force_document and ext not in VIDEO_EXTS and ext not in AUDIO_EXTS and ext not in PHOTO_EXTS:
        kind = await asyncio.to_thread(_probe_stream_type, path)
        if kind == "video":
            ext = ".mp4"   # dummy marker only, doesn't touch the real file name
        elif kind == "audio":
            ext = ".mp3"

    if not force_document and ext in VIDEO_EXTS:
        thumb = path + ".jpg"
        has_thumb = await asyncio.to_thread(extract_thumbnail, path, thumb)
        thumb_to_use = custom_thumb or (thumb if has_thumb else None)
        duration, width, height = await asyncio.to_thread(get_video_metadata, path)
        sent = await client.send_video(
            chat_id=message.chat.id, video=path,
            thumb=thumb_to_use,
            duration=duration, width=width, height=height,
            caption=caption, reply_to_message_id=message.id,
            supports_streaming=True, parse_mode=enums.ParseMode.HTML,
            has_spoiler=spoiler_mode,
            disable_notification=disable_notification,
            progress=progress
        )
        if auto_screenshots:
            # Best-effort, after the real upload — a screenshot failure or
            # slowdown must never affect the video delivery itself.
            try:
                shots_dir = path + "_shots"
                shots = await asyncio.to_thread(extract_screenshots, path, shots_dir, 4)
                if shots:
                    from pyrogram.types import InputMediaPhoto
                    await client.send_media_group(
                        chat_id=message.chat.id,
                        media=[InputMediaPhoto(s) for s in shots],
                        reply_to_message_id=sent.id,
                    )
                for s in shots:
                    try:
                        os.remove(s)
                    except OSError:
                        pass
                try:
                    os.rmdir(shots_dir)
                except OSError:
                    pass
            except Exception:
                pass
    elif not force_document and ext in AUDIO_EXTS:
        sent = await client.send_audio(
            chat_id=message.chat.id, audio=path,
            caption=caption, reply_to_message_id=message.id,
            thumb=custom_thumb,
            parse_mode=enums.ParseMode.HTML,
            disable_notification=disable_notification,
            progress=progress
        )
    elif not force_document and ext in PHOTO_EXTS:
        sent = await client.send_photo(
            chat_id=message.chat.id, photo=path,
            caption=caption, reply_to_message_id=message.id,
            parse_mode=enums.ParseMode.HTML,
            has_spoiler=spoiler_mode,
            disable_notification=disable_notification,
            progress=progress
        )
    else:
        sent = await client.send_document(
            chat_id=message.chat.id, document=path,
            caption=caption, reply_to_message_id=message.id,
            thumb=custom_thumb,
            parse_mode=enums.ParseMode.HTML,
            disable_notification=disable_notification,
            progress=progress
        )

    try:
        from Akbots.backup import backup_message
        await backup_message(client, sent)
    except Exception:
        pass  # auto-backup is best-effort and must never break an upload

    try:
        await _forward_to_custom_channel(client, message, sent)
    except Exception:
        pass  # custom-channel forwarding is best-effort and must never break an upload

    return thumb, sent


async def _forward_to_custom_channel(client, message, sent_message):
    """Copies the just-delivered file into every channel/group the user has
    linked via /set_channel_id or /add_channel_id. Uses copy_message so it
    costs no extra bandwidth (Telegram just re-associates the existing
    file_id) and never touches the local disk copy again.

    Best-effort per channel: if one linked channel fails (bot removed,
    not admin there, channel deleted, flood-wait, etc.) the rest are still
    tried, and none of this ever affects the delivery that already
    succeeded for the user.
    """
    if sent_message is None:
        return
    from database.db import db
    chat_ids = await db.get_dump_chats(message.from_user.id)
    if not chat_ids:
        return
    for chat_id in chat_ids:
        try:
            await client.copy_message(
                chat_id=chat_id,
                from_chat_id=sent_message.chat.id,
                message_id=sent_message.id,
                caption=sent_message.caption,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            continue  # one bad channel shouldn't stop delivery to the others


async def upload_file(client, message, path: str, status, caption: str,
                       file_name: str = None, duration=None, quality: str = None,
                       cache_url: str = None, force_document: bool = False,
                       delete_status: bool = True, disable_notification: bool = False):
    """Sends a downloaded file to Telegram as video/audio/photo/document
    based on its extension, then cleans up the local copy. If the file is
    bigger than Telegram's bot upload limit, it's split into parts first
    (see split_file()) and each part is uploaded and cleaned up in turn.

    file_name/duration/quality are optional — pass them when already known
    (every plugin has file_name in scope already) so the upload progress
    box shows the same File/Quality rows the download progress box does,
    instead of just the bar/percentage.

    cache_url: pass the original source URL to remember this upload in
    link_cache, so the exact same link can be re-sent instantly next time
    instead of being re-downloaded. Only cached when the file wasn't split
    into multiple parts — there's no single file_id for a split file.

    delete_status: set False when a single status message is being reused
    across multiple files (e.g. a folder/batch download loop) — deleting it
    after the first file would break every subsequent status.edit_text()
    call in the loop with MESSAGE_ID_INVALID, silently aborting the batch.

    disable_notification: True sends the file silently (no push
    notification on the recipient's end) — used by /encode when the
    user's enc_notify setting is off."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    ext = os.path.splitext(path)[1].lower()
    user_id = message.from_user.id if message.from_user else None
    if user_id and ext in VIDEO_EXTS:
        try:
            from database.db import db
            if await db.get_auto_sample(user_id):
                sample_path = path + "_sample.mp4"
                made = await asyncio.to_thread(make_sample_clip, path, sample_path)
                if made:
                    try:
                        await client.send_video(
                            chat_id=message.chat.id, video=sample_path,
                            caption=f"<blockquote>🎞 <b>Preview sample</b>\n<i>Full video uploading next...</i></blockquote>",
                            reply_to_message_id=message.id,
                            supports_streaming=True, parse_mode=enums.ParseMode.HTML,
                        )
                    finally:
                        try:
                            os.remove(sample_path)
                        except OSError:
                            pass
        except Exception:
            pass  # sample generation is best-effort and must never block the real upload

    parts = [path]
    if size > SPLIT_SIZE:
        await _status_edit(status, f"<b>{E_BOLT} File is {fmt_bytes(size)} — splitting before upload...</b>")
        parts = await split_file(path, status=status)

    total_parts = len(parts)
    try:
        for i, part_path in enumerate(parts, start=1):
            thumb = None
            try:
                label = "Fast Upload via Main Engine" if total_parts == 1 else f"Fast Upload via Main Engine (part {i}/{total_parts})"
                await _status_edit(status, f"<b>{E_ROCKET} {label}</b>")
                progress = make_upload_progress(
                    status, label=label,
                    file_name=file_name, duration=duration, quality=quality,
                )
                part_caption = caption if total_parts == 1 else f"{caption}\n\n<b>Part {i}/{total_parts}</b>"

                thumb, sent = await _send_one(client, message, part_path, part_caption, progress,
                                               force_document=force_document,
                                               disable_notification=disable_notification)

                if cache_url and total_parts == 1:
                    try:
                        from Akbots.link_cache import store as _cache_store
                        await _cache_store(cache_url, sent, caption=part_caption)
                    except Exception:
                        pass  # caching is best-effort, must never break an upload

                try:
                    part_size = os.path.getsize(part_path)
                except OSError:
                    part_size = 0
                try:
                    from Akbots.user_stats import record_usage
                    # Only count one success per original file, not per part.
                    await record_usage(message.from_user.id, uploaded_bytes=part_size,
                                        success_count=(1 if i == total_parts else 0))
                except Exception:
                    pass  # usage stats are best-effort and must never break an upload
            finally:
                # Remove the part (if it's not the original, unsplit file)
                # and its thumb right away, so we don't hold disk for every
                # part until the whole batch finishes.
                if part_path != path:
                    try:
                        os.remove(part_path)
                    except Exception:
                        pass
                if thumb:
                    try:
                        os.remove(thumb)
                    except Exception:
                        pass

        if delete_status:
            try:
                await status.delete()
            except Exception:
                pass
    finally:
        # `path` itself: removed here once, whether it was uploaded directly
        # (single part == path) or split into separate part files (in which
        # case `path` was left untouched by split_file/the loop above).
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
