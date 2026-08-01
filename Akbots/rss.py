# Developed by: LastPerson07 × AkBots
# Telegram: @AkBots_Official | @THEUPDATEDGUYS
#
# /rss_add, /rss_list, /rss_remove, /rss_check — per-user RSS/Atom feed
# subscriptions. A background job polls every subscribed feed on an
# interval; any entry not seen before gets posted into that user's chat
# and fed straight into the SAME pipeline a manually-pasted link would
# use (yt-dlp's quality picker for supported sites, otherwise the generic
# direct-file downloader) — no separate download/upload path to maintain.

import asyncio
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

logger = logging.getLogger(__name__)

try:
    import feedparser
except ImportError:
    feedparser = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'

MAX_FEEDS_PER_USER = 10
CHECK_INTERVAL_MINUTES = 15
MAX_NEW_ITEMS_PER_CHECK = 10  # cap so a freshly-added, huge feed can't flood the chat


def _entry_guid(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


async def _dispatch_link(client: Client, chat_id: int, title: str, url: str):
    """Posts a notification for a new RSS item, then reuses that message as
    the anchor for the normal auto-download flow (quality picker for
    yt-dlp-supported sites, generic direct-file download otherwise)."""
    notice = await client.send_message(
        chat_id,
        f"<b>{E_ROCKET} New RSS item:</b> {title[:150]}\n<code>{url}</code>",
        parse_mode=enums.ParseMode.HTML
    )
    try:
        from Akbots.ytdl import has_quality_formats, _show_quality_picker
        if await has_quality_formats(url):
            return await _show_quality_picker(client, notice, url)
    except Exception:
        pass
    try:
        from Akbots.urluploader import _handle as generic_handle
        await generic_handle(client, notice, url)
    except Exception as e:
        logger.warning(f"RSS auto-download fallback failed for {url}: {e}")


async def _check_feeds_for_user(client: Client, user_id: int, feeds: list):
    for idx, feed in enumerate(feeds):
        try:
            parsed = await asyncio.to_thread(feedparser.parse, feed["url"])
        except Exception as e:
            logger.warning(f"RSS fetch failed for {feed.get('url')}: {e}")
            continue

        seen = set(feed.get("seen") or [])
        new_entries = [e for e in parsed.entries if _entry_guid(e) not in seen]
        if not new_entries:
            continue

        # Feed order is newest-first by convention; reverse so items post
        # oldest-to-newest, and cap how many go out in one check.
        for entry in list(reversed(new_entries))[-MAX_NEW_ITEMS_PER_CHECK:]:
            link = entry.get("link")
            guid = _entry_guid(entry)
            if link:
                try:
                    await _dispatch_link(client, user_id, entry.get("title", "New item"), link)
                except Exception as e:
                    logger.warning(f"RSS dispatch failed for {link}: {e}")
            await db.mark_rss_seen(user_id, idx, guid)


async def check_all_feeds(client: Client):
    if feedparser is None:
        return
    cursor = await db.get_all_rss_users()
    async for user_doc in cursor:
        feeds = user_doc.get("rss_feeds") or []
        if feeds:
            await _check_feeds_for_user(client, user_doc["id"], feeds)


_scheduler = None  # set by schedule_rss() if apscheduler + feedparser are available


def schedule_rss(app: Client):
    """Starts the periodic feed-check job. No-ops (with a log warning) if
    feedparser or apscheduler isn't installed, or the feature isn't used."""
    global _scheduler
    if feedparser is None:
        logger.warning("RSS feature needs feedparser — add it to requirements.txt.")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("RSS feature needs apscheduler — add it to requirements.txt.")
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        lambda: asyncio.create_task(check_all_feeds(app)),
        "interval", minutes=CHECK_INTERVAL_MINUTES
    )
    _scheduler.start()
    logger.info(f"RSS scheduler started (every {CHECK_INTERVAL_MINUTES} min).")


