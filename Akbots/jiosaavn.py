# Akbots
# JioSaavn music downloader — ported from the standalone jiosaavn-main bot
# (its api/jiosaavn.py + plugins/download_handler.py) into this bot's
# plugin style. Supports:
#   - /saavn <song name>  — search JioSaavn, pick a result, pick bitrate, get MP3
#   - Pasting a jiosaavn.com song/album/playlist link — auto-detected, same
#     as every other link-based plugin in this bot (mega.py, gdrive.py, ...)
#   - Album/playlist links download every track in the set, one after another
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import re
import html
import uuid
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.direct_utils import (
    make_output_folder, safe_filename, make_upload_progress, stream_download,
    E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_BOLT,
)
from Akbots.link_cache import try_send_cached, store as _cache_store
from Akbots.jiosaavn_api import Jiosaavn, HEADERS as SAAVN_HEADERS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    from mutagen.mp4 import MP4, MP4Cover
except ImportError:
    MP4 = None
    MP4Cover = None

JIOSAAVN_PATTERN = re.compile(
    r"(https?://)?(www\.)?jiosaavn\.com/(song|album|featured|artist)/\S+",
    re.IGNORECASE,
)

# session_id -> {"kind": "song"|"album"|"playlist", "item_id": str,
#                "chat_id": int, "message": Message}
_SESSIONS = {}


def _extract_url(text: str):
    m = JIOSAAVN_PATTERN.search(text)
    return m.group(0) if m else None


def _kind_and_id(url: str):
    """('song'|'album'|'playlist'|'artist', item_id) from a jiosaavn.com URL
    — same rule the original bot used: the path segment tells you the type,
    the last path segment is the API token/id."""
    item_id = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    lower = url.lower()
    if "/song/" in lower:
        return "song", item_id
    if "/album/" in lower:
        return "album", item_id
    if "/featured/" in lower:
        return "playlist", item_id
    if "/artist/" in lower:
        return "artist", item_id
    return None, item_id


def _quality_kb(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        make_button("🎧 320 kbps", callback_data=f"saavnq#{session_id}#320", style=_BS.PRIMARY if _BS else None),
        make_button("🎧 160 kbps", callback_data=f"saavnq#{session_id}#160", style=_BS.PRIMARY if _BS else None),
    ], [
        make_button("❌ Cancel", callback_data=f"saavncancel#{session_id}", style=_BS.DANGER if _BS else None),
    ]])


async def _start_session(client: Client, message: Message, kind: str, item_id: str, user_id: int = None):
    if kind == "artist":
        await message.reply_text(
            f"<b>{E_INFO} Artist links aren't downloadable directly.</b>\n"
            f"Use <code>/saavn &lt;artist or song name&gt;</code> to search their tracks instead.",
            parse_mode=enums.ParseMode.HTML,
        )
        return
    if kind is None:
        await message.reply_text(f"<b>{E_CROSS} Couldn't recognise that JioSaavn link.</b>", parse_mode=enums.ParseMode.HTML)
        return

    session_id = uuid.uuid4().hex[:10]
    _SESSIONS[session_id] = {
        "kind": kind, "item_id": item_id, "chat_id": message.chat.id,
        "message": message, "user_id": user_id or message.from_user.id,
    }
    label = {"song": "Song", "album": "Album", "playlist": "Playlist"}[kind]
    await message.reply_text(
        f"<b>{E_ROCKET} {label} detected — choose audio quality:</b>",
        reply_markup=_quality_kb(session_id),
        parse_mode=enums.ParseMode.HTML,
    )


def _artist_names(more_info: dict, role: str) -> str:
    artists = more_info.get("artistMap", {}).get("artists", [])
    names = [a.get("name") for a in artists if a.get("role") == role]
    return ", ".join(filter(None, names))


