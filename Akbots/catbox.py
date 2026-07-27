import re
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    E_CHECK, E_CROSS, E_INFO
)
from Akbots.link_cache import try_send_cached

PATTERN = re.compile(r"(https?://)?(files\.catbox\.moe|litterbox\.catbox\.moe)/\S+", re.IGNORECASE)


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} Catbox link detected...</b>", parse_mode=enums.ParseMode.HTML)
    if await try_send_cached(client, message, url, status):
        return
    folder = make_output_folder("catbox")
    filename = safe_filename(url.split("/")[-1].split("?")[0], "catbox_file")
    dest = f"{folder}/{message.id}_{filename}"
    try:
        await stream_download(url, dest, status, "Downloading from Catbox", user_id=message.from_user.id, file_name=filename)
        await upload_file(client, message, dest, status, f"<b>{E_CHECK} Catbox File</b>\n<code>{filename}</code>", file_name=filename, cache_url=url)
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def catbox_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("catbox") & filters.private)
async def catbox_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/catbox &lt;catbox/litterbox URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)
