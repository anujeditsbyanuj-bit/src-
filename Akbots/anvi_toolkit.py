# Akbots - Don't Remove Credit - @AkBots_Official
#
# Small utility commands ported from Anvi (github.com/.../Anvi) — the parts
# of Anvi already on Pyrogram, rewritten as async handlers to match the rest
# of Akbotz (Anvi's originals were sync `def` handlers and used blocking
# `requests` calls, which would stall Akbotz's single event loop; both
# fixed here). Anvi plugins that duplicated something Akbotz already has
# were skipped rather than ported: telegraph.py and pypi.py (both already
# covered by Akbots/misc_tools.py's /tgm and /pypi) and whois.py (covered
# by Akbots/userinfo.py's /info).
#
# All deps (qrcode, requests, pillow) are already in requirements.txt.

import io
import random

import aiohttp
import qrcode
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from logger import LOGGER

logger = LOGGER(__name__)


@Client.on_message(filters.command("qr"))
async def qr_handler(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>ᴜsᴀɢᴇ:</b> <code>/qr your text or link here</code>", parse_mode=enums.ParseMode.HTML
        )
    text = message.text.split(None, 1)[1]

    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    buf.name = "qrcode.png"
    img.save(buf, format="PNG")
    buf.seek(0)
    await message.reply_photo(buf, caption="<blockquote>Here's your QR code.</blockquote>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("ip"))
async def ip_info(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            "<b>ᴜsᴀɢᴇ:</b> <code>/ip 8.8.8.8</code>", parse_mode=enums.ParseMode.HTML
        )
    ip_address = message.command[1]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.safone.dev/ipinfo?ip={ip_address}", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
    except Exception as e:
        logger.debug(f"anvi_toolkit /ip failed: {e}")
        return await message.reply_text("Couldn't fetch info for that IP right now.")

    if not data or "ip" not in data:
        return await message.reply_text("Unable to fetch information for the provided IP address.")

    await message.reply_text(
        f"<b>ɪᴘ:</b> <code>{data.get('ip')}</code>\n"
        f"<b>ᴄᴏᴜɴᴛʀʏ:</b> {data.get('country', 'N/A')}\n"
        f"<b>ᴄɪᴛʏ:</b> {data.get('city', 'N/A')}\n"
        f"<b>ɪsᴘ:</b> {data.get('isp', 'N/A')}",
        parse_mode=enums.ParseMode.HTML,
    )


_PW_CHARSET = "ASDFGHJKLZXCVBNMQWERTYUIOPabcdefghijklmnopqrstuvwxyz1234567890!@#$%^&*()_+"


@Client.on_message(filters.command(["genpassword", "genpw"]))
async def gen_password(client: Client, message: Message):
    if len(message.command) > 1 and message.command[1].isdigit():
        length = max(4, min(int(message.command[1]), 64))
    else:
        length = random.choice([8, 10, 12, 14, 16])
    pw = "".join(random.sample(_PW_CHARSET, min(length, len(_PW_CHARSET))))
    # sample() can't exceed charset length (75 chars) — pad if a longer length was requested
    while len(pw) < length:
        pw += random.choice(_PW_CHARSET)
    await message.reply_text(
        f"<b>ʟᴇɴɢᴛʜ:</b> {length}\n<b>ᴘᴀssᴡᴏʀᴅ:</b> <code>{pw}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("weather"))
async def weather(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>ᴜsᴀɢᴇ:</b> <code>/weather London</code>", parse_mode=enums.ParseMode.HTML
        )
    location = message.text.split(None, 1)[1].strip()
    await message.reply_photo(
        f"https://wttr.in/{location}.png",
        caption=f"<blockquote>Weather for {location}</blockquote>", parse_mode=enums.ParseMode.HTML,
    )
