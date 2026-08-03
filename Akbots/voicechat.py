"""
Voice Chat — serverless P2P WebRTC voice/video rooms, wired into the main
bot. Ported in from the standalone telegram_voice_bot.py (vc.py) script:
the bot-facing commands live here, the actual room page (PeerJS mesh +
screen-share + chat/file transfer) is served by Akbots/vcweb/page.py off
the same aiohttp server File-to-Link already runs (see
Akbots/filetolink/web_server.py) — so /vc works out of the box wherever
STREAM_BIN_CHANNEL is configured, with no extra ngrok tunnel or second
Pyrogram Client needed.

Commands: /vc (alias /gen) generates a room link, /vchelp shows usage.
"""

import base64
import json
import time

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

try:
    from pyrogram.enums import ButtonStyle as _BS
except ImportError:
    _BS = None

from config import STREAM_URL, STREAM_BIN_CHANNEL
from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO

ROOM_TTL_SECONDS = 86400  # 24 hours, matches the original script


def _vc_ready() -> bool:
    return bool(STREAM_BIN_CHANNEL) and bool(STREAM_URL)


def _build_room_link(user_id: int) -> tuple[str, int]:
    room_id = f"vc_room_{user_id}_{int(time.time())}"
    expiry = int(time.time()) + ROOM_TTL_SECONDS
    encoded = base64.b64encode(
        json.dumps({"roomId": room_id, "expiry": expiry}).encode("utf-8")
    ).decode("utf-8")
    base = STREAM_URL if STREAM_URL.endswith("/") else STREAM_URL + "/"
    return f"{base}vc?vc={encoded}", expiry


def _gen_text(user_name: str, link: str, expiry: int) -> str:
    expires = time.strftime("%d %b %Y, %I:%M %p", time.localtime(expiry))
    return (
        f"<b>{E_CHECK} Voice Chat Room Ready!</b>\n\n"
        f"<b>👤 ᴄʀᴇᴀᴛᴇᴅ ʙʏ:</b> {user_name}\n\n"
        f"<b>🔗 ʟɪɴᴋ:</b>\n<code>{link}</code>\n\n"
        f"<b>⏳ ᴇxᴘɪʀᴇs:</b> {expires} (24 hours from now)"
    )


HELP_TEXT = (
    f"<b>{E_INFO} Voice Chat — How to Use</b>\n\n"
    "<b>1. ɢᴇɴᴇʀᴀᴛᴇ ᴀ ʟɪɴᴋ</b> — /vc\n"
    "<b>2. sʜᴀʀᴇ ɪᴛ</b> with whoever you want to talk to\n"
    "<b>3. ᴊᴏɪɴ</b> — open the link, enter a name, allow mic access\n\n"
    "• Real-time P2P voice, plus screen-share (desktop) and in-room chat/file transfer\n"
    "• Everything is peer-to-peer — nothing is stored on any server\n"
    "• Links expire 24 hours after creation\n"
    "• Use headphones to avoid echo"
)


def _gen_keyboard(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎤 ᴏᴘᴇɴ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ", url=link,
                                   style=_BS.SUCCESS if _BS else None)],
            [InlineKeyboardButton("🔄 ɴᴇᴡ ʟɪɴᴋ", callback_data="vc#gen",
                                   style=_BS.PRIMARY if _BS else None)],
            [InlineKeyboardButton("📖 ʜᴇʟᴘ", callback_data="vc#help",
                                   style=_BS.PRIMARY if _BS else None)],
        ]
    )


def _help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎤 ɢᴇɴᴇʀᴀᴛᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ", callback_data="vc#gen",
                                style=_BS.SUCCESS if _BS else None)]]
    )


async def _send_unavailable(target):
    await target.reply_text(
        f"<b>{E_CROSS} Voice Chat isn't available right now</b>\n"
        "This needs <code>STREAM_BIN_CHANNEL</code> (and a reachable "
        "<code>STREAM_URL</code>) configured — same requirement as /link.",
        parse_mode=ParseMode.HTML,
    )


@Client.on_message(filters.command(["vc", "gen"]) & (filters.private | filters.group))
async def vc_command(client: Client, message: Message):
    if not _vc_ready():
        return await _send_unavailable(message)

    user = message.from_user
    user_name = user.first_name if user else "there"
    link, expiry = _build_room_link(user.id if user else message.chat.id)

    await message.reply_text(
        _gen_text(user_name, link, expiry),
        parse_mode=ParseMode.HTML,
        reply_markup=_gen_keyboard(link),
        disable_web_page_preview=True,
        quote=True,
    )


@Client.on_message(filters.command("vchelp") & (filters.private | filters.group))
async def vchelp_command(client: Client, message: Message):
    await message.reply_text(
        HELP_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=_help_keyboard(),
        disable_web_page_preview=True,
        quote=True,
    )


@Client.on_callback_query(filters.regex(r"^vc#"))
async def vc_callback(client: Client, cq: CallbackQuery):
    action = cq.data.split("#", 1)[1]

    if action == "gen":
        if not _vc_ready():
            await cq.answer("Voice Chat isn't configured on this bot.", show_alert=True)
            return
        await cq.answer("Generating your voice chat link...")
        user = cq.from_user
        user_name = user.first_name if user else "there"
        link, expiry = _build_room_link(user.id if user else cq.message.chat.id)
        await cq.message.reply_text(
            _gen_text(user_name, link, expiry),
            parse_mode=ParseMode.HTML,
            reply_markup=_gen_keyboard(link),
            disable_web_page_preview=True,
            quote=True,
        )

    elif action == "help":
        await cq.answer()
        await cq.message.reply_text(
            HELP_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=_help_keyboard(),
            disable_web_page_preview=True,
            quote=True,
        )
