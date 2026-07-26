# Akbots - Don't Remove Credit - @AkBots_Official
#
# Audio extraction — ported from File-Rename-4GB-Bot-Auto-Rename-Bot's
# plugins/audio_extract.py + helper/ffmpeg.py's extract_audio_from_video(),
# rewritten to reuse Akbots/direct_utils.py (progress bar, upload, splitting)
# instead of the source bot's own send_audio call.

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, VIDEO_EXTS,
)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_TIP   = '<emoji id=5422439311196834318>💡</emoji>'
E_MUSIC = '🎵'


@Client.on_message(filters.private & filters.command(["extract_audio"]))
async def extract_audio_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    replied = message.reply_to_message
    media = replied and (replied.video or replied.document)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_MUSIC} <b>Extract Audio</b>\n\n"
            f"Reply to a <b>video file</b> with <code>/extract_audio</code> to pull "
            f"its audio out as an MP3.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    if replied.document:
        file_name = replied.document.file_name or ""
        if not file_name.lower().endswith(VIDEO_EXTS):
            return await message.reply_text(
                f"<blockquote>{E_CROSS} <b>Not a video file!</b>\n\n"
                f"Reply to a video (.mp4, .mkv, .avi, etc.) to extract its audio.</blockquote>",
                parse_mode=enums.ParseMode.HTML
            )

    orig_name = (replied.video and replied.video.file_name) or \
                (replied.document and replied.document.file_name) or f"video_{replied.id}"
    base_name = os.path.splitext(orig_name)[0]

    status = await message.reply_text(f"<b>{E_MUSIC} Downloading video...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "audio_extract", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)
    out_path = os.path.join(temp_dir, base_name + ".mp3")

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    duration, _, _ = await asyncio.to_thread(get_video_metadata, in_path)

    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", in_path,
        "-vn", "-acodec", "libmp3lame", "-b:a", "192k", out_path,
    ]
    parse_line = make_ffmpeg_progress_parser(duration or 0, title="Extracting Audio...")
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, "Extracting Audio...", parse_line,
        user_id=message.from_user.id, queue_label="Audio extraction",
    )

    if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(
            f"<b>{E_CROSS} Audio extraction failed.</b>\n\n"
            f"{E_TIP} The video may not contain an audio track.\n"
            f"<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        os.remove(in_path)
    except Exception:
        pass

    await upload_file(client, message, out_path, status, f"<b>{E_MUSIC} {base_name}.mp3</b>", file_name=f"{base_name}.mp3")

    shutil.rmtree(temp_dir, ignore_errors=True)
