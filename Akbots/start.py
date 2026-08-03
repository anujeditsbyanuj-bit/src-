# =========================================================
# Developed by: LastPerson07 × AkBots
# Telegram: @AkBots_Official | @THEUPDATEDGUYS
# =========================================================
import os
import re
import asyncio
import random
import time
import shutil
import aiohttp

import pyrogram
from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait, UserIsBlocked, InputUserDeactivated, UserAlreadyParticipant,
    InviteHashExpired, UsernameNotOccupied, AuthKeyUnregistered,
    UserDeactivated, UserDeactivatedBan
)
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message, CallbackQuery, InputMediaPhoto
)

try:
    from pyrogram.enums import ButtonStyle
    BUTTON_STYLE_SUPPORTED = True
except ImportError:
    BUTTON_STYLE_SUPPORTED = False

from Akbots.direct_utils import format_forward_caption, safe_edit
from Akbots.settings import get_settings_buttons
from Akbots.caption import render_caption

# =========================================================
# Custom Premium Emojis (Telegram Animated Sticker IDs)
# =========================================================

E_WARN    = '<emoji id=5420323339723881652>⚠️</emoji>'
E_INFO    = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_CROWN   = '<emoji id=5217822164362739968>👑</emoji>'
E_SPARK   = '<emoji id=5325547803936572038>✨</emoji>'
E_CHECK   = '<emoji id=5039844895779455925>✔️</emoji>'
E_BOLT    = '<emoji id=5456140674028019486>⚡️</emoji>'
E_GEAR    = '<emoji id=5341715473882955310>⚙️</emoji>'
E_STAR    = '<emoji id=5438496463044752972>⭐️</emoji>'
E_STOP    = '<emoji id=5260293700088511294>⛔️</emoji>'
E_GREEN   = '<emoji id=5416081784641168838>🟢</emoji>'
E_RED     = '<emoji id=5411225014148014586>🔴</emoji>'
E_LINK    = '<emoji id=5271604874419647061>🔗</emoji>'
E_BATCH   = '<emoji id=5341498088408234504>💯</emoji>'
E_PENCIL  = '<emoji id=5395444784611480792>✏️</emoji>'
E_TIP     = '<emoji id=5422439311196834318>💡</emoji>'
E_IMAGE   = '<emoji id=5395444784611480792>🖼</emoji>'
E_STATS   = '<emoji id=5042290883949495533>📊</emoji>'
E_PAYMENT = '<emoji id=5325547803936572038>💳</emoji>'
E_CHANNEL = '<emoji id=5271604874419647061>📢</emoji>'
E_DEV     = '<emoji id=5217822164362739968>👨‍💻</emoji>'
E_CROSS   = '<emoji id=5042112436648281096>❌</emoji>'
E_LOCK    = '<emoji id=5296369303661067030>🔒</emoji>'
E_DIAMOND = '<emoji id=5427168083074628963>💎</emoji>'
E_ROCKET  = '<emoji id=5456140674028019486>🚀</emoji>'
E_SHIELD  = '<emoji id=5251203410396458957>🛡</emoji>'
E_CLOCK   = '<emoji id=5386367538735104399>⌛</emoji>'
E_ARROW   = '<emoji id=5416117059207572332>➡️</emoji>'

# =========================================================
# Custom Telegram Button Emoji IDs
# =========================================================

ICON_INFO      = 5334544901428229844
ICON_WARN      = 5447644880824181073
ICON_HELP      = 5443038326535759644
ICON_DEV       = 5823268688874179761
ICON_BACK      = 5447183459602669338
ICON_GEAR      = 5341715473882955310
ICON_PENCIL    = 5395444784611480792
ICON_TRASH     = 5260293700088511294
ICON_REFRESH   = 5375338737028841420
ICON_PREMIUM   = 5217822164362739968
ICON_PAYMENT   = 5325547803936572038
ICON_IMAGE     = 5395444784611480792
ICON_STATS     = 5334544901428229844
ICON_CHANNEL   = 5271604874419647061
ICON_CLOSE     = 5210952531676504517
ICON_HOME      = 5447183459602669338
ICON_LIST      = 5334544901428229844
ICON_DELETE    = 5260293700088511294
ICON_EDIT      = 5395444784611480792
ICON_PLAN      = 5217822164362739968
ICON_WARNING   = 5447644880824181073
ICON_CANCEL    = 5210952531676504517
ICON_CHECK     = 5206607081334906820

from config import API_ID, API_HASH, ERROR_MESSAGE, MAX_BATCH_IDS_FREE, MAX_BATCH_IDS_PREMIUM, TOKEN_BATCH_BONUS
from database.db import db
import math
from logger import LOGGER
logger = LOGGER(__name__)

# =========================================================

SUBSCRIPTION     = os.environ.get('SUBSCRIPTION', 'https://l.arzfun.com/oxGhB')
FREE_LIMIT_SIZE  = 2 * 1024 * 1024 * 1024
FREE_LIMIT_DAILY = 10
UPI_ID  = os.environ.get("UPI_ID", "971916880@ybl")
QR_CODE = os.environ.get("QR_CODE", "https://l.arzfun.com/oxGhB")

REACTIONS = [
    # ── Telegram Official Reactions ──────────────────────
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔",
    "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩",
    "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳",
    "❤️‍🔥", "🌚", "🌭", "💯", "🤣", "⚡", "🍌", "🏆",
    "💔", "🤨", "😐", "🍓", "🍾", "💋", "😈", "😴",
    "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈", "😇",
    "😨", "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃",
    "💅", "🤪", "🗿", "🆒", "💘", "🙉", "🦄", "😘",
    "💊", "🙊", "😎", "👾", "🤷‍♂️", "🤷‍♀️", "😡",
    # ── Premium / Money / Diamond vibes ──────────────────
    "💎", "👑", "💰", "🪙", "💵", "💴", "💶", "💷",
    "💸", "💳", "🏦", "🤑", "💹", "📈", "🏅", "🥇",
    "🎖", "⚜️", "🔱", "♾️",
    # ── Fire / Energy / Power ────────────────────────────
    "🌟", "✨", "💫", "🌠", "☄️", "💥", "⭐", "🌙",
    "🌈", "🪄", "🎯", "🛡", "🚀", "⚔️", "🗡", "🔥",
    # ── Cute / Fun ───────────────────────────────────────
    "🥹", "🫶", "🫠", "🫣", "🥺", "🤭", "🫢", "🤌",
    "🤙", "🤞", "🫰", "🤟", "🫵", "✌️", "🤘",
    # ── Hearts ───────────────────────────────────────────
    "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "🤎",
    "💝", "💖", "💗", "💓", "💞", "💕", "💟", "❣️",
    # ── Animals ──────────────────────────────────────────
    "🐉", "🦋", "🦊", "🐺", "🦁", "🐯", "🦅", "🦉",
    "🐬", "🦈", "🐲",
]

dev_text = "👨‍💻 Mind Behind This Bot:\n• @anujedits76\n• @anujedits76"
channels_text = "📢 Official Channels:\n• @anujedits76\n• @anujedits76\n\nStay updated for new features!"

FEATURES_TXT = (
    f"<b>{E_ROCKET} Bot Features ✨</b>\n\n"
    f"<blockquote expandable><b>📥 ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴄᴏɴᴛᴇɴᴛ sᴀᴠɪɴɢ</b>\n"
    f"Save text/media/files from restricted channels • Batch mode • /login with your own account</blockquote>\n\n"
    f"<blockquote expandable><b>🌐 ᴜɴɪᴠᴇʀsᴀʟ ᴍᴇᴅɪᴀ ᴅᴏᴡɴʟᴏᴀᴅᴇʀ</b>\n"
    f"YouTube, Instagram, Facebook, VK, Dailymotion, MX Player, Zee5, Voot, JioCinema, M3U8 playlists • Per-site cookies • Headless-browser fallback for JS-heavy pages</blockquote>\n\n"
    f"<blockquote expandable><b>☁️ ғɪʟᴇ ʜᴏsᴛs &ᴀᴍᴘ; ʙʏᴘᴀssᴇʀs</b>\n"
    f"Mega.nz, MediaFire, Terabox, Google Drive (OAuth), GDFlix, HubCloud, FilePress, HubDrive, Catbox, Gofile, Pixeldrain, Streamtape, Fembed • Torrent/Magnet + FTP via aria2 • JDownloader integration</blockquote>\n\n"
    f"<blockquote expandable><b>🎵 ᴍᴜsɪᴄ &ᴀᴍᴘ; 📺 ᴀɴɪᴍᴇ</b>\n"
    f"JioSaavn (tagged MP3), Spotify • SubsPlease anime search + auto-poster • KukuTV/KukuFM audio-drama shows (/kuku)</blockquote>\n\n"
    f"<blockquote expandable><b>🗜️ ᴀʀᴄʜɪᴠᴇ &ᴀᴍᴘ; ɢᴀʟʟᴇʀʏ</b>\n"
    f"/zip with password protection — AES-256/AES-128/ZipCrypto via /zipencryption (zip/7z/tar/tar.gz/tar.bz2/rar via /zipformat), folder structure via /zipfolder or file captions, /unzip any format • Twitter/Reddit/Pinterest gallery downloads</blockquote>\n\n"
    f"<blockquote expandable><b>🎬 ᴍᴇᴅɪᴀ ᴇᴅɪᴛɪɴɢ</b>\n"
    f"Format conversion, audio extraction, watermark, metadata, screenshots, sample clips, video merge (/merge), trim (/trim), compress (/compress), spoiler tags, custom thumbnails/captions, auto-rename engine</blockquote>\n\n"
    f"<blockquote expandable><b>🔁 ғᴏʀᴡᴀʀᴅɪɴɢ &ᴀᴍᴘ; ᴀᴜᴛᴏᴍᴀᴛɪᴏɴ</b>\n"
    f"Forward engine with resume/status, FTM live forwarding, channel routes, duplicate cleanup, RSS auto-download, TMDB AutoPost, Auto-Backup</blockquote>\n\n"
    f"<blockquote expandable><b>💎 ᴘʀᴇᴍɪᴜᴍ &ᴀᴍᴘ; ᴀᴅᴍɪɴ</b>\n"
    f"Free/premium tiers, token redemption, referral &amp; transfer, broadcast, ban/unban, per-user stats, Titanium Clone Mode</blockquote>\n\n"
    f"💡 <i>Tap 📜 All Commands below for the full A–Z command list.</i>"
)

