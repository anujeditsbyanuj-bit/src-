# Akbots - Don't Remove Credit - @AkBots_Official
#
# Video re-encoding — reply to a video (or a video sent as a document) with
# /encode and walk through: resolution (144p-4K) -> codec (H.264/H.265) ->
# quality (CRF). Adapted from the ENCODING-BOT-master project's
# utils/encoding.py command-building.
#
# The wizard below still drives the 3 basics (resolution/codec/CRF) so
# /encode stays a quick one-shot picker like the rest of this bot
# (watermark.py / convert.py: download -> ffmpeg -> upload_file). Every
# other knob the source project exposed — preset/tune/CABAC/10-bit/aspect/
# reframe/FPS/hardsub/softsub/watermark-burn/metadata-tag/audio codec+
# bitrate+samplerate+channels/MP4-MKV-AVI — now comes from the persistent
# /encode_settings menu (Akbots/encode_settings.py) via build_encode_args().

import os
import shutil
import uuid
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, VIDEO_EXTS,
)
from Akbots.encode_settings import build_encode_args
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

# height -> label. -2:height keeps the source aspect ratio (even width),
# so a non-16:9 source doesn't get stretched/cropped — only vertical
# resolution is actually chosen, same as every "144p/720p/..." picker users
# already recognise from YouTube etc.
RESOLUTIONS = [
    (144,  "144p"), (240,  "240p"), (360,  "360p"),
    (480,  "480p"), (576,  "576p"), (720,  "720p"),
    (1080, "1080p"), (1440, "1440p (2K)"), (2160, "2160p (4K)"),
]

CODECS = [
    ("h264", "🎞 H.264", "libx264"),
    ("h265", "📦 H.265 / HEVC (smaller file)", "libx265"),
]

# label, crf value — lower CRF = higher quality/bigger file
QUALITIES = [
    ("hq",  "🟢 High Quality", 20),
    ("bal", "🟡 Balanced",     23),
    ("sm",  "🔴 Smaller Size", 28),
]

# session_id -> {"message": Message, "orig_name": str, "height": int, "codec": str}
_SESSIONS = {}


def _probe_has_subtitle_stream(path: str) -> bool:
    import subprocess
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
             "stream=index", "-of", "csv=p=0", path],
            stderr=subprocess.DEVNULL, timeout=30,
        )
        return bool(out.decode().strip())
    except Exception:
        return False


def _replied_video_document(message: Message):
    """Returns (media, orig_name) if the replied message is a video OR a
    document whose filename looks like a video, else (None, None)."""
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
    rows, row = [], []
    for height, label in RESOLUTIONS:
        row.append(make_button(label, callback_data=f"enc#{session_id}#{height}", style=_BS.PRIMARY if _BS else None))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"enccancel#{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


def _codec_kb(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [make_button(label, callback_data=f"enccodec#{session_id}#{key}", style=_BS.PRIMARY if _BS else None)]
        for key, label, _ in CODECS
    ] + [[make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"enccancel#{session_id}", style=_BS.DANGER if _BS else None)]])


def _quality_kb(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [make_button(label, callback_data=f"encq#{session_id}#{key}", style=_BS.PRIMARY if _BS else None)]
        for key, label, _ in QUALITIES
    ] + [[make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"enccancel#{session_id}", style=_BS.DANGER if _BS else None)]])


