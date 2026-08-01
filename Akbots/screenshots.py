# Akbots - Don't Remove Credit - @AkBots_Official
#
# Video Screenshots — grabs evenly-spaced preview frames from a video via
# ffmpeg and sends them as a photo album.
#
# Two ways to use it:
#   /screenshots [count]  — reply to any video/video-document to get
#                           `count` (default 4, max 10) screenshots right
#                           now, one-off.
#   /autoscreenshots on|off — toggle setting: every future video upload
#                           (any downloader plugin, since the hook lives
#                           in direct_utils._send_one) automatically gets
#                           a set of screenshots sent right after it.

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InputMediaPhoto
from database.db import db

from Akbots.direct_utils import extract_screenshots, VIDEO_EXTS

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_TIP   = '<emoji id=5422439311196834318>💡</emoji>'
E_CAM   = '📸'

MAX_SHOTS = 10


@Client.on_message(filters.private & filters.command("screenshots"))
async def screenshots_cmd(client: Client, message: Message):
    replied = message.reply_to_message
    media = replied and (replied.video or replied.document)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a video with <code>/screenshots [count]</code>.\n"
            f"{E_TIP} Default is 4 screenshots, max {MAX_SHOTS}.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    if replied.document:
        fname = replied.document.file_name or ""
        if not fname.lower().endswith(VIDEO_EXTS):
            return await message.reply_text(f"<b>{E_CROSS} Not a video file!</b>", parse_mode=enums.ParseMode.HTML)

    count = 4
    if len(message.command) > 1:
        try:
            count = max(1, min(MAX_SHOTS, int(message.command[1])))
        except ValueError:
            return await message.reply_text(
                f"<b>{E_WARN} Usage:</b> <code>/screenshots 6</code> (1-{MAX_SHOTS})",
                parse_mode=enums.ParseMode.HTML
            )

    user_id = message.from_user.id
    status = await message.reply_text(f"<b>{E_CAM} Downloading video...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "screenshots", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, "input.mp4")

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await status.edit_text(f"<b>{E_CAM} Generating {count} screenshots...</b>", parse_mode=enums.ParseMode.HTML)
    shots = await asyncio.to_thread(extract_screenshots, in_path, temp_dir, count)

    if not shots:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't generate screenshots.</b> The file may be corrupt or not a real video.",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        await client.send_media_group(
            chat_id=message.chat.id,
            media=[InputMediaPhoto(s) for s in shots],
            reply_to_message_id=message.id,
        )
        await status.delete()
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Failed to send:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@Client.on_message(filters.private & filters.command("autoscreenshots"))
async def autoscreenshots_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        current = await db.get_auto_screenshots(user_id)
        return await message.reply_text(
            f"<blockquote>{E_CAM} <b>Auto Screenshots:</b> <code>{'ON' if current else 'OFF'}</code>\n\n"
            f"<b>Usage:</b> <code>/autoscreenshots on</code> or <code>/autoscreenshots off</code>\n"
            f"{E_TIP} When ON, every video this bot uploads for you (any downloader) is automatically "
            f"followed by 4 preview screenshots.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    choice = message.command[1].lower()
    if choice not in ("on", "off"):
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/autoscreenshots on</code> or <code>/autoscreenshots off</code>",
            parse_mode=enums.ParseMode.HTML
        )

    await db.set_auto_screenshots(user_id, choice == "on")
    await message.reply_text(
        f"<b>{E_CHECK} Auto Screenshots {'enabled' if choice == 'on' else 'disabled'}.</b>",
        parse_mode=enums.ParseMode.HTML
    )
