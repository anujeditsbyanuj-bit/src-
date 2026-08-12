# Akbots - Don't Remove Credit - @AkBots_Official
#
# Video Merge (ported from Video-Editor-Bot-V22's bot/handlers/merge.py +
# bot/utils/ffmpeg_utils.py):
#   /merge        — start a merge session, then just send 2+ videos.
#   /mergedone    — join every video collected so far into one file and
#                   upload it back.
#   /mergestatus  — show how many videos are queued in the current session.
#   /mergecancel  — abort the session and wipe any downloaded videos.
#
# The original repo only knew one trick: ffmpeg's concat *demuxer* with
# "-c copy" (fast, but silently produces a broken file — or just fails —
# the moment two source videos don't share the exact same codec/resolution/
# fps). That's fine for a standalone toy bot; here videos routinely arrive
# from wildly different sources (YouTube dl, Terabox, forwarded phone
# clips...), so /mergedone tries the fast stream-copy path first and, only
# if that fails, falls back to the concat *filter* (re-encode) which can
# join mismatched inputs. Both passes get a real progress bar via
# direct_utils' ffmpeg helpers instead of the original's silent subprocess
# call, and the result goes out through upload_file() like every other
# plugin (thumbnail/caption/auto-sample/split-if-too-big all apply here
# too, for free).

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import (
    upload_file, get_video_metadata,
    run_subprocess_with_progress, make_ffmpeg_progress_parser,
)
from Akbots.direct_utils import safe_edit, make_download_progress

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN   = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_TIP    = '<tg-emoji emoji-id="5422439311196834318">💡</tg-emoji>'
E_INFO   = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'
E_GEAR   = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
E_TRASH  = '<tg-emoji emoji-id="5260293700088511294">🗑</tg-emoji>'
E_BOLT   = '<tg-emoji emoji-id="5456140674028019486">⚡️</tg-emoji>'

MAX_MERGE_FILES = 20

# user_id -> {"dir": temp_dir, "files": [path, ...], "names": [orig_name, ...]}
_MERGE_SESSIONS = {}


def _has_session(_, __, message: Message) -> bool:
    return bool(message.from_user) and message.from_user.id in _MERGE_SESSIONS


_merge_active = filters.create(_has_session)


def _cleanup(user_id: int):
    session = _MERGE_SESSIONS.pop(user_id, None)
    if session:
        shutil.rmtree(session["dir"], ignore_errors=True)
    return session


