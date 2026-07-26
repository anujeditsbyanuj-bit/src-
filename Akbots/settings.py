import os
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from database.db import db
from Akbots.strings import COMMANDS_TXT
from config import (
    MAX_BATCH_IDS_FREE, MAX_BATCH_IDS_PREMIUM,
    BATCH_LIMIT_OPTIONS_FREE, BATCH_LIMIT_OPTIONS_PREMIUM,
)
from logger import LOGGER

logger = LOGGER(__name__)

try:
    from pyrogram.enums import ButtonStyle
    BUTTON_STYLE_SUPPORTED = True
except ImportError:
    BUTTON_STYLE_SUPPORTED = False

# =========================================================
# Custom Premium Emojis
# =========================================================

E_WARN    = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO    = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR    = '<emoji id=5341715473882955310>⚙️</emoji>'
E_CHECK   = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS   = '<emoji id=5210952531676504517>❌</emoji>'
E_BOLT    = '<emoji id=5456140674028019486>⚡️</emoji>'
E_DIAMOND = '<emoji id=5217822164362739968>💎</emoji>'
E_STATS   = '<emoji id=5334544901428229844>📊</emoji>'
E_PENCIL  = '<emoji id=5395444784611480792>✏️</emoji>'
E_IMAGE   = '<emoji id=5395444784611480792>🖼</emoji>'
E_TRASH   = '<emoji id=5260293700088511294>🗑</emoji>'
E_TIP     = '<emoji id=5422439311196834318>💡</emoji>'
E_BACK    = '<emoji id=5447183459602669338>⬅️</emoji>'
E_LIST    = '<emoji id=5334544901428229844>📜</emoji>'
E_CROWN   = '<emoji id=5217822164362739968>👑</emoji>'
E_GREEN   = '<emoji id=5416081784641168838>🟢</emoji>'
E_RED     = '<emoji id=5411225014148014586>🔴</emoji>'
E_CLOCK   = '<emoji id=5386367538735104399>⌛</emoji>'
E_BATCH   = '<emoji id=5341498088408234504>💯</emoji>'

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
            [make_button(" 🚀 1 ᴍᴀɴᴀɢᴇʀ ", callback_data="akmanager_btn", icon_custom_emoji_id=ICON_LIST, style=S.PRIMARY)],
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
            [make_button(" 🚀 1 ᴍᴀɴᴀɢᴇʀ ", callback_data="akmanager_btn", icon_custom_emoji_id=ICON_LIST, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
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
        f"<blockquote>{E_GEAR} <b>Settings Panel</b>\n\n"
        f"{E_INFO} <b>Account:</b> {premium_badge}\n"
        f"{E_BOLT} <b>User ID:</b> <code>{user_id}</code>\n\n"
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
            f"<blockquote>{E_TRASH} <b>Set Dump Chat</b>\n\n"
            f"<b>Usage:</b>\n"
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
            f"<blockquote>{E_CHECK} <b>Dump Chat Set Successfully</b>\n\n"
            f"{E_BOLT} <b>Forward To:</b> <code>{chat_id}</code>\n"
            f"{E_INFO} <b>Title:</b> {chat_title}</blockquote>",
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

@Client.on_callback_query(filters.regex("^(cmd_list_btn|dump_chat_btn|thumb_btn|caption_btn|user_stats_btn|batch_limit_btn|settings_back_btn|close_btn)$"))
async def settings_callbacks(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    back_close = get_back_close_buttons()

    if data == "cmd_list_btn":
        await callback_query.edit_message_text(
            COMMANDS_TXT,
            reply_markup=InlineKeyboardMarkup(back_close),
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
            f"<blockquote>{E_BATCH} <b>Batch Limit</b>\n\n"
            f"{E_INFO} <b>Current:</b> <code>{current_label}</code> messages per batch link\n\n"
            f"{E_TIP} Choose how many messages one batch link can save at a time.</blockquote>"
        )
        await callback_query.edit_message_text(
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
                f"<blockquote>{E_TRASH} <b>Current Dump Chat</b>\n\n"
                f"{E_BOLT} <b>Chat ID:</b> <code>{current}</code>\n"
                f"{E_INFO} <b>Title:</b> {title}\n\n"
                f"{E_CHECK} All saved files are forwarded here.\n"
                f"{E_TIP} Use /setchat to change or clear.</blockquote>"
            )
        else:
            text = (
                f"<blockquote>{E_TRASH} <b>No Dump Chat Set</b>\n\n"
                f"{E_INFO} Saved files appear only in this chat.\n"
                f"{E_TIP} Use /setchat &lt;chat_id&gt; to enable forwarding.</blockquote>"
            )
        await callback_query.edit_message_text(
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
            await callback_query.edit_message_text(
                f"<blockquote>{E_IMAGE} <b>No Custom Thumbnail Set</b>\n\n"
                f"{E_TIP} Send a photo to set as default thumbnail for uploads.</blockquote>",
                reply_markup=InlineKeyboardMarkup(back_close),
                parse_mode=enums.ParseMode.HTML
            )

    elif data == "caption_btn":
        caption = await db.get_caption(user_id)
        if caption:
            preview = caption.format(filename="Video_File_2024.mp4", size="1.2 GB")
            text = (
                f"<blockquote>{E_PENCIL} <b>Current Custom Caption</b>\n\n"
                f"<code>{caption}</code>\n\n"
                f"{E_INFO} <b>Preview:</b>\n{preview}\n\n"
                f"{E_TIP} Placeholders: {{filename}}, {{size}}\n"
                f"{E_GEAR} /set_caption &lt;text&gt; to change • /del_caption to remove</blockquote>"
            )
        else:
            text = (
                f"<blockquote>{E_PENCIL} <b>No Custom Caption Set</b>\n\n"
                f"{E_TIP} Use /set_caption &lt;text&gt; to set one.\n"
                f"{E_INFO} Supports {{filename}} and {{size}} placeholders.</blockquote>"
            )
        await callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(back_close), parse_mode=enums.ParseMode.HTML
        )

    elif data == "upload_mode_btn":
        mode = await db.get_upload_mode(user_id)
        is_doc = (mode == "document")
        text = (
            f"<blockquote>📤 <b>Upload Mode</b>\n\n"
            f"{E_INFO} <b>Current:</b> <code>{'📁 Always Document' if is_doc else '🎬 Auto (video/audio/photo/document)'}</code>\n\n"
            f"{E_TIP} <b>Auto</b> sends video/audio/photo files in their native player, and everything "
            f"else as a document — the normal behavior.\n"
            f"{E_TIP} <b>Document</b> forces every file, including videos, out as a plain document. "
            f"Telegram won't re-compress it and it keeps the original quality/codec, at the cost of "
            f"losing the in-app video player and streaming.</blockquote>"
        )
        rows = [[make_button(
            f" {'🎬 SWITCH TO AUTO' if is_doc else '📁 SWITCH TO DOCUMENT'} ",
            callback_data=f"um:{'auto' if is_doc else 'document'}",
            icon_custom_emoji_id=ICON_EDIT if BUTTON_STYLE_SUPPORTED else None,
            style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None,
        )]]
        rows.extend(back_close)
        await callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=enums.ParseMode.HTML
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
            f"<blockquote>{E_STATS} <b>My Usage Statistics</b>\n\n"
            f"{E_CROWN if is_premium else E_INFO} <b>Plan:</b> {'💎 Premium' if is_premium else '👤 Free'}\n"
            f"{E_CLOCK} <b>Daily Limit:</b> <code>{limit_text}</code>\n"
            f"{E_BATCH} <b>Today's Usage:</b> <code>{usage_text}</code>\n\n"
            f"{E_TIP} Upgrade to Premium for unlimited downloads!</blockquote>"
        )
        await callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(back_close), parse_mode=enums.ParseMode.HTML
        )

    elif data == "settings_back_btn":
        is_premium     = await db.check_premium(user_id)
        premium_badge  = f"{E_DIAMOND} Premium Member" if is_premium else f"{E_INFO} Free User"
        text = (
            f"<blockquote>{E_GEAR} <b>Settings Panel</b>\n\n"
            f"{E_INFO} <b>Account:</b> {premium_badge}\n"
            f"{E_BOLT} <b>User ID:</b> <code>{user_id}</code>\n\n"
            f"{E_TIP} Select an option below to customize your experience.</blockquote>"
        )
        await callback_query.edit_message_text(
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
    is_doc = (mode == "document")
    text = (
        f"<blockquote>📤 <b>Upload Mode</b>\n\n"
        f"{E_INFO} <b>Current:</b> <code>{'📁 Always Document' if is_doc else '🎬 Auto (video/audio/photo/document)'}</code>\n\n"
        f"{E_TIP} <b>Auto</b> sends video/audio/photo files in their native player, and everything "
        f"else as a document — the normal behavior.\n"
        f"{E_TIP} <b>Document</b> forces every file, including videos, out as a plain document. "
        f"Telegram won't re-compress it and it keeps the original quality/codec, at the cost of "
        f"losing the in-app video player and streaming.</blockquote>"
    )
    rows = [[make_button(
        f" {'🎬 SWITCH TO AUTO' if is_doc else '📁 SWITCH TO DOCUMENT'} ",
        callback_data=f"um:{'auto' if is_doc else 'document'}",
        icon_custom_emoji_id=ICON_EDIT if BUTTON_STYLE_SUPPORTED else None,
        style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None,
    )], [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                      icon_custom_emoji_id=ICON_CLOSE if BUTTON_STYLE_SUPPORTED else None,
                      style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]]
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^um:(auto|document)$"))
async def upload_mode_set_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    mode = callback_query.matches[0].group(1)

    await db.set_upload_mode(user_id, mode)
    is_doc = (mode == "document")
    await callback_query.answer(
        f"Upload mode set to {'Document' if is_doc else 'Auto'} ✅", show_alert=False
    )

    back_close = get_back_close_buttons()
    text = (
        f"<blockquote>📤 <b>Upload Mode</b>\n\n"
        f"{E_CHECK} <b>Updated:</b> <code>{'📁 Always Document' if is_doc else '🎬 Auto (video/audio/photo/document)'}</code>\n\n"
        f"{E_TIP} This applies to every download — URL uploads, torrents, StreamTape, yt-dlp, everything "
        f"that goes through the shared upload path.</blockquote>"
    )
    rows = [[make_button(
        f" {'🎬 SWITCH TO AUTO' if is_doc else '📁 SWITCH TO DOCUMENT'} ",
        callback_data=f"um:{'auto' if is_doc else 'document'}",
        icon_custom_emoji_id=ICON_EDIT if BUTTON_STYLE_SUPPORTED else None,
        style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None,
    )]]
    rows.extend(back_close)
    await callback_query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=enums.ParseMode.HTML
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
        f"<blockquote>{E_BATCH} <b>Batch Limit</b>\n\n"
        f"{E_CHECK} <b>Updated:</b> <code>{chosen}</code> messages per batch link\n\n"
        f"{E_TIP} Choose how many messages one batch link can save at a time.</blockquote>"
    )
    await callback_query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(back_close), parse_mode=enums.ParseMode.HTML
    )
