# Akbots - Don't Remove Credit - @AkBots_Official
#
# Audio track selection — ported from TG-AudioSelector's
# utils.py (get_audio_tracks / select_audio_tracks / track-selection keyboard)
# and handlers.py (track_/done_tracks/format_ callback flow + per-user queue),
# rewritten to reuse Akbots/direct_utils.py (progress bar, upload, thumbnail)
# and Akbots/task_manager.py (so a running job shows up in /queue and can be
# force-killed with /cancel_all) instead of the source bot's own tqdm /
# download_media / send_video calls and its standalone `daily_limits` dict.
#
# Flow:
#   1. Reply to a video/document with /audio_select
#      - if the user already has a job running, this one is queued instead
#      - blocked outright if the daily quota (15 free / 30 premium) is used up
#   2. Bot downloads it, probes audio streams with ffprobe
#   3. User taps tracks to keep (multi-select toggle) -> Done
#   4. User picks output container: Video (mp4) or Document (mkv)
#   5. ffmpeg stream-copies the video + only the selected audio tracks
#      (no re-encode — fast, lossless) and the result is uploaded back
#   6. Next queued file (if any) for that user starts automatically
#
# Companion commands: /audiocancel, /audioqueue, /audio_defaults, /getid

import os
import json
import shutil
import asyncio
import subprocess
import contextlib
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import db

from Akbots.direct_utils import (
    upload_file, extract_thumbnail, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, get_video_metadata, VIDEO_EXTS, fmt_bytes,
)
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.caption import render_caption
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_TIP   = '<emoji id=5422439311196834318>💡</emoji>'
E_MUSIC = '🎵'
E_BOLT  = '⚡'

# session state, keyed by the status message id -> dict
# {chat_id, user_id, in_path, temp_dir, orig_name, base_name, tracks, selected:set}
_SESSIONS = {}

# per-user queueing: user_id -> status_id of the currently running job
_USER_ACTIVE = {}
# user_id -> [(client, message), ...] waiting their turn (FIFO)
_USER_QUEUE = {}


