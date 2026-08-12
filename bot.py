import asyncio
import datetime
import sys
import os
from datetime import timezone, timedelta
from pyrogram import Client, filters, enums, __version__ as pyrogram_version
from pyrogram.types import Message, BotCommand
from pyrogram.errors import FloodWait, RPCError
from config import API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL, ADMINS
from database.db import db
from logger import LOGGER

try:
    from Akbots.runtime_config import apply_saved_keys_to_config
except ImportError:
    apply_saved_keys_to_config = None

try:
    from keep_alive import keep_alive
except ImportError:
    keep_alive = None

try:
    from Akbots.bgutil_bootstrap import ensure_bgutil_pot_server
except ImportError:
    ensure_bgutil_pot_server = None

try:
    from Akbots.dependency_manager import ensure_pot_provider_installed
except ImportError:
    ensure_pot_provider_installed = None

logger = LOGGER(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
USER_CACHE = set()


def _kill_stale_bot_processes():
    """
    Kill any other process that is still bot.py from a previous crashed/duplicate
    run. A leftover process like this is what causes:
      - "Address already in use" on ports 8080 / 8099 / 8070 (keep-alive,
        MediaInfo streamer, File-to-Link server all bind those ports)
      - "database is locked" on Client.start() (pyrogram's .session file is
        SQLite; a second live process holding it open blocks the new one)
    Safe no-op if psutil isn't installed or nothing stale is found.
    """
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not installed — skipping stale-process cleanup.")
        return

    this_pid = os.getpid()
    killed = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == this_pid:
                continue
            cmdline = proc.info.get("cmdline") or []
            if any("bot.py" in str(part) for part in cmdline):
                proc.kill()
                killed.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed:
        logger.warning(f"Killed stale bot.py process(es) still holding ports/session lock: {killed}")
        import time
        time.sleep(2)  # give the OS a moment to actually release the sockets/file lock

    # Belt-and-suspenders: a hard crash can leave a stray SQLite "-journal"
    # (or "-wal"/"-shm") file next to the .session file, which by itself can
    # also trip "database is locked" even with no process left alive.
    for fname in os.listdir("."):
        if fname.startswith("Akbots_Login_Bot.session-"):
            try:
                os.remove(fname)
                logger.warning(f"Removed stale SQLite lock artifact: {fname}")
            except OSError:
                pass


_kill_stale_bot_processes()

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_BOLT   = '<tg-emoji emoji-id="5456140674028019486">⚡️</tg-emoji>'
E_ROCKET = '<tg-emoji emoji-id="5456140674028019486">🚀</tg-emoji>'
E_GEAR   = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
E_USERS  = '<tg-emoji emoji-id="5334544901428229844">👥</tg-emoji>'
E_CLOCK  = '<tg-emoji emoji-id="5386367538735104399">⌛</tg-emoji>'
E_STOP   = '<tg-emoji emoji-id="5260293700088511294">⛔️</tg-emoji>'
E_STAR   = '<tg-emoji emoji-id="5438496463044752972">⭐️</tg-emoji>'
E_CROWN  = '<tg-emoji emoji-id="5217822164362739968">👑</tg-emoji>'
E_INFO   = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'

LOGO = r"""
   █████╗  ███╗   ██╗ ██╗   ██╗      ██╗
  ██╔══██╗ ████╗  ██║ ██║   ██║      ██║
  ███████║ ██╔██╗ ██║ ██║   ██║      ██║
  ██╔══██║ ██║╚██╗██║ ██║   ██║ ██   ██║
  ██║  ██║ ██║ ╚████║ ╚██████╔╝ ╚█████╔╝
    𝙱𝙾𝚃 𝚆𝙾𝚁𝙺𝙸𝙽𝙶 𝙿𝚁𝙾𝙿𝙴𝚁𝙻𝚈....
"""


class Bot(Client):
    def __init__(self):
        super().__init__(
            name="Akbots_Login_Bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            plugins=dict(root="Akbots"),
            workers=10,
            sleep_threshold=15,
            max_concurrent_transmissions=5,
            ipv6=False,
            in_memory=False,
        )
        self._keep_alive_started = False

    async def start(self, **kwargs):
        print(LOGO)

        if ensure_bgutil_pot_server and not getattr(self, "_bgutil_started", False):
            try:
                ensure_bgutil_pot_server()
                self._bgutil_started = True
            except Exception as e:
                logger.warning(f"bgutil-pot bootstrap failed: {e}")

        if ensure_pot_provider_installed and not getattr(self, "_pot_pip_started", False):
            try:
                ensure_pot_provider_installed()
                self._pot_pip_started = True
            except Exception as e:
                logger.warning(f"pot-provider pip bootstrap failed: {e}")

        if not getattr(self, "_mediainfo_streamer_started", False):
            try:
                from config import MEDIAINFO_STREAM_PORT
                from Akbots.mediainfo_lib.streamer import MediaStreamer
                self._mediainfo_streamer = MediaStreamer(self)
                await self._mediainfo_streamer.start(port=MEDIAINFO_STREAM_PORT)
                self._mediainfo_streamer_started = True
            except Exception as e:
                logger.warning(f"MediaInfo streamer did not start (/mediainfo will be unavailable): {e}")

        if not getattr(self, "_filetolink_server_started", False):
            try:
                from config import STREAM_BIN_CHANNEL, STREAM_PORT
                if not STREAM_BIN_CHANNEL:
                    raise RuntimeError("STREAM_BIN_CHANNEL not configured")
                from Akbots.filetolink.web_server import start_stream_server
                self._filetolink_runner = await start_stream_server(self, STREAM_PORT)
                self._filetolink_server_started = True
                logger.info(f"File-to-Link server started on port {STREAM_PORT}.")
            except Exception as e:
                logger.warning(f"File-to-Link server did not start (/link streaming will be unavailable): {e}")

        # /hotstar's "Direct Link" button (Akbots/hls_proxy.py) needs
        # Akbots/hls_proxy_routes.py mounted on a public port to work.
        # That normally rides the File-to-Link server above for free
        # (same app, same port) — this block only runs as a fallback when
        # that server didn't start (e.g. STREAM_BIN_CHANNEL not set), so
        # the direct-link feature still works without needing file-to-link
        # configured at all.
        if not getattr(self, "_filetolink_server_started", False) and not getattr(self, "_hls_proxy_server_started", False):
            try:
                from aiohttp import web as _web
                from config import STREAM_PORT
                from Akbots.hls_proxy_routes import routes as _hls_routes
                from Akbots.v2hls_routes import routes as _v2hls_routes
                _hls_app = _web.Application()
                _hls_app.add_routes(_hls_routes)
                _hls_app.add_routes(_v2hls_routes)
                _hls_runner = _web.AppRunner(_hls_app)
                await _hls_runner.setup()
                _hls_site = _web.TCPSite(_hls_runner, "0.0.0.0", STREAM_PORT)
                await _hls_site.start()
                self._hls_proxy_runner = _hls_runner
                self._hls_proxy_server_started = True
                logger.info(f"HLS proxy (standalone) started on port {STREAM_PORT}.")
            except Exception as e:
                logger.warning(f"HLS proxy standalone server did not start (direct-link buttons will be unavailable): {e}")

        # /hotstar (Akbots/hotstar.py) talks to services/hotstar-api over
        # config.HOTSTAR_API_URL, which now defaults to this in-process
        # server (see config.py) — no separate deploy or env var needed
        # for a normal setup. Bound to loopback only; see
        # Akbots/hotstar_local_server.py's docstring for why.
        if not getattr(self, "_hotstar_server_started", False):
            try:
                from config import HOTSTAR_LOCAL_PORT
                from Akbots import hotstar_local_server
                ok = await hotstar_local_server.start(HOTSTAR_LOCAL_PORT)
                self._hotstar_server_started = ok
                if ok:
                    logger.info(f"Hotstar API (in-process) started on 127.0.0.1:{HOTSTAR_LOCAL_PORT}.")
            except Exception as e:
                logger.warning(f"Hotstar API server did not start (/hotstar will be unavailable): {e}")

        # Started after the File-to-Link attempt above (not before) so it
        # knows for certain whether that server already bound $PORT — on
        # single-port hosts (Replit/Render/Railway) STREAM_PORT now
        # defaults to $PORT too (see config.py), so this must never blindly
        # bind the same port and swallow real /vc, /watch, /dl traffic
        # behind its plain "alive" response.
        if keep_alive and not self._keep_alive_started:
            try:
                keep_alive(real_server_started=getattr(self, "_filetolink_server_started", False))
                self._keep_alive_started = True
                logger.info("Keep-alive server started.")
            except Exception as e:
                logger.warning(f"Keep-alive failed: {e}")

        if not getattr(self, "_musicbot_started", False):
            try:
                from musicbot.launcher import start_musicbot
                started = await start_musicbot()
                self._musicbot_started = True
                if started:
                    logger.info("Music (group voice chat) module started.")
                else:
                    logger.info("Music module skipped (no STRING_SESSION configured).")
            except Exception as e:
                logger.warning(f"Music module did not start (/play etc. will be unavailable): {e}")

        if apply_saved_keys_to_config and not getattr(self, "_runtime_keys_applied", False):
            try:
                n = await apply_saved_keys_to_config()
                self._runtime_keys_applied = True
                if n:
                    logger.info(f"Applied {n} /setkey override(s) from DB before loading plugins.")
            except Exception as e:
                logger.warning(f"Failed to apply saved /setkey overrides: {e}")

        while True:
            try:
                await super().start(**kwargs)
                break
            except FloodWait as e:
                wait_time = int(e.value) + 10
                logger.warning(f"FLOOD_WAIT detected during login. Sleeping for {wait_time}s...")
                await asyncio.sleep(wait_time)
            except Exception as e:
                err_text = str(e).lower()
                if "database is locked" in err_text:
                    logger.error(
                        "Critical Startup Error: database is locked — clearing stale "
                        "session-lock artifacts and retrying immediately."
                    )
                    _kill_stale_bot_processes()
                    await asyncio.sleep(2)
                elif "is already connected" in err_text:
                    logger.error(
                        "Critical Startup Error: Client is already connected — "
                        "this instance's Client object was left half-started by a "
                        "previous failed attempt. Forcing a clean stop before retrying."
                    )
                    try:
                        await super().stop()
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                else:
                    logger.error(f"Critical Startup Error: {e}")
                    await asyncio.sleep(15)

        me = await self.get_me()

        try:
            user_count = await db.total_users_count()
            logger.info(f"MongoDB Connected: {user_count} users found.")
        except Exception as e:
            logger.error(f"DB stats failed: {e}")
            user_count = "Unknown"

        try:
            await db.ensure_filestore_indexes()
        except Exception as e:
            logger.warning(f"File Store index setup did not complete: {e}")

        try:
            await db.ensure_stream_link_indexes()
        except Exception as e:
            logger.warning(f"File-to-Link index setup did not complete: {e}")

        now = datetime.datetime.now(IST)
        startup_text = (
            f"<blockquote>{E_ROCKET} <b>ʙᴏᴛ sᴜᴄᴄᴇssғᴜʟʟʏ sᴛᴀʀᴛᴇᴅ!</b>\n\n"
            f"{E_STAR} <b>ʙᴏᴛ:</b> @{me.username}\n"
            f"{E_USERS} <b>ᴜsᴇʀs:</b> <code>{user_count} / 200</code>\n"
            f"{E_CLOCK} <b>ᴛɪᴍᴇ:</b> <code>{now.strftime('%I:%M %p')} IST</code>\n\n"
            f"{E_CROWN} <b>ᴅᴇᴠᴇʟᴏᴘᴇᴅ ʙʏ @ᴀɴᴜᴊᴇᴅɪᴛs76</b></blockquote>"
        )

        try:
            await self.send_message(LOG_CHANNEL, startup_text, parse_mode=enums.ParseMode.HTML)
            logger.info("Startup log sent.")
        except Exception as e:
            logger.error(f"Failed to send startup log: {e}")

        await self.set_bot_commands_list()

        try:
            from Akbots.autopost import schedule_autopost
            schedule_autopost(self)

            from Akbots.backup import schedule_db_backup
            schedule_db_backup(self)
        except Exception as e:
            logger.warning(f"AutoPost scheduler did not start: {e}")

        try:
            from Akbots.rss import schedule_rss
            schedule_rss(self)
        except Exception as e:
            logger.warning(f"RSS scheduler did not start: {e}")

        try:
            from Akbots.uptime_monitor import schedule_uptime
            schedule_uptime(self)
        except Exception as e:
            logger.warning(f"Uptime Monitor scheduler did not start: {e}")

        try:
            from Akbots.forward_engine import start_forwarding
            restored = 0
            async for user in db.col.find({"forward_mode": True}):
                try:
                    ok, _ = await start_forwarding(user["id"])
                    if ok:
                        restored += 1
                except Exception as e:
                    logger.warning(f"Forward Engine: couldn't restore user {user.get('id')}: {e}")
            if restored:
                logger.info(f"Forward Engine: resumed live forwarding for {restored} user(s).")
        except Exception as e:
            logger.warning(f"Forward Engine restore did not run: {e}")

        try:
            from Akbots.jdownloader_core import jdownloader
            jdownloader.boot()
        except Exception as e:
            logger.warning(f"JDownloader did not start: {e}")

        try:
            from Akbots.aria2_rpc import daemon as aria2_rpc_daemon
            aria2_rpc_daemon.boot()
        except Exception as e:
            logger.warning(f"aria2 RPC daemon did not start (/rpcadd will be unavailable): {e}")

        try:
            from Akbots.anime import schedule_anime_poster
            schedule_anime_poster(self)
        except Exception as e:
            logger.warning(f"Anime auto-poster scheduler did not start: {e}")

        try:
            from Akbots.hdhub import schedule_hdhub_autopost
            schedule_hdhub_autopost(self)
        except Exception as e:
            logger.warning(f"HDhub auto-post scheduler did not start: {e}")

        try:
            from Akbots.ott_updates import schedule_ott_updates
            schedule_ott_updates(self)
        except Exception as e:
            logger.warning(f"OTT Updates scheduler did not start: {e}")

        try:
            from Akbots.anime_watcher import schedule_anime_watch
            schedule_anime_watch(self)
        except Exception as e:
            logger.warning(f"Anime-watch scheduler did not start: {e}")

        try:
            from Akbots.titanium import boot_personal_bots, register_managed_bot_handler
            await boot_personal_bots()
            register_managed_bot_handler(self)
        except Exception as e:
            logger.warning(f"Titanium personal-bot reconnect did not complete: {e}")

    async def stop(self, *args):
        try:
            await self.send_message(
                LOG_CHANNEL,
                f"<b>{E_STOP} Bot is going Offline.</b>",
                parse_mode=enums.ParseMode.HTML
            )
        except Exception as e:
            logger.debug(f"stop: failed to send offline notice to LOG_CHANNEL: {e}")
        await asyncio.shield(super().stop())
        logger.info("Bot stopped cleanly")

    async def set_bot_commands_list(self):
        commands = [
    BotCommand("start",         "🚀 Start the bot"),
    BotCommand("help",          "❓ Show help"),
    BotCommand("aiulta",        "🤖 AI agent — tell it to do anything in plain words"),
    BotCommand("arena",         "⚔️ Compare two AI models on the same prompt"),
    BotCommand("coach",         "🎙️ Practice chat/discussion/interview/debate with AI"),
    BotCommand("enhance",       "✨ Rewrite a rough prompt into a better one"),
    BotCommand("summarize",     "📄 Analyze/summarize one or many documents, PDF export included"),
    BotCommand("mastervideo",   "🎞️ Upscale/master a video to 1080p/2K/4K/8K (HEVC 10-bit)"),
    BotCommand("login",         "🔐 Login"),
    BotCommand("logout",        "🚪 Logout"),
    BotCommand("jiocinema",     "🎬 Download JioCinema movies"),
    BotCommand("kuku",          "🎬 KukuTV/KukuFM — search, browse popular, or paste a link"),
    BotCommand("kukucancel",    "🚫 Stop a running KukuTV download"),
    BotCommand("cancel",        "🚫 Cancel current action"),
    BotCommand("vc",            "🎤 Generate a voice chat room link"),
    BotCommand("myplan",        "📋 Check your plan"),
    BotCommand("premium",       "⭐ Premium info"),
    BotCommand("broadcast",     "📢 Broadcast message (admin only)"),
    BotCommand("setchat",       "💬 Set target chat"),
    BotCommand("set_channel_id","📡 Link a custom channel/group for files"),
    BotCommand("channel_id","📋 List your linked channels/groups"),
    BotCommand("del_channel_id","🗑 Unlink a custom channel/group"),
    BotCommand("akanager",     "🚀 Manager control panel"),
    BotCommand("addsource",     "➕ Add forward source channel"),
    BotCommand("addtarget",     "➕ Add forward target channel"),
    BotCommand("forwardmode",   "🔁 Toggle live forwarding on/off"),
    BotCommand("forwardstatus", "📊 Forward Engine status"),
    BotCommand("set_thumb",     "🖼️ Set thumbnail"),
    BotCommand("view_thumb",    "👁️ View thumbnail"),
    BotCommand("del_thumb",     "🗑️ Delete thumbnail"),
    BotCommand("change_thumb",  "🔄 Change thumbnail of a video/document"),
    BotCommand("merge",         "🎬 Start a video merge session"),
    BotCommand("mergedone",     "✅ Merge queued videos & send result"),
    BotCommand("mergestatus",   "📋 Show videos queued in merge session"),
    BotCommand("mergecancel",   "🚫 Cancel current merge session"),
    BotCommand("set_caption",   "✏️ Set caption"),
    BotCommand("see_caption",   "📄 View caption"),
    BotCommand("del_caption",   "❌ Delete caption"),
    BotCommand("set_del_word",  "➕ Add delete word"),
    BotCommand("rem_del_word",  "➖ Remove delete word"),
    BotCommand("set_repl_word", "🔄 Add replace word"),
    BotCommand("rem_repl_word", "🔃 Remove replace word"),
    BotCommand("add_premium",   "👑 Add premium to user (admin only)"),
    BotCommand("remove_premium","💔 Remove premium from user (admin only)"),
    BotCommand("ban",           "🔨 Ban a user"),
    BotCommand("unban",         "✅ Unban a user"),
    BotCommand("create_repo",   "🐙 Create a new GitHub repo (admin)"),
    BotCommand("delrepo",       "🐙 Delete a GitHub repo (admin)"),
    BotCommand("downloadrepo",  "🐙 Clone + zip + send a GitHub repo (admin)"),
    BotCommand("fork",          "🐙 Fork a single GitHub repo (admin)"),
    BotCommand("forkall",       "🐙 Fork all repos of a GitHub user (admin)"),
    BotCommand("add_collaborator",    "🐙 Add a GitHub repo collaborator (admin)"),
    BotCommand("remove_collaborator", "🐙 Remove a GitHub repo collaborator (admin)"),
    BotCommand("gitprivate",    "🐙 Set a GitHub repo to private (admin)"),
    BotCommand("gitpublic",     "🐙 Set a GitHub repo to public (admin)"),
    BotCommand("myuses",        "📊 My today's usage"),
    BotCommand("movieinfo",     "🎬 Movie info (admin, needs TMDB key)"),
    BotCommand("poster",        "🖼️ Movie poster (admin, needs TMDB key)"),
    BotCommand("autorename",    "📝 Set auto-rename template"),
    BotCommand("see_autorename","🔎 View auto-rename template"),
    BotCommand("del_autorename","🗑️ Delete auto-rename template"),
    BotCommand("set_prefix",    "➕ Set filename prefix"),
    BotCommand("del_prefix",    "➖ Remove filename prefix"),
    BotCommand("set_suffix",    "➕ Set filename suffix"),
    BotCommand("del_suffix",    "➖ Remove filename suffix"),
    BotCommand("set_metadata",  "🏷️ Set metadata text"),
    BotCommand("apply_metadata","🏷️ Apply metadata (reply to file)"),
    BotCommand("mediainfo",     "📊 Get file's technical info (reply to file)"),
    BotCommand("extract_audio", "🎵 Extract audio as MP3 (reply to video)"),
    BotCommand("set_watermark", "💧 Set watermark text"),
    BotCommand("watermark_position", "💧 Set watermark position"),
    BotCommand("apply_watermark", "💧 Apply watermark (reply to video)"),
    BotCommand("spoiler",       "🙈 Toggle spoiler blur / blur one file (reply)"),
    BotCommand("screenshots",   "🖼️ Generate N screenshots (reply to video)"),
    BotCommand("autoscreenshots","🖼️ Auto-send screenshots after every video upload"),
    BotCommand("sample",        "🎞️ Generate a short sample clip (reply to video)"),
    BotCommand("autosample",    "🎞️ Auto-send a sample clip before every video"),
    BotCommand("tovideo",       "🎬 Resend a document-video as a playable video"),
    BotCommand("todocument",    "📄 Resend a video as a plain document"),
    BotCommand("tomp4",         "🔁 Convert mkv/avi/flv/webm/wmv to real .mp4"),
    BotCommand("encode",        "🎛️ Re-encode a video — resolution/codec/quality (reply to video)"),
    BotCommand("compress",      "🗜️ Compress a video — Fast/Balanced/HEVC/Max presets (reply to video)"),
    BotCommand("trim",          "✂️ Trim a video to a start/end time (reply to video)"),
    BotCommand("setcookies",    "🍪 Set cookies for a domain (admin only)"),
    BotCommand("listcookies",   "🍪 List domains with custom cookies (admin only)"),
    BotCommand("delcookies",    "🍪 Delete cookies for a domain (admin only)"),
    BotCommand("unzip",         "📦 Extract an archive (reply to file)"),
    BotCommand("zip",           "🗜️ Start a zip session"),
    BotCommand("zipformat",     "🗜️ Pick archive format — zip/7z/tar/tar.gz/tar.bz2/rar"),
    BotCommand("zipname",       "✏️ Set zip archive name"),
    BotCommand("zippass",       "🔒 Password-protect the zip/7z (AES-256 by default)"),
    BotCommand("zipencryption", "🔐 Set ZIP encryption mode — aes256/aes128/zipcrypto"),
    BotCommand("zipfolder",     "📁 Organize queued files into folders inside the archive"),
    BotCommand("zipfiles",      "📋 View files queued in your zip session"),
    BotCommand("donezip",       "✅ Build and send the zip"),
    BotCommand("zipcancel",     "🚫 Cancel current zip session"),
    BotCommand("fwd",           "➡️ Forward a message id range"),
    BotCommand("reset",         "♻️ Reset all forward settings"),
    BotCommand("unequify",      "🧹 Delete duplicate messages in a chat"),
    BotCommand("unequifycancel","🚫 Stop a running unequify scan"),
    BotCommand("fwdresume",     "⏯️ Resume last forward job"),
    BotCommand("fwdstatus",     "📊 Show forward job status"),
    BotCommand("fwdcancel",     "🚫 Stop running forward job"),
    BotCommand("fwd_login",     "🔐 Login your account for forwarding"),
    BotCommand("rmsource",      "➖ Remove a forward source channel"),
    BotCommand("sources",       "📋 List forward source channels"),
    BotCommand("rmtarget",      "➖ Remove a forward target channel"),
    BotCommand("targets",       "📋 List forward target channels"),
    BotCommand("fwd_caption",   "✏️ Set forward caption"),
    BotCommand("fwd_button",    "🔘 Set forward button"),
    BotCommand("fwd_filter",    "🧰 Set forward media type filters"),
    BotCommand("fwd_settings",  "⚙️ View/manage all forward settings"),
    BotCommand("channels",      "📡 List connected channels"),
    BotCommand("addroute",      "➕ Add a channel route"),
    BotCommand("delroute",      "➖ Remove a channel route"),
    BotCommand("titanium",      "⚡ Titanium Clone Mode — connect your bots"),
    BotCommand("addbot",        "⚡ Connect a bot token to Titanium"),
    BotCommand("delbot",        "⚡ Disconnect a Titanium bot"),
    BotCommand("yta",           "🎵 Download YouTube audio (mp3)"),
    BotCommand("search",        "🔎 Search YouTube"),
    BotCommand("anime",         "📺 Search & download anime episodes (SubsPlease)"),
    BotCommand("aniworldurl",   "🇩🇪 Resolve/download from aniworld.to, s.to & 7 more (paste URL)"),
    BotCommand("freeflix",      "🇫🇷 Search Anime-Sama/Coflix/French-Stream & download"),
    BotCommand("freeflixurl",   "🇫🇷 Resolve/download from a direct Anime-Sama/Coflix/French-Stream URL"),
    BotCommand("goldenstream",  "🌐 Resolve a stream by TMDB id / AniList id (Vidlink, Hexa, Videasy, etc.)"),
    BotCommand("animedl",       "🈂️ Search anime_downloader's ~15 sites & download"),
    BotCommand("animedlurl",    "🈂️ Resolve/download from a direct anime_downloader-supported URL"),
    BotCommand("anime1v",       "🎌 Search Anime1v (AnimeFLV/JKAnime/TioAnime/+4 more) & download"),
    BotCommand("peliapi",       "🎬 Search PeliApi (PelisPlus/Cuevana/+5 more) & download"),
    BotCommand("status",        "📊 Bot status"),
    BotCommand("storageinfo",   "💾 Disk, cache & video storage stats (alias: /storage)"),
    BotCommand("about",         "ℹ️ About this bot"),
    BotCommand("pay",           "💳 Buy premium"),
    BotCommand("token",         "🔑 Redeem a token"),
    BotCommand("referral",      "🤝 Your referral link & stats"),
    BotCommand("transfer",      "🔁 Transfer premium to another user"),
    BotCommand("genlink",       "🔗 Generate a file share link (admin)"),
    BotCommand("batch",         "💯 Generate a batch share link (admin)"),
    BotCommand("dbchannels",    "📡 List multi-DB storage channels (admin)"),
    BotCommand("adddbchannel",  "➕ Add a DB storage channel (admin)"),
    BotCommand("deldbchannel",  "➖ Remove a DB storage channel (admin)"),
    BotCommand("multidb",       "🔀 Toggle multi-DB round robin (admin)"),
    BotCommand("autobatch",     "📦 Toggle/configure auto-batch (admin)"),
    BotCommand("shortener",     "🔗 Toggle the URL shortener gate (admin)"),
    BotCommand("uploadmode",    "📤 Toggle auto-link-on-upload (admin)"),
    BotCommand("autogenerate",  "🎛️ Toggle auto-generate missing qualities (admin)"),
    BotCommand("ghdl",          "🐙 Download a GitHub release asset / repo / file"),
    BotCommand("hfdl",          "🤗 Download a HuggingFace repo / file"),
    BotCommand("rpcadd",        "🔄 Queue a download on the aria2 RPC engine (pause/resume/edit)"),
    BotCommand("rpctasks",      "📋 List your active aria2 RPC tasks"),
    BotCommand("rpcinfo",       "🔌 aria2 RPC daemon status & connection info (admin)"),
    BotCommand("uptime",        "🌐 Uptime Monitor — watch a URL, get alerted when it goes down"),
    BotCommand("imgtolink",     "🖼️ Upload an image and get a permanent direct link"),
    BotCommand("imgurl",        "🔗 Upload an image URL and get a direct link"),
]
        await self.set_bot_commands(commands[:100])


BotInstance = Bot()


@BotInstance.on_message(filters.private & filters.incoming, group=-1)
async def new_user_log(bot: Client, message: Message):
    user = message.from_user
    if not user or user.id in USER_CACHE:
        # MUST continue propagation, not just `return`. This filter
        # matches EVERY private incoming message from EVERY user, and
        # this handler lives in bot.py — loaded before any Akbots plugin,
        # so it's the very first group=-1 handler to run. A bare `return`
        # here (true for any returning user, i.e. almost everyone after
        # their first /start) silently swallowed every subsequent private
        # message bot-wide: bot tokens for "Add Bot Using Token", channel
        # links/forwards for "Add Channel", cookies pastes, everything
        # that relied on a lower-priority handler (cookies_manager.py,
        # imgtolink.py, direct_utils.wait_for_reply, etc.) ever seeing it.
        message.continue_propagation()

    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        now = datetime.datetime.now(IST)
        log_text = (
            f"<blockquote>{E_USERS} <b>#ɴᴇᴡᴜsᴇʀ</b>\n"
            f"{E_STAR} <b>ᴜsᴇʀ:</b> {user.mention}\n"
            f"{E_INFO} <b>ɪᴅ:</b> <code>{user.id}</code>\n"
            f"{E_CLOCK} <b>ᴛɪᴍᴇ:</b> {now.strftime('%I:%M %p')} IST</blockquote>"
        )
        try:
            await bot.send_message(LOG_CHANNEL, log_text, parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            logger.debug(f"new user log: failed to send to LOG_CHANNEL: {e}")

    USER_CACHE.add(user.id)
    message.continue_propagation()  # this handler only logs — never consumes the message


@BotInstance.on_message(filters.command("refreshcmds") & filters.user(ADMINS))
async def update_commands(bot: Client, message: Message):
    """Admin-only: pushes the BotFather commands-menu list. Named
    /refreshcmds (not /cmd) to avoid colliding with Akbots/start.py's
    public /cmd (plain-text dump of every command) — both used to be
    registered under /cmd, which meant only whichever handler Pyrogram
    happened to check first would ever fire for admins."""
    try:
        await bot.set_bot_commands_list()
        await message.reply_text(
            f"<b>{E_CHECK} Commands menu updated!</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(
            f"<b>{E_CROSS} Error:</b> {e}",
            parse_mode=enums.ParseMode.HTML
        )


if __name__ == "__main__":
    BotInstance.run()
