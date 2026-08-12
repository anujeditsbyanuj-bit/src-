import os
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from database.db import db
from Akbots.strings import COMMANDS_TXT
from Akbots.caption import CAPTION_PLACEHOLDERS_HELP, render_caption
from config import (
    MAX_BATCH_IDS_FREE, MAX_BATCH_IDS_PREMIUM,
    BATCH_LIMIT_OPTIONS_FREE, BATCH_LIMIT_OPTIONS_PREMIUM,
)
from logger import LOGGER
from Akbots.direct_utils import safe_edit, wait_for_reply

logger = LOGGER(__name__)

try:
    from pyrogram.enums import ButtonStyle
    BUTTON_STYLE_SUPPORTED = True
except ImportError:
    BUTTON_STYLE_SUPPORTED = False

# =========================================================
# Custom Premium Emojis
# =========================================================

E_WARN    = '<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji>'
E_INFO    = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'
E_GEAR    = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
E_CHECK   = '<tg-emoji emoji-id="5039844895779455925">✔️</tg-emoji>'
E_CROSS   = '<tg-emoji emoji-id="5042112436648281096">❌</tg-emoji>'
E_BOLT    = '<tg-emoji emoji-id="5456140674028019486">⚡️</tg-emoji>'
E_DIAMOND = '<tg-emoji emoji-id="5427168083074628963">💎</tg-emoji>'
E_STATS   = '<tg-emoji emoji-id="5042290883949495533">📊</tg-emoji>'
E_PENCIL  = '<tg-emoji emoji-id="5395444784611480792">✏️</tg-emoji>'
E_IMAGE   = '<tg-emoji emoji-id="5395444784611480792">🖼</tg-emoji>'
E_TRASH   = '<tg-emoji emoji-id="5260293700088511294">🗑</tg-emoji>'
E_TIP     = '<tg-emoji emoji-id="5422439311196834318">💡</tg-emoji>'
E_BACK    = '<tg-emoji emoji-id="5447183459602669338">⬅️</tg-emoji>'
E_LIST    = '<tg-emoji emoji-id="5334544901428229844">📜</tg-emoji>'
E_CROWN   = '<tg-emoji emoji-id="5217822164362739968">👑</tg-emoji>'
E_GREEN   = '<tg-emoji emoji-id="5416081784641168838">🟢</tg-emoji>'
E_RED     = '<tg-emoji emoji-id="5411225014148014586">🔴</tg-emoji>'
E_CLOCK   = '<tg-emoji emoji-id="5386367538735104399">⌛</tg-emoji>'
E_BATCH   = '<tg-emoji emoji-id="5341498088408234504">💯</tg-emoji>'

# =========================================================
# Icon IDs for Buttons
# =========================================================

ICON_LIST    = 5334544901428229844
ICON_STATS   = 5334544901428229844
ICON_DELETE  = 5260293700088511294
ICON_IMAGE   = 5395444784611480792
ICON_EDIT    = 5395444784611480792
ICON_HOME    = 5447183459602669338
ICON_CLOSE   = 5210952531676504517
ICON_BACK    = 5447183459602669338
ICON_INFO    = 5334544901428229844


def make_button(text, callback_data=None, url=None,
                icon_custom_emoji_id=None, style=None):
    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if BUTTON_STYLE_SUPPORTED:
        if icon_custom_emoji_id:
            kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id
        if style is not None:
            kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)


def get_back_close_buttons():
    if BUTTON_STYLE_SUPPORTED:
        S = ButtonStyle
        return [[
            make_button(" ⬅️ ʙᴀᴄᴋ ",  callback_data="settings_back_btn", icon_custom_emoji_id=ICON_BACK,  style=S.PRIMARY),
            make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",         icon_custom_emoji_id=ICON_CLOSE, style=S.DANGER),
        ]]
    else:
        return [[
            make_button(" ⬅️ ʙᴀᴄᴋ ",  callback_data="settings_back_btn", icon_custom_emoji_id=ICON_BACK, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None),
        ]]


