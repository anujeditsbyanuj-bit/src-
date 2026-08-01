# Akbots - Don't Remove Credit - @AkBots_Official
#
# Audio Trimmer — /atrim <start> [end]
#
# Same UX as trim.py (video trimmer) but for audio files/voice notes:
#   /atrim 00:01:10 00:02:30   -> keeps only 01:10-02:30
#   /atrim 90 150              -> plain seconds also accepted
#   /atrim 00:01:10            -> no end given, trims from 01:10 to the end
#
# Tries stream-copy first (instant, no quality loss), falls back to
# re-encode only if the copy path fails (rare, but some containers don't
# like being cut on a non-keyframe boundary for audio-only streams either).

import os
import shutil
import asyncio
import subprocess
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import (
    upload_file, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, make_output_folder, safe_filename, AUDIO_EXTS,
    fmt_hms,
)
from Akbots.direct_utils import safe_edit

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_SCIS  = '✂️'


def _parse_timestamp(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) > 3:
            return None
        try:
            parts = [float(p) for p in parts]
        except ValueError:
            return None
        secs = 0.0
        for p in parts:
            secs = secs * 60 + p
        return secs
    try:
        return float(raw)
    except ValueError:
        return None


def _replied_audio(message: Message):
    replied = message.reply_to_message
    if not replied:
        return None, None
    if replied.audio:
        return replied.audio, replied.audio.file_name or f"audio_{replied.id}.mp3"
    if replied.voice:
        return replied.voice, f"voice_{replied.id}.ogg"
    if replied.document:
        name = replied.document.file_name or ""
        if name.lower().endswith(AUDIO_EXTS):
            return replied.document, name
    return None, None


def _get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(r.stdout.strip() or "0")
    except ValueError:
        return 0.0


async def _atrim_stream_copy(in_path, out_path, start, end, status, user_id):
    cmd = ["ffmpeg", "-hide_banner", "-y", "-ss", str(start), "-i", in_path]
    if end is not None:
        cmd += ["-t", str(max(0.0, end - start))]
    cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", out_path]
    duration = (end - start) if end is not None else None
    parse_line = make_ffmpeg_progress_parser(duration or 0, title="Trimming audio (fast copy)...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Trimming audio (fast copy)...", parse_line, user_id=user_id, queue_label="Trim audio",
    )
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0, tail


async def _atrim_reencode(in_path, out_path, start, end, status, user_id):
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", in_path, "-ss", str(start)]
    if end is not None:
        cmd += ["-t", str(max(0.0, end - start))]
    cmd += ["-c:a", "aac", "-b:a", "192k", out_path]
    duration = (end - start) if end is not None else None
    parse_line = make_ffmpeg_progress_parser(duration or 0, title="Trimming audio (re-encoding)...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Trimming audio (re-encoding)...", parse_line, user_id=user_id, queue_label="Trim audio",
    )
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0, tail


@Client.on_message(filters.command("atrim") & filters.private)
async def atrim_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    media, orig_name = _replied_audio(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to an <b>audio file / voice note</b> with "
            f"<code>/atrim</code> and a start/end time.\n\n"
            f"{E_INFO} <b>Usage:</b>\n"
            f"<code>/atrim 00:01:10 00:02:30</code> — keep 01:10 to 02:30\n"
            f"<code>/atrim 90 150</code> — plain seconds also work\n"
            f"<code>/atrim 00:01:10</code> — trim from 01:10 to the end</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/atrim &lt;start&gt; [end]</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    args = message.command[1:]
    start = _parse_timestamp(args[0])
    end = _parse_timestamp(args[1]) if len(args) > 1 else None

    if start is None or (len(args) > 1 and end is None):
        return await message.reply_text(
            f"<b>{E_CROSS} Couldn't parse that timestamp.</b> Use <code>HH:MM:SS</code>, "
            f"<code>MM:SS</code>, or plain seconds.",
            parse_mode=enums.ParseMode.HTML,
        )
    if start < 0 or (end is not None and end <= start):
        return await message.reply_text(f"<b>{E_CROSS} End time must be after start time.</b>",
                                         parse_mode=enums.ParseMode.HTML)

    replied = message.reply_to_message
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("atrim"), f"{user_id}_{replied.id}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    orig_name = safe_filename(orig_name, f"audio_{replied.id}.mp3")
    in_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    duration = await asyncio.to_thread(_get_audio_duration, in_path)
    if duration and start >= duration:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Start time ({fmt_hms(start)}) is past the audio's length "
            f"({fmt_hms(duration)}).</b>", parse_mode=enums.ParseMode.HTML)
    if end is not None and duration and end > duration:
        end = duration

    base_name, ext = os.path.splitext(orig_name)
    out_name = f"{base_name}_trim{ext or '.mp3'}"
    out_path = os.path.join(temp_dir, out_name)

    ok, tail = await _atrim_stream_copy(in_path, out_path, start, end, status, user_id)
    if not ok:
        await safe_edit(status.edit_text, f"<b>{E_GEAR} Fast trim failed — re-encoding instead...</b>",
                                parse_mode=enums.ParseMode.HTML)
        ok, tail = await _atrim_reencode(in_path, out_path, start, end, status, user_id)

    if not ok:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Trim failed.</b>\n\n<code>{tail[-300:]}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    try:
        os.remove(in_path)
    except Exception:
        pass

    out_duration = await asyncio.to_thread(_get_audio_duration, out_path)
    range_label = f"{fmt_hms(start)} → {fmt_hms(end) if end is not None else 'end'}"

    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_SCIS} Trimmed: <b>{range_label}</b>",
        file_name=out_name, duration=int(out_duration), quality="Trimmed",
    )

    shutil.rmtree(temp_dir, ignore_errors=True)
