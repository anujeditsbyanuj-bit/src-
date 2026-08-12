# Akbots - Don't Remove Credit - @AkBots_Official
#
# Telegram wiring for anime_downloader/ (vendored anime-downloader-master —
# see requirements.txt's note on it). anime_downloader is a sibling of
# Akbots/ (like ytdl_legacy/, aniworld_lib/, freeflix_lib/), NOT inside it —
# same Pyrogram plugin-scanner reasoning as those.
#
# Two entry points, mirroring the vendored library's own two ways in:
#   - /animedl <query>  -> Anime.search() across every site still enabled
#     in anime_downloader/sites/init.py's ALL_ANIME_SITES (~15 of them —
#     the upstream maintainers already commented out the ones they'd
#     confirmed dead; whether the rest still work in 2026 is unverified).
#   - /animedlurl <url> -> anime_downloader.sites.get_anime_class(url)
#     picks the matching site directly, same as the one search couldn't
#     already narrow down to.
# Both land on the same season-less, flat episode list -> source picker ->
# stream_url flow (anime_downloader has no season concept — a title's
# episodes are one flat, numbered list).

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

MAX_ITEMS = 15

# session_id -> dict, same lightweight in-memory pattern as
# Akbots/aniworld_dl.py's _AW_SESSIONS / Akbots/freeflix_dl.py's _FF_SESSIONS.
_AD_SESSIONS = {}


def _trim_sessions():
    if len(_AD_SESSIONS) > 300:
        _AD_SESSIONS.pop(next(iter(_AD_SESSIONS)), None)


def _search_all_sites(query: str) -> list:
    """Runs Anime.search() on every enabled site's class, tagging each hit
    with its sitename. Sites that error out (dead domain, layout drift,
    Cloudflare, etc.) are just skipped — this package is old, failures on
    individual sites are expected, not a bug in the wiring."""
    from anime_downloader.sites import ALL_ANIME_SITES
    from importlib import import_module

    results = []
    for module_name, site_key, class_name in ALL_ANIME_SITES:
        try:
            module = import_module(f"anime_downloader.sites.{module_name}")
            anime_cls = getattr(module, class_name)
            hits = anime_cls.search(query) or []
        except Exception as e:
            logger.debug(f"animedl_dl: search failed on {site_key}: {e}")
            continue
        for r in hits:
            results.append((site_key, r))
    return results


# ── Search ──────────────────────────────────────────────────────────────

@Client.on_message(filters.command("animedl") & filters.private)
async def animedl_search_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/animedl &lt;search term&gt;</code>\n"
            f"<i>Searches every still-enabled anime_downloader site at once "
            f"(AniTube, AnimTime, AnimeBinge, Animeflv, AnimeFree, AnimeKisa, "
            f"AnimeOnline360, AnimeRush, AnimeStar, AnimeVibe, DBAnimes, "
            f"Erai-Raws, EgyAnime, GenoAnime, Itsaturday, JustDubs, KissAnimeX, "
            f"Nyaa, SubsPlease, Twist.moe, Tenshi.moe, VidStream, VostFree, "
            f"WcoStream — some of these are 2019-era and may no longer work).</i>\n\n"
            f"<i>Or paste a direct URL with <code>/animedlurl &lt;url&gt;</code>.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    query = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_CLOCK} Searching every site — this can take a while...</b>", parse_mode=enums.ParseMode.HTML)

    import asyncio
    try:
        results = await asyncio.to_thread(_search_all_sites, query)
    except Exception as e:
        logger.warning(f"animedl_dl: search_all failed: {e}")
        results = []

    if not results:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No results found anywhere.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _AD_SESSIONS[session_id] = {"results": results}
    _trim_sessions()

    await safe_edit(status.edit_text,
        f"<b>{E_ROCKET} {len(results)} result(s)</b> — tap one.",
        reply_markup=_results_kb(session_id, results),
        parse_mode=enums.ParseMode.HTML)


