# Akbots
# AK Manager menu — mirrors the reference forward-bot's /start layout
# (Bots / Channels / Caption / MongoDB / Filters / Button / AK Manager /
# Extra Settings / Back) using Akbots' own existing features wherever one
# already exists, and clearly marking the pieces that are still pending
# the bigger forwarding-engine port (Filters, Button, core AK Manager).
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from database.db import db
from logger import LOGGER
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
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
        [make_button("🤖 Bots", callback_data="akm_bots", style=_BS.PRIMARY if _BS else None),
         make_button("🏷 Channels", callback_data="channels_btn", style=_BS.PRIMARY if _BS else None)],
        [make_button("🖋 Caption", callback_data="caption_btn", style=_BS.SUCCESS if _BS else None),
         make_button("🗃 MongoDB", callback_data="database_btn", style=_BS.SUCCESS if _BS else None)],
        [make_button("🕵️ Filters", callback_data="akm_filters", style=_BS.PRIMARY if _BS else None),
         make_button("🔘 Button", callback_data="akm_button", style=_BS.SUCCESS if _BS else None)],
        [make_button("🚀 1 Manager 🚀", callback_data="akm_core", style=_BS.PRIMARY if _BS else None)],
        [make_button("🧪 Extra Settings", callback_data="akm_extra", style=_BS.PRIMARY if _BS else None)],
        [make_button("⬅️ Back", callback_data="settings_back_btn", style=_BS.DANGER if _BS else None)],
    ])


@Client.on_message(filters.command("akmanager") & filters.private)
async def akmanager_command(client: Client, message: Message):
    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)
    await message.reply_text(
        f"<b>🚀 1 Manager</b>\n\n<i>Change your settings as you wish:</i>",
        reply_markup=_ak_manager_menu(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akmanager_btn$"))
async def akmanager_btn_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        f"<b>🚀 1 Manager</b>\n\n<i>Change your settings as you wish:</i>",
        reply_markup=_ak_manager_menu(),
        parse_mode=enums.ParseMode.HTML,
    )
    await callback_query.answer()


def _bots_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [make_button("➕ Add bot", callback_data="akm_bots_add", style=_BS.SUCCESS if _BS else None)],
        [make_button("➕ Add User bot", callback_data="akm_bots_adduser", style=_BS.SUCCESS if _BS else None)],
        [make_button("🗑 Remove Bot", callback_data="akm_bots_remove", style=_BS.DANGER if _BS else None)],
        [make_button("⬅️ Back", callback_data="akmanager_btn", style=_BS.DANGER if _BS else None)],
    ])


async def _bots_menu_text(user_id: int) -> str:
    bot_token = await db.get_custom_bot(user_id)
    session = await db.get_session(user_id)
    bot_status = f"{E_CHECK} Connected" if bot_token else f"{E_SOON} Not connected"
    user_status = f"{E_CHECK} Logged in" if session else f"{E_SOON} Not logged in"
    return (
        f"<b>🤖 My Bots</b>\n\n"
        f"<i>You can manage your bots in here.</i>\n\n"
        f"<b>Bot:</b> {bot_status}\n"
        f"<b>User Bot:</b> {user_status}"
    )


@Client.on_callback_query(filters.regex("^akm_bots$"))
async def akm_bots_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        await _bots_menu_text(callback_query.from_user.id),
        reply_markup=_bots_menu(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akm_bots_add$"))
async def akm_bots_add_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        f"<blockquote>➕ <b>Add Bot</b>\n\n"
        f"{E_INFO} Get a bot token from @BotFather, then send:\n"
        f"<code>/setbot &lt;bot_token&gt;</code>\n\n"
        f"<i>Your bot will run alongside Akbots for forwarding.</i></blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("⬅️ Back", callback_data="akm_bots", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akm_bots_adduser$"))
