# Akbots - Don't Remove Credit - @AkBots_Official
#
# /meowtv, /meowverse, /meowtoon — search + play commands for the three
# Meow* content providers ported from the meowtv project (see
# meowtv_provider.py, meowverse_provider.py, meowtoon_provider.py). Search
# results and episode lists come back as buttons; picking one resolves and
# hands back the direct playback link(s) (proxied through
# workers/hls-proxy when HLS_WORKER_URL is set — see Akbots/hls_proxy.py).
#
# Auto-discovered by Pyrogram's plugin loader (plugins=dict(root="Akbots")
# in bot.py) — no manual registration needed.

import time
import math
import uuid
import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from Akbots.direct_utils import safe_edit, E_CHECK, E_CROSS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

from Akbots import meowtv_provider, meowverse_provider, meowtoon_provider, meowly_provider
from Akbots import meow_downloader
from database.db import db

logger = logging.getLogger(__name__)

E_GEAR = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'  # caption/text use ONLY — never as a button label (buttons don't parse HTML)
E_PLAY = '▶️'  # plain unicode — safe to use in both captions and button labels
E_BOLT = '⚡'

_PROVIDERS = {
    "tv": meowtv_provider,
    "verse": meowverse_provider,
    "toon": meowtoon_provider,
}
_LABELS = {"tv": "MeowTV", "verse": "MeowVerse", "toon": "MeowToon"}

# Short-token cache so callback_data (64-byte Telegram limit) never has to
# carry a raw provider id/title — tokens expire after 1 hour.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600


def _store(payload: dict) -> str:
    token = uuid.uuid4().hex[:12]
    _CACHE[token] = (time.time(), payload)
    # opportunistic cleanup
    if len(_CACHE) > 500:
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


# ── Watchlist (ported from meow-cli's favorites.py concept) ─────────────
# Reuses the bot's existing db.add_favourite()/get_favourites()/remove_favourite()
# infra (Akbots/history_favs.py) instead of a new collection — a meowly item
# is just stored as a favourite with a synthetic "meowly://<type>/<id>" url,
# so /favourites, /fav, and /unfav all keep working on it for free.

def _meowly_fav_url(media_type: str, item_id) -> str:
    return f"meowly://{media_type}/{item_id}"


async def _meowly_watchlist_button(user_id: int, media_type: str, item_id, title: str):
    url = _meowly_fav_url(media_type, item_id)
    favs = await db.get_favourites(user_id)
    already = any(f.get("url") == url for f in favs)
    if already:
        token = _store({"kind": "ly_fav_remove", "url": url, "title": title})
        return make_button("💔 Remove from Watchlist", callback_data=f"meowly:{token}", style=_style("SECONDARY"))
    token = _store({"kind": "ly_fav_add", "url": url, "title": title})
    return make_button("⭐ Add to Watchlist", callback_data=f"meowly:{token}", style=_style("SECONDARY"))


def _item_icon(media_type: str) -> str:
    return {"movie": "🎬", "tv": "📺", "person": "🧑", "company": "🏢"}.get(media_type, "🎞️")


def _movie_button_rows(items: list[dict], limit: int = 20) -> list[list]:
    """Build 'pick a title' buttons for any list of TMDB movie/tv/person/company
    items — reused by trending/top-rated/popular/discover/awards/etc. Clicking
    a button feeds back into the existing meowly_callback 'ly_details' flow."""
    buttons = []
    for item in items[:limit]:
        media_type = item.get("media_type") or ("movie" if item.get("title") else "tv")
        title = item.get("title") or item.get("name")
        if not title or not item.get("id"):
            continue
        year = (item.get("release_date") or item.get("first_air_date") or "")[:4]
        token = _store({"kind": "ly_details", "media_type": media_type, "id": item["id"]})
        label = f"{_item_icon(media_type)} {title}" + (f" ({year})" if year else "")
        buttons.append([make_button(label, callback_data=f"meowly:{token}", style=_style("SECONDARY"))])
    return buttons


async def _meowly_send_list(message_or_status, title: str, items: list[dict], not_found: str = "Nothing found."):
    buttons = _movie_button_rows(items)
    if not buttons:
        return await safe_edit(message_or_status.edit_text,
            f"<b>{E_CROSS} {not_found}</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(message_or_status.edit_text, f"<b>{E_CHECK} {title}</b>",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


def _require_meowly(handler):
    async def wrapped(client: Client, message: Message):
        if not meowly_provider.is_configured():
            return await message.reply_text(
                f"<b>{E_CROSS} Meowly isn't configured.</b>\n"
                f"Set <code>TMDB_API_KEY</code> in config/.env "
                f"(free key: themoviedb.org/settings/api).",
                parse_mode=enums.ParseMode.HTML)
        await handler(client, message)
    return wrapped


async def _do_search(client: Client, message: Message, provider_key: str, query: str):
    provider = _PROVIDERS[provider_key]
    label = _LABELS[provider_key]
    status = await message.reply_text(f"<b>{E_GEAR} Searching {label} for “{query}”...</b>",
                                       parse_mode=enums.ParseMode.HTML)
    try:
        results = await provider.search(query)
    except Exception as e:
        logger.warning(f"meow_commands: {label} search failed: {e}")
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} {label} search failed.</b>\n<i>{e}</i>",
            parse_mode=enums.ParseMode.HTML)

    if not results:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No {label} results for “{query}”.</b>",
            parse_mode=enums.ParseMode.HTML)

    buttons = []
    for item in results[:20]:
        if not item.get("id") or not item.get("title"):
            continue
        token = _store({"provider": provider_key, "id": item["id"], "kind": "details"})
        icon = "🎬" if item.get("type") == "movie" else "📺"
        buttons.append([make_button(f"{icon} {item['title']}", callback_data=f"meow:{token}",
                                     style=_style("SECONDARY"))])

    if not buttons:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No usable {label} results for “{query}”.</b>",
            parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} {label} results for “{query}”:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML)


def _make_search_handler(provider_key: str, command_name: str):
    async def handler(client: Client, message: Message):
        if len(message.command) < 2:
            return await message.reply_text(
                f"<b>{E_GEAR} Usage:</b> <code>/{command_name} &lt;search query&gt;</code>",
                parse_mode=enums.ParseMode.HTML)
        query = message.text.split(None, 1)[1].strip()
        await _do_search(client, message, provider_key, query)
    return handler


