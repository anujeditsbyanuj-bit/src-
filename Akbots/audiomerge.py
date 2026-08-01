# Akbots - Don't Remove Credit - @AkBots_Official
#
# Audio Merger — /amerge
#   /amerge        — start a merge session, then just send 2+ audio files.
#   /amergedone    — join everything queued so far into one audio file.
#   /amergestatus  — show how many are queued.
#   /amergecancel  — abort the session and wipe any downloaded audio.
#
# Same fast-copy-then-reencode-fallback strategy as videomerge.py: tries
# ffmpeg's concat demuxer with -c copy first (works when every input
# shares codec/sample-rate), falls back to the concat filter (re-encode
# to AAC) if the sources don't match.

import os
import shutil
import asyncio
import subprocess
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import (
    upload_file, run_subprocess_with_progress, make_ffmpeg_progress_parser,
    AUDIO_EXTS,
)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_TIP   = '<emoji id=5422439311196834318>💡</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_TRASH = '<emoji id=5260293700088511294>🗑</emoji>'
E_MUS   = '🎵'

MAX_MERGE_FILES = 30

# user_id -> {"dir": temp_dir, "files": [path, ...], "names": [orig_name, ...]}
_MERGE_SESSIONS = {}


def _get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(r.stdout.strip() or "0")
    except ValueError:
        return 0.0


def _has_session(_, __, message: Message) -> bool:
    return bool(message.from_user) and message.from_user.id in _MERGE_SESSIONS


_amerge_active = filters.create(_has_session)


def _cleanup(user_id: int):
    session = _MERGE_SESSIONS.pop(user_id, None)
    if session:
        shutil.rmtree(session["dir"], ignore_errors=True)
    return session


def _is_audio_doc(message: Message) -> bool:
    if message.audio or message.voice:
        return True
    if message.document:
        name = (message.document.file_name or "").lower()
        return name.endswith(AUDIO_EXTS)
    return False


