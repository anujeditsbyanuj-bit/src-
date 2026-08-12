# Akbots - Don't Remove Credit - @AkBots_Official
#
# Five small ffmpeg/stdlib-based tools that came up as a comparison against
# NexusMLTB's feature list (that bot's versions of all five were UI-only
# stubs — buttons that replied with a canned status line and never actually
# processed anything). These are real, using the same
# run_subprocess_with_progress / make_ffmpeg_progress_parser / upload_file
# pipeline every other ffmpeg-based module here (trim.py, convert.py,
# audio_extract.py, ...) already uses:
#
#   /togif       <start> <duration>   — reply to a video, get an animated GIF
#   /audio8d                           — reply to audio, apply an 8D
#                                         auto-panning effect
#   /eq          <vol%> <bassdB> <trebledB> <speed%> — reply to audio,
#                                         combined volume/bass/treble/speed
#   /bassboost   <-20 to 20>           — reply to audio
#   /trebleboost <-20 to 20>           — reply to audio
#   /slowreverb                        — reply to audio, "slowed + reverb"
#                                         effect (fixed preset — see below)
#   /jsonformat  <indent 1-4>          — reply to a .json file, re-indent it
#   /subconvert  <srt|vtt|ass>         — reply to a .srt/.vtt/.ass/.ssa
#                                         subtitle file, convert format
#
# NOT implemented — /subconvert does NOT cover .sbv (YouTube's subtitle
# format): ffmpeg has no built-in demuxer/muxer for it, and converting it
# properly needs a bespoke line-parser rather than reusing ffmpeg like the
# other three formats. Flagged here rather than silently failing — a
# .sbv file passed to /subconvert gets an explicit "not supported" reply,
# not a bad conversion.

import os
import re
import json
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, safe_filename, VIDEO_EXTS, AUDIO_EXTS,
)
from Akbots.direct_utils import safe_edit, make_download_progress

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN  = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_GEAR  = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
E_TIP   = '<tg-emoji emoji-id="5422439311196834318">💡</tg-emoji>'

SUBTITLE_EXTS = ('.srt', '.vtt', '.ass', '.ssa')


