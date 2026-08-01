# Akbots - Don't Remove Credit - @AkBots_Official
#
# Video Trimmer — /trim <start> [end]
#
# Reply to a video (or a video sent as a document) with:
#   /trim 00:01:10 00:02:30   -> keeps only 01:10-02:30
#   /trim 90 150              -> plain seconds also accepted
#   /trim 00:01:10            -> no end given, trims from 01:10 to the end
#
# Tries a fast stream-copy cut first (-c copy, no re-encode, near-instant)
# same as merge.py's fast path — but ffmpeg's stream copy can only cut on
# a keyframe, so the result can be a couple hundred ms off, or in rare
# cases fail outright on some containers. If stream copy fails or produces
# an empty/broken file, falls back to a real re-encode (-c:v libx264) which
# cuts at the exact frame every time.

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, make_output_folder, safe_filename, VIDEO_EXTS,
    fmt_hms,
)
from Akbots.direct_utils import safe_edit

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'
E_SCIS   = '✂️'


def _parse_timestamp(raw: str):
    """Accepts HH:MM:SS, MM:SS, or a plain integer/float number of
    seconds. Returns seconds as a float, or None if it can't be parsed."""
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


def _replied_video_document(message: Message):
    replied = message.reply_to_message
    if not replied:
        return None, None
    if replied.video:
        name = replied.video.file_name or f"video_{replied.id}.mp4"
        return replied.video, name
    if replied.document:
        name = replied.document.file_name or ""
        if name.lower().endswith(VIDEO_EXTS):
            return replied.document, name
    return None, None


async def _trim_stream_copy(in_path, out_path, start, end, status, user_id):
    cmd = ["ffmpeg", "-hide_banner", "-y", "-ss", str(start), "-i", in_path]
    if end is not None:
        # -t (duration) rather than -to (absolute end timestamp) — with -ss
        # given as an input option, -to's "measured from start of file or
        # from the seek point" behaviour differs across ffmpeg versions,
        # while -t <duration> after -i is unambiguous either way.
        cmd += ["-t", str(max(0.0, end - start))]
    cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", out_path]
    duration = (end - start) if end is not None else None
    parse_line = make_ffmpeg_progress_parser(duration or 0, title="Trimming (fast copy)...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Trimming (fast copy)...", parse_line, user_id=user_id, queue_label="Trim video",
    )
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0, tail


async def _trim_reencode(in_path, out_path, start, end, status, user_id):
    # -ss after -i here (rather than before, like the fast path) trades a
    # slower seek for frame-accurate decoding — appropriate since this path
    # only runs when the fast copy already failed, i.e. accuracy over speed.
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", in_path, "-ss", str(start)]
    if end is not None:
        cmd += ["-t", str(max(0.0, end - start))]
    cmd += ["-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-c:a", "aac", out_path]
    duration = (end - start) if end is not None else None
    parse_line = make_ffmpeg_progress_parser(duration or 0, title="Trimming (re-encoding)...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Trimming (re-encoding)...", parse_line, user_id=user_id, queue_label="Trim video",
    )
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0, tail


@Client.on_message(filters.command("trim") & filters.private)
async def trim_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    media, orig_name = _replied_video_document(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>video</b> (or a video sent as a file) with "
            f"<code>/trim</code> and a start/end time.\n\n"
            f"{E_INFO} <b>Usage:</b>\n"
            f"<code>/trim 00:01:10 00:02:30</code> — keep 01:10 to 02:30\n"
            f"<code>/trim 90 150</code> — plain seconds also work\n"
            f"<code>/trim 00:01:10</code> — trim from 01:10 to the end</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/trim &lt;start&gt; [end]</code>\n"
            f"e.g. <code>/trim 00:01:10 00:02:30</code>",
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
        return await message.reply_text(
            f"<b>{E_CROSS} End time must be after start time.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    replied = message.reply_to_message
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("trim"), f"{user_id}_{replied.id}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    orig_name = safe_filename(orig_name, f"video_{replied.id}.mp4")
    in_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    duration, _, _ = await asyncio.to_thread(get_video_metadata, in_path)
    if duration and start >= duration:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Start time ({fmt_hms(start)}) is past the video's length "
            f"({fmt_hms(duration)}).</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    if end is not None and duration and end > duration:
        end = duration

    base_name, ext = os.path.splitext(orig_name)
    out_name = f"{base_name}_trim{ext or '.mp4'}"
    out_path = os.path.join(temp_dir, out_name)

    ok, tail = await _trim_stream_copy(in_path, out_path, start, end, status, user_id)
    if not ok:
        await safe_edit(status.edit_text, 
            f"<b>{E_GEAR} Fast trim failed — re-encoding instead...</b>",
            parse_mode=enums.ParseMode.HTML,
        )
        ok, tail = await _trim_reencode(in_path, out_path, start, end, status, user_id)

    if not ok:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Trim failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        os.remove(in_path)
    except Exception:
        pass

    out_duration, _, _ = await asyncio.to_thread(get_video_metadata, out_path)
    range_label = f"{fmt_hms(start)} → {fmt_hms(end) if end is not None else 'end'}"

    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_SCIS} Trimmed: <b>{range_label}</b>",
        file_name=out_name, duration=out_duration, quality="Trimmed",
    )

    shutil.rmtree(temp_dir, ignore_errors=True)
