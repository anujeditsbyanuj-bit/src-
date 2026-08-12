# Akbots - Don't Remove Credit - @AkBots_Official
#
# /animehindi <episode-1-URL> — scrapes a Toonstream / AnimeDekho /
# HindiSubAnime / HindiAnimeVerse / WatchAnimeWorld / ToonsHub episode page
# (Akbots/anime_hindi_scraper.py + anime_hindi_extractors.py, both ported
# from the Anime-Episode-Scraper project), shows every resolved
# provider/quality as a button, and on tap actually downloads it — reusing
# this bot's existing pipelines instead of reimplementing them:
#   - .m3u8 links  -> Akbots/m3u8dl.py's _handle_m3u8_urls() (quality
#     picker, segment download, mux, upload — already built)
#   - direct file links (mp4 etc.) -> Akbots/aria2_dl.py's _handle()
#     (resumable aria2c download + upload — already built)
#
# KNOWN LIMITATION: neither shared pipeline currently forwards a custom
# Referer header per-download. aria2c_download() does send a same-origin
# Referer by default, which covers a lot of hosts, but a few extractors
# return a `referer` that must be the *original embed page's* origin, not
# the CDN's own — those may 403 through auto-download. When that happens
# this command also shows a plain "Open source link" button with the
# resolved URL + the referer it needs, so it's never a dead end.

import asyncio
import time
import uuid
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from Akbots.direct_utils import safe_edit, E_CHECK, E_CROSS, E_INFO
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

from Akbots.anime_hindi_scraper import Scraper, validate_url, SUPPORTED_DOMAINS
from Akbots import m3u8dl, aria2_dl

logger = logging.getLogger(__name__)

E_GEAR = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
E_DOWN = '<tg-emoji emoji-id="5215516614769609401">⬇️</tg-emoji>'

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600


def _store(payload: dict) -> str:
    token = uuid.uuid4().hex[:12]
    _CACHE[token] = (time.time(), payload)
    if len(_CACHE) > 300:
        cutoff = time.time() - _CACHE_TTL
        for k in [k for k, (ts, _) in _CACHE.items() if ts < cutoff]:
            _CACHE.pop(k, None)
    return token


def _load(token: str) -> dict | None:
    entry = _CACHE.get(token)
    if not entry:
        return None
    ts, payload = entry
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(token, None)
        return None
    return payload


def _style(kind):
    return getattr(_BS, kind, None) if _BS else None


@Client.on_message(filters.private & filters.command(
    ["animehindi", "toonstream", "animedekho", "hindisubanime", "hindianimeverse", "watchanimeworld", "toonshub"]
))
async def anime_hindi_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/animehindi &lt;episode page URL&gt;</code>\n"
            f"<i>Supports Toonstream, AnimeDekho, HindiSubAnime, HindiAnimeVerse, "
            f"WatchAnimeWorld, ToonsHub — paste a single episode's page URL.</i>",
            parse_mode=enums.ParseMode.HTML)

    url = message.text.split(None, 1)[1].strip()
    if not validate_url(url):
        return await message.reply_text(
            f"<b>{E_CROSS} That doesn't look like a supported episode page.</b>\n"
            f"<i>Supported: {', '.join(SUPPORTED_DOMAINS)}</i>",
            parse_mode=enums.ParseMode.HTML)

    status = await message.reply_text(f"<b>{E_GEAR} Scraping episode page...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        scraper = Scraper()
        data = await asyncio.to_thread(scraper.get_episode_data, url)
    except Exception as e:
        logger.warning(f"anime_hindi: scrape failed for {url}: {e}")
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Scrape failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not data or not data.get("Details"):
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No streaming sources found on that page.</b>", parse_mode=enums.ParseMode.HTML)

    title = data.get("Title") or "Episode"
    buttons = []
    fallback_count = 0

    for item in data["Details"]:
        provider = item.get("Provider") or "Unknown"
        stream = item.get("Streaming Links")
        source_url = stream.get("source") if isinstance(stream, dict) else None
        resolution = item.get("Resolution")
        label_suffix = f" [{resolution}]" if resolution else ""

        if source_url:
            token = _store({
                "kind": "dl", "provider": provider, "title": title,
                "source": source_url,
                "referer": stream.get("referer") if isinstance(stream, dict) else None,
            })
            buttons.append([make_button(f"{E_DOWN} {provider}{label_suffix}",
                                         callback_data=f"ahindi:{token}", style=_style("PRIMARY"))])
        else:
            # Couldn't resolve a direct stream for this provider — still
            # give an "Open" link to the embed page as a manual fallback.
            embed_url = item.get("Url")
            if embed_url:
                fallback_count += 1
                buttons.append([make_button(f"🔗 {provider} (open manually)", url=embed_url)])

    if not buttons:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Found sources but couldn't resolve any of them.</b>",
            parse_mode=enums.ParseMode.HTML)

    resolved_count = len(buttons) - fallback_count
    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} {title}</b>\n"
        f"<i>{resolved_count} downloadable source(s)"
        f"{f', {fallback_count} manual-only' if fallback_count else ''} — pick one:</i>",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^ahindi:"))
async def anime_hindi_callback(client: Client, query: CallbackQuery):
    token = query.data.split(":", 1)[1]
    payload = _load(token)
    if not payload:
        return await query.answer("This link has expired — scrape the page again.", show_alert=True)
    await query.answer()

    source = payload["source"]
    title = payload.get("title", "Episode")
    provider = payload.get("provider", "")
    label = f"{title} ({provider})" if provider else title

    if ".m3u8" in source.lower():
        await m3u8dl._handle_m3u8_urls(client, query.message, [source])
    else:
        await aria2_dl._handle(client, query.message, source, label_prefix=label)