# Full A-Z sorted command reference, kept in sync with bot.py's BotCommand
# list — used only for the "📜 All Commands" menu button (paginated, since
# 100+ commands is far past Telegram's single-message length limit).
ALL_COMMANDS = [
    ('aadmin', '👮 Anime-watcher admin panel'),
    ('about', 'ℹ️ About this bot'),
    ('acast', '📢 Broadcast to a specific admin-selected audience'),
    ('add_channel_id', '📡 Link a custom channel/group for files'),
    ('add_premium', '👑 Add premium to user (admin only)'),
    ('add_unsubscribe', '🔕 Add an unsubscribe/opt-out entry (admin)'),
    ('addbot', '⚡ Connect a bot token to Titanium'),
    ('adddbchannel', '➕ Add a DB storage channel (admin)'),
    ('addremover', '➖ Add a caption text-remover rule (forward)'),
    ('addreplacer', '🔄 Add a caption find/replace rule (forward)'),
    ('addroute', '➕ Add a channel route'),
    ('addsource', '➕ Add forward source channel'),
    ('addtarget', '➕ Add forward target channel'),
    ('adl', '⬇️ Alias for /dl — download via yt-dlp'),
    ('admin_commands_list', '📋 List all admin-only commands'),
    ('akanager', '🚀 Manager control panel'),
    ('akstats', '📊 Shortlink-bypass usage stats (admin)'),
    ('allcancel', '🚫 Cancel all running m3u8 downloads'),
    ('anime', '📺 Search & download anime episodes (SubsPlease)'),
    ('anime_channel_status', '📡 Show linked anime auto-post channel status'),
    ('antibot', '🤖 Toggle anti-bot/anti-spam protection (admin)'),
    ('apply_metadata', '🏷️ Apply metadata (reply to file)'),
    ('apply_watermark', '💧 Apply watermark (reply to video)'),
    ('audio_defaults', '⚙️ View/set your Audio Selector default filename'),
    ('audio_select', '🎵 Choose which audio track(s) to keep (reply to video)'),
    ('audiocancel', '🚫 Cancel your active/queued Audio Selector job'),
    ('audioqueue', '📚 View your Audio Selector queue & daily usage'),
    ('audioselect', '🎵 Alias for /audio_select'),
    ('autobatch', '📦 Toggle/configure auto-batch (admin)'),
    ('autogenerate', '🎛️ Toggle auto-generate missing qualities (admin)'),
    ('autorename', '📝 Set auto-rename template'),
    ('autosample', '🎞️ Auto-send a sample clip before every video'),
    ('autoscreenshots', '🖼️ Auto-send screenshots after every video upload'),
    ('autotest', '🧪 Test-run the auto-post pipeline once (admin)'),
    ('backupdb', '💾 Back up the database (admin)'),
    ('ban', '🔨 Ban a user'),
    ('batch', '💯 Generate a batch share link (admin)'),
    ('blastmode', '📣 Toggle forward blast mode (send to all targets at once)'),
    ('broadcast', '📢 Broadcast message (admin only)'),
    ('bullets', '• Toggle bullet-point formatting on forwarded captions'),
    ('bypass', '🔓 Bypass a shortlink URL'),
    ('bypassstats', '📊 Shortlink-bypass usage stats (admin)'),
    ('cancel', '🚫 Cancel current action'),
    ('cancel_all', '🚫 Cancel all of your running tasks'),
    ('cancellogin', '🚫 Cancel an in-progress /login'),
    ('catbox', '📤 Upload a file to catbox.moe'),
    ('cfcheck', '🛡️ Check a URL for Cloudflare protection (admin)'),
    ('cfdetect', '🛡️ Detect Cloudflare challenge type on a URL (admin)'),
    ('change_thumb', '🔄 Change thumbnail of a video/document'),
    ('channel_id', '📋 List your linked channels/groups'),
    ('channel_status', '📡 Show your linked channel/group status'),
    ('channels', '📡 List connected channels'),
    ('check', '🔎 Quick diagnostic check (admin)'),
    ('checkdeps', '🧰 Check optional dependency status (admin, see /deps)'),
    ('cleanup', '🧹 Clean up stale anime-watcher entries (admin)'),
    ('clearcaption', '🗑️ Clear the forward caption template'),
    ('clearcookies', '🍪 Clear all stored cookies (admin only)'),
    ('clearremover', '🗑️ Clear all caption text-remover rules'),
    ('clearreplacer', '🗑️ Clear all caption find/replace rules'),
    ('cmd', '📋 Show bot command reference (raw)'),
    ('commands', '📋 List available settings commands'),
    ('compress', '🗜️ Compress a video — Fast/Balanced/HEVC/Max presets (reply to video)'),
    ('cookie', '🍪 View/manage cookies (alias, see /setcookies)'),
    ('cookiecheck', '🍪 Validate a stored cookie file'),
    ('cookies', '🍪 List/manage cookies (admin)'),
    ('coursesellers', '🎓 One-click forward preset: strip links/deeplinks/usernames'),
    ('dailymotion', '🎬 Download a Dailymotion video'),
    ('dbchannels', '📡 List multi-DB storage channels (admin)'),
    ('dblink', '🔗 Get a direct DB-channel file link (admin)'),
    ('del_anime_channel', '🗑️ Remove the anime auto-post channel'),
    ('del_autorename', '🗑️ Delete auto-rename template'),
    ('del_caption', '❌ Delete caption'),
    ('del_channel_id', '🗑 Unlink a custom channel/group'),
    ('del_metadata', '🗑️ Delete metadata template'),
    ('del_mydb', '🗑️ Remove your linked personal DB channel'),
    ('del_prefix', '➖ Remove filename prefix'),
    ('del_suffix', '➖ Remove filename suffix'),
    ('del_thumb', '🗑️ Delete thumbnail'),
    ('del_unsubscribe', '➖ Remove an unsubscribe/opt-out entry (admin)'),
    ('del_watermark', '🗑️ Delete watermark text/settings'),
    ('delbot', '⚡ Disconnect a Titanium bot'),
    ('delbutton', '🗑️ Remove the forward button'),
    ('delcookies', '🍪 Delete cookies for a domain (admin only)'),
    ('deldbchannel', '➖ Remove a DB storage channel (admin)'),
    ('delete_thumb', '🗑️ Delete thumbnail (alias of /del_thumb)'),
    ('delroute', '➖ Remove a channel route'),
    ('deltamode', '🔺 Toggle forward delta mode'),
    ('deps', '🧰 Dependency status — tap-to-install missing pip/apt deps (admin)'),
    ('direct', '🔗 Upload a file from a direct URL'),
    ('dl', '⬇️ Download a video/audio via yt-dlp'),
    ('domains', '🌐 List supported shortlink/bypass domains'),
    ('done_merge', '✅ Alias for /mergedone — finish the merge session'),
    ('donezip', '✅ Build and send the zip'),
    ('dub', '🗣️ Dub a video into another voice (male1/male2/female)'),
    ('dubpdf', '📄 Generate a dub job report as PDF'),
    ('encode', '🎛️ Re-encode a video — resolution/codec/quality (reply to video)'),
    ('encode_settings', '⚙️ View/change persistent /encode defaults'),
    ('eval', '🐍 Execute Python code (owner only)'),
    ('extract_audio', '🎵 Extract audio as MP3 (reply to video)'),
    ('fb', '📘 Download a Facebook video (alias of /facebook)'),
    ('fembed', '🎬 Resolve a Fembed-family mirror link'),
    ('filepress', '🔗 Resolve a FilePress link'),
    ('filmyzilla', '🎬 Search & download from FilmyZilla'),
    ('forward', '➡️ Forward-engine settings menu'),
    ('forwardmode', '🔁 Toggle live forwarding on/off'),
    ('forwardstatus', '📊 Forward Engine status'),
    ('freez', '🧊 Freeze/pause the bot temporarily (admin)'),
    ('ftp', '📁 Upload a file over FTP'),
    ('fwd', '➡️ Forward a message id range'),
    ('fwd_button', '🔘 Set forward button'),
    ('fwd_caption', '✏️ Set forward caption'),
    ('fwd_extblock', '🚫 Block specific file extensions from forwarding'),
    ('fwd_filter', '🧰 Set forward media type filters'),
    ('fwd_keyword', '🔑 Set forward keyword allow/block list'),
    ('fwd_login', '🔐 Login your account for forwarding'),
    ('fwd_settings', '⚙️ View/manage all forward settings'),
    ('fwdcancel', '🚫 Stop running forward job'),
    ('fwdresume', '⏯️ Resume last forward job'),
    ('fwdstatus', '📊 Show forward job status'),
    ('gallery', '🖼️ Download a gallery (gallery-dl)'),
    ('gdflix', '🔗 Resolve a GDFlix link'),
    ('gdrive', '☁️ Google Drive OAuth — access private files/folders'),
    ('genlink', '🔗 Generate a file share link (admin)'),
    ('get_channel_id', '🆔 Get a linked channel/group\'s ID'),
    ('getemoji', '🆔 Get a custom/premium emoji\'s ID (reply to it)'),
    ('getid', '🆔 Get this chat\'s ID (and a replied user\'s ID)'),
    ('gofile', '📤 Upload a file to gofile.io'),
    ('hdhub', '🎬 HDHub — search & download'),
    ('hdhub_forcepost', '📢 Force an immediate HDHub auto-post (admin)'),
    ('hdhub_posted', '📋 List already auto-posted HDHub titles (admin)'),
    ('hdhub_setchannel', '📡 Set the HDHub auto-post target channel (admin)'),
    ('hdhub_settimer', '⏱️ Set the HDHub auto-post interval (admin)'),
    ('hdhub_startauto', '▶️ Start HDHub auto-posting (admin)'),
    ('hdhub_stats', '📊 HDHub auto-post stats (admin)'),
    ('hdhub_status', '📊 HDHub auto-post status (admin)'),
    ('hdhub_stopauto', '⏹️ Stop HDHub auto-posting (admin)'),
    ('help', '❓ Show help'),
    ('hubcloud', '🔗 Resolve a HubCloud link'),
    ('hubdrive', '🔗 Resolve a HubDrive link'),
    ('ig', '📸 Download an Instagram post/reel (alias of /insta)'),
    ('imdb', '🎬 Movie/TV info from IMDb'),
    ('imgtolink', '🖼 Upload an image and get a permanent direct link'),
    ('imgurl', '🔗 Upload an image URL and get a direct link'),
    ('info', 'ℹ️ Show a user\'s info (alias of /userinfo)'),
    ('insta', '📸 Download an Instagram post/reel'),
    ('install', '⬇️ Install one/all missing pip or system dependencies (admin)'),
    ('jd', '📥 JDownloader control panel'),
    ('jdstatus', '📊 JDownloader connection/status'),
    ('jiocinema', '🎬 Download JioCinema movies'),
    ('jiosaavn', '🎵 Download a JioSaavn song'),
    ('kuku', '🎬 KukuTV/KukuFM — search, browse popular, or paste a link'),
    ('kukucancel', '🚫 Stop a running KukuTV download'),
    ('link', '🔗 Reply to a file for stream + download links'),
    ('linkbatch', '🔗 Generate stream + download links for a batch of files'),
    ('linkremover', '➖ Remove links from forwarded captions'),
    ('listcookies', '🍪 List domains with custom cookies (admin only)'),
    ('lksfy', '🔗 Resolve a Linksfy shortlink'),
    ('login', '🔐 Login'),
    ('logout', '🚪 Logout'),
    ('logs', '📄 Fetch the bot\'s log file (admin)'),
    ('m3u8', '🎞️ Download an m3u8/HLS stream'),
    ('maintenance', '🛠️ Toggle maintenance mode (admin)'),
    ('mediainfo', '📋 MediaInfo technical details (reply to file)'),
    ('mega', '☁️ Download from a Mega.nz link'),
    ('merge', '🎬 Start a video merge session'),
    ('mergecancel', '🚫 Cancel current merge session'),
    ('mergedone', '✅ Merge queued videos & send result'),
    ('mergestatus', '📋 Show videos queued in merge session'),
    ('mf', '☁️ Download from a Mediafire link (alias)'),
    ('minfo', '📋 MediaInfo technical details (alias of /mediainfo)'),
    ('mode', '⚙️ Switch bot operating mode (admin)'),
    ('monitor', '👁️ Monitor an anime-watch source (admin)'),
    ('movieinfo', '🎬 Movie info (admin, needs TMDB key)'),
    ('multidb', '🔀 Toggle multi-DB round robin (admin)'),
    ('mxplayer', '🎬 Resolve an MX Player link'),
    ('myanime', '📺 List anime titles you\'re watching'),
    ('myanimedel', '🗑️ Remove an anime title from your watchlist'),
    ('myanimelist', '📺 View your anime watchlist'),
    ('mydb', '💾 View your linked personal DB channel'),
    ('myplan', '📋 Check your plan'),
    ('myuses', '📊 My today\'s usage'),
    ('numbering', '🔢 Toggle auto-numbering on forwarded captions'),
    ('pay', '💳 Buy premium'),
    ('pd', '☁️ Download from Pixeldrain (alias)'),
    ('pimode', '🔢 Toggle Pi Mode — select multiple forward targets at once'),
    ('ping', '🏓 Check bot latency'),
    ('pixeldrain', '☁️ Download from a Pixeldrain link'),
    ('plan', '📋 Check your plan (alias of /myplan)'),
    ('pocketfm', '🎧 Download a PocketFM episode'),
    ('poster', '🖼️ Movie poster (admin, needs TMDB key)'),
    ('potoken', '🔑 Manage YouTube PO-token provider (admin)'),
    ('premium', '⭐ Premium info'),
    ('queue', '📋 Show your active task queue'),
    ('radmin', '👮 Anime-watcher admin panel (alias of /aadmin)'),
    ('referral', '🤝 Your referral link & stats'),
    ('referral_list', '🤝 List your referred users'),
    ('rem_del_word', '➖ Remove delete word'),
    ('rem_repl_word', '🔃 Remove replace word'),
    ('rembot', '⚡ Remove a connected multi-bot'),
    ('remove_premium', '💔 Remove premium from user (admin only)'),
    ('reset', '♻️ Reset all forward settings'),
    ('restart', '🔄 Restart the bot process (admin)'),
    ('rmsource', '➖ Remove a forward source channel'),
    ('rmtarget', '➖ Remove a forward target channel'),
    ('rss_add', '➕ Add an RSS feed to watch'),
    ('rss_check', '🔎 Force-check an RSS feed now'),
    ('rss_list', '📋 List watched RSS feeds'),
    ('rss_remove', '➖ Remove a watched RSS feed'),
    ('saavn', '🎵 Download a JioSaavn song (alias of /jiosaavn)'),
    ('sample', '🎞️ Generate a short sample clip (reply to video)'),
    ('screenshots', '🖼️ Generate N screenshots (reply to video)'),
    ('search', '🔎 Search YouTube'),
    ('see_autorename', '🔎 View auto-rename template'),
    ('see_caption', '📄 View caption'),
    ('see_metadata', '👁️ View metadata template'),
    ('see_prefix', '👁️ View filename prefix'),
    ('see_suffix', '👁️ View filename suffix'),
    ('see_thumb', '👁️ View thumbnail (alias of /view_thumb)'),
    ('see_watermark', '👁️ View watermark text'),
    ('series', '📺 TV series info from IMDb'),
    ('set', '⚙️ Generic admin settings entry point'),
    ('set_anime_channel', '📡 Set the anime auto-post channel'),
    ('set_caption', '✏️ Set caption'),
    ('set_channel', '📡 Link a custom channel/group (alias of /set_channel_id)'),
    ('set_channel_id', '📡 Link a custom channel/group for files'),
    ('set_del_word', '➕ Add delete word'),
    ('set_dump', '📡 Set the dump/log channel (admin)'),
    ('set_expiry', '⏳ Set premium/token expiry (admin)'),
    ('set_metadata', '🏷️ Set metadata text'),
    ('set_mydb', '💾 Link your personal DB channel'),
    ('set_prefix', '➕ Set filename prefix'),
    ('set_repl_word', '🔄 Add replace word'),
    ('set_suffix', '➕ Set filename suffix'),
    ('set_thumb', '🖼️ Set thumbnail'),
    ('set_watermark', '💧 Set watermark text'),
    ('setbot', '⚡ Connect a bot token for multi-bot mode'),
    ('setbutton', '🔘 Set a custom forward button'),
    ('setchat', '💬 Set target chat'),
    ('setcookies', '🍪 Set cookies for a domain (admin only)'),
    ('setfilters', '🧰 Set forward message-type allow-list'),
    ('setgdrivetoken', '🔑 Set your Google Drive OAuth token'),
    ('setprefix', '➕ Set forward caption prefix'),
    ('setsource', '➕ Set the forward source channel'),
    ('setsuffix', '➕ Set forward caption suffix'),
    ('settarget', '➕ Set the forward target channel'),
    ('settings', '⚙️ Open the bot settings menu'),
    ('sh', '💻 Run a shell command (owner only)'),
    ('shell', '💻 Run a shell command (owner only)'),
    ('shortbypass', '🔓 Bypass a shortlink URL (alias of /bypass)'),
    ('shortener', '🔗 Toggle the URL shortener gate (admin)'),
    ('sites', '🌐 List supported shortlink/bypass domains (alias of /domains)'),
    ('song', '🎵 Download a song via yt-dlp'),
    ('sources', '📋 List forward source channels'),
    ('speed', '⚡ Run a network speed test (alias of /speedtest)'),
    ('speedtest', '⚡ Run a network speed test'),
    ('spoiler', '🙈 Toggle spoiler blur / blur one file (reply)'),
    ('spotify', '🎵 Download a Spotify track'),
    ('stape', '🎬 Resolve a Streamtape link'),
    ('start', '🚀 Start the bot'),
    ('stats', '📊 Bot-wide usage stats (admin)'),
    ('status', '📊 Bot status'),
    ('stop', '⏹️ Stop the bot\'s current action'),
    ('stopmonitor', '⏹️ Stop monitoring an anime-watch source (admin)'),
    ('storage', '💾 Disk, cache & video storage stats (alias of /storageinfo)'),
    ('storageinfo', '💾 Disk, cache & video storage stats (alias: /storage)'),
    ('subsplease', '📺 Search SubsPlease anime releases'),
    ('supported', '🌐 List supported shortlink/bypass domains (alias)'),
    ('targets', '📋 List forward target channels'),
    ('terabox', '☁️ Download from a TeraBox link'),
    ('terastream', '🎞️ Get the raw HLS (.m3u8) link for a TeraBox video'),
    ('terms', '📜 Show Terms & Conditions'),
    ('textonlymode', '📝 Toggle forwarding text-only messages'),
    ('thetamode', '🔺 Toggle forward theta mode'),
    ('thumb_mode', '🖼️ Toggle auto-thumbnail mode'),
    ('tiktok', '🎬 Download a TikTok video'),
    ('titanium', '⚡ Titanium Clone Mode — connect your bots'),
    ('todocument', '📄 Resend a video as a plain document'),
    ('token', '🔑 Redeem a token'),
    ('tomp4', '🔁 Convert mkv/avi/flv/webm/wmv to real .mp4'),
    ('torrent', '🧲 Download from a torrent/magnet link'),
    ('totaluses', '📊 Show bot-wide total usage (admin)'),
    ('tovideo', '🎬 Resend a document-video as a playable video'),
    ('transfer', '🔁 Transfer premium to another user'),
    ('trim', '✂️ Trim a video to a start/end time (reply to video)'),
    ('tt', '🎬 Download a TikTok video (alias of /tiktok)'),
    ('unban', '✅ Unban a user'),
    ('unequify', '🧹 Delete duplicate messages in a chat'),
    ('unequifycancel', '🚫 Stop a running unequify scan'),
    ('unwatch', '⏹️ Stop watching an anime title'),
    ('unzip', '📦 Extract an archive (reply to file)'),
    ('upload_mode', '📤 Toggle auto-link-on-upload (alias of /uploadmode)'),
    ('uploadmode', '📤 Toggle auto-link-on-upload (admin)'),
    ('url', '🔗 Upload a file from a direct URL (alias of /direct)'),
    ('userinfo', '👤 Show a user\'s info'),
    ('usernameremover', '➖ Strip @usernames from forwarded captions'),
    ('users', '👥 List/broadcast target user set (admin)'),
    ('useruses', '📊 Show a specific user\'s usage (admin)'),
    ('vc', '🎤 Generate a P2P voice chat room link (alias /gen)'),
    ('vchelp', '📖 How to use Voice Chat'),
    ('view_thumb', '👁️ View thumbnail'),
    ('vk', '🎬 Download a VK video'),
    ('voot', '🎬 Download from Voot'),
    ('wadmins', '👮 List anime-watcher admins'),
    ('warning', '⚠️ Show the safety warning text'),
    ('watch', '📺 Start watching an anime title for new episodes'),
    ('watchlist', '📺 View your anime watchlist (alias of /myanimelist)'),
    ('watchtime', '⏱️ Show anime-watcher scan interval'),
    ('watermark_position', '💧 Set watermark position'),
    ('wthumb', '🖼️ Set a per-anime-title thumbnail'),
    ('wthumbdel', '🗑️ Delete a per-anime-title thumbnail'),
    ('wthumblist', '📋 List per-anime-title thumbnails'),
    ('yt', '▶️ Download a YouTube video'),
    ('yta', '🎵 Download YouTube audio (mp3)'),
    ('ytfallback', '🧪 Force the YouTube fallback downloader (testing)'),
    ('ytraw', '🧪 Force the YouTube fallback downloader, raw mode (testing)'),
    ('yts', '🔎 Search YouTube (alias of /search)'),
    ('ytstatus', '📊 YouTube downloader/PO-token status (admin)'),
    ('zee5', '🎬 Download from Zee5'),
    ('zee5cancel', '🚫 Cancel a running Zee5 download'),
    ('zee5series', '📺 Download a Zee5 series'),
    ('zip', '🗜️ Start a zip session'),
    ('zipcancel', '🚫 Cancel current zip session'),
    ('zipcreate', '🗜️ Instantly zip a replied video/photo/file (no session)'),
    ('zipencryption', '🔐 Set ZIP encryption mode — aes256/aes128/zipcrypto'),
    ('zipfiles', '📋 View files queued in your zip session'),
    ('zipfolder', '📁 Organize queued files into folders inside the archive'),
    ('zipformat', '🗜️ Pick archive format — zip/7z/tar/tar.gz/tar.bz2/rar'),
    ('zipname', '✏️ Set zip archive name'),
    ('zippass', '🔒 Password-protect the zip/7z (AES-256 by default)'),
]
CMDS_PER_PAGE = 20

