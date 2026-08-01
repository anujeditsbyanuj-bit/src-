# Akbots - Don't Remove Credit - @AkBots_Official
#
# RClone Upload — /rclone, /setrcloneconf, /rclonelist
#
#   /setrcloneconf         (admin) — upload rclone.conf to enable this.
#   /rclone <remote:path>  — reply to any file/video/audio/document; the
#                            bot downloads it from Telegram then `rclone
#                            copy`'s it to the given remote path, e.g.
#                            /rclone gdrive:Movies
#   /rclonelist <remote:path> — list files at that remote path.
#
# Ported over from NexusMLTB's utils/rclone.py (subprocess wrapper around
# the `rclone` binary), wired into direct_utils' download/progress/status
# conventions used by every other plugin in this bot.

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from config import RCLONE_PATH, RCLONE_CONFIG_PATH, ADMINS
from Akbots.direct_utils import make_output_folder, safe_filename, safe_edit

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_CLOUD = '☁️'

_AWAITING_CONF = set()


def _rclone_available() -> bool:
    return shutil.which(RCLONE_PATH) is not None and os.path.exists(RCLONE_CONFIG_PATH)


async def _run_rclone(args: list) -> tuple:
    cmd = [RCLONE_PATH, "--config", RCLONE_CONFIG_PATH] + args
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")


@Client.on_message(filters.command("setrcloneconf") & filters.private)
async def setrcloneconf_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        return await message.reply_text(f"<b>{E_CROSS} Admins only.</b>", parse_mode=enums.ParseMode.HTML)
    _AWAITING_CONF.add(user_id)
    await message.reply_text(
        f"<b>{E_CLOUD} Send the <code>rclone.conf</code> file now</b> "
        f"(generate it locally with <code>rclone config</code>).",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.private & filters.document & filters.create(
    lambda _, __, m: bool(m.document and (m.document.file_name or "").lower().endswith(".conf"))
))
async def rcloneconf_receive(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in _AWAITING_CONF:
        return
    _AWAITING_CONF.discard(user_id)
    os.makedirs(os.path.dirname(RCLONE_CONFIG_PATH), exist_ok=True)
    try:
        await client.download_media(message, file_name=RCLONE_CONFIG_PATH)
    except Exception as e:
        return await message.reply_text(f"<b>{E_CROSS} Failed to save:</b> <code>{e}</code>",
                                         parse_mode=enums.ParseMode.HTML)
    if not shutil.which(RCLONE_PATH):
        return await message.reply_text(
            f"<b>{E_WARN} rclone.conf saved, but the <code>rclone</code> binary isn't installed "
            f"on this server.</b> Install it, then /rclone will work.",
            parse_mode=enums.ParseMode.HTML,
        )
    await message.reply_text(f"<b>{E_CHECK} rclone.conf saved. /rclone is ready to use.</b>",
                              parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("rclone") & filters.private)
async def rclone_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if not _rclone_available():
        return await message.reply_text(
            f"<b>{E_WARN} RClone isn't set up yet.</b> An admin needs to run "
            f"<code>/setrcloneconf</code> first (and have the <code>rclone</code> binary installed).",
            parse_mode=enums.ParseMode.HTML,
        )

    replied = message.reply_to_message
    media = replied and (replied.video or replied.audio or replied.document or replied.photo)
    if not media or len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_INFO} Reply to a file with <code>/rclone &lt;remote:path&gt;</code>\n"
            f"e.g. <code>/rclone gdrive:Movies</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    remote_path = message.command[1]
    orig_name = getattr(media, "file_name", None) or f"file_{replied.id}"
    orig_name = safe_filename(orig_name, f"file_{replied.id}")

    status = await message.reply_text(f"<b>{E_GEAR} Downloading from Telegram...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("rclone"), f"{user_id}_{replied.id}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    local_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=local_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOUD} Uploading to <code>{remote_path}</code>...</b>",
                            parse_mode=enums.ParseMode.HTML)

    rc, out, err = await _run_rclone(["copy", local_path, remote_path, "--progress"])
    shutil.rmtree(temp_dir, ignore_errors=True)

    if rc != 0:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} RClone upload failed:</b>\n<code>{(err or out)[-500:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    await safe_edit(status.edit_text, 
        f"<b>{E_CHECK} Uploaded to RClone</b>\n\n<b>File:</b> <code>{orig_name}</code>\n"
        f"<b>Remote:</b> <code>{remote_path}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("rclonelist") & filters.private)
async def rclonelist_cmd(client: Client, message: Message):
    if not _rclone_available():
        return await message.reply_text(f"<b>{E_WARN} RClone isn't set up yet.</b>", parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/rclonelist &lt;remote:path&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    remote_path = message.command[1]
    status = await message.reply_text(f"<b>{E_GEAR} Listing <code>{remote_path}</code>...</b>",
                                       parse_mode=enums.ParseMode.HTML)
    rc, out, err = await _run_rclone(["lsf", remote_path])
    if rc != 0:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b>\n<code>{(err or out)[-500:]}</code>",
                                       parse_mode=enums.ParseMode.HTML)
    lines = [l.strip() for l in out.splitlines() if l.strip()][:50]
    listing = "\n".join(f"• <code>{l}</code>" for l in lines) or f"<i>{E_INFO} Empty.</i>"
    await safe_edit(status.edit_text, f"<b>{E_CLOUD} {remote_path}</b>\n\n{listing}", parse_mode=enums.ParseMode.HTML)
