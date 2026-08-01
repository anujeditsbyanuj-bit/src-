import asyncio
import uuid
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import YTDL_SEARCH_PAGE_SIZE
from Akbots.ytdl import _show_quality_picker
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_SEARCH = '<emoji id=5334544901428229844>🔍</emoji>'

SEARCH_CHUNK_SIZE = 30

# status-message-id -> {"query": str, "results": [...], "exhausted": bool}
_SEARCH_CACHE = {}


def _search_youtube(query: str, chunk_size: int = SEARCH_CHUNK_SIZE):
    """Flat (metadata-only) YouTube search — fast, no per-video info fetch."""
    with yt_dlp.YoutubeDL({
        "quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
        "skip_download": True, "default_search": "ytsearch",
    }) as ydl:
        info = ydl.extract_info(f"ytsearch{chunk_size}:{query}", download=False)

    entries = (info or {}).get("entries") or []
    results = []
    for entry in entries:
        if not entry or not entry.get("id"):
            continue
        duration = entry.get("duration")
        dur_str = ""
        if isinstance(duration, (int, float)) and duration > 0:
            m, s = divmod(int(duration), 60)
            h, m = divmod(m, 60)
            dur_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        results.append({
            "id": entry["id"],
            "title": (entry.get("title") or "Untitled")[:70],
            "uploader": entry.get("uploader") or entry.get("channel") or "",
            "duration": dur_str,
        })
    return results


def _format_results_text(query: str, results, page: int, exhausted: bool = False) -> str:
    start = page * YTDL_SEARCH_PAGE_SIZE
    end = min(start + YTDL_SEARCH_PAGE_SIZE, len(results))

    lines = [f"{E_SEARCH} <b>Search results for:</b> <i>{query}</i>\n"]
    for i, r in enumerate(results[start:end], start=start + 1):
        meta = " — ".join(x for x in (r["uploader"], r["duration"]) if x)
        line = f"{i}. {r['title']}"
        if meta:
            line += f"\n    <i>{meta}</i>"
        lines.append(line)

    if exhausted and end >= len(results):
        lines.append(f"\n<i>Showing {end} of {len(results)} results.</i>")
    else:
        lines.append(f"\n<i>Tap a number to download, or use the buttons for more.</i>")
    return "\n".join(lines)


def _results_keyboard(results, page: int, exhausted: bool = False, status_msg_id: int = None) -> InlineKeyboardMarkup:
    start = page * YTDL_SEARCH_PAGE_SIZE
    end = min(start + YTDL_SEARCH_PAGE_SIZE, len(results))

    buttons, row = [], []
    for abs_idx in range(start, end):
        row.append(make_button(str(abs_idx + 1), callback_data=f"ytsr:{abs_idx}", style=_BS.PRIMARY if _BS else None))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(make_button("◀️ Prev", callback_data=f"ytsrpg:{page - 1}", style=_BS.PRIMARY if _BS else None))
    nav_row.append(make_button(f"Page {page + 1}", callback_data="ytsr:noop", style=_BS.PRIMARY if _BS else None))
    if not exhausted or end < len(results):
        nav_row.append(make_button("Next ▶️", callback_data=f"ytsrpg:{page + 1}", style=_BS.SUCCESS if _BS else None))
    buttons.append(nav_row)

    # Same query, different engine — one tap instead of retyping with
    # /saavn. status_msg_id is the key _SEARCH_CACHE stores the query
    # under, so jiosaavn.py's callback can look it back up.
    if status_msg_id is not None:
        buttons.append([make_button("🎵 Search on JioSaavn instead", callback_data=f"saavnfromtext#{status_msg_id}", style=_BS.PRIMARY if _BS else None)])

    return InlineKeyboardMarkup(buttons)


async def _do_search(client: Client, message: Message, query: str, status: Message = None):
    if yt_dlp is None:
        text = f"<b>{E_CROSS} yt-dlp not installed.</b>\n<i>Run <code>pip install yt-dlp</code> on the host.</i>"
        if status is None:
            return await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        return await status.edit_text(text, parse_mode=enums.ParseMode.HTML)

    if status is None:
        status = await message.reply_text(f"<b>{E_ROCKET} Searching YouTube...</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await status.edit_text(f"<b>{E_ROCKET} Searching YouTube...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        results = await asyncio.to_thread(_search_youtube, query)
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Search failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not results:
        return await status.edit_text(f"<b>{E_CROSS} No results found for:</b> <i>{query}</i>", parse_mode=enums.ParseMode.HTML)

    exhausted = len(results) < SEARCH_CHUNK_SIZE
    _SEARCH_CACHE[status.id] = {"query": query, "results": results, "exhausted": exhausted}
    if len(_SEARCH_CACHE) > 500:
        _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)), None)
    await status.edit_text(
        _format_results_text(query, results, page=0, exhausted=exhausted),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_results_keyboard(results, page=0, exhausted=exhausted, status_msg_id=status.id),
    )


@Client.on_message(filters.command(["search", "yts"]) & filters.private)
async def search_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_ROCKET} Usage:</b> <code>/search &lt;song or video name&gt;</code>\n"
            f"<i>e.g. <code>/search believer imagine dragons</code></i>",
            parse_mode=enums.ParseMode.HTML
        )
    query = message.text.split(None, 1)[1].strip()
    await _do_search(client, message, query)


