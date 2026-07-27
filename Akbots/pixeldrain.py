import re
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    E_CHECK, E_CROSS, E_INFO
)
from Akbots.link_cache import try_send_cached

PATTERN = re.compile(r"(https?://)?(www\.)?pixeldrain\.com/[ul]/\S+", re.IGNORECASE)


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


def _parse(link: str):
    m = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', link)
    if m:
        return "file", m.group(1)
    m = re.search(r'pixeldrain\.com/l/([a-zA-Z0-9]+)', link)
    if m:
        return "list", m.group(1)
    return None, None


async def _download_one(client: Client, message: Message, status, file_id: str, idx=None, total=None, cache_url=None):
    info_url = f"https://pixeldrain.com/api/file/{file_id}/info"
    api_url = f"https://pixeldrain.com/api/file/{file_id}"

    filename = f"pixeldrain_{file_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(info_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    filename = safe_filename(data.get("name"), filename)
    except Exception:
        pass

    folder = make_output_folder("pixeldrain")
    dest = f"{folder}/{message.id}_{file_id}_{filename}"
    label = f"Downloading from Pixeldrain" + (f" ({idx}/{total})" if total else "")
    await stream_download(api_url, dest, status, label, user_id=message.from_user.id, file_name=filename)
    await upload_file(client, message, dest, status, f"<b>{E_CHECK} Pixeldrain File</b>\n<code>{filename}</code>", file_name=filename, cache_url=cache_url)


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} Pixeldrain link detected...</b>", parse_mode=enums.ParseMode.HTML)
    url_type, item_id = _parse(url)
    if not url_type:
        return await status.edit_text(f"<b>{E_CROSS} Invalid Pixeldrain link.</b>", parse_mode=enums.ParseMode.HTML)

    try:
        if url_type == "file":
            if await try_send_cached(client, message, url, status):
                return
            await _download_one(client, message, status, item_id, cache_url=url)
        else:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://pixeldrain.com/api/list/{item_id}") as resp:
                    if resp.status != 200:
                        raise ValueError(f"Could not fetch list (HTTP {resp.status})")
                    data = await resp.json()
            files = data.get("files", [])
            if not files:
                return await status.edit_text(f"<b>{E_CROSS} Empty Pixeldrain list.</b>", parse_mode=enums.ParseMode.HTML)
            for i, f in enumerate(files, 1):
                status = await message.reply_text(f"<b>{E_INFO} Downloading file {i}/{len(files)}...</b>", parse_mode=enums.ParseMode.HTML)
                await _download_one(client, message, status, f.get("id"), i, len(files))
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def pixeldrain_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command(["pixeldrain", "pd"]) & filters.private)
async def pixeldrain_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/pd &lt;pixeldrain URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)
