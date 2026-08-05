# /filmyfly — paste a filmyfly.luxe movie page link, get direct download
# links back (bypasses the linkmake.in -> new1.filesdl.in redirect chain).
#
# The actual scraping/resolving happens in a separate Cloudflare Worker
# (workers/filmyfly-resolver/src/worker.js — deploy it with `wrangler
# deploy` from that folder, then set FILMYFLY_WORKER_URL to the printed
# workers.dev URL). This plugin is just a thin client: hit the worker's
# GET /?url=<page> API and turn its JSON response into quality/size
# buttons, the same shape as Akbots/hdhub.py's link-picker.

import re
import logging

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup

from Akbots.direct_utils import safe_edit, E_CHECK, E_CROSS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_GEAR = '<emoji id=5341715473882955310>⚙️</emoji>'

try:
    from config import FILMYFLY_WORKER_URL
except ImportError:
    FILMYFLY_WORKER_URL = ""

logger = logging.getLogger(__name__)

# Matches any filmyfly.luxe (or a same-template mirror) movie/show page —
# mirrors change TLD/subdomain periodically the same way hdhub4u's do, so
# this doesn't hardcode just .luxe.
PATTERN = re.compile(r"(https?://)?[\w.-]*filmyfly[\w.-]*\.\w+/[\w-]+/?", re.IGNORECASE)


def extract_url(text: str):
    m = PATTERN.search(text)
    if not m:
        return None
    url = m.group(0).rstrip("/")
    return url if url.startswith("http") else f"https://{url}"


def is_available() -> bool:
    return bool(FILMYFLY_WORKER_URL)


async def _resolve(page_url: str):
    """Calls the deployed Cloudflare Worker and returns its parsed
    `movies[0]` dict, or raises with a short human-readable message."""
    api_url = f"{FILMYFLY_WORKER_URL.rstrip('/')}/"
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url, params={"url": page_url},
                                timeout=aiohttp.ClientTimeout(total=45)) as r:
            data = await r.json(content_type=None)
            if r.status != 200 or "movies" not in data:
                raise RuntimeError(data.get("error", f"worker returned {r.status}"))
            movies = data.get("movies") or []
            if not movies:
                raise RuntimeError("no download links found")
            return movies[0]


def _build_keyboard(movie: dict) -> InlineKeyboardMarkup:
    buttons = []
    for group in movie.get("downloadLinks", []):
        # Grouped shape: {"groupTitle": "...", "links": [{"size", "url"}]}
        if isinstance(group, dict) and "links" in group:
            label_prefix = group.get("groupTitle") or ""
            for link in group.get("links", []):
                size = link.get("size") or ""
                label = f"{label_prefix} — {size}".strip(" —") or "Download"
                buttons.append([make_button(f"📥 {label}", url=link.get("url"),
                                             style=_BS.PRIMARY if _BS else None)])
        # Flat fallback shape: {"size", "url"}
        elif isinstance(group, dict) and "url" in group:
            size = group.get("size") or "Download"
            buttons.append([make_button(f"📥 {size}", url=group.get("url"),
                                         style=_BS.PRIMARY if _BS else None)])
    return InlineKeyboardMarkup(buttons) if buttons else None


def _format_caption(movie: dict) -> str:
    title = movie.get("title") or "Unknown Title"
    parts = [f"<b>🎬 {title}</b>"]
    meta = []
    if movie.get("releaseYear"):
        meta.append(str(movie["releaseYear"])[:4])
    if movie.get("language"):
        meta.append(movie["language"])
    if movie.get("quality"):
        meta.append(movie["quality"])
    if meta:
        parts.append(" | ".join(meta))
    parts.append("<i>Tap a button below to download.</i>")
    return "\n\n".join(parts)


@Client.on_message(filters.command(["filmyfly"]))
async def filmyfly_command(client: Client, message: Message):
    if not is_available():
        return await message.reply_text(
            f"<b>{E_CROSS} Filmyfly resolver not configured.</b>\n"
            f"Deploy <code>workers/filmyfly-resolver</code> and set "
            f"<code>FILMYFLY_WORKER_URL</code>.",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/filmyfly &lt;movie page link&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    raw = message.text.split(None, 1)[1].strip()
    url = extract_url(raw) or raw
    status = await message.reply_text(f"<b>{E_GEAR} Resolving download links...</b>",
                                       parse_mode=enums.ParseMode.HTML)
    try:
        movie = await _resolve(url)
    except Exception as e:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Couldn't resolve this link.</b>\n<i>{e}</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    keyboard = _build_keyboard(movie)
    if not keyboard:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No download links found on this page.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    caption = _format_caption(movie)
    poster = movie.get("posterImage")
    try:
        if poster:
            await status.delete()
            await message.reply_photo(poster, caption=caption, reply_markup=keyboard,
                                       parse_mode=enums.ParseMode.HTML)
        else:
            await safe_edit(status.edit_text, caption, reply_markup=keyboard,
                             parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"filmyfly: failed to send result: {e}")
        await safe_edit(status.edit_text, caption, reply_markup=keyboard,
                         parse_mode=enums.ParseMode.HTML)


# ── Auto-detect: a pasted filmyfly link resolves the same way as /filmyfly ──
@Client.on_message(filters.text & filters.regex(PATTERN) & ~filters.command(["filmyfly"]))
async def filmyfly_auto_detect(client: Client, message: Message):
    if not is_available():
        return
    url = extract_url(message.text)
    if not url:
        return
    status = await message.reply_text(f"<b>{E_GEAR} Resolving download links...</b>",
                                       parse_mode=enums.ParseMode.HTML)
    try:
        movie = await _resolve(url)
    except Exception as e:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Couldn't resolve this link.</b>\n<i>{e}</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    keyboard = _build_keyboard(movie)
    if not keyboard:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No download links found on this page.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    caption = _format_caption(movie)
    poster = movie.get("posterImage")
    try:
        if poster:
            await status.delete()
            await message.reply_photo(poster, caption=caption, reply_markup=keyboard,
                                       parse_mode=enums.ParseMode.HTML)
        else:
            await safe_edit(status.edit_text, caption, reply_markup=keyboard,
                             parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"filmyfly: failed to send result: {e}")
        await safe_edit(status.edit_text, caption, reply_markup=keyboard,
                         parse_mode=enums.ParseMode.HTML)
