# Akbots - Don't Remove Credit - @AkBots_Official
#
# Sample Video Generation — cuts a short preview clip from a video via
# ffmpeg (stream-copy first, re-encode fallback) and sends it.
#
# Two ways to use it:
#   /sample [seconds]      — reply to any video/video-document to get a
#                            preview clip right now (default 20s, max 60s).
#   /autosample on|off     — toggle setting: every future full-video
#                            upload (any downloader plugin, hook lives in
#                            direct_utils.upload_file) is preceded by an
#                            auto-generated short sample clip.

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

from Akbots.direct_utils import make_sample_clip, VIDEO_EXTS

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_TIP   = '<emoji id=5422439311196834318>💡</emoji>'
E_FILM  = '🎞'

MAX_SAMPLE_SECONDS = 60


@Client.on_message(filters.private & filters.command("sample"))
async def sample_cmd(client: Client, message: Message):
    replied = message.reply_to_message
    media = replied and (replied.video or replied.document)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a video with <code>/sample [seconds]</code>.\n"
            f"{E_TIP} Default is 20 seconds, max {MAX_SAMPLE_SECONDS}.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    if replied.document:
        fname = replied.document.file_name or ""
        if not fname.lower().endswith(VIDEO_EXTS):
            return await message.reply_text(f"<b>{E_CROSS} Not a video file!</b>", parse_mode=enums.ParseMode.HTML)

    clip_seconds = 20
    if len(message.command) > 1:
        try:
            clip_seconds = max(3, min(MAX_SAMPLE_SECONDS, int(message.command[1])))
        except ValueError:
            return await message.reply_text(
                f"<b>{E_WARN} Usage:</b> <code>/sample 30</code> (3-{MAX_SAMPLE_SECONDS} seconds)",
                parse_mode=enums.ParseMode.HTML
            )

    user_id = message.from_user.id
    status = await message.reply_text(f"<b>{E_FILM} Downloading video...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "sample", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, "input.mp4")
    out_path = os.path.join(temp_dir, "sample.mp4")

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await status.edit_text(f"<b>{E_FILM} Generating {clip_seconds}s sample...</b>", parse_mode=enums.ParseMode.HTML)
    made = await asyncio.to_thread(make_sample_clip, in_path, out_path, clip_seconds)

    if not made:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't generate a sample.</b> The file may be corrupt, not a real video, "
            f"or shorter than the requested clip length.",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        await client.send_video(
            chat_id=message.chat.id, video=out_path,
            caption=f"{E_FILM} <b>Sample clip</b> ({clip_seconds}s)",
            reply_to_message_id=message.id,
            supports_streaming=True, parse_mode=enums.ParseMode.HTML,
        )
        await status.delete()
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Failed to send:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@Client.on_message(filters.private & filters.command("autosample"))
async def autosample_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        current = await db.get_auto_sample(user_id)
        return await message.reply_text(
            f"<blockquote>{E_FILM} <b>Auto Sample:</b> <code>{'ON' if current else 'OFF'}</code>\n\n"
            f"<b>Usage:</b> <code>/autosample on</code> or <code>/autosample off</code>\n"
            f"{E_TIP} When ON, every full-video upload (any downloader) is preceded by a short "
            f"20-second preview clip.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    choice = message.command[1].lower()
    if choice not in ("on", "off"):
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/autosample on</code> or <code>/autosample off</code>",
            parse_mode=enums.ParseMode.HTML
        )

    await db.set_auto_sample(user_id, choice == "on")
    await message.reply_text(
        f"<b>{E_CHECK} Auto Sample {'enabled' if choice == 'on' else 'disabled'}.</b>",
        parse_mode=enums.ParseMode.HTML
    )
