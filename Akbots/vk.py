# VK.com (VKontakte) Video Support
#
# vk.com/vk.ru video and clip links were never excluded from the other
# plugins' domain lists, so they always ended up falling all the way through
# to ytdl.py's *generic* yt-dlp fallback (group=2) — the same tier as "any
# random site yt-dlp happens to support", with no dedicated command, no
# priority over the headless-browser probe, and no VK-specific cookie
# support for login-walled/age-restricted videos.
#
# This plugin promotes VK to a proper first-class integration, the same way
# Instagram/Pinterest/YouTube already are in Akbots/ytdl.py — it just reuses
# that module's existing yt-dlp quality picker rather than duplicating it.

import re
from pyrogram import Client, filters, enums
from pyrogram.types import Message

E_INFO = '<emoji id=5334544901428229844>ℹ️</emoji>'

# Matches vk.com/vk.ru video pages (video-12345_67890, video12345_67890) and
# short clip links (vk.com/clip-123_456), with or without scheme/www/mobile
# subdomain.
PATTERN = re.compile(
    r"(https?://)?(www\.|m\.)?(vk\.com|vk\.ru)/(video|clip)[\-\w]*\S*",
    re.IGNORECASE,
)


def extract_vk_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


@Client.on_message(
    filters.text & filters.private & filters.regex(PATTERN) & ~filters.regex(r"^/"),
    group=1,  # same priority as the other dedicated site handlers in ytdl.py
)
async def vk_auto_detect(client: Client, message: Message):
    from Akbots.ytdl import _show_quality_picker
    url = extract_vk_url(message.text)
    if url:
        await _show_quality_picker(client, message, url)


@Client.on_message(filters.command(["vk"]) & filters.private)
async def vk_command(client: Client, message: Message):
    from Akbots.ytdl import _show_quality_picker
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/vk &lt;vk.com or vk.ru video/clip link&gt;</code>\n"
            f"<i>For private/age-restricted videos, set VK_COOKIES in config.py "
            f"to a Netscape-format cookies.txt exported while logged in.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    raw = message.text.split(None, 1)[1].strip()
    url = extract_vk_url(raw) or raw
    await _show_quality_picker(client, message, url)