@Client.on_message(filters.private & filters.command(["meowtv"]))
async def meowtv_command(client: Client, message: Message):
    await _make_search_handler("tv", "meowtv")(client, message)


@Client.on_message(filters.private & filters.command(["meowverse"]))
async def meowverse_command(client: Client, message: Message):
    if not meowverse_provider.is_configured():
        return await message.reply_text(
            f"<b>{E_CROSS} MeowVerse isn't configured.</b>\n"
            f"Set <code>MEOWVERSE_SECRET_KEY_ENCRYPTED</code>, "
            f"<code>MEOWVERSE_DES_KEY</code>/<code>_IV</code> and "
            f"<code>MEOWVERSE_AES_KEY</code>/<code>_IV</code> in config/.env.",
            parse_mode=enums.ParseMode.HTML)
    await _make_search_handler("verse", "meowverse")(client, message)


@Client.on_message(filters.private & filters.command(["meowtoon"]))
async def meowtoon_command(client: Client, message: Message):
    await _make_search_handler("toon", "meowtoon")(client, message)


@Client.on_callback_query(filters.regex(r"^meow:"))
async def meow_callback(client: Client, query: CallbackQuery):
    if query.data == "meow:noop":
        return await query.answer()

    token = query.data.split(":", 1)[1]
    payload = _load(token)
    if not payload:
        return await query.answer("This result has expired — search again.", show_alert=True)

    provider = _PROVIDERS[payload["provider"]]
    label = _LABELS[payload["provider"]]
    await query.answer()

    if payload["kind"] == "details":
        await query.message.edit_text(f"<b>{E_GEAR} Loading details from {label}...</b>",
                                       parse_mode=enums.ParseMode.HTML)
        try:
            details = await provider.fetch_details(payload["id"])
        except Exception as e:
            logger.warning(f"meow_commands: {label} details failed: {e}")
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load details.</b>\n<i>{e}</i>",
                parse_mode=enums.ParseMode.HTML)

        if not details:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load details for this title.</b>",
                parse_mode=enums.ParseMode.HTML)

        episodes = details.get("episodes") or []
        if len(episodes) <= 1:
            # Single episode / movie — resolve stream directly.
            ep = episodes[0] if episodes else {"id": details["id"], "sourceMovieId": details["id"]}
            title = ep.get("title") or details.get("title") or label
            return await _resolve_and_reply(query, provider, label,
                                             ep.get("sourceMovieId") or details["id"], ep["id"],
                                             provider_key=payload["provider"], title=title)

        # Telegram caps an inline keyboard at 100 buttons, and long-running
        # shows/anime routinely have 100+ episodes — paginate 40 at a time
        # instead of silently truncating (episodes[40:] used to just vanish).
        EP_PAGE_SIZE = 40
        page = payload.get("page", 0)
        total_pages = max(1, math.ceil(len(episodes) / EP_PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        page_episodes = episodes[page * EP_PAGE_SIZE:(page + 1) * EP_PAGE_SIZE]

        buttons = []
        for ep in page_episodes:
            ep_token = _store({"provider": payload["provider"], "kind": "stream",
                                "movie_id": ep.get("sourceMovieId") or details["id"], "episode_id": ep["id"],
                                "title": f"{details.get('title') or label} {ep.get('title') or ''}".strip()})
            season = ep.get("season") or 1
            number = ep.get("number") or "?"
            title = ep.get("title") or f"Episode {number}"
            buttons.append([make_button(f"S{season}E{number} · {title}", callback_data=f"meow:{ep_token}",
                                         style=_style("SECONDARY"))])

        if total_pages > 1:
            nav_row = []
            if page > 0:
                prev_token = _store({"provider": payload["provider"], "kind": "details",
                                      "id": payload["id"], "page": page - 1})
                nav_row.append(make_button("◀️ Prev", callback_data=f"meow:{prev_token}", style=_style("SECONDARY")))
            nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="meow:noop",
                                        style=_style("SECONDARY")))
            if page < total_pages - 1:
                next_token = _store({"provider": payload["provider"], "kind": "details",
                                      "id": payload["id"], "page": page + 1})
                nav_row.append(make_button("Next ▶️", callback_data=f"meow:{next_token}", style=_style("SECONDARY")))
            buttons.append(nav_row)

        caption = f"<b>{E_CHECK} {details.get('title') or label}</b>\n<i>Pick an episode ({len(episodes)} total):</i>"
        return await safe_edit(query.message.edit_text, caption,
                                reply_markup=InlineKeyboardMarkup(buttons),
                                parse_mode=enums.ParseMode.HTML)

    if payload["kind"] == "stream":
        return await _resolve_and_reply(query, provider, label, payload["movie_id"], payload["episode_id"],
                                         provider_key=payload["provider"], title=payload.get("title") or label)

    if payload["kind"] == "download":
        return await _download_and_upload(client, query, provider, label,
                                           payload["movie_id"], payload["episode_id"],
                                           payload.get("title") or label, payload.get("quality"))


