# Akbots - Don't Remove Credit - @AkBots_Official
#
# /mastervideo — high-resolution video upscaling/mastering, ported from the
# standalone "Video Mastering Engine" Node.js CLI (Video_Enhance-main,
# uxtra-mastering-engine v1.0.0). That tool ran interactively on your own
# machine against local files; this version reuses the same exact FFmpeg
# filter chain and encoder settings but as a bot command against a replied
# Telegram video, following this codebase's existing /compress and
# /encode conventions (run_subprocess_with_progress, upload_file, etc.)
# instead of inquirer prompts and a local file scan.
#
# Pipeline (identical to the original CLI's startMastering()):
#   1. Lanczos scale to target resolution (accurate_rnd+bitexact,
#      aspect-preserving + pad to exact target size)
#   2. hqdn3d — 3D spatial/temporal denoise
#   3. cas — Contrast Adaptive Sharpening
#   4. unsharp — additional detail/edge enhancement
#   5. eq — +5% contrast, +12% saturation
#   6. HEVC (libx265) 10-bit encode, slow preset, CRF 16, HDR-ready
#
# Not ported: the original's interactive file-picker/glob scan — replaced
# with "reply to a video" like every other video command here.
#
# Performance note (from the original README): upscaling to 4K/8K is very
# CPU-intensive and slow CRF 16 output files can be huge — this command
# warns about that up front rather than silently taking forever.

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, make_output_folder, safe_filename,
    fmt_bytes, VIDEO_EXTS, safe_edit,
)
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'

# key -> (label, width, height) — same four presets as CONFIG.RESOLUTIONS
# in the original index.ts.
RESOLUTIONS = {
    "8k":    ("8K Ultra HD (7680x4320)", 7680, 4320),
    "4k":    ("4K Ultra HD (3840x2160)", 3840, 2160),
    "2k":    ("2K QHD (2560x1440)",      2560, 1440),
    "1080p": ("1080p Full HD (1920x1080)", 1920, 1080),
}

# Exact encoder settings from the original CONFIG.ENCODER.
PRESET = "slow"
CRF = 16
V_CODEC = "libx265"
PIX_FMT = "yuv420p10le"
SCALE_FLAGS = "lanczos+accurate_rnd+bitexact"
X265_PARAMS = "aq-mode=3:strong-intra-smoothing=0:psy-rd=2.0:psy-rdoq=1.0:rd=4"

# session_id (message.id of the /mastervideo command) -> {"message": Message, "orig_name": str}
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


def _resolution_kb(session_id: str) -> InlineKeyboardMarkup:
    rows = [[make_button(label, callback_data=f"mstr#{session_id}#{key}", style=_BS.PRIMARY if _BS else None)]
            for key, (label, *_rest) in RESOLUTIONS.items()]
    rows.append([make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"mstrcancel#{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


def _build_filter(w: int, h: int) -> str:
    return ",".join([
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags={SCALE_FLAGS}",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "hqdn3d=1.5:1.5:6:6",
        "cas=0.6",
        "unsharp=3:3:0.8:3:3:0",
        "eq=contrast=1.05:saturation=1.12",
        f"format={PIX_FMT}",
    ])


@Client.on_message(filters.private & filters.command(["mastervideo", "upscale", "videomaster"]))
async def mastervideo_cmd(client: Client, message: Message):
    media, orig_name = _replied_video_document(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>ᴠɪᴅᴇᴏ</b> (or a video sent as a file) with "
            f"<code>/mastervideo</code> to upscale/master it to 1080p/2K/4K/8K — Lanczos scaling, "
            f"3D denoise, contrast-adaptive sharpening, and HEVC 10-bit output.\n\n"
            f"<i>{E_WARN} 4K/8K mastering is very CPU-heavy and slow (CRF 16, 'slow' preset) — "
            f"expect a long wait and a large output file for full-length videos.</i></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    session_id = str(message.id)
    _SESSIONS[session_id] = {"message": message, "orig_name": orig_name}
    if len(_SESSIONS) > 200:
        _SESSIONS.pop(next(iter(_SESSIONS)), None)

    await message.reply_text(
        f"<b>{E_GEAR} Choose a target master resolution:</b>\n"
        f"<i>{E_WARN} Higher = slower + bigger file.</i>",
        reply_markup=_resolution_kb(session_id),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^mstrcancel#"))
async def mastervideo_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^mstr#([^#]+)#(8k|4k|2k|1080p)$"))
async def mastervideo_run_callback(client: Client, callback_query: CallbackQuery):
    session_id, res_key = callback_query.matches[0].group(1), callback_query.matches[0].group(2)
    session = _SESSIONS.pop(session_id, None)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text,
            f"<b>{E_CROSS} This session expired — send <code>/mastervideo</code> again.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    message = session["message"]
    orig_name = session["orig_name"]
    replied = message.reply_to_message
    user_id = message.from_user.id
    status = callback_query.message

    label, w, h = RESOLUTIONS[res_key]

    temp_dir = os.path.join(make_output_folder("mastervideo"), f"{user_id}_{replied.id}_{session_id}")
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

    base_name, _ext = os.path.splitext(orig_name)
    out_name = f"{base_name}_MASTERED_{res_key.upper()}.mp4"
    out_path = os.path.join(temp_dir, out_name)

    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", in_path,
        "-vf", _build_filter(w, h),
        "-c:v", V_CODEC, "-tag:v", "hvc1", "-preset", PRESET, "-crf", str(CRF),
        "-x265-params", X265_PARAMS,
        "-c:a", "copy", "-movflags", "+faststart",
        out_path,
    ]

    parse_line = make_ffmpeg_progress_parser(duration or 0, title=f"Mastering ({label})...")
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, f"Mastering ({label})...", parse_line,
        user_id=user_id, queue_label=f"Master ({label})",
    )

    if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Mastering failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        os.remove(in_path)
    except Exception:
        pass

    out_size = os.path.getsize(out_path)
    out_duration, _, _ = await asyncio.to_thread(get_video_metadata, out_path)

    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_ROCKET} Mastered to {label}\n"
        f"HEVC 10-bit • CAS + 3D-Denoise + Unsharp\n"
        f"{fmt_bytes(in_size)} → {fmt_bytes(out_size)}",
        file_name=out_name, duration=out_duration or duration, quality=label,
    )

    shutil.rmtree(temp_dir, ignore_errors=True)