WARNING_TEXT = (
    f"<blockquote>{E_WARN} <b>ɪᴍᴘᴏʀᴛᴀɴᴛ ᴡᴀʀɴɪɴɢ</b>\n\n"
    f"{E_SHIELD} <b>ᴘʟᴇᴀsᴇ ʀᴇᴀᴅ ʙᴇғᴏʀᴇ ᴜsɪɴɢ:</b>\n\n"
    f"{E_LOCK} This bot saves restricted/private content using your Telegram session.\n"
    f"{E_STOP} Never share your session string with anyone.\n"
    f"{E_WARN} Use at your own risk — misuse may lead to account restrictions.\n"
    f"{E_INFO} Bot is for personal use only, not for mass downloading.\n"
    f"{E_CROSS} Developer is not responsible for any account ban or data loss.</blockquote>\n\n"
    f"<i>{E_CHECK} By using this bot, you agree to the above terms.</i>"
)

TERMS_TXT = (
    f"<blockquote>{E_INFO} <b>ᴛᴇʀᴍs & ᴄᴏɴᴅɪᴛɪᴏɴs</b>\n\n"
    f"1. {E_CHECK} This bot is provided as-is, with no uptime or accuracy guarantees.\n"
    f"2. {E_LOCK} Your login session is stored only to fetch content on your behalf.\n"
    f"3. {E_STOP} Don't use this bot to violate copyright or any platform's own Terms of Service.\n"
    f"4. {E_WARN} Free-plan limits (batch size, file size) may change at any time.\n"
    f"5. {E_CROSS} The developer isn't liable for bans, data loss, or misuse by you or others.\n"
    f"6. {E_CHECK} Continuing to use this bot means you accept these terms.</blockquote>"
)


