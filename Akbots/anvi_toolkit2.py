# Akbots - Don't Remove Credit - @AkBots_Official
#
# Second batch of small fun/utility commands ported from Anvi, same
# treatment as anvi_toolkit.py: rewritten async, blocking `requests` swapped
# for aiohttp. Left out of this batch (not ported):
#   - antinsfw.py — depends on Anvi's own private NSFW-scan backend
#     (Anvi.state.arq), which isn't something we have access to.
#   - karma.py — needs its own per-chat on/off toggle + db wiring, better
#     done as its own piece later than folded in here.
#   - goodnight.py's original hardcoded Telegram sticker file_ids dropped —
#     those were captured by Anvi's own bot account and won't resolve for
#     a different bot's API session, so /gn here is emoji-only.

import random
import re
from datetime import datetime

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from logger import LOGGER

logger = LOGGER(__name__)


@Client.on_message(filters.command(["hashtag", "hastag"]))
async def hashtag_gen(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>Usage:</b> <code>/hashtag python</code>", parse_mode=enums.ParseMode.HTML
        )
    keyword = message.text.split(None, 1)[1]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://all-hashtag.com/library/contents/ajax_generator.php",
                data={"keyword": keyword, "filter": "top"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                html = await resp.text()
        match = re.search(r'class="copy-hashtags"[^>]*>([^<]*)<', html)
        content = match.group(1).strip() if match else None
    except Exception as e:
        logger.debug(f"anvi_toolkit2 /hashtag failed: {e}")
        content = None

    if not content:
        return await message.reply_text("Couldn't generate hashtags right now — try again later.")
    await message.reply_text(f"<b>Hashtags for \"{keyword}\":</b>\n<pre>{content}</pre>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("code"))
async def hex_code(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>Usage:</b> <code>/code Hello World!</code> (or a hex string to decode)",
            parse_mode=enums.ParseMode.HTML,
        )
    input_text = message.text.split(None, 1)[1]

    hex_repr = " ".join(format(ord(c), "x") for c in input_text)
    try:
        decoded = bytes.fromhex(input_text.replace(" ", "")).decode("utf-8")
    except Exception:
        decoded = "(not valid hex)"

    await message.reply_text(
        f"<b>Input:</b> {input_text}\n<b>Hex:</b> <code>{hex_repr}</code>\n<b>Decoded (if input was hex):</b> {decoded}",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("day"))
async def day_of_week(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>Usage:</b> <code>/day 1947-08-15</code>", parse_mode=enums.ParseMode.HTML
        )
    try:
        date_obj = datetime.strptime(message.command[1].strip(), "%Y-%m-%d")
    except ValueError as e:
        return await message.reply_text(f"Couldn't parse that date: {e}")
    await message.reply_text(f"{message.command[1].strip()} was a **{date_obj.strftime('%A')}**.")


_DICE_EMOJIS = {"dice": "🎲", "dart": "🎯", "basket": "🏀", "jackpot": "🎰", "bowling": "🎳", "football": "⚽"}


@Client.on_message(filters.command(list(_DICE_EMOJIS.keys())))
async def dice_game(client: Client, message: Message):
    emoji = _DICE_EMOJIS[message.command[0].lower()]
    result = await client.send_dice(message.chat.id, emoji)
    await message.reply_text(f"{message.from_user.mention} scored: **{result.dice.value}**", quote=True)


@Client.on_message(filters.command(["whatsapp", "wa"]))
async def whatsapp_link(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>Usage:</b> <code>/whatsapp +1234567890</code>", parse_mode=enums.ParseMode.HTML
        )
    phone = message.command[1]
    await message.reply_text(f"https://wa.me/{phone.lstrip('+')}")


@Client.on_message(filters.command("bored"))
async def bored(client: Client, message: Message):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://apis.scrimba.com/bored/api/activity", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        activity = data.get("activity")
    except Exception as e:
        logger.debug(f"anvi_toolkit2 /bored failed: {e}")
        activity = None
    await message.reply_text(f"Feeling bored? How about:\n\n{activity}" if activity else "Couldn't fetch an activity right now.")


_GN_EMOJIS = ["😴", "😪", "💤", "🌙", "✨"]


@Client.on_message(filters.command(["gn", "goodnight"]))
async def goodnight(client: Client, message: Message):
    await message.reply_text(random.choice(_GN_EMOJIS))
