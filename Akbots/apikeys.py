# Akbots - Don't Remove Credit - @AkBots_Official
#
# /setkey, /delkey, /listkeys — set or replace ANY API key/token (OpenAI,
# Groq, Gemini, GoFile, TMDB, a future Grok key, or literally anything
# else) directly from the bot. No .env edit, no redeploy, applies
# instantly. See Akbots/runtime_config.py for how it's stored/applied.

from pyrogram import Client, filters, enums
from pyrogram.types import Message

import config
from Akbots.runtime_config import (
    set_key, del_key, list_keys, known_names, mask, KEY_MAP, restart_note,
)

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_KEY   = '<tg-emoji emoji-id="5334544901428229844">🔑</tg-emoji>'


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMINS


@Client.on_message(filters.command("setkey"))
async def setkey_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)

    parts = message.text.split(None, 2)
    if len(parts) < 3:
        names = ", ".join(f"<code>{n}</code>" for n in known_names())
        return await message.reply_text(
            f"{E_KEY} <b>Usage:</b> <code>/setkey &lt;name&gt; &lt;value&gt;</code>\n\n"
            f"<b>Known names:</b> {names}\n\n"
            f"You can also use any custom name (e.g. <code>grok</code>) — it's saved "
            f"and ready for whenever a plugin uses it.\n\n"
            f"<b>Example:</b> <code>/setkey groq gsk_xxxxxxxxxxxx</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    name, value = parts[1].strip(), parts[2].strip()
    if not value:
        return await message.reply_text(f"{E_CROSS} Value can't be empty.", parse_mode=enums.ParseMode.HTML)

    await set_key(name, value)
    note = "" if name.lower() in KEY_MAP else "\n<i>(custom name — not wired to any plugin yet, just saved)</i>"
    warn = restart_note(name)
    if warn:
        note += f"\n⚠️ <i>{warn}</i>"
    await message.reply_text(
        f"{E_CHECK} <b>{name}</b> saved.\n<code>{mask(value)}</code>\n\n"
        f"Takes effect immediately — no restart needed.{note}",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("delkey"))
async def delkey_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)

    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply_text(
            f"{E_KEY} <b>Usage:</b> <code>/delkey &lt;name&gt;</code>", parse_mode=enums.ParseMode.HTML
        )

    name = parts[1].strip()
    await del_key(name)
    await message.reply_text(
        f"{E_CHECK} <b>{name}</b> override removed.\n"
        f"Reverted to the env var / built-in default (if any).",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("listkeys"))
async def listkeys_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)

    keys = await list_keys()
    lines = [f"{E_KEY} <b>API Keys / Tokens</b>\n"]
    for name, (value, source) in keys.items():
        if not value:
            lines.append(f"⬜ <code>{name}</code> — not set")
        elif source == "db":
            lines.append(f"{E_CHECK} <code>{name}</code> → <code>{mask(value)}</code> (set via /setkey)")
        else:
            lines.append(f"{E_CHECK} <code>{name}</code> → <code>{mask(value)}</code> (env/default)")
    lines.append(
        "\nSet: <code>/setkey &lt;name&gt; &lt;value&gt;</code>\n"
        "Remove: <code>/delkey &lt;name&gt;</code>"
    )
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