class script(object):

    START_TXT = (
        f"<b>{E_STAR} Hello {{0}},</b>\n"
        f"<b>{E_ROCKET} I am <a href=https://t.me/{{1}}>{{2}}</a></b>\n"
        f"<i>Your Professional Restricted Content Saver Bot.</i>\n"
        f"<blockquote>"
        f"<b>{E_GREEN} System Status: Online</b>\n"
        f"<b>{E_BOLT} Performance: 10x High-Speed Processing</b>\n"
        f"<b>{E_LOCK} Security: End-to-End Encrypted</b>\n"
        f"<b>{E_STATS} Uptime: 99.9% Guaranteed</b>"
        f"</blockquote>\n"
        f"<b>{E_ARROW} Select an Option Below to Get Started:</b>"
    )

    HELP_TXT = (
        f"<b>{E_INFO} Comprehensive Help & User Guide</b>\n"
        f"<blockquote><b>1️⃣ ᴘᴜʙʟɪᴄ ᴄʜᴀɴɴᴇʟs (ɴᴏ ʟᴏɢɪɴ ʀᴇǫᴜɪʀᴇᴅ)</b></blockquote>\n"
        f"{E_ARROW} Forward or send the post link directly.\n"
        f"{E_CHECK} Compatible with any public channel or group.\n"
        f"{E_LINK} <i>Example Link:</i> <code>https://t.me/channel/123</code>\n"
        f"<blockquote><b>2️⃣ ᴘʀɪᴠᴀᴛᴇ/ʀᴇsᴛʀɪᴄᴛᴇᴅ ᴄʜᴀɴɴᴇʟs (ʟᴏɢɪɴ ʀᴇǫᴜɪʀᴇᴅ)</b></blockquote>\n"
        f"{E_LOCK} Use <code>/login</code> to securely connect your Telegram account.\n"
        f"{E_LINK} Send the private link (e.g., <code>t.me/c/123...</code>).\n"
        f"{E_SHIELD} Bot accesses content using your authenticated session.\n"
        f"<blockquote><b>3️⃣ ʙᴀᴛᴄʜ ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ᴍᴏᴅᴇ</b></blockquote>\n"
        f"{E_BATCH} Initiate with <code>/batch</code> for multiple files.\n"
        f"{E_GEAR} Follow interactive prompts for seamless processing.\n"
        f"<blockquote><b>{E_STOP} Free User Limitations:</b></blockquote>\n"
        f"{E_RED} <b>ᴅᴀɪʟʏ ǫᴜᴏᴛᴀ:</b> 10 Files / 24 Hours\n"
        f"{E_RED} <b>ғɪʟᴇ sɪᴢᴇ ᴄᴀᴘ:</b> 2GB Maximum\n"
        f"<blockquote><b>{E_DIAMOND} Premium Membership Benefits:</b></blockquote>\n"
        f"{E_CHECK} Unlimited Downloads & No Restrictions.\n"
        f"{E_SPARK} Priority Support & Advanced Features.\n"
    )

    ABOUT_TXT = (
        f"<blockquote>{E_INFO} <b>💠 ᴀʙᴏᴜᴛ ᴛʜɪs ʙᴏᴛ 💠</b>\n\n"
        f"<b>╭────[ {E_SPARK}  ᴀɴᴜᴊ ]────⍟</b>\n"
        f"<b>├⍟ {E_ROCKET}  ʙᴏᴛ ɴᴀᴍᴇ  : <a href=https://t.me/{{0}}>{{1}}</a></b>\n"
        f"<b>├⍟ {E_DEV}  ᴅᴇᴠᴇʟᴏᴘᴇʀ  : <a href=https://t.me/anujedits76>ᴀɴᴜᴊ ᴋᴜᴍᴀʀ</a></b>\n"
        f"<b>├⍟ {E_LINK}  ʟɪʙʀᴀʀʏ  : <a href='https://docs.pyrogram.org/'>ᴘʏʀᴏɢʀᴀᴍ ᴀsʏɴᴄ</a></b>\n"
        f"<b>├⍟ {E_BOLT}  ʟᴀɴɢᴜᴀɢᴇ  : <a href='https://www.python.org/'>ᴘʏᴛʜᴏɴ 3.11+</a></b>\n"
        f"<b>├⍟ {E_GEAR}  ᴅᴀᴛᴀʙᴀsᴇ  : <a href='https://www.mongodb.com/'>ᴍᴏɴɢᴏᴅʙ</a></b>\n"
        f"<b>├⍟ {E_STAR}  ʜᴏsᴛɪɴɢ  :  ᴅᴇᴅɪᴄᴀᴛᴇᴅ ʜɪɢʜ-sᴘᴇᴇᴅ ᴠᴘs </b>\n"
        f"<b>╰───────────────⍟</b></blockquote>\n"
    )

    PREMIUM_TEXT = (
        f"<blockquote>{E_DIAMOND} <b>ᴘʀᴇᴍɪᴜᴍ ᴍᴇᴍʙᴇʀsʜɪᴘ ᴘʟᴀɴs</b>\n"
        f"{E_SPARK} Unlock Unlimited Access & Advanced Features!\n\n"
        f"<b>{E_CHECK} Key Benefits:</b>\n"
        f"{E_BATCH} Unlimited Daily Downloads\n"
        f"{E_BOLT} Support for 4GB+ File Sizes\n"
        f"{E_ROCKET} Instant Processing (Zero Delay)\n"
        f"{E_IMAGE} Customizable Thumbnails\n"
        f"{E_PENCIL} Personalized Captions\n"
        f"{E_SHIELD} 24/7 Priority Support</blockquote>\n\n"
        f"<blockquote>{E_PAYMENT} <b>ᴘʀɪᴄɪɴɢ ᴏᴘᴛɪᴏɴs:</b></blockquote>\n"
        f"{E_ARROW} <b>1 ᴍᴏɴᴛʜ ᴘʟᴀɴ:</b> ₹554 / $6 (Billed Monthly)\n"
        f"{E_ARROW} <b>3 ᴍᴏɴᴛʜ ᴘʟᴀɴ:</b> ₹1660 / $17.98 (Save 20%)\n"
        f"{E_CROWN} <b>ʟɪғᴇᴛɪᴍᴇ ᴀᴄᴄᴇss:</b> ₹3000 / $32.5 (One-Time Payment)\n\n"
        f"<blockquote>{E_LOCK} <b>sᴇᴄᴜʀᴇ ᴘᴀʏᴍᴇɴᴛ:</b></blockquote>\n"
        f"{E_BOLT} <b>ᴜᴘɪ ɪᴅ:</b> <code>{{}}</code>\n"
        f"{E_LINK} <b>ǫʀ ᴄᴏᴅᴇ:</b> <a href='{{}}'>Scan to Pay</a>\n"
        f"<i>{E_TIP} After Payment: Send Screenshot to Admin for Instant Activation.</i>\n"
    )

    PROGRESS_BAR = (
        f"<b>{E_BOLT} Processing Task...</b>\n"
        "<blockquote>\n"
        "<b>Progress: {bar} {percentage:.1f}%</b>\n"
        f"<b>{E_ROCKET} Speed:</b> <code>{{speed}}/s</code>\n"
        f"<b>{E_BATCH} Size:</b> <code>{{current}} of {{total}}</code>\n"
        f"<b>{E_CLOCK} Elapsed:</b> <code>{{elapsed}}</code>\n"
        f"<b>{E_TIP} ETA:</b> <code>{{eta}}</code>\n"
        "</blockquote>\n"
    )

    CAPTION = f"""<b><a href="https://t.me/anujedits76"></a></b>\n\n<b>{E_CROWN} Powered By : <a href="https://t.me/anujedits76">𝐀𝐍𝐔𝐉 𝐊𝐔𝐌𝐀𝐑</a></b>"""

    LIMIT_REACHED = (
        f"<blockquote>{E_STOP} <b>ᴅᴀɪʟʏ ʟɪᴍɪᴛ ᴇxᴄᴇᴇᴅᴇᴅ</b>\n"
        f"{E_RED} Your 10 free saves for today have been used.\n"
        f"{E_CLOCK} Quota resets automatically after 24 hours from first download.\n\n"
        f"{E_DIAMOND} <b>ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ғᴏʀ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇss!</b>\n"
        f"{E_CHECK} Remove all restrictions and enjoy seamless downloading.</blockquote>\n"
    )

    SIZE_LIMIT = (
        f"<blockquote>{E_WARN} <b>ғɪʟᴇ sɪᴢᴇ ᴇxᴄᴇᴇᴅᴇᴅ</b>\n"
        f"{E_RED} Free tier limited to 2GB per file.\n\n"
        f"{E_DIAMOND} <b>ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ</b>\n"
        f"{E_CHECK} Download files up to 4GB and beyond with no limits!</blockquote>\n"
    )


