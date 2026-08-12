# Shortlink / mirror-host bypass — resolves shortener/mirror links (exe.io,
# sub2unlock, adfly, gplinks, linkvertise, ouo.io, shareus, filecrypt,
# GDToT, HubDrive, DriveFire, KatDrive, kolop, and many more — see
# Akbots/shortener_lib/bypasser.py's shortners() dispatcher for the full
# list) to a real direct link, from inside this bot.
#
# The scraping logic (bypasser.py/ddl.py/freewall.py) lives in
# Akbots/shortener_lib/ — a proper submodule of this bot, ported in from a
# standalone source project (that project's own Flask server/templates/
# Dockerfile were dropped; only its framework-independent bypass logic was
# worth keeping).
#
# This is a DIFFERENT category of "bypass" from Akbots/cf_bypass.py:
#   - cf_bypass.py gets past Cloudflare's anti-bot challenge on a URL that
#     already points at the real file/page.
#   - this module resolves ad-supported shortener/mirror links (the kind
#     that show a "wait 10 seconds" / captcha / ad-wall page) to find the
#     real URL hiding behind them in the first place.
# Some of the mirror hosts this dispatches to (GDToT, HubDrive, etc.) may
# themselves also be Cloudflare-protected — if a resolved link then fails
# to download, that's exactly what Akbots/cf_bypass.py's automatic
# fetch()/stream_download() integration is for; the two are meant to be
# used one after the other, not as alternatives.
#
# bypasser.py/ddl.py call `open('config.json')` with a bare relative
# filename at import time — relative to the current working directory, NOT
# to their own file location — so importing them safely requires briefly
# chdir-ing into Akbots/shortener_lib/ during import only, then restoring
# the original cwd. Real credentials some of the specific services need
# (GDTot_Crypt, Laravel_Session, XSRF_TOKEN, DRIVEFIRE_CRYPT, KOLOP_CRYPT,
# HUBDRIVE_CRYPT, KATDRIVE_CRYPT, etc. — only a few of the many supported
# services actually need one) should be set as environment variables of
# the same name rather than editing that json file directly; its own
# getenv() helper already checks the environment first and only falls
# back to the (empty-by-default) json values.

import os
import re
import sys
import time
import asyncio
import logging
from typing import Optional

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

logger = logging.getLogger(__name__)

try:
    from config import AKBOTS_BRAND_NAME, AKBOTS_UPDATES_CHANNEL_URL, AKBOTS_SHARE_TEXT
except ImportError:
    AKBOTS_BRAND_NAME = "Akbots"
    AKBOTS_UPDATES_CHANNEL_URL = ""
    AKBOTS_SHARE_TEXT = (
        "Share and Support Bot, We are helping you to save your time and you "
        "can help us by sharing to your friends."
    )

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_GEAR  = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
E_LINK, E_CLOCK, E_BELL, E_SPARK = "🔗", "⏱️", "🔔", "✨"

_LB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shortener_lib")

_bypasser = _ddl = _freewall = None
_AVAILABLE = False       # bypasser + ddl (the actual shortener/mirror dispatch)
_FREEWALL_AVAILABLE = False  # freewall specifically (needs a live Google
                             # reCAPTCHA-solve at import time — see below)

if os.path.isdir(_LB_DIR):
    _cwd = os.getcwd()
    try:
        if _LB_DIR not in sys.path:
            sys.path.insert(0, _LB_DIR)
        os.chdir(_LB_DIR)  # bypasser.py / ddl.py open('config.json') relative to CWD
        import bypasser as _bypasser
        import ddl as _ddl
        _AVAILABLE = True
    except Exception as e:
        logger.warning(f"shortener_bypass: shortener_lib unavailable — {e}")
        _bypasser = _ddl = None
        _AVAILABLE = False
    finally:
        os.chdir(_cwd)

    # freewall.py runs a LIVE Google reCAPTCHA-v3 solve at import time
    # (`RTOKEN = RecaptchaV3()`, unconditional, no try/except of its own —
    # see bypasser.RecaptchaV3) purely to support its paywall-jump feature
    # (shutterstock/adobestock/getty/etc. watermark removal). That's a
    # separate concern from the shortener/mirror dispatch above, so it's
    # imported on its own: if Google ever changes that page's HTML, the
    # request times out, or anything else about that trick breaks, ONLY
    # paywall-jumping degrades — /bypass for ordinary shortlinks/mirrors
    # keeps working regardless.
    if _AVAILABLE:
        _cwd = os.getcwd()
        try:
            os.chdir(_LB_DIR)
            import freewall as _freewall
            _FREEWALL_AVAILABLE = True
        except Exception as e:
            logger.warning(f"shortener_bypass: freewall (paywall-jump) unavailable — {e}")
            _freewall = None
            _FREEWALL_AVAILABLE = False
        finally:
            os.chdir(_cwd)


def is_available() -> bool:
    """Cheap check other modules can use before offering a bypass."""
    return _AVAILABLE


def _run_bypass(url: str) -> str:
    """Blocking worker — mirrors the source project's main.py's own priority order:
    direct-download hosts -> paywall jump -> the big shortener dispatcher.
    Never raises; every branch returns a user-facing string either way,
    matching how that source project's own functions already behave.

    Skips the paywall-jump check entirely if freewall didn't import
    successfully (see _FREEWALL_AVAILABLE above) — falls straight through
    to the shortener dispatcher instead of erroring."""
    try:
        if _bypasser.ispresent(_ddl.ddllist, url):
            return _ddl.direct_link_generator(url)
        if _FREEWALL_AVAILABLE and _freewall.pass_paywall(url, check=True):
            result = _freewall.pass_paywall(url)
            return result if result else "Failed to jump the paywall."
        result = _bypasser.shortners(url)
        return result if result else "No bypass available for this link."
    except Exception as e:
        return f"Error: {e}"


async def bypass_link(url: str) -> Optional[str]:
    """
    Resolve a shortlink/mirror-host URL to its real direct link. Any other
    plugin can call this too:

        from Akbots.shortener_bypass import bypass_link

        result = await bypass_link(url)

    Tries the fast HTTP-only dispatcher (bypasser.py/ddl.py) first; only
    for links it doesn't cover, falls back to the heavier headless-browser
    tier (Akbots/playwright_bypass.py — the bundled bypass-shortlinks
    userscript running in real Chromium), and only if that tier's own
    routing rules actually claim the domain. That fallback fails silently
    (returns None from playwright_bypass) if Playwright/Chromium isn't
    installed, so this never raises just because that heavier tier is
    unavailable on a given host.

    Returns the resolved link/text, or None if shortener_lib isn't
    available at all (missing files/deps) — as opposed to an unsupported
    link, which still returns a string explaining that.
    """
    fast_result = None
    if _AVAILABLE:
        fast_result = await asyncio.to_thread(_run_bypass, url)
        ok = fast_result and not fast_result.lower().startswith("error:") and "no bypass available" not in fast_result.lower()
        if ok:
            return fast_result

    try:
        from Akbots.playwright_bypass import playwright_bypass, is_playwright_bypass_site
        if is_playwright_bypass_site(url):
            browser_result = await playwright_bypass(url)
            if browser_result:
                return browser_result
    except Exception as e:
        logger.info(f"shortener_bypass: playwright fallback tier skipped/failed for {url}: {e}")

    return fast_result if _AVAILABLE else None


async def _bump_bypass_count():
    """Best-effort global counter for /akstats — never blocks/breaks a
    bypass reply if the DB write fails."""
    try:
        from database.db import db
        current = await db.get_fs_config("akbots_bypass_count", 0) or 0
        await db.set_fs_config("akbots_bypass_count", current + 1)
    except Exception:
        pass


def _updates_button() -> Optional[InlineKeyboardMarkup]:
    if not AKBOTS_UPDATES_CHANNEL_URL:
        return None
    return InlineKeyboardMarkup([[make_button(f"{E_BELL} ᴜᴘᴅᴀᴛᴇs", url=AKBOTS_UPDATES_CHANNEL_URL, style=_BS.PRIMARY if _BS else None)]])


def _footer_blocks() -> str:
    """The two blockquotes shown at the bottom of every card, success or
    fail alike — matches the reference bot's layout exactly."""
    return (
        f"<blockquote>{AKBOTS_SHARE_TEXT}</blockquote>\n"
        f"<blockquote>Powered By <b>{AKBOTS_BRAND_NAME}</b></blockquote>"
    )


async def run_bypass_card(url: str) -> tuple[str, bool]:
    """Runs bypass_link(url) with an elapsed-time timer and returns a
    styled HTML card matching the reference bot's layout: on success,
    "Original Link:" / "Bypassed Link:" / "Time Taken: Xs", then the
    Share+Powered-By footer; on failure, "⚠️ No Script Found for: <url>"
    with the SAME footer underneath (not just on success). Used by both
    /shortbypass below and Akbots/akbypass.py's group auto-detect, so the
    two never drift out of sync with each other's formatting."""
    try:
        from Akbots.playwright_bypass import is_playwright_bypass_site
        browser_tier_might_work = is_playwright_bypass_site(url)
    except Exception:
        browser_tier_might_work = False

    footer = _footer_blocks()

    if not is_available() and not browser_tier_might_work:
        return (f"⚠️ <b>ɴᴏ sᴄʀɪᴘᴛ ғᴏᴜɴᴅ ғᴏʀ:</b>\n{url}\n\n{footer}", False)

    start = time.monotonic()
    result = await bypass_link(url)
    elapsed = time.monotonic() - start

    if not result or result.lower().startswith("error:") or "no bypass available" in result.lower():
        return (f"⚠️ <b>ɴᴏ sᴄʀɪᴘᴛ ғᴏᴜɴᴅ ғᴏʀ:</b>\n{url}\n\n{footer}", False)

    await _bump_bypass_count()
    card = (
        f"<blockquote>Original Link :</blockquote>\n"
        f"✅ {url}\n"
        f"<blockquote>Bypassed Link:</blockquote>\n"
        f"✅ {result}\n\n"
        f"<blockquote>Time Taken : {elapsed:.0f} seconds</blockquote>\n\n"
        f"{footer}"
    )
    return (card, True)


# Chat ids that sent a bare "/shortbypass" (no url) and are now waiting for
# the next message to contain the link — lets people do /shortbypass then
# paste the link separately, instead of only /shortbypass <url> in one go.
_PENDING_BYPASS: set = set()

_URL_RE = re.compile(r"https?://\S+")


async def _do_bypass(client: Client, message: Message, url: str):
    """Shared worker for both '/shortbypass <url>' and a pasted link that
    follows a bare '/shortbypass' — keeps the two paths byte-for-byte identical."""
    try:
        from Akbots.playwright_bypass import is_playwright_bypass_site
        browser_tier_might_work = is_playwright_bypass_site(url)
    except Exception:
        browser_tier_might_work = False

    if not is_available() and not browser_tier_might_work:
        return await message.reply_text(
            f"<b>{E_CROSS} Shortener-bypass module isn't available.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(
        f"<b>{E_GEAR} Processing...</b>", parse_mode=enums.ParseMode.HTML
    )

    card, ok = await run_bypass_card(url)
    await safe_edit(status.edit_text, 
        card,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=_updates_button(),
    )


@Client.on_message(filters.command(["shortbypass"]))
async def bypass_command(client: Client, message: Message):
    if len(message.command) < 2:
        _PENDING_BYPASS.add(message.chat.id)
        return await message.reply_text(
            f"<b>{E_GEAR} Send me the link now.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    _PENDING_BYPASS.discard(message.chat.id)
    await _do_bypass(client, message, message.command[1])


@Client.on_message(filters.text & filters.incoming, group=6)
async def bypass_pending_link(client: Client, message: Message):
    """Catches the follow-up message after a bare '/shortbypass' and treats
    the first link found in it as the url to bypass. Ignored entirely unless
    that chat is actually pending (set above), so this never interferes
    with normal messages/other commands."""
    if message.chat.id not in _PENDING_BYPASS:
        return
    text = message.text or ""
    if text.startswith("/"):
        return  # let another command run normally; stay pending
    m = _URL_RE.search(text)
    if not m:
        return  # not a link yet — keep waiting

    _PENDING_BYPASS.discard(message.chat.id)
    await _do_bypass(client, message, m.group(0))
