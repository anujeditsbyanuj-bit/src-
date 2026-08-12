import re

from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN   = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_PENCIL = '<tg-emoji emoji-id="5395444784611480792">✏️</tg-emoji>'
E_TRASH  = '<tg-emoji emoji-id="5260293700088511294">🗑</tg-emoji>'
E_TIP    = '<tg-emoji emoji-id="5422439311196834318">💡</tg-emoji>'
E_INFO   = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'
E_GEAR   = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

# ---------------------------------------------------------------------------
# Shared caption-template engine (used by /set_caption, /see_caption, the
# AK Manager "Caption" button, and every upload path that honours the
# user's custom caption: start.py, rename.py, audio_select.py).
#
# Supported placeholders — kept in parity with the reference forward-bot's
# caption template: {filename} {size} {caption} {year} {language} {quality}
# {type}
# ---------------------------------------------------------------------------

CAPTION_PLACEHOLDERS_HELP = (
    f"{E_INFO} <b>ᴀᴠᴀɪʟᴀʙʟᴇ ᴠᴀʀɪᴀʙʟᴇs:</b>\n"
    f"• <code>{{filename}}</code> — File Name\n"
    f"• <code>{{size}}</code> — File Size\n"
    f"• <code>{{caption}}</code> — Original Caption\n"
    f"• <code>{{year}}</code> — Year extracted from filename\n"
    f"• <code>{{language}}</code> — Audio language detected from filename\n"
    f"• <code>{{quality}}</code> — Video quality (480p, 720p, 1080p, etc)\n"
    f"• <code>{{type}}</code> — Media type (Video, Audio, Document, Photo)\n\n"
    f"{E_WARN} <i>Variables only work on videos, audio and documents. Photos will keep their original caption.</i>\n\n"
    f"{E_GEAR} <b>ʜᴛᴍʟ ғᴏʀᴍᴀᴛᴛɪɴɢ:</b>\n"
    f"• <code>&lt;b&gt;bold&lt;/b&gt;</code>\n"
    f"• <code>&lt;i&gt;italic&lt;/i&gt;</code>\n"
    f"• <code>&lt;u&gt;underline&lt;/u&gt;</code>\n"
    f"• <code>&lt;s&gt;strike&lt;/s&gt;</code>\n"
    f"• <code>&lt;code&gt;monospace&lt;/code&gt;</code>\n"
    f"• <code>&lt;spoiler&gt;spoiler&lt;/spoiler&gt;</code>\n"
    f"• <code>&lt;a href='url'&gt;link text&lt;/a&gt;</code>\n\n"
    f"{E_TIP} <b>ᴇxᴀᴍᴘʟᴇ ᴄᴀᴘᴛɪᴏɴ:</b>\n"
    f"<code>&lt;b&gt;{{filename}}&lt;/b&gt;\n"
    f"📊 Size: {{size}}\n"
    f"🎬 Quality: {{quality}}\n"
    f"📅 Year: {{year}}\n"
    f"🗣 Language: {{language}}</code>"
)

_LANG_PATTERNS = {
    'Hindi': r'\b(Hindi|HIN|हिंदी)\b',
    'English': r'\b(English|ENG)\b',
    'Tamil': r'\b(Tamil|TAM)\b',
    'Telugu': r'\b(Telugu|TEL)\b',
    'Malayalam': r'\b(Malayalam|MAL)\b',
    'Kannada': r'\b(Kannada|KAN)\b',
    'Bengali': r'\b(Bengali|BEN)\b',
    'Marathi': r'\b(Marathi|MAR)\b',
    'Punjabi': r'\b(Punjabi|PUN)\b',
    'Multi Audio': r'\b(Multi[- ]?Audio|Dual[- ]?Audio)\b',
}


def extract_caption_metadata(file_name: str):
    """Return (year, language, quality) parsed out of a filename, matching
    the reference bot's detection so caption templates behave identically."""
    year, language, quality = 'N/A', 'N/A', 'N/A'
    if not file_name:
        return year, language, quality

    year_match = re.search(r'\b(19|20)\d{2}\b', file_name)
    if year_match:
        year = year_match.group()

    quality_match = re.search(r'\b(144|240|360|480|720|1080|1440|2160|4320)p?\b', file_name, re.IGNORECASE)
    if quality_match:
        quality = quality_match.group()
        if not quality.lower().endswith('p'):
            quality += 'p'

    found_langs = [lang for lang, pattern in _LANG_PATTERNS.items() if re.search(pattern, file_name, re.IGNORECASE)]
    if found_langs:
        language = ' + '.join(found_langs)

    return year, language, quality


