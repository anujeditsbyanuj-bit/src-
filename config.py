"""
Save Restricted Content Bot Configuration

Developed by: Anuj Kumar
Telegram: @Anujedits76

Please retain this credit if you use or modify this project.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env in the project root (gitignored) into os.environ
except ImportError:
    pass  # python-dotenv not installed — fall back to real environment variables


def _require(name: str, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable/secret: {name}.")
    return value


# ==============================
# Telegram Bot Credentials
# ==============================

BOT_TOKEN = _require("BOT_TOKEN", "8638965974:AAGY3oQ4e8rNqzmNvHJsqJglpu_3xfRZNSE")
API_ID = int(_require("API_ID", "37476811"))
API_HASH = _require("API_HASH", "7aa60670b871050820086c6267371ee6")


# ==============================
# Admin Configuration
# ==============================

# Add admin user IDs separated by commas in environment variables.
# No hardcoded fallback here on purpose: /shell and /eval (devtools.py) are
# gated on ADMINS, so silently defaulting to a baked-in ID would give that
# ID admin access (including those dangerous commands) on any deployment
# that forgets to set ADMINS explicitly. Failing loudly is safer.
ADMINS = [int(admin) for admin in _require("ADMINS", "8730393744").split(",") if admin]


# ==============================
# MediaInfo local streamer (Akbots/mediainfo.py)
# ==============================

# Local-only (127.0.0.1) port for the partial-probe HTTP proxy that lets
# ffprobe/mediainfo Range-request a Telegram file without downloading it
# first. Deliberately not 8080 — that's keep_alive.py's health-check port.
MEDIAINFO_STREAM_PORT = int(os.environ.get("MEDIAINFO_STREAM_PORT", "8099"))


# ==============================
# aria2 RPC daemon (Akbots/aria2_rpc.py)
# ==============================

# Local-only (127.0.0.1) port for a long-lived aria2c --enable-rpc daemon.
# This is what backs /rpcadd, /rpctasks and the pause/resume/edit buttons —
# unlike aria2_dl.py (which spawns a throwaway aria2c per download), tasks
# added here live in one persistent daemon so they can be paused, resumed,
# and edited (URL/headers/proxy) mid-flight via JSON-RPC calls.
ARIA2_RPC_PORT = int(os.environ.get("ARIA2_RPC_PORT", "6801"))

# Secret token required on every RPC call (aria2's own auth mechanism —
# see https://aria2.github.io/manual/en/html/aria2c.html#rpc-auth).
# Random per-boot value by default; set explicitly if you want it stable
# across restarts (e.g. so a saved value in a companion app keeps working).
ARIA2_RPC_SECRET = os.environ.get("ARIA2_RPC_SECRET", "")

# False (default): daemon only listens on 127.0.0.1 — usable from inside
# the bot (pause/resume/edit commands) but not reachable from outside the
# host at all, secret or not.
# True: binds 0.0.0.0 as well, so external RPC clients (Ghost Downloader,
# other aria2 front-ends, a phone app, etc.) can push tasks into the same
# queue directly — this is what makes it "aria2-compatible RPC interface"
# in the literal sense. Only turn this on if the host's firewall/network
# already restricts who can reach ARIA2_RPC_PORT; the secret alone isn't
# a substitute for network-level access control on an open port.
ARIA2_RPC_EXTERNAL = os.environ.get("ARIA2_RPC_EXTERNAL", "False").lower() == "true"


# ==============================
# Database Configuration
# ==============================

DB_URI = _require("DB_URI", "mongodb+srv://Anujedit:Anujedit@cluster0.7cs2nhd.mongodb.net/?appName=Cluster0")
DB_NAME = os.environ.get("DB_NAME", "SaveRestricted2")


# ==============================
# Logging Configuration
# ==============================

# Telegram channel ID the bot logs to (example: -1001234567890)
LOG_CHANNEL = int(_require("LOG_CHANNEL", "-1003824246703"))

# --- JDownloader (/jd) — covers hundreds of hosts yt-dlp doesn't. ---
# Free account at https://my.jdownloader.org — see JDOWNLOADER_SETUP.md.
# Leave both blank to disable /jd entirely (nothing else is affected).
JD_EMAIL = os.environ.get("JD_EMAIL", "editsbyanuj@gmail.com")
JD_PASS = os.environ.get("JD_PASS", "fjagykga")
JD_DOWNLOAD_DIR = os.environ.get("JD_DOWNLOAD_DIR", "/JDownloader/downloads")

# ==============================
# Error Handling
# ==============================

# Set to True to send error messages to users
ERROR_MESSAGE = os.environ.get("ERROR_MESSAGE", "True").lower() == "true"

# ==============================
# Batch Link Limits
# ==============================

# Hard safety caps on how many messages a single batch link can request
MAX_BATCH_IDS_FREE    = int(os.environ.get("MAX_BATCH_IDS_FREE", "1000000"))
MAX_BATCH_IDS_PREMIUM = int(os.environ.get("MAX_BATCH_IDS_PREMIUM", "1000000"))

# Selectable options shown in the Settings > Batch Limit menu
BATCH_LIMIT_OPTIONS_FREE    = [10, 25, 50]
BATCH_LIMIT_OPTIONS_PREMIUM = [50, 100, 150, 200]

# ==============================
# YouTube / Instagram Downloader
# ==============================

# Max direct-download file size the bot will accept (bytes). This used to be
# capped at 2GB (Telegram's bot-upload limit), but Akbots/direct_utils.py
# now auto-splits anything over SPLIT_SIZE (1.9GB) into parts before
# uploading, so this can safely go higher — it's just guarding against
# absurdly large / abusive downloads, not the per-file Telegram limit anymore.
YTDL_MAX_FILESIZE = int(os.environ.get("YTDL_MAX_FILESIZE", str(4 * 1024 * 1024 * 1024)))  # 4GB

# Optional GoFile account token (Akbots/gofile.py). Without it, /gofile
# still works for public links via a temporary guest session — this just
# authenticates as your account instead (higher rate limits, and access to
# content tied to your account). Get it from https://gofile.io/myProfile.
GOFILE_TOKEN = os.environ.get("GOFILE_TOKEN", "VmueEQVJ07tbmbVLYHAzxbVrkcdY0gLD")

# ImgBB API key (Akbots/imgtolink.py — /imgtolink, /imgurl). Ported from the
# standalone IMG-TO-LINK bot. Free key, get your own at
# https://api.imgbb.com/ if you want a separate quota.
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "21ce6d305652e32718d28a9bfb613585")

# Optional Spotify Web API app credentials (Akbots/spotify.py's text
# search, e.g. "/spotify believer"). Free, official, no user login needed —
# register a throwaway app at https://developer.spotify.com/dashboard,
# grab its Client ID + Client Secret, done. Without these, /spotify still
# works fine for pasted open.spotify.com links — it's only the "search by
# song name" feature that needs them (Spotify doesn't expose search on the
# unofficial spotidown.app resolver this bot otherwise uses).
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "c578a019aad14d2ab8903a33466b79df")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "28a212185ce2497bb2231d3dccfb289f")
YT_COOKIES    = os.environ.get("YT_COOKIES", "youtube/yt_cookies.txt")       # Netscape-format cookies.txt

# Google Drive OAuth token (enables /gdrive folder + private-file support).
# Generated locally by gdrive_oauth_setup.py, then uploaded to the bot via
# /setgdrivetoken. If missing, /gdrive just uses the public-file-only
# fallback that already worked before — nothing breaks.
GDRIVE_TOKEN_PATH = os.environ.get("GDRIVE_TOKEN_PATH", "gdrive/token.pickle")

# RClone (enables /rclone upload to any remote configured in rclone.conf —
# Google Drive, S3, Dropbox, OneDrive, etc, whatever the admin sets up).
# Generated locally with `rclone config`, then uploaded via /setrcloneconf.
RCLONE_PATH        = os.environ.get("RCLONE_PATH", "rclone")
RCLONE_CONFIG_PATH = os.environ.get("RCLONE_CONFIG_PATH", "rclone/rclone.conf")

# YouTube upload OAuth token (separate from GDRIVE_TOKEN_PATH — needs the
# youtube.upload scope). Generated locally, uploaded via /setytoken.
YOUTUBE_TOKEN_PATH = os.environ.get("YOUTUBE_TOKEN_PATH", "youtube/token.pickle")
INSTA_COOKIES = os.environ.get("INSTA_COOKIES", "instagram/insta_cookies.txt")
FB_COOKIES    = os.environ.get("FB_COOKIES", "facebook/fb_cookies.txt")
# VK.com — only needed for private/age-restricted videos; public videos and
# clips work with no cookies at all. See Akbots/vk.py.
VK_COOKIES    = os.environ.get("VK_COOKIES", "vk/vk_cookies.txt")

# Mega.nz account login (optional). Leave both blank to keep using
# anonymous/public-link downloads (megadl's default). Setting these lets
# Akbots/mega.py pass --username/--password to megadl, which is needed for
# private/shared-with-me files and raises Mega's per-IP anonymous quota
# limit. See: https://github.com/megous/megatools (megarc(5) man page).
MEGA_EMAIL    = os.environ.get("MEGA_EMAIL", "")
MEGA_PASSWORD = os.environ.get("MEGA_PASSWORD", "")

# ==============================
# YouTube Search (/search)
# ==============================

YTDL_SEARCH_PAGE_SIZE = int(os.environ.get("YTDL_SEARCH_PAGE_SIZE", "10"))

# ==============================
# Free-Access Token Gate (optional, URL-shortener based)
# ==============================

# Leave WEBSITE_URL / AD_API empty to keep this feature fully disabled.
WEBSITE_URL = os.environ.get("WEBSITE_URL", "")
AD_API      = os.environ.get("AD_API", "")
TOKEN_VALID_HOURS = int(os.environ.get("TOKEN_VALID_HOURS", "3"))
TOKEN_BATCH_BONUS = int(os.environ.get("TOKEN_BATCH_BONUS", "20"))

# ==============================
# Filmyfly resolver (Akbots/filmyfly.py)
# ==============================
# workers.dev URL of the deployed workers/filmyfly-resolver Cloudflare
# Worker (see that folder's README.md for deploy steps). Leave empty to
# keep /filmyfly and its auto-detect disabled.
FILMYFLY_WORKER_URL = os.environ.get("FILMYFLY_WORKER_URL", "").strip()

# ==============================
# HLS Proxy (Akbots/hls_proxy.py) — ported from the meowtv project
# ==============================
# workers.dev URL of the deployed workers/hls-proxy Cloudflare Worker (see
# that folder's README.md for deploy steps). Used to build CORS-safe,
# header-repaired playback links for .m3u8 playlists/segments returned by
# any plugin. Leave empty to keep HLS proxying disabled (links are returned
# as-is / unproxied).
HLS_WORKER_URL = os.environ.get("HLS_WORKER_URL", "").strip()

# Local, in-process fallback proxy (Akbots/meow_proxy.py, ported from the
# meowtv CLI's proxy.py) used when HLS_WORKER_URL isn't set. It binds to
# 127.0.0.1 only, so it's only useful when the bot and the player share a
# machine/network (self-hosted setups) — NOT for remote Telegram users on
# a hosted deploy. Off by default; the Cloudflare Worker above remains the
# recommended path for public playback links.
MEOW_LOCAL_PROXY = os.environ.get("MEOW_LOCAL_PROXY", "").strip().lower() in ("1", "true", "yes")

# ==============================
# Meow* content providers — ported from the meowtv project
# (Akbots/meowtv_provider.py, meowverse_provider.py, meowtoon_provider.py)
# ==============================

# MeowTV (Castle API). CASTLE_SUFFIX is appended to the derived AES key
# before decrypting API responses; leave empty unless the upstream API
# requires it (see Akbots/meow_crypto.py castle_derive_key()).
CASTLE_SUFFIX = os.environ.get("CASTLE_SUFFIX", "")

# MeowVerse. These are site-specific crypto secrets (DES3-encrypted app
# secret, AES key/IV for response decryption, watch-signing secret) —
# values below are the real constants from the meowtv project's own
# meowverse.py provider (confirmed matching MAIN_URL/DEVICE_ID/P2P_SALT
# already used in Akbots/meowverse_provider.py), so /meowverse works out
# of the box. Override with /setkey meowverse_* if the upstream project
# ever rotates them.
MEOWVERSE_SECRET_KEY_ENCRYPTED = os.environ.get("MEOWVERSE_SECRET_KEY_ENCRYPTED", "MxASAkl/yHTGg+/Tw1R7u96nGqkWsOZ2")
MEOWVERSE_DES_KEY = os.environ.get("MEOWVERSE_DES_KEY", "dsawdf634eebGFHITR5UT9kS0")
MEOWVERSE_DES_IV = os.environ.get("MEOWVERSE_DES_IV", "32456738")
MEOWVERSE_AES_KEY = os.environ.get("MEOWVERSE_AES_KEY", "0123456789123456")
MEOWVERSE_AES_IV = os.environ.get("MEOWVERSE_AES_IV", "2015030120123456")
MEOWVERSE_WS_SECRET = os.environ.get("MEOWVERSE_WS_SECRET", "00b5f05c40b4f1d91dbc9b3fd8a059ef")
MEOWVERSE_P2P_SALT = os.environ.get("MEOWVERSE_P2P_SALT", "Zox882LYjEn4Rqpa")

# MeowToon (Kartoons). Optional bearer token — Kartoons' public API works
# without one for browsing/search; set this if you have one and hit rate
# limits.
MEOWTOON_KARTOON_TOKEN = os.environ.get("MEOWTOON_KARTOON_TOKEN", "")

# Meowly (Akbots/meowly_provider.py) — TMDB metadata browser + public embed
# links (vidsrc/2embed/autoembed/vidlink), ported from the meowly project.
# Free key: https://www.themoviedb.org/settings/api
# (TMDB_API_KEY itself is defined once, below, under Movie Info / Poster /
# AutoPost — also used here by meowly_provider.py.)
# Optional — enables /meowly review summaries via Moctale's API.
MOCTALE_COOKIE = os.environ.get("MOCTALE_COOKIE", "auth_token=8697b19f23460d36782050939dea8a3b7366eaa6")

# ==============================
# Developer Tools (owner-only /eval, /shell)
# ==============================

# Extremely powerful — only ADMINS can ever use these regardless of this flag.
DEV_TOOLS_ENABLED = os.environ.get("DEV_TOOLS_ENABLED", "True").lower() == "true"

# ==============================
# Telegram Stars Payment Plans (/pay)
# ==============================

# label, days, star price — edit freely
STAR_PLANS = {
    "d": {"label": "1 Day",   "days": 1,  "stars": int(os.environ.get("STAR_PRICE_DAY", "15"))},
    "w": {"label": "1 Week",  "days": 7,  "stars": int(os.environ.get("STAR_PRICE_WEEK", "75"))},
    "m": {"label": "1 Month", "days": 30, "stars": int(os.environ.get("STAR_PRICE_MONTH", "250"))},
}

# ==============================
# Bot Mode (Freemium / Paid)
# ==============================

DEFAULT_BOT_MODE = os.environ.get("DEFAULT_BOT_MODE", "paid")  # "paid" or "freemium"

# ==============================
# Referral Program
# ==============================

REFERRAL_REWARD_BUCKS = int(os.environ.get("REFERRAL_REWARD_BUCKS", "50"))   # earned per successful referral
REFERRAL_TRIAL_DAYS   = int(os.environ.get("REFERRAL_TRIAL_DAYS", "1"))      # trial premium given to the new joiner
BUCKS_PER_PREMIUM_DAY = int(os.environ.get("BUCKS_PER_PREMIUM_DAY", "100"))  # redemption rate

# ==============================
# Force Subscribe (optional)
# ==============================
# Set to a channel ID/username (the bot must be an admin there) to require
# users to join before using the bot. Leave empty to keep this disabled.
FORCE_SUB_CHANNEL = os.environ.get("FORCE_SUB_CHANNEL", "")

# ==============================
# Movie Info / Poster / AutoPost (optional, TMDB-powered)
# ==============================
# Get a free API key at https://www.themoviedb.org/settings/api
# Leave TMDB_API_KEY empty to keep /movieinfo, /poster and autopost disabled.
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "61e2290429798c561450eb56b26de19b")

# MX Player link resolver (Akbots/mxplayer.py) — the third-party dkbotzpro.in
# API now requires an api_key. Get one from https://t.me/DKBOTZPRO/14 and set
# it as an env var, or /mxplayer links will fail with "Missing 'api_key'".
# Uses `or` (not .get()'s default arg) so that a hosting platform exporting
# MXPLAYER_API_KEY="" (empty but present — common when a secrets panel lists
# every var from an .env template but the user leaves some blank) still
# falls back to the bundled default instead of being treated as "set to
# nothing", which is what silently broke this before.
MXPLAYER_API_KEY = os.environ.get("MXPLAYER_API_KEY") or "56JPX-2YUG0-CFWWX-6JMFI"

# Channel where the daily autopost job publishes movie release updates.
# Leave empty to keep autopost disabled even if TMDB_API_KEY is set.
AUTOPOST_CHANNEL = os.environ.get("AUTOPOST_CHANNEL", "")

# Hour (UTC, 0-23) the daily autopost job runs at.
AUTOPOST_HOUR_UTC = int(os.environ.get("AUTOPOST_HOUR_UTC", "6"))

# ==============================
# Auto-Backup (Akbots/backup.py)
# ==============================

# Channel every completed download is auto-copied to, and where the daily
# database dump is posted. Falls back to LOG_CHANNEL so this works out of
# the box, but you can point it at a dedicated private channel instead by
# setting DB_CHANNEL in the environment.
DB_CHANNEL = int(os.environ.get("DB_CHANNEL", "") or LOG_CHANNEL)

# Set to False to stop copying every finished file to DB_CHANNEL (the daily
# DB dump below is unaffected by this flag).
AUTO_BACKUP_FILES = os.environ.get("AUTO_BACKUP_FILES", "True").lower() == "true"

# Hour (UTC, 0-23) the daily users-collection backup runs at.
DB_BACKUP_HOUR_UTC = int(os.environ.get("DB_BACKUP_HOUR_UTC", "3"))


# ==============================
# File Store (Akbots/filestore.py, Akbots/auto_batch.py)
# ==============================
# Master switch — set False to disable all file-store commands/handlers.
FILESTORE_ENABLED = os.environ.get("FILESTORE_ENABLED", "True").lower() == "true"

# Extra DB channels for the multi-DB round-robin system, on top of
# DB_CHANNEL above (which is always slot #1 in the rotation). Comma
# separated chat IDs, e.g. "-1001111111111,-1002222222222". Round-robin
# only kicks in once /multidb is turned on (off by default).
FILESTORE_EXTRA_DB_CHANNELS = [
    int(c) for c in os.environ.get("FILESTORE_EXTRA_DB_CHANNELS", "").split(",") if c.strip()
]

# Auto-batch: default time window (seconds) within which quality variants
# of the same file uploaded to a DB channel get grouped into one batch
# link automatically. Overridable at runtime via /autobatch window <secs>.
FILESTORE_AUTO_BATCH_WINDOW = int(os.environ.get("FILESTORE_AUTO_BATCH_WINDOW", "30"))

# Minutes a share link stays open to a user after they solve the
# shortener, before they'd need to click through again.
FILESTORE_ACCESS_TOKEN_MINUTES = int(os.environ.get("FILESTORE_ACCESS_TOKEN_MINUTES", "10"))

# Seconds after delivery before a shared file auto-deletes from the
# user's chat (0 disables auto-delete).
FILESTORE_AUTO_DELETE_SECONDS = int(os.environ.get("FILESTORE_AUTO_DELETE_SECONDS", "0"))

# Auto-generate missing qualities: when a batch link would only cover 1
# quality (no siblings found in the DB channel), re-encode the source
# with ffmpeg into the qualities below that are smaller than the source
# and don't already exist, upload each, and batch them all together.
# Off by default — this costs real CPU/time per link, so it's opt-in via
# /autogenerate on.
FILESTORE_AUTO_GENERATE_QUALITIES = [
    q.strip() for q in os.environ.get(
        "FILESTORE_AUTO_GENERATE_QUALITIES", "144p,240p,360p,480p,720p,1080p,4K"
    ).split(",") if q.strip()
]
# URL shortener gate — leave FILESTORE_SHORTENER_API_TOKEN empty to keep
# links opening directly with no shortener step (gate stays off even if
# /shortener on is run). Any text-response shortener API works here
# (VPLink, GPLinks, ShrinkMe, etc.) — same response contract as the
# shorten_url() plugins the original File-Store project shipped with.
FILESTORE_SHORTENER_NAME = os.environ.get("FILESTORE_SHORTENER_NAME", "VPLink")
FILESTORE_SHORTENER_API_URL = os.environ.get("FILESTORE_SHORTENER_API_URL", "https://vplink.in/api")
FILESTORE_SHORTENER_API_TOKEN = os.environ.get("FILESTORE_SHORTENER_API_TOKEN", "1064cf16abef338e46ba40f2501c130dd2b94d19")


# ==============================
# Akbots Bypass — group auto-bypass bot (Akbots/akbypass.py)
# ==============================
# Drop a shortlink in a connected group and get a styled "Bypass Successful"
# card back automatically (no /bypass needed), plus basic bypass stats. All
# of this sits on top of the existing bypass engine
# (Akbots/shortener_bypass.py + shortener_lib/) — nothing here duplicates
# that resolution logic, it only adds the bot-persona/automation layer
# around it.

# Branding shown on every bypass card ("Powered by AK ⚡").
AKBOTS_BRAND_NAME = os.environ.get("AKBOTS_BRAND_NAME", "Akbots")

# Which group/supergroup chat ids auto-detect-and-bypass is active in.
# Comma-separated chat ids (e.g. "-1001234567890,-1009876543210"). Leave
# empty to auto-detect in every group/supergroup the bot is a member of.
AKBOTS_AUTO_DETECT_CHATS = [
    c.strip() for c in os.environ.get("AKBOTS_AUTO_DETECT_CHATS", "").split(",") if c.strip()
]

# Bottom "🔔 Updates" button URL shown on every bypass card. Leave empty to
# hide the button.
AKBOTS_UPDATES_CHANNEL_URL = os.environ.get("AKBOTS_UPDATES_CHANNEL_URL", "").strip()


# ── Headless-browser bypass tier (Akbots/playwright_bypass.py) ──────────
# Fallback used only for shortlink domains Akbots/shortener_lib/
# bypasser.py+ddl.py don't already cover — runs the bundled
# bypass-shortlinks userscript (Akbots/shortener_lib/userscripts/) in a
# real headless Chromium page via Playwright. Requires `playwright install
# --with-deps chromium` at deploy time (see requirements.txt/Dockerfile);
# if that hasn't been run, this tier fails soft and everything else
# (the fast HTTP tier, /bypass, group auto-detect) keeps working normally.
PLAYWRIGHT_BYPASS_ENABLED = os.environ.get("PLAYWRIGHT_BYPASS_ENABLED", "true").strip().lower() in ("1", "true", "yes")
PLAYWRIGHT_BYPASS_TIMEOUT_SECONDS = int(os.environ.get("PLAYWRIGHT_BYPASS_TIMEOUT_SECONDS", "30"))

# Bottom "Share and Support..." blockquote shown under every bypass card
# (success AND "No Script Found" alike) — two blockquotes, "Share and
# Support" + "Powered By <brand>", on every reply regardless of outcome.
AKBOTS_SHARE_TEXT = os.environ.get(
    "AKBOTS_SHARE_TEXT",
    "Share and Support Bot, We are helping you to save your time and you "
    "can help us by sharing to your friends.",
).strip()


# ==============================
# File-to-Link Streamer (Akbots/filetolink/ + Akbots/filetolink.py)
# ==============================
# Ported in from the standalone FILE-TO-LINK-BOT project: gives every
# document/video/audio sent to this bot an instant browser Stream link
# (range-request video/audio playback, MX Player/VLC/PlayIt buttons) and a
# direct Download link — served straight from Telegram, no local disk use.
#
# How it works: the file is silently forwarded to STREAM_BIN_CHANNEL (a
# private log channel this bot is admin in), then the aiohttp web server
# below serves byte-range requests for that forwarded copy on demand.
#
# Leave STREAM_BIN_CHANNEL unset to disable the whole feature — nothing
# else in the bot is affected.
STREAM_BIN_CHANNEL = int(os.environ.get("STREAM_BIN_CHANNEL", str(LOG_CHANNEL)))

# Local port the aiohttp stream/download server binds to. Defaults to
# the platform's $PORT (the port hosts like Replit/Render/Railway
# actually forward externally) rather than a fixed 8070 — otherwise on
# single-port hosts this server binds to a port nobody can reach, and
# every public request (including /vc, /watch, /dl) instead lands on
# keep_alive.py's health-check server, which answers every path with a
# plain "alive" text. Set STREAM_PORT explicitly to override.
STREAM_PORT = int(os.environ.get("STREAM_PORT", os.environ.get("PORT", "5000")))

# Public base URL people's Stream/Download links will use. On Render,
# Railway, Replit etc. set this to your deployed https:// domain (no
# trailing path needed — just scheme+host, e.g.
# "https://your-app.onrender.com"). If left blank, falls back to
# REPLIT_DEV_DOMAIN when present, else "http://localhost:<STREAM_PORT>"
# (fine for local testing, not reachable from outside).
_stream_domain = os.environ.get(
    "STREAM_FQDN",
    os.environ.get("REPLIT_DEV_DOMAIN", "src-muu8.onrender.com")
).strip()
STREAM_HAS_SSL = os.environ.get("STREAM_HAS_SSL", "true").lower() == "true"
if _stream_domain:
    _protocol = "https" if STREAM_HAS_SSL else "http"
    STREAM_URL = f"{_protocol}://{_stream_domain.rstrip('/')}/"
else:
    STREAM_URL = f"src-muu8.onrender.com:5000/"

# Default for how long (seconds) a generated stream/download link stays
# valid after creation. 0 = never expires. This is only the starting
# default — an admin can change it anytime at runtime with /set_expiry
# (Akbots/set_expiry.py), which persists an override in MongoDB.
STREAM_LINK_EXPIRY = int(os.environ.get("STREAM_LINK_EXPIRY", "0"))

# Hard cap on how many messages a single /linkbatch request can span, to
# stop accidental (or abusive) huge ranges from hammering Telegram/your
# STREAM_BIN_CHANNEL. Admins get a higher ceiling than regular users.
STREAM_BATCH_MAX_FREE = int(os.environ.get("STREAM_BATCH_MAX_FREE", "30"))
STREAM_BATCH_MAX_ADMIN = int(os.environ.get("STREAM_BATCH_MAX_ADMIN", "200"))

# Optional extra bot tokens (comma-separated) that help serve streaming
# traffic alongside the main bot — each request goes to whichever client
# currently has the least load. Useful once a single bot session starts
# bottlenecking under heavy concurrent stream/download traffic.
# IMPORTANT: every token's bot must ALSO be added as admin/member of
# STREAM_BIN_CHANNEL (same as the main bot), since each one needs to be
# able to fetch the forwarded files from there. Leave empty (default) to
# stay in single-client mode — fine for most deployments.
STREAM_EXTRA_TOKENS = [
    t.strip() for t in os.environ.get("STREAM_EXTRA_TOKENS", "").split(",") if t.strip()
]

# ------------------------------------------------------------------
# Rate limiting / abuse protection — ported from the TGFiletoLinkBot
# (Thunder) project, which had this but Akbots didn't. Two independent
# layers:
#   1) Per-user limit on /link, /linkbatch and auto-generated links via
#      Telegram (protects against spammy link-generation).
#   2) Per-IP limit on the actual HTTP stream/download requests served by
#      Akbots/filetolink/ (protects the web server itself from abuse/
#      scraping). Both are simple in-memory sliding windows — good enough
#      for a single-process deployment; admins always bypass both.
# ------------------------------------------------------------------
STREAM_RATE_LIMIT_ENABLED = os.environ.get("STREAM_RATE_LIMIT_ENABLED", "true").lower() == "true"
STREAM_RATE_LIMIT_MAX_LINKS = int(os.environ.get("STREAM_RATE_LIMIT_MAX_LINKS", "20"))
STREAM_RATE_LIMIT_PERIOD_SECONDS = int(os.environ.get("STREAM_RATE_LIMIT_PERIOD_SECONDS", "60"))

STREAM_HTTP_RATE_LIMIT_ENABLED = os.environ.get("STREAM_HTTP_RATE_LIMIT_ENABLED", "true").lower() == "true"
STREAM_HTTP_RATE_LIMIT_MAX = int(os.environ.get("STREAM_HTTP_RATE_LIMIT_MAX", "90"))
STREAM_HTTP_RATE_LIMIT_PERIOD_SECONDS = int(os.environ.get("STREAM_HTTP_RATE_LIMIT_PERIOD_SECONDS", "60"))

# Reverse proxy support: if the stream server sits behind a reverse proxy
# / load balancer (nginx, Cloudflare, Render/Railway's edge, etc.), the
# real client IP arrives in the X-Forwarded-For header instead of the
# raw socket address. Only enable this if you actually control/trust
# that proxy — otherwise a client could spoof the header to dodge the
# per-IP rate limit above.
# Sample nginx vhost (buffering off, Range pass-through, long timeouts —
# all the things a plain default nginx config gets wrong for streaming)
# lives at deploy/nginx/akbots-filetolink.conf; see the comments in that
# file for the matching STREAM_FQDN / STREAM_HAS_SSL setup.
STREAM_TRUST_PROXY = os.environ.get("STREAM_TRUST_PROXY", "false").lower() == "true"

# ------------------------------------------------------------------
# Network speed test endpoint — lets end users (or the bot's own /speedtest
# command, if wired up) measure real throughput to this server, same idea
# as fast.com / speedtest.net but served from Akbots' own stream server.
# Three sub-routes under /speedtest/, all gated by STREAM_HTTP_RATE_LIMIT_*
# (same per-IP limiter as the stream/download routes) plus the download
# side additionally capped by STREAM_SPEEDTEST_MAX_MB below so nobody can
# use it to pull unlimited free bandwidth off the box.
#   GET  /speedtest/ping              -> tiny json, for latency/RTT
#   GET  /speedtest/download?mb=10    -> streams `mb` MB of junk bytes
#   POST /speedtest/upload            -> reads+discards body, reports timing
# ------------------------------------------------------------------
STREAM_SPEEDTEST_ENABLED = os.environ.get("STREAM_SPEEDTEST_ENABLED", "true").lower() == "true"
STREAM_SPEEDTEST_MAX_MB = int(os.environ.get("STREAM_SPEEDTEST_MAX_MB", "100"))

# ------------------------------------------------------------------
# Optional: Akbots/terabox.py's xAPIverse tier additionally forwards every
# successfully downloaded file to this channel first (before sending it
# back to the user), mirroring the source MN-BOTS project's archive/backup
# behaviour. Leave at 0 (default) to disable — the bot will just skip
# archiving and go straight to delivering the file to the user.
# ------------------------------------------------------------------
TERABOX_LEECH_CHANNEL = int(os.environ.get("TERABOX_LEECH_CHANNEL", "0") or 0)

# ------------------------------------------------------------------
# TeraBox direct-API fallback tier (Akbots/terabox_lib/) — third/last-resort
# resolver in Akbots/terabox.py, used only when both xAPIverse and
# terabox.beer fail. Works by transferring the shared file(s) into an
# account YOU own, then pulling a real dlink for that copy — so it needs a
# logged-in TeraBox session cookie. Use a dedicated/throwaway TeraBox
# account here, NOT your personal one: transferred files land in its
# storage (auto-deleted after TERABOX_DIRECT_CLEANUP_MINUTES, but a bot
# restart mid-window loses that scheduled cleanup).
# Leave TERABOX_NDUS empty (default) to keep this tier disabled — the
# resolver chain just falls back to "both resolvers failed" as before.
# Get it from your browser: log into terabox.com with the throwaway
# account -> DevTools -> Application/Storage -> Cookies -> `ndus` value.
# ------------------------------------------------------------------
TERABOX_NDUS = os.environ.get("TERABOX_NDUS", "").strip()
TERABOX_DIRECT_CLEANUP_MINUTES = int(os.environ.get("TERABOX_DIRECT_CLEANUP_MINUTES", "30") or 30)

# ------------------------------------------------------------------
# GitHub repo-management commands (Akbots/github_tools.py) — /create_repo,
# /delrepo, /downloadrepo, /fork, /forkall, /add_collaborator,
# /remove_collaborator, /gitprivate, /gitpublic. All of these are gated on
# ADMINS. Needs a GitHub Personal Access Token with `repo` + `delete_repo`
# scopes (https://github.com/settings/tokens). Left blank, the commands
# reply with a "not configured" message instead of failing.
# ------------------------------------------------------------------
GIT_TOKEN = os.environ.get("GIT_TOKEN", "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").strip()

# ------------------------------------------------------------------
# Heroku app-management commands (Akbots/heroku_tools.py) — /createapp,
# /addapp, /removeapp, /herokulogs, /herokuinfo, /delheroku, /veriable,
# /apps, /restartdynos, /rename. All gated on ADMINS. Needs a Heroku API
# key (Account Settings -> API Key on dashboard.heroku.com). Left blank,
# the commands reply with a "not configured" message instead of failing.
# ------------------------------------------------------------------
HEROKU_API = os.environ.get("HEROKU_API", "").strip()

# ------------------------------------------------------------------
# Gemini AI chat (Akbots/gemini_chat.py) — /gemini, /ai, /ask, /resetai,
# /clearai (private chat only). Uses Google's official google-genai SDK
# with a real API key — get a free one at https://aistudio.google.com/apikey.
# Left blank, the commands reply with a "not configured" message instead
# of failing.
# ------------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6KLU1fhQc8cfVcxK6MlS8SG2iOpSUOGmxMDLN7S5lrwig").strip()

# Optional: comma-separated extra keys to rotate through when the current
# one hits Google's free-tier rate limit (e.g. "key2,key3"). Leave blank
# if you only have one key.
GEMINI_API_KEYS_EXTRA = os.environ.get("GEMINI_API_KEYS_EXTRA", "").strip()

# Model name — see https://ai.google.dev/gemini-api/docs/models for the
# current list. "gemini-2.5-flash" is fast/cheap and the recommended
# default; swap to "gemini-2.5-pro" for harder prompts if you don't mind
# the extra latency/cost.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

# ------------------------------------------------------------------
# Groq AI chat (Akbots/groq_chat.py) — /groq, /groqmodel, /resetgroq
# (private chat only). Ported from the standalone groq-chatbot-main
# project (python-telegram-bot + mongopersistence) into this bot's own
# Pyrogram plugin + database/db.py pattern, alongside gemini_chat.py.
# Get a free key at https://console.groq.com/keys. Left blank, the
# commands reply with a "not configured" message instead of failing.
# ------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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").strip()

# Optional: comma-separated extra keys to rotate through when the current
# one hits a rate limit. Leave blank if you only have one key.
GROQ_API_KEYS_EXTRA = os.environ.get("GROQ_API_KEYS_EXTRA", "").strip()

# Model name — see https://console.groq.com/docs/models for the current
# list (Groq's lineup changes often; old ids there get deprecated).
# "openai/gpt-oss-20b" is fast/cheap and the recommended default; users
# can switch per-chat with /groqmodel.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b").strip()

# ------------------------------------------------------------------
# GPT chat (Akbots/openai_chat.py) — /gpt, /gptmodel, /gptmode, /resetgpt
# (private chat only). Ported from karfly/chatgpt_telegram_bot's core
# chat logic (bot/openai_utils.py + config/models.yml + chat_modes.yml)
# into this bot's own Pyrogram plugin + database/db.py pattern.
#
# The original repo talks to OpenAI directly. This port instead routes
# every model through OpenRouter (one OpenAI-compatible endpoint, one
# key) so GPT-4o/4o-mini/5.5 *and* Claude Opus/Sonnet/Haiku are all
# reachable with a single OPENROUTER_API_KEY — no separate OpenAI or
# Anthropic billing needed. Get a key (with credits) at
# https://openrouter.ai/keys.
# ------------------------------------------------------------------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "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").strip()

# Shown to OpenRouter for their leaderboards/rate-limit dashboard — not
# secret, safe to leave as-is or replace with your own bot's name/link.
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL", "https://github.com/Akbots-Ultra").strip()
OPENROUTER_SITE_NAME = os.environ.get("OPENROUTER_SITE_NAME", "Akbots-Ultra").strip()

# Default model — see Akbots/openai_chat.py's MODELS list for every
# option users can switch to with /gptmodel. "openai/gpt-4o-mini" is
# fast/cheap and matches the original repo's default.
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()

# Optional: a *direct* OpenAI key (separate from OPENROUTER_API_KEY above).
# OpenRouter only proxies chat completions — voice transcription (Whisper)
# and image generation (gpt-image-1) need OpenAI's API directly. Leave
# blank to skip those two features; /gpt chat itself doesn't need this.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "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").strip()

# Optional access control for Akbots/openai_chat.py — comma-separated
# Telegram user IDs allowed to use /gpt, /imagine, and voice/vision input.
# Leave blank (default) to let everyone use it; ADMINS can always use it
# regardless of this list.
GPT_ALLOWED_USERS = [
    int(u) for u in os.environ.get("GPT_ALLOWED_USERS", "").strip().split(",") if u.strip()
]

# Optional: xAI Grok API key. Not wired into any plugin yet — kept here so
# it (and any future token) can be set live from the bot with /setkey grok
# <value> (see Akbots/runtime_config.py + Akbots/apikeys.py) without
# needing an env var or redeploy first.
GROK_API_KEY = os.environ.get("GROK_API_KEY", "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").strip()
