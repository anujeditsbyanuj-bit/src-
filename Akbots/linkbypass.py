"""
/bypass — link/ad-lock bypasser, wired in from the Nova bot family
(Nova-adlink-bypasser-bot, Nova-Link-Bypasser-Bot, Nova-Bypasser-bot).

The actual bypass engine lives in Akbots/nova_bypasser/ (ported from
Nova-Bypasser-bot's `bypasser/` package, since that one is pyrogram-based
like this project — the other two Nova repos are python-telegram-bot +
Flask apps and don't share a runtime with this bot). This file is just
the thin command layer: parse the link, call the engine, show the result.
"""

import re
import time
import asyncio
import logging
from urllib.parse import urlparse
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMINS
from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO, safe_edit
from Akbots.nova_bypasser.core import LinkBypasser
from Akbots.nova_bypasser.ai_fallback import ai_bypasser
from Akbots.nova_bypasser.guards import rate_limited, force_sub_required
from Akbots.nova_bypasser.domain_db.updater import DomainUpdater
from Akbots.nova_bypasser.ss.request_queue import RequestQueue

logger = logging.getLogger(__name__)
bypasser = LinkBypasser()
# Global concurrency cap (ported from SS_Bypass_bot's request_queue.py) —
# without this, someone pasting 50 links in one /bypass message would fire
# 50 concurrent network-heavy bypass attempts at once. Everything still
# runs, just at most 5 at a time process-wide; extra requests wait their
# turn instead of piling on.
request_queue = RequestQueue(max_concurrent=5)

# Matches full http(s) URLs, plus bare known-shortener domains with no
# scheme (e.g. someone pastes "linkvertise.com/12345/x" straight from a
# forwarded post) — ported from my-bypass-bot's URL_REGEX.
_URL_RE = re.compile(
    r"(?i)\b("
    r"https?://[^\s<>\"']+"
    r"|"
    r"(?:linkvertise\.com|work\.ink|loot-link\.com|loot-links\.com|"
    r"lootdest\.(?:info|org|com)|boost\.ink|mboost\.me|rekonise\.com|"
    r"sub2unlock\.(?:com|net)|adfoc\.us|adf\.ly|cuty\.io|cety\.io|"
    r"socialwolvez\.com|paster\.so|paste-drop\.com|bit\.ly|tinyurl\.com|"
    r"is\.gd|t\.co|v\.gd|ouo\.io|sh\.st|shorte\.st|gplinks\.[a-z]+)/[^\s<>\"']+"
    r")"
)
_TRAILING_PUNCT = re.compile(r"[)\].,;:!?]+$")


def _normalize_url(raw: str) -> str:
    cleaned = _TRAILING_PUNCT.sub("", raw.strip())
    if not cleaned.lower().startswith(("http://", "https://")):
        cleaned = "https://" + cleaned
    return cleaned


def _extract_url(text: str):
    match = _URL_RE.search(text or "")
    return _normalize_url(match.group(1)) if match else None


def _extract_urls(text: str) -> list:
    """All (deduped) links in a message — ported from my-bypass-bot's
    extract_urls, so a message with movie names/emojis/several links
    mixed in still gets every link picked out."""
    found, seen = [], set()
    for match in _URL_RE.finditer(text or ""):
        url = _normalize_url(match.group(1))
        key = url.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            found.append(url)
    return found


def _validate_url(url: str):
    """Basic sanity checks before we bother hitting the network — ported
    from nova1's utils/validators.py::validate_url (dropped the external
    `validators` package dependency; urlparse covers what we need here).
    Returns (is_valid, error_message)."""
    if not url or not isinstance(url, str):
        return False, "URL cannot be empty"
    url = url.strip()
    if len(url) > 2048:
        return False, "URL is too long (max 2048 characters)"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "URL must start with http:// or https://"
    if not parsed.netloc or "." not in parsed.netloc:
        return False, "URL must have a valid domain"
    return True, None


