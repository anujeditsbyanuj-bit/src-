# Akbots
# AK Manager menu — mirrors the reference forward-bot's /start layout
# (Bots / Channels / Caption / MongoDB / Filters / Button / AK Manager /
# Extra Settings / Back) using Akbots' own existing features wherever one
# already exists, and clearly marking the pieces that are still pending
# the bigger forwarding-engine port (Filters, Button, core AK Manager).
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from database.db import db
from logger import LOGGER
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.titanium import _managed_bots_available, request_bot_autocreate
from Akbots.forward import BTN_URL_REGEX, parse_buttons
from pyrogram.errors import RPCError
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

logger = LOGGER(__name__)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_BACK  = '<emoji id=5447183459602669338>⬅️</emoji>'
E_SOON  = '<emoji id=5447644880824181073>⚠️</emoji>'


def _ak_manager_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [make_button("🤖 ʙᴏᴛs", callback_data="ak_bots", style=_BS.PRIMARY if _BS else None),
         make_button("🏷 ᴄʜᴀɴɴᴇʟs", callback_data="channels_btn", style=_BS.PRIMARY if _BS else None)],
        [make_button("🖋 ᴄᴀᴘᴛɪᴏɴ", callback_data="caption_btn", style=_BS.PRIMARY if _BS else None),
         make_button("🗃 ᴍᴏɴɢᴏᴅʙ", callback_data="database_btn", style=_BS.PRIMARY if _BS else None)],
        [make_button("🕵️ ғɪʟᴛᴇʀs", callback_data="ak_filters", style=_BS.PRIMARY if _BS else None),
         make_button("🔘 ʙᴜᴛᴛᴏɴ", callback_data="ak_button", style=_BS.PRIMARY if _BS else None)],
        [make_button("🚀 ᴍᴀɴᴀɢᴇʀ 🚀", callback_data="ak_core", style=_BS.PRIMARY if _BS else None)],
        [make_button("🧪 ᴇxᴛʀᴀ sᴇᴛᴛɪɴɢs", callback_data="ak_extra", style=_BS.PRIMARY if _BS else None)],
        [make_button("🔁 ʙᴀᴄᴋ", callback_data="settings_back_btn", style=_BS.DANGER if _BS else None)],
    ])


@Client.on_message(filters.command("akanager") & filters.private)
async def akanager_command(client: Client, message: Message):
    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)
    await message.reply_text(
        f"<b>🚀 ᴍᴀɴᴀɢᴇʀ</b>\n\n<i>Change your settings as you wish:</i>",
        reply_markup=_ak_manager_menu(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akanager_btn$"))
async def akanager_btn_callback(client: Client, callback_query: CallbackQuery):
    await safe_edit(callback_query.message.edit_text, 
        f"<b>🚀 ᴍᴀɴᴀɢᴇʀ</b>\n\n<i>Change your settings as you wish:</i>",
        reply_markup=_ak_manager_menu(),
        parse_mode=enums.ParseMode.HTML,
    )
    await callback_query.answer()


def _bots_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [make_button("➕ ᴀᴅᴅ ʙᴏᴛ", callback_data="ak_bots_add", style=_BS.PRIMARY if _BS else None)],
        [make_button("➕ ᴀᴅᴅ ᴜsᴇʀ ʙᴏᴛ", callback_data="ak_bots_adduser", style=_BS.PRIMARY if _BS else None)],
        [make_button("🗑 ʀᴇᴍᴏᴠᴇ ʙᴏᴛ", callback_data="ak_bots_remove", style=_BS.DANGER if _BS else None)],
        [make_button("🔁 ʙᴀᴄᴋ", callback_data="akanager_btn", style=_BS.DANGER if _BS else None)],
    ])


async def _bots_menu_text(user_id: int) -> str:
    bot_token = await db.get_custom_bot(user_id)
    session = await db.get_session(user_id)
    bot_status = f"{E_CHECK} Connected" if bot_token else f"{E_SOON} Not connected"
    user_status = f"{E_CHECK} Logged in" if session else f"{E_SOON} Not logged in"
    return (
        f"<b>🤖 ᴍʏ ʙᴏᴛs</b>\n\n"
        f"<i>You can manage your bots in here.</i>\n\n"
        f"<b>ʙᴏᴛ:</b> {bot_status}\n"
        f"<b>ᴜsᴇʀ ʙᴏᴛ:</b> {user_status}"
    )


