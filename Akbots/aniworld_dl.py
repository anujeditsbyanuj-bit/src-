# Akbots - Don't Remove Credit - @AkBots_Official
#
# Telegram wiring for aniworld_lib/ (vendored AniWorld-Downloader — see
# aniworld_lib/README.md upstream), a URL-based downloader covering
# aniworld.to, s.to (SerienStream), Kinox, Cineby, BurningSeries,
# MegaKino, FilmPalast, MangaFire and HanimeTV.
#
# aniworld_lib is a sibling of Akbots/ (like ytdl_legacy/), NOT inside it —
# Pyrogram's plugins=dict(root="Akbots") loader recursively imports every
# .py under its root, and aniworld_lib has ~150 nested modules (CLI menu,
# Flask web UI, playwright browser drivers) that don't belong in that scan
# and would slow startup / fail on TUI-only deps (npyscreen) this repo
# intentionally doesn't install. See requirements.txt's aniworld_lib note.
#
# All 9 upstream providers are wired, split by how aniworld_lib itself
# shapes each one (checked against every models/<provider>/*.py before
# writing this, not assumed):
#   - AniWorld, SerienStream, Kinox, Cineby, BurningSeries, HanimeTV — full
#     series -> season -> episode tap-through picker. All six expose the
#     identical .title/.seasons/season.episodes/episode.episode_number/
#     .title_en/.title_de/.stream_url/.provider_attempt_order() surface,
#     so one code path (_BROWSABLE_PROVIDERS) drives all of them.
#   - MegaKino, FilmPalast — no series/season concept upstream at all
#     (season_cls=None, series_cls IS episode_cls): any supported URL is
#     already a single watchable page, resolved immediately.
#   - MangaFire — manga (chapters of page images), not video. No
#     stream_url to speak of, so it gets its own chapter-list ->
#     media-group flow (_handle_mangafire_series / awmg# callbacks)
#     instead of the video download path every other provider uses.
#
# NOTE: VOE (AniWorld's primary extractor), SerienStream, Cineby,
# BurningSeries and HanimeTV all drive a real headless Chromium via
# patchright under the hood (see requirements.txt / Dockerfile). First
# resolve can be slow (imports its own DoH-resolving HTTP session + spins
# up a browser context) — the status message is updated so it doesn't look
# stuck, but do warn users this isn't instant.

import math
import uuid
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

MAX_SEASONS = 15
MAX_EPISODES = 15

# Providers with full season/episode browsing wired below. All 9 upstream
# providers share (or, for MegaKino/FilmPalast/MangaFire, are handled via
# a dedicated branch instead of needing) the same Series/Season/Episode
# API surface: .title, .seasons, season.episodes, episode.episode_number/
# .title_en/.title_de/.stream_url/.provider_attempt_order() — verified
# against aniworld_lib/models/{aniworld_to,s_to,kinox,cineby,burningseries,
# hanime_tv}/*.py before adding each one here.
_BROWSABLE_PROVIDERS = {"AniWorld", "SerienStream", "Kinox", "Cineby", "BurningSeries", "HanimeTV"}

# session_id -> {"series": <SeriesObj>, "seasons": [...], "provider": Provider,
#                "season_idx": int (set once a season is opened)}
_AW_SESSIONS = {}


def _trim_sessions():
    if len(_AW_SESSIONS) > 300:
        _AW_SESSIONS.pop(next(iter(_AW_SESSIONS)), None)


# ── Stream resolution ─────────────────────────────────────────────────────

async def _resolve_stream(episode) -> dict | None:
    """Tries every provider aniworld_lib says is available for this
    episode, in its own recommended attempt order, and returns the first
    stream_url that resolves — shaped for meow_downloader.download_stream()."""
    import asyncio

    try:
        providers_to_try = list(episode.provider_attempt_order()) or [episode.selected_provider]
    except Exception:
        providers_to_try = [episode.selected_provider]

    for provider_name in providers_to_try:
        try:
            episode.selected_provider = provider_name
            # aniworld_lib's provider extractors are synchronous (niquests/
            # curl_cffi/patchright calls) — run off the event loop so one
            # slow resolve doesn't block the whole bot.
            stream_url = await asyncio.to_thread(lambda: episode.stream_url)
        except Exception as e:
            logger.debug(f"aniworld_dl: provider {provider_name} failed: {e}")
            continue
        if stream_url:
            return {
                "videoUrl": stream_url,
                "qualities": [{"quality": provider_name, "url": stream_url}],
                "headers": {},
                "subtitles": [],
            }
    return None