def _result_keyboard(bypassed_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Open Link", url=bypassed_url)]])


async def _bypass_one(url: str, chat_id: int = 0, user_id: int = 0) -> dict:
    is_valid, err = _validate_url(url)
    if not is_valid:
        return {"success": False, "error": err, "original": url}
    await request_queue.acquire(chat_id, user_id)
    try:
        result = await bypasser.bypass(url)
    except Exception as e:
        logger.error(f"/bypass failed for {url}: {e}")
        result = {"success": False, "error": str(e)}
    finally:
        request_queue.release()
    result["original"] = url
    return result


@Client.on_message(filters.command(["bypass", "b"]))
@force_sub_required
@rate_limited(calls=5, period=60)
async def bypass_cmd(client: Client, message: Message):
    args = message.text.split(maxsplit=1)
    source_text = args[1] if len(args) >= 2 else (message.reply_to_message.text if message.reply_to_message else "")
    urls = _extract_urls(source_text)

    if not urls:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/bypass &lt;link&gt;</code>\n"
            f"<i>Or reply to a message containing the link(s) with</i> <code>/bypass</code>.\n"
            f"<i>Multiple links in one message are all bypassed together.</i>\n\n"
            f"<b>Supports:</b> gdtot/gdflix, sharer.pw, uptobox, terabox, linkvertise, "
            f"adf.ly, gplinks, ouo.io, droplink, and a generic bypasser for most other "
            f"ad-locked/shortener sites.",
            parse_mode=enums.ParseMode.HTML
        )

    if len(urls) == 1:
        return await _bypass_single_flow(message, urls[0])
    await _bypass_bulk_flow(message, urls)


async def _bypass_single_flow(message: Message, url: str):
    status = await message.reply_text(f"<b>🔄 Bypassing link...</b>\n<i>This can take up to ~30s.</i>", parse_mode=enums.ParseMode.HTML)
    start = time.time()
    user_id = message.from_user.id if message.from_user else 0
    result = await _bypass_one(url, message.chat.id, user_id)
    elapsed = time.time() - start

    if not result.get("success"):
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Bypass failed.</b>\n"
            f"<b>Reason:</b> <code>{result.get('error', 'unknown error')}</code>\n\n"
            f"<i>Site may be unsupported, or its ad-lock changed since this was last updated.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    bypassed_url = result["bypassed_url"]
    bypass_type = result.get("type", "unknown")
    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} Link bypassed!</b> <i>({elapsed:.1f}s, method: {bypass_type})</i>\n\n"
        f"<code>{bypassed_url}</code>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_result_keyboard(bypassed_url),
    )


async def _bypass_bulk_flow(message: Message, urls: list):
    """Bypass every link in the message concurrently, with a live
    progress counter — ported from my-bypass-bot's bypass_many()."""
    total = len(urls)
    progress = await message.reply_text(f"<b>🔄 Bypassing 0/{total} links...</b>", parse_mode=enums.ParseMode.HTML)
    results = [None] * total
    completed = 0
    lock = asyncio.Lock()
    user_id = message.from_user.id if message.from_user else 0

    async def worker(index: int, url: str):
        nonlocal completed
        result = await _bypass_one(url, message.chat.id, user_id)
        async with lock:
            results[index] = result
            completed += 1
            await safe_edit(progress.edit_text, f"<b>🔄 Bypassing {completed}/{total} links...</b>", parse_mode=enums.ParseMode.HTML)

    await asyncio.gather(*(worker(i, u) for i, u in enumerate(urls)))

    succeeded = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    summary_lines = [f"<b>{E_CHECK} Done — {len(succeeded)}/{total} bypassed successfully.</b>\n"]
    for r in results:
        if r.get("success"):
            summary_lines.append(f"✅ <code>{r['original'][:50]}</code> → <code>{r['bypassed_url'][:60]}</code>")
        else:
            summary_lines.append(f"❌ <code>{r['original'][:50]}</code> — {r.get('error', 'failed')[:60]}")
    await safe_edit(progress.edit_text, "\n".join(summary_lines), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)

    # Each successful link also gets its own message with an "Open" button,
    # so they're individually tappable/forwardable (not just buried in text).
    for r in succeeded:
        await message.reply_text(
            f"<b>🔗 Direct link</b>\n<code>{r['bypassed_url']}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=_result_keyboard(r["bypassed_url"]),
            disable_web_page_preview=True,
        )


