import os
import re
import io
import pickle
import asyncio
import aiohttp
import contextlib
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    format_progress, format_media_caption, E_CHECK, E_CROSS, E_INFO
)
from config import GDRIVE_TOKEN_PATH
from Akbots.link_cache import try_send_cached
from Akbots.direct_utils import safe_edit

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from natsort import natsorted
except ImportError:
    natsorted = None

try:
    from googleapiclient.discovery import build as _gapi_build
    from googleapiclient.errors import HttpError as _GApiHttpError
    from googleapiclient.http import MediaIoBaseDownload
except ImportError:
    _gapi_build = None
    _GApiHttpError = Exception
    MediaIoBaseDownload = None

# Public-link-only mode (yt-dlp / scraping) always works with zero setup.
# If an admin uploads a token.pickle via /setgdrivetoken, folder downloads
# and private files also become available through the real Drive API.
PATTERN = re.compile(r"(https?://)?(www\.)?drive\.google\.com/\S+", re.IGNORECASE)

ID_PATTERN = re.compile(r"/d/([-\w]+)|/folders/([-\w]+)|[?&]id=([-\w]+)")
_GOOGLE_APPS_PREFIX = "application/vnd.google-apps"
_MAX_FOLDER_DEPTH = 50

_service = None  # cached googleapiclient Drive service, built lazily


@contextlib.asynccontextmanager
async def _tracked(user_id: int, label: str):
    """Registers the currently-running task with task_manager for the
    duration of the `with` block, so it shows up in /queue and can be
    stopped via /cancel_all — same registry stream_download() uses
    internally. Needed here because the OAuth Drive-API download (both
    single-file and folder) and the yt-dlp-based Drive download never go
    through stream_download, so without this they were invisible to
    /cancel_all entirely (only the manual-scrape fallback path was ever
    covered, since that one alone calls stream_download with user_id=)."""
    task_id = None
    try:
        from Akbots import task_manager
        task_id = task_manager.register(user_id, asyncio.current_task(), label)
    except Exception:
        task_id = None
    try:
        yield
    finally:
        if task_id is not None:
            from Akbots import task_manager
            task_manager.unregister(user_id, task_id)


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


def _extract_id(link: str):
    m = ID_PATTERN.search(link)
    if not m:
        return None
    return m.group(1) or m.group(2) or m.group(3)


def _is_folder_link(url: str) -> bool:
    return "/folders/" in url


def _is_google_apps(mime_type: str) -> bool:
    return (mime_type or "").startswith(_GOOGLE_APPS_PREFIX)


def _oauth_available() -> bool:
    return _gapi_build is not None and os.path.exists(GDRIVE_TOKEN_PATH)