def render_caption(template: str, *, filename: str = "", size: str = "", caption: str = "",
                    media_type: str = "") -> str:
    """Fill a user's custom caption template with every supported variable.
    Falls back gracefully (never raises) if the template only uses a subset,
    or uses an unknown placeholder."""
    year, language, quality = extract_caption_metadata(filename)
    try:
        rendered = template.format(
            filename=filename, size=size, caption=caption or "",
            year=year, language=language, quality=quality, type=media_type,
        )
    except (KeyError, IndexError):
        try:
            rendered = template.format(filename=filename, size=size, caption=caption or "")
        except (KeyError, IndexError):
            rendered = template
    return f"<blockquote>{rendered}</blockquote>"

@Client.on_message(filters.command("set_caption") & filters.private)
async def set_caption(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>ᴜsᴀɢᴇ ᴇʀʀᴏʀ</b>\n\n"
            f"{E_TIP} Please provide the caption text after the command.\n\n"
            f"{E_PENCIL} <b>ᴄᴏʀʀᴇᴄᴛ ғᴏʀᴍᴀᴛ:</b>\n"
            f"<code>/set_caption Your Caption Here</code>\n\n"
            f"{CAPTION_PLACEHOLDERS_HELP}</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    caption = message.text.split(" ", 1)[1].strip()
    try:
        caption.format(filename="", size="", caption="", year="", language="", quality="", type="")
    except (KeyError, IndexError) as e:
        return await message.reply_text(
            f"<blockquote>{E_CROSS} <b>ɪɴᴠᴀʟɪᴅ ᴠᴀʀɪᴀʙʟᴇ:</b> <code>{e}</code>\n\n"
            f"{CAPTION_PLACEHOLDERS_HELP}</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    await db.set_caption(user_id, caption)
    await message.reply_text(
        f"<blockquote>{E_CHECK} <b>ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ sᴀᴠᴇᴅ!</b>\n\n"
        f"{E_PENCIL} <b>ᴘʀᴇᴠɪᴇᴡ:</b>\n<code>{caption}</code>\n\n"
        f"<i>{E_INFO} This caption will be applied to your future downloads.</i></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("see_caption") & filters.private)
async def see_caption(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    caption = await db.get_caption(user_id)
    if caption:
        preview = render_caption(
            caption, filename="Video_File_2024.mp4", size="1.2 GB",
            caption="Original caption text", media_type="Video",
        )
        await message.reply_text(
            f"<blockquote>{E_PENCIL} <b>ʏᴏᴜʀ ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ</b>\n\n"
            f"<code>{caption}</code>\n\n"
            f"{E_INFO} <b>ᴘʀᴇᴠɪᴇᴡ:</b>\n{preview}\n\n"
            f"<i>{E_TRASH} To delete this, use /del_caption</i></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    else:
        await message.reply_text(
            f"<blockquote>{E_CROSS} <b>ɴᴏ ᴄᴀᴘᴛɪᴏɴ sᴇᴛ</b>\n\n"
            f"{E_INFO} You are currently using the default bot caption.\n"
            f"<i>{E_TIP} Use /set_caption to customize it.</i></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

@Client.on_message(filters.command("del_caption") & filters.private)
async def del_caption(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    caption = await db.get_caption(user_id)
    if not caption:
        return await message.reply_text(
            f"<b>{E_WARN} No Caption Found.</b>\n<i>You don't have a custom caption set.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    await db.del_caption(user_id)
    await message.reply_text(
        f"<blockquote>{E_TRASH} <b>ᴄᴜsᴛᴏᴍ ᴄᴀᴘᴛɪᴏɴ ʀᴇᴍᴏᴠᴇᴅ</b>\n\n"
        f"<i>{E_INFO} Your uploads will now use the default bot caption.</i></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )
