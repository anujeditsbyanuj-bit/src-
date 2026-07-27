# /hdhub — browse HDhub4u's latest movies/shows and download them straight
# into this chat, plus an admin auto-post-to-channel feature.
#
# The actual scraping/caching/storage logic (HDhub4uScraper, CacheManager,
# Database) lives in Akbots/hdhub_lib/ — a proper submodule of this bot,
# not a separate bundled repo. Those three files started out as part of a
# standalone HdhubScraper project (its own bot.py, built on
# python-telegram-bot — a different framework, with its own Client/token,
# not usable as an Akbots plugin directly), but only the
# framework-independent library code was worth keeping: it's been moved in
# here, and HdhubScraper's own bot.py/requirements.txt/etc. are gone —
# nothing in this bot runs them.

import re
import time
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

logger = logging.getLogger(__name__)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'

_scraper = None
_cache = None
_AVAILABLE = False

try:
    from Akbots.hdhub_lib.scraper import HDhub4uScraper
    from Akbots.hdhub_lib.cache_manager import CacheManager
    _scraper = HDhub4uScraper()
    _cache = CacheManager()
    _AVAILABLE = True
except Exception as e:
    logger.warning(f"hdhub: hdhub_lib unavailable — {e}")
    _AVAILABLE = False

# HDhub4u runs on many rotating clone/mirror domains (hdhub4u.rehab,
# hdhub4u.cl, new3.hdhub4u.cl, etc. — same template scraper.py is hardcoded
# to only browse via its own main_url for). This pattern matches ANY of
# those mirrors' movie/show slug pages so a pasted link still works even
# when it's not on whatever domain scraper.py's get_latest_content() uses —
# get_download_links(url) itself doesn't care which mirror the url is on,
# it just parses whatever HTML comes back.
PATTERN = re.compile(
    r"(https?://)?[\w.-]*hdhub4u[\w.-]*\.\w+/[\w-]+/?", re.IGNORECASE
)


def extract_url(text: str):
    m = PATTERN.search(text)
    if not m:
        return None
    url = m.group(0).rstrip("/")
    return url if url.startswith("http") else f"https://{url}"


# callback_data has a 64-byte limit, so the actual title/URL lists from a
# listing live here keyed by a short id, not embedded in the button itself.
# In-memory only — fine for a single bot process; each /hdhub or item tap
# replaces the previous entry, nothing accumulates unbounded across days.
_LISTINGS: dict = {}
_LINKS: dict = {}

# Which existing Akbots plugin's _handle(client, message, url) should take
# a resolved mirror link, keyed by domain substring — the exact same
# plugins a pasted link would be auto-detected into.
_DOMAIN_HANDLERS = (
    ("mediafire.com", "Akbots.mediafire"),
    ("mega.nz", "Akbots.mega"),
    ("drive.google.com", "Akbots.gdrive"),
    ("pixeldrain.", "Akbots.pixeldrain"),
    ("gofile.io", "Akbots.gofile"),
    ("streamtape", "Akbots.streamtape"),
    ("catbox.moe", "Akbots.catbox"),
)


def is_available() -> bool:
    return _AVAILABLE


async def _resolve_links_for_url(status: Message, title: str, url: str, links_id: str):
    """Shared by both the /hdhub browse-button flow and a directly pasted
    movie/show link — fetches download-server options for `url` and shows
    them as buttons, or a clear error if the page has none."""
    links = await _scraper.get_download_links(url, _cache)
    if not links:
        await status.edit_text(
            f"<b>{E_CROSS} No download links found for this page.</b>\n"
            f"<i>Layout may differ on this mirror, or the page has no server "
            f"links at all.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    _LINKS[links_id] = links
    buttons = [
        [make_button(f"{lk['server']} — {lk['quality']}", callback_data=f"hdl:{links_id}:{i}", style=_BS.PRIMARY if _BS else None)]
        for i, lk in enumerate(links)
    ]
    await status.edit_text(
        f"<b>{E_CHECK} {title}</b>\nPick a server/quality:",
        parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons),
    )