def _caption_menu_buttons():
    S = ButtonStyle if BUTTON_STYLE_SUPPORTED else None
    return [
        [make_button("📝 sᴇᴛ ᴄᴀᴘᴛɪᴏɴ", callback_data="caption_set_btn", style=S.SUCCESS if S else None)],
        [make_button("👀 sᴇᴇ ᴄᴀᴘᴛɪᴏɴ", callback_data="caption_see_btn", style=S.PRIMARY if S else None),
         make_button("🗑 ᴅᴇʟᴇᴛᴇ ᴄᴀᴘᴛɪᴏɴ", callback_data="caption_del_btn", style=S.DANGER if S else None)],
        [make_button("🔁 ʙᴀᴄᴋ", callback_data="settings_back_btn", style=S.DANGER if S else None)],
    ]


def _thumb_menu_buttons():
    S = ButtonStyle if BUTTON_STYLE_SUPPORTED else None
    return [
        [make_button("📤 sᴇᴛ ᴛʜᴜᴍʙɴᴀɪʟ", callback_data="thumb_set_btn", style=S.SUCCESS if S else None)],
        [make_button("🔁 ʙᴀᴄᴋ", callback_data="settings_back_btn", style=S.DANGER if S else None)],
    ]


def build_upload_type_view(mode: str):
    """Builds the (text, markup) pair for the Upload Type menu —
    matches the reference Document / Media / Back layout."""
    is_doc = (mode == "document")
    current_label = "DOCUMENT" if is_doc else "MEDIA"
    text = (
        f"<blockquote>📤 <b>ᴜᴘʟᴏᴀᴅ ᴛʏᴘᴇ</b>\n\n"
        f"{E_INFO} <b>ᴄᴜʀʀᴇɴᴛ:</b> <code>{current_label}</code>\n\n"
        f"{E_TIP} Choose how you want to receive media files:</blockquote>"
    )
    rows = [
        [make_button(
            " 📄 ᴅᴏᴄᴜᴍᴇɴᴛ ", callback_data="um:document",
            icon_custom_emoji_id=ICON_EDIT if BUTTON_STYLE_SUPPORTED else None,
            style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None,
        )],
        [make_button(
            " 🎬 ᴍᴇᴅɪᴀ ", callback_data="um:auto",
            icon_custom_emoji_id=ICON_EDIT if BUTTON_STYLE_SUPPORTED else None,
            style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None,
        )],
        [make_button(
            " ⬅️ ʙᴀᴄᴋ ", callback_data="settings_back_btn",
            icon_custom_emoji_id=ICON_BACK if BUTTON_STYLE_SUPPORTED else None,
            style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None,
        )],
    ]
    return text, InlineKeyboardMarkup(rows)


