# Akbots - Don't Remove Credit - @AkBots_Official
#
# YouTube Upload — /ytupload, /setytoken
#
#   /setytoken (admin) — upload a token.pickle generated with the
#       youtube.upload OAuth scope (separate from the Drive token — see
#       gdrive_oauth_setup.py for the pattern, but request scope
#       "https://www.googleapis.com/auth/youtube.upload" instead).
#
#   /ytupload Title | description | public|unlisted|private
#       (reply to a video) — downloads it from Telegram and uploads it to
#       the authenticated YouTube channel via the Data API v3 resumable
#       upload, in a background thread so the event loop stays responsive.
#       Only title is required; description and privacy default to empty
#       and "unlisted".

import os
import shutil
import pickle
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from config import YOUTUBE_TOKEN_PATH, ADMINS
from Akbots.direct_utils import make_output_folder, safe_filename, VIDEO_EXTS, safe_edit, make_download_progress

try:
    from googleapiclient.discovery import build as _gapi_build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError as _GApiHttpError
except ImportError:
    _gapi_build = None
    MediaFileUpload = None
    _GApiHttpError = Exception

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN  = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_INFO  = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'
E_GEAR  = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
E_YT    = '▶️'

_pending_token_upload: set[int] = set()
_service = None


def _oauth_available() -> bool:
    return _gapi_build is not None and os.path.exists(YOUTUBE_TOKEN_PATH)


async def _get_service():
    global _service
    if _service is not None:
        return _service
    if not _oauth_available():
        return None

    def _build():
        with open(YOUTUBE_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
        return _gapi_build("youtube", "v3", credentials=creds)

    try:
        _service = await asyncio.to_thread(_build)
        return _service
    except Exception:
        return None


def _upload_sync(service, path, title, description, privacy):
    body = {
        "snippet": {"title": title[:100], "description": description[:5000]},
        "status": {"privacyStatus": privacy},
    }
    media = MediaFileUpload(path, chunksize=-1, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    return response


@Client.on_message(filters.command("setytoken") & filters.private)
async def setytoken_cmd(client: Client, message: Message):
    if message.from_user.id not in ADMINS:
        return
    if message.document:
        return await _save_token(message)
    _pending_token_upload.add(message.from_user.id)
    await message.reply_text(
        f"<b>{E_INFO} Send the <code>token.pickle</code> file now.</b>\n"
        f"<i>Generate it locally with the youtube.upload OAuth scope.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def _save_token(message: Message):
    global _service
    os.makedirs(os.path.dirname(YOUTUBE_TOKEN_PATH) or ".", exist_ok=True)
    try:
        await message.download(file_name=YOUTUBE_TOKEN_PATH)
    except Exception as e:
        return await message.reply_text(f"<b>{E_CROSS} Failed to save:</b> <code>{e}</code>",
                                         parse_mode=enums.ParseMode.HTML)
    _service = None
    await message.reply_text(f"<b>{E_CHECK} YouTube token saved. /ytupload is ready.</b>",
                              parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.document & filters.create(
    lambda _, __, m: bool(m.document and (m.document.file_name or "").endswith(".pickle"))
))
async def ytoken_receive(client: Client, message: Message):
    if message.from_user.id in _pending_token_upload:
        _pending_token_upload.discard(message.from_user.id)
        await _save_token(message)


@Client.on_message(filters.command("ytupload") & filters.private)
async def ytupload_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if _gapi_build is None:
        return await message.reply_text(f"<b>{E_CROSS} google-api-python-client isn't installed.</b>",
                                         parse_mode=enums.ParseMode.HTML)
    if not _oauth_available():
        return await message.reply_text(
            f"<b>{E_WARN} YouTube upload isn't set up yet.</b> An admin needs to run "
            f"<code>/setytoken</code> first.", parse_mode=enums.ParseMode.HTML)

    replied = message.reply_to_message
    is_video = replied and (replied.video or (replied.document and
               (replied.document.file_name or "").lower().endswith(VIDEO_EXTS)))
    if not is_video or len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_INFO} Reply to a <b>ᴠɪᴅᴇᴏ</b> with:\n"
            f"<code>/ytupload Title | description | public|unlisted|private</code>\n\n"
            f"Only the title is required.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    raw = message.text.split(None, 1)[1]
    parts = [p.strip() for p in raw.split("|")]
    title = parts[0] or "Uploaded via Akbots"
    description = parts[1] if len(parts) > 1 else ""
    privacy = parts[2].lower() if len(parts) > 2 else "unlisted"
    if privacy not in ("public", "unlisted", "private"):
        privacy = "unlisted"

    media = replied.video or replied.document
    orig_name = getattr(media, "file_name", None) or f"video_{replied.id}.mp4"
    orig_name = safe_filename(orig_name, f"video_{replied.id}.mp4")

    status = await message.reply_text(f"<b>{E_GEAR} Downloading from Telegram...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("ytupload"), f"{user_id}_{replied.id}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    local_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=local_path,
                                     progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_YT} Uploading to YouTube ({privacy})...</b>", parse_mode=enums.ParseMode.HTML)

    service = await _get_service()
    try:
        response = await asyncio.to_thread(_upload_sync, service, local_path, title, description, privacy)
    except _GApiHttpError as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} YouTube API error:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Upload failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    shutil.rmtree(temp_dir, ignore_errors=True)
    video_id = response.get("id") if response else None
    if not video_id:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Upload finished but no video ID returned.</b>",
                                       parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, 
        f"<b>{E_CHECK} Uploaded to YouTube</b>\n\n<b>ᴛɪᴛʟᴇ:</b> {title}\n"
        f"<b>ᴘʀɪᴠᴀᴄʏ:</b> {privacy}\n<b>ʟɪɴᴋ:</b> https://youtu.be/{video_id}",
        parse_mode=enums.ParseMode.HTML,
    )
