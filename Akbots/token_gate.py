import random
import string
import datetime
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import db
from config import WEBSITE_URL, AD_API, TOKEN_VALID_HOURS, TOKEN_BATCH_BONUS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_INFO  = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'

# Temporary in-memory param store: (bot identity, user_id) -> random param
# awaiting verification.
#
# Keyed by (id(client), user_id) rather than bare user_id: the main bot and
# every Titanium clone (Akbots/titanium.py) run as separate Client instances
# INSIDE THE SAME PYTHON PROCESS. An owner who runs /token on the main bot
# and then on their clone within the same window would have the second
# command's param silently overwrite the first's, so the first bot's
# deep-link would fail with "Invalid or expired verification link" even
# though it's fresh. See Akbots/cookies_manager.py's _pk() for the same fix.
_pending_params = {}


def _pk(client, user_id: int) -> tuple[int, int]:
    return (id(client), user_id)


def _is_configured():
    return bool(WEBSITE_URL and AD_API)


async def _get_shortened_url(deep_link: str):
    api_url = f"https://{WEBSITE_URL}/api?api={AD_API}&url={deep_link}"
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "success":
                    return data.get("shortenedUrl")
    return None


async def verify_token_param(client: Client, message: Message) -> bool:
    """Called from start.py when /start freeaccess_<param> is received.
    Returns True if it handled the message (so start.py should not also
    send the normal welcome message)."""
    user_id = message.from_user.id
    param = message.command[1][len("freeaccess_"):]
    key = _pk(client, user_id)

    if _pending_params.get(key) != param:
        await message.reply_text(
            f"<b>{E_CROSS} Invalid or expired verification link.</b>\n"
            f"<i>Use /token to generate a new one.</i>",
            parse_mode=enums.ParseMode.HTML
        )
        return True

    del _pending_params[key]
    expiry = datetime.datetime.now() + datetime.timedelta(hours=TOKEN_VALID_HOURS)
    await db.set_free_token(user_id, expiry)
    await message.reply_text(
        f"<b>{E_CHECK} Verified successfully!</b>\n\n"
        f"<i>Enjoy free access for the next {TOKEN_VALID_HOURS} hours "
        f"(+{TOKEN_BATCH_BONUS} extra batch limit).</i>",
        parse_mode=enums.ParseMode.HTML
    )
    return True


@Client.on_message(filters.command(["token"]) & filters.private)
async def token_command(client: Client, message: Message):
    user_id = message.from_user.id

    if not _is_configured():
        return await message.reply_text(
            f"<b>{E_INFO} Free-access tokens aren't set up on this bot.</b>",
            parse_mode=enums.ParseMode.HTML
        )

    if await db.check_premium(user_id):
        return await message.reply_text(
            f"<b>{E_CHECK} You're a Premium user — no token needed! 😉</b>",
            parse_mode=enums.ParseMode.HTML
        )

    if await db.has_valid_free_token(user_id):
        return await message.reply_text(
            f"<b>{E_CHECK} Your free-access session is already active. Enjoy!</b>",
            parse_mode=enums.ParseMode.HTML
        )

    param = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    _pending_params[_pk(client, user_id)] = param

    bot = await client.get_me()
    deep_link = f"https://t.me/{bot.username}?start=freeaccess_{param}"
    shortened = await _get_shortened_url(deep_link)
    if not shortened:
        return await message.reply_text(
            f"<b>{E_CROSS} Couldn't generate a token link right now. Try again later.</b>",
            parse_mode=enums.ParseMode.HTML
        )

    buttons = InlineKeyboardMarkup([[make_button("✅ ᴠᴇʀɪғʏ ɴᴏᴡ", url=shortened, style=_BS.DANGER if _BS else None)]])
    await message.reply_text(
        f"<b>{E_INFO} Tap below to verify and unlock free access:</b>\n\n"
        f"• Valid for {TOKEN_VALID_HOURS} hours\n"
        f"• +{TOKEN_BATCH_BONUS} extra batch limit\n"
        f"• All features unlocked temporarily",
        reply_markup=buttons, parse_mode=enums.ParseMode.HTML
    )