# =========================================================
# Safe InlineKeyboardButton builder
# =========================================================

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


# =========================================================
# Helper: Paginated "All Commands" view
# =========================================================

def _cmdlist_total_pages():
    return max(1, math.ceil(len(ALL_COMMANDS) / CMDS_PER_PAGE))


def _cmdlist_text(page: int) -> str:
    total_pages = _cmdlist_total_pages()
    page = max(0, min(page, total_pages - 1))
    start_i = page * CMDS_PER_PAGE
    chunk = ALL_COMMANDS[start_i:start_i + CMDS_PER_PAGE]
    lines = [f"<code>/{cmd}</code> — {desc}" for cmd, desc in chunk]
    return (
        f"<b>📜 ᴀʟʟ ᴄᴏᴍᴍᴀɴᴅs (ᴀ–ᴢ)</b>  <i>· page {page + 1}/{total_pages}</i>\n\n"
        + "\n".join(lines)
    )


def _cmdlist_buttons(page: int) -> InlineKeyboardMarkup:
    total_pages = _cmdlist_total_pages()
    page = max(0, min(page, total_pages - 1))
    nav_row = []
    if page > 0:
        nav_row.append(make_button(" ◀️ ᴘʀᴇᴠ ", callback_data=f"cmdlist:{page - 1}", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None))
    if page < total_pages - 1:
        nav_row.append(make_button(" ɴᴇxᴛ ▶️ ", callback_data=f"cmdlist:{page + 1}", style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None))
    rows = []
    if nav_row:
        rows.append(nav_row)
    rows.append([
        make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="menu_msg_back", icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
        make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="menu_msg_close", icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None),
    ])
    return InlineKeyboardMarkup(rows)


# =========================================================
# Helper: Build Start Buttons  (with ButtonStyle)
# =========================================================

async def clone_footer(client: Client) -> str:
    """If `client` is a Titanium clone (not the main Akbots bot itself),
    returns a 'Created & Managed by @AkBots' footer line to append to
    START_TXT. Empty string on the main bot. Import of Akbots.titanium
    is deferred to avoid a circular import (titanium.py imports
    make_button from this module at its own top level)."""
    try:
        from config import BOT_TOKEN as _MAIN_TOKEN
        if getattr(client, "bot_token", None) == _MAIN_TOKEN:
            return ""
        from Akbots.titanium import get_main_bot_username
        main_username = await get_main_bot_username()
        return (
            f"\n<blockquote>⚡ <b>ᴄʀᴇᴀᴛᴇᴅ &ᴀᴍᴘ; ᴍᴀɴᴀɢᴇᴅ ʙʏ</b> "
            f"<a href=https://t.me/{main_username}>@{main_username}</a></blockquote>"
        )
    except Exception:
        return ""


