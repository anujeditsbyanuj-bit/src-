# MX Player Link Support
# Ported from MX-Player-Downloader-Bot-DKBOTZ and, on top of that, Mx_Downloader
# (helpers.py + plugins/downloader.py) — rewritten to fit this bot's plugin
# style and reuse Akbots/direct_utils.py for the actual download/upload.
#
# MX Player pages don't expose a direct file — they're resolved via an
# external API to an m3u8/mpd stream URL, which yt-dlp can then read like any
# other site. Mx_Downloader added one thing the first port didn't have:
# MX Player episodes often carry several separate audio tracks (language
# dubs), and a viewer may want more than one of them muxed into the same
# file — so this version shows a proper multi-select audio-track step
# (toggle any number on, tap Done) instead of always grabbing just one.
#
# Not ported: Mx_Downloader's automatic >1.85GB file splitting. That's a
# generic capability none of this bot's other downloaders have either (they
# all just enforce YTDL_MAX_FILESIZE and fail with a clear error instead of
# splitting), so adding it only for MX Player would be inconsistent — it'd
# need to become a shared direct_utils feature to make sense everywhere.

import os
import re
import uuid
import shutil
import asyncio
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import YTDL_MAX_FILESIZE
from Akbots.direct_utils import upload_file, E_CHECK, E_CROSS, E_INFO, E_ROCKET

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# Reuses ytdl.py's existing download-progress-hook helper (throttled
# percentage/speed/ETA text via progress_hooks, edited back onto the status
# message from yt-dlp's worker thread) instead of duplicating that logic —
# _download_sync below previously had NO progress_hooks entry at all, so the
# status message just sat on a static "Downloading..." the whole time and
# the only progress bar anyone ever saw was upload_file's own, once the
# download finished and upload started.
from Akbots.ytdl import _make_download_progress_hook
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

PATTERN = re.compile(r"(https?://)?(www\.)?(mxplayer\.in|mxplay\.com)/\S+", re.IGNORECASE)

# Third-party resolver API — unofficial, may change/break at any time.
# You can get your own key from https://t.me/DKBOTZPRO/14
_MXPLAYER_API = "https://ott.dkbotzpro.in/mxplayer"

DOWNLOAD_DIR = "mxplayer_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

LANG_MAP = {
    "en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu",
    "ml": "Malayalam", "kn": "Kannada", "bn": "Bengali", "mr": "Marathi",
    "pa": "Punjabi", "gu": "Gujarati",
}

# session_id -> {url, title, episode, season, thumb, video_formats,
#                 audio_formats, chat_id, reply_to, selected_audio}
_SESSIONS = {}


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


def _slug_title(url: str):
    """Best-effort human title from the URL slug, used only if the API
    doesn't return one (mirrors Mx_Downloader's extract_title_from_url)."""
    try:
        path = url.split("?")[0].split("#")[0]
        slug = [s for s in path.split("/") if s][-1]
        slug = re.sub(r"-[0-9a-f]{20,}$", "", slug)
        stop = {"watch", "movie", "online", "free", "hd", "stream", "streaming",
                "web", "series", "webseries", "episode", "full", "official",
                "trailer", "video", "in", "on", "the", "a", "an"}
        words = [w for w in slug.replace("-", " ").replace("_", " ").split() if w.lower() not in stop]
        title = " ".join(words).title()
        return title if len(title) >= 3 else None
    except Exception:
        return None


async def _resolve(link: str) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for _ in range(3):
            try:
                async with session.get(_MXPLAYER_API, params={"url": link}) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
            except Exception:
                continue

            if not data or not data.get("status"):
                raise ValueError(data.get("message", "Failed to fetch MX Player data") if data else "MX Player API error")

            stream_url = data.get("m3u8_url") or data.get("mpd_url")
            if not stream_url:
                raise ValueError("No playable stream found for this MX Player link.")

            data["_stream_url"] = stream_url
            data["_title"] = data.get("show_title") or _slug_title(link) or "Unknown"
            return data

    raise ValueError("MX Player API is not responding — try again later.")


def _extract_formats_sync(stream_url: str):
    if yt_dlp is None:
        return [], []
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(stream_url, download=False)
    formats = info.get("formats") or []

    videos, audios = [], []
    for f in formats:
        fid = str(f.get("format_id"))
        ext = f.get("ext", "mp4")
        if f.get("vcodec") != "none" and f.get("height"):
            height = f.get("height", 0)
            size = f.get("filesize") or f.get("filesize_approx") or 0
            size_str = f" ({size / 1024 / 1024:.1f}MB)" if size else ""
            videos.append({"id": fid, "height": height,
                            "label": f"🎬 {height}p{size_str}"})
        elif f.get("acodec") != "none" and f.get("vcodec") == "none":
            abr = f.get("abr") or 0
            lang = f.get("language") or ""
            lang_label = f" - {LANG_MAP.get(lang, lang.upper())}" if lang else ""
            audios.append({"id": fid, "abr": abr,
                            "label": f"🎵 {int(abr) if abr else '?'}kbps{lang_label}"})

    videos.sort(key=lambda x: x["height"], reverse=True)
    audios.sort(key=lambda x: x["abr"], reverse=True)
    return videos, audios


