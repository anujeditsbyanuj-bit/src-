import sys
import time
import datetime
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
)
from database.db import db
from config import ADMINS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_CLOCK  = '<emoji id=5386367538735104399>⌛</emoji>'
E_CROWN  = '<emoji id=5217822164362739968>👑</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'

BOT_START_TIME = time.time()


def _uptime() -> str:
    seconds = int(time.time() - BOT_START_TIME)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


# =========================================================
# /stats - Bot-wide usage overview (owner only)
# =========================================================

@Client.on_message(filters.command("stats") & filters.user(ADMINS))
async def bot_stats(client: Client, message: Message):
    start = time.time()
    total_users = await db.total_users_count()
    premium_cursor = await db.get_premium_users()
    premium_count = len(await premium_cursor.to_list(length=None))
    ping_ms = round((time.time() - start) * 1000)

    await message.reply_text(
        f"<blockquote>{E_INFO} <b>Bot Statistics</b>\n\n"
        f"🏓 <b>Ping:</b> <code>{ping_ms} ms</code>\n"
        f"👥 <b>Total Users:</b> <code>{total_users}</code>\n"
        f"{E_CROWN} <b>Premium Users:</b> <code>{premium_count}</code>\n"
        f"{E_CLOCK} <b>Uptime:</b> <code>{_uptime()}</code>\n\n"
        f"🐍 <b>Python:</b> <code>{sys.version.split()[0]}</code></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )


# =========================================================
# /check <user_id> - View any user's premium status (owner)
# =========================================================

@Client.on_message(filters.command("check") & filters.user(ADMINS))
async def check_user_premium(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/check &lt;user_id&gt;</code>", parse_mode=enums.ParseMode.HTML
        )
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await message.reply_text(f"<b>{E_CROSS} Invalid user ID.</b>", parse_mode=enums.ParseMode.HTML)

    expiry = await db.check_premium(user_id)
    if not expiry:
        return await message.reply_text(
            f"<b>{E_INFO} No active premium found for <code>{user_id}</code>.</b>", parse_mode=enums.ParseMode.HTML
        )

    if expiry is True or expiry is None:
        expiry_text, left_text = "Permanent", "Never expires"
    else:
        try:
            expiry_date = datetime.date.fromisoformat(str(expiry)) if isinstance(expiry, str) else expiry.date()
            days_left = (expiry_date - datetime.date.today()).days
            expiry_text = expiry_date.isoformat()
            left_text = f"{days_left} day(s)" if days_left >= 0 else "Expired"
        except Exception:
            expiry_text, left_text = str(expiry), "Unknown"

    await message.reply_text(
        f"<blockquote>{E_CROWN} <b>Premium Status</b>\n\n"
        f"⚡ <b>User ID:</b> <code>{user_id}</code>\n"
        f"{E_CLOCK} <b>Expires:</b> <code>{expiry_text}</code>\n"
        f"{E_INFO} <b>Time Left:</b> <code>{left_text}</code></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )


# =========================================================
# /freez - Bulk-remove expired premium users (owner)
# =========================================================

@Client.on_message(filters.command("freez") & filters.user(ADMINS))
async def freez_expired_premium(client: Client, message: Message):
    status = await message.reply_text(f"<b>{E_INFO} Scanning premium users...</b>", parse_mode=enums.ParseMode.HTML)
    cursor = await db.get_premium_users()
    users = await cursor.to_list(length=None)

    removed, kept = [], 0
    today = datetime.date.today()
    for user in users:
        expiry = user.get("premium_expiry")
        uid = user.get("id")
        if not expiry:
            kept += 1
            continue
        try:
            expiry_date = datetime.date.fromisoformat(str(expiry))
        except Exception:
            kept += 1
            continue
        if expiry_date < today:
            await db.remove_premium(uid)
            removed.append(str(uid))
        else:
            kept += 1

    removed_text = ", ".join(removed) if removed else "None"
    await safe_edit(status.edit_text, 
        f"<b>{E_CHECK} Cleanup Complete</b>\n\n"
        f"🧊 <b>Removed (expired):</b> <code>{len(removed)}</code>\n"
        f"➡️ <code>{removed_text}</code>\n\n"
        f"{E_CROWN} <b>Still Active:</b> <code>{kept}</code>",
        parse_mode=enums.ParseMode.HTML
    )


# =========================================================
# /mode - Toggle global Freemium / Paid mode (owner)
# =========================================================

@Client.on_message(filters.command("mode") & filters.user(ADMINS))
async def bot_mode_command(client: Client, message: Message):
    current = await db.get_bot_mode()
    text, buttons = _mode_view(current)
    await message.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


def _mode_view(current: str):
    free_mark = "✅" if current == "freemium" else "⭕"
    paid_mark = "✅" if current == "paid" else "⭕"
    text = (
        f"<blockquote>{E_GEAR} <b>Bot Mode</b>\n\n"
        f"<b>Current:</b> <code>{current.upper()}</code>\n\n"
        f"🆓 <b>Freemium</b> — everyone gets premium features free\n"
        f"{E_CROWN} <b>Paid</b> — only premium users get full access</blockquote>"
    )
    buttons = InlineKeyboardMarkup([[
        make_button(f"{free_mark} Freemium", callback_data="set_mode_freemium", style=_BS.PRIMARY if _BS else None),
        make_button(f"{paid_mark} Paid", callback_data="set_mode_paid", style=_BS.PRIMARY if _BS else None),
    ]])
    return text, buttons


@Client.on_callback_query(filters.regex(r"^set_mode_(freemium|paid)$"))
async def set_mode_callback(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("🚫 Not authorized.", show_alert=True)
    mode = callback_query.matches[0].group(1)
    await db.set_bot_mode(mode)
    text, buttons = _mode_view(mode)
    await safe_edit(callback_query.edit_message_text, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    await callback_query.answer(f"Switched to {mode.upper()} ✅")


# =========================================================
# /restart - Restart the bot process (owner)
# =========================================================

@Client.on_message(filters.command("restart") & filters.user(ADMINS))
async def restart_bot(client: Client, message: Message):
    import os
    await message.reply_text(f"<b>{E_ROCKET} Restarting...</b>", parse_mode=enums.ParseMode.HTML)
    os.execl(sys.executable, sys.executable, "-m", "bot")


# =========================================================
# /logs - Send the bot's current log file (owner only)
# =========================================================

@Client.on_message(filters.command("logs") & filters.user(ADMINS))
async def send_logs(client: Client, message: Message):
    import os
    log_path = "logs.txt"  # matches the RotatingFileHandler path in logger.py
    if not os.path.exists(log_path):
        return await message.reply_text(
            f"<b>{E_CROSS} No log file found yet.</b>", parse_mode=enums.ParseMode.HTML
        )
    try:
        await message.reply_document(
            document=log_path,
            caption=f"<b>{E_INFO} Current bot logs.</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(
            f"<b>{E_CROSS} Couldn't send logs:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )


# =========================================================
# /ytstatus - YouTube auth diagnostic: PO Token plugin + cookies (owner)
# =========================================================

@Client.on_message(filters.command(["ytstatus", "potoken", "cookies"]) & filters.user(ADMINS))
async def ytdl_auth_status(client: Client, message: Message):
    import os
    import subprocess
    from config import YT_COOKIES

    pot_installed = False
    try:
        result = subprocess.run(
            ["pip", "show", "bgutil-ytdlp-pot-provider"],
            capture_output=True, text=True, timeout=10
        )
        pot_installed = result.returncode == 0
    except Exception:
        pass

    lines = []
    if pot_installed:
        lines.append(f"{E_CHECK} <b>PO Token Plugin</b> — auto-generating tokens (primary)")
    else:
        lines.append(f"{E_CROSS} <b>PO Token Plugin</b> — not installed")

    if YT_COOKIES and os.path.exists(YT_COOKIES):
        lines.append(f"{E_CHECK} <b>Cookies file</b> — <code>{YT_COOKIES}</code>")
    else:
        lines.append(f"{E_WARN} <b>Cookies</b> — not configured (fallback)")

    status = "\n".join(lines)
    text = (
        f"<b>{E_ROCKET} YT-DLP Authentication Status</b>\n\n"
        f"{status}\n\n"
        f"<b>─── How It Works ───</b>\n"
        f"• <b>PO Token Plugin</b> (auto) — generates tokens in background, "
        f"no manual setup\n"
        f"• <b>Cookies</b> (manual fallback) — only needed if PO tokens fail\n\n"
        f"<b>If YouTube still fails:</b> upload a fresh <code>cookies.txt</code> "
        f"and set <code>YT_COOKIES</code> to its path.\n\n"
        f"📖 <a href=\"https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide\">PO Token Guide</a>"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


# =========================================================
# /set - Configure the "/" command menu shown in Telegram UI (owner)
# =========================================================

@Client.on_message(filters.command("set") & filters.user(ADMINS))
async def set_bot_commands(client: Client, message: Message):
    await client.set_bot_commands([
        BotCommand("start", "🚀 Start the bot"),
        BotCommand("help", "❓ How to use"),
        BotCommand("login", "🔑 Connect your account"),
        BotCommand("logout", "🚪 Disconnect account"),
        BotCommand("settings", "⚙️ Personalize things"),
        BotCommand("status", "📊 Your account overview"),
        BotCommand("myplan", "💎 Check your plan"),
        BotCommand("premium", "👑 Upgrade to premium"),
        BotCommand("pay", "⭐ Pay with Telegram Stars"),
        BotCommand("transfer", "💘 Gift premium to others"),
        BotCommand("referral", "🎁 Invite friends, earn rewards"),
        BotCommand("yt", "🎬 Download video (30+ sites)"),
        BotCommand("search", "🔍 Search YouTube by name"),
        BotCommand("yta", "🎵 Download audio (30+ sites)"),
        BotCommand("fb", "📘 Download Facebook video"),
        BotCommand("insta", "📸 Download Instagram video"),
        BotCommand("filepress", "🔓 Bypass FilePress/HubDrive link"),
        BotCommand("gdflix", "🔓 Bypass GDFlix link"),
        BotCommand("hubcloud", "🔓 Bypass HubCloud link"),
        BotCommand("speedtest", "🚀 Server speed test"),
        BotCommand("ping", "🏓 Check response speed"),
        BotCommand("cancel", "🚫 Cancel current task"),
        BotCommand("cancel_all", "🚫 Cancel all your active tasks"),
        BotCommand("queue", "📋 See your active tasks"),
        BotCommand("rss_add", "📡 Subscribe to an RSS feed"),
        BotCommand("rss_list", "📡 List your RSS feeds"),
        BotCommand("rss_remove", "📡 Unsubscribe from a feed"),
        BotCommand("rss_check", "📡 Check feeds right now"),
        BotCommand("cookies", "🍪 YT-DLP auth status"),
        BotCommand("setcookies", "📤 Upload cookies.txt"),
        BotCommand("clearcookies", "🗑️ Delete cookies file"),
        BotCommand("setgdrivetoken", "🔑 Upload GDrive OAuth token"),
        BotCommand("jd", "📦 Download via JDownloader"),
        BotCommand("jdstatus", "🔌 JDownloader connection status"),
        BotCommand("watch", "👀 Watch an anime site for new episodes"),
        BotCommand("unwatch", "🚫 Stop watching a site"),
        BotCommand("watchlist", "📺 List watched anime sites"),
        BotCommand("myanime", "📺 Add anime to your personal list"),
        BotCommand("myanimelist", "📋 View your personal anime list"),
        BotCommand("monitor", "🚀 Start monitoring your anime list"),
        BotCommand("stopmonitor", "🛑 Stop monitoring your anime list"),
        BotCommand("batch", "📦 Download episodes 1-N from a site"),
    ])
    await message.reply_text(f"<b>{E_CHECK} Command menu updated successfully.</b>", parse_mode=enums.ParseMode.HTML)


# =========================================================
# /admin_commands_list - Paginated admin reference (owner)
# =========================================================

ADMIN_COMMANDS = [
    ("/logs", "Send the current bot log file"),
    ("/queue all", "See every user's active tasks"),
    ("/cancel_all all", "Cancel every active task, for every user"),
    ("/stats", "View overall bot usage statistics"),
    ("/check <id>", "View a user's premium status"),
    ("/add_premium <id> <days>", "Add user to premium"),
    ("/remove_premium <id>", "Remove user from premium"),
    ("/freez", "Bulk-remove expired premium users"),
    ("/mode", "Toggle Freemium / Paid mode"),
    ("/broadcast", "Broadcast a message to all users"),
    ("/ban <id>", "Ban a user"),
    ("/unban <id>", "Unban a user"),
    ("/eval", "Run Python code (debug console)"),
    ("/shell", "Run a host shell command"),
    ("/restart", "Restart the bot"),
    ("/set", "Refresh the bot's command menu"),
    ("/cookies", "Check YT-DLP PO token + cookies auth status"),
    ("/setcookies <domain>", "Upload cookies.txt for a domain"),
    ("/clearcookies <domain>", "Delete cookies for a domain"),
    ("/setgdrivetoken", "Upload GDrive OAuth token.pickle (folder support)"),
    ("/set_anime_channel <id>", "Auto-post new SubsPlease episodes to a channel"),
    ("/del_anime_channel", "Turn off the anime auto-poster"),
    ("/anime_channel_status", "Check the configured anime auto-post channel"),
    ("/jdstatus", "Check if JDownloader is connected"),
    ("/deps", "Check optional dependency install status (tap to install)"),
    ("/install <key|all>", "Install a missing optional pip dependency"),
]
PER_PAGE = 6


def _build_admin_page(page: int):
    total_pages = (len(ADMIN_COMMANDS) + PER_PAGE - 1) // PER_PAGE
    page = max(1, min(page, total_pages))
    start = (page - 1) * PER_PAGE
    chunk = ADMIN_COMMANDS[start:start + PER_PAGE]

    lines = [f"<b>{E_GEAR} Admin Commands (Page {page}/{total_pages})</b>\n"]
    for cmd, desc in chunk:
        lines.append(f"<code>{cmd}</code> — {desc}")
    text = "\n".join(lines)

    nav = []
    if page > 1:
        nav.append(make_button("⬅️ Prev", callback_data=f"admincmds:{page-1}", style=_BS.PRIMARY if _BS else None))
    if page < total_pages:
        nav.append(make_button("➡️ Next", callback_data=f"admincmds:{page+1}", style=_BS.SUCCESS if _BS else None))
    buttons = InlineKeyboardMarkup([nav]) if nav else None
    return text, buttons


@Client.on_message(filters.command("admin_commands_list") & filters.user(ADMINS))
async def admin_commands_list(client: Client, message: Message):
    text, buttons = _build_admin_page(1)
    await message.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^admincmds:(\d+)$"))
async def admin_commands_page_callback(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("🚫 Not authorized.", show_alert=True)
    page = int(callback_query.matches[0].group(1))
    text, buttons = _build_admin_page(page)
    await safe_edit(callback_query.edit_message_text, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    await callback_query.answer()
