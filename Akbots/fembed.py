"""
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None
Fembed / embed-mirror host downloader.
Ported from Url-uploader-Bot-V4 (plugins/fembed.py + plugins/extra.py's
dl_fembed handler), rewritten to fit this bot's plugin style and reuse
Akbots/direct_utils.py instead of the old bot's custom progress/thumbnail code.

Fembed-family sites are "embed" video hosts (used by streaming/mirror sites
to embed a player) that serve a handful of direct-file mirrors at different
qualities for a single video id. Unlike every other plugin in this bot
(which either owns one known file host, or hands off to yt-dlp), these
sites need a dedicated scraper (the `lk21` library) to pull out the list of
downloadable mirror links, then a quality picker before the actual
download+upload — closer in spirit to Akbots/quality_selector.py than to
a plain single-link plugin like mediafire.py.
"""

import re
import string
import random
import asyncio

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    E_CHECK, E_CROSS, E_INFO
)
from Akbots.link_cache import try_send_cached

try:
    from lk21 import Bypass
except ImportError:
    Bypass = None

PATTERN = re.compile(
    r"(https?://)?(www\.)?"
    r"(fembed\.com|fembed-hd\.com|femax20\.com|vanfem\.com|suzihaza\.com|"
    r"embedsito\.com|owodeuwu\.xyz|plusto\.link|watchse\.icu|feurl\.com)"
    r"/\S+",
    re.IGNORECASE,
)

# session_key -> {"mirrors": [{"label": str, "url": str}, ...], "title": str}
_SESSIONS = {}


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


def _normalize(url: str) -> str:
    # The old bot always rewrote whatever fembed-family link it saw to a
    # canonical fembed.com/f/<id> URL before bypassing, since lk21's bypasser
    # expects that path shape. Keep the same behaviour.
    return "https://fembed.com/f/" + url.rstrip("/").split("/")[-1]


def _bypass(url: str):
    """Blocking call — must be run in an executor."""
    if Bypass is None:
        raise RuntimeError("The 'lk21' package is required for fembed links (pip install lk21).")
    bypasser = Bypass()
    items = bypasser.bypass_url(_normalize(url))
    mirrors = []
    for item in items:
        key = item.get("key", "")
        quality, _, ext = key.partition("/")
        mirrors.append({
            "label": quality or key,
            "ext": ext or "mp4",
            "url": item["value"],
        })
    return mirrors


async def _show_picker(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} Fembed link detected, fetching mirrors...</b>",
        parse_mode=enums.ParseMode.HTML
    )
    if await try_send_cached(client, message, url, status):
        return
    try:
        loop = asyncio.get_event_loop()
        mirrors = await loop.run_in_executor(None, _bypass, url)
        if not mirrors:
            raise ValueError("No downloadable mirrors found for this link.")
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    key = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
    _SESSIONS[key] = {"mirrors": mirrors, "page_url": url}

    buttons = [
        [make_button(f"🎥 {m['label']} ({m['ext']})", callback_data=f"fmb:{key}:{i}", style=_BS.PRIMARY if _BS else None)]
        for i, m in enumerate(mirrors)
    ]
    buttons.append([make_button("❌ Cancel", callback_data=f"fmb:{key}:cancel", style=_BS.DANGER if _BS else None)])

    await status.edit_text(
        f"<b>{E_CHECK} Choose a mirror to download:</b>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def fembed_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _show_picker(client, message, url)


@Client.on_message(filters.command("fembed") & filters.private)
async def fembed_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/fembed &lt;fembed-family link&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    raw = message.command[1]
    url = extract_url(raw) or raw
    await _show_picker(client, message, url)


@Client.on_callback_query(filters.regex(r"^fmb:"))
async def fembed_callback(client: Client, callback_query: CallbackQuery):
    try:
        _, key, choice = callback_query.data.split(":", 2)
    except ValueError:
        return await callback_query.answer("Invalid selection", show_alert=True)

    session = _SESSIONS.get(key)
    if not session:
        return await callback_query.answer("This session expired, send the link again.", show_alert=True)

    if choice == "cancel":
        _SESSIONS.pop(key, None)
        await callback_query.answer("Cancelled")
        return await callback_query.message.delete()

    try:
        idx = int(choice)
        mirror = session["mirrors"][idx]
    except (ValueError, IndexError):
        return await callback_query.answer("Invalid selection", show_alert=True)

    await callback_query.answer()
    status = callback_query.message
    message = status.reply_to_message or status

    try:
        await status.edit_text(f"<b>{E_INFO} Downloading mirror...</b>", parse_mode=enums.ParseMode.HTML)
        filename = safe_filename(f"fembed_{mirror['label']}.{mirror['ext']}", "fembed_file")
        folder = make_output_folder("fembed")
        dest = f"{folder}/{status.id}_{filename}"
        await stream_download(mirror["url"], dest, status, "Downloading from Fembed mirror", user_id=callback_query.from_user.id, file_name=filename)
        await upload_file(client, message, dest, status, f"<b>{E_CHECK} Fembed File</b>\n<code>{filename}</code>", file_name=filename, cache_url=session.get("page_url"))
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        _SESSIONS.pop(key, None)
