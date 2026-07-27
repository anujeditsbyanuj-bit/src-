# Akbots - Don't Remove Credit - @AkBots_Official
#
# Text watermark — simplified port of File-Rename-4GB-Bot-Auto-Rename-Bot's
# plugins/watermark.py. The source bot picked position via inline buttons;
# this version takes it as a command argument to keep the plugin
# self-contained (no callback-query handler needed). Burning text into
# frames needs a real re-encode (drawtext isn't stream-copyable), so this
# is slower than the metadata/rename features — that's inherent to the
# ffmpeg filter, not something a smarter flag avoids.

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

from Akbots.direct_utils import (
    upload_file, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, VIDEO_EXTS,
)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_TIP   = '<emoji id=5422439311196834318>💡</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_TRASH = '<emoji id=5260293700088511294>🗑</emoji>'
E_STAMP = '💧'

POSITIONS = {
    "top-left":     "x=10:y=10",
    "top-right":    "x=w-tw-10:y=10",
    "bottom-left":  "x=10:y=h-th-10",
    "bottom-right": "x=w-tw-10:y=h-th-10",
    "center":       "x=(w-tw)/2:y=(h-th)/2",
}
POSITION_LABELS = {
    "top-left": "↖️ Top Left", "top-right": "↗️ Top Right",
    "bottom-left": "↙️ Bottom Left", "bottom-right": "↘️ Bottom Right",
    "center": "⏺️ Center",
}


def _escape_drawtext(text: str) -> str:
    """Escapes characters that are special to ffmpeg's drawtext filter
    argument syntax (colon separates key=value pairs, quotes/backslash need
    literal escaping, percent triggers strftime expansion)."""
    return (
        text.replace('\\', '\\\\')
            .replace(':', '\\:')
            .replace("'", "\u2019")   # swap to a plain apostrophe look-alike, simplest safe fix
            .replace('%', '\\%')
    )


@Client.on_message(filters.private & filters.command("set_watermark"))
async def set_watermark_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>Usage:</b> <code>/set_watermark Your Text</code>\n\n"
            f"{E_TIP} Then use <code>/watermark_position bottom-right</code> to choose where "
            f"it's burned in. Positions: <code>top-left</code>, <code>top-right</code>, "
            f"<code>bottom-left</code>, <code>bottom-right</code>, <code>center</code> "
            f"(default: bottom-right).</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    text = message.text.split(" ", 1)[1]
    await db.set_watermark(message.from_user.id, text)
    await message.reply_text(f"<b>{E_CHECK} Watermark text saved:</b> <code>{text}</code>",
                              parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("see_watermark"))
async def see_watermark_cmd(client: Client, message: Message):
    text = await db.get_watermark(message.from_user.id)
    position = await db.get_watermark_position(message.from_user.id)
    if text:
        await message.reply_text(
            f"<b>{E_GEAR} Watermark:</b> <code>{text}</code>\n"
            f"<b>Position:</b> {POSITION_LABELS.get(position, position)}",
            parse_mode=enums.ParseMode.HTML
        )
    else:
        await message.reply_text(f"<b>{E_WARN} No watermark set.</b> Use <code>/set_watermark</code>.",
                                  parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("del_watermark"))
async def del_watermark_cmd(client: Client, message: Message):
    await db.set_watermark(message.from_user.id, None)
    await message.reply_text(f"<b>{E_TRASH} Watermark removed.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("watermark_position"))
async def watermark_position_cmd(client: Client, message: Message):
    if len(message.command) < 2 or message.command[1].lower() not in POSITIONS:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/watermark_position bottom-right</code>\n\n"
            f"Options: {', '.join(f'<code>{p}</code>' for p in POSITIONS)}",
            parse_mode=enums.ParseMode.HTML
        )
    position = message.command[1].lower()
    await db.set_watermark_position(message.from_user.id, position)
    await message.reply_text(f"<b>{E_CHECK} Watermark position set to</b> {POSITION_LABELS[position]}",
                              parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("apply_watermark"))
async def apply_watermark_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    text = await db.get_watermark(user_id)
    if not text:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>No watermark text set!</b>\n\n"
            f"Use <code>/set_watermark Your Text</code> first, then reply to a video with "
            f"<code>/apply_watermark</code>.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    replied = message.reply_to_message
    media = replied and (replied.video or replied.document)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a video with <code>/apply_watermark</code>.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    if replied.document:
        fname = replied.document.file_name or ""
        if not fname.lower().endswith(VIDEO_EXTS):
            return await message.reply_text(f"<b>{E_CROSS} Not a video file!</b>",
                                             parse_mode=enums.ParseMode.HTML)

    position = await db.get_watermark_position(user_id)
    pos_expr = POSITIONS.get(position, POSITIONS["bottom-right"])

    orig_name = (replied.video and replied.video.file_name) or \
                (replied.document and replied.document.file_name) or f"video_{replied.id}.mp4"
    base_name, ext = os.path.splitext(orig_name)
    ext = ext or ".mp4"

    status = await message.reply_text(f"<b>{E_STAMP} Downloading video...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "watermark", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    in_path = os.path.join(temp_dir, orig_name)
    out_path = os.path.join(temp_dir, base_name + "_watermarked" + ext)

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    duration, _, _ = await asyncio.to_thread(get_video_metadata, in_path)

    escaped = _escape_drawtext(text)
    drawtext = (
        f"drawtext=text='{escaped}':fontcolor=white:fontsize=28:"
        f"box=1:boxcolor=black@0.5:boxborderw=6:{pos_expr}"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", in_path,
        "-vf", drawtext, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy", out_path,
    ]
    parse_line = make_ffmpeg_progress_parser(duration or 0, title="Applying Watermark...")
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, "Applying Watermark...", parse_line,
        user_id=message.from_user.id, queue_label="Watermark",
    )

    if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await status.edit_text(
            f"<b>{E_CROSS} Watermarking failed.</b>\n\n<code>{tail[-300:]}</code>",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        os.remove(in_path)
    except Exception:
        pass

    await upload_file(client, message, out_path, status, f"<b>{base_name}{ext}</b>\n\n{E_STAMP} Watermarked", file_name=f"{base_name}{ext}")

    shutil.rmtree(temp_dir, ignore_errors=True)
