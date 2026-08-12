# Akbots - Don't Remove Credit - @AkBots_Official
#
# Telegram wiring for the two vendored Node.js sidecars:
#   - services/anime1v-api/ (7 anime sites: AnimeYT, AnimeFLV, Hentaila,
#     JKAnime, Monoschinos, TioAnime, AnimeAV1)
#   - services/peliapi/     (movies/TV/anime: PelisPlus, Cuevana, RepelisHD,
#     PoseidonHD, SeriesFlixHD, AnimeYT, Unlimplay)
#
# Both are vendored AS-IS from AKASHI-VERSE (Node/Express + Puppeteer,
# ~7.5k and ~5.3k lines respectively), called over HTTP rather than
# imported into this Python process — same pattern this repo already uses
# for services/hotstar-api (Akbots/hotstar.py). Porting that much
# Puppeteer-driven scraping logic to Python line-by-line would be a far
# larger, far riskier undertaking than calling their REST APIs as-is.
#
# Unlike hotstar-api, these two are now built and started inside THIS
# container by the Dockerfile/entrypoint.sh (127.0.0.1:3000 and :5555) —
# no separate Railway deploy required. config.py's ANIME1V_API_URL /
# PELIAPI_URL already default to that localhost pair, so both commands
# work out of the box; the "not configured" fallback below only fires if
# someone explicitly blanks those env vars out.

import uuid
import math
import json
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
    from config import ANIME1V_API_URL, ANIME1V_API_KEY, PELIAPI_URL, PELIAPI_API_KEY
except ImportError:
    ANIME1V_API_URL = ANIME1V_API_KEY = PELIAPI_URL = PELIAPI_API_KEY = ""

logger = logging.getLogger(__name__)

MAX_ITEMS = 15
_TIMEOUT = aiohttp.ClientTimeout(total=25)

_AK_SESSIONS = {}


def _trim_sessions():
    if len(_AK_SESSIONS) > 300:
        _AK_SESSIONS.pop(next(iter(_AK_SESSIONS)), None)


def _label(item: dict, *keys: str, default: str = "Untitled") -> str:
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


# ── Anime1v ──────────────────────────────────────────────────────────────

def _anime1v_headers() -> dict:
    return {"x-api-key": ANIME1V_API_KEY} if ANIME1V_API_KEY else {}


async def _anime1v_get(session: aiohttp.ClientSession, path: str, params: dict) -> dict | None:
    url = ANIME1V_API_URL.rstrip("/") + path
    try:
        async with session.get(url, params=params, headers=_anime1v_headers(), timeout=_TIMEOUT) as r:
            data = await r.json(content_type=None)
            if r.status != 200 or not data or data.get("success") is False:
                return None
            return data
    except Exception as e:
        logger.debug(f"akashi_dl: anime1v GET {path} failed: {e}")
        return None


@Client.on_message(filters.command("anime1v") & filters.private)
async def anime1v_command(client: Client, message: Message):
    if not ANIME1V_API_URL:
        return await message.reply_text(
            f"<b>{E_INFO} /anime1v isn't configured.</b>\n"
            f"Deploy <code>services/anime1v-api</code> and set <code>ANIME1V_API_URL</code>.",
            parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/anime1v &lt;search term&gt;</code>",
            parse_mode=enums.ParseMode.HTML)

    query = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_CLOCK} Searching...</b>", parse_mode=enums.ParseMode.HTML)

    async with aiohttp.ClientSession() as session:
        data = await _anime1v_get(session, "/api/v1/anime/search", {"q": query})

    results = ((data or {}).get("data") or {}).get("results") or []
    if not results:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No results found.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _AK_SESSIONS[session_id] = {"kind": "a1v_results", "results": results}
    _trim_sessions()

    await safe_edit(status.edit_text,
        f"<b>{E_ROCKET} {len(results)} result(s)</b> — tap one.",
        reply_markup=_a1v_results_kb(session_id, results),
        parse_mode=enums.ParseMode.HTML)


