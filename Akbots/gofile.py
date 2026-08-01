import re
import time
import uuid
import hashlib
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import GOFILE_TOKEN
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

PATTERN = re.compile(r"(https?://)?(www\.)?gofile\.io/d/\S+", re.IGNORECASE)
GOFILE_API = "https://api.gofile.io"

MAX_GOFILE_FILES = 150   # hard cap so a huge/abused folder tree can't run away
MAX_GOFILE_DEPTH = 6     # how many levels of subfolders to descend into

# GoFile's /contents endpoint (and /accounts) always require an
# "X-Website-Token" + "X-BL" header pair now, whether the request carries a
# real account token or not. This used to be scraped out of
# gofile.io/dist/js/config.js, but that value moved — it's actually a
# client-computed SHA-256 the site's own JS derives on the fly, salted with
# the User-Agent, a fixed "en-US" locale, the account token (if any), and a
# 4-hour time slot. Computing it locally (same formula GoFile's JS uses) is
# what a known-working reference downloader does, and is why the old
# scrape-based version was silently failing on both single files AND
# folders — every /contents call was getting rejected before a single file
# was ever listed.
_WEBSITE_TOKEN_SALT = "9844d94d963d30"


def _website_token(account_token: str = "") -> str:
    user_agent = DEFAULT_HEADERS.get("User-Agent", "Mozilla/5.0")
    time_slot = int(time.time()) // 14400
    raw = f"{user_agent}::en-US::{account_token}::{time_slot}::{_WEBSITE_TOKEN_SALT}"
    return hashlib.sha256(raw.encode()).hexdigest()


# Guest (temporary, tokenless) account — created on demand the first time
# a request is made without GOFILE_TOKEN configured, and reused after that.
# This is what makes /gofile work out of the box even with no setup.
_guest_token_cache = {"token": None}

# session_id -> {"files": [content_dict, ...], "chat_id", "message"}
_FOLDER_SESSIONS = {}


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


async def _get_account_token(session: aiohttp.ClientSession) -> str:
    """Real account token if GOFILE_TOKEN is configured, otherwise a
    throwaway guest token created on the fly (guest accounts are how
    gofile.io itself treats anonymous visitors browsing a public link)."""
    if GOFILE_TOKEN:
        return GOFILE_TOKEN
    if _guest_token_cache["token"]:
        return _guest_token_cache["token"]
    headers = {**DEFAULT_HEADERS, "X-Website-Token": _website_token(""), "X-BL": "en-US"}
    async with session.post(f"{GOFILE_API}/accounts", headers=headers) as resp:
        resp.raise_for_status()
        data = await resp.json()
    token = (data.get("data") or {}).get("token")
    if not token:
        raise ValueError("Could not create a GoFile guest session.")
    _guest_token_cache["token"] = token
    return token


async def _get_contents(session: aiohttp.ClientSession, content_id: str, account_token: str) -> dict:
    """Fetches one content node — this is a single file if the link points
    straight at a file, or a folder (with a 'children' map) if it points at
    a directory; GoFile uses the same /d/<id> URL shape for both, so which
    one it is only becomes known from this response."""
    endpoint = f"/contents/{content_id}?cache=true&sortField=createTime&sortDirection=1"
    headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {account_token}",
        "X-Website-Token": _website_token(account_token),
        "X-BL": "en-US",
    }
    async with session.get(f"{GOFILE_API}{endpoint}", headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _collect_files(session: aiohttp.ClientSession, content_id: str, account_token: str,
                          depth: int = 0, _out=None):
    """Recursively walks a GoFile content ID — a single /d/<id> link can be
    either one file or a whole folder (unlike MediaFire, GoFile doesn't use
    a separate URL shape for folders), and a folder can itself contain
    subfolders. Flattens everything into a list of file-content dicts,
    same idea as mediafire.py's folder support."""
    if _out is None:
        _out = []
    if depth > MAX_GOFILE_DEPTH or len(_out) >= MAX_GOFILE_FILES:
        return _out

    data = await _get_contents(session, content_id, account_token)
    if data.get("status") != "ok":
        if depth == 0:
            raise ValueError(_status_error_message(data.get("status", "unknown")))
        return _out  # a broken/deleted subfolder shouldn't kill the whole batch

    node = data["data"]
    children = node.get("children") or node.get("files") or {}
    child_list = list(children.values()) if isinstance(children, dict) else (children or [])

    if not child_list:
        # No children at all means this content ID IS a single file, not a
        # folder — the root call falls into this case for a plain file link.
        if node.get("type", "file") == "file" or node.get("link") or node.get("directLink"):
            _out.append(node)
        return _out

    for child in child_list:
        if len(_out) >= MAX_GOFILE_FILES:
            break
        if child.get("type") == "folder":
            sub_id = child.get("id") or child.get("code")
            if sub_id:
                await _collect_files(session, sub_id, account_token, depth=depth + 1, _out=_out)
        else:
            _out.append(child)

    return _out


def _status_error_message(status_code: str) -> str:
    if status_code == "error-notFound":
        return "This GoFile link doesn't exist or has expired."
    if status_code == "error-passwordRequired":
        return "This GoFile link is password-protected — not supported yet."
    return f"GoFile API error: {status_code}"


def _folder_confirm_kb(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        make_button("✅ Download all", callback_data=f"gofold#{session_id}", style=_BS.SUCCESS if _BS else None),
        make_button("❌ Cancel", callback_data=f"gofoldcancel#{session_id}", style=_BS.DANGER if _BS else None),
    ]])


