import re
import uuid
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    DEFAULT_HEADERS, E_CHECK, E_CROSS, E_INFO, E_ROCKET
)
from Akbots.link_cache import try_send_cached
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

PATTERN = re.compile(r"(https?://)?(www\.)?mediafire\.com/\S+", re.IGNORECASE)
# /folder/<key>/... is a completely different page type from /file/<key>/...
# — folder pages render their listing entirely client-side via JS, so the
# #downloadButton scraper below finds nothing on them. This needs MediaFire's
# folder API instead (see _fetch_folder_files).
FOLDER_PATTERN = re.compile(r"mediafire\.com/folder/([a-zA-Z0-9]+)", re.IGNORECASE)

FOLDER_API = "https://www.mediafire.com/api/1.5/folder/get_content.php"
MAX_FOLDER_FILES = 150   # hard cap so a huge/abused folder can't run away
MAX_FOLDER_DEPTH = 4     # how many levels of subfolders to descend into

# session_id -> {"files": [(quickkey, filename), ...], "chat_id", "message"}
_FOLDER_SESSIONS = {}


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


def _folder_key(url: str):
    m = FOLDER_PATTERN.search(url)
    return m.group(1) if m else None


def _parse_with_bs4(html: str):
    """Precise element-based parse (matches the old bot's approach): targets
    the exact #downloadButton anchor instead of pattern-matching the raw
    HTML, so it isn't thrown off if some other mediafire.com link/host
    happens to appear elsewhere on the page."""
    soup = BeautifulSoup(html, "html.parser")
    btn = soup.find("a", id="downloadButton")
    if not btn or not btn.get("href"):
        return None, None

    direct_url = btn["href"]
    filename = None
    name_el = soup.find("div", class_="filename")
    if name_el:
        filename = name_el.get_text(strip=True)
    else:
        label_el = soup.find("div", class_="dl-btn-label")
        if label_el and label_el.get("title"):
            filename = label_el["title"]
    return direct_url, filename


def _parse_with_regex(html: str):
    m = re.search(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"', html)
    if not m:
        return None, None
    direct_url = m.group(1)
    name_m = re.search(r'<div class="filename"[^>]*>([^<]+)</div>', html)
    filename = name_m.group(1).strip() if name_m else None
    return direct_url, filename


async def _extract_direct_url(link: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(link, headers=DEFAULT_HEADERS) as resp:
            if resp.status != 200:
                raise ValueError(f"Could not open Mediafire page (HTTP {resp.status})")
            html = await resp.text()

    direct_url = filename = None
    if BeautifulSoup is not None:
        direct_url, filename = _parse_with_bs4(html)
    if not direct_url:
        direct_url, filename = _parse_with_regex(html)
    if not direct_url:
        raise ValueError("Could not find Mediafire download link. Link may be dead or restricted.")

    if not filename:
        filename = direct_url.split("/")[-1].split("?")[0]
    return direct_url, filename


async def _folder_api_call(session: aiohttp.ClientSession, content_type: str, folder_key: str):
    params = {
        "content_type": content_type, "filter": "all", "order_by": "name",
        "order_direction": "asc", "chunk": 1, "version": "1.5",
        "folder_key": folder_key, "response_format": "json",
    }
    async with session.get(FOLDER_API, params=params, headers=DEFAULT_HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
        if r.status != 200:
            return None
        data = await r.json(content_type=None)
    resp = (data or {}).get("response", {})
    if resp.get("result") != "Success":
        return None
    return resp


async def _fetch_folder_files(folder_key: str, depth: int = 0, _out=None):
    """Recursively walks a public MediaFire folder (and its subfolders) and
    returns a flat [(quickkey, filename), ...] list, using MediaFire's own
    folder API — no session/login needed for public folders."""
    if _out is None:
        _out = []
    if depth > MAX_FOLDER_DEPTH or len(_out) >= MAX_FOLDER_FILES:
        return _out

    async with aiohttp.ClientSession() as session:
        files_resp = await _folder_api_call(session, "files", folder_key)
        if files_resp:
            for f in files_resp.get("folder_content", {}).get("files", []):
                if len(_out) >= MAX_FOLDER_FILES:
                    break
                qk, name = f.get("quickkey"), f.get("filename")
                if qk and name:
                    _out.append((qk, name))

        if len(_out) < MAX_FOLDER_FILES:
            folders_resp = await _folder_api_call(session, "folders", folder_key)
            if folders_resp:
                for sub in folders_resp.get("folder_content", {}).get("folders", []):
                    sub_key = sub.get("folderkey")
                    if sub_key and len(_out) < MAX_FOLDER_FILES:
                        await _fetch_folder_files(sub_key, depth=depth + 1, _out=_out)

    return _out


def _folder_confirm_kb(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        make_button("✅ ᴅᴏᴡɴʟᴏᴀᴅ ᴀʟʟ", callback_data=f"mffold#{session_id}", style=_BS.SUCCESS if _BS else None),
        make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"mffoldcancel#{session_id}", style=_BS.DANGER if _BS else None),
    ]])


