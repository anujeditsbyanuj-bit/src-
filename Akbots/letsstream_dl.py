# Akbots - Don't Remove Credit - @AkBots_Official
#
# Telegram wiring replicating letsstream2-main's video-source resolution
# (src/utils/video-source-loader.ts + src/hooks/use-streamflix-api.ts).
#
# letsstream2 is a React/Firebase frontend — there's no Python library to
# vendor, and no embedded scraper either: its embed-source list is fetched
# at runtime from a deployer-supplied JSON endpoint
# (VITE_VIDEO_SOURCE_API), not something baked into that repo. This module
# ports that same fetch+template+parse logic 1:1 so any JSON of that shape
# works here too — set LETSSTREAM_SOURCE_API_URL in config.py to point at
# one (letsstream2's own if you're running an instance, or something you
# host yourself), same "leave empty to disable" pattern as the other
# sidecar-style commands in this repo.
#
# Expected JSON shape (from video-source-loader.ts's JsonVideoSource):
#   {"sources": [
#     {"key": "...", "name": "...", "isApiSource": false,
#      "movieUrlPattern": "https://.../{id}",
#      "tvUrlPattern": "https://.../{id}/{season}/{episode}"},
#     ...
#   ]}
# "{id}"/"{season}"/"{episode}" get substituted with the TMDB id/season/
# episode. If isApiSource is true, the built URL is itself fetched and
# parsed as either a Watch32 response ({"servers": [{"url","name",
# "source","subtitles","error"}]}) or a StreamFlix response ({"links":
# [{"url","quality","tier"}]}) — see use-streamflix-api.ts's
# isWatch32Response/isStreamFlixResponse type guards, replicated below.
# If isApiSource is false/absent, the built URL is used directly as a
# watch-in-browser embed link, same as Akbots/meowly-style providers.

import uuid
import logging

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_CLOCK, safe_edit
from Akbots import meow_downloader
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    from config import LETSSTREAM_SOURCE_API_URL, TMDB_API_KEY
except ImportError:
    LETSSTREAM_SOURCE_API_URL = TMDB_API_KEY = ""

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)
_LS_SESSIONS = {}
_sources_cache = {"sources": None}  # simple process-lifetime cache, mirrors
                                     # the frontend's 1h staleTime react-query cache


def _trim_sessions():
    if len(_LS_SESSIONS) > 300:
        _LS_SESSIONS.pop(next(iter(_LS_SESSIONS)), None)


async def _get_sources(session: aiohttp.ClientSession) -> list[dict]:
    if _sources_cache["sources"] is not None:
        return _sources_cache["sources"]
    try:
        async with session.get(LETSSTREAM_SOURCE_API_URL,
                                headers={"Origin": "https://letsstream.example"},
                                timeout=_TIMEOUT) as r:
            data = await r.json(content_type=None)
    except Exception as e:
        logger.warning(f"letsstream_dl: fetching source list failed: {e}")
        return []
    sources = (data or {}).get("sources") or []
    _sources_cache["sources"] = sources
    return sources


def _build_url(source: dict, tmdb_id, season=None, episode=None) -> str | None:
    pattern = source.get("tvUrlPattern") if season else source.get("movieUrlPattern")
    if not pattern:
        return None
    url = pattern.replace("{id}", str(tmdb_id))
    if season:
        url = url.replace("{season}", str(season)).replace("{episode}", str(episode or 1))
    return url


def _is_watch32(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("servers"), list)


def _is_streamflix(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("links"), list)


def _label_links(links: list[dict]) -> list[dict]:
    """Mirrors use-streamflix-api.ts's labelLinks(): disambiguate repeated
    qualities as 'quality (1)', 'quality (2)', ..."""
    counts, seen = {}, {}
    for l in links:
        counts[l.get("quality")] = counts.get(l.get("quality"), 0) + 1
    out = []
    for l in links:
        q = l.get("quality")
        seen[q] = seen.get(q, 0) + 1
        label = f"{q} ({seen[q]})" if counts.get(q, 0) > 1 else q
        out.append({**l, "label": label})
    return out


def _convert_watch32(servers: list[dict]) -> list[dict]:
    out = []
    for s in servers:
        if not s.get("url") or s.get("error"):
            continue
        out.append({"url": s["url"], "quality": "Auto", "tier": s.get("source") or "Watch32",
                     "label": s.get("name"), "subtitles": s.get("subtitles") or []})
    return out


async def _resolve_api_source(session: aiohttp.ClientSession, url: str) -> list[dict]:
    try:
        async with session.get(url, timeout=_TIMEOUT) as r:
            data = await r.json(content_type=None)
    except Exception as e:
        logger.debug(f"letsstream_dl: api-source fetch failed for {url}: {e}")
        return []
    if _is_watch32(data):
        return _convert_watch32(data["servers"])
    if _is_streamflix(data):
        return _label_links(data["links"])
    return []


async def _resolve_all(tmdb_id, season=None, episode=None) -> list[dict]:
    """Returns a flat list of {url, label/quality, ...} across every
    configured source — API sources resolved to real links, non-API
    sources kept as direct embed URLs."""
    import asyncio
    async with aiohttp.ClientSession() as session:
        sources = await _get_sources(session)
        if not sources:
            return []

        async def _one(src):
            url = _build_url(src, tmdb_id, season, episode)
            if not url:
                return []
            if src.get("isApiSource"):
                return await _resolve_api_source(session, url)
            return [{"url": url, "quality": "Embed", "label": src.get("name") or src.get("key"), "subtitles": []}]

        results = await asyncio.gather(*[_one(s) for s in sources], return_exceptions=True)
        flat = [item for r in results if isinstance(r, list) for item in r]
        return flat