@Client.on_message(filters.command(["hdhub"]))
async def hdhub_command(client: Client, message: Message):
    if not is_available():
        return await message.reply_text(f"<b>{E_CROSS} HDhub module not available.</b>", parse_mode=enums.ParseMode.HTML)

    # /hdhub <url> — resolve a specific movie/show link directly, on
    # whichever hdhub4u mirror it's on, instead of browsing the latest list.
    if len(message.command) > 1:
        url = extract_url(message.command[1]) or message.command[1]
        return await _handle_direct_link(message, url)

    status = await message.reply_text(f"<b>{E_GEAR} Fetching latest content...</b>", parse_mode=enums.ParseMode.HTML)
    items = await _scraper.get_latest_content(_cache)
    if not items:
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't fetch latest content — site may be down or changed its layout.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    listing_id = str(int(time.time() * 1000))
    _LISTINGS[listing_id] = items

    buttons = [
        [make_button(f"{it['title']} ({it['quality']})", callback_data=f"hdi:{listing_id}:{i}", style=_BS.PRIMARY if _BS else None)]
        for i, it in enumerate(items)
    ]
    await status.edit_text(
        f"<b>{E_CHECK} Latest on HDhub4u — tap one:</b>",
        parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _handle_direct_link(message: Message, url: str):
    status = await message.reply_text(f"<b>{E_GEAR} Fetching links for:</b>\n{url}", parse_mode=enums.ParseMode.HTML)
    links_id = f"direct:{int(time.time() * 1000)}"
    title = url.rsplit("/", 1)[-1].replace("-", " ") or url
    await _resolve_links_for_url(status, title, url, links_id)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN) & ~filters.regex(r"^/"), group=1)
async def hdhub_auto_detect(client: Client, message: Message):
    if not is_available():
        return
    url = extract_url(message.text)
    if url:
        await _handle_direct_link(message, url)


@Client.on_callback_query(filters.regex(r"^hdi:"))
async def hdhub_item_callback(client: Client, query: CallbackQuery):
    _, listing_id, idx = query.data.split(":")
    items = _LISTINGS.get(listing_id)
    if not items or int(idx) >= len(items):
        return await query.answer("This listing expired — run /hdhub again.", show_alert=True)

    item = items[int(idx)]
    await query.answer()
    await query.message.edit_text(f"<b>{E_GEAR} Fetching links for:</b>\n{item['title']}", parse_mode=enums.ParseMode.HTML)
    await _resolve_links_for_url(query.message, item["title"], item["url"], f"{listing_id}:{idx}")


@Client.on_callback_query(filters.regex(r"^hdi_back:"))
async def hdhub_back_callback(client: Client, query: CallbackQuery):
    listing_id = query.data.split(":")[1]
    items = _LISTINGS.get(listing_id)
    if not items:
        return await query.answer("This listing expired — run /hdhub again.", show_alert=True)
    await query.answer()
    buttons = [
        [make_button(f"{it['title']} ({it['quality']})", callback_data=f"hdi:{listing_id}:{i}", style=_BS.PRIMARY if _BS else None)]
        for i, it in enumerate(items)
    ]
    await query.message.edit_text(
        f"<b>{E_CHECK} Latest on HDhub4u — tap one:</b>",
        parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons),
    )