def get_start_buttons():
    if BUTTON_STYLE_SUPPORTED:
        S = ButtonStyle
        return InlineKeyboardMarkup([
            [
                make_button(" 💎 ᴘʀᴇᴍɪᴜᴍ ",  callback_data="buy_premium",   icon_custom_emoji_id=ICON_PREMIUM, style=S.PRIMARY),
                make_button(" 🆘 ʜᴇʟᴘ ",      callback_data="help_btn",      icon_custom_emoji_id=ICON_HELP,    style=S.PRIMARY),
            ],
            [
                make_button(" ⚙️ sᴇᴛᴛɪɴɢs ",  callback_data="settings_btn",  icon_custom_emoji_id=ICON_GEAR,    style=S.PRIMARY),
                make_button(" 📋 ᴍʏ ᴘʟᴀɴ ",   callback_data="myplan_btn",    icon_custom_emoji_id=ICON_PLAN,    style=S.PRIMARY),
            ],
            [
                make_button(" 📢 ᴄʜᴀɴɴᴇʟs ",  callback_data="channels_info", icon_custom_emoji_id=ICON_CHANNEL, style=S.PRIMARY),
                make_button(" ℹ️ ᴀʙᴏᴜᴛ ",     callback_data="about_btn",     icon_custom_emoji_id=ICON_INFO,    style=S.PRIMARY),
            ],
            [
                make_button(" 🚀 ʙᴏᴛ ғᴇᴀᴛᴜʀᴇs ", callback_data="features_btn", icon_custom_emoji_id=ICON_LIST,  style=S.PRIMARY),
            ],
            [
                make_button(" 📜 ᴀʟʟ ᴄᴏᴍᴍᴀɴᴅs ", callback_data="cmdlist:0",    icon_custom_emoji_id=ICON_LIST,  style=S.PRIMARY),
            ],
            [
                make_button(" ⚠️ ᴡᴀʀɴɪɴɢ ",   callback_data="warning_btn",   icon_custom_emoji_id=ICON_WARNING, style=S.DANGER),
            ],
            [
                make_button(" 👨‍💻 ᴅᴇᴠᴇʟᴏᴘᴇʀ ", callback_data="dev_info",      icon_custom_emoji_id=ICON_DEV,     style=S.PRIMARY),
            ]
        ])
    else:
        return InlineKeyboardMarkup([
            [
                make_button(" 💎 ᴘʀᴇᴍɪᴜᴍ ",  callback_data="buy_premium",   icon_custom_emoji_id=ICON_PREMIUM, style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None),
                make_button(" 🆘 ʜᴇʟᴘ ",      callback_data="help_btn",      icon_custom_emoji_id=ICON_HELP, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            ],
            [
                make_button(" ⚙️ sᴇᴛᴛɪɴɢs ",  callback_data="settings_btn",  icon_custom_emoji_id=ICON_GEAR, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
                make_button(" 📋 ᴍʏ ᴘʟᴀɴ ",   callback_data="myplan_btn",    icon_custom_emoji_id=ICON_PLAN, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            ],
            [
                make_button(" 📢 ᴄʜᴀɴɴᴇʟs ",  callback_data="channels_info", icon_custom_emoji_id=ICON_CHANNEL, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
                make_button(" ℹ️ ᴀʙᴏᴜᴛ ",     callback_data="about_btn",     icon_custom_emoji_id=ICON_INFO, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            ],
            [
                make_button(" 🚀 ʙᴏᴛ ғᴇᴀᴛᴜʀᴇs ", callback_data="features_btn", icon_custom_emoji_id=ICON_LIST, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            ],
            [
                make_button(" 📜 ᴀʟʟ ᴄᴏᴍᴍᴀɴᴅs ", callback_data="cmdlist:0",    icon_custom_emoji_id=ICON_LIST, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            ],
            [
                make_button(" ⚠️ ᴡᴀʀɴɪɴɢ ",   callback_data="warning_btn",   icon_custom_emoji_id=ICON_WARNING, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None),
            ],
            [
                make_button(" 👨‍💻 ᴅᴇᴠᴇʟᴏᴘᴇʀ ", callback_data="dev_info",      icon_custom_emoji_id=ICON_DEV, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            ]
        ])


# =========================================================
# Utility Functions
# =========================================================

def humanbytes(size):
    if not size:
        return "0B"
    power = 2 ** 10
    n = 0
    Dic_powerN = {0: ' ', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'


def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = (
        ((str(days) + "d, ") if days else "") +
        ((str(hours) + "h, ") if hours else "") +
        ((str(minutes) + "m, ") if minutes else "") +
        ((str(seconds) + "s, ") if seconds else "")
    )
    return tmp[:-2] if tmp else "0s"


class batch_temp(object):
    IS_BATCH = {}


def get_message_type(msg):
    if getattr(msg, 'document', None): return "Document"
    if getattr(msg, 'video', None):    return "Video"
    if getattr(msg, 'photo', None):    return "Photo"
    if getattr(msg, 'audio', None):    return "Audio"
    if getattr(msg, 'text', None):     return "Text"
    return None


# =========================================================
# Wallhaven Image Fetcher — ANIME + PEOPLE ONLY
# =========================================================

WALLHAVEN_API_KEY = "FsXt5pwoerVZrsV3DwhRctls8YzUev9H"

WALLHAVEN_QUERIES = [
    "anime+girl+portrait", "anime+portrait+face", "anime+girl+close+up",
    "anime+beautiful+face", "anime+school+girl", "anime+school+uniform",
    "anime+sailor+uniform", "anime+fantasy+girl", "anime+magic+girl",
    "anime+witch+girl", "anime+elf+girl", "anime+princess",
    "anime+dark+girl", "anime+gothic+girl", "anime+demon+girl",
    "anime+vampire+girl", "anime+girl+sakura", "anime+girl+nature",
    "anime+girl+sunset", "anime+girl+rain", "anime+girl+snow",
    "anime+girl+flowers", "anime+girl+forest", "anime+girl+summer",
    "anime+girl+winter", "anime+girl+spring", "anime+girl+autumn",
    "anime+kawaii+girl", "anime+cute+girl", "anime+chibi+girl",
    "anime+warrior+girl", "anime+sword+girl", "anime+ninja+girl",
    "anime+knight+girl", "anime+cyberpunk+girl", "anime+girl+ocean",
    "anime+girl+sky", "anime+girl+clouds", "anime+mermaid",
    "anime+girl+night", "anime+girl+stars", "anime+girl+moon",
    "anime+girl+galaxy", "anime+pink+hair+girl", "anime+blue+hair+girl",
    "anime+white+hair+girl", "anime+silver+hair+girl", "anime+red+hair+girl",
    "anime+blonde+anime+girl", "anime+girl+smile", "anime+girl+serious",
    "anime+kimono+girl", "anime+yukata+girl", "anime+shrine+maiden",
    "anime+japanese+girl", "anime+waifu", "anime+girl+4k",
    "anime+girl+aesthetic", "anime+girl+minimal", "anime+cherry+blossom",
    "anime+boy+cool", "anime+couple", "anime+art",
    "beautiful+girl+portrait", "asian+girl+portrait+4k",
    "aesthetic+girl+photography", "beautiful+woman+4k",
    "girl+nature+portrait", "model+photography+portrait",
    "cute+girl+wallpaper", "pretty+girl+face+portrait",
    "girl+sunset+photography", "woman+aesthetic+wallpaper",
    "girl+flowers+photography", "beautiful+eyes+portrait",
    "girl+rain+photography", "woman+forest+portrait",
    "girl+city+night+photography",
]


async def fetch_random_image() -> str:
    fallback = "https://l.arzfun.com/duLNg"
    try:
        query = random.choice(WALLHAVEN_QUERIES)
        page  = random.randint(1, 3)
        url = (
            f"https://wallhaven.cc/api/v1/search"
            f"?categories=011&purity=100&q={query}"
            f"&sorting=random&page={page}&apikey={WALLHAVEN_API_KEY}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json()
                images = data.get("data", [])
                if not images:
                    url_fallback = (
                        f"https://wallhaven.cc/api/v1/search"
                        f"?categories=011&purity=100&sorting=random&apikey={WALLHAVEN_API_KEY}"
                    )
                    async with session.get(url_fallback, timeout=aiohttp.ClientTimeout(total=10)) as resp2:
                        data2 = await resp2.json()
                        images = data2.get("data", [])
                if not images:
                    return fallback
                chosen    = random.choice(images)
                image_url = chosen.get("path", fallback)
                logger.info(f"Wallhaven | Query: {query} | Page: {page} | Image: {image_url}")
                return image_url
    except Exception as e:
        logger.error(f"Wallhaven fetch failed: {e}")
        return fallback


# =========================================================

async def downstatus(client, statusfile, message, chat):
    # Fast (premium-session) downloads can finish in well under 3s, so this
    # poller must check often enough to catch the status file before it's
    # deleted by the caller once the transfer is done.
    while not os.path.exists(statusfile):
        await asyncio.sleep(0.5)
    last_txt = None
    while os.path.exists(statusfile):
        try:
            with open(statusfile, "r", encoding='utf-8') as f:
                txt = f.read()
            if txt != last_txt:
                await safe_edit(client.edit_message_text, chat, message.id, txt)
                last_txt = txt
        except Exception:
            pass
        await asyncio.sleep(1)


async def upstatus(client, statusfile, message, chat):
    while not os.path.exists(statusfile):
        await asyncio.sleep(0.5)
    last_txt = None
    while os.path.exists(statusfile):
        try:
            with open(statusfile, "r", encoding='utf-8') as f:
                txt = f.read()
            if txt != last_txt:
                await safe_edit(client.edit_message_text, chat, message.id, txt)
                last_txt = txt
        except Exception:
            pass
        await asyncio.sleep(1)


_progress_cache: dict = {}
_progress_start_time: dict = {}


def progress(current, total, message, transfer_type):
    user_id = message.from_user.id if message.from_user else None
    if user_id and batch_temp.IS_BATCH.get(user_id) is True:
        raise Exception("Cancelled")
    now = time.time()
    task_id = f"{message.chat.id}_{message.id}_{transfer_type}"
    if task_id not in _progress_start_time:
        _progress_start_time[task_id] = now
    last_time = _progress_cache.get(task_id, 0)
    if (now - last_time) > 5 or current == total:
        try:
            elapsed = now - _progress_start_time[task_id]
            percentage = (current * 100 / total) if total > 0 else 0
            speed = current / elapsed if elapsed > 0 else 0
            eta = (total - current) / speed if speed > 0 else 0
            filled = int(percentage / 5)
            bar = '█' * filled + '░' * (20 - filled)
            status = script.PROGRESS_BAR.format(
                bar=bar, percentage=percentage,
                current=humanbytes(current), total=humanbytes(total),
                speed=humanbytes(speed),
                elapsed=TimeFormatter(int(elapsed * 1000)),
                eta=TimeFormatter(int(eta * 1000))
            )
            status_file = f"{message.chat.id}_{message.id}_{transfer_type}status.txt"
            with open(status_file, "w", encoding='utf-8') as f:
                f.write(status)
            _progress_cache[task_id] = now
            if current == total:
                _progress_start_time.pop(task_id, None)
                _progress_cache.pop(task_id, None)
        except Exception:
            pass


# =========================================================
# Command Handlers
# =========================================================

@Client.on_message(filters.command(["start"]))
async def send_start(client: Client, message: Message):
    # Force-subscribe gate (see Akbots/forcesub.py). No-op unless
    # FORCE_SUB_CHANNEL is configured.
    from Akbots.forcesub import is_subscribed, send_force_sub_prompt
    if not await is_subscribed(client, message.from_user.id):
        await send_force_sub_prompt(client, message)
        return

    # Referral link handling (see Akbots/referral.py) — must run BEFORE the
    # user is auto-created below, since it needs to know if this is a new user.
    if len(message.command) > 1 and message.command[1].startswith("refer_"):
        from Akbots.referral import process_referral_start
        code = message.command[1][len("refer_"):]
        handled = await process_referral_start(client, message, code)
        if handled:
            return

    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)

    # Optional free-access token verification (see Akbots/token_gate.py)
    if len(message.command) > 1 and message.command[1].startswith("freeaccess_"):
        from Akbots.token_gate import verify_token_param
        handled = await verify_token_param(message)
        if handled:
            return

    # File Store deep links — share tokens, batch_<id> links, and legacy
    # base64 "get-" links (see Akbots/filestore.py).
    if len(message.command) > 1:
        from Akbots.filestore import handle_filestore_start
        handled = await handle_filestore_start(client, message, message.command[1].strip())
        if handled:
            return

    try:
        await message.react(emoji=random.choice(REACTIONS), big=True)
    except Exception:
        pass
    photo_url = await fetch_random_image()
    bot = await client.get_me()
    start_caption = script.START_TXT.format(
        message.from_user.mention, bot.username, bot.first_name
    ) + await clone_footer(client)
    await client.send_photo(
        chat_id=message.chat.id,
        photo=photo_url,
        caption=start_caption,
        reply_markup=get_start_buttons(),
        reply_parameters=pyrogram.types.ReplyParameters(message_id=message.id),
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command(["help"]))
async def send_help(client: Client, message: Message):
    if BUTTON_STYLE_SUPPORTED:
        buttons = InlineKeyboardMarkup([
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER)]
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]
        ])
    await client.send_message(
        chat_id=message.chat.id,
        text=script.HELP_TXT,
        reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command(["plan"]))
async def send_plan(client: Client, message: Message):
    if BUTTON_STYLE_SUPPORTED:
        buttons = InlineKeyboardMarkup([
            [make_button(" 📸 sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ᴘʀᴏᴏғ ", url="https://t.me/anujedits76",
                         icon_custom_emoji_id=ICON_PAYMENT, style=ButtonStyle.SUCCESS)],
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER)]
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [make_button(" 📸 sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ᴘʀᴏᴏғ ", url="https://t.me/anujedits76",
                         icon_custom_emoji_id=ICON_PAYMENT, style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]
        ])
    await client.send_photo(
        chat_id=message.chat.id,
        photo=SUBSCRIPTION,
        caption=script.PREMIUM_TEXT.format(UPI_ID, QR_CODE),
        reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML
    )


async def _stop_everything_for(user_id: int, chat_id: int) -> int:
    """Best-effort stop of every kind of download/upload running for a user:
    batch-mode loops (flag-checked), any task registered with task_manager
    (ytdl, direct_utils-backed downloads incl. terabox/mega/torrent/gdrive/
    mediafire/pixeldrain/streamtape/fembed/gofile/catbox/urluploader/aria2,
    plus instagram/facebook/mxplayer which register their own handler
    task), and m3u8dl's own batch queue. Returns a rough count of things
    that were actively stopped (not counting the batch-mode flag, which has
    no count of its own)."""
    batch_temp.IS_BATCH[user_id] = True

    stopped = 0
    try:
        from Akbots import task_manager
        stopped += task_manager.cancel_all_for(user_id)
    except Exception:
        pass

    try:
        from Akbots.m3u8dl import cancel_all as m3u8_cancel_all
        if await m3u8_cancel_all(chat_id):
            stopped += 1
    except Exception:
        pass

    return stopped


@Client.on_message(filters.command(["cancel", "stop"]))
async def send_cancel(client: Client, message: Message):
    await _stop_everything_for(message.from_user.id, message.chat.id)
    await message.reply_text(
        f"<b>{E_CROSS} Download/Process Cancelled Successfully.</b>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex(r"^batch_cancel_btn:(\d+)$"))
async def batch_cancel_callback(client: Client, callback_query: CallbackQuery):
    owner_id = int(callback_query.matches[0].group(1))
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("This isn't your batch to cancel.", show_alert=True)
    await _stop_everything_for(owner_id, callback_query.message.chat.id)
    await callback_query.answer("Cancelling everything...")


@Client.on_message(filters.command(["ping"]))
async def send_ping(client: Client, message: Message):
    start_t = time.time()
    sent = await message.reply_text(
        f"<b>{E_BOLT} Checking speed...</b>", parse_mode=enums.ParseMode.HTML
    )
    ms = int((time.time() - start_t) * 1000)
    quality = E_GREEN if ms < 300 else (E_WARN if ms < 800 else E_RED)
    await safe_edit(sent.edit_text, 
        f"<b>{E_BOLT} Pong!</b>\n\n{quality} <b>ʀᴇsᴘᴏɴsᴇ ᴛɪᴍᴇ:</b> <code>{ms} ms</code>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command(["status"]))
async def send_status(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    session_data = await db.get_session(user_id)
    caption      = await db.get_caption(user_id)
    dump_chat    = await db.get_dump_chat(user_id)
    is_premium   = await db.check_premium(user_id)
    custom_limit = await db.get_batch_limit(user_id)
    total_saved  = await db.get_total_saved(user_id)
    job_active   = batch_temp.IS_BATCH.get(user_id) is False

    default_cap  = MAX_BATCH_IDS_PREMIUM if is_premium else MAX_BATCH_IDS_FREE
    limit_label  = str(min(custom_limit, default_cap)) if custom_limit else str(default_cap)

    await message.reply_text(
        f"<blockquote>{E_STATS} <b>ʏᴏᴜʀ sᴛᴀᴛᴜs</b>\n\n"
        f"{E_GREEN if session_data else E_RED} <b>sᴇssɪᴏɴ:</b> {'Connected and ready' if session_data else 'Not connected'}\n"
        f"{E_PENCIL} <b>ᴄᴀᴘᴛɪᴏɴ:</b> {'Set' if caption else 'Not set'}\n"
        f"{E_STOP} <b>ᴅᴜᴍᴘ ᴄʜᴀᴛ:</b> {dump_chat if dump_chat else 'Not set'}\n"
        f"{E_CROWN if is_premium else E_INFO} <b>ᴘʟᴀɴ:</b> {'💎 Premium' if is_premium else '👤 Free'}\n"
        f"{E_BATCH} <b>ʙᴀᴛᴄʜ ʟɪᴍɪᴛ:</b> <code>{limit_label}</code>\n"
        f"{E_STAR} <b>ғɪʟᴇs sᴀᴠᴇᴅ:</b> <code>{total_saved}</code>\n"
        f"{E_BOLT if job_active else E_STOP} <b>ᴀᴄᴛɪᴠᴇ ᴛᴀsᴋ:</b> {'Running now' if job_active else 'Nothing running right now'}</blockquote>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command(["about"]))
async def send_about(client: Client, message: Message):
    if BUTTON_STYLE_SUPPORTED:
        buttons = InlineKeyboardMarkup([
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER)]
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]
        ])
    bot = await client.get_me()
    await client.send_message(
        chat_id=message.chat.id,
        text=script.ABOUT_TXT.format(bot.username, bot.first_name),
        reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command(["warning"]))
async def send_warning(client: Client, message: Message):
    if BUTTON_STYLE_SUPPORTED:
        buttons = InlineKeyboardMarkup([
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER)]
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]
        ])
    await client.send_message(
        chat_id=message.chat.id,
        text=WARNING_TEXT,
        reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command(["terms"]))
async def send_terms(client: Client, message: Message):
    if BUTTON_STYLE_SUPPORTED:
        buttons = InlineKeyboardMarkup([
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER)]
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="close_btn",
                         icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]
        ])
    await client.send_message(
        chat_id=message.chat.id,
        text=TERMS_TXT,
        reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command(["settings"]))
