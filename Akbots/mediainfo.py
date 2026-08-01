# Akbots - Don't Remove Credit - @AkBots_Official
#
# MediaInfo — wired in from the Mediainfo-Bot-master repo. Reply to any
# video/audio/document with /mediainfo to get its technical details
# (codecs, resolution, bitrate, audio/subtitle tracks, etc.) via
# mediainfo/ffprobe.
#
# Unlike a naive port (download the whole file, then run mediainfo on the
# local copy), this probes the file over Akbots/mediainfo_lib/streamer.py's
# local HTTP proxy: ffprobe/mediainfo makes its own Range requests against
# that proxy, which pulls only the specific byte ranges it asks for
# (container header at the start, moov atom often at the end) straight
# from Telegram's CDN via pyrogram's stream_media. The full file is never
# written to disk, so this stays fast and low-disk even on multi-GB files.
#
# Command-gated (rather than a blanket handler on every video/audio/
# document) so it doesn't collide with the dozen other plugins in this bot
# that already act on incoming media.

import io
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

from Akbots.mediainfo_lib.probe import extract_mediainfo
from Akbots.mediainfo_lib.formatter import format_output
from Akbots.direct_utils import safe_edit

E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_MAG   = '🔍'
E_CHART = '📊'

STREAM_HOST = "127.0.0.1"


def _stream_port() -> int:
    from Akbots.mediainfo_lib.streamer import _RUNNING_PORT
    return _RUNNING_PORT or 8099


@Client.on_message(filters.private & filters.command(["mediainfo", "minfo"]))
async def mediainfo_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if await db.is_banned(user_id):
        return await message.reply_text(
            f"<b>{E_CROSS} You are banned from using this bot.</b>", parse_mode=enums.ParseMode.HTML
        )

    replied = message.reply_to_message
    media = replied and (replied.video or replied.audio or replied.document)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a video/audio/document with "
            f"<code>/mediainfo</code>.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    file_name = getattr(media, "file_name", None) or "File"
    status = await message.reply_text(
        f"<b>{E_MAG} Extracting MediaInfo…</b>\n<i>Probing the file over the network — no download needed.</i>",
        parse_mode=enums.ParseMode.HTML
    )

    try:
        url = f"http://{STREAM_HOST}:{_stream_port()}/stream/{replied.chat.id}/{replied.id}"
        data = await extract_mediainfo(url)
        text = format_output(data)
    except Exception as e:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    full_text = f"<b>{E_CHART} MediaInfo for:</b> <code>{file_name}</code>\n\n<blockquote expandable>{text}</blockquote>"

    if len(full_text) > 4096:
        clean_text = text.replace("```", "").strip()
        bio = io.BytesIO(clean_text.encode("utf-8"))
        bio.name = f"{file_name}.txt"
        await safe_edit(status.edit_text, 
            f"<b>{E_CHART} MediaInfo for:</b> <code>{file_name}</code> is too long to show inline — "
            f"sending it as a text file.",
            parse_mode=enums.ParseMode.HTML
        )
        await message.reply_document(
            document=bio,
            caption=f"<blockquote><b>{E_CHART} MediaInfo for:</b> <code>{file_name}</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    else:
        await safe_edit(status.edit_text, full_text, parse_mode=enums.ParseMode.HTML)
