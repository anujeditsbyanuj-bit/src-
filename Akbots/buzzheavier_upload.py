# Akbots - Don't Remove Credit - @AkBots_Official
#
# BuzzHeavier Upload — /buzzupload
#
# Reply to any file/video/audio/document with /buzzupload — downloads it
# from Telegram, uploads it to BuzzHeavier, and returns the share link.
# (Uses the same aiohttp multipart approach as NexusMLTB's
# utils/third_party.py upload_buzzheavier(), wired into this bot's
# download/progress/status conventions.)

import os
import shutil
import aiohttp
import aiofiles
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import make_output_folder, safe_filename

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_UP    = '⬆️'

BUZZHEAVIER_UPLOAD_URL = "https://buzzheavier.com/api/upload"


async def upload_buzzheavier(file_path: str) -> str | None:
    name = os.path.basename(file_path)
    async with aiohttp.ClientSession() as session:
        async with aiofiles.open(file_path, "rb") as f:
            content = await f.read()
        form = aiohttp.FormData()
        form.add_field("file", content, filename=name)
        async with session.post(BUZZHEAVIER_UPLOAD_URL, data=form, timeout=aiohttp.ClientTimeout(total=1800)) as r:
            if r.status != 200:
                return None
            resp = await r.json()
            return resp.get("url") or resp.get("data", {}).get("url")


@Client.on_message(filters.command("buzzupload") & filters.private)
async def buzzupload_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    replied = message.reply_to_message
    media = replied and (replied.video or replied.audio or replied.document or replied.photo)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_INFO} Reply to a <b>file/video/audio</b> with <code>/buzzupload</code> "
            f"to upload it to BuzzHeavier and get a share link.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    orig_name = getattr(media, "file_name", None) or f"file_{replied.id}"
    orig_name = safe_filename(orig_name, f"file_{replied.id}")

    status = await message.reply_text(f"<b>{E_GEAR} Downloading from Telegram...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("buzzheavier"), f"{user_id}_{replied.id}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    local_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=local_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    await status.edit_text(f"<b>{E_UP} Uploading to BuzzHeavier...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        url = await upload_buzzheavier(local_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Upload error:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    shutil.rmtree(temp_dir, ignore_errors=True)

    if not url:
        return await status.edit_text(f"<b>{E_CROSS} BuzzHeavier upload failed.</b>",
                                       parse_mode=enums.ParseMode.HTML)

    await status.edit_text(
        f"<b>{E_CHECK} Uploaded to BuzzHeavier</b>\n\n<b>File:</b> <code>{orig_name}</code>\n"
        f"<b>Link:</b> {url}",
        parse_mode=enums.ParseMode.HTML, disable_web_page_preview=False,
    )