async def _get_service():
    """Lazily builds (and caches) the Drive API service from token.pickle.
    Returns None if OAuth isn't set up — callers fall back to the existing
    public-link method."""
    global _service
    if _service is not None:
        return _service
    if not _oauth_available():
        return None

    def _build():
        with open(GDRIVE_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
        return _gapi_build("drive", "v3", credentials=creds)

    try:
        _service = await asyncio.to_thread(_build)
        return _service
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OAuth API path: folders + private files (needs /setgdrivetoken uploaded).
# ---------------------------------------------------------------------------

def _get_file_metadata_sync(service, file_id: str) -> dict:
    return service.files().get(
        fileId=file_id, supportsAllDrives=True,
        fields="name, id, mimeType, size"
    ).execute()


def _get_files_in_folder_sync(service, folder_id: str) -> list:
    files, page_token = [], None
    while True:
        resp = service.files().list(
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            q=f"'{folder_id}' in parents and trashed = false",
            spaces="drive", pageSize=200,
            fields="nextPageToken, files(id, name, mimeType, size, shortcutDetails)",
            orderBy="folder, name", pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if page_token is None:
            return files


async def _oauth_download_file(client: Client, message: Message, status, service,
                                file_id: str, dest_folder: str, label_prefix: str = "",
                                cache_url: str = None):
    """Downloads one file via the Drive API (chunked, with progress) then
    uploads it to Telegram. Used for both single-file and folder-member
    downloads."""
    meta = await asyncio.to_thread(_get_file_metadata_sync, service, file_id)
    if _is_google_apps(meta.get("mimeType", "")):
        return  # Docs/Sheets/Slides can't be downloaded directly — skip

    filename = safe_filename(meta.get("name"), f"gdrive_{file_id}")
    dest = f"{dest_folder}/{message.id}_{filename}"
    total_size = int(meta.get("size", 0) or 0)

    loop = asyncio.get_running_loop()
    start = loop.time()
    last_edit = 0.0

    def _run_download():
        nonlocal last_edit
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(dest, "wb") as f:
            downloader = MediaIoBaseDownload(f, request, chunksize=50 * 1024 * 1024)
            done = False
            while not done:
                dl_status, done = downloader.next_chunk()
                now = loop.time()
                if dl_status and now - last_edit >= 3:
                    last_edit = now
                    downloaded = dl_status.resumable_progress
                    elapsed = now - start
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    pct = (downloaded / total_size * 100) if total_size else 0
                    eta = ((total_size - downloaded) / speed) if (total_size and speed > 0) else None
                    async def _edit():
                        try:
                            await safe_edit(status.edit_text, 
                                format_progress(pct, speed_bps=speed, done_bytes=downloaded,
                                                 total_bytes=total_size or None, elapsed_secs=elapsed,
                                                 eta_secs=eta, title=f"{label_prefix}Downloading from Google Drive",
                                                 file_name=filename),
                                parse_mode=enums.ParseMode.HTML
                            )
                        except Exception:
                            pass  # e.g. "message not modified" — safe to ignore
                    asyncio.run_coroutine_threadsafe(_edit(), loop)

    await asyncio.to_thread(_run_download)
    await upload_file(client, message, dest, status, f"<b>{E_CHECK} Google Drive File</b>\n<code>{filename}</code>\n\nDownloaded by @anujedits76", file_name=filename, cache_url=cache_url)


async def _oauth_download_folder(client: Client, message: Message, status, service,
                                  folder_id: str, dest_folder: str, depth: int = 0):
    if depth > _MAX_FOLDER_DEPTH:
        return
    items = await asyncio.to_thread(_get_files_in_folder_sync, service, folder_id)
    if not items:
        return
    if natsorted is not None:
        items = natsorted(items, key=lambda k: k.get("name", ""))

    total = len(items)
    for idx, item in enumerate(items, 1):
        file_id = item["id"]
        mime_type = item.get("mimeType", "")
        shortcut = item.get("shortcutDetails")
        if shortcut:
            file_id = shortcut.get("targetId", file_id)
            mime_type = shortcut.get("targetMimeType", mime_type)

        if mime_type == "application/vnd.google-apps.folder":
            await _oauth_download_folder(client, message, status, service, file_id, dest_folder, depth + 1)
        elif _is_google_apps(mime_type):
            continue
        else:
            await _oauth_download_file(
                client, message, status, service, file_id, dest_folder,
                label_prefix=f"[{idx}/{total}] "
            )


# ---------------------------------------------------------------------------
# Primary method: yt-dlp's own Google Drive extractor.
# Google has changed the manual "uc?export=download&confirm=..." dance
# several times (virus-scan warning page, different confirm-token markup for
# large files, etc). Rather than re-scraping that HTML ourselves, hand the
# whole thing to yt-dlp — its GoogleDrive extractor is actively maintained
# against exactly these changes, so it's the more durable option.
# ---------------------------------------------------------------------------

def _ytdlp_download(url: str, out_dir: str, progress_hook=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": os.path.join(out_dir, "%(title).100s.%(ext)s"),
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        return path, info


async def _try_ytdlp(client: Client, message: Message, status, url: str):
    if yt_dlp is None:
        return False
    await safe_edit(status.edit_text, f"<b>{E_INFO} Fetching from Google Drive...</b>", parse_mode=enums.ParseMode.HTML)
    folder = make_output_folder("gdrive")
    try:
        from Akbots.ytdl import _make_download_progress_hook
        loop = asyncio.get_running_loop()
        hook = _make_download_progress_hook(status, loop)
    except Exception:
        hook = None
    try:
        path, info = await asyncio.to_thread(_ytdlp_download, url, folder, hook)
    except Exception:
        return False  # fall through to the manual scrape method below

    if not path or not os.path.exists(path):
        return False

    filename = safe_filename(os.path.basename(path), "gdrive_file")
    caption = format_media_caption(info, credit="anujedits76")
    if not caption or caption.strip() == "Downloaded by @anujedits76":
        # info had nothing usable (Drive rarely exposes uploader/views/etc.) —
        # fall back to the plain filename caption, still with credit.
        caption = f"<b>{E_CHECK} Google Drive File</b>\n<code>{filename}</code>\n\nDownloaded by @anujedits76"
    await upload_file(client, message, path, status, caption, file_name=filename, cache_url=url)
    return True


# ---------------------------------------------------------------------------
# Fallback method: manual scrape of the uc?export=download confirm page.
# Kept as a backup for whatever yt-dlp's extractor doesn't (yet) handle.
# ---------------------------------------------------------------------------

async def _resolve_direct_url(file_id: str):
    base = "https://drive.google.com/uc?export=download"
    filename = None
    async with aiohttp.ClientSession() as session:
        async with session.get(base, params={"id": file_id}) as resp:
            html = await resp.text()
            name_m = re.search(r'"(?:filename|title)"\s*:\s*"([^"]+)"', html) or re.search(r'<span[^>]*id="download-title"[^>]*>([^<]+)</span>', html)
            if name_m:
                filename = name_m.group(1)
            token_m = re.search(r'confirm=([0-9A-Za-z_-]+)', html) or re.search(r'name="confirm"\s+value="([^"]+)"', html)
            if token_m:
                return f"{base}&confirm={token_m.group(1)}&id={file_id}", filename
    return f"{base}&id={file_id}", filename


async def _fallback_manual(client: Client, message: Message, status, file_id: str, cache_url: str = None):
    direct_url, drive_filename = await _resolve_direct_url(file_id)
    filename = safe_filename(drive_filename, f"gdrive_{file_id}")
    folder = make_output_folder("gdrive")
    dest = f"{folder}/{message.id}_{filename}"
    await safe_edit(status.edit_text, f"<b>{E_INFO} Google Drive link detected (fallback method)...</b>", parse_mode=enums.ParseMode.HTML)
    await stream_download(direct_url, dest, status, "Downloading from Google Drive", user_id=message.from_user.id, file_name=filename)
    await upload_file(client, message, dest, status, f"<b>{E_CHECK} Google Drive File</b>\n<code>{filename}</code>\n\nDownloaded by @anujedits76", file_name=filename, cache_url=cache_url)


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} Google Drive link detected...</b>", parse_mode=enums.ParseMode.HTML)
    file_id = _extract_id(url)
    if not file_id:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Could not find a file ID in this link.</b>", parse_mode=enums.ParseMode.HTML)

    if not _is_folder_link(url) and await try_send_cached(client, message, url, status):
        return

    if _is_folder_link(url):
        service = await _get_service()
        if service is None:
            return await safe_edit(status.edit_text, 
                f"<b>{E_CROSS} Folder links need Google Drive API setup.</b>\n"
                f"<i>Ask an admin to run the OAuth setup and upload a token.pickle "
                f"via /setgdrivetoken — single public files work fine without it.</i>",
                parse_mode=enums.ParseMode.HTML
            )
        try:
            folder = make_output_folder("gdrive")
            await safe_edit(status.edit_text, f"<b>{E_INFO} Reading Google Drive folder contents...</b>", parse_mode=enums.ParseMode.HTML)
            async with _tracked(message.from_user.id, "Google Drive folder download"):
                await _oauth_download_folder(client, message, status, service, file_id, folder)
            await safe_edit(status.edit_text, f"<b>{E_CHECK} Folder download complete.</b>", parse_mode=enums.ParseMode.HTML)
        except _GApiHttpError as e:
            await safe_edit(status.edit_text, f"<b>{E_CROSS} Drive API error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await safe_edit(status.edit_text, f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        return

    try:
        async with _tracked(message.from_user.id, "Google Drive download"):
            if await _try_ytdlp(client, message, status, url):
                return
        await _fallback_manual(client, message, status, file_id, cache_url=url)
    except Exception as e:
        # Public methods failed — this is usually a private file. Try the
        # OAuth API as a last resort if it's set up.
        service = await _get_service()
        if service is not None:
            try:
                folder = make_output_folder("gdrive")
                await safe_edit(status.edit_text, f"<b>{E_INFO} Trying Google Drive API (private file)...</b>", parse_mode=enums.ParseMode.HTML)
                async with _tracked(message.from_user.id, "Google Drive private file download"):
                    await _oauth_download_file(client, message, status, service, file_id, folder, cache_url=url)
                return
            except Exception as e2:
                return await safe_edit(status.edit_text, 
                    f"<b>{E_CROSS} Error:</b>\n<code>{e2}</code>", parse_mode=enums.ParseMode.HTML
                )
        await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>\n"
            f"<i>File may be private — set up /setgdrivetoken for private-file access.</i>",
            parse_mode=enums.ParseMode.HTML
        )


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def gdrive_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("gdrive") & filters.private)
async def gdrive_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/gdrive &lt;drive.google.com URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)