async def _download_one_song(client: Client, message: Message, status: Message, song_id: str, bitrate: int, user_id: int = None):
    """Downloads + sends a single song. Returns True on success, False on
    any failure (so batch downloads can skip and continue to the next track
    instead of aborting the whole album/playlist)."""
    user_id = user_id or message.from_user.id
    saavn = Jiosaavn()
    try:
        song_response = await saavn.get_song(song_id=song_id)
        song_data = (song_response or {}).get("songs", [None])[0]
        if not song_data:
            return False
    except Exception:
        return False

    title = html.unescape(song_data.get("title", "Unknown"))
    more_info = song_data.get("more_info", {})
    album = html.unescape(more_info.get("album", "") or "")
    music = more_info.get("music") or _artist_names(more_info, "music")
    singers = _artist_names(more_info, "singer") or music or "Unknown"
    duration = int(more_info.get("duration") or 0)
    release_year = song_data.get("year")
    image_url = (song_data.get("image") or "").replace("150x150", "500x500")
    song_url = song_data.get("perma_url") or f"https://www.jiosaavn.com/song/{song_id}"

    if await try_send_cached(client, message, song_url, status):
        return True

    folder = make_output_folder("jiosaavn")
    safe_title = safe_filename(title, "song")
    unique = f"{safe_title}_{song_id}"
    dest = os.path.join(folder, f"{unique}.m4a")
    thumb_path = os.path.join(folder, f"{unique}.jpg")

    try:
        dl_data = await saavn.get_download_url(song_id=song_id, bitrate=bitrate)
        auth_url = (dl_data or {}).get("auth_url")
        if not auth_url:
            await status.edit_text(f"<b>{E_CROSS} No downloadable stream found for:</b> {title}", parse_mode=enums.ParseMode.HTML)
            return False

        if image_url:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(image_url, headers=SAAVN_HEADERS) as r:
                        if r.status == 200:
                            with open(thumb_path, "wb") as f:
                                f.write(await r.read())
            except Exception:
                pass

        await stream_download(
            auth_url, dest, status, f"Downloading {title}",
            headers=SAAVN_HEADERS, user_id=user_id, file_name=f"{unique}.m4a",
        )

        if MP4 is not None:
            try:
                audio = MP4(dest)
                audio["\xa9nam"] = title
                audio["\xa9alb"] = album
                audio["\xa9ART"] = singers
                if release_year:
                    audio["\xa9day"] = str(release_year)
                if os.path.exists(thumb_path):
                    with open(thumb_path, "rb") as f:
                        audio["covr"] = [MP4Cover(f.read(), imageformat=MP4Cover.FORMAT_JPEG)]
                audio.save()
            except Exception:
                pass  # tagging is best-effort — an untagged file is still fine to send

        caption_lines = [
            f"<b>🎧 {title}</b>",
            f"<b>📚 Album:</b> {album}" if album else "",
            f"<b>🎤 Artist:</b> {singers}" if singers else "",
            f"<b>🎚 Quality:</b> {bitrate}kbps",
        ]
        caption = "\n".join(filter(None, caption_lines))

        progress = make_upload_progress(status, file_name=title, duration=duration, quality=f"{bitrate}kbps")
        sent = await client.send_audio(
            chat_id=message.chat.id, audio=dest,
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            duration=duration, title=title, performer=singers,
            caption=caption, reply_to_message_id=message.id,
            parse_mode=enums.ParseMode.HTML, progress=progress,
        )
        try:
            from Akbots.backup import backup_message
            await backup_message(client, sent)
        except Exception:
            pass
        try:
            await _cache_store(song_url, sent, caption=caption)
        except Exception:
            pass
        return True
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Failed:</b> {title}\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        return False
    finally:
        # make_output_folder() returns a shared "downloads/jiosaavn" folder
        # used by every concurrent user — only remove this song's own two
        # files, never the whole folder (that would delete other users'
        # in-progress downloads too).
        for p in (dest, thumb_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def _run_download(client: Client, message: Message, kind: str, item_id: str, bitrate: int, status: Message, user_id: int = None):
    if kind == "song":
        await status.edit_text(f"<b>{E_BOLT} Starting download...</b>", parse_mode=enums.ParseMode.HTML)
        await _download_one_song(client, message, status, item_id, bitrate, user_id=user_id)
        return

    saavn = Jiosaavn()
    album_id = item_id if kind == "album" else None
    playlist_id = item_id if kind == "playlist" else None
    page_no = 1
    total_done = 0
    while True:
        try:
            response = await saavn.get_playlist_or_album(album_id=album_id, playlist_id=playlist_id, page_no=page_no)
        except Exception as e:
            await status.edit_text(f"<b>{E_CROSS} Failed to fetch {kind}:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
            return
        songs = (response or {}).get("list") or []
        if not songs:
            break
        for song in songs:
            song_url = song.get("perma_url", "")
            song_id = song_url.rsplit("/", 1)[-1] if song_url else None
            if not song_id:
                continue
            total_done += 1
            await status.edit_text(
                f"<b>{E_BOLT} Downloading track {total_done}...</b>", parse_mode=enums.ParseMode.HTML
            )
            await _download_one_song(client, message, status, song_id, bitrate, user_id=user_id)
        page_no += 1

    if total_done == 0:
        await status.edit_text(f"<b>{E_CROSS} No tracks found in this {kind}.</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await status.edit_text(f"<b>{E_CHECK} Done — {total_done} track(s) sent.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.text & filters.private & filters.regex(JIOSAAVN_PATTERN) & ~filters.regex(r"^/"), group=1)
async def jiosaavn_auto_detect(client: Client, message: Message):
    url = _extract_url(message.text)
    if not url:
        return
    kind, item_id = _kind_and_id(url)
    await _start_session(client, message, kind, item_id)


async def _do_saavn_search(client: Client, message: Message, query: str, status: Message = None):
    """Runs a JioSaavn song search and shows tappable results. `status` lets
    a caller that already posted a placeholder message (e.g. the plain-text
    auto-search button below) reuse/edit it instead of a fresh reply."""
    if status is None:
        status = await message.reply_text(f"<b>{E_INFO} Searching JioSaavn for:</b> {query}", parse_mode=enums.ParseMode.HTML)
    else:
        await status.edit_text(f"<b>{E_INFO} Searching JioSaavn for:</b> {query}", parse_mode=enums.ParseMode.HTML)

    try:
        response = await Jiosaavn().search(query=query, search_type="songs", page_size=8)
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Search failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    results = (response or {}).get("results", [])
    if not results:
        return await status.edit_text(f"<b>{E_CROSS} No results for:</b> {query}", parse_mode=enums.ParseMode.HTML)

    buttons = []
    for result in results:
        song_url = result.get("perma_url", "")
        song_id = song_url.rsplit("/", 1)[-1] if song_url else None
        if not song_id:
            continue
        title = html.unescape(result.get("title", "Unknown"))
        album = html.unescape(result.get("more_info", {}).get("album", "") or "")
        label = f"🎙 {title} — {album}" if album else f"🎙 {title}"
        buttons.append([make_button(label[:64], callback_data=f"saavnpick#{song_id}", style=_BS.PRIMARY if _BS else None)])

    if not buttons:
        return await status.edit_text(f"<b>{E_CROSS} No results for:</b> {query}", parse_mode=enums.ParseMode.HTML)

    await status.edit_text(
        f"<b>{E_ROCKET} Results for:</b> {query}\n<i>Tap a song to download it.</i>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["saavn", "jiosaavn"]) & filters.private)
async def saavn_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b>\n"
            f"<code>/saavn &lt;song name&gt;</code> — search JioSaavn\n"
            f"<code>/saavn &lt;jiosaavn.com link&gt;</code> — song/album/playlist link also works",
            parse_mode=enums.ParseMode.HTML,
        )
    query = message.text.split(None, 1)[1].strip()

    if JIOSAAVN_PATTERN.search(query):
        url = _extract_url(query)
        kind, item_id = _kind_and_id(url)
        return await _start_session(client, message, kind, item_id)

    await _do_saavn_search(client, message, query)


# ---------------------------------------------------------------------------
# Auto-detection for plain-typed song names (no /saavn command at all).
# ytsearch.py's plain_text_auto_search already claims every bare, non-URL
# text message (group=9) and treats it as a YouTube search — that handler
# runs first and can't be duplicated here without picking one engine over
# the other for the same ambiguous text. Instead, its results message gets
# an extra button offering the same query on JioSaavn — one tap, no typing
# /saavn — which is what actually needs "auto-detecting" here.
# ---------------------------------------------------------------------------

@Client.on_callback_query(filters.regex(r"^saavnfromtext#"))
async def saavn_from_text_callback(client: Client, callback_query: CallbackQuery):
    status_msg_id = int(callback_query.data.split("#", 1)[1])
    await callback_query.answer()
    try:
        from Akbots.ytsearch import _SEARCH_CACHE
        cached = _SEARCH_CACHE.get(status_msg_id)
    except Exception:
        cached = None
    if not cached:
        return await callback_query.message.edit_text(
            f"<b>{E_CROSS} That search expired — send the song name again.</b>", parse_mode=enums.ParseMode.HTML
        )
    await _do_saavn_search(client, callback_query.message, cached["query"], status=callback_query.message)


@Client.on_callback_query(filters.regex(r"^saavnpick#"))
async def saavn_pick_callback(client: Client, callback_query: CallbackQuery):
    song_id = callback_query.data.split("#", 1)[1]
    await callback_query.answer()
    await _start_session(client, callback_query.message, "song", song_id, user_id=callback_query.from_user.id)


@Client.on_callback_query(filters.regex(r"^saavnq#"))
async def saavn_quality_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, bitrate = callback_query.data.split("#")
    session = _SESSIONS.pop(session_id, None)
    await callback_query.answer()
    if not session:
        return await callback_query.message.edit_text(f"<b>{E_CROSS} Session expired — send the link again.</b>", parse_mode=enums.ParseMode.HTML)

    status = callback_query.message
    await status.edit_text(f"<b>{E_INFO} Preparing download...</b>", parse_mode=enums.ParseMode.HTML)
    await _run_download(client, session["message"], session["kind"], session["item_id"], int(bitrate), status, user_id=session.get("user_id"))


@Client.on_callback_query(filters.regex(r"^saavncancel#"))
async def saavn_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await callback_query.message.edit_text(f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)
