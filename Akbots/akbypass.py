# Akbots
# Akbots Bypass — the automation layer on top of
# Akbots/shortener_bypass.py + shortener_lib/ (the actual link-resolution
# engine, unchanged). This file adds the *bot persona* around it:
#
#   1. Group auto-detect: drop a supported shortlink in a connected group,
#      no /bypass needed — the bot reacts, resolves it, and replies with a
#      styled "Bypass Successful ✨" card (shared formatter lives in
#      shortener_bypass.py's run_bypass_card(), so /bypass and this
#      auto-detect always look identical).
#   2. /domains (aliases /sites, /supported): lists every domain the
#      bypass engine currently supports. Built by scanning shortener_lib/
#      bypasser.py's dispatcher + ddl.py's ddllist at import time, so the
#      list can never drift out of sync with what's actually wired up.
#   3. /akstats (admin-only): bypass count, supported-domain count,
#      engine on/off state.
#
# Everything here is config-gated (see config.py's Akbots Bypass block) and
# fails safe: if shortener_lib isn't importable, /akstats still works
# fine and auto-detect just never fires.
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import re
import time
import logging
import asyncio

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from Akbots.shortener_bypass import (
    bypass_link, is_available as bypass_available, run_bypass_card,
    _LB_DIR, _ddl,
)
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    from Akbots.playwright_bypass import is_playwright_bypass_site, supported_site_count
except Exception:
    def is_playwright_bypass_site(url):
        return False
    def supported_site_count():
        return 0

try:
    from config import (
        ADMINS, AKBOTS_BRAND_NAME, AKBOTS_AUTO_DETECT_CHATS, AKBOTS_UPDATES_CHANNEL_URL,
    )
except ImportError:
    ADMINS = []
    AKBOTS_BRAND_NAME = "Akbots"
    AKBOTS_AUTO_DETECT_CHATS = []
    AKBOTS_UPDATES_CHANNEL_URL = ""

logger = logging.getLogger(__name__)

E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_BELL, E_FIRE = "🔔", "🔥"


# ── Dynamic supported-domain list ────────────────────────────────────────
# Scans shortener_lib/bypasser.py's shortners() dispatcher (its `elif
# "https://domain..." in url:` chain) for every literal domain fragment,
# and merges in ddl.py's ddllist (direct-download hosts). This is how
# /domains stays accurate without a hand-maintained second copy of ~150+
# names that would inevitably go stale the next time bypasser.py gains a
# new host.
def _extract_dispatcher_domains() -> list:
    path = os.path.join(_LB_DIR, "bypasser.py") if _LB_DIR else None
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read()
    except Exception:
        return []

    start = src.find("def shortners(url):")
    body = src[start:] if start != -1 else src
    domains = set()
    for m in re.finditer(r'"(https?://[^"]+)"', body):
        host = m.group(1).replace("https://", "").replace("http://", "").split("/")[0].rstrip(".")
        if host and "." in host:
            domains.add(host.lower())
    return sorted(domains)


def _build_supported_domains() -> list:
    domains = set(_extract_dispatcher_domains())
    if _ddl is not None:
        domains.update(d.lower() for d in getattr(_ddl, "ddllist", []) if "." in d)
    return sorted(domains)


_SUPPORTED_DOMAINS = _build_supported_domains()  # computed once at import


def supported_domains() -> list:
    """Display list for /domains — bypasser.py/ddl.py's clean domain names
    plus a count of the additional Playwright-tier sites (those are
    regex-fragments, not always clean domain strings, so they're reported
    as a count rather than mixed into the same flat list)."""
    return _SUPPORTED_DOMAINS


def _build_link_pattern():
    """Regex for the *known-clean* bypasser.py/ddl.py domains only (safe:
    these are plain hostnames, always re.escape()'d). Used as a quick
    pre-filter; NOT extended with the Playwright tier's raw
    supported_sites.txt lines — several of those are hand-written
    "domain OR domain OR ..." groups meant for human reading, not
    guaranteed valid/composable regex (mixing 385 of them into one
    pattern broke compilation in testing: "nothing to repeat"). The
    Playwright tier is instead checked directly via
    is_playwright_bypass_site() in ak_auto_bypass() below, against
    its own already-validated match_rules.txt/include_rules.txt
    patterns (see playwright_bypass.py)."""
    if not _SUPPORTED_DOMAINS:
        return re.compile(r"https?://\S+")
    fragments = sorted((re.escape(d) for d in _SUPPORTED_DOMAINS), key=len, reverse=True)
    return re.compile(r"https?://(?:www\.)?(?:" + "|".join(fragments) + r")\S*", re.IGNORECASE)


_GENERIC_URL_RE = re.compile(r"https?://\S+")


_LINK_PATTERN = _build_link_pattern()


