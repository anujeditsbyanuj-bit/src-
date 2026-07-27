# Akbots - Don't Remove Credit - @AkBots_Official
#
# Per-user MongoDB — lets a person plug in their OWN MongoDB connection
# string instead of relying only on Akbots' one shared DB (config.DB_URI /
# config.DB_NAME). Akbots itself never stops using the shared DB — that's
# still where this very setting (and everything else: session, fwd
# settings, titanium bots) is stored. What this adds is an opt-in SECOND
# connection, available to any plugin via
# `database.db.get_user_db_client(user_id)`, for storing data that's
# naturally per-user (e.g. a duplicate-file cache) on the person's own
# cluster instead of the shared one.
#
# Commands:
#   /mydb                — status panel (also reachable via /settings →
#                           "🗄 my database" button)
#   /set_mydb <uri>       — save a mongodb:// or mongodb+srv:// URI (tested
#                           for reachability before saving)
#   /del_mydb             — remove it, falls back to the shared DB again
#                           for anything that was using it
#
# This is infrastructure only — no existing plugin was switched over to
# use the per-user connection in this pass, so setting a URI here has no
# visible effect yet on its own. It's the foundation the Channels/Bots/AK
# Manager work builds on next.

import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from database.db import db, get_user_db_client, evict_user_db_client, _extract_db_name
from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO

try:
    from Akbots.settings import make_button, get_back_close_buttons, BUTTON_STYLE_SUPPORTED, ICON_INFO
    from pyrogram.enums import ButtonStyle as _BS
except ImportError:
    make_button = None


def _mask_uri(uri: str) -> str:
    """Shows scheme + host only — never the credentials embedded in a
    mongo URI. e.g. mongodb+srv://user:pass@cluster0.abcde.mongodb.net/db
    -> mongodb+srv://cluster0.abcde.mongodb.net/db"""
    try:
        scheme, rest = uri.split("://", 1)
        # rsplit on the LAST "@": credentials come first and may themselves
        # contain "@" (e.g. an email-style username, or a password with an
        # "@" in it) — splitting on the first "@" would leave part of the
        # password in host_and_path instead of stripping it.
        host_and_path = rest.rsplit("@", 1)[-1]
        return f"{scheme}://{host_and_path}"
    except Exception:
        return "•••• (unparsable)"


def _status_text(uri: str | None) -> str:
    if uri:
        return (
            f"<b>{E_INFO} Your MongoDB</b>\n\n"
            f"<b>Status:</b> {E_CHECK} connected\n"
            f"<b>Cluster:</b> <code>{_mask_uri(uri)}</code>\n\n"
            f"<code>/del_mydb</code> — disconnect it (falls back to the shared DB)\n"
            f"<code>/set_mydb &lt;uri&gt;</code> — replace it"
        )
    return (
        f"<b>{E_INFO} Your MongoDB</b>\n\n"
        f"<b>Status:</b> {E_CROSS} not set — using Akbots' shared DB\n\n"
        f"<b>Usage:</b> <code>/set_mydb mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/mydb</code>\n"
        f"<i>Get a free cluster URI from mongodb.com/atlas. Your credentials are never shown "
        f"back in chat once saved.</i>"
    )


@Client.on_message(filters.private & filters.command("mydb"))
async def mydb_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    uri = await db.get_user_db_uri(user_id)
    await message.reply_text(_status_text(uri), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.private & filters.command("set_mydb"))
async def set_mydb_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/set_mydb mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/mydb</code>",
            parse_mode=enums.ParseMode.HTML
        )

    uri = message.text.split(" ", 1)[1].strip()
    if not (uri.startswith("mongodb://") or uri.startswith("mongodb+srv://")):
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid URI.</b> Must start with <code>mongodb://</code> or <code>mongodb+srv://</code>",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>{E_INFO} Testing connection...</b>", parse_mode=enums.ParseMode.HTML)

    # Delete any credentials the person just pasted in plain chat, win or
    # lose on the test below — no reason for the token to sit in chat
    # history any longer than it takes to read it.
    try:
        await message.delete()
    except Exception:
        pass

    import motor.motor_asyncio
    try:
        test_client = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
        await asyncio.wait_for(test_client.server_info(), timeout=10)
        test_client.close()
    except Exception as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't connect:</b> <code>{e}</code>\n\n"
            f"<i>Double check the URI, and that your Atlas cluster's Network Access allows "
            f"connections from anywhere (0.0.0.0/0) — Akbots' server IP isn't fixed.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    evict_user_db_client(user_id)  # drop any stale cached client from a previous URI
    await db.set_user_db_uri(user_id, uri)
    await status.edit_text(
        f"<b>{E_CHECK} Connected.</b>\n\n<b>Cluster:</b> <code>{_mask_uri(uri)}</code>\n"
        f"<b>Database:</b> <code>{_extract_db_name(uri)}</code>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("del_mydb"))
async def del_mydb_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    uri = await db.get_user_db_uri(user_id)
    if not uri:
        return await message.reply_text(f"<b>{E_INFO} No custom database set.</b>", parse_mode=enums.ParseMode.HTML)
    await db.clear_user_db_uri(user_id)
    await message.reply_text(
        f"<b>{E_CHECK} Disconnected.</b> Falling back to Akbots' shared DB.",
        parse_mode=enums.ParseMode.HTML
    )


# --------------------------------------------------------------------------
# /settings → "🗄 my database" button
# --------------------------------------------------------------------------

@Client.on_callback_query(filters.regex("^database_btn$"))
async def database_btn_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    uri = await db.get_user_db_uri(user_id)
    back_close = get_back_close_buttons() if make_button else None
    await callback_query.edit_message_text(
        _status_text(uri),
        reply_markup=InlineKeyboardMarkup(back_close) if back_close else None,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True
    )
