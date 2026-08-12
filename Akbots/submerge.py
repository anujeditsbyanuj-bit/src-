# Akbots - Don't Remove Credit - @AkBots_Official
#
# Video + Subtitle Merger — /addsub
#
#   /addsub soft   (default) -> reply to a video, then send the subtitle
#                    file (.srt/.ass/.vtt). Subtitle is muxed in as a
#                    separate selectable track, video/audio untouched
#                    (fast, -c copy).
#   /addsub hard   -> burns the subtitle permanently into the video frames
#                    (re-encode with the subtitles/ass filter — slower,
#                    but works on players/devices with no subtitle
#                    support, e.g. some phone media players).

import os
import re
import shutil
import asyncio
import time
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, make_output_folder, safe_filename, VIDEO_EXTS,
)
from Akbots.direct_utils import safe_edit, make_download_progress

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN  = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_INFO  = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'
E_GEAR  = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
E_CC    = '💬'

SESSION_TIMEOUT = 300
SUB_EXTS = ('.srt', '.ass', '.ssa', '.vtt')

# user_id -> {"video": name, "replied_id": id, "mode": "hard"/"soft", "ts": time}
_PENDING = {}


def _video_from(message: Message):
    if message.video:
        return message.video, message.video.file_name or f"video_{message.id}.mp4"
    if message.document and (message.document.file_name or "").lower().endswith(VIDEO_EXTS):
        return message.document, message.document.file_name
    return None, None


def _sub_from(message: Message):
    if message.document and (message.document.file_name or "").lower().endswith(SUB_EXTS):
        return message.document, message.document.file_name
    return None, None


@Client.on_message(filters.command("addsub") & filters.private)
async def addsub_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    replied = message.reply_to_message
    video, vname = _video_from(replied) if replied else (None, None)
    if not video:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>ᴠɪᴅᴇᴏ</b> with <code>/addsub</code>.\n\n"
            f"{E_INFO} <b>ᴜsᴀɢᴇ:</b>\n"
            f"<code>/addsub soft</code> — mux as a selectable track (default, fast)\n"
            f"<code>/addsub hard</code> — burn subtitles into the video (slower)</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    mode = "hard" if (len(message.command) > 1 and message.command[1].lower().startswith("hard")) else "soft"
    _PENDING[user_id] = {"video": safe_filename(vname, f"video_{replied.id}.mp4"),
                          "replied_id": replied.id, "mode": mode, "ts": time.time()}

    await message.reply_text(
        f"<b>{E_CC} Got the video ({mode}sub mode).</b> Now send the <b>.sʀᴛ/.ᴀss/.ᴠᴛᴛ</b> "
        f"subtitle file (within {SESSION_TIMEOUT // 60} min).",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.private & filters.document, group=3)
async def addsub_receive(client: Client, message: Message):
    user_id = message.from_user.id
    session = _PENDING.get(user_id)
    if not session:
        return
    if time.time() - session["ts"] > SESSION_TIMEOUT:
        _PENDING.pop(user_id, None)
        return

    sub, sname = _sub_from(message)
    if not sub:
        return

    _PENDING.pop(user_id, None)
    vname, mode = session["video"], session["mode"]

    status = await message.reply_text(f"<b>{E_GEAR} Downloading video + subtitle...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("addsub"), f"{user_id}_{session['replied_id']}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    v_path = os.path.join(temp_dir, vname)
    s_path = os.path.join(temp_dir, safe_filename(sname, f"sub_{message.id}.srt"))

    try:
        video_msg = await client.get_messages(message.chat.id, session["replied_id"])
        await client.download_media(video_msg, file_name=v_path,
                                     progress=make_download_progress(status, file_name=vname))
        await client.download_media(message, file_name=s_path,
                                     progress=make_download_progress(status, file_name=sname))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    v_duration, _, _ = await asyncio.to_thread(get_video_metadata, v_path)
    base_name, ext = os.path.splitext(vname)
    ext = ext or ".mp4"

    if mode == "soft":
        out_name = f"{base_name}_softsub{ext}"
        out_path = os.path.join(temp_dir, out_name)
        # mkv container needed if source isn't already mkv, for reliable soft-sub muxing
        if ext.lower() != ".mkv":
            out_name = f"{base_name}_softsub.mkv"
            out_path = os.path.join(temp_dir, out_name)
        sub_codec = "srt" if s_path.lower().endswith((".srt", ".vtt")) else "ass"
        if out_path.lower().endswith(".mkv"):
            sub_codec = "srt" if s_path.lower().endswith(".srt") else "ass"
        cmd = [
            "ffmpeg", "-hide_banner", "-y", "-i", v_path, "-i", s_path,
            "-map", "0", "-map", "1", "-c", "copy",
            "-c:s", sub_codec if out_path.lower().endswith(".mkv") else "mov_text",
            out_path,
        ]
        title = "Muxing subtitle (soft)..."
    else:
        out_name = f"{base_name}_hardsub{ext}"
        out_path = os.path.join(temp_dir, out_name)
        # ffmpeg's subtitles filter needs a path with no special chars issues; escape colons for Windows-style
        escaped_sub = s_path.replace("\\", "/").replace(":", "\\:")
        vf = f"subtitles='{escaped_sub}'" if s_path.lower().endswith((".ass", ".ssa")) else f"subtitles='{escaped_sub}'"
        cmd = [
            "ffmpeg", "-hide_banner", "-y", "-i", v_path,
            "-vf", vf, "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
            "-c:a", "copy", out_path,
        ]
        title = "Burning subtitle (hard)..."

    parse_line = make_ffmpeg_progress_parser(v_duration or 0, title=title)
    rc, tail = await run_subprocess_with_progress(
        cmd, status, title, parse_line, user_id=user_id, queue_label="Add subtitle",
    )

    if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Subtitle merge failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    out_duration, _, _ = await asyncio.to_thread(get_video_metadata, out_path)
    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_CC} Subtitle {'burned in' if mode == 'hard' else 'muxed'} ({mode}sub)",
        file_name=out_name, duration=out_duration, quality=f"{mode.title()}sub",
    )

    shutil.rmtree(temp_dir, ignore_errors=True)