def _audio_keyboard(session_id: str, audios, selected: set, is_final_step: bool):
    rows = []
    for a in audios[:20]:
        mark = "✅ " if a["id"] in selected else ""
        rows.append([make_button(f"{mark}{a['label']}", callback_data=f"mxa:{session_id}:{a['id']}", style=_BS.PRIMARY if _BS else None)])
    action_row = [make_button("🚀 Done", callback_data=f"mxdone:{session_id}", style=_BS.SUCCESS if _BS else None)]
    if not is_final_step:
        action_row.append(make_button("⏭ Skip Audio", callback_data=f"mxskip:{session_id}", style=_BS.PRIMARY if _BS else None))
    rows.append(action_row)
    rows.append([make_button("❌ Cancel", callback_data=f"mxcancel:{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} MX Player link detected — resolving...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        data = await _resolve(url)
        videos, audios = await asyncio.to_thread(_extract_formats_sync, data["_stream_url"])
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not videos and not audios:
        return await status.edit_text(f"<b>{E_CROSS} Could not detect any downloadable formats.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _SESSIONS[session_id] = {
        "url": data["_stream_url"], "title": data["_title"],
        "episode": data.get("seo_title") or "", "season": data.get("season") or "",
        "thumb": data.get("thumbnail") or "", "video_formats": videos, "audio_formats": audios,
        "selected_video": None, "selected_audio": set(),
        "chat_id": message.chat.id, "reply_to": message.id, "orig_message": message,
    }

    title = data["_title"]
    text = (
        f"<b>{E_ROCKET} {title[:80]}</b>\n\n"
        f"<b>Available qualities:</b>\n" + "\n".join(f"✅ {v['label']}" for v in videos[:15]) +
        (f"\n\n<i>{len(audios)} audio track(s) available after you pick a quality.</i>" if audios else "") +
        "\n\n<i>Tap a quality below:</i>"
    )
    buttons = [[make_button(v["label"], callback_data=f"mxv:{session_id}:{v['id']}", style=_BS.PRIMARY if _BS else None)] for v in videos[:20]]
    if audios:
        buttons.append([make_button("🎵 Audio Only", callback_data=f"mxaudioonly:{session_id}", style=_BS.SUCCESS if _BS else None)])
    buttons.append([make_button("❌ Cancel", callback_data=f"mxcancel:{session_id}", style=_BS.DANGER if _BS else None)])
    keyboard = InlineKeyboardMarkup(buttons)

    await status.delete()
    thumb = data.get("thumbnail")
    if thumb:
        try:
            await message.reply_photo(thumb, caption=text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
            return
        except Exception:
            pass
    await message.reply_text(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^mxv:([a-f0-9]+):(\S+)$"))
async def _pick_video(client: Client, cq: CallbackQuery):
    session_id, fid = cq.matches[0].group(1), cq.matches[0].group(2)
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Session expired, send the link again.", show_alert=True)
    session["selected_video"] = fid
    await cq.answer("Video quality selected.")
    if not session["audio_formats"]:
        return await _start_download(client, cq, session_id)
    await _edit(cq.message, f"<b>{E_CHECK} Quality selected. Now pick audio track(s):</b>",
                _audio_keyboard(session_id, session["audio_formats"], session["selected_audio"], False))


@Client.on_callback_query(filters.regex(r"^mxaudioonly:([a-f0-9]+)$"))
async def _pick_audio_only(client: Client, cq: CallbackQuery):
    session_id = cq.matches[0].group(1)
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Session expired, send the link again.", show_alert=True)
    session["selected_video"] = None
    await cq.answer("Audio-only mode.")
    await _edit(cq.message, f"<b>{E_CHECK} Pick audio track(s):</b>",
                _audio_keyboard(session_id, session["audio_formats"], session["selected_audio"], True))


@Client.on_callback_query(filters.regex(r"^mxa:([a-f0-9]+):(\S+)$"))
async def _toggle_audio(client: Client, cq: CallbackQuery):
    session_id, fid = cq.matches[0].group(1), cq.matches[0].group(2)
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Session expired, send the link again.", show_alert=True)
    sel = session["selected_audio"]
    (sel.discard if fid in sel else sel.add)(fid)
    await cq.answer("Track removed." if fid not in sel else "Track added.")
    is_final = session["selected_video"] is None
    await cq.message.edit_reply_markup(_audio_keyboard(session_id, session["audio_formats"], sel, is_final))


@Client.on_callback_query(filters.regex(r"^mxskip:([a-f0-9]+)$"))
async def _skip_audio(client: Client, cq: CallbackQuery):
    session_id = cq.matches[0].group(1)
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Session expired, send the link again.", show_alert=True)
    session["selected_audio"] = set()
    await cq.answer("Skipping audio selection.")
    await _start_download(client, cq, session_id)


@Client.on_callback_query(filters.regex(r"^mxdone:([a-f0-9]+)$"))
async def _done_audio(client: Client, cq: CallbackQuery):
    session_id = cq.matches[0].group(1)
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Session expired, send the link again.", show_alert=True)
    if session["selected_video"] is None and not session["selected_audio"]:
        return await cq.answer("Pick at least one audio track for audio-only mode.", show_alert=True)
    await cq.answer("Starting download...")
    await _start_download(client, cq, session_id)


@Client.on_callback_query(filters.regex(r"^mxcancel:([a-f0-9]+)$"))
async def _cancel(client: Client, cq: CallbackQuery):
    _SESSIONS.pop(cq.matches[0].group(1), None)
    await cq.message.delete()
    await cq.answer("Cancelled.")


async def _edit(msg: Message, text: str, keyboard):
    if msg.photo:
        return await msg.edit_caption(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    return await msg.edit_text(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)


def _download_sync(session: dict, out_dir: str, progress_hook=None):
    video_id = session["selected_video"]
    audio_ids = list(session["selected_audio"])
    audio_only = video_id is None

    if audio_only:
        fmt = "+".join(audio_ids) if audio_ids else "bestaudio"
        opts = {
            "quiet": True, "no_warnings": True, "format": fmt,
            "outtmpl": os.path.join(out_dir, "%(title).70s.%(ext)s"),
            "max_filesize": YTDL_MAX_FILESIZE,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        }
    else:
        fmt = f"{video_id}+" + "+".join(audio_ids) if audio_ids else f"{video_id}+bestaudio/best"
        opts = {
            "quiet": True, "no_warnings": True, "format": fmt,
            "outtmpl": os.path.join(out_dir, "%(title).70s.%(ext)s"),
            "max_filesize": YTDL_MAX_FILESIZE, "merge_output_format": "mp4",
        }
        if len(audio_ids) > 1:
            opts["audio_multistreams"] = True

    if progress_hook is not None:
        opts["progress_hooks"] = [progress_hook]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(session["url"], download=True)
        path = ydl.prepare_filename(info)
        path = os.path.splitext(path)[0] + (".mp3" if audio_only else "." + (info.get("ext") or "mp4"))
    return path


async def _start_download(client: Client, cq: CallbackQuery, session_id: str):
    session = _SESSIONS.get(session_id)
    if not session:
        return
    status = cq.message
    session_dir = os.path.join(DOWNLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)

    task_id = None
    try:
        from Akbots import task_manager
        task_id = task_manager.register(
            cq.from_user.id, asyncio.current_task(),
            f"MX Player: {session.get('title', 'video')[:40]}"
        )
    except Exception:
        task_id = None

    try:
        await _edit(status, f"<b>{E_ROCKET} Downloading...</b>", None)
        loop = asyncio.get_running_loop()
        hook = _make_download_progress_hook(
            status, loop,
            file_name=session.get("title"),
            quality=(next(
                (v["label"] for v in session.get("video_formats", []) if v["id"] == session.get("selected_video")),
                None
            ) if session.get("selected_video") is not None else "MP3 (Audio)"),
        )
        filepath = await asyncio.to_thread(_download_sync, session, session_dir, hook)
        if not os.path.exists(filepath):
            raise FileNotFoundError("Download finished but file was not found (likely size limit).")

        title = session["title"]
        episode = session.get("episode") or "N/A"
        season = session.get("season") or "N/A"
        caption = (
            f"<b>{E_CHECK} {title[:100]}</b>\n"
            f"📺 <b>Episode:</b> {episode}\n"
            f"📦 <b>Season:</b> {season}"
        )

        video_id = session.get("selected_video")
        if video_id is None:
            quality_label = "MP3 (Audio)"
        else:
            quality_label = next(
                (v["label"] for v in session.get("video_formats", []) if v["id"] == video_id),
                None
            )

        # upload_file picks video/audio/document by extension and handles
        # its own thumbnail + progress + Akbots.user_stats accounting.
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
            file_name=title, quality=quality_label,
        )
    except Exception as e:
        await _edit(status, f"<b>{E_CROSS} Download failed:</b>\n<code>{e}</code>", None)
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(cq.from_user.id, task_id)
            except Exception:
                pass
        _SESSIONS.pop(session_id, None)
        shutil.rmtree(session_dir, ignore_errors=True)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def mxplayer_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("mxplayer") & filters.private)
async def mxplayer_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/mxplayer &lt;MX Player URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)
