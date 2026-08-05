# Akbots - Don't Remove Credit - @AkBots_Official
#
# Video Splitter — /splitvideo
#
#   /splitvideo parts 4        -> split into 4 equal-length pieces
#   /splitvideo 00:05:00       -> split into pieces of ~5 min each
#   /splitvideo 300            -> plain seconds also accepted (per part)
#
# This is different from the automatic size-based splitting that already
# happens inside upload_file() (split_file() in direct_utils, which only
# kicks in when a finished upload is over Telegram's 2GB limit) — this is
# a user-requested, duration-driven cut into N playable pieces, each one
# uploaded back separately as its own status/progress cycle.
#
# Uses ffmpeg stream-copy (-c copy) per segment for speed; each segment is
# probed afterwards so the caption shows its real length.

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

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_SCIS  = '✂️'

MAX_PARTS = 30


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


@Client.on_message(filters.command("splitvideo") & filters.private)
async def splitvideo_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    media, orig_name = _replied_video_document(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>ᴠɪᴅᴇᴏ</b> with <code>/splitvideo</code>.\n\n"
            f"{E_INFO} <b>ᴜsᴀɢᴇ:</b>\n"
            f"<code>/splitvideo parts 4</code> — split into 4 equal pieces\n"
            f"<code>/splitvideo 00:05:00</code> — ~5 min per piece\n"
            f"<code>/splitvideo 300</code> — 300 sec per piece</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/splitvideo parts &lt;n&gt;</code> or "
            f"<code>/splitvideo &lt;duration per part&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    args = message.command[1:]
    replied = message.reply_to_message
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("splitvideo"), f"{user_id}_{replied.id}")
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
    if not duration:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't read video duration.</b>",
                                       parse_mode=enums.ParseMode.HTML)

    # Work out per-part duration
    if args[0].lower() == "parts":
        if len(args) < 2 or not args[1].isdigit():
            shutil.rmtree(temp_dir, ignore_errors=True)
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Usage:</b> <code>/splitvideo parts &lt;n&gt;</code>",
                                           parse_mode=enums.ParseMode.HTML)
        n_parts = int(args[1])
        if n_parts < 2 or n_parts > MAX_PARTS:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Parts must be between 2 and {MAX_PARTS}.</b>",
                                           parse_mode=enums.ParseMode.HTML)
        part_len = duration / n_parts
    else:
        part_len = _parse_timestamp(args[0])
        if not part_len or part_len <= 0:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't parse that duration.</b>",
                                           parse_mode=enums.ParseMode.HTML)
        n_parts = max(2, int(-(-duration // part_len)))  # ceil
        if n_parts > MAX_PARTS:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return await safe_edit(status.edit_text, 
                f"<b>{E_CROSS} That duration would create {n_parts} parts (max {MAX_PARTS}).</b> "
                f"Use a bigger duration.", parse_mode=enums.ParseMode.HTML)

    base_name, ext = os.path.splitext(orig_name)
    ext = ext or ".mp4"

    part_paths = []
    for i in range(n_parts):
        start = i * part_len
        if start >= duration:
            break
        out_name = f"{base_name}_part{i + 1}{ext}"
        out_path = os.path.join(temp_dir, out_name)
        cmd = ["ffmpeg", "-hide_banner", "-y", "-ss", str(start), "-i", in_path]
        if i < n_parts - 1:
            cmd += ["-t", str(part_len)]
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", out_path]
        parse_line = make_ffmpeg_progress_parser(part_len, title=f"Splitting part {i + 1}/{n_parts}...")
        rc, tail = await run_subprocess_with_progress(
            cmd, status, f"Splitting part {i + 1}/{n_parts}...", parse_line,
            user_id=user_id, queue_label="Split video",
        )
        if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return await safe_edit(status.edit_text, 
                f"<b>{E_CROSS} Split failed on part {i + 1}.</b>\n\n<code>{tail[-300:]}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        part_paths.append((out_path, out_name))

    try:
        os.remove(in_path)
    except Exception:
        pass

    await safe_edit(status.edit_text, 
        f"<b>{E_CHECK} Split into {len(part_paths)} parts — uploading...</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    for idx, (p_path, p_name) in enumerate(part_paths, 1):
        p_duration, _, _ = await asyncio.to_thread(get_video_metadata, p_path)
        upload_status = status if idx == len(part_paths) else await message.reply_text(
            f"<b>{E_GEAR} Uploading part {idx}/{len(part_paths)}...</b>", parse_mode=enums.ParseMode.HTML)
        await upload_file(
            client, message, p_path, upload_status,
            f"<b>{p_name}</b>\n\n{E_SCIS} Part {idx}/{len(part_paths)} ({fmt_hms(p_duration)})",
            file_name=p_name, duration=p_duration, quality=f"Part {idx}/{len(part_paths)}",
        )

    shutil.rmtree(temp_dir, ignore_errors=True)