@Client.on_message(filters.private & filters.command("encode"))
async def encode_cmd(client: Client, message: Message):
    media, orig_name = _replied_video_document(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a <b>ᴠɪᴅᴇᴏ</b> (or a video sent as a file) with "
            f"<code>/encode</code> to re-encode it — choose resolution (144p-4K), codec "
            f"(H.264/H.265), and quality.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    session_id = uuid.uuid4().hex[:10]
    _SESSIONS[session_id] = {"message": message, "orig_name": orig_name}
    if len(_SESSIONS) > 200:
        _SESSIONS.pop(next(iter(_SESSIONS)), None)

    await message.reply_text(
        f"<b>{E_GEAR} Step 1/3 — Choose target resolution:</b>",
        reply_markup=_resolution_kb(session_id),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^enccancel#"))
async def encode_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^enc#([a-f0-9]+)#(\d+)$"))
async def encode_resolution_callback(client: Client, callback_query: CallbackQuery):
    session_id, height = callback_query.matches[0].group(1), int(callback_query.matches[0].group(2))
    session = _SESSIONS.get(session_id)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, 
            f"<b>{E_CROSS} This session expired — send <code>/encode</code> again.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    session["height"] = height
    label = next((lbl for h, lbl in RESOLUTIONS if h == height), f"{height}p")
    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_GEAR} Step 2/3 — Resolution:</b> {label}\n<b>ᴄʜᴏᴏsᴇ ᴄᴏᴅᴇᴄ:</b>",
        reply_markup=_codec_kb(session_id),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^enccodec#([a-f0-9]+)#(h264|h265)$"))
async def encode_codec_callback(client: Client, callback_query: CallbackQuery):
    session_id, codec_key = callback_query.matches[0].group(1), callback_query.matches[0].group(2)
    session = _SESSIONS.get(session_id)
    await callback_query.answer()
    if not session or "height" not in session:
        return await safe_edit(callback_query.message.edit_text, 
            f"<b>{E_CROSS} This session expired — send <code>/encode</code> again.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    session["codec"] = codec_key
    codec_label = next(lbl for key, lbl, _ in CODECS if key == codec_key)
    res_label = next((lbl for h, lbl in RESOLUTIONS if h == session["height"]), f"{session['height']}p")
    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_GEAR} Step 3/3 — {res_label} / {codec_label}</b>\n<b>ᴄʜᴏᴏsᴇ ǫᴜᴀʟɪᴛʏ:</b>",
        reply_markup=_quality_kb(session_id),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^encq#([a-f0-9]+)#(hq|bal|sm)$"))
async def encode_quality_callback(client: Client, callback_query: CallbackQuery):
    session_id, quality_key = callback_query.matches[0].group(1), callback_query.matches[0].group(2)
    session = _SESSIONS.pop(session_id, None)
    await callback_query.answer()
    if not session or "height" not in session or "codec" not in session:
        return await safe_edit(callback_query.message.edit_text, 
            f"<b>{E_CROSS} This session expired — send <code>/encode</code> again.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    message = session["message"]
    orig_name = session["orig_name"]
    height = session["height"]
    codec_key = session["codec"]
    replied = message.reply_to_message
    user_id = message.from_user.id
    status = callback_query.message

    res_label = next((lbl for h, lbl in RESOLUTIONS if h == height), f"{height}p")
    ffmpeg_codec = next(fc for key, _, fc in CODECS if key == codec_key)
    crf = next(c for key, _, c in QUALITIES if key == quality_key)
    quality_label = next(lbl for key, lbl, _ in QUALITIES if key == quality_key)

    base_name, orig_ext = os.path.splitext(orig_name)

    temp_dir = os.path.join("downloads", "encode", f"{user_id}_{replied.id}_{session_id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    duration, src_w, src_h = await asyncio.to_thread(get_video_metadata, in_path)

    # Extra ffmpeg args come from the user's saved /encode_settings; the
    # wizard's resolution/codec/CRF picks above always take priority over
    # enc_resolution/enc_codec/enc_crf inside that dict, so it only
    # contributes preset/tune/CABAC/10-bit/aspect/reframe/fps/hardsub/
    # softsub/watermark/metadata/audio/container.
    has_subs = bool(await asyncio.to_thread(_probe_has_subtitle_stream, in_path))
    extra = await build_encode_args(user_id, in_path, has_subs=has_subs, has_audio=True)

    ext = extra["extension"]
    out_name = f"{base_name}_{res_label.split()[0]}_{codec_key}{ext}"
    out_path = os.path.join(temp_dir, out_name)

    # -2 keeps width even and preserves aspect ratio for the chosen height —
    # upscaling (e.g. a 480p source -> 1080p) works too, ffmpeg just won't
    # add real detail, same tradeoff as any other resolution picker.
    vf_chain = [f"scale=-2:{height}"] + extra["vf_parts"]

    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", in_path]
    cmd += ["-vf", ",".join(vf_chain)]
    cmd += ["-c:v", ffmpeg_codec, "-crf", str(crf)]
    cmd += extra["video_extra"]
    if codec_key == "h265" and ext == ".mp4":
        # Without this tag some Apple devices/players refuse to play HEVC
        # inside an mp4 container — the source project didn't set it either.
        cmd += ["-tag:v", "hvc1"]
    cmd += extra["audio_extra"] if extra["audio_extra"] else ["-c:a", "copy"]
    cmd += extra["subs_extra"]
    cmd += extra["metadata_extra"]
    cmd += [out_path]

    parse_line = make_ffmpeg_progress_parser(duration or 0, title=f"Encoding to {res_label}...")
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, f"Encoding to {res_label}...", parse_line,
        user_id=user_id, queue_label=f"Encode {res_label}",
    )

    if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Encoding failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        os.remove(in_path)
    except Exception:
        pass

    out_duration, _, _ = await asyncio.to_thread(get_video_metadata, out_path)
    codec_label = next(lbl for key, lbl, _ in CODECS if key == codec_key)

    from database.db import db as _db
    raw_settings = await _db.get_encode_settings(user_id)

    await upload_file(
        client, message, out_path, status,
        f"<b>{out_name}</b>\n\n{E_ROCKET} {res_label} • {codec_label} • {quality_label}",
        file_name=out_name, duration=out_duration or duration, quality=res_label,
        disable_notification=not raw_settings["enc_notify"],
    )

    if raw_settings["enc_autodel"]:
        try:
            await replied.delete()
        except Exception:
            pass  # best-effort — missing delete rights (e.g. in a group) shouldn't fail the encode

    shutil.rmtree(temp_dir, ignore_errors=True)