def _results_kb(session_id: str, results: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(results) / MAX_ITEMS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_ITEMS

    buttons = []
    for i, (site, r) in enumerate(results[start:start + MAX_ITEMS], start=start):
        label = f"[{site}] {r.title}"
        buttons.append([make_button(label[:60], callback_data=f"adres#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])

    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"adresp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="adnoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"adresp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


# ── Direct URL ──────────────────────────────────────────────────────────

@Client.on_message(filters.command("animedlurl") & filters.private)
async def animedl_url_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/animedlurl &lt;url&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    url = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_CLOCK} Checking URL...</b>", parse_mode=enums.ParseMode.HTML)
    await _open_anime(status, url)


async def _open_anime(status: Message, url: str):
    import asyncio
    from anime_downloader.sites import get_anime_class

    try:
        anime_cls = await asyncio.to_thread(get_anime_class, url)
    except Exception:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Unsupported URL — no matching site.</b>",
            parse_mode=enums.ParseMode.HTML)
    if not anime_cls:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Unsupported URL.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Loading episode list...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        anime = await asyncio.to_thread(anime_cls, url=url)
    except Exception as e:
        logger.warning(f"animedl_dl: Anime() load failed for {url}: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't load that.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    try:
        episode_count = len(anime)
    except Exception:
        episode_count = 0
    if not episode_count:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No episodes found.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _AD_SESSIONS[session_id] = {"anime": anime, "episode_count": episode_count,
                                 "title": getattr(anime, "title", None) or "Anime"}
    _trim_sessions()

    await safe_edit(status.edit_text,
        f"<b>{E_ROCKET} {_AD_SESSIONS[session_id]['title']}</b>\n<i>{episode_count} episode(s) — tap one.</i>",
        reply_markup=_episodes_kb(session_id, episode_count),
        parse_mode=enums.ParseMode.HTML)


def _episodes_kb(session_id: str, episode_count: int, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(episode_count / MAX_ITEMS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_ITEMS
    end = min(start + MAX_ITEMS, episode_count)

    buttons = [[make_button(f"Ep {i + 1}", callback_data=f"adep#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)]
               for i in range(start, end)]

    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"adepp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="adnoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"adepp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


# ── Stream resolution ──────────────────────────────────────────────────

async def _resolve_episode_and_reply(status: Message, session_id: str, ep_idx: int):
    session = _AD_SESSIONS.get(session_id)
    if not session:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Resolving stream...</b>", parse_mode=enums.ParseMode.HTML)
    import asyncio
    anime = session["anime"]
    try:
        episode = await asyncio.to_thread(lambda: anime[ep_idx])
        source = await asyncio.to_thread(episode.source)
        stream_url = await asyncio.to_thread(lambda: source.stream_url)
        referer = await asyncio.to_thread(lambda: source.referer)
    except Exception as e:
        logger.warning(f"animedl_dl: resolve failed: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Resolve failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not stream_url:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No stream URL found for this episode.</b>", parse_mode=enums.ParseMode.HTML)

    headers = {"Referer": referer} if referer else {}
    stream = {"videoUrl": stream_url, "qualities": [{"quality": "Play", "url": stream_url}],
              "headers": headers, "subtitles": []}
    title = f"{session['title']} — Ep {ep_idx + 1}"

    buttons = [[make_button("▶️ Play", url=stream_url, style=_BS.PRIMARY if _BS else None)]]
    if meow_downloader.is_download_available():
        dl_id = uuid.uuid4().hex[:10]
        _AD_SESSIONS[dl_id] = {"stream": stream, "title": title}
        _trim_sessions()
        buttons.append([make_button("⬇️ Download & send here", callback_data=f"adgo#{dl_id}", style=_BS.SECONDARY if _BS else None)])
    buttons.append([make_button("⬅️ Back to episodes", callback_data=f"adback#{session_id}", style=_BS.DANGER if _BS else None)])

    await safe_edit(status.edit_text, f"<b>{E_CHECK} {title}</b>",
                     reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


# ── Callbacks ──────────────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^adnoop$"))
async def animedl_noop_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^adres#"))
async def animedl_result_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#")
    idx = int(idx)
    session = _AD_SESSIONS.get(session_id)
    await callback_query.answer()
    results = (session or {}).get("results")
    if not session or results is None or idx >= len(results):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    _, r = results[idx]
    await _open_anime(callback_query.message, r.url)


@Client.on_callback_query(filters.regex(r"^adresp#"))
async def animedl_result_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    page = int(page)
    session = _AD_SESSIONS.get(session_id)
    await callback_query.answer()
    results = (session or {}).get("results")
    if not session or results is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(results)} result(s)</b> — tap one.",
        reply_markup=_results_kb(session_id, results, page), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^adepp#"))
async def animedl_episode_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    page = int(page)
    session = _AD_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session or "episode_count" not in session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {session['title']}</b>\n<i>{session['episode_count']} episode(s) — tap one.</i>",
        reply_markup=_episodes_kb(session_id, session["episode_count"], page), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^adback#"))
async def animedl_back_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    session = _AD_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session or "episode_count" not in session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {session['title']}</b>\n<i>{session['episode_count']} episode(s) — tap one.</i>",
        reply_markup=_episodes_kb(session_id, session["episode_count"]), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^adep#"))
async def animedl_episode_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, ep_idx = callback_query.data.split("#")
    await callback_query.answer()
    await _resolve_episode_and_reply(callback_query.message, session_id, int(ep_idx))


@Client.on_callback_query(filters.regex(r"^adgo#"))
async def animedl_download_callback(client: Client, callback_query: CallbackQuery):
    dl_id = callback_query.data.split("#", 1)[1]
    entry = _AD_SESSIONS.get(dl_id)
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
            caption=f"<b>{E_CHECK} {title}</b>\n<i>via /animedl</i>",
            parse_mode=enums.ParseMode.HTML, supports_streaming=True,
        )
        await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"animedl_dl: download failed: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    finally:
        if path:
            meow_downloader.cleanup(path)