@Client.on_message(filters.command("supportedsites"))
async def supported_sites_cmd(client: Client, message: Message):
    sites = bypasser.get_supported_sites()
    ai_line = "🟢 enabled" if ai_bypasser.is_available() else "🔴 disabled (set OPENAI_API_KEY to enable)"
    await message.reply_text(
        f"<b>{E_INFO} Dedicated bypass support:</b>\n" +
        "\n".join(f"• <code>{s}</code>" for s in sites) +
        f"\n\n<b>Plus ~60 exact domain routes</b> (GDrive clones, file/video hosters, "
        f"regional shorteners) checked first when they match, before anything else.\n\n"
        f"<i>Everything else falls through the ladder: universal HTML/CSS/JS extraction → "
        f"shrinkme-cluster resolver → Cloudflare handling → generic redirect-follow → "
        f"third-party bypass APIs (7 services across 2 rotating pools) → optional browser "
        f"automation → AI fallback ({ai_line}).</i>\n\n"
        f"<i>Domains are checked against a live community-sourced reputation DB "
        f"first (admins: /updatedomains to refresh it, /bypassstats for usage stats).</i>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("bypassstats") & filters.user(ADMINS))
async def bypass_stats_cmd(client: Client, message: Message):
    """Admin-only dashboard over the lightweight learning stats recorded
    in database/db.py's bypass_stats_col — how many domains have been
    bypassed, which method wins most for each, and overall success rate.
    Ported from the Nova bots' admin bypass-stats commands, adapted to
    read this bot's own MongoDB collection instead of Firebase."""
    from database.db import db

    docs = [doc async for doc in db.bypass_stats_col.find({}).sort("updated_at", -1).limit(25)]
    if not docs:
        return await message.reply_text(f"<b>{E_INFO} No bypass attempts recorded yet.</b>", parse_mode=enums.ParseMode.HTML)

    total_domains = await db.bypass_stats_col.count_documents({})
    lines = []
    total_tries = total_wins = 0
    for doc in docs:
        domain = doc.get("domain", "?")
        attempts = doc.get("attempts", {})
        dom_tries = sum(s.get("tries", 0) for s in attempts.values())
        dom_wins = sum(s.get("wins", 0) for s in attempts.values())
        total_tries += dom_tries
        total_wins += dom_wins
        best = max(attempts.items(), key=lambda kv: kv[1].get("wins", 0), default=(None, {}))
        best_method = best[0] or "—"
        rate = f"{(dom_wins / dom_tries * 100):.0f}%" if dom_tries else "—"
        lines.append(f"• <code>{domain}</code> — {rate} success, best: <code>{best_method}</code> ({dom_tries} tries)")

    overall_rate = f"{(total_wins / total_tries * 100):.0f}%" if total_tries else "—"
    await message.reply_text(
        f"<b>{E_INFO} Bypass stats</b> — {total_domains} domains tracked, {overall_rate} overall success\n\n"
        + "\n".join(lines)
        + (f"\n\n<i>Showing 25 most recently active domains.</i>" if total_domains > 25 else ""),
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("updatedomains") & filters.user(ADMINS))
async def update_domains_cmd(client: Client, message: Message):
    """Admin-only: refresh the live shortener-domain reputation DB
    (Akbots/nova_bypasser/domain_db/) from its community GitHub sources.
    Runs automatically every `NOVA_BYPASS_DOMAIN_DB_REFRESH_DAYS` (default
    7) as a side effect of normal use too; this just forces it now."""
    status = await message.reply_text(f"<b>🔄 Refreshing domain database from community sources...</b>", parse_mode=enums.ParseMode.HTML)
    updater = DomainUpdater(bypasser.domain_db, refresh_days=0)
    try:
        result = await updater.refresh(force=True)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Refresh failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not result.get("refreshed"):
        return await safe_edit(status.edit_text, f"<b>{E_INFO} Skipped:</b> <code>{result.get('reason')}</code>", parse_mode=enums.ParseMode.HTML)

    stats = bypasser.domain_db.get_stats()
    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} Domain database refreshed!</b>\n\n"
        f"<b>This refresh:</b> {result['active']} active, {result['inactive']} inactive ({result['total']} total from community)\n"
        f"<b>DB overall:</b> {stats['active_shorteners']} active / {stats['inactive_shorteners']} inactive / {stats['total_domains']} domains, {stats['cached_bypasses']} cached results",
        parse_mode=enums.ParseMode.HTML
    )