@Client.on_message(filters.command("rss_add") & filters.private)
async def rss_add_command(client: Client, message: Message):
    if feedparser is None:
        return await message.reply_text(
            f"<b>{E_CROSS} RSS support isn't installed on this server.</b>\n"
            f"<i>Run <code>pip install feedparser</code> on the host.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    if len(message.command) < 3:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/rss_add &lt;name&gt; &lt;feed_url&gt;</code>\n"
            f"<i>Example:</i> <code>/rss_add MyAnime https://example.com/feed.xml</code>",
            parse_mode=enums.ParseMode.HTML
        )
    name = message.command[1]
    url = message.command[2]

    feeds = await db.get_rss_feeds(message.from_user.id)
    if len(feeds) >= MAX_FEEDS_PER_USER:
        return await message.reply_text(
            f"<b>{E_CROSS} Limit reached ({MAX_FEEDS_PER_USER} feeds).</b> Remove one with /rss_remove first.",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>{E_INFO} Checking feed...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        parsed = await asyncio.to_thread(feedparser.parse, url)
        if not parsed.entries:
            return await status.edit_text(
                f"<b>{E_CROSS} No entries found — is this a valid RSS/Atom feed URL?</b>",
                parse_mode=enums.ParseMode.HTML
            )
        # Mark everything currently in the feed as already-seen, so
        # subscribing doesn't immediately dump the whole backlog into the
        # chat — only items published AFTER this point get auto-downloaded.
        initial_seen = [_entry_guid(e) for e in parsed.entries]
    except Exception as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't read that feed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    await db.add_rss_feed(message.from_user.id, name, url, initial_seen)
    await status.edit_text(
        f"<b>{E_CHECK} Subscribed to \"{name}\".</b>\n"
        f"<i>New items will be auto-downloaded here roughly every {CHECK_INTERVAL_MINUTES} min "
        f"(or run /rss_check to check right now).</i>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("rss_list") & filters.private)
async def rss_list_command(client: Client, message: Message):
    feeds = await db.get_rss_feeds(message.from_user.id)
    if not feeds:
        return await message.reply_text(
            f"<b>{E_INFO} No RSS feeds subscribed yet.</b> Use /rss_add.", parse_mode=enums.ParseMode.HTML
        )
    lines = [f"<b>{E_INFO} Your RSS feeds:</b>", ""]
    for i, f in enumerate(feeds):
        lines.append(f"<b>{i}.</b> {f['name']}\n   <code>{f['url']}</code>")
    lines.append("")
    lines.append("<i>Remove one with /rss_remove &lt;number&gt;</i>")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.command("rss_remove") & filters.private)
async def rss_remove_command(client: Client, message: Message):
    if len(message.command) < 2 or not message.command[1].isdigit():
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/rss_remove &lt;number&gt;</code> (see /rss_list)",
            parse_mode=enums.ParseMode.HTML
        )
    idx = int(message.command[1])
    ok = await db.remove_rss_feed(message.from_user.id, idx)
    if ok:
        await message.reply_text(f"<b>{E_CHECK} Removed.</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(
            f"<b>{E_CROSS} No feed at that number — check /rss_list.</b>", parse_mode=enums.ParseMode.HTML
        )


@Client.on_message(filters.command("rss_check") & filters.private)
async def rss_check_command(client: Client, message: Message):
    if feedparser is None:
        return await message.reply_text(
            f"<b>{E_CROSS} RSS support isn't installed on this server.</b>", parse_mode=enums.ParseMode.HTML
        )
    feeds = await db.get_rss_feeds(message.from_user.id)
    if not feeds:
        return await message.reply_text(
            f"<b>{E_INFO} No RSS feeds subscribed yet.</b> Use /rss_add.", parse_mode=enums.ParseMode.HTML
        )
    status = await message.reply_text(f"<b>{E_ROCKET} Checking your feeds now...</b>", parse_mode=enums.ParseMode.HTML)
    await _check_feeds_for_user(client, message.from_user.id, feeds)
    await status.edit_text(f"<b>{E_CHECK} Done — any new items were posted above.</b>", parse_mode=enums.ParseMode.HTML)
