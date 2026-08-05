# Akbots - Don't Remove Credit - @AkBots_Official
#
# Standalone Compressor — /compress (reply to a video).
#
# /encode is a full resolution+codec+CRF wizard for people who want control
# over the output; /compress is the one-tap version for people who just
# want a smaller file, same resolution, minimum fuss — pick a preset and
# go. No resolution wizard, no encode_settings integration; just
# straight-to-ffmpeg presets tuned for either speed or size:
#
#   ⚡ Fast          — H.264, veryfast preset, CRF 28  (quick, decent shrink)
#   🟡 Balanced       — H.264, medium preset,   CRF 23  (default-quality shrink)
#   📦 HEVC (Smaller) — H.265, medium preset,   CRF 28  (best size, slower)
#   🗜 Max Compress   — H.265, slow preset,      CRF 32  (smallest, slowest)

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, make_output_folder, safe_filename,
    fmt_bytes, VIDEO_EXTS,
)
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_PACK   = '📦'

# key -> (label, ffmpeg codec, preset, crf, container tag needed)
PRESETS = {
    "fast": ("⚡ Fast (H.264)",           "libx264", "veryfast", 28, False),
    "bal":  ("🟡 Balanced (H.264)",       "libx264", "medium",   23, False),
    "hevc": ("📦 HEVC — Smaller file",    "libx265", "medium",   28, True),
    "max":  ("🗜 Max Compression (HEVC)", "libx265", "slow",     32, True),
}

# session_id (message.id of the /compress command) -> {"message": Message}
_SESSIONS = {}


def _replied_video_document(message: Message):
    replied = message.reply_to_message
    if not replied:
        return None, None
    if replied.video:
        name = replied.video.file_name or f"video_{replied.id}.mp4"
        return replied.video, name
    if replied.document:
        name = replied.document.file_name or ""
        if name.lower().endswith(VIDEO_EXTS):
            return replied.document, name
    return None, None


def _preset_kb(session_id: str) -> InlineKeyboardMarkup:
    rows = [[make_button(label, callback_data=f"cmp#{session_id}#{key}", style=_BS.PRIMARY if _BS else None)]
            for key, (label, *_rest) in PRESETS.items()]
    rows.append([make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"cmpcancel#{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


@Client.on_message(filters.private & filters.command("compress"))
async def compress_cmd(client: Client, message: Message):
    media, orig_name = _replied_video_document(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>ᴠɪᴅᴇᴏ</b> (or a video sent as a file) with "
            f"<code>/compress</code> to shrink its file size — pick a preset "
            f"(Fast, Balanced, HEVC, or Max Compression). Resolution stays unchanged; "
            f"for resolution/codec/CRF control, use <code>/encode</code> instead.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    session_id = str(message.id)
    _SESSIONS[session_id] = {"message": message, "orig_name": orig_name}
    if len(_SESSIONS) > 200:
        _SESSIONS.pop(next(iter(_SESSIONS)), None)

    await message.reply_text(
        f"<b>{E_PACK} Choose a compression preset:</b>",
        reply_markup=_preset_kb(session_id),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^cmpcancel#"))
async def compress_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^cmp#([^#]+)#(fast|bal|hevc|max)$"))
async def compress_run_callback(client: Client, callback_query: CallbackQuery):
    session_id, preset_key = callback_query.matches[0].group(1), callback_query.matches[0].group(2)
    session = _SESSIONS.pop(session_id, None)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, 
            f"<b>{E_CROSS} This session expired — send <code>/compress</code> again.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    message = session["message"]
    orig_name = session["orig_name"]
    replied = message.reply_to_message
    user_id = message.from_user.id
    status = callback_query.message

    label, codec, preset, crf, needs_tag = PRESETS[preset_key]

    temp_dir = os.path.join(make_output_folder("compress"), f"{user_id}_{replied.id}_{session_id}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    orig_name = safe_filename(orig_name, f"video_{replied.id}.mp4")
    in_path = os.path.join(temp_dir, orig_name)

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    try:
        in_size = os.path.getsize(in_path)
    except OSError:
        in_size = 0
    duration, _, _ = await asyncio.to_thread(get_video_metadata, in_path)

    base_name, ext = os.path.splitext(orig_name)
    ext = ext if ext.lower() in VIDEO_EXTS else ".mp4"
    out_name = f"{base_name}_{preset_key}{ext}"
    out_path = os.path.join(temp_dir, out_name)

    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", in_path,
           "-c:v", codec, "-preset", preset, "-crf", str(crf)]
    if needs_tag and ext == ".mp4":
        # Without this tag some Apple devices/players refuse to play HEVC
        # inside an mp4 container.
        cmd += ["-tag:v", "hvc1"]
    cmd += ["-c:a", "copy", out_path]

    parse_line = make_ffmpeg_progress_parser(duration or 0, title=f"Compressing ({label})...")
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, f"Compressing ({label})...", parse_line,
        user_id=user_id, queue_label=f"Compress ({label})",
    )

    if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Compression failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        os.remove(in_path)
    except Exception:
        pass

    out_size = os.path.getsize(out_path)
    out_duration, _, _ = await asyncio.to_thread(get_video_metadata, out_path)
    saved_pct = ((in_size - out_size) / in_size * 100) if in_size else 0
    saved_label = f"{saved_pct:.0f}% smaller" if saved_pct > 0 else "no size reduction"

    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_ROCKET} {label}\n"
        f"{fmt_bytes(in_size)} → {fmt_bytes(out_size)} ({saved_label})",
        file_name=out_name, duration=out_duration or duration, quality=label,
    )

    shutil.rmtree(temp_dir, ignore_errors=True)