async def _ensure_user(message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    return user_id


def _replied_media(message: Message, exts):
    """Returns (media, filename) for a replied video/audio/document whose
    name matches `exts`, else (None, None). Mirrors the helper pattern used
    across trim.py/convert.py/audio_extract.py."""
    replied = message.reply_to_message
    if not replied:
        return None, None
    for media in (replied.video, replied.audio, replied.document, replied.voice):
        if not media:
            continue
        name = getattr(media, "file_name", None) or f"file_{replied.id}"
        if exts is None or name.lower().endswith(exts):
            return media, name
    return None, None


# ═══════════════════════════════════════════════════════════════════════
# /togif — video -> animated GIF
# ═══════════════════════════════════════════════════════════════════════
@Client.on_message(filters.private & filters.command("togif"))
async def togif_cmd(client: Client, message: Message):
    user_id = await _ensure_user(message)
    media, orig_name = _replied_media(message, VIDEO_EXTS)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_GEAR} <b>ᴠɪᴅᴇᴏ → ɢɪғ</b>\n\n"
            f"Reply to a <b>ᴠɪᴅᴇᴏ</b> with <code>/togif [start] [duration]</code>.\n"
            f"Defaults: start <code>0</code>s, duration <code>10</code>s (max 20s — GIFs "
            f"get huge fast).\n\n"
            f"Example: <code>/togif 5 8</code> → 8-second GIF starting at 5s.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    args = message.command[1:]
    try:
        start = float(args[0]) if len(args) >= 1 else 0.0
        duration = min(float(args[1]) if len(args) >= 2 else 10.0, 20.0)
    except ValueError:
        return await message.reply_text(f"<b>{E_CROSS} start/duration must be numbers (seconds).</b>", parse_mode=enums.ParseMode.HTML)

    base_name = os.path.splitext(orig_name)[0]
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "togif", f"{user_id}_{message.reply_to_message.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)
    palette_path = os.path.join(temp_dir, "palette.png")
    out_path = os.path.join(temp_dir, base_name + ".gif")

    try:
        await client.download_media(message.reply_to_message, file_name=in_path, progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Converting to GIF...</b>", parse_mode=enums.ParseMode.HTML)

    # Two-pass palette approach — plain "ffmpeg -i in.mp4 out.gif" produces
    # ugly, banded 256-color GIFs. Generating a custom palette from the
    # actual clip first (palettegen) and dithering against it
    # (paletteuse) is the standard ffmpeg recipe for GIFs that don't look
    # posterized.
    fps = "10"
    scale = "480:-1"
    try:
        proc1 = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(start), "-t", str(duration), "-i", in_path,
            "-vf", f"fps={fps},scale={scale}:flags=lanczos,palettegen",
            palette_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err1 = await proc1.communicate()
        if proc1.returncode != 0 or not os.path.exists(palette_path):
            raise RuntimeError(err1.decode(errors="replace")[-300:])

        proc2 = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(start), "-t", str(duration), "-i", in_path,
            "-i", palette_path,
            "-lavfi", f"fps={fps},scale={scale}:flags=lanczos[x];[x][1:v]paletteuse",
            out_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err2 = await proc2.communicate()
        if proc2.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(err2.decode(errors="replace")[-300:])
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} GIF conversion failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await upload_file(client, message, out_path, status, f"<b>{E_CHECK} {base_name}.gif</b>", file_name=f"{base_name}.gif")
    shutil.rmtree(temp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Audio effects — 8D, EQ, bass/treble boost, slowed+reverb
# ═══════════════════════════════════════════════════════════════════════
async def _run_audio_filter(client: Client, message: Message, af: str, out_suffix: str, label: str):
    """Shared worker for all audio-filter commands below: download the
    replied audio -> run one ffmpeg -af filter chain -> upload the result.
    `af` is the ffmpeg -af filtergraph string; `out_suffix` is appended to
    the base filename (before the extension) so e.g. "song.mp3" ->
    "song_8d.mp3"."""
    user_id = await _ensure_user(message)
    media, orig_name = _replied_media(message, AUDIO_EXTS)
    if not media:
        return None  # caller already sent the usage text
    return media, orig_name, user_id


async def _process_audio(client: Client, message: Message, media, orig_name: str,
                          user_id: int, af: str, out_suffix: str, label: str):
    ext = os.path.splitext(orig_name)[1] or ".mp3"
    base_name = os.path.splitext(orig_name)[0]
    out_name = safe_filename(f"{base_name}{out_suffix}{ext}", f"audio{out_suffix}{ext}")

    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)
    temp_dir = os.path.join("downloads", "audiofx", f"{user_id}_{message.reply_to_message.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)
    out_path = os.path.join(temp_dir, out_name)

    try:
        await client.download_media(message.reply_to_message, file_name=in_path, progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    duration, _, _ = await asyncio.to_thread(get_video_metadata, in_path)

    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", in_path, "-af", af, out_path]
    parse_line = make_ffmpeg_progress_parser(duration or 0, title=label)
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, label, parse_line,
        user_id=user_id, queue_label=label,
    )

    if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} {label} failed.</b>\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML
        )

    await upload_file(client, message, out_path, status, f"<b>{E_CHECK} {out_name}</b>", file_name=out_name)
    shutil.rmtree(temp_dir, ignore_errors=True)


@Client.on_message(filters.private & filters.command("audio8d"))
async def audio8d_cmd(client: Client, message: Message):
    media, orig_name = _replied_media(message, AUDIO_EXTS)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_GEAR} <b>8ᴅ ᴀᴜᴅɪᴏ</b>\n\n"
            f"Reply to an <b>ᴀᴜᴅɪᴏ ғɪʟᴇ</b> with <code>/audio8d</code> to apply an "
            f"auto-panning \"8D\" effect (headphones recommended).</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    user_id = await _ensure_user(message)
    # apulsator auto-pans the audio left<->right on a slow cycle — the
    # standard ffmpeg recipe the "8D audio" trend is built on. hz controls
    # how fast it swings; 0.08 (~12.5s per full cycle) is the commonly used
    # value that sounds "8D" rather than just jarring.
    await _process_audio(client, message, media, orig_name, user_id,
                          af="apulsator=hz=0.08", out_suffix="_8D", label="Applying 8D effect")


