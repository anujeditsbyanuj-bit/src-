# Akbots - Don't Remove Credit - @AkBots_Official
#
# Video Storage Info — /storageinfo (alias /storage).
#
# Everyone gets a look at the bot's storage picture:
#   • Server disk — total/used/free on the host filesystem
#   • Local cache — how much of that disk downloads/ (the bot's own scratch
#     space for in-progress downloads/encodes/merges/etc) is using right now
#   • File-to-Link storage — how many videos have ever been served a
#     streaming link, and their combined size (Akbots/filetolink*, backed
#     by the stream_links collection — see database/db.py)
#
# Admins additionally see the DB channel rotation (which Telegram channels
# actually hold the permanent copies) — full add/remove management is
# still /dbchannels (Akbots/filestore.py), this just surfaces a summary
# inline so admins don't need two commands to get the full picture.

import os
import shutil
import asyncio
import time
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import ADMINS, DB_CHANNEL
from database.db import db
from Akbots.direct_utils import fmt_bytes, fmt_duration

E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'
E_DISK   = '💾'
E_FOLDER = '🗂'
E_FILM   = '🎬'
E_SAT    = '📡'


def _dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _draw_disk_bar(used_pct, length=14, filled="█", empty="░"):
    used_pct = max(0, min(100, used_pct or 0))
    filled_n = round(length * used_pct / 100)
    return filled * filled_n + empty * (length - filled_n)


@Client.on_message(filters.private & filters.command(["storageinfo", "storage"]))
async def storage_info_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    status = await message.reply_text(f"<b>{E_GEAR} Gathering storage info...</b>", parse_mode=enums.ParseMode.HTML)

    # Disk usage of the filesystem the bot's working directory lives on.
    disk_total, disk_used, disk_free = await asyncio.to_thread(shutil.disk_usage, ".")
    disk_pct = (disk_used / disk_total * 100) if disk_total else 0

    # The bot's own scratch space — every downloads/<service>/... folder
    # every plugin here writes to while a job is in progress (should be
    # near-empty when the bot is idle; everything gets cleaned up after
    # upload, so a large number here usually means either heavy concurrent
    # traffic right now, or a stuck job that didn't clean up after itself).
    downloads_dir = "downloads"
    cache_size = await asyncio.to_thread(_dir_size, downloads_dir) if os.path.isdir(downloads_dir) else 0

    # File-to-Link: every video that's ever been given a streaming link.
    try:
        ftl_stats = await db.get_stream_link_stats()
    except Exception:
        ftl_stats = {"count": 0, "total_size": 0, "oldest_ts": None, "newest_ts": None}

    lines = [
        f"<blockquote><b>{E_DISK} Storage Info</b>",
        "",
        f"<b>Server Disk</b>",
        f"<code>{_draw_disk_bar(disk_pct)}</code> {disk_pct:.1f}%",
        f"Used: <b>{fmt_bytes(disk_used)}</b> / {fmt_bytes(disk_total)}  •  Free: <b>{fmt_bytes(disk_free)}</b>",
        "",
        f"<b>{E_FOLDER} Bot Cache (downloads/)</b>",
        f"Currently using: <b>{fmt_bytes(cache_size)}</b>",
        "<i>Temporary — cleared automatically once each job finishes.</i>",
        "",
        f"<b>{E_FILM} Video Storage (File-to-Link)</b>",
        f"Videos stored: <b>{ftl_stats['count']}</b>",
        f"Total size: <b>{fmt_bytes(ftl_stats['total_size'])}</b>",
    ]
    if ftl_stats.get("oldest_ts"):
        age = time.time() - ftl_stats["oldest_ts"]
        lines.append(f"Oldest link: <b>{fmt_duration(age)} ago</b>")

    if user_id in ADMINS:
        try:
            extra_channels = await db.get_db_channels()
            multi_on = await db.is_multi_db_enabled()
        except Exception:
            extra_channels, multi_on = [], False
        total_channels = 1 + len(extra_channels)
        lines += [
            "",
            f"<b>{E_SAT} DB Channel Rotation (admin)</b>",
            f"Primary: <code>{DB_CHANNEL}</code>",
            f"Extra channels: <b>{len(extra_channels)}</b>  •  Round robin: {'ON ✅' if multi_on else 'OFF ❌'}",
            f"Total DB channels: <b>{total_channels}</b>",
            f"<i>Full list/manage: /dbchannels</i>",
        ]

    lines.append("</blockquote>")

    await status.edit_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)
