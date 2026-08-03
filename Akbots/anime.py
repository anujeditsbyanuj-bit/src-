# Akbots
# Anime downloader — on-demand version of AutoAnimeBot's SubsPlease
# integration, PLUS an optional background auto-poster.
#
# On-demand (/anime <name>) reuses this bot's existing torrent/aria2
# download pipeline (Akbots/torrent.py) for the actual download+upload —
# no separate download code to maintain.
#
# The auto-poster (set up via /set_anime_channel, admin-only) polls
# SubsPlease's "latest releases" endpoint on an interval and posts any
# episode not seen before straight into the configured channel, the same
# way the original AutoAnimeBot did — minus the FFmpeg re-encoding step
# (raw fansub file is posted as-is) and minus the Telethon user-session
# requirement (this bot's own Pyrogram bot session uploads directly,
# exactly like every other plugin here).
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import uuid
import hashlib
import asyncio
import logging
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import ADMINS
from database.db import db
from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO, E_ROCKET, safe_edit
from Akbots.torrent import _handle as _torrent_download
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

logger = logging.getLogger(__name__)

SUBSPLEASE_SEARCH = "https://subsplease.org/api/?f=search&tz=Asia/Kolkata&s={query}"
SUBSPLEASE_LATEST = "https://subsplease.org/api/?f=latest&tz=Asia/Kolkata"
SUBSPLEASE_BASE = "https://subsplease.org"

MAX_SHOWS = 12
MAX_EPISODES = 15

# Auto-poster settings
ANIME_CHECK_INTERVAL_MINUTES = 10
MAX_NEW_PER_CHECK = 5          # caps a flood if the channel is set up fresh
ANIME_QUALITY_PRIORITY = ["1080", "720", "480"]   # first one available wins

# session_id -> {"shows": {show_name: [entries...]}, "order": [show_name, ...]}
_SESSIONS = {}




async def _search(query: str):
    url = SUBSPLEASE_SEARCH.format(query=query.replace(" ", "%20"))
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return {}
            data = await r.json(content_type=None)
    return data if isinstance(data, dict) else {}


def _group_by_show(raw: dict, query: str):
    """SubsPlease's search endpoint is fuzzy and can return unrelated
    recent releases alongside real matches — filter down to entries whose
    show name actually contains the query, then group by show, newest
    episode first."""
    q = query.lower()
    shows = {}
    for entry in raw.values():
        show = entry.get("show", "Unknown")
        if q not in show.lower():
            continue
        shows.setdefault(show, []).append(entry)
    for eps in shows.values():
        eps.sort(key=lambda e: e.get("release_date", ""), reverse=True)
    return shows


def _quality_kb(session_id: str, show_idx: int, ep_idx: int, downloads: list) -> InlineKeyboardMarkup:
    row = []
    for d in downloads:
        res = d.get("res", "?")
        row.append(make_button(f"{res}ᴘ", callback_data=f"animedl#{session_id}#{show_idx}#{ep_idx}#{res}", style=_BS.PRIMARY if _BS else None))
    return InlineKeyboardMarkup([row, [make_button("⬅️ ʙᴀᴄᴋ ᴛᴏ ᴇᴘɪsᴏᴅᴇs", callback_data=f"animesh#{session_id}#{show_idx}", style=_BS.DANGER if _BS else None)]])