async def _resolve_and_reply(query: CallbackQuery, provider, label: str, movie_id: str, episode_id: str,
                              provider_key: str = None, title: str = None):
    await query.message.edit_text(f"<b>{E_GEAR} Resolving stream link...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        stream = await provider.fetch_stream_url(movie_id, episode_id)
    except Exception as e:
        logger.warning(f"meow_commands: {label} stream resolve failed: {e}")
        return await safe_edit(query.message.edit_text,
            f"<b>{E_CROSS} Couldn't resolve a stream link.</b>\n<i>{e}</i>",
            parse_mode=enums.ParseMode.HTML)

    if not stream or not stream.get("videoUrl"):
        logger.warning(f"meow_commands: {label} fetch_stream_url({movie_id}, {episode_id}) returned no videoUrl")
        return await safe_edit(query.message.edit_text,
            f"<b>{E_CROSS} No playable stream found.</b>",
            parse_mode=enums.ParseMode.HTML)

    qualities = stream.get("qualities") or [{"quality": "Play", "url": stream["videoUrl"]}]
    buttons = [[make_button(f"▶️ {q.get('quality') or 'Play'}", url=q["url"],
                             style=_style("PRIMARY"))] for q in qualities if q.get("url")]

    # Download-to-Telegram buttons (Akbots/meow_downloader.py) — re-resolves
    # the stream fresh at download time rather than caching the (often
    # short-lived) CDN URL, so only movie_id/episode_id/quality go in the
    # token.
    if meow_downloader.is_download_available() and provider_key:
        dl_buttons = []
        for q in qualities:
            if not q.get("url"):
                continue
            dl_token = _store({"provider": provider_key, "kind": "download",
                                "movie_id": movie_id, "episode_id": episode_id,
                                "title": title or label, "quality": q.get("quality")})
            dl_buttons.append(make_button(f"⬇️ {q.get('quality') or 'Download'}",
                                           callback_data=f"meow:{dl_token}", style=_style("SECONDARY")))
        if dl_buttons:
            # Two per row so the quality list doesn't get too tall.
            buttons.append(dl_buttons[:2])
            if len(dl_buttons) > 2:
                buttons.append(dl_buttons[2:4])

    subtitles = stream.get("subtitles") or []
    sub_note = f"\n<i>{len(subtitles)} subtitle track(s) available.</i>" if subtitles else ""

    await safe_edit(query.message.edit_text,
        f"<b>{E_CHECK} {label} stream ready.</b>{sub_note}\n"
        f"<i>Tap a quality to play, or ⬇️ to download &amp; get it sent here.</i>",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        parse_mode=enums.ParseMode.HTML)


async def _download_and_upload(client: Client, query: CallbackQuery, provider, label: str,
                                movie_id: str, episode_id: str, title: str, quality: str = None):
    """Resolves the stream fresh, downloads it via Akbots/meow_downloader.py
    (yt-dlp), then uploads the file to the chat — same status-message /
    progress-box flow as Akbots/ytdl.py's download commands."""
    status = query.message
    await safe_edit(status.edit_text, f"<b>{E_GEAR} Resolving stream link...</b>",
                     parse_mode=enums.ParseMode.HTML)
    try:
        stream = await provider.fetch_stream_url(movie_id, episode_id)
    except Exception as e:
        logger.warning(f"meow_commands: {label} download-resolve failed: {e}")
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Couldn't resolve a stream link.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not stream or not stream.get("videoUrl"):
        logger.warning(f"meow_commands: {label} download-resolve({movie_id}, {episode_id}) returned no videoUrl")
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No playable stream found.</b>", parse_mode=enums.ParseMode.HTML)

    path = None
    try:
        path = await meow_downloader.download_stream(stream, title, status, quality=quality)
        await safe_edit(status.edit_text, f"<b>{E_BOLT} Uploading to Telegram...</b>",
                         parse_mode=enums.ParseMode.HTML)
        await client.send_video(
            chat_id=query.message.chat.id,
            video=path,
            caption=f"<b>{E_CHECK} {title}</b>{f' · {quality}' if quality else ''}\n<i>via {label}</i>",
            parse_mode=enums.ParseMode.HTML,
            supports_streaming=True,
        )
        await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"meow_commands: {label} download failed: {e}")
        await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Download failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    finally:
        if path:
            meow_downloader.cleanup(path)


