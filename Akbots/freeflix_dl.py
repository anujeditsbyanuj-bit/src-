# Akbots - Don't Remove Credit - @AkBots_Official
#
# Telegram wiring for freeflix_lib/ (vendored from freeflix-cli-main's
# src/freeflix_cli/scraping/ + the handful of support modules it actually
# imports — net_config.py, cloudflare.py, config_loader.py, defaults.py,
# tracker.py). Everything else upstream (handlers/, the interactive CLI
# menus, AniList-linking prompts, mpv/IPC playback, splash screen, i18n)
# was deliberately left out — it's all built around `input()`-driven menus
# and a local media player, neither of which apply headlessly in a bot.
#
# freeflix_lib is a sibling of Akbots/ (like ytdl_legacy/, aniworld_lib/),
# NOT inside it — same reasoning as aniworld_lib: Pyrogram's
# plugins=dict(root="Akbots") loader would otherwise try to import it as
# plugin code.
#
# Sites wired:
#   - Anime-Sama, Coflix, French-Stream — full search -> series -> season
#     -> episode -> player list -> resolved HLS link, via /freeflix (search)
#     and /freeflixurl (direct URL, auto-detected by domain).
#   - GoldenAnime / GoldenMS — these two are TMDB-id / AniList-id—driven
#     multi-provider extractors (vidlink/hexa/xpass/mapple/videasy for
#     movies+TV, sudatchi/anizone/animetsu/allanime for anime), not
#     URL-browsers — wired as their own /goldenstream command.
#
# NOT wired: French-Manga (manga, not video — same story as MangaFire in
# aniworld_dl.py, would need its own image/media-group flow, skipped for
# now) and PapyStreaming (upstream scraping/papystreaming.py only has a
# `search()` returning plain dicts, no get_series/get_season/get_episode —
# the rest of that provider isn't implemented upstream, not something to
# invent here).

import uuid
import math
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_CLOCK, safe_edit
from Akbots import meow_downloader
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

logger = logging.getLogger(__name__)

MAX_RESULTS = 15
MAX_ITEMS = 15

# session_id -> arbitrary dict, same lightweight in-memory pattern as
# Akbots/aniworld_dl.py's _AW_SESSIONS (no persistence needed — these are
# short-lived browse sessions).
_FF_SESSIONS = {}


def _trim_sessions():
    if len(_FF_SESSIONS) > 300:
        _FF_SESSIONS.pop(next(iter(_FF_SESSIONS)), None)


_SITES = {
    "sama": "🎌 Anime-Sama",
    "coflix": "🎬 Coflix",
    "fstream": "🍿 French-Stream",
}

_DOMAIN_HINTS = {
    "anime-sama": "sama",
    "coflix": "coflix",
    "french-stream": "fstream",
}


def _detect_site(url: str) -> str | None:
    low = url.lower()
    for hint, site in _DOMAIN_HINTS.items():
        if hint in low:
            return site
    return None


def _site_module(site: str):
    from freeflix_lib.scraping import anime_sama, coflix, french_stream
    return {"sama": anime_sama, "coflix": coflix, "fstream": french_stream}[site]


# ── Search ──────────────────────────────────────────────────────────────

@Client.on_message(filters.command("freeflix") & filters.private)
async def freeflix_search_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/freeflix &lt;search term&gt;</code>\n"
            f"<i>Searches Anime-Sama, Coflix and French-Stream at once.</i>\n\n"
            f"<i>Or paste a direct URL from any of those three with "
            f"<code>/freeflixurl &lt;url&gt;</code>, or use "
            f"<code>/goldenstream</code> for the TMDB/AniList-id extractors.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    query = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_CLOCK} Searching...</b>", parse_mode=enums.ParseMode.HTML)

    import asyncio
    async def _search(site):
        mod = _site_module(site)
        try:
            results = await asyncio.to_thread(mod.search, query)
        except Exception as e:
            logger.debug(f"freeflix_dl: search failed on {site}: {e}")
            return []
        return [(site, r) for r in (results or [])]

    grouped = await asyncio.gather(*[_search(s) for s in _SITES], return_exceptions=True)
    all_results = [item for g in grouped if isinstance(g, list) for item in g]

    if not all_results:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No results found anywhere.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _FF_SESSIONS[session_id] = {"results": all_results}
    _trim_sessions()

    await safe_edit(status.edit_text,
        f"<b>{E_ROCKET} {len(all_results)} result(s)</b> — tap one.",
        reply_markup=_results_kb(session_id, all_results),
        parse_mode=enums.ParseMode.HTML)