def _episodes_kb(session_id: str, show_idx: int, entries: list) -> InlineKeyboardMarkup:
    buttons = []
    for i, e in enumerate(entries[:MAX_EPISODES]):
        label = f"Ep {e.get('episode', '?')}  ·  {e.get('time', '')}"
        buttons.append([make_button(label, callback_data=f"animeep#{session_id}#{show_idx}#{i}", style=_BS.PRIMARY if _BS else None)])
    buttons.append([make_button("⬅️ ʙᴀᴄᴋ ᴛᴏ ʀᴇsᴜʟᴛs", callback_data=f"animeback#{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(buttons)


def _shows_kb(session_id: str, order: list) -> InlineKeyboardMarkup:
    buttons = []
    for i, show in enumerate(order[:MAX_SHOWS]):
        buttons.append([make_button(f"📺 {show}", callback_data=f"animesh#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])
    return InlineKeyboardMarkup(buttons)


@Client.on_message(filters.command(["anime", "subsplease"]) & filters.private)
async def anime_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/anime &lt;show name&gt;</code>\n"
            f"<i>e.g. <code>/anime one piece</code></i>\n\n"
            f"<i>Note: this searches SubsPlease's currently airing/recent releases — older, "
            f"fully-finished shows may not turn up here.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    query = message.text.split(None, 1)[1].strip()
    status = await message.reply_text(f"<b>{E_INFO} Searching SubsPlease for:</b> {query}", parse_mode=enums.ParseMode.HTML)

    try:
        raw = await _search(query)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Search failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    shows = _group_by_show(raw, query)
    if not shows:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} No matches for:</b> {query}\n"
            f"<i>SubsPlease only tracks currently airing / recent shows.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    order = list(shows.keys())
    session_id = uuid.uuid4().hex[:10]
    _SESSIONS[session_id] = {"shows": shows, "order": order, "message": message}
    if len(_SESSIONS) > 300:
        _SESSIONS.pop(next(iter(_SESSIONS)), None)

    await safe_edit(status.edit_text, 
        f"<b>{E_ROCKET} Found {len(order)} show(s) for:</b> {query}\n<i>Tap one to see episodes.</i>",
        reply_markup=_shows_kb(session_id, order),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^animeback#"))
async def anime_back_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    session = _SESSIONS.get(session_id)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)
    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_ROCKET} Found {len(session['order'])} show(s).</b>\n<i>Tap one to see episodes.</i>",
        reply_markup=_shows_kb(session_id, session["order"]),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^animesh#"))
async def anime_show_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, show_idx = callback_query.data.split("#")
    show_idx = int(show_idx)
    session = _SESSIONS.get(session_id)
    await callback_query.answer()
    if not session or show_idx >= len(session["order"]):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)

    show = session["order"][show_idx]
    entries = session["shows"][show]
    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_ROCKET} {show}</b>\n<i>{len(entries)} episode(s) available — tap one:</i>",
        reply_markup=_episodes_kb(session_id, show_idx, entries),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^animeep#"))
async def anime_episode_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, show_idx, ep_idx = callback_query.data.split("#")
    show_idx, ep_idx = int(show_idx), int(ep_idx)
    session = _SESSIONS.get(session_id)
    await callback_query.answer()
    if not session or show_idx >= len(session["order"]):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)

    show = session["order"][show_idx]
    entries = session["shows"][show]
    if ep_idx >= len(entries):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Episode not found.</b>", parse_mode=enums.ParseMode.HTML)

    entry = entries[ep_idx]
    downloads = entry.get("downloads") or []
    if not downloads:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} No download links for this episode.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_ROCKET} {show} — Ep {entry.get('episode', '?')}</b>\n<i>Choose quality:</i>",
        reply_markup=_quality_kb(session_id, show_idx, ep_idx, downloads),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^animedl#"))
async def anime_download_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, show_idx, ep_idx, res = callback_query.data.split("#")
    show_idx, ep_idx = int(show_idx), int(ep_idx)
    session = _SESSIONS.get(session_id)
    await callback_query.answer()
    if not session or show_idx >= len(session["order"]):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — search again.</b>", parse_mode=enums.ParseMode.HTML)

    show = session["order"][show_idx]
    entries = session["shows"][show]
    if ep_idx >= len(entries):
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Episode not found.</b>", parse_mode=enums.ParseMode.HTML)

    entry = entries[ep_idx]
    magnet = next((d.get("magnet") for d in entry.get("downloads", []) if d.get("res") == res), None)
    if not magnet:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} That quality isn't available anymore.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_CHECK} {show} — Ep {entry.get('episode', '?')} ({res}p)</b>\n<i>Handing off to the torrent downloader...</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    # Reuses this bot's existing magnet/torrent -> aria2c -> upload pipeline
    # as-is (progress bar, /queue, /cancel_all all come for free) instead
    # of reimplementing torrent downloading here.
    await _torrent_download(client, session["message"], magnet)


# =====================================================================
# Auto-poster — background job + admin channel config
# =====================================================================

def _is_batch_episode(episode: str) -> bool:
    """Season/batch releases have a range like '01-12' instead of a single
    episode number — same skip rule the original bot used, since a batch
    torrent is usually many GB and not something you want auto-downloaded
    unattended."""
    return "-" in (episode or "")


def _episode_uid(show: str, episode: str) -> str:
    return hashlib.sha256(f"{show}::{episode}".encode()).hexdigest()


def _best_magnet(downloads: list):
    by_res = {d.get("res"): d.get("magnet") for d in downloads}
    for res in ANIME_QUALITY_PRIORITY:
        if by_res.get(res):
            return res, by_res[res]
    return None, None