def _a1v_results_kb(session_id: str, results: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(results) / MAX_ITEMS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_ITEMS
    buttons = []
    for i, r in enumerate(results[start:start + MAX_ITEMS], start=start):
        label = f"[{r.get('provider', '?')}] {_label(r, 'title', 'name')}"
        buttons.append([make_button(label[:60], callback_data=f"a1vinfo#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])
    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"a1vresp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="aknoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"a1vresp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)
    return InlineKeyboardMarkup(buttons)


@Client.on_callback_query(filters.regex(r"^a1vresp#"))
async def anime1v_result_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    session = _AK_SESSIONS.get(session_id)
    await callback_query.answer()
    results = (session or {}).get("results")
    if not session or results is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(results)} result(s)</b> — tap one.",
        reply_markup=_a1v_results_kb(session_id, results, int(page)), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^a1vinfo#"))
async def anime1v_info_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#")
    session = _AK_SESSIONS.get(session_id)
    await callback_query.answer()
    results = (session or {}).get("results")
    if not session or results is None or int(idx) >= len(results):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    r = results[int(idx)]
    url = _label(r, "url", "slug", default="")
    if not url:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} No URL on this result.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(callback_query.message.edit_text, f"<b>{E_CLOCK} Loading episode list...</b>", parse_mode=enums.ParseMode.HTML)
    async with aiohttp.ClientSession() as session_http:
        data = await _anime1v_get(session_http, "/api/v1/anime/info", {"url": url})
    info = (data or {}).get("data") or {}
    episodes = info.get("episodes") or []
    if not episodes:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} No episodes found.</b>", parse_mode=enums.ParseMode.HTML)

    ep_session_id = uuid.uuid4().hex[:10]
    _AK_SESSIONS[ep_session_id] = {"kind": "a1v_episodes", "episodes": episodes, "title": _label(info, "title", default="Anime")}
    _trim_sessions()

    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {_AK_SESSIONS[ep_session_id]['title']}</b>\n<i>{len(episodes)} episode(s) — tap one.</i>",
        reply_markup=_a1v_episodes_kb(ep_session_id, episodes), parse_mode=enums.ParseMode.HTML)


