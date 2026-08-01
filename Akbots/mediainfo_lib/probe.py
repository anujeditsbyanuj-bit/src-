# Akbots - Don't Remove Credit - @AkBots_Official
#
# Ported from Mediainfo-Bot-master/core/mediainfo.py. Runs `mediainfo` (if
# installed) or falls back to `ffprobe` (bundled with the ffmpeg apt
# package Akbots already installs) against a URL — in practice the local
# streamer.py URL — so the tool does its own Range-request seeking and
# only the header/footer bytes it needs get pulled from Telegram.

import asyncio
import json
import shutil


async def extract_mediainfo(url: str) -> dict:
    if shutil.which("mediainfo"):
        cmd = ["mediainfo", "--Output=JSON", url]
        return await _run_command(cmd)

    if shutil.which("ffprobe"):
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            url,
        ]
        return await _run_command(cmd)

    raise Exception("Neither 'mediainfo' nor 'ffprobe' was found on the system.")


async def _run_command(cmd) -> dict:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode().strip() or "Unknown error"
        raise Exception(f"Command error: {error_msg}")

    return json.loads(stdout.decode())