@Client.on_message(filters.command("set_anime_channel") & filters.user(ADMINS))
async def set_anime_channel_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/set_anime_channel -100xxxxxxxxxx</code>\n"
            f"<i>Make sure the bot is already an admin in that channel.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    raw = message.command[1].strip()
    try:
        chat_id = int(raw)
    except ValueError:
        return await message.reply_text(f"<b>{E_CROSS} Invalid channel ID.</b>", parse_mode=enums.ParseMode.HTML)

    try:
        chat = await client.get_chat(chat_id)
        await client.send_message(
            chat_id,
            f"{E_CHECK} <b>ᴀɴɪᴍᴇ ᴀᴜᴛᴏ-ᴘᴏsᴛᴇʀ ʟɪɴᴋᴇᴅ ᴛᴏ ᴛʜɪs ᴄʜᴀɴɴᴇʟ</b>\n"
            f"<i>New SubsPlease episodes will be posted here automatically "
            f"(checked every {ANIME_CHECK_INTERVAL_MINUTES} min).</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        return await message.reply_text(
            f"<b>{E_CROSS} Couldn't post there.</b>\n"
            f"<i>Make sure the bot is an admin (with post permission) in that channel first.</i>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.set_anime_channel(chat_id)
    await message.reply_text(
        f"<b>{E_CHECK} Anime channel set:</b> {chat.title or chat_id}\n"
        f"<i>To stop auto-posting: /del_anime_channel</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("del_anime_channel") & filters.user(ADMINS))
async def del_anime_channel_command(client: Client, message: Message):
    await db.set_anime_channel(None)
    await message.reply_text(f"<b>{E_CHECK} Anime auto-poster disabled.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("anime_channel_status") & filters.user(ADMINS))
async def anime_channel_status_command(client: Client, message: Message):
    channel = await db.get_anime_channel()
    if not channel:
        return await message.reply_text(
            f"<b>{E_INFO} No anime channel set.</b> Use /set_anime_channel.", parse_mode=enums.ParseMode.HTML
        )
    try:
        chat = await client.get_chat(channel)
        title = chat.title or str(channel)
    except Exception:
        title = str(channel)
    await message.reply_text(
        f"<b>{E_INFO} Anime auto-poster is ON</b>\n<b>ᴄʜᴀɴɴᴇʟ:</b> {title}\n<code>{channel}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


async def check_new_anime(client: Client):
    channel = await db.get_anime_channel()
    if not channel:
        return

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(SUBSPLEASE_LATEST, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return
                data = await r.json(content_type=None)
    except Exception as e:
        logger.warning(f"Anime auto-poster: fetch failed: {e}")
        return

    if not isinstance(data, dict):
        return

    # SubsPlease returns newest-first; reverse so unseen items post
    # oldest-to-newest, same convention as rss.py, and cap the batch.
    entries = list(data.values())
    entries.reverse()

    posted = 0
    for entry in entries:
        if posted >= MAX_NEW_PER_CHECK:
            break
        show = entry.get("show", "Unknown")
        episode = entry.get("episode", "")
        if _is_batch_episode(episode):
            continue

        uid = _episode_uid(show, episode)
        if await db.is_anime_uploaded(uid):
            continue

        res, magnet = _best_magnet(entry.get("downloads") or [])
        if not magnet:
            await db.add_anime_uploaded(uid)
            continue

        try:
            notice = await client.send_message(
                channel,
                f"<b>{E_ROCKET} {show} — Episode {episode}</b>\n<i>({res}p, via SubsPlease)</i>",
                parse_mode=enums.ParseMode.HTML,
            )
            await _torrent_download(client, notice, magnet)
        except Exception as e:
            logger.warning(f"Anime auto-poster: failed to post {show} ep {episode}: {e}")

        await db.add_anime_uploaded(uid)
        posted += 1


_scheduler = None


def schedule_anime_poster(app: Client):
    """Starts the periodic new-episode check. No-ops (with a log warning)
    if apscheduler isn't installed — the /anime on-demand search still
    works fine either way, this only affects the background auto-poster."""
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("Anime auto-poster needs apscheduler — add it to requirements.txt.")
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        lambda: asyncio.create_task(check_new_anime(app)),
        "interval", minutes=ANIME_CHECK_INTERVAL_MINUTES,
    )
    _scheduler.start()
    logger.info(f"Anime auto-poster scheduler started (every {ANIME_CHECK_INTERVAL_MINUTES} min).")