def _results_kb(session_id: str, results: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(results) / MAX_RESULTS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_RESULTS

    buttons = []
    for i, (site, r) in enumerate(results[start:start + MAX_RESULTS], start=start):
        title = getattr(r, "title", None) or (r.get("title") if isinstance(r, dict) else None) or "Untitled"
        label = f"{_SITES.get(site, site)} · {title}"
        buttons.append([make_button(label[:60], callback_data=f"ffres#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])

    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"ffresp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="ffnoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"ffresp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


# ── Direct URL ──────────────────────────────────────────────────────────

@Client.on_message(filters.command("freeflixurl") & filters.private)
async def freeflix_url_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/freeflixurl &lt;url&gt;</code>\n"
            f"<i>Anime-Sama, Coflix or French-Stream series/season/movie page.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    url = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_CLOCK} Checking URL...</b>", parse_mode=enums.ParseMode.HTML)

    site = _detect_site(url)
    if not site:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Unsupported URL.</b>\n<i>Supported: anime-sama, coflix, french-stream.</i>",
            parse_mode=enums.ParseMode.HTML)

    await _open_series(status, site, url)


async def _open_series(status: Message, site: str, url: str):
    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Loading...</b>", parse_mode=enums.ParseMode.HTML)
    import asyncio
    mod = _site_module(site)
    try:
        if site == "sama":
            series = await asyncio.to_thread(mod.get_series, url)
            seasons = series.seasons  # list[SeasonAccess(title,url)]
        elif site == "coflix":
            content = await asyncio.to_thread(mod.get_content, url)
            if hasattr(content, "players"):  # CoflixMovie — no season browsing needed
                return await _resolve_players_and_reply(status, content.players, getattr(content, "title", "Movie"))
            series = content
            seasons = series.seasons
        else:  # fstream
            content = await asyncio.to_thread(mod.get_content, url)
            if hasattr(content, "players"):  # FrenchStreamMovie
                return await _resolve_players_and_reply(status, content.players, getattr(content, "title", "Movie"))
            # FrenchStreamSeason — episodes already resolved, no separate season fetch
            season_id = uuid.uuid4().hex[:10]
            _FF_SESSIONS[season_id] = {"site": site, "episodes_by_lang": content.episodes}
            _trim_sessions()
            return await _show_episode_langs_or_list(status, season_id, getattr(content, "title", "Series"))
    except Exception as e:
        logger.warning(f"freeflix_dl: series/content load failed for {url}: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't load that.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not seasons:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No seasons found.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _FF_SESSIONS[session_id] = {"site": site, "series": series, "seasons": seasons}
    _trim_sessions()

    await safe_edit(status.edit_text,
        f"<b>{E_ROCKET} {series.title}</b>\n<i>{len(seasons)} season(s) — tap one.</i>",
        reply_markup=_seasons_kb(session_id, seasons),
        parse_mode=enums.ParseMode.HTML)


def _seasons_kb(session_id: str, seasons: list) -> InlineKeyboardMarkup:
    buttons = [[make_button(f"📁 {s.title}", callback_data=f"ffse#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)]
               for i, s in enumerate(seasons[:MAX_ITEMS])]
    return InlineKeyboardMarkup(buttons)


async def _show_episode_langs_or_list(status: Message, session_id: str, series_title: str):
    """anime-sama/french-stream episodes come back keyed by language
    (episodes_by_lang: dict[str, list[Episode]]) — offer a language picker
    if there's more than one, otherwise skip straight to the episode list."""
    session = _FF_SESSIONS[session_id]
    episodes_by_lang = session["episodes_by_lang"]
    langs = [l for l in episodes_by_lang if episodes_by_lang[l]]
    if not langs:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No episodes found.</b>", parse_mode=enums.ParseMode.HTML)

    if len(langs) == 1:
        session["lang"] = langs[0]
        return await safe_edit(status.edit_text,
            f"<b>{E_ROCKET} {series_title}</b> <i>({langs[0]})</i>\n{len(episodes_by_lang[langs[0]])} episode(s) — tap one.",
            reply_markup=_episodes_kb(session_id, episodes_by_lang[langs[0]]),
            parse_mode=enums.ParseMode.HTML)

    buttons = [[make_button(f"🌐 {l} ({len(episodes_by_lang[l])} ep)", callback_data=f"fflang#{session_id}#{l}",
                             style=_BS.PRIMARY if _BS else None)] for l in langs]
    session["series_title"] = series_title
    await safe_edit(status.edit_text, f"<b>{E_ROCKET} {series_title}</b>\n<i>Pick a language.</i>",
                     reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


def _episodes_kb(session_id: str, episodes: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(episodes) / MAX_ITEMS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_ITEMS

    buttons = []
    for i, ep in enumerate(episodes[start:start + MAX_ITEMS], start=start):
        label = getattr(ep, "title", None) or f"Episode {i + 1}"
        buttons.append([make_button(label[:60], callback_data=f"ffep#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])

    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"ffepp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="ffnoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"ffepp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


# ── Player resolution (shared by Coflix episodes/movies, French-Stream
#    episodes/movies, and Anime-Sama episodes) ─────────────────────────

async def _resolve_players_and_reply(status: Message, players: list, title: str):
    from freeflix_lib.scraping import player as player_mod
    import asyncio

    if not players:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No players found for this episode.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Resolving player links...</b>", parse_mode=enums.ParseMode.HTML)

    async def _try(p):
        try:
            link = await asyncio.to_thread(player_mod.get_hls_link, p.url)
        except Exception:
            link = None
        return (p.name, link)

    results = await asyncio.gather(*[_try(p) for p in players], return_exceptions=True)
    working = [(name, link) for r in results if isinstance(r, tuple) for name, link in [r] if link]

    if not working:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} None of the {len(players)} player(s) resolved.</b> <i>Sites often rotate hosts — try again in a bit.</i>",
            parse_mode=enums.ParseMode.HTML)

    stream = {"videoUrl": working[0][1],
              "qualities": [{"quality": name, "url": link} for name, link in working],
              "headers": {}, "subtitles": []}

    buttons = [[make_button(f"▶️ Play ({working[0][0]})", url=working[0][1], style=_BS.PRIMARY if _BS else None)]]
    if meow_downloader.is_download_available():
        dl_id = uuid.uuid4().hex[:10]
        _FF_SESSIONS[dl_id] = {"stream": stream, "title": title}
        _trim_sessions()
        buttons.append([make_button("⬇️ Download & send here", callback_data=f"ffgo#{dl_id}", style=_BS.SECONDARY if _BS else None)])

    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} {title}</b> <i>({len(working)}/{len(players)} player(s) working)</i>",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


# ── Callbacks ──────────────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^ffnoop$"))
async def freeflix_noop_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^ffres#"))
async def freeflix_result_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#")
    idx = int(idx)
    session = _FF_SESSIONS.get(session_id)
    await callback_query.answer()
    results = (session or {}).get("results")
    if not session or results is None or idx >= len(results):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    site, r = results[idx]
    url = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None)
    if not url:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} This result has no URL.</b>", parse_mode=enums.ParseMode.HTML)
    await _open_series(callback_query.message, site, url)


@Client.on_callback_query(filters.regex(r"^ffresp#"))
async def freeflix_result_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    page = int(page)
    session = _FF_SESSIONS.get(session_id)
    await callback_query.answer()
    results = (session or {}).get("results")
    if not session or results is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(results)} result(s)</b> — tap one.",
        reply_markup=_results_kb(session_id, results, page), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^ffse#"))
async def freeflix_season_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, season_idx = callback_query.data.split("#")
    season_idx = int(season_idx)
    session = _FF_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session or "seasons" not in session or season_idx >= len(session["seasons"]):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)

    site = session["site"]
    season_ref = session["seasons"][season_idx]
    mod = _site_module(site)
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CLOCK} Loading episodes...</b>", parse_mode=enums.ParseMode.HTML)
    import asyncio
    try:
        season = await asyncio.to_thread(mod.get_season, season_ref.url)
    except Exception as e:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Couldn't load season.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if site == "sama":
        # SamaSeason.episodes: dict[lang, list[Episode]] — episodes already
        # have .players, no separate get_episode() fetch needed.
        sub_id = uuid.uuid4().hex[:10]
        _FF_SESSIONS[sub_id] = {"site": site, "episodes_by_lang": season.episodes}
        _trim_sessions()
        return await _show_episode_langs_or_list(callback_query.message, sub_id, season.title)

    # coflix: CoflixSeason.episodes -> list[EpisodeAccess(title,url)], one
    # more fetch (get_episode) needed per tap to get .players.
    session[f"episodes_{season_idx}"] = season.episodes
    if not season.episodes:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} No episodes found.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(season.episodes)} episode(s)</b> — tap one.",
        reply_markup=_episodes_kb(session_id, season.episodes),
        parse_mode=enums.ParseMode.HTML)
    session["_last_season_idx"] = season_idx


