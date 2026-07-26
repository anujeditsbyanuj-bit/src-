"""
Save Restricted Content Bot Configuration

Developed by: Anuj Kumar
Telegram: @Anujedits76

Please retain this credit if you use or modify this project.
"""

import os


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
MAX_BATCH_IDS_FREE    = int(os.environ.get("MAX_BATCH_IDS_FREE", "50"))
MAX_BATCH_IDS_PREMIUM = int(os.environ.get("MAX_BATCH_IDS_PREMIUM", "200"))

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
INSTA_COOKIES = os.environ.get("INSTA_COOKIES", "instagram/insta_cookies.txt")
FB_COOKIES    = os.environ.get("FB_COOKIES", "facebook/fb_cookies.txt")
# VK.com — only needed for private/age-restricted videos; public videos and
# clips work with no cookies at all. See Akbots/vk.py.
VK_COOKIES    = os.environ.get("VK_COOKIES", "vk/vk_cookies.txt")

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
# TeraBox support-bot upload pool (Akbots/terabox.py)
# ==============================
# Extra bot tokens (comma separated) that terabox.py can borrow to upload
# into DB_CHANNEL when the main bot is busy. Each finished file is uploaded
# by whichever of these bots is free, then instantly copied from DB_CHANNEL
# to the user by the main bot — so heavy TeraBox traffic doesn't hog the
# main bot's own upload slot. Leave empty to skip the pool entirely; the
# plugin then just uploads directly with the main bot as before.
TERABOX_SUPPORT_BOT_TOKENS = [
    t.strip() for t in os.environ.get("TERABOX_SUPPORT_BOT_TOKENS", "").split(",") if t.strip()
]

# ==============================
# TeraBox account-transfer bridge (Akbots/terabridge_account.py)
# ==============================
# Optional 4th extraction tier for terabox.py, ported from TeraBridge-api.
# Logs into a real TeraBox account with its NDUS session cookie, transfers
# the shared file into that account's own /cloudvids storage folder, then
# resolves a direct dlink from there. Different failure mode than the
# cookie-less scrape/guest-mode/teradownloader tiers already in
# terabox.py — those keep running as fallback either way.
#
# Leave TERABOX_NDUS_COOKIE empty to skip this tier entirely.
#
# Get the cookie from a browser logged into terabox.com: DevTools →
# Application/Storage → Cookies → copy the full cookie string (must
# include "ndus=..."). A PREMIUM account is strongly recommended — free
# accounts get bandwidth-throttled and hit small daily transfer/storage
# caps, so this tier would fail almost as often as it helps on a free one.
TERABOX_NDUS_COOKIE = os.environ.get("TERABOX_NDUS_COOKIE", "").strip()

# Multiple accounts to rotate between, instead of/alongside the single
# cookie above. JSON array of {"id","cookie"} objects, or comma-separated
# "id:cookie" pairs. Only actually rotates round-robin when
# UPSTASH_REDIS_REST_URL/TOKEN below are also set — without Redis, only
# one account (TERABOX_NDUS_COOKIE, or this list's first entry) is ever
# used per process, with no persisted rotation state.
TERABOX_ACCOUNTS_RAW = os.environ.get("TERABOX_ACCOUNTS", "").strip()

# Files kept in each account's /cloudvids folder before the oldest are
# auto-deleted to free up storage space. Runs on every resolve call once
# the folder exceeds this count.
TERABOX_MAX_STORED_FILES = int(os.environ.get("TERABOX_MAX_STORED_FILES", "50"))

# ── Ban-avoidance for the account pool above ────────────────────────────
# These exist so multiple accounts actually protect each other instead of
# just spreading load evenly (evenly-spread traffic can still get a single
# account flagged if it alone is used too often in a short window).
#
# Max transfer requests any single account will take in a rolling hour
# before the pool temporarily stops picking it (falls through to another
# healthy account instead). Keep well under what a real human doing casual
# TeraBox usage would do.
TERABOX_MAX_REQUESTS_PER_HOUR = int(os.environ.get("TERABOX_MAX_REQUESTS_PER_HOUR", "20"))

