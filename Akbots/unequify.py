# Akbots - Don't Remove Credit - @AkBots_Official
#
# /unequify — scans a chat's message history and deletes duplicate
# messages (same file, or same text/caption), ported from fwdbot's
# plugins/unequify.py. Needs either the bot to be an admin in the chat
# (can delete messages), or the user's own /login session to be a member
# with delete rights there — same bot-then-userbot fallback /setsource
# and /settarget already use.
#
# Commands:
#   /unequify <chat_id or @username>   — asks for confirmation, then scans
#                                         and deletes duplicates
#   /unequifycancel                    — stop a running scan

import asyncio
import hashlib
import time
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import RPCError, FloodWait
from database.db import db
from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO, E_CLOCK, safe_edit
from Akbots.forward import _parse_chat, _resolve_chat
from Akbots import task_manager
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

DELETE_BATCH = 50            # delete duplicates in chunks of this size
PROGRESS_EVERY_SEC = 2        # throttle status-message edits

_RUNNING: dict[int, asyncio.Task] = {}
_PENDING: dict[int, tuple] = {}   # user_id -> (chat_ref, via_hint) awaiting confirm


def _message_hash(msg: Message):
    """(kind, hash) for duplicate detection — same file (by Telegram's own
    file_unique_id, so re-uploads/re-compressions still count as different)
    or identical text/caption. Returns (None, None) if neither applies."""
    for attr in ("document", "video", "photo", "audio", "voice", "sticker", "animation"):
        obj = getattr(msg, attr, None)
        if obj:
            fid = getattr(obj, "file_unique_id", None)
            if fid:
                return attr, fid
    if msg.text:
        return "text", hashlib.md5(msg.text.encode()).hexdigest()
    if msg.caption:
        return "caption", hashlib.md5(msg.caption.encode()).hexdigest()
    return None, None


def _progress_text(total, deleted, elapsed):
    m, s = divmod(int(elapsed), 60)
    return (
        f"<b>{E_CLOCK} Unequify running…</b>\n\n"
        f"<b>Scanned:</b> <code>{total}</code>\n"
        f"<b>Duplicates deleted:</b> <code>{deleted}</code>\n"
        f"<b>Elapsed:</b> <code>{m}m {s}s</code>"
    )


@Client.on_message(filters.private & filters.command("unequify"))
async def unequify_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if user_id in _RUNNING and not _RUNNING[user_id].done():
        return await message.reply_text(
            f"<b>{E_CROSS} A scan is already running.</b> Use /unequifycancel to stop it first.",
            parse_mode=enums.ParseMode.HTML
        )

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Delete duplicate messages in a chat</b>\n\n"
            f"<b>Usage:</b> <code>/unequify &lt;chat_id or @username&gt;</code>\n\n"
            f"<i>Scans the chat's full history and deletes any message that's a "
            f"duplicate of an earlier one (same file, or same text/caption). "
            f"Needs the bot to be an admin there, or your /login session to be "
            f"a member with delete rights.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    chat_ref = _parse_chat(message.text.split(" ", 1)[1].strip())
    chat, via, userbot = await _resolve_chat(client, user_id, chat_ref)
    if not chat:
        has_session = bool(await db.get_session(user_id))
        hint = (
            "Your login session can't see it either — double check the chat id/username."
            if has_session else
            "The bot isn't in that chat, and you haven't run /fwd_login yet — do that if "
            "it's a private chat, so your own account can be used instead."
        )
        return await message.reply_text(f"<b>{E_CROSS} Can't access that chat.</b> {hint}", parse_mode=enums.ParseMode.HTML)

    if userbot:
        await userbot.disconnect()  # just testing access here; job below reconnects if it wins

    _PENDING[user_id] = (chat.id, via)
    confirm_kb = InlineKeyboardMarkup([[
        make_button("✅ Yes, scan & delete", callback_data=f"uneq:yes:{user_id}", style=_BS.DANGER if _BS else None),
        make_button("❌ Cancel", callback_data="uneq:no", style=_BS.DANGER if _BS else None),
    ]])
    title = chat.title or chat.first_name or str(chat.id)
    await message.reply_text(
        f"<b>⚠️ This will permanently delete duplicate messages in:</b> {title}\n\n"
        f"<i>This can't be undone. Continue?</i>",
        reply_markup=confirm_kb, parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex(r"^uneq:(yes|no)"))
