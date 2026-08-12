# Akbots - Don't Remove Credit - @AkBots_Official
#
# /tohls — reply to a video (or a document that's a video file) and this
# downloads it, converts it into an adaptive-bitrate HLS package via
# Akbots/v2hls_converter.py (ported from Video-to-HLS's main.py — see
# that module's docstring for what was and wasn't kept), and replies
# with a streaming link served locally through Akbots/v2hls_routes.py
# (same public web server config.STREAM_URL already points at — no
# GitHub Pages / Internet Archive account or separate deploy needed).
#
# Auto-discovered by Pyrogram's plugin loader (plugins=dict(root="Akbots")
# in bot.py) — no manual registration needed. v2hls_routes.py's mounting
# onto the aiohttp app is registered separately, see bot.py.

import asyncio
import logging
import shutil
import time
import uuid
from pathlib import Path

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import safe_edit, E_CHECK, E_CROSS, make_download_progress
from Akbots import v2hls_converter
from Akbots.meow_downloader import find_ffmpeg
from config import STREAM_URL

logger = logging.getLogger(__name__)

E_GEAR = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

# Where converted packages live on disk — Akbots/v2hls_routes.py serves
# straight out of here, one subfolder per job.
OUTPUT_ROOT = Path("data/v2hls_output")
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

# Job folders older than this get swept on every new /tohls run — nothing
# here is meant to be permanent storage, just enough to hand someone a
# working link for a while.
_JOB_TTL = 24 * 3600


def _cleanup_old_jobs():
    if not OUTPUT_ROOT.exists():
        return
    cutoff = time.time() - _JOB_TTL
    for job_dir in OUTPUT_ROOT.iterdir():
        try:
            if job_dir.is_dir() and job_dir.stat().st_mtime < cutoff:
                shutil.rmtree(job_dir, ignore_errors=True)
        except OSError:
            pass


@Client.on_message(filters.private & filters.command("tohls"))
async def tohls_cmd(client: Client, message: Message):
    target = message.reply_to_message
    media = target and (target.video or target.document or target.animation)
    if not media:
        return await message.reply_text(
            f"<b>{E_GEAR} Reply to a video with</b> <code>/tohls</code>\n"
            f"<i>Converts it into an adaptive HLS stream (multi-quality, multi-audio, subtitles) "
            f"and gives you back a streaming link.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    if not find_ffmpeg():
        return await message.reply_text(
            f"<b>{E_CROSS} ffmpeg isn't available on this host.</b> /tohls needs it to encode the renditions.",
            parse_mode=enums.ParseMode.HTML
        )

    _cleanup_old_jobs()
    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_path = None
    status = await message.reply_text(f"<b>{E_GEAR} Downloading from Telegram...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        input_path = await target.download(
            file_name=str(job_dir / "source"),
            progress=make_download_progress(status, file_name="source video")
        )

        def _on_progress(text: str):
            # Called from the ffmpeg-running worker thread — bounce the
            # edit back onto the bot's event loop, same pattern
            # meow_downloader._make_progress_hook uses for yt-dlp.
            asyncio.run_coroutine_threadsafe(
                safe_edit(status.edit_text, f"<b>{E_GEAR} {text}</b>", parse_mode=enums.ParseMode.HTML),
                loop,
            )

        loop = asyncio.get_event_loop()
        master_path = await asyncio.to_thread(
            v2hls_converter.convert_to_hls, Path(input_path), job_dir, on_progress=_on_progress
        )

        stream_link = f"{STREAM_URL.rstrip('/')}/v2hls/{job_id}/master.m3u8"
        await safe_edit(status.edit_text,
            f"<b>{E_CHECK} HLS ready.</b>\n\n"
            f"🔗 <b>Stream link:</b>\n<code>{stream_link}</code>\n\n"
            f"<i>Open it in VLC, or embed with hls.js — see Video-to-HLS's README for a basic example.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"v2hls_commands: conversion failed for job {job_id}: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Conversion failed.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        shutil.rmtree(job_dir, ignore_errors=True)
    finally:
        # The raw downloaded source file isn't needed once encoding's
        # done (or failed) — only the HLS output under job_dir should stick
        # around for v2hls_routes.py to serve.
        if input_path and Path(input_path).exists():
            try:
                Path(input_path).unlink()
            except OSError:
                pass