async def _handle_folder(client: Client, message: Message, url: str):
    folder_key = _folder_key(url)
    if not folder_key:
        return await message.reply_text(f"<b>{E_CROSS} Couldn't read that folder link.</b>", parse_mode=enums.ParseMode.HTML)

    status = await message.reply_text(f"<b>{E_INFO} Reading folder contents...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        files = await _fetch_folder_files(folder_key)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to read folder:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not files:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} No files found.</b>\n"
            f"<i>The folder may be empty, private, or password-protected.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    session_id = uuid.uuid4().hex[:10]
    _FOLDER_SESSIONS[session_id] = {"files": files, "chat_id": message.chat.id, "message": message}
    capped_note = f" (capped at {MAX_FOLDER_FILES})" if len(files) >= MAX_FOLDER_FILES else ""
    await safe_edit(status.edit_text, 
        f"<b>{E_ROCKET} Found {len(files)} file(s){capped_note} in this folder.</b>\n"
        f"<i>Each will be downloaded and sent one by one.</i>",
        reply_markup=_folder_confirm_kb(session_id),
        parse_mode=enums.ParseMode.HTML,
    )


async def _download_one_file(client: Client, message: Message, status: Message, quickkey: str, hint_name: str):
    share_url = f"https://www.mediafire.com/file/{quickkey}/file"
    if await try_send_cached(client, message, share_url, status, delete_status=False):
        return True
    try:
        direct_url, filename = await _extract_direct_url(share_url)
        filename = safe_filename(filename or hint_name, hint_name or "mediafire_file")
        folder = make_output_folder("mediafire")
        dest = f"{folder}/{message.id}_{quickkey}_{filename}"
        await stream_download(direct_url, dest, status, f"Downloading {filename}", user_id=message.from_user.id, file_name=filename)
        # delete_status=False: this status message is reused across every
        # file in the folder loop — deleting it after file 1 would crash
        # the next status.edit_text() call and silently kill the batch.
        await upload_file(client, message, dest, status, f"<b>{E_CHECK} Mediafire File</b>\n<code>{filename}</code>", file_name=filename, cache_url=share_url, delete_status=False)
        return True
    except Exception as e:
        await message.reply_text(f"<b>{E_CROSS} Failed:</b> {hint_name}\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        return False


@Client.on_callback_query(filters.regex(r"^mffold#"))
async def mediafire_folder_go_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    session = _FOLDER_SESSIONS.pop(session_id, None)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the folder link again.</b>", parse_mode=enums.ParseMode.HTML)

    status = callback_query.message
    files = session["files"]
    total = len(files)
    done = failed = 0
    for i, (quickkey, filename) in enumerate(files, start=1):
        try:
            await safe_edit(status.edit_text, 
                f"<b>{E_INFO} File {i}/{total}:</b> {filename}\n✅ {done}   ❌ {failed}",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            # Status message got deleted/edited-away somehow (rate limit,
            # manual deletion, etc.) — don't let that abort the rest of
            # the batch, just skip the progress update for this file.
            pass
        ok = await _download_one_file(client, session["message"], status, quickkey, filename)
        done += 1 if ok else 0
        failed += 0 if ok else 1

    try:
        await safe_edit(status.edit_text, 
            f"<b>{E_CHECK} Done — {done}/{total} file(s) sent</b>" + (f", {failed} failed." if failed else "."),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        await session["message"].reply_text(
            f"<b>{E_CHECK} Done — {done}/{total} file(s) sent</b>" + (f", {failed} failed." if failed else "."),
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_callback_query(filters.regex(r"^mffoldcancel#"))
async def mediafire_folder_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _FOLDER_SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)


async def _handle(client: Client, message: Message, url: str):
    if FOLDER_PATTERN.search(url):
        return await _handle_folder(client, message, url)

    status = await message.reply_text(f"<b>{E_INFO} Mediafire link detected...</b>", parse_mode=enums.ParseMode.HTML)
    if await try_send_cached(client, message, url, status):
        return
    try:
        direct_url, filename = await _extract_direct_url(url)
        filename = safe_filename(filename, "mediafire_file")
        folder = make_output_folder("mediafire")
        dest = f"{folder}/{message.id}_{filename}"
        await stream_download(direct_url, dest, status, "Downloading from Mediafire", user_id=message.from_user.id, file_name=filename)
        await upload_file(client, message, dest, status, f"<b>{E_CHECK} Mediafire File</b>\n<code>{filename}</code>", file_name=filename, cache_url=url)
    except Exception as e:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def mediafire_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("mf") & filters.private)
async def mediafire_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/mf &lt;mediafire URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)
