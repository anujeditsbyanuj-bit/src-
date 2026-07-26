"""
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None
File-to-Link: turns any document/video/audio sent to this bot into an
instant browser Stream link (range-request playback + MX Player / VLC /
PlayIt buttons) and a direct Download link.

Ported in from the standalone FILE-TO-LINK-BOT project. The actual
streaming engine lives in Akbots/filetolink/ (aiohttp server + Telegram
byte-range fetcher); this file is just the bot-facing plugin: it forwards
the incoming file to STREAM_BIN_CHANNEL, builds the two links, and replies
with the buttons.

Disabled automatically (auto handler + /link both no-op with a clear
message) if STREAM_BIN_CHANNEL / STREAM_URL aren't usable.
"""

import time
import logging

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS
from database.db import db
from Akbots.filetolink.render_template import get_size
from Akbots.filetolink.link_builder import links_ready, get_media, forward_and_link
from Akbots.filetolink.rate_limit import link_limiter

logger = logging.getLogger(__name__)


async def _gate(message: Message) -> bool:
    """Ban + per-user rate-limit check, shared by /link and the
    auto-generate-on-upload handler. Returns True if the request should
    proceed. Admins always bypass both checks."""
    user = message.from_user
    if not user:
        return True
    if user.id in ADMINS:
        return True

    try:
        if await db.is_banned(user.id):
            return False
    except Exception:
        pass  # fail open — a DB hiccup shouldn't block a legit user

    if not link_limiter.check(str(user.id)):
        retry_after = link_limiter.retry_after(str(user.id))
        try:
            await message.reply_text(
                f"⏳ You're generating links too quickly — please wait "
                f"~{retry_after}s and try again.",
                quote=True,
            )
        except Exception:
            pass
        return False

    return True


async def _generate_and_reply(client: Client, message: Message, media_msg: Message):
    file_obj = get_media(media_msg)
    if not file_obj:
        return

    file_name = file_obj.file_name or f"file_{int(time.time())}"
    file_size = get_size(file_obj.file_size or 0)

    try:
        links = await forward_and_link(client, media_msg)
    except Exception as e:
        logger.warning(f"[filetolink] Could not forward to STREAM_BIN_CHANNEL: {e}")
        await message.reply_text(
            "⚠️ Couldn't generate a link right now — make sure this bot is an "
            "**admin** in the STREAM_BIN_CHANNEL configured in config.py.",
            quote=True,
        )
        return

    try:
        user = message.from_user
        await links["forwarded"].reply_text(
            f"Requested by: {user.mention if user else message.chat.id}\n"
            f"User ID: <code>{user.id if user else message.chat.id}</code>\n"
            f"Stream: {links['stream']}",
            disable_web_page_preview=True,
            quote=True,
        )
    except Exception:
        pass

    caption = (
        f"✅ <b>Your link is ready!</b>\n\n"
        f"📄 <b>File:</b> <code>{file_name}</code>\n"
        f"💾 <b>Size:</b> {file_size}\n\n"
        f"🎬 <b>Stream:</b> {links['stream']}\n"
        f"⬇️ <b>Download:</b> {links['download']}"
    )

    await message.reply_text(
        caption,
        disable_web_page_preview=True,
        quote=True,
        reply_markup=InlineKeyboardMarkup([
            [
                make_button("▶️ Stream", url=links["stream"], style=_BS.SUCCESS if _BS else None),
                make_button("⬇️ Download", url=links["download"], style=_BS.SUCCESS if _BS else None),
            ],
            [make_button("✖️ Close", callback_data="ftl_close", style=_BS.DANGER if _BS else None)],
        ]),
    )


@Client.on_message(filters.command("link") & filters.private & filters.reply)
async def link_cmd(client: Client, message: Message):
    """/link as a reply to a document/video/audio -> stream + download links."""
    if not links_ready():
        await message.reply_text(
            "⚠️ File-to-Link isn't configured yet. Set STREAM_BIN_CHANNEL (and, "
            "for a real deployment, STREAM_FQDN) in config.py / env vars.",
            quote=True,
        )
        return

    target = message.reply_to_message
    if not target or not get_media(target):
        await message.reply_text("Reply to a document, video, or audio file with /link.", quote=True)
        return

    if not await _gate(message):
        return

    status = await message.reply_text("⏳ Generating your link...", quote=True)
    await _generate_and_reply(client, message, target)
    try:
        await status.delete()
    except Exception:
        pass


@Client.on_message(filters.private & (filters.document | filters.video | filters.audio), group=6)
async def auto_link_handler(client: Client, message: Message):
    """
    Auto-generate stream/download links for any file sent directly to the
    bot in private chat. Runs in its own handler group (6) so it doesn't
    interfere with other file-handling plugins (e.g. filestore.py's
    admin-only auto_link_on_upload, which runs in an earlier group).
    """
    if not links_ready():
        return
    if message.from_user and message.from_user.id in ADMINS:
        # Admins already get filestore.py's richer auto-link-on-upload flow;
        # don't double-send here.
        return
    if not await _gate(message):
        return
    await _generate_and_reply(client, message, message)


@Client.on_callback_query(filters.regex("^ftl_close$"))
async def ftl_close_cb(client: Client, callback_query):
    try:
        await callback_query.message.delete()
    except Exception:
        await callback_query.answer()