# ── Commands ───────────────────────────────────────────────────────────

@Client.on_message(filters.command("letsstream") & filters.private)
async def letsstream_command(client: Client, message: Message):
    if not LETSSTREAM_SOURCE_API_URL:
        return await message.reply_text(
            f"<b>{E_INFO} /letsstream isn't configured.</b>\n"
            f"Set <code>LETSSTREAM_SOURCE_API_URL</code> to a JSON endpoint "
            f"shaped like letsstream2's <code>VITE_VIDEO_SOURCE_API</code> "
            f"(see <code>Akbots/letsstream_dl.py</code>'s header comment for the format).",
            parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/letsstream movie &lt;tmdb_id&gt;</code>\n"
            f"<code>/letsstream tv &lt;tmdb_id&gt; &lt;season&gt; &lt;episode&gt;</code>\n"
            f"<code>/letsstream search &lt;query&gt;</code> (to find a TMDB id first)",
            parse_mode=enums.ParseMode.HTML)

    args = message.command[1:]
    kind = args[0].lower()
    status = await message.reply_text(f"<b>{E_CLOCK} Working...</b>", parse_mode=enums.ParseMode.HTML)

    if kind == "search":
        if len(args) < 2 or not TMDB_API_KEY:
            return await safe_edit(status.edit_text,
                f"<b>{E_CROSS} Usage:</b> <code>/letsstream search &lt;query&gt;</code>" +
                ("" if TMDB_API_KEY else f"\n<i>(TMDB_API_KEY not set — can't search)</i>"),
                parse_mode=enums.ParseMode.HTML)
        query = " ".join(args[1:])
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.themoviedb.org/3/search/multi",
                                        params={"api_key": TMDB_API_KEY, "query": query},
                                        timeout=_TIMEOUT) as r:
                    data = await r.json(content_type=None)
            except Exception as e:
                return await safe_edit(status.edit_text, f"<b>{E_CROSS} Search failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        results = [r for r in (data or {}).get("results", []) if r.get("media_type") in ("movie", "tv")][:10]
        if not results:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} No results found.</b>", parse_mode=enums.ParseMode.HTML)
        lines = [f"<code>/letsstream {r['media_type']} {r['id']}"
                 + (" &lt;season&gt; &lt;episode&gt;" if r["media_type"] == "tv" else "") + "</code> — "
                 + (r.get("title") or r.get("name") or "Untitled")
                 + (f" ({(r.get('release_date') or r.get('first_air_date') or '')[:4]})" if (r.get('release_date') or r.get('first_air_date')) else "")
                 for r in results]
        return await safe_edit(status.edit_text, f"<b>{E_ROCKET} Results:</b>\n\n" + "\n".join(lines), parse_mode=enums.ParseMode.HTML)

    if kind == "movie" and len(args) >= 2:
        tmdb_id, season, episode = args[1], None, None
    elif kind == "tv" and len(args) >= 4:
        tmdb_id, season, episode = args[1], args[2], args[3]
    else:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Bad arguments — see /letsstream for usage.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Resolving across every configured source...</b>", parse_mode=enums.ParseMode.HTML)
    links = await _resolve_all(tmdb_id, season, episode)
    if not links:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No sources returned a link.</b>", parse_mode=enums.ParseMode.HTML)

    title = f"TMDB {kind} {tmdb_id}" + (f" S{season}E{episode}" if season else "")
    session_id = uuid.uuid4().hex[:10]
    _LS_SESSIONS[session_id] = {"kind": "download", "stream": {
        "videoUrl": links[0]["url"],
        "qualities": [{"quality": l.get("label") or l.get("quality") or "Link", "url": l["url"]} for l in links],
        "headers": {}, "subtitles": links[0].get("subtitles") or [],
    }, "title": title}
    _trim_sessions()

    buttons = [[make_button(f"▶️ {l.get('label') or l.get('quality') or 'Play'}", url=l["url"], style=_BS.PRIMARY if _BS else None)]
               for l in links[:8]]
    if meow_downloader.is_download_available():
        buttons.append([make_button("⬇️ Download & send here (first link)", callback_data=f"lsgo#{session_id}", style=_BS.SECONDARY if _BS else None)])

    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} {title}</b> <i>({len(links)} link(s) found)</i>",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^lsgo#"))
async def letsstream_download_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    entry = _LS_SESSIONS.get(session_id)
    await callback_query.answer()
    if not entry:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} This link expired — resolve again.</b>", parse_mode=enums.ParseMode.HTML)

    stream, title = entry["stream"], entry["title"]
    status = callback_query.message
    path = None
    try:
        path = await meow_downloader.download_stream(stream, title, status)
        await safe_edit(status.edit_text, f"<b>{E_ROCKET} Uploading to Telegram...</b>", parse_mode=enums.ParseMode.HTML)
        await client.send_video(
            chat_id=status.chat.id, video=path,
            caption=f"<b>{E_CHECK} {title}</b>\n<i>via /letsstream</i>",
            parse_mode=enums.ParseMode.HTML, supports_streaming=True,
        )
        await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"letsstream_dl: download failed: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    finally:
        if path:
            meow_downloader.cleanup(path)
