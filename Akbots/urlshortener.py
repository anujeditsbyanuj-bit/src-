# Akbots - Don't Remove Credit - @AkBots_Official
#
# URL Shortener — /shorten <url>
#
# This bot already had shortener_bypass.py (bypass/unshorten an existing
# short link back to the real URL) and a private shorten_url() helper
# inside filestore.py (used internally to wrap file-store delivery
# links). This adds the missing piece: a plain user-facing command to
# turn ANY link into a short one, reusing the same configured shortener
# (FILESTORE_SHORTENER_API_URL/API_TOKEN in config.py — vplink.in by
# default) instead of duplicating the API call.

import re
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from config import FILESTORE_SHORTENER_API_TOKEN, FILESTORE_SHORTENER_NAME
from Akbots.filestore import shorten_url

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN  = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_INFO  = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'
E_LINK  = '🔗'

URL_RE = re.compile(r"https?://\S+")


@Client.on_message(filters.command("shorten") & filters.private)
async def shorten_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if not FILESTORE_SHORTENER_API_TOKEN:
        return await message.reply_text(
            f"<b>{E_WARN} No shortener configured.</b> Set "
            f"<code>FILESTORE_SHORTENER_API_TOKEN</code> (and optionally "
            f"<code>FILESTORE_SHORTENER_API_URL</code>/<code>FILESTORE_SHORTENER_NAME</code>) "
            f"in the environment first.",
            parse_mode=enums.ParseMode.HTML,
        )

    target = None
    if len(message.command) > 1:
        target = message.command[1]
    elif message.reply_to_message:
        text = message.reply_to_message.text or message.reply_to_message.caption or ""
        m = URL_RE.search(text)
        target = m.group() if m else None

    if not target or not URL_RE.match(target):
        return await message.reply_text(
            f"<blockquote>{E_INFO} <b>ᴜsᴀɢᴇ:</b> <code>/shorten &lt;url&gt;</code>\n"
            f"or reply to a message containing a link with <code>/shorten</code>.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    short = await shorten_url(target)
    if short == target:
        return await message.reply_text(
            f"<b>{E_CROSS} Couldn't shorten that link right now</b> (shortener API error) — "
            f"here's the original:\n<code>{target}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    await message.reply_text(
        f"<b>{E_LINK} Shortened via {FILESTORE_SHORTENER_NAME}</b>\n\n{short}",
        parse_mode=enums.ParseMode.HTML,
    )