async def akm_bots_adduser_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        f"<blockquote>➕ <b>Add User Bot</b>\n\n"
        f"{E_INFO} Log in with your own Telegram account (used as a fallback when your bot "
        f"can't access a chat). Send:\n"
        f"<code>/fwd_login</code>\n\n"
        f"<i>You'll be asked for your phone number, then the login code.</i></blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("⬅️ Back", callback_data="akm_bots", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akm_bots_remove$"))
async def akm_bots_remove_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        f"<blockquote>🗑 <b>Remove Bot / User Bot</b>\n\n"
        f"{E_INFO} <code>/rembot</code> — Disconnect your bot\n"
        f"{E_INFO} <code>/logout</code> — Log out your user bot session</blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("⬅️ Back", callback_data="akm_bots", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akm_extra$"))
async def akm_extra_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        f"<blockquote>{E_INFO} <b>Extra Settings</b>\n\n"
        f"<code>/uploadmode video|file|auto</code> — Send as video or document\n"
        f"<code>/spoiler on|off</code> — Hide media preview until tapped\n"
        f"<code>/noforward on|off</code> — Disable forwarding of uploads\n"
        f"<code>/invertmedia on|off</code> — Caption above media\n"
        f"<code>/screenshots on|off</code> — Capture screenshots (Video mode only)\n"
        f"<code>/samplevideo on|off</code> — Short preview clip (Premium)\n"
        f"<code>/autounzip on|off</code> — Auto-extract ZIP files\n"
        f"<code>/set_format mp4|mkv|webm</code> — YT-DLP output format\n"
        f"<code>/updates on|off</code> — Bot update broadcasts</blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("⬅️ Back", callback_data="akmanager_btn", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akm_filters$"))
async def akm_filters_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    from Akbots.forward_engine import FILTER_TYPES
    await callback_query.message.edit_text(
        f"<blockquote>{E_INFO} <b>Filters</b>\n\n"
        f"<code>/setfilters photo,video,document</code> — only forward these types\n"
        f"<code>/clearfilters</code> — forward every type again\n\n"
        f"<b>Available:</b> {', '.join(FILTER_TYPES)}</blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("⬅️ Back", callback_data="akmanager_btn", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akm_button$"))
async def akm_button_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        f"<blockquote>{E_INFO} <b>Button</b>\n\n"
        f"<code>/setbutton Button Text | https://example.com</code> — attach to every forwarded message\n"
        f"<code>/delbutton</code> — remove it</blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("⬅️ Back", callback_data="akmanager_btn", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^akm_core$"))
async def akm_core_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        f"<blockquote>{E_INFO} <b>1 Manager (Forwarding Core)</b>\n\n"
        f"<code>/addsource -100xxxxxxxxxx</code> — add a source channel\n"
        f"<code>/sources</code> — list sources\n"
        f"<code>/addtarget -100xxxxxxxxxx</code> — add a target channel\n"
        f"<code>/targets</code> — list targets\n"
        f"<code>/forwardmode on|off</code> — start/stop live forwarding\n"
        f"<code>/forwardstatus</code> — check status\n\n"
        f"<code>/addreplacer old | new</code> · <code>/clearreplacer</code>\n"
        f"<code>/addremover word</code> · <code>/clearremover</code>\n"
        f"<code>/setprefix text</code> · <code>/setsuffix text</code> · <code>/clearcaption</code>\n\n"
        f"<b>Advanced:</b> <code>/numbering</code> · <code>/bullets</code> · <code>/deltamode</code> · "
        f"<code>/thetamode</code> · <code>/blastmode</code> · <code>/usernameremover</code> · "
        f"<code>/linkremover</code> · <code>/coursesellers</code> · <code>/textonlymode</code> · <code>/pimode</code>\n\n"
        f"<i>Requires a connected bot — /setbot first.</i></blockquote>",
        reply_markup=InlineKeyboardMarkup([[make_button("⬅️ Back", callback_data="akmanager_btn", style=_BS.DANGER if _BS else None)]]),
        parse_mode=enums.ParseMode.HTML,
    )