@Client.on_callback_query(filters.regex("^ak_bots$"))
async def ak_bots_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await safe_edit(callback_query.message.edit_text, 
        await _bots_menu_text(callback_query.from_user.id),
        reply_markup=_bots_menu(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^ak_bots_add$"))
async def ak_bots_add_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await safe_edit(callback_query.message.edit_text, 
        f"<u><b>ᴀᴅᴅ ʙᴏᴛ</b></u>\n\n"
        f"<b>ᴄʜᴏᴏsᴇ ʏᴏᴜʀ ᴍᴇᴛʜᴏᴅ:</b>\n\n"
        f"🤖 <b>ᴀᴅᴅ ʙᴏᴛ ᴜsɪɴɢ ᴛᴏᴋᴇɴ:</b> paste a token from @BotFather — always works, recommended.\n\n"
        f"⚡ <b>ᴀᴜᴛᴏ ʙᴏᴛ:</b> tap a button and Telegram creates one for you automatically. "
        f"<i>Needs \"Bot Management Mode\" enabled for this bot in @BotFather first — if you see "
        f"\"CREATE_BOT_BLOCKED\", that setting isn't on yet. Use Add Bot Using Token instead.</i>",
        reply_markup=InlineKeyboardMarkup([
            [make_button("🤖 ᴀᴅᴅ ʙᴏᴛ ᴜsɪɴɢ ᴛᴏᴋᴇɴ", callback_data="ak_bots_add_token", style=_BS.PRIMARY if _BS else None)],
            [make_button("⚡ ᴀᴅᴅ ᴀᴜᴛᴏ ʙᴏᴛ", callback_data="ak_bots_autocreate", style=_BS.PRIMARY if _BS else None)],
            [make_button("🔁 ʙᴀᴄᴋ", callback_data="ak_bots", style=_BS.PRIMARY if _BS else None)],
        ]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^ak_bots_add_token$"))
async def ak_bots_add_token_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    from Akbots.direct_utils import wait_for_reply
    from Akbots.multibot import try_set_custom_bot

    chat_id = callback_query.message.chat.id
    user_id = callback_query.from_user.id

    await safe_edit(callback_query.message.edit_text,
        f"📥 <b>sᴇɴᴅ ᴍᴇ ʏᴏᴜʀ ʙᴏᴛ ᴛᴏᴋᴇɴ ғʀᴏᴍ @ʙᴏᴛғᴀᴛʜᴇʀ</b>\n\n"
        f"📝 <b>ᴇxᴀᴍᴘʟᴇ:</b> <code>1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ</code>\n\n"
        f"/cancel - cancel this process",
        reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ", callback_data="ak_bots_add", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        reply = await wait_for_reply(client, chat_id, user_id, timeout=120)
    except asyncio.TimeoutError:
        return await client.send_message(
            chat_id, f"<b>❌ ᴛɪᴍᴇᴏᴜᴛ! ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.</b>",
            reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ", callback_data="ak_bots_add", style=_BS.DANGER if _BS else None)]]),
            parse_mode=enums.ParseMode.HTML,
        )

    token = (reply.text or "").strip()
    if not token or token.lower() == "/cancel":
        return await reply.reply_text(f"{E_CHECK} Cancelled.", parse_mode=enums.ParseMode.HTML, quote=True)

    status = await reply.reply_text(f"<b>{E_INFO} Verifying token...</b>", parse_mode=enums.ParseMode.HTML, quote=True)
    ok, text, me = await try_set_custom_bot(user_id, token)

    if not ok:
        return await safe_edit(status.edit_text, 
            text,
            reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ", callback_data="ak_bots_add", style=_BS.DANGER if _BS else None)]]),
            parse_mode=enums.ParseMode.HTML,
        )

    await safe_edit(status.edit_text, text,
        reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="akanager_btn", style=_BS.PRIMARY if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )
    await _ask_target_chat(client, chat_id, user_id)


async def _ask_target_chat(client: Client, chat_id: int, user_id: int):
    """(SET TARGET CHAT) step — ported from the reference bot's flow that
    chains straight in after a bot is connected. Accepts either a forwarded
    message from the target chat, or a t.me link to it."""
    from Akbots.direct_utils import wait_for_reply

    prompt = await client.send_message(
        chat_id,
        f"<b>( sᴇᴛ ᴛᴀʀɢᴇᴛ ᴄʜᴀᴛ )</b>\n\n"
        f"📩 <b>ғᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ʏᴏᴜʀ ᴛᴀʀɢᴇᴛ ᴄʜᴀᴛ</b>\n\n"
        f"🔗 <b>ᴏʀ sᴇɴᴅ ᴀ ʟɪɴᴋ ᴛᴏ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ/ɢʀᴏᴜᴘ</b>\n"
        f"<i>Example:</i> <code>https://t.me/channel/123</code> <i>or</i>\n"
        f"<code>https://t.me/c/123456/789</code>\n\n"
        f"/cancel - cancel this process",
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        reply = await wait_for_reply(client, chat_id, user_id, timeout=120)
    except asyncio.TimeoutError:
        return await client.send_message(
            chat_id, f"<b>❌ ᴛɪᴍᴇᴏᴜᴛ! ᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ.</b>",
            reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="akanager_btn", style=_BS.DANGER if _BS else None)]]),
            parse_mode=enums.ParseMode.HTML,
        )

    if reply.text and reply.text.strip().lower() == "/cancel":
        return await reply.reply_text(f"{E_CHECK} Cancelled.", parse_mode=enums.ParseMode.HTML, quote=True)

    from Akbots.forward import _parse_chat, _resolve_chat
    import re as _re

    chat_ref = None
    if reply.forward_from_chat:
        chat_ref = reply.forward_from_chat.id
    elif reply.text:
        raw = reply.text.strip()
        m = _re.match(r"https?://t\.me/c/(\d+)/\d+", raw)
        if m:
            chat_ref = int(f"-100{m.group(1)}")
        else:
            m = _re.match(r"https?://t\.me/([A-Za-z0-9_]+)(?:/\d+)?", raw)
            if m:
                chat_ref = _parse_chat(m.group(1))
            else:
                chat_ref = _parse_chat(raw)

    if chat_ref is None:
        return await reply.reply_text(
            f"<b>❌ ɪɴᴠᴀʟɪᴅ ᴛᴀʀɢᴇᴛ. ғᴏʀᴡᴀʀᴅ ᴀ ᴍᴇssᴀɢᴇ ᴏʀ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ᴛ.ᴍᴇ ʟɪɴᴋ.</b>",
            reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="akanager_btn", style=_BS.DANGER if _BS else None)]]),
            parse_mode=enums.ParseMode.HTML, quote=True,
        )

    status = await reply.reply_text(f"<b>{E_INFO} Resolving target chat...</b>", parse_mode=enums.ParseMode.HTML, quote=True)
    chat, via, acc = await _resolve_chat(client, user_id, chat_ref)
    if acc:
        await acc.disconnect()
    if not chat:
        return await safe_edit(status.edit_text, 
            f"<b>❌ ᴄᴀɴ'ᴛ ᴀᴄᴄᴇss ᴛʜᴀᴛ ᴄʜᴀᴛ.</b> Make sure your bot (or your logged-in "
            f"session) is actually a member/admin there, then try again.",
            reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="akanager_btn", style=_BS.DANGER if _BS else None)]]),
            parse_mode=enums.ParseMode.HTML,
        )

    await db.set_fwd_target(user_id, chat.id, via)
    await safe_edit(status.edit_text, 
        f"<b>{E_CHECK} Target chat set:</b> {chat.title or chat.first_name} (<code>{chat.id}</code>)",
        reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ ᴛᴏ sᴇᴛᴛɪɴɢs", callback_data="akanager_btn", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^ak_bots_autocreate$"))
