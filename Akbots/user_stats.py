# Akbots
# Daily usage stats — ported from Url-uploader-Bot-V4
# (Plugin/admin/user_stats_cmd.py + Plugin/database/user_stats_db.py).
# Tracks per-day uploaded/downloaded bytes and successful-file counts per
# user in MongoDB, exposed via /myuses, /totaluses (admin) and /useruses
# (admin). record_usage() is called from Akbots/direct_utils.py's
# upload_file() so every downloader that shares that helper is covered.
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from config import ADMINS
from Akbots.direct_utils import fmt_bytes

_stats_col = db.db["user_stats"]


def _today():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


async def record_usage(user_id: int, uploaded_bytes: int = 0, downloaded_bytes: int = 0, success_count: int = 0):
    """Increments today's usage counters for a user. Safe to call often —
    failures here must never break an actual upload/download."""
    try:
        await _stats_col.update_one(
            {"user_id": user_id, "date": _today()},
            {
                "$inc": {
                    "uploaded_bytes": uploaded_bytes,
                    "downloaded_bytes": downloaded_bytes,
                    "success_count": success_count,
                }
            },
            upsert=True,
        )
    except Exception:
        pass  # stats are best-effort; never let this break an upload


async def get_user_stats(user_id: int):
    return await _stats_col.find_one({"user_id": user_id, "date": _today()})


async def get_all_stats_today():
    cursor = _stats_col.find({"date": _today()})
    return await cursor.to_list(length=None)


@Client.on_message(filters.command("myuses") & filters.private)
async def my_uses(client: Client, message: Message):
    stats = await get_user_stats(message.from_user.id)
    if not stats:
        return await message.reply_text(
            "📊 Aaj aapka koi record nahi mila. Pehle kuch upload ya download karein."
        )

    uploaded = stats.get("uploaded_bytes", 0)
    downloaded = stats.get("downloaded_bytes", 0)
    total_files = stats.get("success_count", 0)

    text = (
        f"<b>📈 ᴀᴀᴘᴋᴀ ᴀᴀᴊ ᴋᴀ ᴜsᴀɢᴇ</b>\n\n"
        f"👤 <b>ᴜsᴇʀ:</b> {message.from_user.mention}\n"
        f"⬆️ <b>ᴜᴘʟᴏᴀᴅᴇᴅ:</b> <code>{fmt_bytes(uploaded)}</code>\n"
        f"⬇️ <b>ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ:</b> <code>{fmt_bytes(downloaded)}</code>\n"
        f"📦 <b>ᴛᴏᴛᴀʟ ᴜsᴀɢᴇ:</b> <code>{fmt_bytes(uploaded + downloaded)}</code>\n"
        f"🗂 <b>ғɪʟᴇs ᴜᴘʟᴏᴀᴅᴇᴅ:</b> <code>{total_files}</code>"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("totaluses") & filters.user(ADMINS))
async def total_uses(client: Client, message: Message):
    stats_list = await get_all_stats_today()
    if not stats_list:
        return await message.reply_text("📊 Aaj ke liye koi usage record nahi mila.")

    total_uploaded = sum(s.get("uploaded_bytes", 0) for s in stats_list)
    total_downloaded = sum(s.get("downloaded_bytes", 0) for s in stats_list)
    total_files = sum(s.get("success_count", 0) for s in stats_list)

    top3 = sorted(
        stats_list,
        key=lambda x: x.get("uploaded_bytes", 0) + x.get("downloaded_bytes", 0),
        reverse=True,
    )[:3]

    text = (
        f"<b>📊 ᴀʟʟ ᴜsᴇʀs (ᴛᴏᴅᴀʏ) — sᴜᴍᴍᴀʀʏ</b>\n\n"
        f"📤 <b>ᴛᴏᴛᴀʟ ᴜᴘʟᴏᴀᴅᴇᴅ:</b> <code>{fmt_bytes(total_uploaded)}</code>\n"
        f"📥 <b>ᴛᴏᴛᴀʟ ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ:</b> <code>{fmt_bytes(total_downloaded)}</code>\n"
        f"📦 <b>ᴛᴏᴛᴀʟ ᴄᴏᴍʙɪɴᴇᴅ:</b> <code>{fmt_bytes(total_uploaded + total_downloaded)}</code>\n"
        f"🗂 <b>ᴛᴏᴛᴀʟ ғɪʟᴇs ᴜᴘʟᴏᴀᴅᴇᴅ:</b> <code>{total_files}</code>\n\n"
        f"<b>🏆 ᴛᴏᴘ 3 ᴜsᴇʀs (ʙʏ ᴛᴏᴛᴀʟ ᴜsᴀɢᴇ)</b>"
    )
    for i, u in enumerate(top3, start=1):
        uid = u.get("user_id")
        up, down = u.get("uploaded_bytes", 0), u.get("downloaded_bytes", 0)
        text += (
            f"\n\n{i}. 👤 <b>ᴜsᴇʀ ɪᴅ:</b> <code>{uid}</code>\n"
            f"    • Uploaded: {fmt_bytes(up)} ⬆️\n"
            f"    • Downloaded: {fmt_bytes(down)} ⬇️\n"
            f"    • Total: {fmt_bytes(up + down)} 📦\n"
            f"    • Files: {u.get('success_count', 0)} 🗂"
        )

    if len(text) > 4000:
        fname = f"totaluses_{message.id}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text.replace("<b>", "").ʀᴇᴘʟᴀᴄᴇ("</b>", "").replace("<code>", "").replace("</code>", ""))
        await message.reply_document(fname, caption="📊 All Users Today (Summary)")
        try:
            os.remove(fname)
        except Exception:
            pass
    else:
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("useruses") & filters.user(ADMINS))
async def check_user_cmd(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            "⚠️ <b>ᴜsᴀɢᴇ:</b> <code>/useruses user_id</code>", parse_mode=enums.ParseMode.HTML
        )
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid user_id. Sirf numeric Telegram ID dijiye.")

    stats = await get_user_stats(user_id)
    if not stats:
        return await message.reply_text(f"❌ Aaj ke liye user <code>{user_id}</code> ka koi record nahi mila.", parse_mode=enums.ParseMode.HTML)

    uploaded = stats.get("uploaded_bytes", 0)
    downloaded = stats.get("downloaded_bytes", 0)
    text = (
        f"<b>📊 sᴛᴀᴛs ғᴏʀ</b> <code>{user_id}</code> <b>(ᴛᴏᴅᴀʏ)</b>\n\n"
        f"⬆️ <b>ᴜᴘʟᴏᴀᴅᴇᴅ:</b> <code>{fmt_bytes(uploaded)}</code>\n"
        f"⬇️ <b>ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ:</b> <code>{fmt_bytes(downloaded)}</code>\n"
        f"📦 <b>ᴛᴏᴛᴀʟ ᴜsᴀɢᴇ:</b> <code>{fmt_bytes(uploaded + downloaded)}</code>\n"
        f"🗂 <b>ғɪʟᴇs ᴜᴘʟᴏᴀᴅᴇᴅ:</b> <code>{stats.get('success_count', 0)}</code>"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