@Client.on_message(filters.command("amerge") & filters.private)
async def start_amerge(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if user_id in _MERGE_SESSIONS:
        count = len(_MERGE_SESSIONS[user_id]["files"])
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>An audio merge session is already running.</b>\n\n"
            f"{E_INFO} <b>{count}</b> file(s) queued so far.\n"
            f"{E_TIP} Send more audio, <code>/amergedone</code> to finish, "
            f"or <code>/amergecancel</code> to start over.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    temp_dir = os.path.join("downloads", "amerge", str(user_id))
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    _MERGE_SESSIONS[user_id] = {"dir": temp_dir, "files": [], "names": []}

    await message.reply_text(
        f"<blockquote>{E_MUS} <b>Audio Merge — session started</b>\n\n"
        f"{E_TIP} Send me the audio files you want to merge, in order.\n"
        f"{E_INFO} Up to <b>{MAX_MERGE_FILES}</b> files per session.\n\n"
        f"<code>/amergedone</code> — merge everything sent so far\n"
        f"<code>/amergestatus</code> — see how many are queued\n"
        f"<code>/amergecancel</code> — abort</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(
    filters.private & _amerge_active &
    (filters.audio | filters.voice | (filters.document & filters.create(
        lambda _, __, m: bool(m.document and (m.document.file_name or "").lower().endswith(AUDIO_EXTS))
    )))
)
async def collect_amerge_audio(client: Client, message: Message):
    user_id = message.from_user.id
    session = _MERGE_SESSIONS[user_id]

    if len(session["files"]) >= MAX_MERGE_FILES:
        return await message.reply_text(
            f"<b>{E_WARN} Limit reached ({MAX_MERGE_FILES} files).</b>\n"
            f"<i>{E_TIP} Send /amergedone to merge what's already queued.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    media = message.audio or message.voice or message.document
    orig_name = getattr(media, "file_name", None) or f"audio_{len(session['files']) + 1}.mp3"
    idx = len(session["files"])
    dest = os.path.join(session["dir"], f"{idx:02d}_{orig_name}")

    status = await message.reply_text(f"<b>{E_GEAR} Downloading audio {idx + 1}...</b>",
                                       parse_mode=enums.ParseMode.HTML)
    try:
        path = await client.download_media(message, file_name=dest)
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    session["files"].append(path)
    session["names"].append(orig_name)
    await status.edit_text(
        f"<b>{E_CHECK} Audio {idx + 1} added</b> — <code>{orig_name}</code>\n"
        f"<i>{E_INFO} {len(session['files'])} queued. Send more or /amergedone.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("amergestatus") & filters.private & _amerge_active)
async def amerge_status(client: Client, message: Message):
    session = _MERGE_SESSIONS[message.from_user.id]
    if not session["files"]:
        listing = f"<i>{E_INFO} No audio queued yet.</i>"
    else:
        listing = "\n".join(f"{i + 1}. <code>{n}</code>" for i, n in enumerate(session["names"]))
    await message.reply_text(f"<blockquote>{E_GEAR} <b>Audio Merge Session</b>\n\n{listing}</blockquote>",
                              parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("amergecancel") & filters.private & _amerge_active)
async def cancel_amerge(client: Client, message: Message):
    _cleanup(message.from_user.id)
    await message.reply_text(
        f"<blockquote>{E_TRASH} <b>Audio merge session cancelled.</b>\n"
        f"<i>{E_INFO} Any downloaded files were deleted.</i></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


async def _concat_stream_copy(files, out_path, status, total_duration, user_id):
    list_path = out_path + "_inputs.txt"
    with open(list_path, "w") as f:
        for fp in files:
            escaped = fp.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    cmd = ["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0",
           "-i", list_path, "-c", "copy", out_path]
    parse_line = make_ffmpeg_progress_parser(total_duration, title="Merging audio (fast copy)...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Merging audio (fast copy)...", parse_line, user_id=user_id, queue_label="Merge audio",
    )
    try:
        os.remove(list_path)
    except Exception:
        pass
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0, tail


async def _concat_reencode(files, out_path, status, total_duration, user_id):
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    for fp in files:
        cmd += ["-i", fp]
    n = len(files)
    filter_parts = "".join(f"[{i}:a:0]" for i in range(n))
    filter_complex = f"{filter_parts}concat=n={n}:v=0:a=1[outa]"
    cmd += ["-filter_complex", filter_complex, "-map", "[outa]",
            "-c:a", "aac", "-b:a", "192k", out_path]
    parse_line = make_ffmpeg_progress_parser(total_duration, title="Merging audio (re-encoding)...")
    rc, tail = await run_subprocess_with_progress(
        cmd, status, "Merging audio (re-encoding)...", parse_line, user_id=user_id, queue_label="Merge audio",
    )
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0, tail


@Client.on_message(filters.command(["amergedone", "done_amerge"]) & filters.private & _amerge_active)
async def finish_amerge(client: Client, message: Message):
    user_id = message.from_user.id
    session = _MERGE_SESSIONS[user_id]
    files = session["files"]

    if len(files) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Need at least 2 audio files to merge.</b>\n"
            f"<i>{E_TIP} Send more, or /amergecancel to abort.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(f"<b>{E_GEAR} Preparing to merge {len(files)} audio files...</b>",
                                       parse_mode=enums.ParseMode.HTML)

    durations = [await asyncio.to_thread(_get_audio_duration, fp) for fp in files]
    total_duration = sum(durations)

    out_path = os.path.join(session["dir"], f"merged_{user_id}.mp3")

    ok, tail = await _concat_stream_copy(files, out_path, status, total_duration, user_id)
    if not ok:
        await status.edit_text(f"<b>{E_GEAR} Fast merge failed (mismatched sources) — re-encoding instead...</b>",
                                parse_mode=enums.ParseMode.HTML)
        out_path = os.path.join(session["dir"], f"merged_{user_id}.m4a")
        ok, tail = await _concat_reencode(files, out_path, status, total_duration, user_id)

    if not ok:
        _cleanup(user_id)
        return await status.edit_text(f"<b>{E_CROSS} Merge failed.</b>\n\n<code>{tail[-300:]}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    out_duration = await asyncio.to_thread(_get_audio_duration, out_path)
    out_name = os.path.basename(out_path)

    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_CHECK} {len(files)} audio files merged",
        file_name=out_name, duration=int(out_duration or total_duration), quality="Merged",
    )

    _cleanup(user_id)