# ── UI builders ────────────────────────────────────────────────────────────

def _seasons_kb(session_id: str, seasons: list) -> InlineKeyboardMarkup:
    buttons = []
    for i, season in enumerate(seasons[:MAX_SEASONS]):
        try:
            label = f"📁 Season {season.season_number}"
        except Exception:
            label = f"📁 Season {i + 1}"
        buttons.append([make_button(label, callback_data=f"awse#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])
    return InlineKeyboardMarkup(buttons)


def _episodes_kb(session_id: str, season_idx: int, episodes: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(episodes) / MAX_EPISODES))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_EPISODES

    buttons = []
    for i, ep in enumerate(episodes[start:start + MAX_EPISODES], start=start):
        try:
            num = ep.episode_number
            title = ep.title_en or ep.title_de or ""
        except Exception:
            num, title = i + 1, ""
        label = f"Ep {num}" + (f" · {title}" if title else "")
        buttons.append([make_button(label, callback_data=f"awep#{session_id}#{season_idx}#{i}", style=_BS.PRIMARY if _BS else None)])

    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"awsep#{session_id}#{season_idx}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="awnoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"awsep#{session_id}#{season_idx}#{page + 1}", style=_sec))
        buttons.append(nav_row)

    buttons.append([make_button("⬅️ Back to seasons", callback_data=f"awback#{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(buttons)


# ── Commands ───────────────────────────────────────────────────────────────

@Client.on_message(filters.command(["aniworldurl", "awdl"]) & filters.private)
async def aniworld_url_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/aniworldurl &lt;url&gt;</code>\n\n"
            f"<i>Paste a series/show page URL — full season/episode picker for "
            f"AniWorld, SerienStream, Kinox, Cineby, BurningSeries, HanimeTV, and "
            f"chapter picker for MangaFire. MegaKino/FilmPalast/others: paste the "
            f"direct episode/video-page URL.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    url = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_CLOCK} Checking URL...</b>", parse_mode=enums.ParseMode.HTML)

    from aniworld_lib.providers import resolve_provider, normalize_url

    try:
        provider = resolve_provider(url)
    except ValueError:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Unsupported URL.</b>\n"
            f"<i>Supported: aniworld.to, s.to, Kinox, Cineby, BurningSeries, "
            f"MegaKino, FilmPalast, MangaFire, HanimeTV.</i>",
            parse_mode=enums.ParseMode.HTML)

    normalized = normalize_url(url)

    # MegaKino/FilmPalast have no series/season concept at all in
    # aniworld_lib (season_cls=None, series_cls IS episode_cls) — every
    # supported URL for them is already a single watchable page.
    if provider.season_cls is None and provider.series_cls is provider.episode_cls:
        return await _resolve_and_reply(status, provider, url)

    # MangaFire is manga (chapters of page images), not video — wired
    # through its own chapter-list + media-group flow, not the video
    # stream_url path every other provider here uses.
    if provider.name == "MangaFire":
        return await _handle_mangafire_series(status, provider, url)

    # Direct episode URL → resolve immediately, no browsing needed.
    if provider.episode_pattern and provider.episode_pattern.fullmatch(normalized) and \
            not (provider.series_pattern and provider.series_pattern.fullmatch(normalized)):
        return await _resolve_and_reply(status, provider, url)

    if provider.name not in _BROWSABLE_PROVIDERS or not provider.series_cls:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} That's a {provider.name} series/show page, not an episode.</b>\n"
            f"<i>Paste a direct episode URL instead.</i>",
            parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Loading series info...</b>", parse_mode=enums.ParseMode.HTML)
    import asyncio
    try:
        series = await asyncio.to_thread(provider.series_cls, url=url)
        seasons = await asyncio.to_thread(lambda: series.seasons)
    except Exception as e:
        logger.warning(f"aniworld_dl: series load failed for {url}: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't load series.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not seasons:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No seasons found.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _AW_SESSIONS[session_id] = {"series": series, "seasons": seasons, "provider": provider}
    _trim_sessions()

    try:
        title = series.title
    except Exception:
        title = "Series"

    await safe_edit(status.edit_text,
        f"<b>{E_ROCKET} {title}</b>\n<i>{len(seasons)} season(s) found — tap one.</i>",
        reply_markup=_seasons_kb(session_id, seasons),
        parse_mode=enums.ParseMode.HTML)


async def _resolve_and_reply(status: Message, provider, url: str):
    await safe_edit(status.edit_text,
        f"<b>{E_CLOCK} Resolving stream from {provider.name}...</b>\n"
        f"<i>This can take a bit — several of these providers drive a real browser.</i>",
        parse_mode=enums.ParseMode.HTML)
    import asyncio
    try:
        episode = await asyncio.to_thread(provider.episode_cls, url=url)
        stream = await _resolve_stream(episode)
    except Exception as e:
        logger.warning(f"aniworld_dl: resolve failed for {url}: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Resolve failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not stream or not stream.get("videoUrl"):
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No provider on {provider.name} returned a working stream for this episode.</b>",
            parse_mode=enums.ParseMode.HTML)

    used_provider = stream["qualities"][0]["quality"]
    buttons = [[make_button(f"▶️ Play ({used_provider})", url=stream["videoUrl"], style=_BS.PRIMARY if _BS else None)]]
    if meow_downloader.is_download_available():
        dl_id = uuid.uuid4().hex[:10]
        _AW_SESSIONS[dl_id] = {"stream": stream, "title": getattr(episode, "title_en", None)
                                or getattr(episode, "title_de", None) or provider.name}
        _trim_sessions()
        buttons.append([make_button("⬇️ Download & send here", callback_data=f"awgo#{dl_id}", style=_BS.SECONDARY if _BS else None)])

    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} Stream ready</b> <i>(via {used_provider})</i>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML)