def get_settings_buttons():
    if BUTTON_STYLE_SUPPORTED:
        S = ButtonStyle
        return InlineKeyboardMarkup([
            [make_button(" 📜 ᴄᴏᴍᴍᴀɴᴅ ʟɪsᴛ ",      callback_data="cmd_list_btn",  icon_custom_emoji_id=ICON_LIST,   style=S.PRIMARY)],
            [make_button(" 📊 ᴍʏ ᴜsᴀɢᴇ sᴛᴀᴛs ",     callback_data="user_stats_btn", icon_custom_emoji_id=ICON_STATS,  style=S.PRIMARY)],
            [make_button(" 🗑 ᴅᴜᴍᴘ ᴄʜᴀᴛ ",          callback_data="dump_chat_btn",  icon_custom_emoji_id=ICON_DELETE, style=S.PRIMARY)],
            [make_button(" 💯 ʙᴀᴛᴄʜ ʟɪᴍɪᴛ ",        callback_data="batch_limit_btn", icon_custom_emoji_id=ICON_STATS, style=S.PRIMARY)],
            [
                make_button(" 🖼 ᴛʜᴜᴍʙɴᴀɪʟ ", callback_data="thumb_btn",   icon_custom_emoji_id=ICON_IMAGE, style=S.PRIMARY),
                make_button(" 📝 ᴄᴀᴘᴛɪᴏɴ ",   callback_data="caption_btn", icon_custom_emoji_id=ICON_EDIT,  style=S.PRIMARY),
            ],
            [make_button(" 📤 ᴜᴘʟᴏᴀᴅ ᴍᴏᴅᴇ ", callback_data="upload_mode_btn", icon_custom_emoji_id=ICON_EDIT, style=S.PRIMARY)],
            [make_button(" 🎬 ᴇɴᴄᴏᴅɪɴɢ sᴇᴛᴛɪɴɢs ", callback_data="encset:root", icon_custom_emoji_id=ICON_EDIT, style=S.PRIMARY)],
            [make_button(" 🗄 ᴍʏ ᴅᴀᴛᴀʙᴀsᴇ ", callback_data="database_btn", icon_custom_emoji_id=ICON_INFO, style=S.PRIMARY)],
            [make_button(" 📡 ᴄʜᴀɴɴᴇʟs ", callback_data="channels_btn", icon_custom_emoji_id=ICON_LIST, style=S.PRIMARY)],
            [make_button(" 🚀 ᴍᴀɴᴀɢᴇʀ ", callback_data="akanager_btn", icon_custom_emoji_id=ICON_LIST, style=S.PRIMARY)],
            [make_button(" ⚡ ᴛɪᴛᴀɴɪᴜᴍ ᴄʟᴏɴᴇ ᴍᴏᴅᴇ ", callback_data="titanium_status", icon_custom_emoji_id=ICON_LIST, style=S.PRIMARY)],
            [make_button(" ❌ ᴄʟᴏsᴇ ᴍᴇɴᴜ ", callback_data="close_btn", icon_custom_emoji_id=ICON_CLOSE, style=S.DANGER)],
        ])
    else:
        return InlineKeyboardMarkup([
            [make_button(" 📜 ᴄᴏᴍᴍᴀɴᴅ ʟɪsᴛ ",      callback_data="cmd_list_btn",  icon_custom_emoji_id=ICON_LIST, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" 📊 ᴍʏ ᴜsᴀɢᴇ sᴛᴀᴛs ",     callback_data="user_stats_btn", icon_custom_emoji_id=ICON_STATS, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" 🗑 ᴅᴜᴍᴘ ᴄʜᴀᴛ ",          callback_data="dump_chat_btn",  icon_custom_emoji_id=ICON_DELETE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" 💯 ʙᴀᴛᴄʜ ʟɪᴍɪᴛ ",        callback_data="batch_limit_btn", icon_custom_emoji_id=ICON_STATS, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [
                make_button(" 🖼 ᴛʜᴜᴍʙɴᴀɪʟ ", callback_data="thumb_btn",   icon_custom_emoji_id=ICON_IMAGE, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
                make_button(" 📝 ᴄᴀᴘᴛɪᴏɴ ",   callback_data="caption_btn", icon_custom_emoji_id=ICON_EDIT, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            ],
            [make_button(" 📤 ᴜᴘʟᴏᴀᴅ ᴍᴏᴅᴇ ", callback_data="upload_mode_btn", icon_custom_emoji_id=ICON_EDIT, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" 🎬 ᴇɴᴄᴏᴅɪɴɢ sᴇᴛᴛɪɴɢs ", callback_data="encset:root", icon_custom_emoji_id=ICON_EDIT, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" 🗄 ᴍʏ ᴅᴀᴛᴀʙᴀsᴇ ", callback_data="database_btn", icon_custom_emoji_id=ICON_INFO, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" 📡 ᴄʜᴀɴɴᴇʟs ", callback_data="channels_btn", icon_custom_emoji_id=ICON_LIST, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" 🚀 ᴍᴀɴᴀɢᴇʀ ", callback_data="akanager_btn", icon_custom_emoji_id=ICON_LIST, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" ⚡ ᴛɪᴛᴀɴɪᴜᴍ ᴄʟᴏɴᴇ ᴍᴏᴅᴇ ", callback_data="titanium_status", icon_custom_emoji_id=ICON_LIST, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" ❌ ᴄʟᴏsᴇ ᴍᴇɴᴜ ", callback_data="close_btn", icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)],
        ])


# ======================================================
# /settings - Enhanced Professional Settings Menu
# ======================================================

@Client.on_message(filters.command("settings") & filters.private)
async def settings_menu(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    is_premium = await db.check_premium(user_id)
    premium_badge = f"{E_DIAMOND} Premium Member" if is_premium else f"{E_INFO} Free User"
    text = (
        f"<blockquote>{E_GEAR} <b>sᴇᴛᴛɪɴɢs ᴘᴀɴᴇʟ</b>\n\n"
        f"{E_INFO} <b>ᴀᴄᴄᴏᴜɴᴛ:</b> {premium_badge}\n"
        f"{E_BOLT} <b>ᴜsᴇʀ ɪᴅ:</b> <code>{user_id}</code>\n\n"
        f"{E_TIP} Select an option below to customize your experience.</blockquote>"
    )
    await message.reply_text(text, reply_markup=get_settings_buttons(), parse_mode=enums.ParseMode.HTML)


# ======================================================
# /commands - Direct Access to Commands List
# ======================================================

@Client.on_message(filters.command("commands") & filters.private)
async def direct_commands(client: Client, message: Message):
    if BUTTON_STYLE_SUPPORTED:
        buttons = InlineKeyboardMarkup([[
            make_button(" ⚙️ ᴏᴘᴇɴ sᴇᴛᴛɪɴɢs ", callback_data="settings_back_btn", style=ButtonStyle.PRIMARY),
            make_button(" ❌ ᴄʟᴏsᴇ ",          callback_data="close_btn",          style=ButtonStyle.DANGER),
        ]])
    else:
        buttons = InlineKeyboardMarkup([[
            make_button(" ⚙️ ᴏᴘᴇɴ sᴇᴛᴛɪɴɢs ", callback_data="settings_back_btn", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            make_button(" ❌ ᴄʟᴏsᴇ ",          callback_data="close_btn", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None),
        ]])
    await message.reply_text(
        COMMANDS_TXT, reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True
    )


# ======================================================
# /setchat - Set or Clear Dump Chat
# ======================================================

@Client.on_message(filters.command("setchat") & filters.private)
async def set_dump_chat(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_TRASH} <b>sᴇᴛ ᴅᴜᴍᴘ ᴄʜᴀᴛ</b>\n\n"
            f"<b>ᴜsᴀɢᴇ:</b>\n"
            f"<code>/setchat &lt;chat_id&gt;</code> {E_BOLT} Set forward destination\n"
            f"<code>/setchat clear</code> {E_CROSS} Remove dump chat\n\n"
            f"<i>{E_TIP} Example: /setchat -1001234567890</i></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    arg = message.command[1].strip().lower()
    if arg == "clear":
        await db.set_dump_chat(user_id, None)
        return await message.reply_text(
            f"<b>{E_CHECK} Dump Chat Cleared Successfully</b>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        chat_id = int(arg)
        try:
            chat = await client.get_chat(chat_id)
            chat_title = chat.title or "Private Chat"
        except Exception as e:
            logger.debug(f"set dump chat: get_chat({chat_id}) failed: {e}")
            chat_title = "Unknown Chat"
        await db.set_dump_chat(user_id, chat_id)
        await message.reply_text(
            f"<blockquote>{E_CHECK} <b>ᴅᴜᴍᴘ ᴄʜᴀᴛ sᴇᴛ sᴜᴄᴄᴇssғᴜʟʟʏ</b>\n\n"
            f"{E_BOLT} <b>ғᴏʀᴡᴀʀᴅ ᴛᴏ:</b> <code>{chat_id}</code>\n"
            f"{E_INFO} <b>ᴛɪᴛʟᴇ:</b> {chat_title}</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    except ValueError:
        await message.reply_text(
            f"<b>{E_CROSS} Invalid Chat ID</b>\n\n<i>Must be a number (e.g., -1001234567890)</i>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(
            f"<b>{E_CROSS} Unable to Access Chat</b>\n<i>{e}</i>",
            parse_mode=enums.ParseMode.HTML
        )


# ======================================================
# Callbacks - Full Settings Navigation
# ======================================================

@Client.on_callback_query(filters.regex("^(cmd_list_btn|dump_chat_btn|thumb_btn|thumb_set_btn|caption_btn|caption_set_btn|caption_see_btn|caption_del_btn|user_stats_btn|batch_limit_btn|upload_mode_btn|settings_back_btn|close_btn)$"))
async def settings_callbacks(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    back_close = get_back_close_buttons()

    if data == "cmd_list_btn":
        # COMMANDS_TXT (~3600 chars) is always longer than Telegram's 1024-char
        # caption limit. The settings panel this button lives on is a photo
        # message (settings_panel() in start.py edits its *caption*), so a
        # plain edit_message_text() here silently falls back to a caption
        # edit that's guaranteed to fail — the tap does nothing, no error
        # shown. Deleting the photo and sending a fresh text-only message
        # (same fix pattern as channels.py's _render_channels_menu) sidesteps
        # the caption limit entirely.
        markup = InlineKeyboardMarkup(back_close)
        msg = callback_query.message
        if msg and getattr(msg, "media", None):
            try:
                await msg.delete()
            except Exception:
                pass
            else:
                await client.send_message(
                    user_id, COMMANDS_TXT, reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
                )
                await callback_query.answer()
                return
        await safe_edit(callback_query.edit_message_text, 
            COMMANDS_TXT,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True
        )

    elif data == "batch_limit_btn":
        is_premium = await db.check_premium(user_id)
        options = BATCH_LIMIT_OPTIONS_PREMIUM if is_premium else BATCH_LIMIT_OPTIONS_FREE
        hard_cap = MAX_BATCH_IDS_PREMIUM if is_premium else MAX_BATCH_IDS_FREE
        current = await db.get_batch_limit(user_id)
        current_label = str(min(current, hard_cap)) if current else f"{hard_cap} (default)"

        rows = [
            [make_button(f" {val} ", callback_data=f"bl:{val}",
                         icon_custom_emoji_id=ICON_STATS if BUTTON_STYLE_SUPPORTED else None,
                         style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]
            for val in options
        ]
        rows.extend(back_close)
        text = (
            f"<blockquote>{E_BATCH} <b>ʙᴀᴛᴄʜ ʟɪᴍɪᴛ</b>\n\n"
            f"{E_INFO} <b>ᴄᴜʀʀᴇɴᴛ:</b> <code>{current_label}</code> messages per batch link\n\n"
            f"{E_TIP} Choose how many messages one batch link can save at a time.</blockquote>"
        )
        await safe_edit(callback_query.edit_message_text, 
            text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=enums.ParseMode.HTML
        )

    elif data == "dump_chat_btn":
        current = await db.get_dump_chat(user_id)
        if current:
            try:
                chat = await client.get_chat(current)
                title = chat.title or "Private Chat"
            except Exception as e:
                logger.debug(f"dump chat display: get_chat({current}) failed: {e}")
                title = "Unknown (Inaccessible)"
            text = (
                f"<blockquote>{E_TRASH} <b>ᴄᴜʀʀᴇɴᴛ ᴅᴜᴍᴘ ᴄʜᴀᴛ</b>\n\n"
                f"{E_BOLT} <b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{current}</code>\n"
                f"{E_INFO} <b>ᴛɪᴛʟᴇ:</b> {title}\n\n"
                f"{E_CHECK} All saved files are forwarded here.\n"
                f"{E_TIP} Use /setchat to change or clear.</blockquote>"
            )
        else:
            text = (
                f"<blockquote>{E_TRASH} <b>ɴᴏ ᴅᴜᴍᴘ ᴄʜᴀᴛ sᴇᴛ</b>\n\n"
                f"{E_INFO} Saved files appear only in this chat.\n"
                f"{E_TIP} Use /setchat &lt;chat_id&gt; to enable forwarding.</blockquote>"
            )
        await safe_edit(callback_query.edit_message_text, 
            text, reply_markup=InlineKeyboardMarkup(back_close), parse_mode=enums.ParseMode.HTML
        )

    elif data == "thumb_btn":
        thumb = await db.get_thumbnail(user_id)
        if thumb and os.path.exists(thumb):
            await callback_query.message.reply_photo(
                thumb,
                caption=f"<b>{E_IMAGE} Your Current Custom Thumbnail</b>\n\n"
                        f"<i>{E_TIP} Send a new photo to update • /del_thumb to remove</i>",
                parse_mode=enums.ParseMode.HTML
            )
            await callback_query.answer("Thumbnail preview sent below 👇")
        else:
            await safe_edit(callback_query.edit_message_text, 
                f"<blockquote>{E_IMAGE} <b>ɴᴏ ᴄᴜsᴛᴏᴍ ᴛʜᴜᴍʙɴᴀɪʟ sᴇᴛ</b>\n\n"
                f"{E_TIP} Send a photo to set as default thumbnail for uploads.</blockquote>",
                reply_markup=InlineKeyboardMarkup(_thumb_menu_buttons()),
                parse_mode=enums.ParseMode.HTML
            )

    elif data == "thumb_set_btn":
        await callback_query.answer()
        ask = await callback_query.message.reply_text(
            f"<blockquote>📤 <b>sᴇᴛ ᴄᴜsᴛᴏᴍ ᴛʜᴜᴍʙɴᴀɪʟ</b>\n\n"
            f"{E_TIP} Send a photo now to set it as your custom thumbnail.\n\n"
            f"Use /cancel to cancel.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
        try:
            resp = await wait_for_reply(client, chat_id=callback_query.message.chat.id, user_id=user_id, timeout=120)
        except asyncio.TimeoutError:
            return await safe_edit(ask.edit_text, f"<b>{E_CROSS} Timed out — nothing changed.</b>", parse_mode=enums.ParseMode.HTML)

        if resp.text and resp.text.strip() == "/cancel":
            return await safe_edit(ask.edit_text, f"<b>{E_INFO} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)
        if not resp.photo:
            return await safe_edit(ask.edit_text, f"<b>{E_CROSS} That's not a photo.</b> Run it again to retry.", parse_mode=enums.ParseMode.HTML)

        await db.set_thumbnail(user_id, resp.photo.file_id)
        await ask.delete()
        await client.send_photo(
            user_id, resp.photo.file_id,
            caption=f"<b>{E_CHECK} Custom thumbnail set!</b>\n\n<i>{E_TIP} This will be used for your future uploads.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    elif data == "caption_btn":
        text = (
            f"<u><b>✏️ ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ ✏️</b></u>\n\n"
            f"📝 <b>ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ sᴇᴛᴛɪɴɢs</b>\n"
            f"<i>You can set a custom caption template for videos, documents and audio files.</i>\n\n"
            f"{CAPTION_PLACEHOLDERS_HELP}"
        )
        await safe_edit(callback_query.edit_message_text, 
            text, reply_markup=InlineKeyboardMarkup(_caption_menu_buttons()), parse_mode=enums.ParseMode.HTML
        )

    elif data == "caption_set_btn":
        await callback_query.answer()
        ask = await callback_query.message.reply_text(
            f"<blockquote>📝 <b>sᴇᴛ ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ</b>\n\n"
            f"{E_TIP} Send your custom caption now.\n\n"
            f"{CAPTION_PLACEHOLDERS_HELP}\n\n"
            f"Use /cancel to cancel.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
        try:
            resp = await wait_for_reply(client, chat_id=callback_query.message.chat.id, user_id=user_id, timeout=180)
        except asyncio.TimeoutError:
            return await safe_edit(ask.edit_text, f"<b>{E_CROSS} Timed out — nothing changed.</b>", parse_mode=enums.ParseMode.HTML)

        text_in = (resp.text or "").strip()
        if text_in == "/cancel":
            return await safe_edit(ask.edit_text, f"<b>{E_INFO} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)
        if not text_in:
            return await safe_edit(ask.edit_text, f"<b>{E_CROSS} That's not text.</b> Run it again to retry.", parse_mode=enums.ParseMode.HTML)

        try:
            text_in.format(filename="", size="", caption="", year="", language="", quality="", type="")
        except (KeyError, IndexError) as e:
            return await safe_edit(ask.edit_text, f"<b>{E_CROSS} Invalid variable:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

        await db.set_caption(user_id, text_in)
        await safe_edit(ask.edit_text, 
            f"<b>{E_CHECK} Custom caption saved!</b>\n\n<code>{text_in}</code>",
            parse_mode=enums.ParseMode.HTML
        )

    elif data == "caption_see_btn":
        caption = await db.get_caption(user_id)
        if caption:
            preview = render_caption(
                caption, filename="Video_File_2024.mp4", size="1.2 GB",
                caption="Original caption text", media_type="Video",
            )
            text = (
                f"<blockquote>{E_PENCIL} <b>ʏᴏᴜʀ ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ</b>\n\n"
                f"<code>{caption}</code>\n\n"
                f"{E_INFO} <b>ᴘʀᴇᴠɪᴇᴡ:</b>\n{preview}</blockquote>"
            )
        else:
            text = f"<blockquote>{E_CROSS} <b>ɴᴏ ᴄᴀᴘᴛɪᴏɴ sᴇᴛ</b>\n\n{E_INFO} You are currently using the default bot caption.</blockquote>"
        await safe_edit(callback_query.edit_message_text, 
            text, reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ", callback_data="caption_btn", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]]),
            parse_mode=enums.ParseMode.HTML
        )

    elif data == "caption_del_btn":
        caption = await db.get_caption(user_id)
        if not caption:
            text = f"<b>{E_WARN} No Caption Found.</b>\n<i>You don't have a custom caption set.</i>"
        else:
            await db.del_caption(user_id)
            text = f"<blockquote>{E_TRASH} <b>ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ ʀᴇᴍᴏᴠᴇᴅ</b>\n\n<i>{E_INFO} Your uploads will now use the default bot caption.</i></blockquote>"
        await safe_edit(callback_query.edit_message_text, 
            text, reply_markup=InlineKeyboardMarkup([[make_button("🔁 ʙᴀᴄᴋ", callback_data="caption_btn", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]]),
            parse_mode=enums.ParseMode.HTML
        )

    elif data == "upload_mode_btn":
        mode = await db.get_upload_mode(user_id)
        text, markup = build_upload_type_view(mode)
        await safe_edit(callback_query.edit_message_text, 
            text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
        )

    elif data == "user_stats_btn":
        is_premium = await db.check_premium(user_id)
        user_data  = await db.col.find_one({'id': int(user_id)})
        if is_premium:
            limit_text = "♾️ Unlimited"
            usage_text = "Ignored (Premium)"
        else:
            daily_limit = 10
            used        = user_data.get('daily_usage', 0)
            limit_text  = f"{daily_limit} Files / 24h"
            usage_text  = f"{used} / {daily_limit}"
        text = (
            f"<blockquote>{E_STATS} <b>ᴍʏ ᴜsᴀɢᴇ sᴛᴀᴛɪsᴛɪᴄs</b>\n\n"
            f"{E_CROWN if is_premium else E_INFO} <b>ᴘʟᴀɴ:</b> {'💎 Premium' if is_premium else '👤 Free'}\n"
            f"{E_CLOCK} <b>ᴅᴀɪʟʏ ʟɪᴍɪᴛ:</b> <code>{limit_text}</code>\n"
            f"{E_BATCH} <b>ᴛᴏᴅᴀʏ's ᴜsᴀɢᴇ:</b> <code>{usage_text}</code>\n\n"
            f"{E_TIP} Upgrade to Premium for unlimited downloads!</blockquote>"
        )
        await safe_edit(callback_query.edit_message_text, 
            text, reply_markup=InlineKeyboardMarkup(back_close), parse_mode=enums.ParseMode.HTML
        )

    elif data == "settings_back_btn":
        is_premium     = await db.check_premium(user_id)
        premium_badge  = f"{E_DIAMOND} Premium Member" if is_premium else f"{E_INFO} Free User"
        text = (
            f"<blockquote>{E_GEAR} <b>sᴇᴛᴛɪɴɢs ᴘᴀɴᴇʟ</b>\n\n"
            f"{E_INFO} <b>ᴀᴄᴄᴏᴜɴᴛ:</b> {premium_badge}\n"
            f"{E_BOLT} <b>ᴜsᴇʀ ɪᴅ:</b> <code>{user_id}</code>\n\n"
            f"{E_TIP} Select an option below to customize your experience.</blockquote>"
        )
        await safe_edit(callback_query.edit_message_text, 
            text, reply_markup=get_settings_buttons(), parse_mode=enums.ParseMode.HTML
        )

    elif data == "close_btn":
        await callback_query.message.delete()

    await callback_query.answer()


# ======================================================
# /upload_mode - Direct shortcut into the Upload Mode toggle
# ======================================================

@Client.on_message(filters.command("upload_mode") & filters.private)
async def upload_mode_command(client: Client, message: Message):
    user_id = message.from_user.id
    mode = await db.get_upload_mode(user_id)
    text, markup = build_upload_type_view(mode)
    await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^um:(auto|document)$"))
async def upload_mode_set_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    mode = callback_query.matches[0].group(1)

    await db.set_upload_mode(user_id, mode)
    is_doc = (mode == "document")
    await callback_query.answer(
        f"Upload type set to {'Document' if is_doc else 'Media'} ✅", show_alert=False
    )

    text, markup = build_upload_type_view(mode)
    await safe_edit(callback_query.edit_message_text, 
        text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex(r"^bl:(\d+)$"))
async def batch_limit_set_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    chosen = int(callback_query.matches[0].group(1))

    is_premium = await db.check_premium(user_id)
    hard_cap = MAX_BATCH_IDS_PREMIUM if is_premium else MAX_BATCH_IDS_FREE
    options = BATCH_LIMIT_OPTIONS_PREMIUM if is_premium else BATCH_LIMIT_OPTIONS_FREE

    # Server-side validation: even if a stale/crafted callback arrives,
    # never let a user set a limit above their plan's allowed value.
    if chosen not in options:
        chosen = min(chosen, hard_cap)

    await db.set_batch_limit(user_id, chosen)
    await callback_query.answer(f"Batch limit set to {chosen} ✅", show_alert=False)

    back_close = get_back_close_buttons()
    text = (
        f"<blockquote>{E_BATCH} <b>ʙᴀᴛᴄʜ ʟɪᴍɪᴛ</b>\n\n"
        f"{E_CHECK} <b>ᴜᴘᴅᴀᴛᴇᴅ:</b> <code>{chosen}</code> messages per batch link\n\n"
        f"{E_TIP} Choose how many messages one batch link can save at a time.</blockquote>"
    )
    await safe_edit(callback_query.edit_message_text, 
        text, reply_markup=InlineKeyboardMarkup(back_close), parse_mode=enums.ParseMode.HTML
    )