@Client.on_message(filters.command("merge") & filters.private)
async def start_merge(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if user_id in _MERGE_SESSIONS:
        count = len(_MERGE_SESSIONS[user_id]["files"])
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>ᴀ ᴍᴇʀɢᴇ sᴇssɪᴏɴ ɪs ᴀʟʀᴇᴀᴅʏ ʀᴜɴɴɪɴɢ.</b>\n\n"
            f"{E_INFO} <b>{count}</b> video(s) queued so far.\n"
            f"{E_TIP} Send more videos, <code>/mergedone</code> to finish, "
            f"or <code>/mergecancel</code> to start over.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    temp_dir = os.path.join("downloads", "merge", str(user_id))
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    _MERGE_SESSIONS[user_id] = {"dir": temp_dir, "files": [], "names": []}

    await message.reply_text(
        f"<blockquote>{E_BOLT} <b>ᴠɪᴅᴇᴏ ᴍᴇʀɢᴇ — sᴇssɪᴏɴ sᴛᴀʀᴛᴇᴅ</b>\n\n"
        f"{E_TIP} Send me the video files you want to merge, in order, "
        f"as videos or documents.\n"
        f"{E_INFO} Up to <b>{MAX_MERGE_FILES}</b> videos per session.\n\n"
        f"<code>/mergedone</code> — merge everything sent so far\n"
        f"<code>/mergestatus</code> — see how many are queued\n"
        f"<code>/mergecancel</code> — abort</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(
    filters.private & _merge_active &
    (filters.video | (filters.document & filters.create(
        lambda _, __, m: bool(m.document and (m.document.mime_type or "").startswith("video/"))
    )))
)
async def collect_merge_video(client: Client, message: Message):
    user_id = message.from_user.id
    session = _MERGE_SESSIONS[user_id]

    if len(session["files"]) >= MAX_MERGE_FILES:
        return await message.reply_text(
            f"<b>{E_WARN} Limit reached ({MAX_MERGE_FILES} videos).</b>\n"
            f"<i>{E_TIP} Send /mergedone to merge what's already queued.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    media = message.video or message.document
    orig_name = media.file_name or f"video_{len(session['files']) + 1}.mp4"
    idx = len(session["files"])
    dest = os.path.join(session["dir"], f"{idx:02d}_{orig_name}")

    status = await message.reply_text(f"<b>{E_GEAR} Downloading video {idx + 1}...</b>",
                                       parse_mode=enums.ParseMode.HTML)
    try:
        path = await client.download_media(message, file_name=dest,
                                            progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    session["files"].append(path)
    session["names"].append(orig_name)
    await safe_edit(status.edit_text, 
        f"<b>{E_CHECK} Video {idx + 1} added</b> — <code>{orig_name}</code>\n"
        f"<i>{E_INFO} {len(session['files'])} queued. Send more or /mergedone.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("mergestatus") & filters.private & _merge_active)
async def merge_status(client: Client, message: Message):
    user_id = message.from_user.id
    session = _MERGE_SESSIONS[user_id]
    if not session["files"]:
        listing = f"<i>{E_INFO} No videos queued yet.</i>"
    else:
        listing = "\n".join(f"{i + 1}. <code>{n}</code>" for i, n in enumerate(session["names"]))
    await message.reply_text(
        f"<blockquote>{E_GEAR} <b>ᴍᴇʀɢᴇ sᴇssɪᴏɴ</b>\n\n{listing}</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("mergecancel") & filters.private & _merge_active)
async def cancel_merge(client: Client, message: Message):
    user_id = message.from_user.id
    _cleanup(user_id)
    await message.reply_text(
        f"<blockquote>{E_TRASH} <b>ᴍᴇʀɢᴇ sᴇssɪᴏɴ ᴄᴀɴᴄᴇʟʟᴇᴅ.</b>\n"
        f"<i>{E_INFO} Any downloaded videos were deleted.</i></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


async def _concat_stream_copy(files, out_path, status, total_duration, user_id):
    """Fast path — no re-encode, works only when every input shares codec/
    resolution/fps/container profile."""
    list_path = out_path + "_inputs.txt"
    with open(list_path, "w") as f:
        for fp in files:
            escaped = fp.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    cmd = ["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0",
           "-i", list_path, "-c", "copy", out_path]
    parse_line = make_ffmpeg_progress_parser(total_duration, title="Merging (fast copy)...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Merging (fast copy)...", parse_line, user_id=user_id, queue_label="Merge videos",
    )
    try:
        os.remove(list_path)
    except Exception:
        pass
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0, tail


async def _concat_reencode(files, out_path, status, total_duration, user_id):
    """Fallback path — re-encodes every input through the concat filter so
    mismatched codecs/resolutions/framerates can still be joined."""
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    for fp in files:
        cmd += ["-i", fp]
    n = len(files)
    filter_parts = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    filter_complex = f"{filter_parts}concat=n={n}:v=1:a=1[outv][outa]"
    cmd += ["-filter_complex", filter_complex, "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-c:a", "aac", out_path]
    parse_line = make_ffmpeg_progress_parser(total_duration, title="Merging (re-encoding)...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Merging (re-encoding)...", parse_line, user_id=user_id, queue_label="Merge videos",
    )
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0, tail



@Client.on_message(filters.command(["mergedone", "done_merge"]) & filters.private & _merge_active)
async def finish_merge(client: Client, message: Message):
    user_id = message.from_user.id
    session = _MERGE_SESSIONS[user_id]
    files = session["files"]

    if len(files) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Need at least 2 videos to merge.</b>\n"
            f"<i>{E_TIP} Send more videos, or /mergecancel to abort.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(f"<b>{E_GEAR} Preparing to merge {len(files)} videos...</b>",
                                       parse_mode=enums.ParseMode.HTML)

    durations = []
    for fp in files:
        d, _, _ = await asyncio.to_thread(get_video_metadata, fp)
        durations.append(d or 0)
    total_duration = sum(durations)

    out_path = os.path.join(session["dir"], f"merged_{user_id}.mp4")

    ok, tail = await _concat_stream_copy(files, out_path, status, total_duration, user_id)
    if not ok:
        await safe_edit(status.edit_text, 
            f"<b>{E_GEAR} Fast merge failed (mismatched sources) — re-encoding instead...</b>",
            parse_mode=enums.ParseMode.HTML,
        )
        ok, tail = await _concat_reencode(files, out_path, status, total_duration, user_id)

    if not ok:
        _cleanup(user_id)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Merge failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    out_duration, _, _ = await asyncio.to_thread(get_video_metadata, out_path)

    await upload_file(
        client, message, out_path, status,
        f"<b>merged_{user_id}.mp4</b>\n\n{E_CHECK} {len(files)} videos merged",
        file_name=f"merged_{user_id}.mp4", duration=out_duration or total_duration, quality="Merged",
    )

    _cleanup(user_id)