def _probe_audio_tracks(path: str):
    """Returns a list of (stream_index_within_audio, label) for every audio
    stream in the file, e.g. [(0, 'eng'), (1, 'hin (Commentary)')]."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index:stream_tags=language,title",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(out.stdout or "{}")
        streams = data.get("streams", [])
    except Exception:
        return []

    tracks = []
    for i, s in enumerate(streams):
        tags = s.get("tags", {}) or {}
        label = tags.get("language", f"Track {i}")
        if tags.get("title"):
            label += f" ({tags['title']})"
        tracks.append((i, label))
    return tracks


def _build_track_keyboard(status_id: int) -> InlineKeyboardMarkup:
    sess = _SESSIONS[status_id]
    rows = []
    for idx, label in sess["tracks"]:
        mark = f"{E_CHECK} " if idx in sess["selected"] else ""
        rows.append([make_button(f"{mark}{label}", callback_data=f"asel:trk:{status_id}:{idx}", style=_BS.PRIMARY if _BS else None)])
    rows.append([make_button("✅ Done", callback_data=f"asel:done:{status_id}", style=_BS.SUCCESS if _BS else None)])
    rows.append([make_button(f"{E_CROSS} Cancel", callback_data=f"asel:cancel:{status_id}", style=_BS.PRIMARY if _BS else None)])
    return InlineKeyboardMarkup(rows)


def _build_format_keyboard(status_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [make_button("🎬 Video (.mp4)", callback_data=f"asel:fmt:{status_id}:video", style=_BS.PRIMARY if _BS else None)],
        [make_button("📄 Document (.mkv)", callback_data=f"asel:fmt:{status_id}:mkv", style=_BS.PRIMARY if _BS else None)],
        [make_button(f"{E_CROSS} Cancel", callback_data=f"asel:cancel:{status_id}", style=_BS.PRIMARY if _BS else None)],
    ])


async def _cleanup_session(status_id: int):
    sess = _SESSIONS.pop(status_id, None)
    if sess:
        shutil.rmtree(sess["temp_dir"], ignore_errors=True)
    return sess


async def _release_and_advance(client: Client, user_id: int):
    """Marks the user as no longer having an active job, then kicks off the
    next queued file (if any) as a fresh, independent task."""
    _USER_ACTIVE.pop(user_id, None)
    queue = _USER_QUEUE.get(user_id)
    if queue:
        nxt_message = queue.pop(0)
        if not queue:
            _USER_QUEUE.pop(user_id, None)
        asyncio.create_task(_start_job(client, nxt_message))


@Client.on_message(filters.private & filters.command(["audio_select", "audioselect"]))
async def audio_select_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    replied = message.reply_to_message
    media = replied and (replied.video or replied.document)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_MUSIC} <b>Audio Track Selector</b>\n\n"
            f"Reply to a <b>video file</b> with <code>/audio_select</code> to choose "
            f"which audio track(s) to keep (or remove) — no quality loss, no re-encode.\n\n"
            f"{E_TIP} <code>/audio_defaults</code>, <code>/audiocancel</code>, <code>/audioqueue</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    if replied.document:
        file_name = replied.document.file_name or ""
        if not file_name.lower().endswith(VIDEO_EXTS):
            return await message.reply_text(
                f"<blockquote>{E_CROSS} <b>Not a video file!</b>\n\n"
                f"Reply to a video (.mp4, .mkv, .avi, etc.) to select its audio tracks.</blockquote>",
                parse_mode=enums.ParseMode.HTML
            )

    if await db.check_audio_select_limit(user_id):
        limit = await db.get_audio_select_limit(user_id)
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>Daily limit reached.</b>\n\n"
            f"You've used all <code>{limit}</code> audio-selection jobs for today. "
            f"It resets 24h after your first job of the day.\n"
            f"{E_TIP} Premium users get 30/day instead of 15 — check /premium.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    if user_id in _USER_ACTIVE:
        _USER_QUEUE.setdefault(user_id, []).append(message)
        pos = len(_USER_QUEUE[user_id])
        return await message.reply_text(
            f"<blockquote>{E_MUSIC} <b>You already have a job running.</b>\n\n"
            f"This file has been queued — position <code>{pos}</code>. "
            f"It'll start automatically once your current job finishes.\n"
            f"{E_TIP} Use /audioqueue to check, or /audiocancel to stop the current one.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    await _start_job(client, message)


async def _start_job(client: Client, message: Message):
    """Downloads the replied file and shows the track-selection keyboard.
    Called directly for a fresh /audio_select, or automatically for the
    next item in a user's queue."""
    user_id = message.from_user.id
    replied = message.reply_to_message
    _USER_ACTIVE[user_id] = True  # placeholder until we know the status_id

    orig_name = (replied.video and replied.video.file_name) or \
                (replied.document and replied.document.file_name) or f"video_{replied.id}"
    default_name = await db.get_audio_select_name(user_id)
    if default_name:
        ext = os.path.splitext(orig_name)[1] or ".mp4"
        orig_name = default_name if default_name.lower().endswith(ext.lower()) else default_name + ext
    base_name = os.path.splitext(orig_name)[0]

    status = await message.reply_text(f"<b>{E_MUSIC} Downloading video...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "audio_select", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                parse_mode=enums.ParseMode.HTML)
        return await _release_and_advance(client, user_id)

    tracks = await asyncio.to_thread(_probe_audio_tracks, in_path)
    if not tracks:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await safe_edit(status.edit_text, f"<b>{E_CROSS} No audio tracks found in this video.</b>",
                                parse_mode=enums.ParseMode.HTML)
        return await _release_and_advance(client, user_id)

    _USER_ACTIVE[user_id] = status.id
    _SESSIONS[status.id] = {
        "chat_id": message.chat.id,
        "user_id": user_id,
        "orig_message": message,
        "in_path": in_path,
        "temp_dir": temp_dir,
        "orig_name": orig_name,
        "base_name": base_name,
        "tracks": tracks,
        "selected": set(),
    }

    await safe_edit(status.edit_text, 
        f"<b>{E_MUSIC} Select audio track(s) to keep:</b>\n\n"
        f"<i>Tap to toggle, then hit Done.</i>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_build_track_keyboard(status.id)
    )


@Client.on_callback_query(filters.regex(r"^asel:"))
async def audio_select_callback(client: Client, cq: CallbackQuery):
    parts = cq.data.split(":")
    action = parts[1]
    status_id = int(parts[2])

    sess = _SESSIONS.get(status_id)
    if not sess:
        return await cq.answer("This session has expired.", show_alert=True)
    if cq.from_user.id != sess["user_id"]:
        return await cq.answer("This isn't your session.", show_alert=True)

    if action == "cancel":
        user_id = sess["user_id"]
        await _cleanup_session(status_id)
        await safe_edit(cq.message.edit_text, f"<b>{E_CROSS} Cancelled and temporary files deleted.</b>",
                                    parse_mode=enums.ParseMode.HTML)
        await cq.answer("Cancelled")
        return await _release_and_advance(client, user_id)

    if action == "trk":
        idx = int(parts[3])
        sel = sess["selected"]
        sel.discard(idx) if idx in sel else sel.add(idx)
        await cq.message.edit_reply_markup(_build_track_keyboard(status_id))
        return await cq.answer()

    if action == "done":
        if not sess["selected"]:
            return await cq.answer("Select at least one track first.", show_alert=True)
        await safe_edit(cq.message.edit_text, 
            f"<b>{E_MUSIC} Select output format:</b>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=_build_format_keyboard(status_id)
        )
        return await cq.answer()

    if action == "fmt":
        fmt = parts[3]  # 'video' -> mp4, 'mkv' -> mkv
        await cq.answer("Processing...")
        await safe_edit(cq.message.edit_text, f"<b>{E_BOLT} Selecting audio tracks...</b>", parse_mode=enums.ParseMode.HTML)
        await _process_and_upload(client, cq.message, status_id, fmt)


async def _process_and_upload(client: Client, status: Message, status_id: int, fmt: str):
    sess = _SESSIONS.get(status_id)
    if not sess:
        return
    user_id = sess["user_id"]

    try:
        in_path = sess["in_path"]
        temp_dir = sess["temp_dir"]
        base_name = sess["base_name"]
        selected = sorted(sess["selected"])
        ext = ".mp4" if fmt == "video" else ".mkv"
        out_path = os.path.join(temp_dir, base_name + "_audiosel" + ext)
        thumb_path = os.path.join(temp_dir, base_name + ".jpg")

        duration, _, _ = await asyncio.to_thread(get_video_metadata, in_path)

        cmd = ["ffmpeg", "-hide_banner", "-y", "-i", in_path, "-map", "0:v:0"]
        for idx in selected:
            cmd += ["-map", f"0:a:{idx}"]
        cmd += ["-c", "copy"]
        if fmt == "mkv":
            cmd += ["-f", "matroska"]
        cmd += [out_path]

        parse_line = make_ffmpeg_progress_parser(duration or 0, title="Selecting Audio Tracks...")
        returncode, tail = await run_subprocess_with_progress(
            cmd, status, "Selecting Audio Tracks...", parse_line,
            user_id=user_id, queue_label=f"Audio track selection — {base_name}{ext}",
        )

        if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            await safe_edit(status.edit_text, 
                f"<b>{E_CROSS} Processing failed.</b>\n\n"
                f"{E_TIP} The selected combination of tracks may not be remuxable without re-encoding.\n"
                f"<code>{tail[-300:]}</code>",
                parse_mode=enums.ParseMode.HTML
            )
            return

        await asyncio.to_thread(extract_thumbnail, in_path, thumb_path)

        try:
            os.remove(in_path)
        except Exception:
            pass

        default_caption = await db.get_caption(user_id)
        out_name = base_name + ext
        if default_caption:
            try:
                out_size = fmt_bytes(os.path.getsize(out_path))
            except OSError:
                out_size = ""
            orig_msg = sess.get("orig_message")
            orig_caption = ""
            if orig_msg is not None:
                src = orig_msg.reply_to_message or orig_msg
                if getattr(src, "caption", None):
                    orig_caption = src.caption.html
            caption = render_caption(
                default_caption, filename=out_name, size=out_size,
                caption=orig_caption, media_type="Video",
            )
        else:
            caption = f"<blockquote><b>{E_MUSIC} {out_name}</b></blockquote>"
        await upload_file(
            client, sess["orig_message"], out_path, status, caption,
            file_name=base_name + ext,
            force_document=(fmt == "mkv"),
        )
        await db.add_audio_select_usage(user_id)

    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await safe_edit(status.edit_text, f"<b>{E_CROSS} Job cancelled.</b>", parse_mode=enums.ParseMode.HTML)
        raise
    finally:
        await _cleanup_session(status_id)
        await _release_and_advance(client, user_id)


@Client.on_message(filters.private & filters.command(["audiocancel"]))
async def audio_cancel_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    status_id = _USER_ACTIVE.get(user_id)

    if not status_id or status_id is True:
        # either nothing active, or a download is still in flight and hasn't
        # produced a session yet — nothing selectable to cancel mid-download,
        # but still worth clearing so a stuck job doesn't block the queue
        if user_id in _USER_ACTIVE:
            _USER_ACTIVE.pop(user_id, None)
        cleared = len(_USER_QUEUE.pop(user_id, []) or [])
        if cleared:
            return await message.reply_text(
                f"<b>{E_CHECK} Cleared {cleared} queued file(s).</b>", parse_mode=enums.ParseMode.HTML
            )
        return await message.reply_text(f"<b>{E_WARN} No active audio-selection job to cancel.</b>",
                                         parse_mode=enums.ParseMode.HTML)

    sess = _SESSIONS.get(status_id)
    if sess:
        # not yet in the ffmpeg stage — just tear down the session directly
        await _cleanup_session(status_id)
        await _release_and_advance(client, user_id)
        return await message.reply_text(f"<b>{E_CHECK} Job cancelled and temporary files deleted.</b>",
                                         parse_mode=enums.ParseMode.HTML)

    # already in the ffmpeg stage — kill it via task_manager; the
    # asyncio.CancelledError handling in _process_and_upload does the cleanup
    from Akbots import task_manager
    count = task_manager.cancel_all_for(user_id)
    await message.reply_text(
        f"<b>{E_CHECK} Cancel signal sent.</b>" if count else
        f"<b>{E_WARN} Nothing to cancel right now.</b>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command(["audioqueue"]))
async def audio_queue_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    status_id = _USER_ACTIVE.get(user_id)
    queue = _USER_QUEUE.get(user_id, [])

    if not status_id and not queue:
        return await message.reply_text(f"<b>{E_MUSIC} No active or queued audio-selection jobs.</b>",
                                         parse_mode=enums.ParseMode.HTML)

    lines = [f"<b>{E_MUSIC} Audio Selector — Your Queue</b>", ""]
    if status_id:
        sess = _SESSIONS.get(status_id)
        name = sess["orig_name"] if sess else "downloading..."
        lines.append(f"{E_BOLT} <b>Running:</b> {name}")
    if queue:
        lines.append("")
        lines.append(f"<b>Waiting ({len(queue)}):</b>")
        for i, m in enumerate(queue, start=1):
            r = m.reply_to_message
            n = (r.video and r.video.file_name) or (r.document and r.document.file_name) or f"video_{r.id}"
            lines.append(f"  {i}. {n}")
    usage = await db.get_audio_select_usage(user_id)
    limit = await db.get_audio_select_limit(user_id)
    lines.append("")
    lines.append(f"{E_TIP} <b>Today's usage:</b> <code>{usage}/{limit}</code>")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command(["audio_defaults"]))
async def audio_defaults_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    args = message.text.split(maxsplit=1)[1:]
    if not args:
        name = await db.get_audio_select_name(user_id)
        caption = await db.get_caption(user_id)
        usage = await db.get_audio_select_usage(user_id)
        limit = await db.get_audio_select_limit(user_id)
        return await message.reply_text(
            f"<blockquote><b>{E_MUSIC} Audio Selector — Your Settings</b>\n\n"
            f"<b>Default filename:</b> {name or 'Not set (keeps original name)'}\n"
            f"<b>Caption:</b> {'Set (via /set_caption)' if caption else 'Not set'}\n"
            f"<b>Today's usage:</b> <code>{usage}/{limit}</code></blockquote>\n\n"
            f"{E_TIP} <code>/audio_defaults &lt;filename&gt;</code> to set a default output name.\n"
            f"{E_TIP} <code>/set_caption &lt;text&gt;</code> to set the caption used on uploads.",
            parse_mode=enums.ParseMode.HTML
        )

    filename = args[0].strip()
    await db.set_audio_select_name(user_id, filename)
    await message.reply_text(
        f"<b>{E_CHECK} Default filename set to:</b> <code>{filename}</code>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command(["getid"]))
async def get_id_cmd(client: Client, message: Message):
    chat = message.chat
    lines = [f"<b>Chat ID:</b> <code>{chat.id}</code>", f"<b>Chat Type:</b> <code>{chat.type}</code>"]
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        lines.append(f"<b>Replied User ID:</b> <code>{u.id}</code>")
    if message.from_user:
        lines.append(f"<b>Your ID:</b> <code>{message.from_user.id}</code>")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