@Client.on_callback_query(filters.regex(r"^fflang#"))
async def freeflix_lang_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, lang = callback_query.data.split("#", 2)
    session = _FF_SESSIONS.get(session_id)
    await callback_query.answer()
    episodes_by_lang = (session or {}).get("episodes_by_lang")
    if not session or episodes_by_lang is None or lang not in episodes_by_lang:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    session["lang"] = lang
    title = session.get("series_title", "Series")
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {title}</b> <i>({lang})</i>\n{len(episodes_by_lang[lang])} episode(s) — tap one.",
        reply_markup=_episodes_kb(session_id, episodes_by_lang[lang]), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^ffepp#"))
async def freeflix_episode_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    page = int(page)
    session = _FF_SESSIONS.get(session_id)
    await callback_query.answer()
    episodes = None
    if session and "episodes_by_lang" in session and session.get("lang"):
        episodes = session["episodes_by_lang"].get(session["lang"])
    elif session:
        idx = session.get("_last_season_idx")
        episodes = session.get(f"episodes_{idx}") if idx is not None else None
    if episodes is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(episodes)} episode(s)</b> — tap one.",
        reply_markup=_episodes_kb(session_id, episodes, page), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^ffep#"))
async def freeflix_episode_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, ep_idx = callback_query.data.split("#")
    ep_idx = int(ep_idx)
    session = _FF_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)

    # anime-sama / french-stream: episode already has .players.
    if "episodes_by_lang" in session and session.get("lang"):
        episodes = session["episodes_by_lang"][session["lang"]]
        if ep_idx >= len(episodes):
            return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired.</b>", parse_mode=enums.ParseMode.HTML)
        ep = episodes[ep_idx]
        return await _resolve_players_and_reply(callback_query.message, ep.players, ep.title)

    # coflix: episode is an EpisodeAccess(title,url) — fetch .players now.
    idx = session.get("_last_season_idx")
    episodes = session.get(f"episodes_{idx}") if idx is not None else None
    if episodes is None or ep_idx >= len(episodes):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired.</b>", parse_mode=enums.ParseMode.HTML)
    ep_ref = episodes[ep_idx]
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CLOCK} Loading episode...</b>", parse_mode=enums.ParseMode.HTML)
    import asyncio
    from freeflix_lib.scraping import coflix
    try:
        ep = await asyncio.to_thread(coflix.get_episode, ep_ref.url)
    except Exception as e:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Couldn't load episode.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    await _resolve_players_and_reply(callback_query.message, ep.players, ep.title)