async def _download_one(client: Client, message: Message, status: Message, info: dict, account_token: str,
                         idx: int, total: int, cache_url):
    filename = safe_filename(info.get("name"), f"gofile_{idx}")
    direct_url = info.get("link") or info.get("directLink")
    if not direct_url:
        return False
    folder = make_output_folder("gofile")
    dest = f"{folder}/{message.id}_{idx}_{filename}"
    label = f"Downloading from GoFile ({idx}/{total})" if total > 1 else "Downloading from GoFile"
    dl_headers = {**DEFAULT_HEADERS, "Cookie": f"accountToken={account_token}"}
    try:
        await stream_download(direct_url, dest, status, label, headers=dl_headers, user_id=message.from_user.id, file_name=filename)
        await upload_file(client, message, dest, status, f"<b>{E_CHECK} GoFile</b>\n<code>{filename}</code>", file_name=filename, cache_url=cache_url)
        return True
    except Exception as e:
        await message.reply_text(f"<b>{E_CROSS} Failed:</b> {filename}\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        return False


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} GoFile link detected...</b>", parse_mode=enums.ParseMode.HTML)
    if await try_send_cached(client, message, url, status):
        return
    content_id = url.split("/d/")[-1].split("?")[0].strip("/")

    try:
        async with aiohttp.ClientSession() as session:
            account_token = await _get_account_token(session)
            files = await _collect_files(session, content_id, account_token)

        if not files:
            raise ValueError("No files found in this GoFile link.")

        if len(files) == 1:
            await safe_edit(status.edit_text, f"<b>{E_INFO} Downloading...</b>", parse_mode=enums.ParseMode.HTML)
            await _download_one(client, message, status, files[0], account_token, 1, 1, cache_url=url)
            return

        session_id = uuid.uuid4().hex[:10]
        _FOLDER_SESSIONS[session_id] = {"files": files, "account_token": account_token, "message": message}
        capped_note = f" (capped at {MAX_GOFILE_FILES})" if len(files) >= MAX_GOFILE_FILES else ""
        await safe_edit(status.edit_text, 
            f"<b>{E_ROCKET} Found {len(files)} file(s){capped_note} in this GoFile folder.</b>\n"
            f"<i>Each will be downloaded and sent one by one.</i>",
            reply_markup=_folder_confirm_kb(session_id),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^gofold#"))
async def gofile_folder_go_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    session = _FOLDER_SESSIONS.pop(session_id, None)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the link again.</b>", parse_mode=enums.ParseMode.HTML)

    status = callback_query.message
    files = session["files"]
    account_token = session["account_token"]
    message = session["message"]
    total = len(files)
    done = failed = 0
    for idx, info in enumerate(files, start=1):
        await safe_edit(status.edit_text, 
            f"<b>{E_INFO} File {idx}/{total}:</b> {info.get('name', '?')}\n✅ {done}   ❌ {failed}",
            parse_mode=enums.ParseMode.HTML,
        )
        ok = await _download_one(client, message, status, info, account_token, idx, total, cache_url=None)
        done += 1 if ok else 0
        failed += 0 if ok else 1

    await safe_edit(status.edit_text, 
        f"<b>{E_CHECK} Done — {done}/{total} file(s) sent</b>" + (f", {failed} failed." if failed else "."),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^gofoldcancel#"))
async def gofile_folder_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _FOLDER_SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def gofile_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("gofile") & filters.private)
async def gofile_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/gofile &lt;gofile.io URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)