async def send_settings(client: Client, message: Message):
    user_id = message.from_user.id
    is_premium = await db.check_premium(user_id)
    badge = f"{E_DIAMOND} Premium Member" if is_premium else f"{E_INFO} Standard User"

    buttons = get_settings_buttons()

    text = (
        f"<blockquote>{E_GEAR} <b>sᴇᴛᴛɪɴɢs ᴅᴀsʜʙᴏᴀʀᴅ</b>\n\n"
        f"{E_INFO} <b>ᴀᴄᴄᴏᴜɴᴛ sᴛᴀᴛᴜs:</b> {badge}\n"
        f"{E_BOLT} <b>ᴜsᴇʀ ɪᴅ:</b> <code>{user_id}</code>\n\n"
        f"{E_TIP} Customize and manage your bot preferences below:</blockquote>"
    )

    await client.send_message(
        chat_id=message.chat.id,
        text=text,
        reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML
    )


# =========================================================
# Settings Panel
# =========================================================

async def settings_panel(client, callback_query):
    user_id = callback_query.from_user.id
    is_premium = await db.check_premium(user_id)
    badge = f"{E_DIAMOND} Premium Member" if is_premium else f"{E_INFO} Standard User"

    buttons = get_settings_buttons()

    text = (
        f"<blockquote>{E_GEAR} <b>sᴇᴛᴛɪɴɢs ᴅᴀsʜʙᴏᴀʀᴅ</b>\n\n"
        f"{E_INFO} <b>ᴀᴄᴄᴏᴜɴᴛ sᴛᴀᴛᴜs:</b> {badge}\n"
        f"{E_BOLT} <b>ᴜsᴇʀ ɪᴅ:</b> <code>{user_id}</code>\n\n"
        f"{E_TIP} Customize and manage your bot preferences below:</blockquote>"
    )

    await safe_edit(callback_query.edit_message_caption, 
        caption=text,
        reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML
    )


# =========================================================
# Save Handler
# =========================================================

# Public link:  t.me/<username>/<msg_id>(-<end_id>)
# Private link: t.me/c/<chat_id>/<msg_id>(-<end_id>)
_PUB  = re.compile(r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})/(\d+)(?:-(\d+))?", re.I)
_PRIV = re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)(?:-(\d+))?", re.I)


@Client.on_message(filters.text & filters.private & ~filters.regex("^/"))
async def save(client: Client, message: Message):
    if "https://t.me/" not in message.text:
        return

    from Akbots.multibot import get_user_bot
    delivery_client = await get_user_bot(message.from_user.id) or client

    is_limit_reached = await db.check_limit(message.from_user.id)
    if is_limit_reached:
        if BUTTON_STYLE_SUPPORTED:
            btn = InlineKeyboardMarkup([
                [make_button(" 💎 ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ", callback_data="buy_premium",
                             icon_custom_emoji_id=ICON_PREMIUM, style=ButtonStyle.PRIMARY)]
            ])
        else:
            btn = InlineKeyboardMarkup([
                [make_button(" 💎 ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ", callback_data="buy_premium",
                             icon_custom_emoji_id=ICON_PREMIUM, style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None)]
            ])
        return await message.reply_photo(
            photo=SUBSCRIPTION,
            caption=script.LIMIT_REACHED,
            reply_markup=btn,
            parse_mode=enums.ParseMode.HTML
        )

    if batch_temp.IS_BATCH.get(message.from_user.id) is False:
        return await message.reply_text(
            f"<b>{E_WARN} A Task is Currently Processing.</b>\n"
            f"<i>{E_TIP} Please wait or use /cancel to stop.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    link_text = message.text.replace("?single", "").strip()

    is_batch   = "https://t.me/b/" in message.text
    priv_match = None if is_batch else _PRIV.search(link_text)
    pub_match  = None if (is_batch or priv_match) else _PUB.search(link_text)

    is_private_link = bool(priv_match)
    is_public_link  = not is_private_link and not is_batch

    if priv_match:
        target, from_str, to_str = priv_match.groups()
        fromID = int(from_str)
        toID = int(to_str) if to_str else fromID
    elif pub_match:
        target, from_str, to_str = pub_match.groups()
        fromID = int(from_str)
        toID = int(to_str) if to_str else fromID
    else:
        # Fallback for the internal batch-dump format (t.me/b/...) or any
        # link shape the regexes above don't recognise.
        datas = message.text.split("/")
        temp = datas[-1].replace("?single", "").split("-")
        try:
            fromID = int(temp[0].strip())
        except (ValueError, IndexError):
            return await message.reply_text(
                f"<b>{E_CROSS} Invalid link format.</b>", parse_mode=enums.ParseMode.HTML
            )
        try:
            toID = int(temp[1].strip())
        except (ValueError, IndexError):
            toID = fromID
        target = datas[4] if is_batch else (datas[3] if len(datas) > 3 else None)

    progress_msg = None
    if toID > fromID:
        is_premium = bool(await db.check_premium(message.from_user.id))
        default_cap = MAX_BATCH_IDS_PREMIUM if is_premium else MAX_BATCH_IDS_FREE
        custom_limit = await db.get_batch_limit(message.from_user.id)
        max_ids = min(custom_limit, default_cap) if custom_limit else default_cap
        if not is_premium and await db.has_valid_free_token(message.from_user.id):
            max_ids += TOKEN_BATCH_BONUS
        toID = min(toID, fromID + max_ids - 1)

        total_items = toID - fromID + 1
        cancel_markup = InlineKeyboardMarkup([[
            make_button(" ❌ ᴄᴀɴᴄᴇʟ ʙᴀᴛᴄʜ ", callback_data=f"batch_cancel_btn:{message.from_user.id}",
                        icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)
        ]])
        progress_msg = await message.reply_text(
            f"<b>{E_BATCH} Batch Started</b>\n"
            f"<i>{E_INFO} Processing {total_items} message(s)...</i>",
            reply_markup=cancel_markup, parse_mode=enums.ParseMode.HTML
        )

    batch_temp.IS_BATCH[message.from_user.id] = False

    acc = None

    try:
        for msgid in range(fromID, toID + 1):
            if batch_temp.IS_BATCH.get(message.from_user.id) is True:
                break

            if is_public_link:
                username = target
                try:
                    await client.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=username,
                        message_id=msgid,
                        reply_to_message_id=message.id
                    )
                    await db.add_traffic(message.from_user.id)
                    await db.increment_total_saved(message.from_user.id)
                    await asyncio.sleep(1)
                    continue
                except Exception as e:
                    logger.warning(f"copy_message failed for {username}/{msgid}: {e}")

            user_data = await db.get_session(message.from_user.id)
            if user_data is None:
                await message.reply(
                    f"<b>{E_LOCK} Authentication Required</b>\n\n"
                    f"<i>{E_INFO} Access to this content requires login.</i>\n"
                    f"<i>{E_ARROW} Use /login to securely authorize your account.</i>",
                    parse_mode=enums.ParseMode.HTML
                )
                break

            if acc is None:
                try:
                    acc = Client(
                        "saverestricted",
                        session_string=user_data,
                        api_hash=API_HASH,
                        api_id=API_ID,
                        in_memory=True,
                        max_concurrent_transmissions=10
                    )
                    await acc.connect()
                except Exception as e:
                    await message.reply(
                        f"<b>{E_CROSS} Authentication Failed</b>\n\n"
                        f"<i>{E_WARN} Session expired. Please /logout and /login again.</i>\n"
                        f"<code>{e}</code>",
                        parse_mode=enums.ParseMode.HTML
                    )
                    break

            if is_private_link:
                chat_target = int("-100" + target)
            else:
                chat_target = target

            await handle_restricted_content(delivery_client, acc, message, chat_target, msgid)
            await asyncio.sleep(2)

    finally:
        if acc is not None:
            try:
                await acc.disconnect()
            except Exception:
                pass
        was_cancelled = batch_temp.IS_BATCH.get(message.from_user.id) is True
        batch_temp.IS_BATCH[message.from_user.id] = True
        if progress_msg is not None:
            try:
                if was_cancelled:
                    await safe_edit(progress_msg.edit_text, 
                        f"<b>{E_CROSS} Batch Cancelled.</b>", parse_mode=enums.ParseMode.HTML
                    )
                else:
                    await safe_edit(progress_msg.edit_text, 
                        f"<b>{E_CHECK} Batch Completed.</b>", parse_mode=enums.ParseMode.HTML
                    )
            except Exception:
                pass


# =========================================================
# Restricted Content Handler
# =========================================================