@Client.on_message(filters.private & filters.command("bassboost"))
async def bassboost_cmd(client: Client, message: Message):
    media, orig_name = _replied_media(message, AUDIO_EXTS)
    args = message.command[1:]
    if not media or not args:
        return await message.reply_text(
            f"<blockquote>{E_GEAR} <b>ʙᴀss ʙᴏᴏsᴛᴇʀ</b>\n\n"
            f"Reply to an <b>ᴀᴜᴅɪᴏ ғɪʟᴇ</b> with <code>/bassboost &lt;-20 to 20&gt;</code>.\n"
            f"Example: <code>/bassboost 10</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        gain = float(args[0])
        if not -20 <= gain <= 20:
            raise ValueError
    except ValueError:
        return await message.reply_text(f"<b>{E_CROSS} Value must be a number between -20 and 20.</b>", parse_mode=enums.ParseMode.HTML)

    user_id = await _ensure_user(message)
    await _process_audio(client, message, media, orig_name, user_id,
                          af=f"bass=g={gain}", out_suffix="_bass", label="Boosting bass")


@Client.on_message(filters.private & filters.command("trebleboost"))
async def trebleboost_cmd(client: Client, message: Message):
    media, orig_name = _replied_media(message, AUDIO_EXTS)
    args = message.command[1:]
    if not media or not args:
        return await message.reply_text(
            f"<blockquote>{E_GEAR} <b>ᴛʀᴇʙʟᴇ ʙᴏᴏsᴛᴇʀ</b>\n\n"
            f"Reply to an <b>ᴀᴜᴅɪᴏ ғɪʟᴇ</b> with <code>/trebleboost &lt;-20 to 20&gt;</code>.\n"
            f"Example: <code>/trebleboost 8</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        gain = float(args[0])
        if not -20 <= gain <= 20:
            raise ValueError
    except ValueError:
        return await message.reply_text(f"<b>{E_CROSS} Value must be a number between -20 and 20.</b>", parse_mode=enums.ParseMode.HTML)

    user_id = await _ensure_user(message)
    await _process_audio(client, message, media, orig_name, user_id,
                          af=f"treble=g={gain}", out_suffix="_treble", label="Boosting treble")


@Client.on_message(filters.private & filters.command("eq"))
async def eq_cmd(client: Client, message: Message):
    media, orig_name = _replied_media(message, AUDIO_EXTS)
    args = message.command[1:]
    if not media or len(args) < 4:
        return await message.reply_text(
            f"<blockquote>{E_GEAR} <b>ᴍᴜsɪᴄ ᴇǫᴜᴀʟɪᴢᴇʀ</b>\n\n"
            f"Reply to an <b>ᴀᴜᴅɪᴏ ғɪʟᴇ</b> with:\n"
            f"<code>/eq &lt;volume%&gt; &lt;bassdB&gt; &lt;trebledB&gt; &lt;speed%&gt;</code>\n\n"
            f"• volume%: 10–200\n• bass/treble: -20 to 20 dB\n• speed%: 50–200\n\n"
            f"Example: <code>/eq 120 6 4 100</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        volume, bass, treble, speed = (float(a) for a in args[:4])
        if not (10 <= volume <= 200 and -20 <= bass <= 20 and -20 <= treble <= 20 and 50 <= speed <= 200):
            raise ValueError
    except ValueError:
        return await message.reply_text(
            f"<b>{E_CROSS} Check your ranges:</b> volume 10-200, bass/treble -20 to 20, speed 50-200.",
            parse_mode=enums.ParseMode.HTML
        )

    user_id = await _ensure_user(message)
    # atempo only accepts 0.5-2.0 per instance, which conveniently matches
    # the 50-200% range this command exposes — no chaining needed.
    af = f"volume={volume/100},bass=g={bass},treble=g={treble},atempo={speed/100}"
    await _process_audio(client, message, media, orig_name, user_id,
                          af=af, out_suffix="_eq", label="Applying EQ")


@Client.on_message(filters.private & filters.command("slowreverb"))
async def slowreverb_cmd(client: Client, message: Message):
    media, orig_name = _replied_media(message, AUDIO_EXTS)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_GEAR} <b>sʟᴏᴡᴇᴅ + ʀᴇᴠᴇʀʙ</b>\n\n"
            f"Reply to an <b>ᴀᴜᴅɪᴏ ғɪʟᴇ</b> with <code>/slowreverb</code> for the "
            f"slowed-down, reverb-heavy \"lofi\" remix effect.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    user_id = await _ensure_user(message)
    # Fixed preset, not user-tunable (matches how this trend's tools
    # normally work — one button, one sound). atempo=0.85 for the slow-
    # down, aecho for the reverb tail (delay/decay values tuned for a
    # subtle wash rather than a cavernous echo).
    af = "atempo=0.85,aecho=0.8:0.88:60:0.4"
    await _process_audio(client, message, media, orig_name, user_id,
                          af=af, out_suffix="_slowreverb", label="Applying slowed + reverb")


# ═══════════════════════════════════════════════════════════════════════
# /jsonformat — re-indent a .json file
# ═══════════════════════════════════════════════════════════════════════
@Client.on_message(filters.private & filters.command("jsonformat"))
async def jsonformat_cmd(client: Client, message: Message):
    media, orig_name = _replied_media(message, (".json",))
    args = message.command[1:]
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_GEAR} <b>ᴊsᴏɴ ғᴏʀᴍᴀᴛᴛᴇʀ</b>\n\n"
            f"Reply to a <b>.ᴊsᴏɴ ғɪʟᴇ</b> with <code>/jsonformat [indent]</code> "
            f"(indent 1-4, default 2).</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    indent = 2
    if args:
        try:
            indent = int(args[0])
            if not 1 <= indent <= 4:
                raise ValueError
        except ValueError:
            return await message.reply_text(f"<b>{E_CROSS} Indent must be a whole number 1-4.</b>", parse_mode=enums.ParseMode.HTML)

    user_id = await _ensure_user(message)
    base_name = os.path.splitext(orig_name)[0]
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "jsonformat", f"{user_id}_{message.reply_to_message.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)
    out_path = os.path.join(temp_dir, f"{base_name}_formatted.json")

    try:
        await client.download_media(message.reply_to_message, file_name=in_path, progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    try:
        with open(in_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
    except json.JSONDecodeError as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Not valid JSON:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await upload_file(client, message, out_path, status,
                       f"<b>{E_CHECK} {base_name}_formatted.json</b> (indent={indent})",
                       file_name=f"{base_name}_formatted.json", force_document=True)
    shutil.rmtree(temp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# /subconvert — srt/vtt/ass/ssa subtitle format conversion
# ═══════════════════════════════════════════════════════════════════════
@Client.on_message(filters.private & filters.command("subconvert"))
async def subconvert_cmd(client: Client, message: Message):
    media, orig_name = _replied_media(message, None)
    args = message.command[1:]

    if media and orig_name.lower().endswith(".sbv"):
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>.sʙᴠ ɪsɴ'ᴛ sᴜᴘᴘᴏʀᴛᴇᴅ.</b>\n\n"
            f"ffmpeg has no built-in support for YouTube's .sbv format — only "
            f"<code>srt</code>, <code>vtt</code>, and <code>ass</code>/<code>ssa</code> "
            f"conversions work here.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    if not media or not orig_name.lower().endswith(SUBTITLE_EXTS) or not args:
        return await message.reply_text(
            f"<blockquote>{E_GEAR} <b>sᴜʙᴛɪᴛʟᴇ ᴄᴏɴᴠᴇʀᴛᴇʀ</b>\n\n"
            f"Reply to a <b>.sʀᴛ / .ᴠᴛᴛ / .ᴀss / .ssᴀ</b> file with "
            f"<code>/subconvert &lt;srt|vtt|ass&gt;</code>.\n\n"
            f"Example: <code>/subconvert vtt</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    target = args[0].lower().lstrip(".")
    if target not in ("srt", "vtt", "ass"):
        return await message.reply_text(f"<b>{E_CROSS} Target format must be srt, vtt, or ass.</b>", parse_mode=enums.ParseMode.HTML)

    base_name = os.path.splitext(orig_name)[0]
    if orig_name.lower().endswith(f".{target}") or (target == "ass" and orig_name.lower().endswith(".ssa")):
        return await message.reply_text(f"<b>{E_WARN} This file is already .{target}.</b>", parse_mode=enums.ParseMode.HTML)

    user_id = await _ensure_user(message)
    status = await message.reply_text(f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "subconvert", f"{user_id}_{message.reply_to_message.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)
    out_path = os.path.join(temp_dir, f"{base_name}.{target}")

    try:
        await client.download_media(message.reply_to_message, file_name=in_path, progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Converting to .{target}...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", in_path, out_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(err.decode(errors="replace")[-300:])
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Conversion failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await upload_file(client, message, out_path, status, f"<b>{E_CHECK} {base_name}.{target}</b>",
                       file_name=f"{base_name}.{target}", force_document=True)
    shutil.rmtree(temp_dir, ignore_errors=True)
