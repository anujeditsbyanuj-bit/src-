# Download History & Favourite System
#
# /history       — shows the user's most recent downloads (any downloader
#                   that calls db.add_download_history() shows up here —
#                   currently wired into ytdl.py and mxplayer.py).
# /favourites     — lists saved links.
# /fav <url>      — save a link as a favourite. Also accepts a number
#                   (e.g. /fav 3) to save the 3rd item from /history instead
#                   of retyping the URL.
# /unfav <url|n>  — remove a favourite, by URL or by its position in
#                   /favourites.

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO

HISTORY_LIMIT = 15


def _fmt_ts(dt) -> str:
    try:
        return dt.strftime("%d %b, %H:%M")
    except Exception:
        return "unknown time"


@Client.on_message(filters.command(["history", "downloads"]) & filters.private)
async def history_command(client: Client, message: Message):
    items = await db.get_download_history(message.from_user.id, limit=HISTORY_LIMIT)
    if not items:
        return await message.reply_text(
            f"<b>{E_INFO} No downloads yet.</b> Send a link to download something first.",
            parse_mode=enums.ParseMode.HTML,
        )

    lines = [f"<b>⏰ Your last {len(items)} download(s):</b>", ""]
    for i, entry in enumerate(items, 1):
        title = (entry.get("title") or "Unknown")[:60]
        kind = entry.get("type", "file")
        quality = entry.get("quality")
        meta = f" · {quality}" if quality else ""
        when = _fmt_ts(entry.get("at"))
        lines.append(f"<b>{i}.</b> {title} <i>({kind}{meta})</i>\n    <i>{when}</i>")
    lines.append("")
    lines.append("<i>Tip: /fav &lt;number&gt; saves that one to your favourites.</i>")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.command(["favourites", "favorites", "favs"]) & filters.private)
async def favourites_command(client: Client, message: Message):
    favs = await db.get_favourites(message.from_user.id)
    if not favs:
        return await message.reply_text(
            f"<b>{E_INFO} No favourites saved yet.</b>\n"
            f"Use <code>/fav &lt;link&gt;</code> to save one, or <code>/fav &lt;number&gt;</code> "
            f"after checking /history.",
            parse_mode=enums.ParseMode.HTML,
        )

    lines = [f"<b>⭐️ Your favourites ({len(favs)}):</b>", ""]
    for i, f in enumerate(favs, 1):
        title = (f.get("title") or f.get("url"))[:60]
        lines.append(f"<b>{i}.</b> {title}\n    <code>{f.get('url')}</code>")
    lines.append("")
    lines.append("<i>Remove one with /unfav &lt;number&gt;.</i>")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.command("fav") & filters.private)
async def fav_add_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/fav &lt;link&gt;</code> or <code>/fav &lt;history number&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1].strip()
    user_id = message.from_user.id

    url, title = None, None
    if arg.isdigit():
        history = await db.get_download_history(user_id, limit=HISTORY_LIMIT)
        idx = int(arg)
        if idx < 1 or idx > len(history):
            return await message.reply_text(
                f"<b>{E_CROSS} No history item #{idx}.</b> Check /history for valid numbers.",
                parse_mode=enums.ParseMode.HTML,
            )
        entry = history[idx - 1]
        url, title = entry.get("url"), entry.get("title")
    else:
        url = arg

    if not url:
        return await message.reply_text(
            f"<b>{E_CROSS} That history item has no saved link.</b>", parse_mode=enums.ParseMode.HTML
        )

    added = await db.add_favourite(user_id, url, title)
    if added:
        await message.reply_text(f"<b>{E_CHECK} Saved to favourites.</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"<b>{E_INFO} Already in your favourites.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("unfav") & filters.private)
async def unfav_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/unfav &lt;link&gt;</code> or <code>/unfav &lt;number&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1].strip()
    user_id = message.from_user.id

    if arg.isdigit():
        removed = await db.remove_favourite_by_index(user_id, int(arg))
    else:
        ok = await db.remove_favourite(user_id, arg)
        removed = {"url": arg} if ok else None

    if removed:
        await message.reply_text(f"<b>{E_CHECK} Removed from favourites.</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"<b>{E_CROSS} Couldn't find that favourite.</b>", parse_mode=enums.ParseMode.HTML)