def _a1v_episodes_kb(session_id: str, episodes: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(episodes) / MAX_ITEMS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_ITEMS
    buttons = []
    for i, ep in enumerate(episodes[start:start + MAX_ITEMS], start=start):
        label = _label(ep, "title", "number", default=f"Episode {i + 1}")
        if label.isdigit():
            label = f"Episode {label}"
        buttons.append([make_button(label[:60], callback_data=f"a1vep#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])
    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"a1vepp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="aknoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"a1vepp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)
    return InlineKeyboardMarkup(buttons)


@Client.on_callback_query(filters.regex(r"^a1vepp#"))
async def anime1v_episode_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    session = _AK_SESSIONS.get(session_id)
    await callback_query.answer()
    episodes = (session or {}).get("episodes")
    if not session or episodes is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {session['title']}</b>\n<i>{len(episodes)} episode(s) — tap one.</i>",
        reply_markup=_a1v_episodes_kb(session_id, episodes, int(page)), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^a1vep#"))
async def anime1v_episode_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#")
    session = _AK_SESSIONS.get(session_id)
    await callback_query.answer()
    episodes = (session or {}).get("episodes")
    if not session or episodes is None or int(idx) >= len(episodes):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    ep = episodes[int(idx)]
    ep_url = _label(ep, "url", default="")
    if not ep_url:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} This episode has no URL.</b>", parse_mode=enums.ParseMode.HTML)

    status = callback_query.message
    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Loading servers...</b>", parse_mode=enums.ParseMode.HTML)
    async with aiohttp.ClientSession() as session_http:
        data = await _anime1v_get(session_http, "/api/v1/anime/episode", {"url": ep_url})
    ep_data = (data or {}).get("data") or {}

    candidate_urls = []
    for group_key in ("streamLinks", "downloadLinks"):
        group = ep_data.get(group_key) or {}
        for variant_list in (group.values() if isinstance(group, dict) else []):
            for entry in (variant_list or []):
                u = _label(entry, "url", "link", "href", "embed", default="")
                if u:
                    candidate_urls.append(u)
    candidate_urls = list(dict.fromkeys(candidate_urls))[:8]  # dedupe, cap fan-out

    if not candidate_urls:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No playback servers found.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Resolving direct stream ({len(candidate_urls)} server(s))...</b>", parse_mode=enums.ParseMode.HTML)
    async with aiohttp.ClientSession() as session_http:
        resolved = await _anime1v_get(session_http, "/api/v1/anime/resolve", {"urls": json.dumps(candidate_urls)})

    stream_url = (resolved or {}).get("streamUrl")
    if not stream_url:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} None of the {len(candidate_urls)} server(s) resolved to a direct stream.</b>",
            parse_mode=enums.ParseMode.HTML)

    stream = {"videoUrl": stream_url, "qualities": [{"quality": resolved.get("server", "Play"), "url": stream_url}],
              "headers": {}, "subtitles": []}
    title = f"{session['title']} — {_label(ep, 'title', default='Episode')}"

    buttons = [[make_button(f"▶️ Play ({resolved.get('server', 'server')})", url=stream_url, style=_BS.PRIMARY if _BS else None)]]
    if meow_downloader.is_download_available():
        dl_id = uuid.uuid4().hex[:10]
        _AK_SESSIONS[dl_id] = {"kind": "download", "stream": stream, "title": title}
        _trim_sessions()
        buttons.append([make_button("⬇️ Download & send here", callback_data=f"akgo#{dl_id}", style=_BS.SECONDARY if _BS else None)])

    await safe_edit(status.edit_text, f"<b>{E_CHECK} {title}</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


# ── PeliApi ─────────────────────────────────────────────────────────────

def _peliapi_headers() -> dict:
    return {"x-api-key": PELIAPI_API_KEY} if PELIAPI_API_KEY else {}


async def _peliapi_get(session: aiohttp.ClientSession, path: str, params: dict) -> dict | None:
    url = PELIAPI_URL.rstrip("/") + path
    try:
        async with session.get(url, params=params, headers=_peliapi_headers(), timeout=_TIMEOUT) as r:
            data = await r.json(content_type=None)
            if r.status != 200 or not data or data.get("success") is False:
                return None
            return data
    except Exception as e:
        logger.debug(f"akashi_dl: peliapi GET {path} failed: {e}")
        return None


@Client.on_message(filters.command("peliapi") & filters.private)
async def peliapi_command(client: Client, message: Message):
    if not PELIAPI_URL:
        return await message.reply_text(
            f"<b>{E_INFO} /peliapi isn't configured.</b>\n"
            f"Deploy <code>services/peliapi</code> and set <code>PELIAPI_URL</code>.",
            parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/peliapi &lt;search term&gt;</code>",
            parse_mode=enums.ParseMode.HTML)

    query = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_CLOCK} Searching...</b>", parse_mode=enums.ParseMode.HTML)

    async with aiohttp.ClientSession() as session:
        data = await _peliapi_get(session, "/api/v1/content/search", {"s": query})
    results = (data or {}).get("data") or (data or {}).get("results") or []
    if isinstance(results, dict):
        results = results.get("results") or []
    if not results:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No results found.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _AK_SESSIONS[session_id] = {"kind": "peli_results", "results": results}
    _trim_sessions()

    await safe_edit(status.edit_text,
        f"<b>{E_ROCKET} {len(results)} result(s)</b> — tap one.",
        reply_markup=_peli_results_kb(session_id, results), parse_mode=enums.ParseMode.HTML)


def _peli_results_kb(session_id: str, results: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(results) / MAX_ITEMS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_ITEMS
    buttons = []
    for i, r in enumerate(results[start:start + MAX_ITEMS], start=start):
        label = f"[{_label(r, 'type', default='?')}] {_label(r, 'title', 'name')}"
        buttons.append([make_button(label[:60], callback_data=f"peliinfo#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])
    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"peliresp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="aknoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"peliresp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)
    return InlineKeyboardMarkup(buttons)


@Client.on_callback_query(filters.regex(r"^peliresp#"))
async def peliapi_result_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    session = _AK_SESSIONS.get(session_id)
    await callback_query.answer()
    results = (session or {}).get("results")
    if not session or results is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(results)} result(s)</b> — tap one.",
        reply_markup=_peli_results_kb(session_id, results, int(page)), parse_mode=enums.ParseMode.HTML)


async def _peliapi_resolve_candidates(candidate_urls: list) -> tuple[str, str] | None:
    """PeliApi's /resolve only takes one url at a time (unlike anime1v's
    array-capable /resolve) — race them ourselves client-side."""
    import asyncio
    async with aiohttp.ClientSession() as session:
        async def _try(u):
            data = await _peliapi_get(session, "/api/v1/content/resolve", {"url": u})
            direct = ((data or {}).get("data") or {}).get("directUrl")
            return direct if direct and direct != u else None

        results = await asyncio.gather(*[_try(u) for u in candidate_urls[:8]], return_exceptions=True)
        for u, r in zip(candidate_urls, results):
            if isinstance(r, str) and r:
                return u, r
    return None


@Client.on_callback_query(filters.regex(r"^peliinfo#"))
async def peliapi_info_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#")
    session = _AK_SESSIONS.get(session_id)
    await callback_query.answer()
    results = (session or {}).get("results")
    if not session or results is None or int(idx) >= len(results):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    r = results[int(idx)]
    slug = _label(r, "slug", "id", default="")
    content_type = _label(r, "type", default="movie")
    provider = r.get("provider")
    title = _label(r, "title", "name", default="Untitled")
    if not slug:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} No slug on this result.</b>", parse_mode=enums.ParseMode.HTML)

    status = callback_query.message
    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Loading details...</b>", parse_mode=enums.ParseMode.HTML)
    params = {"type": content_type}
    if provider:
        params["provider"] = provider
    async with aiohttp.ClientSession() as session_http:
        data = await _peliapi_get(session_http, f"/api/v1/content/info/{slug}", params)
    info = (data or {}).get("data") or {}

    if info.get("seasons"):
        # Series — flatten seasons into one paginated episode list, storing
        # (season, episode, title) tuples so we can call /servers per tap.
        flat = []
        for s in info["seasons"]:
            for ep in (s.get("episodes") or []):
                flat.append({"season": s.get("season") or s.get("number") or 1,
                              "episode": ep.get("episode") or ep.get("number"),
                              "title": _label(ep, "title", default=""),
                              "url": ep.get("url")})
        if not flat:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} No episodes found.</b>", parse_mode=enums.ParseMode.HTML)
        ep_session_id = uuid.uuid4().hex[:10]
        _AK_SESSIONS[ep_session_id] = {"kind": "peli_episodes", "episodes": flat, "title": title,
                                        "slug": slug, "provider": provider}
        _trim_sessions()
        return await safe_edit(status.edit_text,
            f"<b>{E_ROCKET} {title}</b>\n<i>{len(flat)} episode(s) — tap one.</i>",
            reply_markup=_peli_episodes_kb(ep_session_id, flat), parse_mode=enums.ParseMode.HTML)

    # Movie — servers already included in the info response.
    servers = info.get("servers") or []
    candidate_urls = [u for u in (_label(s, "url", default="") for s in servers) if u]
    if not candidate_urls:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No playback servers found.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Resolving direct stream ({len(candidate_urls)} server(s))...</b>", parse_mode=enums.ParseMode.HTML)
    result = await _peliapi_resolve_candidates(candidate_urls)
    if not result:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} None of the servers resolved.</b>", parse_mode=enums.ParseMode.HTML)
    _, stream_url = result
    await _peli_reply_stream(status, stream_url, title)