# ── Chat scoping + stats helpers ─────────────────────────────────────────
def _chat_allowed(chat_id) -> bool:
    if not AKBOTS_AUTO_DETECT_CHATS:
        return True
    return str(chat_id) in AKBOTS_AUTO_DETECT_CHATS


def _updates_button():
    if not AKBOTS_UPDATES_CHANNEL_URL:
        return None
    return InlineKeyboardMarkup([[make_button(f"{E_BELL} Updates", url=AKBOTS_UPDATES_CHANNEL_URL, style=_BS.PRIMARY if _BS else None)]])


# ── 1. Group auto-detect-and-bypass ──────────────────────────────────────
# Two separate handler groups on purpose: the silence tracker (group=5)
# runs for every group text message so the ads engine below always has a
# fresh "last activity" timestamp, independent of whether that particular
# message happens to contain a bypassable link.
_last_activity: dict = {}   # chat_id -> unix timestamp of last text message


@Client.on_message(filters.group & filters.text, group=5)
async def ak_track_activity(client: Client, message: Message):
    if message.text and not message.text.startswith("/"):
        _last_activity[message.chat.id] = time.time()


@Client.on_message(filters.group & filters.text, group=7)
async def ak_auto_bypass(client: Client, message: Message):
    if not _chat_allowed(message.chat.id):
        return
    text = message.text or ""
    if not text or text.startswith("/"):
        return  # let /bypass (shortener_bypass.py) handle explicit commands

    # Cheap pre-filter: any known-domain match (fast tier) short-circuits
    # straight to a URL; otherwise scan for *any* http(s) URL and only
    # then pay for the (slightly heavier) Playwright-tier rule check.
    m = _LINK_PATTERN.search(text)
    url = m.group(0) if m else None
    if not url:
        candidate = _GENERIC_URL_RE.search(text)
        if not candidate or not is_playwright_bypass_site(candidate.group(0)):
            return
        url = candidate.group(0)
    elif not bypass_available() and not is_playwright_bypass_site(url):
        return

    try:
        await client.send_reaction(message.chat.id, message.id, E_FIRE)
    except Exception:
        pass  # reactions can fail (rate-limited, disabled in chat, etc.) — non-fatal

    card, ok = await run_bypass_card(url)
    try:
        await message.reply_text(
            card, parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True, reply_markup=_updates_button(),
        )
    except FloodWait as e:
        await asyncio.sleep(int(e.value) + 1)
    except Exception as e:
        logger.warning(f"akbypass: reply failed in {message.chat.id}: {e}")


# ── 2. /domains — supported-site list ────────────────────────────────────
@Client.on_message(filters.command(["domains", "sites", "supported"]))
async def domains_command(client: Client, message: Message):
    domains = supported_domains()
    pw_count = supported_site_count()
    if not domains and not pw_count:
        return await message.reply_text(
            f"<b>{E_CROSS}</b> Bypass module isn't available right now.",
            parse_mode=enums.ParseMode.HTML,
        )
    joined = ", ".join(domains) if domains else "(fast-tier module unavailable)"
    header = (
        f"<b>☰ Supported Sites In This Bot!</b>\n"
        f"<b>»</b> <code>{len(domains)}</code> direct + <code>{pw_count}</code> via headless-browser tier\n\n"
    )

    if len(header) + len(joined) + 14 <= 4000:
        await message.reply_text(
            header + f"<code>{joined}</code>", parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True
        )
        return

    await message.reply_text(header.strip(), parse_mode=enums.ParseMode.HTML)
    chunk_size = 3500
    for i in range(0, len(joined), chunk_size):
        await message.reply_text(f"<code>{joined[i:i + chunk_size]}</code>", parse_mode=enums.ParseMode.HTML)


# ── 3. /akstats (admin-only) ───────────────────────────────────────────
@Client.on_message(filters.command(["akstats", "bypassstats"]) & filters.user(ADMINS))
async def ak_stats_command(client: Client, message: Message):
    try:
        from database.db import db
        bypass_count = await db._fs_get_config("akbots_bypass_count", 0) or 0
    except Exception:
        bypass_count = "N/A"

    await message.reply_text(
        f"<b>📊 Akbots Bypass Stats</b>\n\n"
        f"✅ <b>Total bypasses:</b> <code>{bypass_count}</code>\n"
        f"💬 <b>Tracked active chats:</b> <code>{len(_last_activity)}</code>\n"
        f"🌐 <b>Supported domains:</b> <code>{len(supported_domains())}</code> direct + <code>{supported_site_count()}</code> browser-tier\n"
        f"⚡ <b>Fast bypass engine:</b> <code>{'available' if bypass_available() else 'unavailable'}</code>",
        parse_mode=enums.ParseMode.HTML,
    )