# ── MangaFire (manga chapters — images, not video) ─────────────────────

MAX_CHAPTERS = 15
MANGA_GROUP_SIZE = 10  # Telegram's sendMediaGroup hard cap


def _chapters_kb(session_id: str, chapters: list, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(chapters) / MAX_CHAPTERS))
    page = max(0, min(page, total_pages - 1))
    start = page * MAX_CHAPTERS

    buttons = []
    for i, ch in enumerate(chapters[start:start + MAX_CHAPTERS], start=start):
        try:
            label = f"📖 Ch. {ch.episode_number}" + (f" — {ch.title_en or ch.title_de}" if (ch.title_en or ch.title_de) else "")
        except Exception:
            label = f"📖 Chapter {i + 1}"
        buttons.append([make_button(label, callback_data=f"awmg#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])

    if total_pages > 1:
        _sec = getattr(_BS, "SECONDARY", None) if _BS else None
        nav_row = []
        if page > 0:
            nav_row.append(make_button("◀️ Prev", callback_data=f"awmgp#{session_id}#{page - 1}", style=_sec))
        nav_row.append(make_button(f"{page + 1}/{total_pages}", callback_data="awnoop", style=_sec))
        if page < total_pages - 1:
            nav_row.append(make_button("Next ▶️", callback_data=f"awmgp#{session_id}#{page + 1}", style=_sec))
        buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


async def _handle_mangafire_series(status: Message, provider, url: str):
    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Loading manga info...</b>", parse_mode=enums.ParseMode.HTML)
    import asyncio
    try:
        series = await asyncio.to_thread(provider.series_cls, url=url)
        # MangaFireToSeries.seasons IS its flat chapter list (season_cls ==
        # episode_cls == MangaFireToChapter upstream — there's no real
        # season/episode hierarchy for manga, just chapters).
        chapters = await asyncio.to_thread(lambda: series.seasons)
    except Exception as e:
        logger.warning(f"aniworld_dl: MangaFire series load failed for {url}: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't load manga.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not chapters:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No chapters found.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _AW_SESSIONS[session_id] = {"chapters": chapters, "provider": provider}
    _trim_sessions()

    try:
        title = series.title
    except Exception:
        title = "Manga"

    await safe_edit(status.edit_text,
        f"<b>{E_ROCKET} {title}</b>\n<i>{len(chapters)} chapter(s) — tap one to get the pages sent here as photos.</i>",
        reply_markup=_chapters_kb(session_id, chapters),
        parse_mode=enums.ParseMode.HTML)


async def _send_manga_chapter(client: Client, status: Message, chapter, title: str = ""):
    from pyrogram.types import InputMediaPhoto
    import asyncio

    await safe_edit(status.edit_text, f"<b>{E_CLOCK} Fetching page list...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        pages = await asyncio.to_thread(lambda: chapter.pages)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't load pages.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    image_urls = []
    for p in pages:
        try:
            image_urls.append(p.image_url)
        except Exception:
            continue
    if not image_urls:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No pages found in this chapter.</b>", parse_mode=enums.ParseMode.HTML)

    caption = f"<b>{E_CHECK} {title}</b>" if title else None
    sent_groups = 0
    try:
        for i in range(0, len(image_urls), MANGA_GROUP_SIZE):
            batch = image_urls[i:i + MANGA_GROUP_SIZE]
            media = [InputMediaPhoto(u, caption=(caption if i == 0 and j == 0 else None),
                                      parse_mode=enums.ParseMode.HTML if (i == 0 and j == 0) else None)
                     for j, u in enumerate(batch)]
            await client.send_media_group(chat_id=status.chat.id, media=media)
            sent_groups += 1
            await safe_edit(status.edit_text,
                f"<b>{E_CLOCK} Sending pages...</b> {min(i + MANGA_GROUP_SIZE, len(image_urls))}/{len(image_urls)}",
                parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"aniworld_dl: manga send failed: {e}")
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Sending pages failed partway.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent {len(image_urls)} page(s).</b>", parse_mode=enums.ParseMode.HTML)


# ── Callbacks ────────────────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^awmg#"))
async def aniworld_mangafire_chapter_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, ch_idx = callback_query.data.split("#")
    ch_idx = int(ch_idx)
    session = _AW_SESSIONS.get(session_id)
    await callback_query.answer()
    chapters = (session or {}).get("chapters")
    if not session or chapters is None or ch_idx >= len(chapters):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the URL again.</b>", parse_mode=enums.ParseMode.HTML)

    chapter = chapters[ch_idx]
    try:
        title = f"Chapter {chapter.episode_number}"
    except Exception:
        title = "Chapter"
    await _send_manga_chapter(client, callback_query.message, chapter, title)


@Client.on_callback_query(filters.regex(r"^awmgp#"))
async def aniworld_mangafire_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, page = callback_query.data.split("#")
    page = int(page)
    session = _AW_SESSIONS.get(session_id)
    await callback_query.answer()
    chapters = (session or {}).get("chapters")
    if not session or chapters is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the URL again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(chapters)} chapter(s)</b> — tap one to get the pages sent here as photos.",
        reply_markup=_chapters_kb(session_id, chapters, page),
        parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^awnoop$"))
async def aniworld_noop_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^awback#"))
async def aniworld_back_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    session = _AW_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the URL again.</b>", parse_mode=enums.ParseMode.HTML)
    try:
        title = session["series"].title
    except Exception:
        title = "Series"
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {title}</b>\n<i>{len(session['seasons'])} season(s) found — tap one.</i>",
        reply_markup=_seasons_kb(session_id, session["seasons"]),
        parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^awse#"))
async def aniworld_season_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, season_idx = callback_query.data.split("#")
    season_idx = int(season_idx)
    session = _AW_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session or season_idx >= len(session["seasons"]):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the URL again.</b>", parse_mode=enums.ParseMode.HTML)

    season = session["seasons"][season_idx]
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CLOCK} Loading episodes...</b>", parse_mode=enums.ParseMode.HTML)
    import asyncio
    try:
        episodes = await asyncio.to_thread(lambda: season.episodes)
    except Exception as e:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Couldn't load episodes.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    session[f"episodes_{season_idx}"] = episodes
    if not episodes:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} No episodes found in this season.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(episodes)} episode(s)</b> — tap one.",
        reply_markup=_episodes_kb(session_id, season_idx, episodes),
        parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^awsep#"))
async def aniworld_season_page_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, season_idx, page = callback_query.data.split("#")
    season_idx, page = int(season_idx), int(page)
    session = _AW_SESSIONS.get(session_id)
    await callback_query.answer()
    episodes = (session or {}).get(f"episodes_{season_idx}")
    if not session or episodes is None:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the URL again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_ROCKET} {len(episodes)} episode(s)</b> — tap one.",
        reply_markup=_episodes_kb(session_id, season_idx, episodes, page),
        parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^awep#"))