# ---------------------------------------------------------------------------
# Plain-text auto-search: "lofi song", "believer imagine dragons", etc. with
# no /search command and no link at all falls through to here. Instead of
# picking one engine for the user, it shows a YouTube / JioSaavn / Spotify
# picker — each option runs that engine's own full search (same as typing
# /search, /saavn, or /spotify directly with that query).
#
# Registered LAST (highest group number) so every link-based auto-detect
# across every other file (mega/gdrive/.../ytdl generic fallback) gets first
# refusal; this only ever sees messages nothing else claimed. Also skips
# users mid-/login (phone/code/password prompts in session.py) so their
# input isn't hijacked into a search.
# ---------------------------------------------------------------------------

_URL_HINT = filters.regex(r"https?://|www\.|t\.me/|magnet:\?", flags=0)


def _login_in_progress(_, __, message: Message) -> bool:
    try:
        from Akbots.session import LOGIN_STATE
        return message.from_user is not None and message.from_user.id in LOGIN_STATE
    except Exception:
        return False


_not_logging_in = ~filters.create(_login_in_progress)

# token -> {"query": str, "message": Message} — the original text message is
# kept so a downloaded track/video still threads as a reply to what the user
# actually typed, not to this picker card.
_PLAIN_QUERY_CACHE = {}


def _engine_picker_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        make_button("▶️ YouTube", callback_data=f"enginepick#yt#{token}", style=_BS.SUCCESS if _BS else None),
        make_button("🎵 JioSaavn", callback_data=f"enginepick#saavn#{token}", style=_BS.PRIMARY if _BS else None),
        make_button("🟢 Spotify", callback_data=f"enginepick#spotify#{token}", style=_BS.PRIMARY if _BS else None),
    ]])


@Client.on_message(
    filters.text & filters.private
    & ~filters.regex(r"^/")
    & ~_URL_HINT
    & _not_logging_in,
    group=9,
)
async def plain_text_auto_search(client: Client, message: Message):
    query = message.text.strip()
    if not (1 <= len(query) <= 100):
        return

    token = uuid.uuid4().hex[:10]
    _PLAIN_QUERY_CACHE[token] = {"query": query, "message": message}
    if len(_PLAIN_QUERY_CACHE) > 500:
        _PLAIN_QUERY_CACHE.pop(next(iter(_PLAIN_QUERY_CACHE)), None)

    await message.reply_text(
        f"<b>{E_SEARCH} Search</b> <i>{query}</i> <b>on:</b>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_engine_picker_kb(token),
    )


@Client.on_callback_query(filters.regex(r"^enginepick#(yt|saavn|spotify)#(.+)$"))
async def engine_pick_callback(client: Client, callback_query: CallbackQuery):
    engine, token = callback_query.matches[0].group(1), callback_query.matches[0].group(2)
    cached = _PLAIN_QUERY_CACHE.pop(token, None)
    await callback_query.answer()
    if not cached:
        return await callback_query.message.edit_text(
            f"<b>{E_CROSS} This search expired — send the song name again.</b>", parse_mode=enums.ParseMode.HTML
        )

    query, orig_message = cached["query"], cached["message"]
    status = callback_query.message

    if engine == "yt":
        await _do_search(client, orig_message, query, status=status)
    elif engine == "saavn":
        from Akbots.jiosaavn import _do_saavn_search
        await _do_saavn_search(client, orig_message, query, status=status)
    elif engine == "spotify":
        from Akbots.spotify import _do_spotify_search
        await _do_spotify_search(client, orig_message, query, status=status)


@Client.on_callback_query(filters.regex(r"^ytsr:noop$"))
async def search_page_indicator_callback(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^ytsr:(\d+)$"))
async def search_result_callback(client: Client, callback_query: CallbackQuery):
    cached = _SEARCH_CACHE.get(callback_query.message.id)
    if not cached:
        return await callback_query.answer("This search result has expired — please search again.", show_alert=True)

    idx = int(callback_query.matches[0].group(1))
    results = cached["results"]
    if idx < 0 or idx >= len(results):
        return await callback_query.answer("Invalid selection.", show_alert=True)

    await callback_query.answer("Fetching qualities...")
    video_url = f"https://www.youtube.com/watch?v={results[idx]['id']}"
    await _show_quality_picker(client, callback_query.message, video_url)


@Client.on_callback_query(filters.regex(r"^ytsrpg:(\d+)$"))
async def search_page_callback(client: Client, callback_query: CallbackQuery):
    cached = _SEARCH_CACHE.get(callback_query.message.id)
    if not cached:
        return await callback_query.answer("This search result has expired — please search again.", show_alert=True)

    page = int(callback_query.matches[0].group(1))
    results, query = cached["results"], cached["query"]
    exhausted = cached.get("exhausted", False)

    if not exhausted:
        page_start = page * YTDL_SEARCH_PAGE_SIZE
        while page_start >= len(results) and not exhausted:
            await callback_query.answer("Loading more results...")
            try:
                more = await asyncio.to_thread(_search_youtube, query)
                if not more or len(more) < SEARCH_CHUNK_SIZE:
                    exhausted = True
                    cached["exhausted"] = True
                existing_ids = {r["id"] for r in results}
                for r in more:
                    if r["id"] not in existing_ids:
                        results.append(r)
                        existing_ids.add(r["id"])
                cached["results"] = results
            except Exception as e:
                return await callback_query.answer(f"Error: {e}", show_alert=True)
            page_start = page * YTDL_SEARCH_PAGE_SIZE

    await callback_query.answer()
    await callback_query.message.edit_text(
        _format_results_text(query, results, page=page, exhausted=exhausted),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_results_keyboard(results, page=page, exhausted=exhausted, status_msg_id=callback_query.message.id),
    )