async def _meowly_resolve_and_reply(query: CallbackQuery, media_type: str, item_id, season, episode, title: str):
    """meowly's equivalent of _resolve_and_reply() above — resolves a real
    (non-iframe) stream via Akbots/meowly_provider.fetch_stream_url() ->
    Akbots/meowly_resolvers.py, then offers Play/Download buttons the same
    way MeowTV/MeowVerse/MeowToon do."""
    await query.message.edit_text(f"<b>{E_GEAR} Resolving stream link...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        stream = await meowly_provider.fetch_stream_url(media_type, item_id, season, episode, title)
    except Exception as e:
        logger.warning(f"meow_commands: meowly stream resolve failed: {e}")
        return await safe_edit(query.message.edit_text,
            f"<b>{E_CROSS} Couldn't resolve a stream link.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not stream or not stream.get("videoUrl"):
        logger.warning(f"meow_commands: meowly fetch_stream_url({media_type}, {item_id}) returned no videoUrl")
        return await safe_edit(query.message.edit_text,
            f"<b>{E_CROSS} No playable stream found on any resolver "
            f"(VidSrc/VidRock/Peachify/Videasy).</b>", parse_mode=enums.ParseMode.HTML)

    qualities = stream.get("qualities") or [{"quality": "Play", "url": stream["videoUrl"]}]
    buttons = [[make_button(f"▶️ {q.get('quality') or 'Play'}", url=q["url"],
                             style=_style("PRIMARY"))] for q in qualities if q.get("url")]

    if meow_downloader.is_download_available():
        dl_buttons = []
        for q in qualities:
            if not q.get("url"):
                continue
            dl_token = _store({"kind": "ly_dl_go", "media_type": media_type, "id": item_id,
                                "season": season, "episode": episode, "title": title,
                                "quality": q.get("quality")})
            dl_buttons.append(make_button(f"⬇️ {q.get('quality') or 'Download'}",
                                           callback_data=f"meowly:{dl_token}", style=_style("SECONDARY")))
        if dl_buttons:
            buttons.append(dl_buttons[:2])
            if len(dl_buttons) > 2:
                buttons.append(dl_buttons[2:4])

    subtitles = stream.get("subtitles") or []
    sub_note = f"\n<i>{len(subtitles)} subtitle track(s) available.</i>" if subtitles else ""

    await safe_edit(query.message.edit_text,
        f"<b>{E_CHECK} {title} stream ready.</b>{sub_note}\n"
        f"<i>Tap a quality to play, or ⬇️ to download &amp; get it sent here.</i>",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        parse_mode=enums.ParseMode.HTML)


async def _meowly_download_and_upload(client: Client, query: CallbackQuery, media_type: str, item_id,
                                       season, episode, title: str, quality: str = None):
    """meowly's equivalent of _download_and_upload() above."""
    status = query.message
    await safe_edit(status.edit_text, f"<b>{E_GEAR} Resolving stream link...</b>",
                     parse_mode=enums.ParseMode.HTML)
    try:
        stream = await meowly_provider.fetch_stream_url(media_type, item_id, season, episode, title)
    except Exception as e:
        logger.warning(f"meow_commands: meowly download-resolve failed: {e}")
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Couldn't resolve a stream link.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not stream or not stream.get("videoUrl"):
        logger.warning(f"meow_commands: meowly download-resolve({media_type}, {item_id}) returned no videoUrl")
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No playable stream found.</b>", parse_mode=enums.ParseMode.HTML)

    path = None
    try:
        path = await meow_downloader.download_stream(stream, title, status, quality=quality)
        await safe_edit(status.edit_text, f"<b>{E_BOLT} Uploading to Telegram...</b>",
                         parse_mode=enums.ParseMode.HTML)
        await client.send_video(
            chat_id=query.message.chat.id,
            video=path,
            caption=f"<b>{E_CHECK} {title}</b>{f' · {quality}' if quality else ''}\n<i>via Meowly</i>",
            parse_mode=enums.ParseMode.HTML,
            supports_streaming=True,
        )
        await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"meow_commands: meowly download failed: {e}")
        await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Download failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    finally:
        if path:
            meow_downloader.cleanup(path)


# ── Meowly (TMDB metadata + public embed servers) ───────────────────────
# Different shape from the other three (no proprietary stream resolving,
# just TMDB details + public embed iframe links), so it gets its own small
# command/callback set below, sharing the same _store()/_load() cache.

@Client.on_message(filters.private & filters.command(["meowly"]))
async def meowly_command(client: Client, message: Message):
    if not meowly_provider.is_configured():
        return await message.reply_text(
            f"<b>{E_CROSS} Meowly isn't configured.</b>\n"
            f"Set <code>TMDB_API_KEY</code> in config/.env "
            f"(free key: themoviedb.org/settings/api).",
            parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/meowly &lt;search query&gt;</code>",
            parse_mode=enums.ParseMode.HTML)

    query = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_GEAR} Searching for “{query}”...</b>",
                                       parse_mode=enums.ParseMode.HTML)
    try:
        results = await meowly_provider.search(query)
    except Exception as e:
        logger.warning(f"meow_commands: meowly search failed: {e}")
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Search failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not results:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No results for “{query}”.</b>", parse_mode=enums.ParseMode.HTML)

    buttons = []
    for item in results[:20]:
        title = item.get("title") or item.get("name")
        if not title:
            continue
        year = (item.get("release_date") or item.get("first_air_date") or "")[:4]
        icon = _item_icon(item.get("media_type"))
        token = _store({"kind": "ly_details", "media_type": item["media_type"], "id": item["id"]})
        label = f"{icon} {title}" + (f" ({year})" if year else "")
        buttons.append([make_button(label, callback_data=f"meowly:{token}", style=_style("SECONDARY"))])

    await safe_edit(status.edit_text, f"<b>{E_CHECK} Results for “{query}”:</b>",
                     reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


async def _meowly_send_watch_links(query: CallbackQuery, details: dict, media_type: str,
                                    season: int = None, episode: int = None):
    title = details.get("title") or details.get("name") or "Untitled"
    year = (details.get("release_date") or details.get("first_air_date") or "")[:4]
    overview = (details.get("overview") or "").strip()
    if len(overview) > 500:
        overview = overview[:497] + "..."
    rating = details.get("vote_average")

    ep_note = f" — S{season}E{episode}" if media_type == "tv" and season else ""
    caption = f"<b>{title}</b>{f' ({year})' if year else ''}{ep_note}\n"
    if rating:
        caption += f"⭐ {rating:.1f}/10\n"
    if overview:
        caption += f"\n<i>{overview}</i>"

    links = meowly_provider.embed_links(media_type, details.get("id"), season, episode)
    buttons = [[make_button(f"▶️ {l['name']}", url=l["url"], style=_style("PRIMARY"))] for l in links]

    try:
        trailer_url = await meowly_provider.get_trailer(media_type, str(details.get("id")))
        if trailer_url:
            buttons.append([make_button("🎞️ Trailer", url=trailer_url)])
    except Exception:
        pass

    dl_token = _store({"kind": "ly_download", "media_type": media_type, "id": details.get("id"),
                        "season": season, "episode": episode, "title": title})
    buttons.append([make_button("⬇️ Real Download", callback_data=f"meowly:{dl_token}", style=_style("SECONDARY"))])

    review_token = _store({"kind": "ly_reviews", "title": title, "date": details.get("release_date") or details.get("first_air_date")})
    buttons.append([make_button("📝 Reviews", callback_data=f"meowly:{review_token}", style=_style("SECONDARY"))])

    collection = details.get("belongs_to_collection")
    if media_type == "movie" and collection and collection.get("id"):
        coll_token = _store({"kind": "ly_collection", "id": collection["id"]})
        buttons.append([make_button(f"🎞️ Collection: {collection.get('name', 'Series')}",
                                     callback_data=f"meowly:{coll_token}", style=_style("SECONDARY"))])

    fav_button = await _meowly_watchlist_button(query.from_user.id, media_type, details.get("id"), title)
    buttons.append([fav_button])

    poster = meowly_provider.poster_url(details.get("poster_path"), "large")
    await query.message.delete()
    if poster:
        await query.message.reply_photo(poster, caption=caption,
                                         reply_markup=InlineKeyboardMarkup(buttons),
                                         parse_mode=enums.ParseMode.HTML)
    else:
        await query.message.reply_text(caption, reply_markup=InlineKeyboardMarkup(buttons),
                                        parse_mode=enums.ParseMode.HTML)


# ── Discovery commands (trending / top-rated / popular / upcoming / etc.) ──
# All follow the same shape: fetch a list from meowly_provider, show it as
# pick-a-title buttons via _meowly_send_list(), which feed back into the
# meowly_callback 'ly_details' flow above.

@Client.on_message(filters.private & filters.command(["meowtrend"]))
@_require_meowly
async def meowtrend_command(client: Client, message: Message):
    kind = message.command[1].lower() if len(message.command) > 1 else "all"
    if kind not in ("movie", "tv", "all"):
        kind = "all"
    status = await message.reply_text(f"<b>{E_GEAR} Loading trending...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        items = await meowly_provider.get_trending(kind)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    await _meowly_send_list(status, "Trending today:", items)


@Client.on_message(filters.private & filters.command(["meowtop"]))
@_require_meowly
async def meowtop_command(client: Client, message: Message):
    kind = message.command[1].lower() if len(message.command) > 1 else "movie"
    if kind not in ("movie", "tv"):
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/meowtop movie</code> or <code>/meowtop tv</code>",
            parse_mode=enums.ParseMode.HTML)
    status = await message.reply_text(f"<b>{E_GEAR} Loading top rated...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        items = await meowly_provider.get_top_rated(kind)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    await _meowly_send_list(status, f"Top rated {kind}:", items)


@Client.on_message(filters.private & filters.command(["meowpopular"]))
@_require_meowly
async def meowpopular_command(client: Client, message: Message):
    kind = message.command[1].lower() if len(message.command) > 1 else "movie"
    if kind not in ("movie", "tv"):
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/meowpopular movie</code> or <code>/meowpopular tv</code>",
            parse_mode=enums.ParseMode.HTML)
    status = await message.reply_text(f"<b>{E_GEAR} Loading popular...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        items = await meowly_provider.get_popular(kind)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    await _meowly_send_list(status, f"Popular {kind}:", items)


@Client.on_message(filters.private & filters.command(["meowupcoming"]))
@_require_meowly
async def meowupcoming_command(client: Client, message: Message):
    status = await message.reply_text(f"<b>{E_GEAR} Loading upcoming movies...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        items = await meowly_provider.get_upcoming()
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    await _meowly_send_list(status, "Upcoming movies:", items)


@Client.on_message(filters.private & filters.command(["meownow"]))
@_require_meowly
async def meownow_command(client: Client, message: Message):
    status = await message.reply_text(f"<b>{E_GEAR} Loading now playing...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        items = await meowly_provider.get_now_playing()
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    await _meowly_send_list(status, "Now playing in theaters:", items)


@Client.on_message(filters.private & filters.command(["meowairing"]))
@_require_meowly
async def meowairing_command(client: Client, message: Message):
    status = await message.reply_text(f"<b>{E_GEAR} Loading airing today...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        items = await meowly_provider.get_airing_today()
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    await _meowly_send_list(status, "TV airing today:", items)


@Client.on_message(filters.private & filters.command(["meowontheair"]))
@_require_meowly
async def meowontheair_command(client: Client, message: Message):
    status = await message.reply_text(f"<b>{E_GEAR} Loading on-the-air shows...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        items = await meowly_provider.get_on_the_air()
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    await _meowly_send_list(status, "Currently on the air:", items)


async def _meowly_send_watch_links_to_message(message: Message, details: dict, media_type: str,
                                               season: int = None, episode: int = None):
    """Same as _meowly_send_watch_links but starting from a Message instead of
    a CallbackQuery (used by /meowrandom, which has no callback to edit)."""
    title = details.get("title") or details.get("name") or "Untitled"
    year = (details.get("release_date") or details.get("first_air_date") or "")[:4]
    overview = (details.get("overview") or "").strip()
    if len(overview) > 500:
        overview = overview[:497] + "..."
    rating = details.get("vote_average")
    caption = f"<b>{title}</b>{f' ({year})' if year else ''}\n"
    if rating:
        caption += f"⭐ {rating:.1f}/10\n"
    if overview:
        caption += f"\n<i>{overview}</i>"

    links = meowly_provider.embed_links(media_type, details.get("id"), season, episode)
    buttons = [[make_button(f"▶️ {l['name']}", url=l["url"], style=_style("PRIMARY"))] for l in links]
    try:
        trailer_url = await meowly_provider.get_trailer(media_type, str(details.get("id")))
        if trailer_url:
            buttons.append([make_button("🎞️ Trailer", url=trailer_url)])
    except Exception:
        pass

    dl_token = _store({"kind": "ly_download", "media_type": media_type, "id": details.get("id"),
                        "season": season, "episode": episode, "title": title})
    buttons.append([make_button("⬇️ Real Download", callback_data=f"meowly:{dl_token}", style=_style("SECONDARY"))])

    review_token = _store({"kind": "ly_reviews", "title": title, "date": details.get("release_date") or details.get("first_air_date")})
    buttons.append([make_button("📝 Reviews", callback_data=f"meowly:{review_token}", style=_style("SECONDARY"))])

    collection = details.get("belongs_to_collection")
    if media_type == "movie" and collection and collection.get("id"):
        coll_token = _store({"kind": "ly_collection", "id": collection["id"]})
        buttons.append([make_button(f"🎞️ Collection: {collection.get('name', 'Series')}",
                                     callback_data=f"meowly:{coll_token}", style=_style("SECONDARY"))])

    fav_button = await _meowly_watchlist_button(message.from_user.id, media_type, details.get("id"), title)
    buttons.append([fav_button])

    poster = meowly_provider.poster_url(details.get("poster_path"), "large")
    if poster:
        await message.reply_photo(poster, caption=caption,
                                   reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(caption, reply_markup=InlineKeyboardMarkup(buttons),
                                  parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command(["meowrandom"]))
@_require_meowly
async def meowrandom_command(client: Client, message: Message):
    status = await message.reply_text(f"<b>{E_GEAR} Picking something random...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        item = await meowly_provider.get_random_content()
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    if not item:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't find anything — try again.</b>",
                                parse_mode=enums.ParseMode.HTML)
    media_type = item.get("media_type", "movie")
    try:
        details = await meowly_provider.get_details(media_type, str(item["id"]))
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to load details.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    if not details:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to load details.</b>",
                                parse_mode=enums.ParseMode.HTML)
    if media_type == "movie":
        await status.delete()
        return await _meowly_send_watch_links_to_message(message, details, "movie")
    # tv — let them pick a season same as the normal flow
    token = _store({"kind": "ly_details", "media_type": "tv", "id": item["id"]})
    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} {details.get('name', 'Untitled')}</b>\n<i>Tap below to pick a season:</i>",
        reply_markup=InlineKeyboardMarkup([[make_button(
            f"📺 {details.get('name', 'Untitled')}", callback_data=f"meowly:{token}",
            style=_style("SECONDARY"))]]),
        parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command(["meowgenres"]))
@_require_meowly
async def meowgenres_command(client: Client, message: Message):
    kind = message.command[1].lower() if len(message.command) > 1 else "movie"
    if kind not in ("movie", "tv"):
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/meowgenres movie</code> or <code>/meowgenres tv</code>",
            parse_mode=enums.ParseMode.HTML)
    status = await message.reply_text(f"<b>{E_GEAR} Loading genres...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        genres = await meowly_provider.get_genre_list(kind)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    if not genres:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No genres found.</b>", parse_mode=enums.ParseMode.HTML)

    buttons, row = [], []
    for g in genres:
        token = _store({"kind": "ly_genre", "type": kind, "genre_id": g["id"], "genre_name": g["name"]})
        row.append(make_button(g["name"], callback_data=f"meowly:{token}", style=_style("SECONDARY")))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    await safe_edit(status.edit_text, f"<b>{E_CHECK} Pick a {kind} genre:</b>",
                     reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command(["meowawards"]))
@_require_meowly
async def meowawards_command(client: Client, message: Message):
    buttons = []
    for key, meta in meowly_provider.AWARD_LISTS.items():
        token = _store({"kind": "ly_award", "award_key": key})
        buttons.append([make_button(meta["name"], callback_data=f"meowly:{token}", style=_style("SECONDARY"))])
    await message.reply_text(f"<b>{E_CHECK} Pick an award category:</b>",
                              reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command(["meowpeople"]))
@_require_meowly
async def meowpeople_command(client: Client, message: Message):
    page = 1
    if len(message.command) > 1 and message.command[1].isdigit():
        page = max(1, int(message.command[1]))
    status = await message.reply_text(f"<b>{E_GEAR} Loading popular people...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        data = await meowly_provider.get_popular_people(page)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    people = [{**p, "media_type": "person"} for p in (data or {}).get("results") or []]
    await _meowly_send_list(status, f"Popular people (page {page}):", people)


# Well-known TV network TMDB IDs — the site doesn't expose a network picker
# either, so this curated shortlist covers the major streamers/broadcasters.
_NETWORKS = {
    "Netflix": 213, "HBO": 49, "Disney+": 2739, "Apple TV+": 2552,
    "Amazon": 1024, "Hulu": 453, "AMC": 174, "BBC": 4, "FX": 88,
}


@Client.on_message(filters.private & filters.command(["meownetworks"]))
@_require_meowly
async def meownetworks_command(client: Client, message: Message):
    buttons, row = [], []
    for name, net_id in _NETWORKS.items():
        token = _store({"kind": "ly_network", "id": net_id, "name": name})
        row.append(make_button(name, callback_data=f"meowly:{token}", style=_style("SECONDARY")))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    await message.reply_text(f"<b>{E_CHECK} Pick a network:</b>",
                              reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command(["meowlist", "meowwatchlist"]))
@_require_meowly
async def meowlist_command(client: Client, message: Message):
    favs = await db.get_favourites(message.from_user.id)
    meowly_favs = [f for f in favs if str(f.get("url", "")).startswith("meowly://")]
    if not meowly_favs:
        return await message.reply_text(
            f"<b>{E_CROSS} Your watchlist is empty.</b>\n"
            f"<i>Tap ⭐ Add to Watchlist on any title's play-links screen to save it here.</i>",
            parse_mode=enums.ParseMode.HTML)

    buttons = []
    for f in meowly_favs[:30]:
        try:
            media_type, item_id = f["url"].removeprefix("meowly://").split("/", 1)
        except ValueError:
            continue
        icon = _item_icon(media_type)
        token = _store({"kind": "ly_details", "media_type": media_type, "id": item_id})
        buttons.append([make_button(f"{icon} {f.get('title', 'Untitled')}",
                                     callback_data=f"meowly:{token}", style=_style("SECONDARY"))])

    await message.reply_text(
        f"<b>{E_CHECK} Your watchlist ({len(meowly_favs)}):</b>\n"
        f"<i>Use /unfav &lt;link&gt; to remove — or just open a title and tap 💔 Remove.</i>",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command(["meowfeed"]))
@_require_meowly
async def meowfeed_command(client: Client, message: Message):
    start = 0
    if len(message.command) > 1 and message.command[1].isdigit():
        start = int(message.command[1])
    status = await message.reply_text(f"<b>{E_GEAR} Loading genre feed...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        rows = await meowly_provider.get_next_genres(start, limit=3, kind="all")
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                parse_mode=enums.ParseMode.HTML)
    if not rows:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No more genres.</b> Try <code>/meowfeed 0</code> to start over.",
            parse_mode=enums.ParseMode.HTML)

    await status.delete()
    for row in rows:
        buttons = _movie_button_rows(row["movies"], limit=8)
        if buttons:
            await message.reply_text(f"<b>{row['title']}</b>",
                                      reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    more_token_start = start + 3
    await message.reply_text("<i>Keep scrolling?</i>",
        reply_markup=InlineKeyboardMarkup([[make_button(
            "➡️ More genres", callback_data=f"meowlyfeed:{more_token_start}", style=_style("SECONDARY"))]]),
        parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^meowlyfeed:"))
async def meowlyfeed_callback(client: Client, query: CallbackQuery):
    start = int(query.data.split(":", 1)[1])
    await query.answer()
    try:
        rows = await meowly_provider.get_next_genres(start, limit=3, kind="all")
    except Exception as e:
        return await query.message.edit_text(f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    if not rows:
        return await query.message.edit_text(
            f"<b>{E_CROSS} No more genres.</b> Try <code>/meowfeed 0</code> to start over.",
            parse_mode=enums.ParseMode.HTML)

    await query.message.delete()
    for row in rows:
        buttons = _movie_button_rows(row["movies"], limit=8)
        if buttons:
            await query.message.reply_text(f"<b>{row['title']}</b>",
                reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    more_start = start + 3
    await query.message.reply_text("<i>Keep scrolling?</i>",
        reply_markup=InlineKeyboardMarkup([[make_button(
            "➡️ More genres", callback_data=f"meowlyfeed:{more_start}", style=_style("SECONDARY"))]]),
        parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^meowly:"))
async def meowly_callback(client: Client, query: CallbackQuery):
    if query.data == "meowly:noop":
        return await query.answer()

    token = query.data.split(":", 1)[1]
    payload = _load(token)
    if not payload:
        return await query.answer("This result has expired — search again.", show_alert=True)
    await query.answer()

    if payload["kind"] == "ly_details":
        media_type, item_id = payload["media_type"], payload["id"]

        if media_type == "person":
            await query.message.edit_text(f"<b>{E_GEAR} Loading person...</b>", parse_mode=enums.ParseMode.HTML)
            try:
                person = await meowly_provider.get_person_details(str(item_id))
                credits = await meowly_provider.get_person_credits(str(item_id))
            except Exception as e:
                return await safe_edit(query.message.edit_text,
                    f"<b>{E_CROSS} Couldn't load person.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
            if not person:
                return await safe_edit(query.message.edit_text,
                    f"<b>{E_CROSS} Couldn't load person.</b>", parse_mode=enums.ParseMode.HTML)

            bio = (person.get("biography") or "").strip()
            if len(bio) > 600:
                bio = bio[:597] + "..."
            caption = f"<b>{person.get('name', 'Unknown')}</b>\n"
            if person.get("known_for_department"):
                caption += f"<i>{person['known_for_department']}</i>\n"
            if bio:
                caption += f"\n{bio}\n"

            cast = sorted((credits or {}).get("cast") or [],
                          key=lambda c: c.get("popularity") or 0, reverse=True)
            buttons = _movie_button_rows(cast, limit=15)
            photo = meowly_provider.poster_url(person.get("profile_path"), "large")
            await query.message.delete()
            if photo:
                return await query.message.reply_photo(photo, caption=caption,
                    reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                    parse_mode=enums.ParseMode.HTML)
            return await query.message.reply_text(caption,
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                parse_mode=enums.ParseMode.HTML)

        if media_type == "company":
            await query.message.edit_text(f"<b>{E_GEAR} Loading company...</b>", parse_mode=enums.ParseMode.HTML)
            try:
                company = await meowly_provider.get_company_details(str(item_id))
                discover = await meowly_provider.get_discover_by_company(str(item_id))
            except Exception as e:
                return await safe_edit(query.message.edit_text,
                    f"<b>{E_CROSS} Couldn't load company.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
            name = (company or {}).get("name") or "Unknown company"
            buttons = _movie_button_rows((discover or {}).get("results") or [])
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CHECK} {name}</b>\n<i>Titles from this studio:</i>",
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                parse_mode=enums.ParseMode.HTML)

        await query.message.edit_text(f"<b>{E_GEAR} Loading details...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            details = await meowly_provider.get_details(media_type, str(item_id))
        except Exception as e:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load details.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        if not details:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load details.</b>", parse_mode=enums.ParseMode.HTML)

        if media_type == "movie":
            return await _meowly_send_watch_links(query, details, "movie")

        # TV — show season picker
        num_seasons = details.get("number_of_seasons") or 1
        buttons = []
        for n in range(1, min(num_seasons, 30) + 1):
            season_token = _store({"kind": "ly_season", "id": item_id, "season": n})
            buttons.append([make_button(f"Season {n}", callback_data=f"meowly:{season_token}",
                                         style=_style("SECONDARY"))])
        title = details.get("name") or "Untitled"
        return await safe_edit(query.message.edit_text,
            f"<b>{E_CHECK} {title}</b>\n<i>Pick a season:</i>",
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    if payload["kind"] == "ly_season":
        item_id, season = payload["id"], payload["season"]
        await query.message.edit_text(f"<b>{E_GEAR} Loading episodes...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            season_data = await meowly_provider.get_season_details(str(item_id), season)
        except Exception as e:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load episodes.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

        episodes = (season_data or {}).get("episodes") or []
        if not episodes:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} No episodes found for season {season}.</b>", parse_mode=enums.ParseMode.HTML)

        EP_PAGE_SIZE = 40
        page = payload.get("page", 0)
        total_pages = max(1, math.ceil(len(episodes) / EP_PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        page_episodes = episodes[page * EP_PAGE_SIZE:(page + 1) * EP_PAGE_SIZE]

        buttons = []
        for ep in page_episodes:
            ep_token = _store({"kind": "ly_episode", "id": item_id, "season": season,
                                "episode": ep.get("episode_number")})
            ep_title = ep.get("name") or f"Episode {ep.get('episode_number')}"
            buttons.append([make_button(f"E{ep.get('episode_number')} · {ep_title}",
                                         callback_data=f"meowly:{ep_token}", style=_style("SECONDARY"))])

        if total_pages > 1:
            nav_row = []
            if page > 0:
                prev_token = _store({"kind": "ly_season", "id": item_id, "season": season, "page": page - 1})
                nav_row.append(make_button("◀️ Prev", callback_data=f"meowly:{prev_token}", style=_style("SECONDARY")))
            nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="meowly:noop",
                                        style=_style("SECONDARY")))
            if page < total_pages - 1:
                next_token = _store({"kind": "ly_season", "id": item_id, "season": season, "page": page + 1})
                nav_row.append(make_button("Next ▶️", callback_data=f"meowly:{next_token}", style=_style("SECONDARY")))
            buttons.append(nav_row)

        return await safe_edit(query.message.edit_text,
            f"<b>{E_CHECK} Season {season}</b>\n<i>Pick an episode ({len(episodes)} total):</i>",
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    if payload["kind"] == "ly_episode":
        item_id, season, episode = payload["id"], payload["season"], payload["episode"]
        await query.message.edit_text(f"<b>{E_GEAR} Loading...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            details = await meowly_provider.get_details("tv", str(item_id))
        except Exception as e:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load details.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        if not details:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load details.</b>", parse_mode=enums.ParseMode.HTML)
        return await _meowly_send_watch_links(query, details, "tv", season, episode)

    if payload["kind"] == "ly_download":
        media_type, item_id = payload["media_type"], payload["id"]
        season, episode = payload.get("season"), payload.get("episode")
        title = payload.get("title") or "Untitled"
        return await _meowly_resolve_and_reply(query, media_type, item_id, season, episode, title)

    if payload["kind"] == "ly_dl_go":
        return await _meowly_download_and_upload(
            client, query, payload["media_type"], payload["id"],
            payload.get("season"), payload.get("episode"),
            payload.get("title") or "Untitled", payload.get("quality"))

    if payload["kind"] == "ly_genre":
        kind, genre_id, genre_name = payload["type"], payload["genre_id"], payload["genre_name"]
        await query.message.edit_text(f"<b>{E_GEAR} Loading {genre_name}...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            items = await meowly_provider.get_discover(kind, genre_id=str(genre_id))
        except Exception as e:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load genre.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        buttons = _movie_button_rows(items)
        if not buttons:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Nothing found for {genre_name}.</b>", parse_mode=enums.ParseMode.HTML)
        return await safe_edit(query.message.edit_text,
            f"<b>{E_CHECK} {genre_name}:</b>",
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    if payload["kind"] == "ly_award":
        award_key = payload["award_key"]
        meta = meowly_provider.AWARD_LISTS.get(award_key)
        if not meta:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Unknown award category.</b>", parse_mode=enums.ParseMode.HTML)
        await query.message.edit_text(f"<b>{E_GEAR} Loading {meta['name']}...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            items = await meowly_provider.get_list_details(meta["listId"])
        except Exception as e:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} Couldn't load list.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        buttons = _movie_button_rows(items)
        if not buttons:
            return await safe_edit(query.message.edit_text,
                f"<b>{E_CROSS} No winners found for {meta['name']}.</b>", parse_mode=enums.ParseMode.HTML)
        return await safe_edit(query.message.edit_text,
            f"<b>{E_CHECK} {meta['name']}:</b>",
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    # ── ly_reviews / ly_collection / ly_network — triggered from buttons on a
    # poster *photo* message, so we reply with a new message rather than
    # editing (Pyrogram can't edit_text a photo message's caption via edit_text).

    if payload["kind"] == "ly_reviews":
        title, date = payload["title"], payload.get("date")
        status = await query.message.reply_text(f"<b>{E_GEAR} Loading reviews...</b>", parse_mode=enums.ParseMode.HTML)
        slug = meowly_provider.get_moctale_slug(title, date)
        try:
            data = await meowly_provider.get_moctale_reviews(slug) if slug else None
        except Exception as e:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't load reviews.</b>\n<i>{e}</i>",
                                    parse_mode=enums.ParseMode.HTML)
        if not data:
            return await safe_edit(status.edit_text,
                f"<b>{E_CROSS} No Moctale reviews found for “{title}”.</b>\n"
                f"<i>(Needs <code>MOCTALE_COOKIE</code> configured, and the title to exist on Moctale.)</i>",
                parse_mode=enums.ParseMode.HTML)

        rating = data.get("overallRating") or data.get("rating")
        total = data.get("totalReviews") or data.get("total_reviews") or data.get("reviewsCount")
        summary = (data.get("aiSummary") or data.get("summary") or "").strip()
        if len(summary) > 700:
            summary = summary[:697] + "..."

        text = f"<b>📝 Reviews — {title}</b>\n"
        if rating:
            text += f"⭐ {rating:.1f}/10"
            if total:
                text += f" ({total} reviews)"
            text += "\n"
        if summary:
            text += f"\n<i>{summary}</i>"
        if not rating and not summary:
            text += "\n<i>No summary available.</i>"

        return await safe_edit(status.edit_text, text, parse_mode=enums.ParseMode.HTML)

    if payload["kind"] == "ly_collection":
        collection_id = payload["id"]
        status = await query.message.reply_text(f"<b>{E_GEAR} Loading collection...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            collection = await meowly_provider.get_collection(str(collection_id))
        except Exception as e:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't load collection.</b>\n<i>{e}</i>",
                                    parse_mode=enums.ParseMode.HTML)
        if not collection:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't load collection.</b>",
                                    parse_mode=enums.ParseMode.HTML)
        parts = [{**m, "media_type": "movie"} for m in collection.get("parts") or []]
        buttons = _movie_button_rows(parts)
        name = collection.get("name") or "Collection"
        if not buttons:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} No movies found in {name}.</b>",
                                    parse_mode=enums.ParseMode.HTML)
        return await safe_edit(status.edit_text, f"<b>{E_CHECK} {name}:</b>",
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    if payload["kind"] == "ly_network":
        network_id, fallback_name = payload["id"], payload["name"]
        await query.message.edit_text(f"<b>{E_GEAR} Loading {fallback_name} shows...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            network, discover = await asyncio.gather(
                meowly_provider.get_network_details(str(network_id)),
                meowly_provider.get_discover_by_network(str(network_id)),
            )
        except Exception as e:
            return await safe_edit(query.message.edit_text, f"<b>{E_CROSS} Failed.</b>\n<i>{e}</i>",
                                    parse_mode=enums.ParseMode.HTML)
        name = (network or {}).get("name") or fallback_name
        buttons = _movie_button_rows((discover or {}).get("results") or [])
        if not buttons:
            return await safe_edit(query.message.edit_text, f"<b>{E_CROSS} No shows found for {name}.</b>",
                                    parse_mode=enums.ParseMode.HTML)
        return await safe_edit(query.message.edit_text, f"<b>{E_CHECK} {name} shows:</b>",
            reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

    if payload["kind"] == "ly_fav_add":
        added = await db.add_favourite(query.from_user.id, payload["url"], title=payload["title"])
        toast = f"⭐ Added “{payload['title']}” to your watchlist!" if added else "Already in your watchlist."
        await query.answer(toast, show_alert=False)
        # Swap the button in place: Add -> Remove, without re-fetching TMDB details.
        remove_token = _store({"kind": "ly_fav_remove", "url": payload["url"]})
        new_buttons = []
        for row in query.message.reply_markup.inline_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data == query.data:
                    new_row.append(make_button("💔 Remove from Watchlist",
                                                callback_data=f"meowly:{remove_token}", style=_style("SECONDARY")))
                else:
                    new_row.append(btn)
            new_buttons.append(new_row)
        try:
            await query.message.edit_reply_markup(InlineKeyboardMarkup(new_buttons))
        except Exception:
            pass
        return

    if payload["kind"] == "ly_fav_remove":
        await db.remove_favourite(query.from_user.id, payload["url"])
        await query.answer("💔 Removed from your watchlist.", show_alert=False)
        add_token = _store({"kind": "ly_fav_add", "url": payload["url"], "title": payload.get("title", "")})
        new_buttons = []
        for row in query.message.reply_markup.inline_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data == query.data:
                    new_row.append(make_button("⭐ Add to Watchlist",
                                                callback_data=f"meowly:{add_token}", style=_style("SECONDARY")))
                else:
                    new_row.append(btn)
            new_buttons.append(new_row)
        try:
            await query.message.edit_reply_markup(InlineKeyboardMarkup(new_buttons))
        except Exception:
            pass
        return
