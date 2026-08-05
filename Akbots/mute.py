# Akbots - Don't Remove Credit - @AkBots_Official
#
# Mute Audio — /mute
#
# Reply to a video (or a video sent as a document) with /mute to strip its
# audio track completely (video-only output, same container/codec, no
# re-encode needed since we're just dropping the audio stream with -an).
#
# Ported over from NexusMLTB's ffmpeg-based approach, but wired into this
# bot's direct_utils pipeline (progress bar, upload_file with auto-sample/
# auto-split, db user tracking) same as trim.py / videomerge.py.

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, make_output_folder, safe_filename, VIDEO_EXTS,
)
from Akbots.direct_utils import safe_edit

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_MUTE  = '🔇'


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


@Client.on_message(filters.command("mute") & filters.private)
async def mute_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    media, orig_name = _replied_video_document(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>ᴠɪᴅᴇᴏ</b> (or a video sent as a file) with "
            f"<code>/mute</code> to remove its audio track.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    replied = message.reply_to_message
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("mute"), f"{user_id}_{replied.id}")
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

    base_name, ext = os.path.splitext(orig_name)
    out_name = f"{base_name}_muted{ext or '.mp4'}"
    out_path = os.path.join(temp_dir, out_name)

    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", in_path, "-c", "copy", "-an", out_path]
    parse_line = make_ffmpeg_progress_parser(duration or 0, title="Muting audio...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Muting audio...", parse_line, user_id=user_id, queue_label="Mute video",
    )

    if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Mute failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        os.remove(in_path)
    except Exception:
        pass

    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_MUTE} Audio track removed",
        file_name=out_name, duration=duration, quality="Muted",
    )

    shutil.rmtree(temp_dir, ignore_errors=True)
