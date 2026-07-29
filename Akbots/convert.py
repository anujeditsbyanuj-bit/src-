# Akbots - Don't Remove Credit - @AkBots_Official
#
# Format Conversion:
#   /tovideo     — reply to a video sent/received as a plain document and
#                   re-send it as a proper streamable video (downloads +
#                   goes through the normal upload_file path, which
#                   auto-detects by extension, so it comes out as video
#                   for free — no re-encode, same bytes).
#   /todocument  — reply to a video and re-send it as a plain document
#                   (loses the in-app player, keeps the exact original
#                   file — no re-encode).
#   /tomp4       — reply to a video in ANY container (mkv, avi, flv, wmv,
#                   webm, ...) and get back an actual .mp4 — a real
#                   conversion via ffmpeg (stream-copy remux first, falls
#                   back to re-encode only if the source codec genuinely
#                   isn't mp4-compatible).

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    upload_file, get_video_metadata, remux_to_mp4,
    run_subprocess_with_progress, make_ffmpeg_progress_parser, VIDEO_EXTS,
)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'


def _replied_video_document(message: Message):
    """Returns (media, orig_name) if the replied message is a video OR a
    document whose filename looks like a video, else (None, None)."""
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


@Client.on_message(filters.private & filters.command("tovideo"))
async def tovideo_cmd(client: Client, message: Message):
    media, orig_name = _replied_video_document(message)
    if not media or not message.reply_to_message.document:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a video sent as a <b>document/file</b> with "
            f"<code>/tovideo</code> to re-send it as a proper playable video.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    user_id = message.from_user.id
    replied = message.reply_to_message
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "tovideo", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    # upload_file's shared _send_one already auto-detects by extension and
    # sends VIDEO_EXTS files as a real video — no re-encode needed here,
    # the file was already a video, just mis-delivered as a document.
    await upload_file(client, message, in_path, status, f"<b>{orig_name}</b>", file_name=orig_name)
    shutil.rmtree(temp_dir, ignore_errors=True)


@Client.on_message(filters.private & filters.command("todocument"))
async def todocument_cmd(client: Client, message: Message):
    replied = message.reply_to_message
    if not replied or not replied.video:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>video</b> with <code>/todocument</code> to "
            f"re-send it as a plain file (no re-encode, keeps the original quality/codec, loses "
            f"the in-app player).</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    user_id = message.from_user.id
    orig_name = replied.video.file_name or f"video_{replied.id}.mp4"
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "todocument", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    # force_document=True overrides the user's saved /upload_mode for just
    # this one call, without touching that saved setting.
    await upload_file(client, message, in_path, status, f"<b>{orig_name}</b>",
                       file_name=orig_name, force_document=True)
    shutil.rmtree(temp_dir, ignore_errors=True)


@Client.on_message(filters.private & filters.command("tomp4"))
async def tomp4_cmd(client: Client, message: Message):
    media, orig_name = _replied_video_document(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a video (any container — mkv, avi, flv, webm, wmv...) "
            f"with <code>/tomp4</code> to convert it to a real .mp4.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    base_name = os.path.splitext(orig_name)[0]
    if orig_name.lower().endswith(".mp4"):
        return await message.reply_text(f"<b>{E_WARN} This is already an .mp4 file.</b>", parse_mode=enums.ParseMode.HTML)

    user_id = message.from_user.id
    replied = message.reply_to_message
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "tomp4", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)
    out_path = os.path.join(temp_dir, base_name + ".mp4")

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await status.edit_text(f"<b>{E_GEAR} Converting to MP4...</b>", parse_mode=enums.ParseMode.HTML)
    ok = await asyncio.to_thread(remux_to_mp4, in_path, out_path)

    if not ok:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(
            f"<b>{E_CROSS} Conversion failed.</b> The source file may be corrupt or use an "
            f"unsupported codec.",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        os.remove(in_path)
    except OSError:
        pass

    await upload_file(client, message, out_path, status, f"<b>{base_name}.mp4</b>", file_name=f"{base_name}.mp4")
    shutil.rmtree(temp_dir, ignore_errors=True)
