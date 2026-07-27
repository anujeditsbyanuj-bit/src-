"""
Maintenance Mode — a single global switch that, when on, makes the bot
reply "under maintenance" to every non-admin message/callback instead of
running any other plugin. Toggle with /maintenance on|off (admin only).

Implementation note: this runs as a very-early handler group (-10, well
before any other plugin's default group 0) that either stops propagation
(blocking everyone else) or does nothing at all (letting every other
plugin run completely normally). Admins always bypass it, including being
able to run /maintenance itself while it's on.
"""

import time

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

from config import ADMINS
from database.db import db

MAINTENANCE_TEXT = (
    "🛠️ <b>Bot is currently under maintenance.</b>\n\n"
    "We'll be back shortly — please try again in a little while."
)

# In-process cache so a busy bot doesn't hit Mongo on every single update.
# Refreshed instantly on toggle, and lazily every _CACHE_TTL seconds
# otherwise (covers the case of toggling from a different bot instance/shell).
_cache = {"enabled": False, "checked_at": 0.0}
_CACHE_TTL = 15


async def _is_enabled() -> bool:
    now = time.time()
    if now - _cache["checked_at"] > _CACHE_TTL:
        try:
            _cache["enabled"] = await db.get_maintenance_mode()
        except Exception:
            pass
        _cache["checked_at"] = now
    return _cache["enabled"]


@Client.on_message(filters.command("maintenance") & filters.user(ADMINS))
async def maintenance_cmd(client: Client, message: Message):
    parts = message.text.strip().split()
    if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
        current = await db.get_maintenance_mode()
        await message.reply_text(
            f"Maintenance mode is currently <b>{'ON' if current else 'OFF'}</b>.\n"
            f"Use <code>/maintenance on</code> or <code>/maintenance off</code>.",
            quote=True,
        )
        return

    enabled = parts[1].lower() == "on"
    await db.set_maintenance_mode(enabled)
    _cache["enabled"] = enabled
    _cache["checked_at"] = time.time()
    await message.reply_text(
        f"🛠️ Maintenance mode is now <b>{'ON' if enabled else 'OFF'}</b>.",
        quote=True,
    )


@Client.on_message(filters.all, group=-10)
async def maintenance_gate(client: Client, message: Message):
    if not message.from_user or message.from_user.id in ADMINS:
        return
    if await _is_enabled():
        try:
            await message.reply_text(MAINTENANCE_TEXT, quote=True)
        except Exception:
            pass
        message.stop_propagation()


@Client.on_callback_query(group=-10)
async def maintenance_gate_cb(client: Client, callback_query: CallbackQuery):
    if not callback_query.from_user or callback_query.from_user.id in ADMINS:
        return
    if await _is_enabled():
        try:
            await callback_query.answer(
                "Bot is under maintenance. Please try again later.", show_alert=True
            )
        except Exception:
            pass
        callback_query.stop_propagation()