async def handle_restricted_content(client: Client, acc, message: Message, chat_target, msgid):
    try:
        msg: Message = await acc.get_messages(chat_target, msgid)
    except Exception as e:
        logger.error(f"Error fetching message {msgid} from {chat_target}: {e}")
        return

    if not msg or msg.empty:
        return

    msg_type = get_message_type(msg)
    if not msg_type:
        return

    file_size = 0
    if msg_type == "Document" and msg.document:
        file_size = msg.document.file_size or 0
    elif msg_type == "Video" and msg.video:
        file_size = msg.video.file_size or 0
    elif msg_type == "Audio" and msg.audio:
        file_size = msg.audio.file_size or 0

    is_premium_user = await db.check_premium(message.from_user.id)

    if file_size > FREE_LIMIT_SIZE and not is_premium_user:
        if BUTTON_STYLE_SUPPORTED:
            btn = InlineKeyboardMarkup([
                [make_button(" 💎 ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ", callback_data="buy_premium",
                             icon_custom_emoji_id=ICON_PREMIUM, style=ButtonStyle.PRIMARY)]
            ])
        else:
            btn = InlineKeyboardMarkup([
                [make_button(" 💎 ᴜᴘɢʀᴀᴅᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ", callback_data="buy_premium",
                             icon_custom_emoji_id=ICON_PREMIUM, style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None)]
            ])
        await client.send_message(
            message.chat.id, script.SIZE_LIMIT,
            reply_markup=btn,
            parse_mode=enums.ParseMode.HTML
        )
        return

    if msg_type == "Text":
        try:
            await client.send_message(
                message.chat.id, msg.text,
                entities=msg.entities,
                parse_mode=enums.ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send text message: {e}")
        return

    await db.add_traffic(message.from_user.id)
    await db.increment_total_saved(message.from_user.id)
    smsg = await client.send_message(
        message.chat.id,
        f'<b>{E_BOLT} Starting Download...</b>',
        reply_to_message_id=message.id,
        parse_mode=enums.ParseMode.HTML
    )

    temp_dir = f"downloads/{message.chat.id}_{message.id}"
    os.makedirs(temp_dir, exist_ok=True)

    down_status_file = f"{message.chat.id}_{message.id}_downstatus.txt"
    up_status_file   = f"{message.chat.id}_{message.id}_upstatus.txt"

    down_task = None
    up_task   = None

    try:
        down_task = asyncio.create_task(
            downstatus(client, down_status_file, smsg, message.chat.id)
        )
        file = await acc.download_media(
            msg, file_name=f"{temp_dir}/",
            progress=progress, progress_args=(message, "down")
        )
        # Let the poller pick up the final progress state before we delete
        # the status file — otherwise fast downloads can finish (and get
        # cleaned up here) before downstatus() ever gets a chance to read
        # the file, so no progress bar ever shows up for quick transfers.
        await asyncio.sleep(1.2)
        if os.path.exists(down_status_file):
            os.remove(down_status_file)
        down_task.cancel()
        try:
            await down_task
        except asyncio.CancelledError:
            pass

    except Exception as e:
        if os.path.exists(down_status_file):
            os.remove(down_status_file)
        if down_task and not down_task.done():
            down_task.cancel()
            try:
                await down_task
            except asyncio.CancelledError:
                pass
        if "Cancelled" in str(e):
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            await safe_edit(smsg.edit, f"<b>{E_CROSS} Task Cancelled</b>", parse_mode=enums.ParseMode.HTML)
            return
        logger.error(f"Download failed for msg {msgid}: {e}")
        await smsg.delete()
        return

    try:
        up_task = asyncio.create_task(
            upstatus(client, up_status_file, smsg, message.chat.id)
        )
        ph_path = None
        thumb_id = await db.get_thumbnail(message.from_user.id)
        if thumb_id:
            try:
                ph_path = await client.download_media(
                    thumb_id, file_name=f"{temp_dir}/custom_thumb.jpg"
                )
            except Exception as e:
                logger.error(f"Custom thumb download failed: {e}")

        if not ph_path:
            try:
                if msg_type == "Video" and msg.video and msg.video.thumbs:
                    ph_path = await acc.download_media(
                        msg.video.thumbs[0].file_id, file_name=f"{temp_dir}/thumb.jpg"
                    )
                elif msg_type == "Document" and msg.document and msg.document.thumbs:
                    ph_path = await acc.download_media(
                        msg.document.thumbs[0].file_id, file_name=f"{temp_dir}/thumb.jpg"
                    )
            except Exception:
                pass

        custom_caption = await db.get_caption(message.from_user.id)
        if custom_caption:
            filename = file.split("/")[-1] if file else "file"
            final_caption = render_caption(
                custom_caption, filename=filename, size=humanbytes(file_size),
                caption=(msg.caption.html if msg.caption else ""), media_type=msg_type,
            )
        else:
            forward_caption = format_forward_caption(msg, credit="anujedits76")
            if msg.caption:
                forward_caption += f"\n\n{msg.caption}"
            final_caption = f"<blockquote>{forward_caption}</blockquote>"

        if msg_type == "Document":
            await client.send_document(
                message.chat.id, file, thumb=ph_path, caption=final_caption,
                progress=progress, progress_args=(message, "up")
            )
        elif msg_type == "Video":
            await client.send_video(
                message.chat.id, file,
                duration=msg.video.duration, width=msg.video.width, height=msg.video.height,
                thumb=ph_path, caption=final_caption,
                progress=progress, progress_args=(message, "up")
            )
        elif msg_type == "Audio":
            await client.send_audio(
                message.chat.id, file, thumb=ph_path, caption=final_caption,
                progress=progress, progress_args=(message, "up")
            )
        elif msg_type == "Photo":
            await client.send_photo(message.chat.id, file, caption=final_caption)

    except Exception as e:
        logger.error(f"Upload failed for msg {msgid}: {e}")
        await safe_edit(smsg.edit, 
            f"<b>{E_CROSS} Upload Failed:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML
        )

    finally:
        for f_path in [up_status_file, down_status_file]:
            if os.path.exists(f_path):
                try:
                    os.remove(f_path)
                except Exception:
                    pass
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        if up_task and not up_task.done():
            up_task.cancel()
            try:
                await up_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await client.delete_messages(message.chat.id, [smsg.id])
        except Exception:
            pass
        task_key_down = f"{message.chat.id}_{message.id}_down"
        task_key_up   = f"{message.chat.id}_{message.id}_up"
        _progress_cache.pop(task_key_down, None)
        _progress_cache.pop(task_key_up, None)
        _progress_start_time.pop(task_key_down, None)
        _progress_start_time.pop(task_key_up, None)


# =========================================================
# Callback Query Handler
# =========================================================

@Client.on_callback_query()
async def button_callbacks(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    message = callback_query.message
    if not message:
        return

    if data == "dev_info":
        await callback_query.answer(text=dev_text, show_alert=True)
        return

    elif data == "channels_info":
        await callback_query.answer(text=channels_text, show_alert=True)
        return

    elif data == "warning_btn":
        if BUTTON_STYLE_SUPPORTED:
            buttons = InlineKeyboardMarkup([
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY)]
            ])
        else:
            buttons = InlineKeyboardMarkup([
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]
            ])
        await safe_edit(callback_query.edit_message_caption, 
            caption=WARNING_TEXT,
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )

    elif data == "settings_btn":
        await settings_panel(client, callback_query)

    elif data == "myplan_btn":
        if BUTTON_STYLE_SUPPORTED:
            buttons = InlineKeyboardMarkup([
                [make_button(" 📸 sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ᴘʀᴏᴏғ ", url="https://t.me/anujedits76",
                             icon_custom_emoji_id=ICON_PAYMENT, style=ButtonStyle.SUCCESS)],
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY)]
            ])
        else:
            buttons = InlineKeyboardMarkup([
                [make_button(" 📸 sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ᴘʀᴏᴏғ ", url="https://t.me/anujedits76",
                             icon_custom_emoji_id=ICON_PAYMENT, style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None)],
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]
            ])
        try:
            await client.edit_message_media(
                chat_id=message.chat.id,
                message_id=message.id,
                media=InputMediaPhoto(
                    media=SUBSCRIPTION,
                    caption=script.PREMIUM_TEXT.format(UPI_ID, QR_CODE)
                ),
                reply_markup=buttons
            )
        except Exception:
            await safe_edit(callback_query.edit_message_caption, 
                caption=script.PREMIUM_TEXT.format(UPI_ID, QR_CODE),
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )

    elif data == "buy_premium":
        if BUTTON_STYLE_SUPPORTED:
            buttons = InlineKeyboardMarkup([
                [make_button(" 📸 sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ᴘʀᴏᴏғ ", url="https://t.me/anujedits76",
                             icon_custom_emoji_id=ICON_PAYMENT, style=ButtonStyle.SUCCESS)],
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY)]
            ])
        else:
            buttons = InlineKeyboardMarkup([
                [make_button(" 📸 sᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ᴘʀᴏᴏғ ", url="https://t.me/anujedits76",
                             icon_custom_emoji_id=ICON_PAYMENT, style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None)],
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]
            ])
        try:
            await client.edit_message_media(
                chat_id=message.chat.id,
                message_id=message.id,
                media=InputMediaPhoto(
                    media=SUBSCRIPTION,
                    caption=script.PREMIUM_TEXT.format(UPI_ID, QR_CODE)
                ),
                reply_markup=buttons
            )
        except Exception:
            await safe_edit(callback_query.edit_message_caption, 
                caption=script.PREMIUM_TEXT.format(UPI_ID, QR_CODE),
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )

    elif data == "help_btn":
        if BUTTON_STYLE_SUPPORTED:
            buttons = InlineKeyboardMarkup([
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY)]
            ])
        else:
            buttons = InlineKeyboardMarkup([
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]
            ])
        await safe_edit(callback_query.edit_message_caption, 
            caption=script.HELP_TXT,
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )

    elif data == "about_btn":
        if BUTTON_STYLE_SUPPORTED:
            buttons = InlineKeyboardMarkup([
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY)]
            ])
        else:
            buttons = InlineKeyboardMarkup([
                [make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="start_btn",
                             icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]
            ])
        bot = await client.get_me()
        await safe_edit(callback_query.edit_message_caption, 
            caption=script.ABOUT_TXT.format(bot.username, bot.first_name),
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )

    elif data == "start_btn":
        bot = await client.get_me()
        photo_url = await fetch_random_image()
        start_caption = script.START_TXT.format(
            callback_query.from_user.mention,
            bot.username,
            bot.first_name
        ) + await clone_footer(client)
        try:
            await client.edit_message_media(
                chat_id=message.chat.id,
                message_id=message.id,
                media=InputMediaPhoto(
                    media=photo_url,
                    caption=start_caption
                ),
                reply_markup=get_start_buttons()
            )
        except Exception:
            await safe_edit(callback_query.edit_message_caption, 
                caption=start_caption,
                reply_markup=get_start_buttons(),
                parse_mode=enums.ParseMode.HTML
            )

    elif data == "features_btn":
        buttons = InlineKeyboardMarkup([[
            make_button(" ⬅️ ʙᴀᴄᴋ ", callback_data="menu_msg_back", icon_custom_emoji_id=ICON_HOME, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
            make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="menu_msg_close", icon_custom_emoji_id=ICON_CLOSE, style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None),
        ]])
        await message.reply_text(
            FEATURES_TXT, reply_markup=buttons, parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )

    elif data.startswith("cmdlist:"):
        page = int(data.split(":", 1)[1])
        text = _cmdlist_text(page)
        buttons = _cmdlist_buttons(page)
        if message.photo:
            # Fired from the main start card — that message stays as-is
            # (it's a photo, its caption can't hold 100+ commands), so this
            # opens a brand-new plain-text message for the paginated list.
            await message.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        else:
            # Fired from Prev/Next on an existing "All Commands" message —
            # just page it in place.
            await safe_edit(callback_query.edit_message_text, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    elif data in ("menu_msg_back", "menu_msg_close"):
        # Both Bot Features and All Commands open as standalone messages on
        # top of the persistent /start card, so Back and Close do the same
        # thing here: dismiss this message, leaving the start card visible.
        try:
            await message.delete()
        except Exception:
            pass

    elif data == "close_btn":
        await message.delete()

    else:
        # Not one of this handler's known callback_data values — let it fall
        # through to other files' filtered @Client.on_callback_query handlers
        # (e.g. ytq:, ytsr:, ytsrpg: in ytdl.py / ytsearch.py). Without this,
        # this no-filter catch-all silently consumes every callback query and
        # those other handlers never run.
        raise pyrogram.ContinuePropagation

    try:
        await callback_query.answer()
    except Exception:
        pass
