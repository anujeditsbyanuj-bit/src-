# Akbots - Don't Remove Credit - @AkBots_Official
#
# Anti-piracy group guard — ported from COPYRIGHT2 (github.com/DAXXTEAM/
# COPYRIGHT2), which was already Pyrogram-based like the rest of Akbotz, so
# this is a genuine port rather than a rewrite. Kept: keyword/card-number
# detection and the progressive warn -> mute escalation. Dropped from the
# original while porting:
#   - A stray `@app.on_message()` with no chat-type filter that matched
#     every private AND group message (the source's own `if chat.type ==
#     "private": return` line came after that, but the handler itself was
#     unfiltered — wasteful and redundant with the guard below).
#   - `delete_and_reply` / "delete messages over 10 words" — unrelated to
#     copyright, would nuke ordinary conversation, not ported.
#   - `delete_pdf_files` — deleted PDFs with a hostile, slur-laden warning
#     message. Not appropriate content for a bot users interact with, not
#     ported. (PDF piracy is already covered by the keyword list below —
#     "ncert", "pdf book", "textbook pdf", etc. — just without the abuse.)
#   - A bug in the source: `handle_message` referenced an undefined
#     `edited_message` variable instead of `message`, which meant the
#     text/caption-presence check would always raise instead of skipping
#     empty messages. Fixed here.
#   - Warning storage moved from COPYRIGHT2's blocking `pymongo` calls
#     (called without await, inside `async def`) onto Akbotz's existing
#     async motor db (see Database.get_group_warnings / add_group_warning
#     in database/db.py).
#
# Only runs in groups/supergroups. Admins, the chat creator, and Akbotz's
# own ADMINS are always exempt.

import re
import time

from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError

from config import ADMINS
from database.db import db
from logger import LOGGER

logger = LOGGER(__name__)

FORBIDDEN_KEYWORDS = [
    # piracy - movies/shows
    "download movie", "latest movie", "free movie", "web series", "torrent",
    "magnet link", "bluray", "web-dl", "hdcam", "hdrip",
    "bollywood movie", "hollywood movie", "netflix download", "prime video download",
    "hotstar download",

    # piracy - books/education
    "ncert", "pdf book", "ebook free", "textbook pdf", "solution pdf",
    "ncert solutions", "cbse pdf", "jee pdf", "neet pdf", "upsc pdf",

    # piracy - music
    "mp3 download", "album free", "spotify download", "apple music download",

    # piracy - software/games
    "cracked", "crack", "keygen", "serial key", "activation key", "license key",
    "mod apk", "premium apk", "hack apk", "full version free", "nulled",
    "cracked software", "pirated game", "steam crack",

    # piracy - courses
    "udemy course", "coursera free", "paid course free", "course crack",
    "skillshare free", "lynda free", "pluralsight free",

    # piracy - accounts
    "netflix account", "spotify premium", "youtube premium", "disney+ account",
    "amazon prime account", "shared account", "premium account free",

    # carding/fraud
    "carding", "cc checker", "cvv", "bins", "fullz", "paypal logs", "bank logs",
    "stripe logs", "buy cvv", "free cc", "cc dump", "credit card generator",
    "skimmer", "live cc", "fresh cvv", "buy dumps", "atm hack", "balance checker",

    # hacking/spam tools
    "rat tool", "grabber", "keylogger", "fud crypter", "stealer",
    "telegram token grabber", "ddos tool", "bruteforce", "openbullet config",
    "sql injection", "zero-day", "exploit tool",
    "telegram auto join bot", "telegram clone bot", "mass report bot",
    "group auto adder", "invite bomb", "join spammer",

    # release tags
    "[bluray]", "[web-dl]", "[hdrip]", "[cam]", "[ts]", "[hdcam]",
    "[x264]", "[x265]", "[hevc]", "yify", "rarbg", "1337x",
]

_CARD_REGEX = re.compile(
    r'(?<!\d)(\d{13,16})[|:/\s-]+(\d{1,2})[|:/\s-]+(\d{2,4})[|:/\s-]+(\d{3,4})(?!\d)'
)


def _matches_violation(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]", "", lowered)
    if any(k in lowered or k in cleaned for k in FORBIDDEN_KEYWORDS):
        return True
    return bool(_CARD_REGEX.search(lowered))


async def _mute_minutes_for_strike(client: Client, chat_id: int, user_id: int, strike: int) -> int | None:
    """Returns minutes muted, or None if this strike is warning-only or the
    mute attempt failed (e.g. bot isn't admin)."""
    if strike < 3:
        return None
    minutes = {3: 1, 4: 2, 5: 3}.get(strike, min(strike - 2, 30))
    try:
        from pyrogram.types import ChatPermissions
        await client.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=int(time.time()) + minutes * 60,
        )
        return minutes
    except RPCError as e:
        logger.debug(f"anticopyright_guard: mute failed for {user_id} in {chat_id}: {e}")
        return None


@Client.on_message(filters.group & (filters.text | filters.caption), group=5)
async def anticopyright_guard(client: Client, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if user_id in ADMINS:
        return

    text = message.text or message.caption or ""
    if not _matches_violation(text):
        return

    try:
        member = await client.get_chat_member(message.chat.id, user_id)
        if member.status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR):
            return
    except Exception:
        pass

    try:
        await message.delete()
    except Exception as e:
        logger.debug(f"anticopyright_guard: couldn't delete message: {e}")

    strike = await db.add_group_warning(message.chat.id, user_id)
    mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name

    minutes = await _mute_minutes_for_strike(client, message.chat.id, user_id, strike)

    if minutes is not None:
        text_out = (
            f"🔇 **Muted — strike {strike}**\n\n{mention}\n"
            f"Reason: repeated copyright/policy violation\n"
            f"Muted for: {minutes} minute(s)"
        )
    elif strike == 1:
        text_out = (
            f"⚠️ **Warning 1/3**\n\n{mention}\n"
            f"Your message was removed for sharing piracy/policy-violating content.\n"
            f"Next strike: another warning. 3rd strike: 1 minute mute."
        )
    else:  # strike == 2
        text_out = (
            f"⚠️ **Warning 2/3**\n\n{mention}\n"
            f"One more violation and you'll be muted."
        )

    try:
        await message.chat.send_message(text_out)
    except Exception:
        pass