def _peli_episodes_kb(session_id: str, episodes: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(episodes) / MAX_ITEMS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_ITEMS
    buttons = []
    for i, ep in enumerate(episodes[start:start + MAX_ITEMS], start=start):
        label = f"S{ep.get('season', '?')}E{ep.get('episode', '?')}" + (f" — {ep['title']}" if ep.get("title") else "")
        buttons.append([make_button(label[:60], callback_data=f"peliep#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])
    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"peliepp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="aknoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"peliepp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)
    return InlineKeyboardMarkup(buttons)


@Client.on_callback_query(filters.regex(r"^peliepp#"))
async def peliapi_episode_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    session = _AK_SESSIONS.get(session_id)
    await callback_query.answer()
    episodes = (session or {}).get("episodes")
    if not session or episodes is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {session['title']}</b>\n<i>{len(episodes)} episode(s) — tap one.</i>",
        reply_markup=_peli_episodes_kb(session_id, episodes, int(page)), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^peliep#"))
async def peliapi_episode_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#")
    session = _AK_SESSIONS.get(session_id)
    await callback_query.answer()
    episodes = (session or {}).get("episodes")
    if not session or episodes is None or int(idx) >= len(episodes):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    ep = episodes[int(idx)]
    status = callback_query.message
    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Loading servers...</b>", parse_mode=enums.ParseMode.HTML)

    params = {"slug": session["slug"], "season": ep.get("season") or 1, "episode": ep.get("episode") or 1}
    if session.get("provider"):
        params["provider"] = session["provider"]
    if ep.get("url"):
        params["url"] = ep["url"]

    async with aiohttp.ClientSession() as session_http:
        data = await _peliapi_get(session_http, "/api/v1/content/servers", params)
    servers = ((data or {}).get("data") or {}).get("servers") or (data or {}).get("data") or []
    if isinstance(servers, dict):
        servers = servers.get("servers") or []
    candidate_urls = [u for u in (_label(s, "url", default="") for s in servers) if u]
    if not candidate_urls:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No playback servers found.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Resolving direct stream ({len(candidate_urls)} server(s))...</b>", parse_mode=enums.ParseMode.HTML)
    result = await _peliapi_resolve_candidates(candidate_urls)
    if not result:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} None of the servers resolved.</b>", parse_mode=enums.ParseMode.HTML)
    _, stream_url = result
    title = f"{session['title']} — S{ep.get('season')}E{ep.get('episode')}"
    await _peli_reply_stream(status, stream_url, title)


async def _peli_reply_stream(status: Message, stream_url: str, title: str):
    stream = {"videoUrl": stream_url, "qualities": [{"quality": "Play", "url": stream_url}],
              "headers": {}, "subtitles": []}
    buttons = [[make_button("▶️ Play", url=stream_url, style=_BS.PRIMARY if _BS else None)]]
    if meow_downloader.is_download_available():
        dl_id = uuid.uuid4().hex[:10]
        _AK_SESSIONS[dl_id] = {"kind": "download", "stream": stream, "title": title}
        _trim_sessions()
        buttons.append([make_button("⬇️ Download & send here", callback_data=f"akgo#{dl_id}", style=_BS.SECONDARY if _BS else None)])
    await safe_edit(status.edit_text, f"<b>{E_CHECK} {title}</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


# ── Shared: no-op + download ─────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^aknoop$"))
async def akashi_noop_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^akgo#"))
async def akashi_download_callback(client: Client, callback_query: CallbackQuery):
    dl_id = callback_query.data.split("#", 1)[1]
    entry = _AK_SESSIONS.get(dl_id)
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
            caption=f"<b>{E_CHECK} {title}</b>\n<i>via /anime1v or /peliapi</i>",
            parse_mode=enums.ParseMode.HTML, supports_streaming=True,
        )
        await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"akashi_dl: download failed: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    finally:
        if path:
            meow_downloader.cleanup(path)