async def ak_bots_autocreate_callback(client: Client, callback_query: CallbackQuery):
    if not _managed_bots_available():
        return await callback_query.answer(
            "This bot's kurigram build doesn't have Managed Bots support yet "
            "(needs a very recent version). Use Add Bot Using Token instead for now.",
            show_alert=True,
        )
    await callback_query.answer()
    owner_id = callback_query.from_user.id
    try:
        await request_bot_autocreate(
            client, callback_query.message.chat.id, owner_id,
            purpose="custom_bot",
            header_text="⚡ ᴀᴜᴛᴏ-ᴄʀᴇᴀᴛᴇ ʏᴏᴜʀ ғᴏʀᴡᴀʀᴅɪɴɢ ʙᴏᴛ\n\n"
                        "Tap the button below. Telegram will show a pre-filled name "
                        "and username for your bot — edit them if you like, then tap Create.\n\n"
                        "Your bot will be connected automatically — no token copying needed.\n\n"
                        "⚠️ Requires \"Bot Management Mode\" enabled for this bot in @BotFather "
                        "(Mini App → Bot Settings). If Telegram shows \"CREATE_BOT_BLOCKED\" "
                        "when you tap Create, that setting isn't on yet — enable it and try again, "
                        "or use Add Bot Using Token instead, which always works.",
            button_text="🤖 Create my bot",
            suggested_name="My Akbots Forwarder",
        )
    except RPCError as e:
        logger.warning(f"ak_bots_autocreate: SendMessage with request-peer button failed: {e}")
        await callback_query.message.reply_text(
            f"<b>{E_SOON} Couldn't start auto-create:</b> <code>{e}</code>\n\n"
            f"<i>Make sure \"Bot Management Mode\" is enabled for this bot in "
            f"@BotFather's Mini App — this feature won't work without it. "
            f"Falling back to Add Bot Using Token is always available.</i>",
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_callback_query(filters.regex("^ak_bots_adduser$"))
async def ak_bots_adduser_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await safe_edit(callback_query.message.edit_text, 
        f"<u><b>ᴀᴅᴅ ᴜsᴇʀ ʙᴏᴛ</b></u>\n\n"
        f"<b>ᴄʜᴏᴏsᴇ ʏᴏᴜʀ ʟᴏɢɪɴ ᴍᴇᴛʜᴏᴅ:</b>",
        reply_markup=InlineKeyboardMarkup([
            [make_button("📱 ʟᴏɢɪɴ ᴜsɪɴɢ ᴘʜᴏɴᴇ ɴᴜᴍʙᴇʀ", callback_data="ak_bots_adduser_phone", style=_BS.PRIMARY if _BS else None)],
            [make_button("🔑 ʟᴏɢɪɴ ᴜsɪɴɢ sᴛʀɪɴɢ sᴇssɪᴏɴ", callback_data="ak_bots_adduser_string", style=_BS.PRIMARY if _BS else None)],
            [make_button("🔁 ʙᴀᴄᴋ", callback_data="ak_bots", style=_BS.DANGER if _BS else None)],
        ]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^ak_bots_adduser_phone$"))
async def ak_bots_adduser_phone_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    from Akbots.session import _send_phone_login_prompt
    await _send_phone_login_prompt(client, callback_query.message.chat.id, callback_query.from_user.id)


@Client.on_callback_query(filters.regex("^ak_bots_adduser_string$"))
async def ak_bots_adduser_string_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    from Akbots.session import _send_string_session_prompt
    await _send_string_session_prompt(client, callback_query.message.chat.id, callback_query.from_user.id)


@Client.on_callback_query(filters.regex("^ak_bots_remove$"))
async def ak_bots_remove_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await safe_edit(callback_query.message.edit_text, 
        f"<blockquote>🗑 <b>ʀᴇᴍᴏᴠᴇ ʙᴏᴛ / ᴜsᴇʀ ʙᴏᴛ</b>\n\n"
        f"{E_INFO} <code>/rembot</code> — Disconnect your bot\n"
        f"{E_INFO} <code>/logout</code> — Log out your user bot session</blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ", callback_data="ak_bots", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )


def _extra_settings_buttons(s: dict) -> InlineKeyboardMarkup:
    S = _BS
    poll_mark = "✅" if s.get("poll") else "❌"
    secure_mark = "✅" if s.get("secure_message") else "❌"
    return InlineKeyboardMarkup([
        [make_button("📊 ᴘᴏʟʟ", callback_data="ak_extra_toggle:poll", style=S.PRIMARY if S else None),
         make_button(poll_mark, callback_data="ak_extra_toggle:poll", style=(S.SUCCESS if s.get("poll") else S.DANGER) if S else None)],
        [make_button("🔒 sᴇᴄᴜʀᴇ ᴍᴇssᴀɢᴇ", callback_data="ak_extra_toggle:secure", style=S.PRIMARY if S else None),
         make_button(secure_mark, callback_data="ak_extra_toggle:secure", style=(S.SUCCESS if s.get("secure_message") else S.DANGER) if S else None)],
        [make_button("🛑 sɪᴢᴇ ʟɪᴍɪᴛ", callback_data="ak_extra_size", style=S.PRIMARY if S else None)],
        [make_button("💾 ᴇxᴛᴇɴsɪᴏɴ", callback_data="ak_extra_ext", style=S.PRIMARY if S else None)],
        [make_button("♦️ ᴋᴇʏᴡᴏʀᴅs ♦️", callback_data="ak_extra_kw", style=S.PRIMARY if S else None)],
        [make_button("⫷ ʙᴀᴄᴋ", callback_data="akanager_btn", style=S.DANGER if S else None)],
    ])


@Client.on_callback_query(filters.regex("^ak_extra$"))
async def ak_extra_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, callback_query.from_user.first_name)
    s = await db.get_fwd_settings(user_id)
    # Ported from the reference FTM Forward Bot: Extra Settings only swaps the
    # keyboard, the message text ("change your settings as your wish") stays put.
    await safe_edit(callback_query.message.edit_reply_markup, reply_markup=_extra_settings_buttons(s))


@Client.on_callback_query(filters.regex(r"^ak_extra_toggle:(poll|secure)$"))
async def ak_extra_toggle_callback(client: Client, callback_query: CallbackQuery):
    field = callback_query.matches[0].group(1)
    user_id = callback_query.from_user.id
    s = await db.get_fwd_settings(user_id)
    if field == "poll":
        new_val = not s.get("poll", False)
        await db.set_fwd_poll(user_id, new_val)
        await callback_query.answer(f"Poll: {'ON' if new_val else 'OFF'}")
    else:
        new_val = not s.get("secure_message", False)
        await db.set_fwd_secure_message(user_id, new_val)
        await callback_query.answer(f"Secure Message: {'ON' if new_val else 'OFF'}")
    s = await db.get_fwd_settings(user_id)
    await safe_edit(callback_query.message.edit_reply_markup, reply_markup=_extra_settings_buttons(s))


# --- Size limit — ported from the reference bot's size_button()/file_size flow ---

def _size_limit_buttons(mb: int) -> InlineKeyboardMarkup:
    S = _BS

    def b(label, delta=None, value=None):
        cb = f"ak_extra_sl:{value}" if value is not None else f"ak_extra_sl:{max(0, mb + delta)}"
        return make_button(label, callback_data=cb, style=(S.SUCCESS if (delta or 0) >= 0 else S.DANGER) if S else None)

    return InlineKeyboardMarkup([
        [b("+1", 1), b("-1", -1)],
        [b("+5", 5), b("-5", -5)],
        [b("+10", 10), b("-10", -10)],
        [b("+50", 50), b("-50", -50)],
        [b("+100", 100), b("-100", -100)],
        [make_button("0 = ɴᴏ ʟɪᴍɪᴛ", callback_data="ak_extra_sl:0", style=S.PRIMARY if S else None)],
        [make_button("⏪ ʙᴀᴄᴋ", callback_data="ak_extra", style=S.DANGER if S else None)],
    ])


def _size_limit_text(mb: int) -> str:
    status = f"files above <code>{mb}</code> MB will be skipped" if mb else "no limit — all files forward"
    return f"<u><b>sɪᴢᴇ ʟɪᴍɪᴛ</b></u>\n\n<b>ʏᴏᴜ ᴄᴀɴ sᴇᴛ ᴀ ғɪʟᴇ sɪᴢᴇ ʟɪᴍɪᴛ ᴛᴏ ғᴏʀᴡᴀʀᴅ.</b>\n\nStatus: {status}"


@Client.on_callback_query(filters.regex("^ak_extra_size$"))
async def ak_extra_size_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    s = await db.get_fwd_settings(user_id)
    mb = s['size_limit_mb']
    await safe_edit(callback_query.message.edit_text, 
        _size_limit_text(mb), reply_markup=_size_limit_buttons(mb), parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex(r"^ak_extra_sl:(\d+)$"))
async def ak_extra_size_set_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    mb = int(callback_query.matches[0].group(1))
    await db.set_fwd_size_limit(user_id, mb)
    await callback_query.answer(f"Size limit: {mb or 'no limit'}")
    await safe_edit(callback_query.message.edit_text, 
        _size_limit_text(mb), reply_markup=_size_limit_buttons(mb), parse_mode=enums.ParseMode.HTML
    )


# --- Extension blocklist — ported from the reference bot's get_extension/add_extension flow ---

def _list_buttons(items, add_cb: str, clear_cb: str, back_cb: str) -> InlineKeyboardMarkup:
    """extract_btn() port: up to 5 chip-style buttons per row, each just an
    alert-preview (tapping shows the value), then Add / Remove all / Back."""
    S = _BS
    rows, row = [], []
    for item in (items or []):
        row.append(make_button(item, callback_data=f"ak_extra_noop:{item[:40]}", style=S.PRIMARY if S else None))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([make_button("➕ ᴀᴅᴅ ➕", callback_data=add_cb, style=S.SUCCESS if S else None)])
    rows.append([make_button("ʀᴇᴍᴏᴠᴇ ᴀʟʟ", callback_data=clear_cb, style=S.DANGER if S else None)])
    rows.append([make_button("⏪ ʙᴀᴄᴋ", callback_data=back_cb, style=S.DANGER if S else None)])
    return InlineKeyboardMarkup(rows)


@Client.on_callback_query(filters.regex(r"^ak_extra_noop:"))
async def ak_extra_noop_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer(callback_query.data.split(":", 1)[1], show_alert=True)


@Client.on_callback_query(filters.regex("^ak_extra_ext$"))
async def ak_extra_ext_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    s = await db.get_fwd_settings(user_id)
    await safe_edit(callback_query.message.edit_text, 
        "<u><b>ᴇxᴛᴇɴsɪᴏɴs</b></u>\n\n<b>ғɪʟᴇs ᴡɪᴛʜ ᴛʜᴇsᴇ ᴇxᴛᴇɴsɪᴏɴs ᴡɪʟʟ ɴᴏᴛ ғᴏʀᴡᴀʀᴅ.</b>",
        reply_markup=_list_buttons(s['ext_block'], "ak_extra_ext_add", "ak_extra_ext_clear", "ak_extra"),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^ak_extra_ext_add$"))
async def ak_extra_ext_add_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    await callback_query.message.delete()
    txt = await client.send_message(
        user_id, "<b>ᴘʟᴇᴀsᴇ sᴇɴᴅ ʏᴏᴜʀ ᴇxᴛᴇɴsɪᴏɴs (sᴇᴘᴀʀᴀᴛᴇ ʙʏ sᴘᴀᴄᴇ).</b>\n\n/cancel — cancel this process",
        parse_mode=enums.ParseMode.HTML,
    )
    ask = await _ak_listen(user_id)
    s = await db.get_fwd_settings(user_id)
    back_markup = _list_buttons(s['ext_block'], "ak_extra_ext_add", "ak_extra_ext_clear", "ak_extra")
    if ask is None:
        return await safe_edit(txt.edit_text, f"<b>{E_SOON} Process has been automatically cancelled.</b>", reply_markup=back_markup, parse_mode=enums.ParseMode.HTML)
    if ask.text.strip().startswith("/cancel"):
        return await safe_edit(txt.edit_text, "<b>ᴘʀᴏᴄᴇss ᴄᴀɴᴄᴇʟʟᴇᴅ.</b>", reply_markup=back_markup, parse_mode=enums.ParseMode.HTML)
    for ext in ask.text.strip().split():
        await db.add_fwd_ext_block(user_id, ext)
    s = await db.get_fwd_settings(user_id)
    await safe_edit(txt.edit_text, 
        f"<b>{E_CHECK} Successfully updated.</b>",
        reply_markup=_list_buttons(s['ext_block'], "ak_extra_ext_add", "ak_extra_ext_clear", "ak_extra"),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^ak_extra_ext_clear$"))
async def ak_extra_ext_clear_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    await db.clear_fwd_ext_block(user_id)
    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_CHECK} Successfully deleted.</b>",
        reply_markup=_list_buttons([], "ak_extra_ext_add", "ak_extra_ext_clear", "ak_extra"),
        parse_mode=enums.ParseMode.HTML,
    )


# --- Keywords allowlist — ported from the reference bot's get_keyword/add_keyword flow ---

@Client.on_callback_query(filters.regex("^ak_extra_kw$"))
async def ak_extra_kw_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    s = await db.get_fwd_settings(user_id)
    await safe_edit(callback_query.message.edit_text, 
        "<u><b>ᴋᴇʏᴡᴏʀᴅs</b></u>\n\n<b>ғɪʟᴇs ᴡɪᴛʜ ᴛʜᴇsᴇ ᴋᴇʏᴡᴏʀᴅs ɪɴ ᴛʜᴇ ғɪʟᴇ ɴᴀᴍᴇ ᴡɪʟʟ ғᴏʀᴡᴀʀᴅ.</b>",
        reply_markup=_list_buttons(s['keywords'], "ak_extra_kw_add", "ak_extra_kw_clear", "ak_extra"),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^ak_extra_kw_add$"))
async def ak_extra_kw_add_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    await callback_query.message.delete()
    txt = await client.send_message(
        user_id, "<b>ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴛʜᴇ ᴋᴇʏᴡᴏʀᴅs (sᴇᴘᴀʀᴀᴛᴇ ʙʏ sᴘᴀᴄᴇ).</b>\n\n/cancel — cancel this process",
        parse_mode=enums.ParseMode.HTML,
    )
    ask = await _ak_listen(user_id)
    s = await db.get_fwd_settings(user_id)
    back_markup = _list_buttons(s['keywords'], "ak_extra_kw_add", "ak_extra_kw_clear", "ak_extra")
    if ask is None:
        return await safe_edit(txt.edit_text, f"<b>{E_SOON} Process has been automatically cancelled.</b>", reply_markup=back_markup, parse_mode=enums.ParseMode.HTML)
    if ask.text.strip().startswith("/cancel"):
        return await safe_edit(txt.edit_text, "<b>ᴘʀᴏᴄᴇss ᴄᴀɴᴄᴇʟʟᴇᴅ.</b>", reply_markup=back_markup, parse_mode=enums.ParseMode.HTML)
    for word in ask.text.strip().split():
        await db.add_fwd_keyword(user_id, word)
    s = await db.get_fwd_settings(user_id)
    await safe_edit(txt.edit_text, 
        f"<b>{E_CHECK} Successfully updated.</b>",
        reply_markup=_list_buttons(s['keywords'], "ak_extra_kw_add", "ak_extra_kw_clear", "ak_extra"),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^ak_extra_kw_clear$"))
async def ak_extra_kw_clear_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    await db.clear_fwd_keywords(user_id)
    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_CHECK} Successfully deleted.</b>",
        reply_markup=_list_buttons([], "ak_extra_kw_add", "ak_extra_kw_clear", "ak_extra"),
        parse_mode=enums.ParseMode.HTML,
    )


_FILTER_TYPE_ROWS = [
    ("text", "🖍️ Texts"),
    ("document", "📁 Documents"),
    ("video", "🎞️ Videos"),
    ("photo", "📷 Photos"),
    ("audio", "🎧 Audios"),
    ("voice", "🎤 Voices"),
    ("animation", "🎭 Animations"),
    ("sticker", "🃏 Stickers"),
]


def _filters_buttons(s: dict) -> InlineKeyboardMarkup:
    S = _BS
    blocked = set(s['filters'])

    def toggle_row(key, label, on):
        mark = "✅" if on else "❌"
        return [
            make_button(label, callback_data=f"ak_filters_toggle:{key}", style=S.PRIMARY if S else None),
            make_button(mark, callback_data=f"ak_filters_toggle:{key}", style=(S.SUCCESS if on else S.DANGER) if S else None),
        ]

    rows = [toggle_row("forward_tag", "🏷️ Forward tag", s['tag'])]
    for key, label in _FILTER_TYPE_ROWS:
        rows.append(toggle_row(key, label, key not in blocked))
    rows.append(toggle_row("duplicate", "▶️ Skip duplicate", s['skip_duplicate']))
    rows.append([make_button("⫷ ʙᴀᴄᴋ", callback_data="akanager_btn", style=S.DANGER if S else None)])
    return InlineKeyboardMarkup(rows)


@Client.on_callback_query(filters.regex("^ak_filters$"))
async def ak_filters_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    s = await db.get_fwd_settings(user_id)
    await safe_edit(callback_query.message.edit_text, 
        "<b>💠 ᴄᴜsᴛᴏᴍ ғɪʟᴛᴇʀs 💠</b>\n\n<b>ᴄᴏɴғɪɢᴜʀᴇ ᴛʜᴇ ᴛʏᴘᴇ ᴏғ ᴍᴇssᴀɢᴇs ᴡʜɪᴄʜ ʏᴏᴜ ᴡᴀɴᴛ ғᴏʀᴡᴀʀᴅ.</b>",
        reply_markup=_filters_buttons(s),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^ak_filters_toggle:(forward_tag|duplicate|text|document|video|photo|audio|voice|animation|sticker)$"))
async def ak_filters_toggle_callback(client: Client, callback_query: CallbackQuery):
    key = callback_query.matches[0].group(1)
    user_id = callback_query.from_user.id
    s = await db.get_fwd_settings(user_id)

    if key == "forward_tag":
        await db.set_fwd_tag(user_id, not s['tag'])
    elif key == "duplicate":
        await db.set_fwd_skip_duplicate(user_id, not s['skip_duplicate'])
    else:
        blocked = list(s['filters'])
        if key in blocked:
            blocked.remove(key)
        else:
            blocked.append(key)
        await db.set_fwd_filters(user_id, blocked)

    await callback_query.answer()
    s = await db.get_fwd_settings(user_id)
    await safe_edit(callback_query.message.edit_reply_markup, reply_markup=_filters_buttons(s))


# ---------------------------------------------------------------------
# Minimal listen()/conversation system, ported from the reference bot's
# plugins/conversation.py — shared by the Button, Extension and Keyword
# "send me a value" flows below.
# ---------------------------------------------------------------------
_AK_LISTEN = {}  # user_id -> asyncio.Future


async def _ak_listen(user_id: int, timeout: int = 300):
    future = asyncio.get_event_loop().create_future()
    _AK_LISTEN[user_id] = future
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        _AK_LISTEN.pop(user_id, None)


@Client.on_message(filters.private & filters.text, group=-999)
async def _ak_listen_catcher(client: Client, message: Message):
    future = _AK_LISTEN.get(message.from_user.id)
    if future and not future.done():
        future.set_result(message)
        message.stop_propagation()
    else:
        # Not consumed here — let it fall through to lower-priority groups
        # (e.g. direct_utils.wait_for_reply's group=-1 handler used by the
        # "Add Bot Using Token" flow, cookies_manager, archive, ytdl, etc.).
        # Without this, this being the lowest group number (-999) in the
        # bot meant it silently swallowed EVERY private text message
        # whenever no _AK_LISTEN future was pending — including bot tokens
        # sent after "Add Bot Using Token", which is why the bot gave no
        # response at all.
        message.continue_propagation()


def _button_menu_text_and_markup(has_button: bool) -> tuple:
    text = (
        f"<u><b>ᴄᴜsᴛᴏᴍ ʙᴜᴛᴛᴏɴ</b></u>\n\n"
        f"<b>ʏᴏᴜ ᴄᴀɴ sᴇᴛ ᴀ ɪɴʟɪɴᴇ ʙᴜᴛᴛᴏɴ ᴛᴏ ᴍᴇssᴀɢᴇs.</b>\n\n"
        f"<u><b>ғᴏʀᴍᴀᴛ:</b></u>\n"
        f"<code>[FtmBotzx][buttonurl:https://t.me/ftmbotzx]</code>\n"
    )
    if has_button:
        rows = [[
            make_button("👀 sᴇᴇ ʙᴜᴛᴛᴏɴ", callback_data="ak_button_see", style=_BS.PRIMARY if _BS else None),
            make_button("🗑 ʀᴇᴍᴏᴠᴇ ʙᴜᴛᴛᴏɴ", callback_data="ak_button_delete", style=_BS.DANGER if _BS else None),
        ]]
    else:
        rows = [[make_button("➕ ᴀᴅᴅ ʙᴜᴛᴛᴏɴ ➕", callback_data="ak_button_add", style=_BS.PRIMARY if _BS else None)]]
    rows.append([make_button("⏪ ʙᴀᴄᴋ", callback_data="akanager_btn", style=_BS.DANGER if _BS else None)])
    return text, InlineKeyboardMarkup(rows)


@Client.on_callback_query(filters.regex("^ak_button$"))
async def ak_button_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    s = await db.get_fwd_settings(user_id)
    text, markup = _button_menu_text_and_markup(bool(s['button']))
    await safe_edit(callback_query.message.edit_text, text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex("^ak_button_add$"))
async def ak_button_add_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    await callback_query.message.delete()

    txt = await client.send_message(
        user_id,
        f"<b>sᴇɴᴅ ʏᴏᴜʀ ᴄᴜsᴛᴏᴍ ʙᴜᴛᴛᴏɴ.</b>\n\n"
        f"<b>ғᴏʀᴍᴀᴛ:</b>\n<code>[FtmBotzx][buttonurl:https://t.me/ftmbotzx]</code>\n\n"
        f"/cancel — cancel this process",
        parse_mode=enums.ParseMode.HTML,
    )
    ask = await _ak_listen(user_id)
    s = await db.get_fwd_settings(user_id)
    _, back_markup = _button_menu_text_and_markup(bool(s['button']))

    if ask is None:
        return await safe_edit(txt.edit_text, 
            f"<b>{E_SOON} Process has been automatically cancelled.</b>", reply_markup=back_markup, parse_mode=enums.ParseMode.HTML
        )
    if ask.text and ask.text.strip().startswith("/cancel"):
        return await safe_edit(txt.edit_text, "<b>ᴘʀᴏᴄᴇss ᴄᴀɴᴄᴇʟʟᴇᴅ.</b>", reply_markup=back_markup, parse_mode=enums.ParseMode.HTML)

    raw = ask.text.strip()
    if "buttonurl:" not in raw or not BTN_URL_REGEX.search(raw) or not parse_buttons(raw):
        return await safe_edit(txt.edit_text, 
            f"<b>{E_SOON} INVALID BUTTON.</b>\n"
            f"Format: <code>[Text][buttonurl:https://...]</code>",
            reply_markup=back_markup, parse_mode=enums.ParseMode.HTML,
        )

    await db.set_fwd_button(user_id, raw)
    s = await db.get_fwd_settings(user_id)
    _, back_markup = _button_menu_text_and_markup(bool(s['button']))
    await safe_edit(txt.edit_text, 
        f"<b>{E_CHECK} Successfully button added.</b>", reply_markup=back_markup, parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex("^ak_button_see$"))
async def ak_button_see_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    s = await db.get_fwd_settings(user_id)
    preview_markup = parse_buttons(s['button']) if s['button'] else None
    rows = list(preview_markup.inline_keyboard) if preview_markup else []
    rows.append([make_button("⏪ ʙᴀᴄᴋ", callback_data="ak_button", style=_BS.DANGER if _BS else None)])
    await safe_edit(callback_query.message.edit_text, 
        "<b>ʏᴏᴜʀ ᴄᴜsᴛᴏᴍ ʙᴜᴛᴛᴏɴ</b>", reply_markup=InlineKeyboardMarkup(rows), parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex("^ak_button_delete$"))
async def ak_button_delete_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    await db.clear_fwd_button(user_id)
    _, back_markup = _button_menu_text_and_markup(False)
    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_CHECK} Successfully button deleted.</b>", reply_markup=back_markup, parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex("^ak_core$"))
async def ak_core_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await safe_edit(callback_query.message.edit_text, 
        f"<blockquote>{E_INFO} <b>ᴍᴀɴᴀɢᴇʀ (ғᴏʀᴡᴀʀᴅɪɴɢ ᴄᴏʀᴇ)</b>\n\n"
        f"<code>/addsource -100xxxxxxxxxx</code> — add a source channel\n"
        f"<code>/sources</code> — list sources\n"
        f"<code>/addtarget -100xxxxxxxxxx</code> — add a target channel\n"
        f"<code>/targets</code> — list targets\n"
        f"<code>/forwardmode on|off</code> — start/stop live forwarding\n"
        f"<code>/forwardstatus</code> — check status\n\n"
        f"<code>/addreplacer old | new</code> · <code>/clearreplacer</code>\n"
        f"<code>/addremover word</code> · <code>/clearremover</code>\n"
        f"<code>/setprefix text</code> · <code>/setsuffix text</code> · <code>/clearcaption</code>\n\n"
        f"<b>ᴀᴅᴠᴀɴᴄᴇᴅ:</b> <code>/numbering</code> · <code>/bullets</code> · <code>/deltamode</code> · "
        f"<code>/thetamode</code> · <code>/blastmode</code> · <code>/usernameremover</code> · "
        f"<code>/linkremover</code> · <code>/coursesellers</code> · <code>/textonlymode</code> · <code>/pimode</code>\n\n"
        f"<i>Requires a connected bot — /setbot first.</i></blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ", callback_data="akanager_btn", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )
