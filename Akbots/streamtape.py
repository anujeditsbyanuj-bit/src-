import re
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    DEFAULT_HEADERS, E_CHECK, E_CROSS, E_INFO
)
from Akbots.link_cache import try_send_cached

PATTERN = re.compile(r"(https?://)?(www\.)?(streamtape\.\w+|stape\.\w+)/\S+", re.IGNORECASE)


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


async def _extract_direct_url(link: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(link, headers=DEFAULT_HEADERS) as resp:
            html = await resp.text()

    url = None

    # Method 1: direct link pattern in page source (works most of the time)
    m = re.search(r"getElementById\('norobotlink'\)\.href\s*=\s*['\"]([^'\"]+)", html)
    if m:
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url

    # Method 2: token-based extraction — StreamTape sometimes serves the page
    # without the norobotlink script but still exposes a get_video token.
    if not url:
        token_m = re.search(r"token=([a-zA-Z0-9_]+)", html)
        if token_m:
            url = f"https://streamtape.com/get_video?id={token_m.group(1)}"

    # Method 3: obfuscated link construction — last resort, grabs the final
    # /<digits>/<name> style path StreamTape's JS assembles client-side.
    if not url:
        parts = re.findall(r"(/\d+/[^'\"]+)", html)
        if parts:
            url = "https://streamtape.com" + parts[-1]

    if not url:
        raise ValueError("Could not extract StreamTape direct link. Video may be removed.")

    title_m = re.search(r'<title>([^<]+)</title>', html)
    filename = title_m.group(1).strip() if title_m else "streamtape_video"
    filename = re.sub(r'[^\w\s\-.]', '', filename).strip()
    if not filename.lower().endswith(('.mp4', '.mkv', '.avi', '.webm')):
        filename += '.mp4'
    return url, filename


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} StreamTape link detected...</b>", parse_mode=enums.ParseMode.HTML)
    if await try_send_cached(client, message, url, status):
        return
    try:
        direct_url, filename = await _extract_direct_url(url)
        filename = safe_filename(filename, "streamtape_video.mp4")

        # aria2c gives resumable, multi-connection downloads — if a transfer
        # drops partway, retrying continues from the .aria2 control file
        # instead of restarting. Falls back to the plain aiohttp streamer
        # (single connection, no resume) if aria2c isn't installed.
        from Akbots.torrent import _aria2c_available
        if _aria2c_available():
            import os
            import shutil
            from Akbots.aria2_dl import aria2c_download
            # message.id is only unique WITHIN a single chat, not globally, so
            # two users whose messages happen to share an id would otherwise
            # collide; include chat.id to keep folders globally unique.
            folder = os.path.join("downloads", "streamtape", f"task_{message.chat.id}_{message.id}")
            try:
                dest = await aria2c_download(direct_url, folder, status,
                                              label="Downloading from StreamTape (resumable)",
                                              out_name=filename,
                                              user_id=message.from_user.id, queue_label="StreamTape download")
                await upload_file(client, message, dest, status,
                                   f"<b>{E_CHECK} StreamTape Video</b>\n<code>{filename}</code>", file_name=filename, cache_url=url)
            finally:
                shutil.rmtree(folder, ignore_errors=True)
            return

        folder = make_output_folder("streamtape")
        dest = f"{folder}/{message.id}_{filename}"
        await stream_download(direct_url, dest, status, "Downloading from StreamTape", user_id=message.from_user.id, file_name=filename)
        await upload_file(client, message, dest, status, f"<b>{E_CHECK} StreamTape Video</b>\n<code>{filename}</code>", file_name=filename, cache_url=url)
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def streamtape_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("stape") & filters.private)
async def streamtape_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/stape &lt;streamtape URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)