# Minimum seconds between two consecutive uses of the SAME account, even
# if it's otherwise the least-recently-used one in the pool. Prevents
# rapid-fire back-to-back requests on one account when traffic bursts.
TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS = int(os.environ.get("TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS", "8"))

# How long (seconds) an account sits in "cooldown" after a ban-like signal
# (session forbidden, transfer blocked, etc.) before the pool will try it
# again automatically. Distinct from "unhealthy" (dead cookie, needs a
# human to fix) — cooldown self-recovers.
TERABOX_ACCOUNT_COOLDOWN_SECONDS = int(os.environ.get("TERABOX_ACCOUNT_COOLDOWN_SECONDS", "1800"))

# How many different accounts resolve_via_account() will try in one
# request before giving up and letting terabox.py fall through to its
# other tiers. Only account-fault errors (not "this share link is
# invalid") trigger moving to the next account.
TERABOX_MAX_ACCOUNT_RETRIES = int(os.environ.get("TERABOX_MAX_ACCOUNT_RETRIES", "3"))

# Upstash Redis REST credentials (free tier is enough) — used only by the
# account-transfer bridge above for persisted multi-account rotation and
# unhealthy-account tracking. Leave empty for single-account/in-memory
# mode. Sign up at https://upstash.com, create a Redis database, and copy
# the REST URL + token from its dashboard.
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip()
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()

# ── TeraBridge feature-parity layer (response cache, rate limiter, HMAC) ───
# Ported from TeraBridge-api's api/index.py caching/rate-limiting behavior.
# In-memory LRU cache (falls back to memory-only if Redis above isn't set;
# reuses the same UPSTASH_REDIS_REST_URL/TOKEN for a shared cache when it
# is set) of resolved TeraBox links, so repeated requests for the same
# link within TERABOX_CACHE_TTL_SECONDS skip re-hitting Terabox entirely.
TERABOX_CACHE_TTL_SECONDS = int(os.environ.get("TERABOX_CACHE_TTL_SECONDS", "60"))
TERABOX_CACHE_MAX_ENTRIES = int(os.environ.get("TERABOX_CACHE_MAX_ENTRIES", "500"))

# Per-user sliding-window rate limit (Telegram bots don't have per-IP
# traffic the way a web API does, so this is applied per Telegram user
# id instead) protecting the account pool / Terabox session tokens above
# from being exhausted by one user spamming links.
TERABOX_RATE_LIMIT_PER_MIN = int(os.environ.get("TERABOX_RATE_LIMIT_PER_MIN", "30"))

# Secret used to HMAC-sign cached resolve entries (and any proxy tokens
# terabox.py hands out) so a tampered or cross-instance-poisoned cache
# read is detected and discarded instead of silently served. Falls back
# to a value derived from API_HASH if unset — set this explicitly in
# production so it survives a redeploy with a different API_HASH.
TERABOX_HMAC_SECRET = os.environ.get("TERABOX_HMAC_SECRET", "").strip()


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

# Branding shown on every bypass card ("Powered by <name> ⚡").
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

# Local port the aiohttp stream/download server binds to.
STREAM_PORT = int(os.environ.get("STREAM_PORT", "8070"))

# Public base URL people's Stream/Download links will use. On Render,
# Railway, Replit etc. set this to your deployed https:// domain (no
# trailing path needed — just scheme+host, e.g.
# "https://your-app.onrender.com"). If left blank, falls back to
# REPLIT_DEV_DOMAIN when present, else "http://localhost:<STREAM_PORT>"
# (fine for local testing, not reachable from outside).
_stream_domain = os.environ.get(
    "STREAM_FQDN",
    os.environ.get("REPLIT_DEV_DOMAIN", "")
).strip()
STREAM_HAS_SSL = os.environ.get("STREAM_HAS_SSL", "true").lower() == "true"
if _stream_domain:
    _protocol = "https" if STREAM_HAS_SSL else "http"
    STREAM_URL = f"{_protocol}://{_stream_domain.rstrip('/')}/"
else:
    STREAM_URL = f"http://localhost:{STREAM_PORT}/"

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