# =============================================================================
# /setgdrivetoken - admin uploads the OAuth token.pickle (see
# gdrive_oauth_setup.py, run locally, for how to generate one)
# =============================================================================

_pending_token_upload: set[int] = set()


@Client.on_message(filters.command("setgdrivetoken") & filters.private)
async def setgdrivetoken_command(client: Client, message: Message):
    from config import ADMINS
    if message.from_user.id not in ADMINS:
        return
    if message.document:
        return await _save_gdrive_token(message)
    _pending_token_upload.add(message.from_user.id)
    await message.reply_text(
        f"<b>{E_INFO} Send the <code>token.pickle</code> file now.</b>\n"
        f"<i>Generate it locally with gdrive_oauth_setup.py first — see GDRIVE_SETUP.md.</i>",
        parse_mode=enums.ParseMode.HTML
    )


async def _save_gdrive_token(message: Message):
    global _service
    os.makedirs(os.path.dirname(GDRIVE_TOKEN_PATH) or ".", exist_ok=True)
    try:
        await message.download(file_name=GDRIVE_TOKEN_PATH)
    except Exception as e:
        return await message.reply_text(
            f"<b>{E_CROSS} Failed to save token:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )
    _service = None  # force rebuild with the new token on next use
    await message.reply_text(
        f"<b>{E_CHECK} Google Drive token saved.</b>\n"
        f"<i>Folder downloads and private files are now available via /gdrive.</i>",
        parse_mode=enums.ParseMode.HTML
    )


# group=-2 so this runs before rename.py's/cookies_manager.py's catch-all
# document handlers; only acts when /setgdrivetoken is actually pending.
@Client.on_message(filters.private & filters.document, group=-2)
async def setgdrivetoken_file_receive(client: Client, message: Message):
    from config import ADMINS
    user_id = message.from_user.id
    if user_id not in ADMINS or user_id not in _pending_token_upload:
        return
    _pending_token_upload.discard(user_id)
    await _save_gdrive_token(message)
    message.stop_propagation()