async def aniworld_episode_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, season_idx, ep_idx = callback_query.data.split("#")
    season_idx, ep_idx = int(season_idx), int(ep_idx)
    session = _AW_SESSIONS.get(session_id)
    await callback_query.answer()
    episodes = (session or {}).get(f"episodes_{season_idx}")
    if not session or episodes is None or ep_idx >= len(episodes):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the URL again.</b>", parse_mode=enums.ParseMode.HTML)

    episode = episodes[ep_idx]
    provider = session["provider"]
    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_CLOCK} Resolving stream...</b>\n<i>This can take a bit.</i>",
        parse_mode=enums.ParseMode.HTML)
    try:
        stream = await _resolve_stream(episode)
    except Exception as e:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Resolve failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not stream or not stream.get("videoUrl"):
        return await safe_edit(callback_query.message.edit_text,
            f"<b>{E_CROSS} No provider returned a working stream for this episode.</b>",
            parse_mode=enums.ParseMode.HTML)

    try:
        title = f"{session['series'].title} — Ep {episode.episode_number}"
    except Exception:
        title = f"Episode {ep_idx + 1}"

    used_provider = stream["qualities"][0]["quality"]
    buttons = [[make_button(f"▶️ Play ({used_provider})", url=stream["videoUrl"], style=_BS.PRIMARY if _BS else None)]]
    if meow_downloader.is_download_available():
        dl_id = uuid.uuid4().hex[:10]
        _AW_SESSIONS[dl_id] = {"stream": stream, "title": title}
        _trim_sessions()
        buttons.append([make_button("⬇️ Download & send here", callback_data=f"awgo#{dl_id}", style=_BS.SECONDARY if _BS else None)])
    buttons.append([make_button("⬅️ Back to episodes", callback_data=f"awse#{session_id}#{season_idx}", style=_BS.DANGER if _BS else None)])

    await safe_edit(callback_query.message.edit_text,
        f"<b>{E_CHECK} {title}</b> <i>(via {used_provider})</i>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^awgo#"))
async def aniworld_download_callback(client: Client, callback_query: CallbackQuery):
    dl_id = callback_query.data.split("#", 1)[1]
    entry = _AW_SESSIONS.get(dl_id)
    await callback_query.answer()
    if not entry:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} This link expired — resolve the episode again.</b>", parse_mode=enums.ParseMode.HTML)

    stream, title = entry["stream"], entry["title"]
    status = callback_query.message
    path = None
    try:
        path = await meow_downloader.download_stream(stream, title, status)
        await safe_edit(status.edit_text, f"<b>{E_ROCKET} Uploading to Telegram...</b>", parse_mode=enums.ParseMode.HTML)
        await client.send_video(
            chat_id=status.chat.id,
            video=path,
            caption=f"<b>{E_CHECK} {title}</b>\n<i>via /aniworldurl</i>",
            parse_mode=enums.ParseMode.HTML,
            supports_streaming=True,
        )
        await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"aniworld_dl: download failed: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    finally:
        if path:
            meow_downloader.cleanup(path)