@Client.on_callback_query(filters.regex(r"^hdl:"))
async def hdhub_link_callback(client: Client, query: CallbackQuery):
    _, links_id, idx = query.data.split(":", 2)
    links = _LINKS.get(links_id)
    if not links or int(idx) >= len(links):
        return await query.answer("This link list expired — run /hdhub again.", show_alert=True)

    link = links[int(idx)]["url"]
    await query.answer()
    await query.message.edit_text(f"<b>{E_GEAR} Resolving:</b>\n<code>{link}</code>", parse_mode=enums.ParseMode.HTML)

    # Mirror hosts like hubdrive/hubcloud/hblinks sit behind their own
    # ad-wall/crypt-token page — resolve through the shortener/mirror
    # bypasser (same one /bypass uses) before handing off to a downloader.
    resolved = link
    try:
        from Akbots.shortener_bypass import bypass_link, is_available as bypass_available
        if bypass_available():
            result = await bypass_link(link)
            if result and result.startswith("http"):
                resolved = result
    except Exception:
        pass

    handler_module = "Akbots.urluploader"  # generic direct-file fallback (has cf_bypass wired in)
    for domain, module in _DOMAIN_HANDLERS:
        if domain in resolved.lower():
            handler_module = module
            break

    try:
        mod = __import__(handler_module, fromlist=["_handle"])
        await mod._handle(client, query.message, resolved)
    except Exception as e:
        logger.warning(f"hdhub: download dispatch failed for {resolved}: {e}")
        await query.message.edit_text(
            f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>\n\n"
            f"<b>Link:</b> <code>{resolved}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# =============================================================================
# Auto-post to a channel — the rest of HdhubScraper/bot.py's own feature set
# (its /setchannel, /settimer, /status, /posted, /start_autopost,
# /stop_autopost, /force_post, /stats commands), ported from its
# python-telegram-bot handlers onto this bot's own Pyrogram Client, using
# the SAME HDhub4uScraper + CacheManager instance already set up above, plus
# HdhubScraper/database.py (SQLite — self-contained, resolves its own file
# path from its own __file__, so it works regardless of import location; no
# chdir trick needed here unlike Akbots/shortener_lib/bypasser.py).
# =============================================================================

import asyncio

_db = None
if _AVAILABLE:
    try:
        from Akbots.hdhub_lib.database import Database
        _db = Database()
    except Exception as e:
        logger.warning(f"hdhub: HdhubScraper database unavailable — {e}")
        _db = None

_scheduler = None  # set by schedule_hdhub_autopost() if apscheduler is available

_QUALITY_LABELS = {
    "4K": "🎥 4K UHD", "2160p": "🎥 4K UHD", "1080p": "📺 1080p FHD",
    "720p": "📱 720p HD", "480p": "📱 480p SD",
}


def _admin_only(func):
    async def wrapper(client: Client, message: Message):
        from config import ADMINS
        if message.from_user.id not in ADMINS:
            return await message.reply_text(f"<b>{E_CROSS} Admins only.</b>", parse_mode=enums.ParseMode.HTML)
        if not is_available() or _db is None:
            return await message.reply_text(f"<b>{E_CROSS} HDhub auto-post module not available.</b>", parse_mode=enums.ParseMode.HTML)
        return await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper


@Client.on_message(filters.command(["hdhub_setchannel"]))
@_admin_only
async def hdhub_setchannel(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/hdhub_setchannel &lt;channel_id&gt;</code>\n"
            f"e.g. <code>/hdhub_setchannel -1001234567890</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    _db.set_setting("channel_id", message.command[1])
    await message.reply_text(f"<b>{E_CHECK} Auto-post channel set to</b> <code>{message.command[1]}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["hdhub_settimer"]))
@_admin_only
async def hdhub_settimer(client: Client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/hdhub_settimer &lt;minutes&gt;</code>", parse_mode=enums.ParseMode.HTML
        )
    minutes = max(15, int(message.command[1]))  # floor of 15min so it can't hammer the site
    _db.set_setting("interval_minutes", str(minutes))
    if _scheduler:
        _scheduler.reschedule_job("hdhub_autopost", trigger="interval", minutes=minutes)
    await message.reply_text(f"<b>{E_CHECK} Auto-post interval set to</b> <code>{minutes}</code> minutes.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["hdhub_status"]))
@_admin_only
async def hdhub_status(client: Client, message: Message):
    channel = _db.get_setting("channel_id") or "not set"
    interval = _db.get_setting("interval_minutes") or "60"
    running = _db.get_setting("autopost_running") == "1"
    last_post = _db.get_last_post_time() or "never"
    await message.reply_text(
        f"<b>{E_INFO if False else E_GEAR} HDhub Auto-Post Status</b>\n\n"
        f"<b>Channel:</b> <code>{channel}</code>\n"
        f"<b>Interval:</b> <code>{interval} min</code>\n"
        f"<b>Running:</b> {'✅ Yes' if running else '❌ No'}\n"
        f"<b>Last post:</b> <code>{last_post}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["hdhub_posted"]))
@_admin_only
async def hdhub_posted(client: Client, message: Message):
    posts = _db.get_recent_posts(limit=10)
    if not posts:
        return await message.reply_text(f"<b>{E_GEAR} No posts yet.</b>", parse_mode=enums.ParseMode.HTML)
    lines = [f"<b>{E_CHECK} Last {len(posts)} posts:</b>"]
    for p in posts:
        lines.append(f"• {p['title']} — <code>{p['posted_at']}</code>")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["hdhub_stats"]))
@_admin_only
async def hdhub_stats(client: Client, message: Message):
    await message.reply_text(
        f"<b>{E_GEAR} HDhub Auto-Post Stats</b>\n\n"
        f"<b>Total posts:</b> <code>{_db.get_total_posts()}</code>\n"
        f"<b>Today:</b> <code>{_db.get_posts_count_today()}</code>\n"
        f"<b>Unique content:</b> <code>{_db.get_unique_content_count()}</code>\n"
        f"<b>DB size:</b> <code>{_db.get_size_mb():.2f} MB</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["hdhub_startauto"]))
@_admin_only
async def hdhub_startauto(client: Client, message: Message):
    if not _db.get_setting("channel_id"):
        return await message.reply_text(
            f"<b>{E_CROSS} Set a channel first:</b> <code>/hdhub_setchannel &lt;channel_id&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    _db.set_setting("autopost_running", "1")
    if not _scheduler:
        schedule_hdhub_autopost(client)
    await message.reply_text(f"<b>{E_CHECK} HDhub auto-post started.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["hdhub_stopauto"]))
@_admin_only
async def hdhub_stopauto(client: Client, message: Message):
    _db.set_setting("autopost_running", "0")
    await message.reply_text(f"<b>{E_CHECK} HDhub auto-post stopped.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["hdhub_forcepost"]))
@_admin_only
async def hdhub_forcepost(client: Client, message: Message):
    status = await message.reply_text(f"<b>{E_GEAR} Checking for new content...</b>", parse_mode=enums.ParseMode.HTML)
    posted = await _post_new_content(client, force=True)
    await status.edit_text(f"<b>{E_CHECK} Posted {posted} new item(s).</b>", parse_mode=enums.ParseMode.HTML)


def _format_post_caption(item: dict) -> str:
    title = item.get("title", "Unknown")
    quality = item.get("quality", "")
    caption = f"🎬 <b>{title}</b>"
    if quality:
        caption += f"\n\n📊 Quality: <code>{quality}</code>"
    links = item.get("download_links", [])
    if links:
        caption += f"\n\n💾 {len(links)} download link(s) available — tap below."
    return caption


def _build_post_keyboard(item: dict) -> InlineKeyboardMarkup | None:
    links = item.get("download_links", [])[:8]
    if not links:
        return None
    buttons = []
    for lk in links:
        label = _QUALITY_LABELS.get(lk.get("quality"), f"📥 {lk.get('quality', lk.get('server', 'Link'))}")
        buttons.append([make_button(label, url=lk["url"], style=_BS.PRIMARY if _BS else None)])
    return InlineKeyboardMarkup(buttons)


async def _post_new_content(client: Client, force: bool = False, limit_per_run: int = 3) -> int:
    """Core auto-post job — fetches latest content, skips anything already
    posted (tracked in database.py's sqlite posts table), resolves each
    new item's download links, and posts it to the configured channel.
    Returns how many were posted. Shared by both the scheduled job and
    /hdhub_forcepost."""
    if _db is None or not is_available():
        return 0
    channel = _db.get_setting("channel_id")
    if not channel:
        return 0
    if not force and _db.get_setting("autopost_running") != "1":
        return 0

    try:
        items = await _scraper.get_latest_content(_cache)
    except Exception as e:
        logger.warning(f"hdhub: autopost fetch failed: {e}")
        return 0

    posted = 0
    for item in items:
        if _db.is_posted(item["url"]):
            continue
        try:
            item["download_links"] = await _scraper.get_download_links(item["url"], _cache)
        except Exception:
            item["download_links"] = []

        try:
            caption = _format_post_caption(item)
            keyboard = _build_post_keyboard(item)
            if item.get("poster_url"):
                await client.send_photo(channel, item["poster_url"], caption=caption,
                                         reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
            else:
                await client.send_message(channel, caption, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
            _db.add_post(item["title"], item["url"])
            posted += 1
            if posted >= limit_per_run:
                break
        except Exception as e:
            logger.warning(f"hdhub: failed to post '{item.get('title')}': {e}")

    return posted


def schedule_hdhub_autopost(app: Client):
    """Call once from Bot.start() after the client is running (see bot.py)
    — mirrors Akbots/autopost.py's own schedule_autopost() pattern. No-op
    if apscheduler/HdhubScraper aren't available; the job itself checks
    autopost_running before doing anything, so scheduling it unconditionally
    is safe — it's a no-op until /hdhub_startauto is used."""
    global _scheduler
    if not is_available() or _db is None:
        logger.info("HDhub auto-post not scheduled (HdhubScraper unavailable).")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("HDhub auto-post enabled but apscheduler isn't installed.")
        return

    interval = int(_db.get_setting("interval_minutes") or "60")
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(_post_new_content, "interval", minutes=interval, args=[app], id="hdhub_autopost")
    _scheduler.start()
    logger.info(f"HDhub auto-post scheduler started (every {interval} min; posts only while running).")
