# Akbots
# Force Subscribe — ported from Url-uploader-Bot-V4 (Plugin/functions/forcesub.py)
# Don't Remove Credit
# Telegram Channel @AkBots_Official

from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait, UserNotParticipant, ChatAdminRequired, PeerIdInvalid, ChannelInvalid
)
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import asyncio

from config import FORCE_SUB_CHANNEL, ADMINS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_WARN  = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'


def _channel_id():
    """Accepts either a numeric channel ID or an @username in the env var."""
    val = FORCE_SUB_CHANNEL.strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        return val if val.startswith("@") else f"@{val}"


async def is_subscribed(client: Client, user_id: int) -> bool:
    """True if force-subscribe is disabled, the user is an admin, or the user
    has joined FORCE_SUB_CHANNEL. False only when we're sure they haven't
    joined — any misconfiguration fails open so a broken setting can't lock
    everyone out of the bot."""
    channel = _channel_id()
    if not channel:
        return True
    if user_id in ADMINS:
        return True

    try:
        member = await client.get_chat_member(channel, user_id)
        return member.status not in ("left", "kicked", "banned")
    except UserNotParticipant:
        return False
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return True
    except (ChatAdminRequired, PeerIdInvalid, ChannelInvalid, ValueError, KeyError):
        return True
    except Exception:
        return True


async def send_force_sub_prompt(client: Client, message: Message):
    channel = _channel_id()
    invite_link = None
    try:
        link_obj = await client.create_chat_invite_link(channel)
        invite_link = link_obj.invite_link
    except Exception:
        pass

    buttons = []
    if invite_link:
        buttons.append([make_button("📢 ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ", url=invite_link, style=_BS.PRIMARY if _BS else None)])
    buttons.append([make_button("🔄 ɪ'ᴠᴇ ᴊᴏɪɴᴇᴅ — ʀᴇғʀᴇsʜ", callback_data="refresh_force_sub", style=_BS.PRIMARY if _BS else None)])

    await message.reply_text(
        f"<b>{E_WARN} Please join our updates channel to use this bot.</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^refresh_force_sub$"))
async def refresh_force_sub_cb(client: Client, callback_query):
    ok = await is_subscribed(client, callback_query.from_user.id)
    if ok:
        await callback_query.answer(f"{E_CHECK} Thanks for joining! Send /start again.", show_alert=True)
        try:
            await callback_query.message.delete()
        except Exception:
            pass
    else:
        await callback_query.answer(f"{E_CROSS} You haven't joined yet.", show_alert=True)
