"""
/set_expiry — admin command to change, at runtime, how long freshly
generated Stream/Download links (Akbots/filetolink/, /link, /linkbatch)
stay valid, without touching STREAM_LINK_EXPIRY / restarting the bot.

Usage:
    /set_expiry            — show the current value
    /set_expiry 1h         — links expire 1 hour after creation
    /set_expiry 30m        — 30 minutes
    /set_expiry 1h30m      — 1 hour 30 minutes (d/h/m/s can be combined)
    /set_expiry 3600       — plain seconds also works
    /set_expiry 0          — never expire (until changed again)
    /set_expiry off        — alias for 0
    /set_expiry reset      — go back to the STREAM_LINK_EXPIRY env default

The value is persisted in MongoDB (Database.set_link_expiry_seconds /
get_link_expiry_seconds in database/db.py), so it survives restarts, and
takes effect immediately for every stream/download request handled by
Akbots/filetolink/stream_routes.py (which caches it in-process for a few
seconds at a time — see note_expiry_changed()).
"""

import re

from pyrogram import Client, filters
from pyrogram.types import Message

from config import ADMINS, STREAM_LINK_EXPIRY
from database.db import db
from Akbots.filetolink import stream_routes as _routes

_DURATION_RE = re.compile(
    r"^(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$"
)


def _readable(seconds: int) -> str:
    if seconds <= 0:
        return "never (links don't expire)"
    seconds = int(seconds)
    periods = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    parts = []
    for name, secs in periods:
        val, seconds = divmod(seconds, secs)
        if val:
            parts.append(f"{val}{name}")
    return " ".join(parts)


def _parse_duration(text: str):
    """Returns seconds (int) parsed from things like '1h30m', '45m',
    '3600', '0', 'off'. Returns None if it doesn't look like a duration
    at all."""
    text = text.strip().lower()
    if text in ("0", "off", "none", "never"):
        return 0
    if text.isdigit():
        return int(text)
    m = _DURATION_RE.match(text)
    if not m or not any(m.groupdict().values()):
        return None
    parts = {k: int(v) if v else 0 for k, v in m.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


@Client.on_message(filters.command("set_expiry") & filters.user(ADMINS))
async def set_expiry_cmd(client: Client, message: Message):
    parts = message.text.strip().split(maxsplit=1)

    if len(parts) == 1:
        current = await db.get_link_expiry_seconds()
        effective = STREAM_LINK_EXPIRY if current is None else current
        source = "default (STREAM_LINK_EXPIRY env var)" if current is None else "admin override"
        await message.reply_text(
            f"⏳ Stream/Download links currently expire after: <b>{_readable(effective)}</b>\n"
            f"Source: {source}\n\n"
            f"Use <code>/set_expiry 1h</code>, <code>/set_expiry 30m</code>, "
            f"<code>/set_expiry 0</code> (never), or <code>/set_expiry reset</code> "
            f"to go back to the env default.",
            quote=True,
        )
        return

    arg = parts[1].strip().lower()

    if arg == "reset":
        await db.clear_link_expiry_override()
        _routes.note_expiry_changed(STREAM_LINK_EXPIRY)
        await message.reply_text(
            f"↩️ Reset to the env default: <b>{_readable(STREAM_LINK_EXPIRY)}</b>.",
            quote=True,
        )
        return

    seconds = _parse_duration(arg)
    if seconds is None:
        await message.reply_text(
            "Didn't understand that duration. Try things like "
            "<code>/set_expiry 1h</code>, <code>/set_expiry 45m</code>, "
            "<code>/set_expiry 1h30m</code>, <code>/set_expiry 3600</code>, "
            "or <code>/set_expiry 0</code> for never.",
            quote=True,
        )
        return

    await db.set_link_expiry_seconds(seconds)
    _routes.note_expiry_changed(seconds)
    await message.reply_text(
        f"✅ Stream/Download links will now expire after: <b>{_readable(seconds)}</b>\n"
        f"(applies to newly generated links going forward — links already "
        f"handed out keep the expiry window that was active when they were created).",
        quote=True,
    )
