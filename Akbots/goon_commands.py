# Akbots - Don't Remove Credit - @AkBots_Official
#
# Auto-detect (paste a link) AND /ak <link> (manual command) both work.
# Either way: resolves the link via Akbots/goon_provider.py (login + m3u8
# extraction, now with master-playlist variant parsing — see that
# module's docstring), shows a quality-choose menu (real resolutions
# found in the stream, snapped to the standard 2160p/1080p/720p/480p/
# 240p/144p ladder, plus Auto (Best)), downloads the picked one with
# Akbots/meow_downloader.py (same yt-dlp-based downloader every other
# Meow* provider uses), and sends the file straight to the chat. Same
# auto-detect pattern the other bypassers in Akbots/premiumlinks.py use.
#
# Entirely inactive until config.GOON_BASE_URL is set — see that
# comment in config.py. With it unset, both the autodetect filter and
# /ak reject everything (GOON_URL_PATTERN stays None), so this file is a
# complete no-op either way.
#
# Auto-discovered by Pyrogram's plugin loader (plugins=dict(root="Akbots")
# in bot.py) — no manual registration needed.

import time
import uuid
import logging
import re
from urllib.parse import urlparse

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from Akbots.direct_utils import safe_edit, E_CHECK, E_CROSS, E_BOLT, make_upload_progress
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

from Akbots import goon_provider, meow_downloader
from config import GOON_BASE_URL

logger = logging.getLogger(__name__)

E_GEAR = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

_GOON_HOST = urlparse(GOON_BASE_URL).netloc if GOON_BASE_URL else None
# Built once at import time from config.GOON_BASE_URL — if that's unset,
# this is None and the filter below is built to never match anything (see
# filters.create lambda), rather than crashing on an empty regex.
GOON_URL_PATTERN = re.compile(re.escape(_GOON_HOST)) if _GOON_HOST else None

# Order the quality grid renders in — 2 buttons per row, same ladder
# goon_provider._build_qualities() snaps real variants onto.
_QUALITY_ORDER = ["2160p", "1080p", "720p", "480p", "240p", "144p"]

# Short-token cache so callback_data (64-byte Telegram limit) never has to
# carry a raw stream dict — tokens expire after 1 hour. Same pattern as
# Akbots/meow_commands.py's _CACHE.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600


def _store(payload: dict) -> str:
    token = uuid.uuid4().hex[:12]
    _CACHE[token] = (time.time(), payload)
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


def _quality_keyboard(token: str) -> InlineKeyboardMarkup:
    """Same layout as the screenshot: resolution buttons 2-per-row (only
    the ones actually present in this stream's qualities list), then a
    full-width "Auto (Best)" row, then a full-width "Cancel" row."""
    entry = _load(token)
    available = {q["quality"] for q in (entry or {}).get("qualities", []) if q.get("quality") != "Auto"}

    rows = []
    row = []
    for label in _QUALITY_ORDER:
        if label not in available:
            continue
        row.append(make_button(f"🎞 {label}", callback_data=f"goonq:{token}:{label}", style=_style("PRIMARY")))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([make_button("⚡ Auto (Best)", callback_data=f"goonq:{token}:Auto", style=_style("SUCCESS"))])
    rows.append([make_button("❌ Cancel", callback_data=f"goonq:{token}:cancel", style=_style("DANGER"))])
    return InlineKeyboardMarkup(rows)


async def _resolve_and_show_qualities(client: Client, message: Message, video_url: str):
    status = await message.reply_text(f"<b>{E_GEAR} Resolving stream link...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        stream = await goon_provider.resolve_goon_stream(video_url)
    except Exception as e:
        logger.warning(f"goon_commands: resolve failed for {video_url}: {e}")
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Couldn't resolve a stream link.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)

    if not stream or not stream.get("videoUrl"):
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} No playable stream found for that link.</b>", parse_mode=enums.ParseMode.HTML)

    qualities = stream.get("qualities") or [{"quality": "Auto", "url": stream["videoUrl"]}]
    token = _store({"stream": stream, "qualities": qualities, "chat_id": message.chat.id})

    # Only one usable entry (Auto) — no real variants were found in the
    # master playlist, so skip the menu and go straight to download
    # instead of showing a picker with nothing meaningful to pick.
    if len(qualities) <= 1:
        return await _download_and_upload(client, status, token, "Auto")

    await safe_edit(status.edit_text,
        f"<b>{E_BOLT} Apni quality choose karo:</b>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_quality_keyboard(token))


async def _download_and_upload(client: Client, status: Message, token: str, quality: str):
    entry = _load(token)
    if not entry:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} This request expired, please send the link again.</b>", parse_mode=enums.ParseMode.HTML)

    stream = entry["stream"]
    await safe_edit(status.edit_text, f"<b>{E_GEAR} Starting download ({quality})...</b>", parse_mode=enums.ParseMode.HTML)

    path = None
    try:
        pick = quality if quality != "Auto" else None
        path = await meow_downloader.download_stream(stream, "video", status, quality=pick)
        progress = make_upload_progress(status, file_name="video")
        await client.send_video(
            chat_id=entry["chat_id"],
            video=path,
            caption=f"<b>{E_CHECK} Downloaded</b> · {quality}",
            parse_mode=enums.ParseMode.HTML,
            supports_streaming=True,
            progress=progress,
        )
        await safe_edit(status.edit_text, f"<b>{E_CHECK} Sent.</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"goon_commands: download failed for token {token}: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
    finally:
        if path:
            meow_downloader.cleanup(path)
        _CACHE.pop(token, None)


@Client.on_message(filters.command("ak") & filters.private)
async def ak_command(client: Client, message: Message):
    args = message.text.split(maxsplit=1)
    source_text = args[1] if len(args) >= 2 else (message.reply_to_message.text if message.reply_to_message else "")

    if not GOON_URL_PATTERN:
        return await message.reply_text(
            f"<b>{E_CROSS} This isn't set up yet</b> — GOON_BASE_URL isn't configured.",
            parse_mode=enums.ParseMode.HTML
        )

    match = GOON_URL_PATTERN.search(source_text or "")
    if not match:
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/ak &lt;link&gt;</code>\n"
            f"<i>Or reply to a message containing the link with</i> <code>/ak</code>.\n"
            f"<i>Link must be from {_GOON_HOST}.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    # Pull the actual URL out (not just the matched host) — same shape
    # extract_all_diskwala_urls-style helpers in other modules use, just
    # inline here since this file only ever deals with one link at a time.
    url_match = re.search(r"https?://\S+", source_text)
    video_url = url_match.group(0).rstrip(").,]}\"'") if url_match else source_text.strip()

    await _resolve_and_show_qualities(client, message, video_url)


@Client.on_message(
    filters.text & filters.private
    & filters.create(lambda _, __, m: bool(GOON_URL_PATTERN and GOON_URL_PATTERN.search(m.text or "")))
    & ~filters.regex(r"^/"),
    group=1,
)
async def goon_autodetect(client: Client, message: Message):
    await _resolve_and_show_qualities(client, message, message.text.strip())


@Client.on_callback_query(filters.regex(r"^goonq:"))
async def goon_quality_callback(client: Client, callback_query: CallbackQuery):
    try:
        _, token, choice = callback_query.data.split(":", 2)
    except ValueError:
        return await callback_query.answer("Invalid selection.", show_alert=True)

    if choice == "cancel":
        _CACHE.pop(token, None)
        await callback_query.answer("Cancelled.")
        return await safe_edit(callback_query.message.edit_text,
            f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)

    entry = _load(token)
    if not entry:
        await callback_query.answer("This request expired, please send the link again.", show_alert=True)
        return await safe_edit(callback_query.message.edit_text,
            f"<b>{E_CROSS} This request expired, please send the link again.</b>", parse_mode=enums.ParseMode.HTML)

    await callback_query.answer(f"Downloading {choice}...")
    await _download_and_upload(client, callback_query.message, token, choice)
