# Akbots - Don't Remove Credit - @AkBots_Official
#
# Video + Audio Merger — /addaudio
#
#   Reply to a video with /addaudio, then send/reply the audio file when
#   asked. The audio track is muxed onto the video (video stream copied,
#   audio re-encoded to AAC for container compatibility). If the audio is
#   longer/shorter than the video, output length follows the shorter of
#   the two by default (-shortest) unless /addaudio long is used to keep
#   the longer track (video freezes on last frame / audio continues).
#
# Session-based like videomerge.py: /addaudio opens a short-lived session
# tied to the user, the next audio/video-as-document they send closes it.

import os
import shutil
import asyncio
import time
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, make_output_folder, safe_filename,
    VIDEO_EXTS, AUDIO_EXTS,
)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_MUS   = '🎵'

SESSION_TIMEOUT = 300  # seconds

# user_id -> {"video": (media, name), "keep": "shortest"/"longest", "ts": time}
_PENDING = {}


def _video_from(message: Message):
    if message.video:
        return message.video, message.video.file_name or f"video_{message.id}.mp4"
    if message.document and (message.document.file_name or "").lower().endswith(VIDEO_EXTS):
        return message.document, message.document.file_name
    return None, None


def _audio_from(message: Message):
    if message.audio:
        return message.audio, message.audio.file_name or f"audio_{message.id}.mp3"
    if message.voice:
        return message.voice, f"voice_{message.id}.ogg"
    if message.document and (message.document.file_name or "").lower().endswith(AUDIO_EXTS):
        return message.document, message.document.file_name
    return None, None


@Client.on_message(filters.command("addaudio") & filters.private)
async def addaudio_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    replied = message.reply_to_message
    video, vname = _video_from(replied) if replied else (None, None)
    if not video:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>video</b> with <code>/addaudio</code> "
            f"(optionally <code>/addaudio long</code> to keep the longer track instead of "
            f"trimming to the shorter one).</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    keep = "longest" if (len(message.command) > 1 and message.command[1].lower() in ("long", "longest")) else "shortest"
    _PENDING[user_id] = {"video": (video, safe_filename(vname, f"video_{replied.id}.mp4")),
                          "replied_id": replied.id, "keep": keep, "ts": time.time()}

    await message.reply_text(
        f"<b>{E_MUS} Got the video.</b> Now send or reply with the <b>audio file</b> "
        f"you want to merge onto it (within {SESSION_TIMEOUT // 60} min).",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.private & (filters.audio | filters.voice | filters.document), group=2)
async def addaudio_receive(client: Client, message: Message):
    user_id = message.from_user.id
    session = _PENDING.get(user_id)
    if not session:
        return
    if time.time() - session["ts"] > SESSION_TIMEOUT:
        _PENDING.pop(user_id, None)
        return

    audio, aname = _audio_from(message)
    if not audio:
        return  # not an audio-ish file; ignore, let other handlers process it

    _PENDING.pop(user_id, None)
    video, vname = session["video"]
    keep = session["keep"]

    status = await message.reply_text(f"<b>{E_GEAR} Downloading video + audio...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("addaudio"), f"{user_id}_{session['replied_id']}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    v_path = os.path.join(temp_dir, vname)
    a_path = os.path.join(temp_dir, safe_filename(aname, f"audio_{message.id}.mp3"))

    try:
        # video came from the replied-to message captured earlier
        video_msg = await client.get_messages(message.chat.id, session["replied_id"])
        await client.download_media(video_msg, file_name=v_path)
        await client.download_media(message, file_name=a_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    v_duration, _, _ = await asyncio.to_thread(get_video_metadata, v_path)

    base_name, ext = os.path.splitext(vname)
    out_name = f"{base_name}_withaudio{ext or '.mp4'}"
    out_path = os.path.join(temp_dir, out_name)

    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", v_path, "-i", a_path,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    ]
    if keep == "shortest":
        cmd.append("-shortest")
    cmd.append(out_path)

    parse_line = make_ffmpeg_progress_parser(v_duration or 0, title="Merging audio into video...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Merging audio into video...", parse_line, user_id=user_id, queue_label="Add audio",
    )

    if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(
            f"<b>{E_CROSS} Merge failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    out_duration, _, _ = await asyncio.to_thread(get_video_metadata, out_path)
    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_MUS} Audio merged ({keep})",
        file_name=out_name, duration=out_duration, quality="Audio merged",
    )

    shutil.rmtree(temp_dir, ignore_errors=True)