@Client.on_callback_query(filters.regex(r"^ffgo#"))
async def freeflix_download_callback(client: Client, callback_query: CallbackQuery):
    dl_id = callback_query.data.split("#", 1)[1]
    entry = _FF_SESSIONS.get(dl_id)
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
            caption=f"<b>{E_CHECK} {title}</b>\n<i>via /freeflix</i>",
            parse_mode=enums.ParseMode.HTML, supports_streaming=True,
        )
        await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"freeflix_dl: download failed: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    finally:
        if path:
            meow_downloader.cleanup(path)


# ── GoldenAnime / GoldenMS (TMDB-id / AniList-id extractors) ──────────

@Client.on_message(filters.command("goldenstream") & filters.private)
async def golden_stream_command(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b>\n"
            f"<code>/goldenstream movie &lt;tmdb_id&gt;</code>\n"
            f"<code>/goldenstream tv &lt;tmdb_id&gt; &lt;season&gt; &lt;episode&gt;</code>\n"
            f"<code>/goldenstream anime &lt;anilist_id&gt; &lt;episode&gt;</code>\n\n"
            f"<i>GoldenMS covers movies/TV (Vidlink, Hexa, Xpass, Mapple, Videasy). "
            f"GoldenAnime covers anime by AniList id (Sudatchi, AniZone, AnimeTsu, AllAnime).</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    kind = args[0].lower()
    status = await message.reply_text(f"<b>{E_CLOCK} Resolving...</b>", parse_mode=enums.ParseMode.HTML)
    import asyncio

    try:
        if kind == "movie" and len(args) >= 2:
            from freeflix_lib.scraping.goldenms import goldenms_extractor
            results = await asyncio.to_thread(goldenms_extractor.extract, None, args[1])
            title = f"TMDB Movie {args[1]}"
        elif kind == "tv" and len(args) >= 4:
            from freeflix_lib.scraping.goldenms import goldenms_extractor
            results = await asyncio.to_thread(goldenms_extractor.extract, None, args[1], None, None, args[2], args[3])
            title = f"TMDB TV {args[1]} S{args[2]}E{args[3]}"
        elif kind == "anime" and len(args) >= 3:
            from freeflix_lib.scraping.goldenanime import goldenanime
            results = await asyncio.to_thread(goldenanime.extract_vo, None, args[1], int(args[2]))
            title = f"AniList {args[1]} Ep {args[2]}"
        else:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Bad arguments — see /goldenstream for usage.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"freeflix_dl: goldenstream resolve failed: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Resolve failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    valid = [r for r in (results or []) if r.get("url")]
    if not valid:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No sources found.</b>", parse_mode=enums.ParseMode.HTML)

    stream = {"videoUrl": valid[0]["url"],
              "qualities": [{"quality": r.get("provider") or r.get("name") or f"Source {i+1}", "url": r["url"]} for i, r in enumerate(valid)],
              "headers": valid[0].get("headers") or {}, "subtitles": valid[0].get("subtitles") or []}

    buttons = [[make_button(f"▶️ Play ({stream['qualities'][0]['quality']})", url=stream["videoUrl"], style=_BS.PRIMARY if _BS else None)]]
    if meow_downloader.is_download_available():
        dl_id = uuid.uuid4().hex[:10]
        _FF_SESSIONS[dl_id] = {"stream": stream, "title": title}
        _trim_sessions()
        buttons.append([make_button("⬇️ Download & send here", callback_data=f"ffgo#{dl_id}", style=_BS.SECONDARY if _BS else None)])

    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} {title}</b> <i>({len(valid)} source(s) found)</i>",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