async def unequify_confirm_cb(client: Client, callback_query: CallbackQuery):
    parts = callback_query.data.split(":")
    if parts[1] == "no":
        _PENDING.pop(callback_query.from_user.id, None)
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_INFO} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)

    owner_id = int(parts[2])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("This isn't your request.", show_alert=True)

    pending = _PENDING.pop(owner_id, None)
    if not pending:
        return await safe_edit(callback_query.message.edit_text, 
            f"<b>{E_CROSS} This confirmation expired.</b> Run /unequify again.", parse_mode=enums.ParseMode.HTML
        )
    chat_id, via = pending
    status_msg = await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_CLOCK} Starting scan…</b>", parse_mode=enums.ParseMode.HTML
    )
    task = asyncio.create_task(_run_unequify(client, owner_id, chat_id, via, status_msg))
    _RUNNING[owner_id] = task
    task_id = task_manager.register(owner_id, task, f"Unequify {chat_id}")
    task.add_done_callback(lambda t: task_manager.unregister(owner_id, task_id))


async def _run_unequify(client: Client, user_id: int, chat_id: int, via: str, status_msg: Message):
    from Akbots.forward import _make_userbot  # local import, avoids a circular import at module load

    job_client = client
    userbot = None
    if via == "user":
        userbot = await _make_userbot(user_id)
        if not userbot:
            return await safe_edit(status_msg.edit_text, 
                f"<b>{E_CROSS} Your login session isn't available anymore.</b> Run /fwd_login again.",
                parse_mode=enums.ParseMode.HTML
            )
        job_client = userbot

    seen = {}
    duplicates = []
    total = 0
    deleted = 0
    start = time.time()
    last_edit = 0.0
    cancel_kb = InlineKeyboardMarkup([[make_button("🚫 Cancel", callback_data="uneq:stop", style=_BS.DANGER if _BS else None)]])

    try:
        async for msg in job_client.get_chat_history(chat_id):
            total += 1
            kind, h = _message_hash(msg)
            if kind:
                key = f"{kind}:{h}"
                if key in seen:
                    duplicates.append(msg.id)
                else:
                    seen[key] = msg.id

            if len(duplicates) >= DELETE_BATCH:
                try:
                    await job_client.delete_messages(chat_id, duplicates)
                    deleted += len(duplicates)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await job_client.delete_messages(chat_id, duplicates)
                    deleted += len(duplicates)
                except RPCError:
                    pass
                duplicates = []

            now = time.time()
            if now - last_edit > PROGRESS_EVERY_SEC:
                try:
                    await safe_edit(status_msg.edit_text, 
                        _progress_text(total, deleted, now - start),
                        reply_markup=cancel_kb, parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass
                last_edit = now

        if duplicates:
            try:
                await job_client.delete_messages(chat_id, duplicates)
                deleted += len(duplicates)
            except RPCError:
                pass

        elapsed = time.time() - start
        m, s = divmod(int(elapsed), 60)
        await safe_edit(status_msg.edit_text, 
            f"<b>{E_CHECK} Unequify complete.</b>\n\n"
            f"<b>Scanned:</b> <code>{total}</code>\n"
            f"<b>Duplicates deleted:</b> <code>{deleted}</code>\n"
            f"<b>Time taken:</b> <code>{m}m {s}s</code>",
            parse_mode=enums.ParseMode.HTML
        )
    except asyncio.CancelledError:
        await safe_edit(status_msg.edit_text, 
            f"<b>🚫 Stopped.</b> Scanned <code>{total}</code>, deleted <code>{deleted}</code> so far.",
            parse_mode=enums.ParseMode.HTML
        )
        raise
    finally:
        if userbot:
            await userbot.disconnect()


@Client.on_callback_query(filters.regex(r"^uneq:stop$"))
async def unequify_stop_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    task = _RUNNING.get(user_id)
    if not task or task.done():
        return await callback_query.answer("Nothing running.", show_alert=True)
    task.cancel()
    await callback_query.answer("Stopping…")


@Client.on_message(filters.private & filters.command("unequifycancel"))
async def unequify_cancel_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    task = _RUNNING.get(user_id)
    if not task or task.done():
        return await message.reply_text(f"<b>{E_INFO} No unequify scan is running.</b>", parse_mode=enums.ParseMode.HTML)
    task.cancel()
    await message.reply_text(f"<b>🚫 Stopping...</b>", parse_mode=enums.ParseMode.HTML)
