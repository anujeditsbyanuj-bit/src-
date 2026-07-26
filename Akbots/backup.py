# Akbots
# Auto-Backup — copies every finished download to DB_CHANNEL and posts a
# daily JSON dump of the users collection there too, so the bot's data
# survives a lost/corrupted MongoDB instance or an accidental /delete_user.
#
# Both halves are best-effort: a backup failure must never break the user's
# actual download/upload, and a DB dump failure must never crash the
# scheduler. Everything here swallows its own exceptions and just logs.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import json
import logging
import datetime
import tempfile

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import DB_CHANNEL, AUTO_BACKUP_FILES, DB_BACKUP_HOUR_UTC, ADMINS

logger = logging.getLogger("Akbots.backup")

_scheduler = None  # set by schedule_db_backup() if apscheduler is available


async def backup_message(client: Client, sent_message: Message, note: str = None):
    """Copies an already-sent message (the file we just delivered to the
    user) into DB_CHANNEL. Uses copy_message rather than re-uploading from
    disk — Telegram just re-associates the existing file_id server-side, so
    this costs no extra bandwidth or disk I/O regardless of file size.

    Best-effort: never raises, so a backup hiccup (bot not admin in the
    channel, channel deleted, flood-wait, etc.) never breaks the upload
    that already succeeded for the user.
    """
    if not AUTO_BACKUP_FILES or sent_message is None:
        return
    try:
        await client.copy_message(
            chat_id=DB_CHANNEL,
            from_chat_id=sent_message.chat.id,
            message_id=sent_message.id,
            caption=note if note is not None else sent_message.caption,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        logger.debug(f"backup_message: failed to copy to DB_CHANNEL: {e}")


def _json_default(obj):
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return str(obj)  # covers ObjectId and anything else json can't handle


async def dump_users_to_file() -> str:
    """Exports every document in the users collection to a local JSON file
    and returns its path. Caller is responsible for deleting it afterwards."""
    from database.db import db

    users = []
    async for user in db.get_all_users():
        user.pop("_id", None)  # Mongo's ObjectId isn't meaningful on restore
        users.append(user)

    fd, path = tempfile.mkstemp(prefix="users_backup_", suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(users, f, default=_json_default, indent=2, ensure_ascii=False)
    return path, len(users)


async def run_db_backup(app: Client) -> bool:
    """Dumps the users collection and posts it to DB_CHANNEL as a document.
    Returns True on success. Safe to call from a scheduled job or an admin
    command — never raises."""
    path = None
    try:
        path, count = await dump_users_to_file()
        stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        await app.send_document(
            DB_CHANNEL, path,
            file_name=f"users_backup_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M')}.json",
            caption=f"<b>🗄 Database Backup</b>\n<b>👤 Users:</b> <code>{count}</code>\n<b>🕒 {stamp}</b>",
            parse_mode=enums.ParseMode.HTML,
        )
        logger.info(f"DB backup posted to DB_CHANNEL ({count} users).")
        return True
    except Exception as e:
        logger.error(f"DB backup failed: {e}")
        return False
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass


def schedule_db_backup(app: Client):
    """Call once from Bot.start() after the client is running. No-op if
    apscheduler isn't installed."""
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("Auto-backup enabled but apscheduler isn't installed — add it to requirements.txt.")
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(run_db_backup, "cron", hour=DB_BACKUP_HOUR_UTC, args=[app])
    _scheduler.start()
    logger.info(f"DB auto-backup scheduler started (daily {DB_BACKUP_HOUR_UTC}:00 UTC -> DB_CHANNEL {DB_CHANNEL}).")


@Client.on_message(filters.command("backupdb") & filters.user(ADMINS))
async def backupdb_command(client: Client, message: Message):
    status = await message.reply_text("<b>🗄 Building database backup...</b>", parse_mode=enums.ParseMode.HTML)
    ok = await run_db_backup(client)
    await status.edit_text(
        "<b>✅ Backup posted to DB_CHANNEL.</b>" if ok else
        "<b>❌ Backup failed — check the logs.</b>",
        parse_mode=enums.ParseMode.HTML,
    )
