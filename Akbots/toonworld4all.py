# Akbots - Don't Remove Credit - @AkBots_Official
#
# /toonworld4all <url> — two modes, same as the toonworld4all() function
# this was ported from (JFZBypassBot's FZBypass/core/bypass_scrape.py):
#
#   Series page (no /episode/ in the url) — lists every episode's page
#   link, no bypassing needed yet (that happens per-episode).
#
#   Episode page — scrapes every quality's `/redirect/main.php?url=`
#   link, chases each through however many redirect hops it takes to
#   land on a known gate domain (rocklinks/link1s — see
#   Akbots/wpsafelink.py's KNOWN_GATES), then resolves that gate to the
#   final download link.
#
# The original's episode-mode redirect-chasing was a `requests.get(...,
# allow_redirects=False)` loop — synchronous, one link fully chased
# before the next one even starts, blocking the whole event loop each
# time since requests isn't async at all. Below, EVERY link's redirect
# chain is followed concurrently (asyncio.gather over aiohttp calls),
# and the gate resolution step after it is too — so an episode with 6
# quality links takes roughly as long as the slowest single one, not the
# sum of all six, and nothing blocks the bot for other chats meanwhile.

import asyncio
import logging
import re

import aiohttp
from bs4 import BeautifulSoup
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import safe_edit, E_CROSS
from Akbots.wpsafelink import resolve_known_gate, WPSafelinkError, KNOWN_GATES

logger = logging.getLogger(__name__)

E_GEAR = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

TOONWORLD4ALL_PATTERN = re.compile(r"https?://toonworld4all\.\S+", re.IGNORECASE)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_MAX_REDIRECT_HOPS = 15


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, headers={"User-Agent": _UA}) as r:
        return await r.text()


async def _chase_redirect(session: aiohttp.ClientSession, start_url: str) -> str | None:
    """Follows one redirect hop at a time (mirroring the original's
    manual Location-header loop) until landing on a URL containing a
    known gate name, or gives up after _MAX_REDIRECT_HOPS (the original
    had no cap at all — an unrecognized redirect chain would spin
    forever; this returns None instead so the caller can report it as a
    failed link rather than hanging)."""
    current = start_url
    for _ in range(_MAX_REDIRECT_HOPS):
        if any(gate in current for gate in KNOWN_GATES):
            return current
        try:
            async with session.get(current, headers={"User-Agent": _UA},
                                    allow_redirects=False) as r:
                location = r.headers.get("Location")
        except Exception as e:
            logger.warning(f"toonworld4all: redirect chase failed on {current}: {e}")
            return None
        if not location:
            return None
        current = location
    return None


async def _resolve_episode_link(session: aiohttp.ClientSession, redirect_url: str) -> str:
    """One quality link's full pipeline: chase its redirect chain to a
    known gate, then resolve that gate. Raises on any failure — callers
    gather with return_exceptions=True and format the exception as the
    per-link error text, matching the original's per-link try/except
    formatting."""
    gate_url = await _chase_redirect(session, redirect_url)
    if not gate_url:
        raise WPSafelinkError("Redirect chain never reached a known gate")
    return await resolve_known_gate(session, gate_url)


async def _series_listing(session: aiohttp.ClientSession, url: str) -> str:
    xml = await _fetch_text(session, url)
    soup = BeautifulSoup(xml, "html.parser")
    episode_links = soup.select('a[href*="/episode/"]')
    headings = soup.select('div[class*="mks_accordion_heading"]')
    title_match = re.search(r'"name":"(.+?)"', xml)
    series_title = title_match.group(1).split('"')[0] if title_match else "Series"

    text = f"<b><i>{series_title}</i></b>"
    for n, (heading, link) in enumerate(zip(headings, episode_links), start=1):
        ep_title = heading.strong.string if heading.strong else heading.get_text(strip=True)
        text += f"\n\n{n}. <i><b>{ep_title}</b></i>\n┖ <b>Link :</b> {link['href']}"
    return text


async def _episode_mode(session: aiohttp.ClientSession, url: str, status: Message) -> str:
    xml = await _fetch_text(session, url)
    soup = BeautifulSoup(xml, "html.parser")
    links = soup.select('a[href*="/redirect/main.php?url="]')
    titles = soup.select("h5")
    if not links or not titles:
        raise WPSafelinkError("No download links found on this episode page — layout may have changed")

    series_title = titles[0].string or titles[0].get_text(strip=True)
    quality_titles = titles[1:]
    if not quality_titles:
        raise WPSafelinkError("Found quality headers but no quality titles to match them to")
    slicer, _ = divmod(len(links), len(quality_titles))
    if slicer == 0:
        slicer = len(links)

    await safe_edit(status.edit_text,
        f"<b>{E_GEAR} Resolving {len(links)} link(s) across {len(quality_titles)} quality option(s)...</b>",
        parse_mode=enums.ParseMode.HTML)

    resolved = await asyncio.gather(
        *[_resolve_episode_link(session, link["href"]) for link in links],
        return_exceptions=True,
    )
    grouped = [resolved[i:i + slicer] for i in range(0, len(resolved), slicer)]

    text = f"<b><i>{series_title}</i></b>"
    for quality_title, group in zip(quality_titles, grouped):
        label = quality_title.string or quality_title.get_text(strip=True)
        text += f"\n\n<b>{label}</b>\n┃\n┖ <b>Links :</b> "
        pieces = []
        for link, result in zip(links, group):
            link_label = link.get_text(strip=True) or label
            if isinstance(result, Exception):
                pieces.append(f"<i>{result}</i>")
            else:
                pieces.append(f'<a href="{result}">{link_label}</a>')
        text += ", ".join(pieces)
    return text


async def bypass_toonworld4all(url: str, status: Message = None) -> str:
    """Public entry point — dispatches to series-listing or episode mode
    based on whether the url is a series page or a specific episode
    page, same distinction the original made via '/episode/' in url."""
    async with aiohttp.ClientSession() as session:
        if "/redirect/main.php?url=" in url:
            # A bare gate-chain link pasted directly (not a toonworld4all
            # page at all) — same shortcut the original had.
            resolved = await _chase_redirect(session, url)
            if not resolved:
                raise WPSafelinkError("Redirect chain never reached a known gate")
            final = await resolve_known_gate(session, resolved)
            return f"┎ <b>Source Link:</b> {url}\n┃\n┖ <b>Bypass Link:</b> {final}"
        if "/episode/" not in url:
            return await _series_listing(session, url)
        return await _episode_mode(session, url, status)


@Client.on_message(filters.command(["toonworld4all"]) & filters.private)
async def toonworld4all_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/toonworld4all &lt;series or episode page URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _run(message, message.command[1])


@Client.on_message(filters.text & filters.private & filters.regex(TOONWORLD4ALL_PATTERN) & ~filters.regex(r"^/"), group=1)
async def toonworld4all_autodetect(client: Client, message: Message):
    await _run(message, message.text.strip())


async def _run(message: Message, url: str):
    status = await message.reply_text(f"<b>{E_GEAR} Fetching page...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        text = await bypass_toonworld4all(url, status=status)
    except WPSafelinkError as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} {e}</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"toonworld4all: failed for {url}: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't process that link.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)

    if len(text) > 4000:
        # Telegram's ~4096-char message limit — same chunking approach
        # used elsewhere in this codebase for long bypass results.
        for i in range(0, len(text), 4000):
            await message.reply_text(text[i:i + 4000], parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        await status.delete()
    else:
        await safe_edit(status.edit_text, text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
