# Akbots - Don't Remove Credit - @AkBots_Official
#
# Metadata editing — simplified port of File-Rename-4GB-Bot-Auto-Rename-Bot's
# plugins/metadata.py + helper/ffmpeg.py's change_metadata(). The source bot
# used a "--change-title X --change-author Y" mini-syntax and a Telegram
# .ask() prompt; this version is a single saved text applied as the
# container title/author (and per-stream titles) — reply to any
# video/audio/document with /apply_metadata to remux it in place.
#
# Fast stream-copy remux (-c copy), so this doesn't re-encode — only the
# metadata tags change, output size/quality is identical to the input.

import os
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

from Akbots.direct_utils import upload_file

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'
E_TRASH  = '<emoji id=5260293700088511294>🗑</emoji>'
E_TIP    = '<emoji id=5422439311196834318>💡</emoji>'
E_BOLT   = '⚡️'


@Client.on_message(filters.private & filters.command("set_metadata"))
async def set_metadata_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>Usage:</b> <code>/set_metadata Your Text</code>\n\n"
            f"{E_TIP} This text is written as the title/author tag on files you "
            f"process with <code>/apply_metadata</code>.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    text = message.text.split(" ", 1)[1]
    await db.set_metadata(message.from_user.id, text)
    await message.reply_text(
        f"<b>{E_CHECK} Metadata text saved:</b> <code>{text}</code>", parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("see_metadata"))
async def see_metadata_cmd(client: Client, message: Message):
    text = await db.get_metadata(message.from_user.id)
    reply = f"<b>{E_GEAR} Your metadata text:</b> <code>{text}</code>" if text else \
            f"<b>{E_WARN} No metadata text set.</b> Use <code>/set_metadata</code>."
    await message.reply_text(reply, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("del_metadata"))
async def del_metadata_cmd(client: Client, message: Message):
    await db.set_metadata(message.from_user.id, None)
    await message.reply_text(f"<b>{E_TRASH} Metadata text removed.</b>", parse_mode=enums.ParseMode.HTML)


async def _remux_with_metadata(input_path: str, output_path: str, text: str) -> bool:
    """Stream-copies input_path -> output_path with title/author/comment
    metadata tags set to `text`. Returns True on success."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", input_path, "-map", "0", "-c", "copy",
        "-metadata", f"title={text}",
        "-metadata", f"author={text}",
        "-metadata", "comment=Renamed via Akbots",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    await proc.communicate()
    return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0


@Client.on_message(filters.private & filters.command("apply_metadata"))
async def apply_metadata_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    text = await db.get_metadata(user_id)
    if not text:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>No metadata text set!</b>\n\n"
            f"Use <code>/set_metadata Your Text</code> first, then reply to a file with "
            f"<code>/apply_metadata</code>.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    replied = message.reply_to_message
    media = replied and (replied.video or replied.audio or replied.document)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a video/audio/document with <code>/apply_metadata</code>.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    file_name = getattr(media, "file_name", None) or f"file_{replied.id}"
    status = await message.reply_text(f"<b>{E_BOLT} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "metadata", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, file_name)
    ext = os.path.splitext(file_name)[1] or ".mkv"
    out_path = os.path.join(temp_dir, os.path.splitext(file_name)[0] + "_meta" + ext)

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    await status.edit_text(f"<b>{E_BOLT} Writing metadata...</b>", parse_mode=enums.ParseMode.HTML)
    ok = await _remux_with_metadata(in_path, out_path, text)

    if not ok:
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        return await status.edit_text(
            f"<b>{E_CROSS} Metadata write failed.</b> The file's container may not support it.",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        os.remove(in_path)
    except Exception:
        pass

    await upload_file(client, message, out_path, status, f"<b>{file_name}</b>\n\n{E_GEAR} Metadata updated", file_name=file_name)

    try:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass
