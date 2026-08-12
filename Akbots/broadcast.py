# Akbots - Don't Remove Credit - @AkBots_Official

from pyrogram.errors import InputUserDeactivated, UserNotParticipant, FloodWait, UserIsBlocked, PeerIdInvalid
from database.db import db
from pyrogram import Client, filters, enums
from config import ADMINS
import asyncio
import datetime
import time
from pyrogram.types import Message
import json
import os
from logger import LOGGER
from Akbots.direct_utils import safe_edit

logger = LOGGER(__name__)

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN   = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_BOLT   = '<tg-emoji emoji-id="5456140674028019486">⚡️</tg-emoji>'
E_STATS  = '<tg-emoji emoji-id="5334544901428229844">📊</tg-emoji>'
E_ROCKET = '<tg-emoji emoji-id="5456140674028019486">🚀</tg-emoji>'
E_CLOCK  = '<tg-emoji emoji-id="5386367538735104399">⌛</tg-emoji>'
E_STOP   = '<tg-emoji emoji-id="5260293700088511294">⛔️</tg-emoji>'
E_USERS  = '<tg-emoji emoji-id="5334544901428229844">👥</tg-emoji>'
E_SPARK  = '<tg-emoji emoji-id="5325547803936572038">✨</tg-emoji>'
E_GREEN  = '<tg-emoji emoji-id="5416081784641168838">🟢</tg-emoji>'

async def broadcast_messages(user_id, message):
    try:
        await message.copy(chat_id=user_id)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await broadcast_messages(user_id, message)
    except InputUserDeactivated:
        await db.delete_user(int(user_id))
        return False, "Deleted"
    except UserIsBlocked:
        await db.delete_user(int(user_id))
        return False, "Blocked"
    except PeerIdInvalid:
        await db.delete_user(int(user_id))
        return False, "Error"
    except Exception as e:
        logger.error(f"[!] Broadcast error for {user_id}: {e}")
        return False, "Error"

@Client.on_message(filters.command("broadcast") & filters.user(ADMINS))
async def broadcast_command(bot: Client, message: Message):
    b_msg = message.reply_to_message
    if not b_msg:
        return await message.reply_text(
            f"<b>{E_WARN} Reply to a message you want to broadcast.</b>",
            parse_mode=enums.ParseMode.HTML,
            quote=True
        )

    users      = await db.get_all_users()
    sts        = await message.reply_text(
        f"<b>{E_ROCKET} Broadcasting your message...</b>",
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )

    start_time  = time.time()
    total_users = await db.total_users_count()
    done = blocked = deleted = failed = success = 0

    async for user in users:
        user_id = user.get('id')
        if user_id:
            pti, sh = await broadcast_messages(int(user_id), b_msg)
            if pti:
                success += 1
            else:
                if sh == "Blocked":   blocked += 1
                elif sh == "Deleted": deleted += 1
                elif sh == "Error":   failed  += 1
            done += 1
        else:
            done   += 1
            failed += 1

        if done % 20 == 0:
            await safe_edit(sts.edit, 
                f"<blockquote>{E_BOLT} <b>ʙʀᴏᴀᴅᴄᴀsᴛ ɪɴ ᴘʀᴏɢʀᴇss</b>\n\n"
                f"{E_USERS} <b>ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> {total_users}\n"
                f"{E_SPARK} <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> {done} / {total_users}\n"
                f"{E_CHECK} <b>sᴜᴄᴄᴇss:</b> {success}\n"
                f"{E_STOP} <b>ʙʟᴏᴄᴋᴇᴅ:</b> {blocked}\n"
                f"{E_CROSS} <b>ᴅᴇʟᴇᴛᴇᴅ:</b> {deleted}</blockquote>",
                parse_mode=enums.ParseMode.HTML
            )

    time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
    await safe_edit(sts.edit, 
        f"<blockquote>{E_CHECK} <b>ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!</b>\n\n"
        f"{E_CLOCK} <b>ᴛɪᴍᴇ ᴛᴀᴋᴇɴ:</b> {time_taken}\n\n"
        f"{E_USERS} <b>ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> {total_users}\n"
        f"{E_SPARK} <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> {done} / {total_users}\n"
        f"{E_CHECK} <b>sᴜᴄᴄᴇss:</b> {success}\n"
        f"{E_STOP} <b>ʙʟᴏᴄᴋᴇᴅ:</b> {blocked}\n"
        f"{E_CROSS} <b>ᴅᴇʟᴇᴛᴇᴅ:</b> {deleted}</blockquote>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("acast") & filters.user(ADMINS))
async def announcement_cast_command(bot: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/acast &lt;announcement text&gt;</code>\n"
            f"<i>Sends a formatted text announcement to every user (no reply needed).</i>",
            parse_mode=enums.ParseMode.HTML, quote=True
        )

    text = message.text.split(None, 1)[1]
    announcement = (
        f"<blockquote>{E_ROCKET} <b>ᴀɴɴᴏᴜɴᴄᴇᴍᴇɴᴛ</b>\n\n{text}</blockquote>"
    )

    users       = await db.get_all_users()
    sts         = await message.reply_text(
        f"<b>{E_ROCKET} Sending announcement...</b>", parse_mode=enums.ParseMode.HTML, quote=True
    )
    start_time  = time.time()
    total_users = await db.total_users_count()
    done = blocked = deleted = failed = success = 0

    async for user in users:
        user_id = user.get('id')
        if user_id:
            try:
                await bot.send_message(int(user_id), announcement, parse_mode=enums.ParseMode.HTML)
                success += 1
            except UserIsBlocked:
                await db.delete_user(int(user_id))
                blocked += 1
            except (PeerIdInvalid, InputUserDeactivated):
                await db.delete_user(int(user_id))
                deleted += 1
            except Exception as e:
                logger.error(f"[!] Announcement error for {user_id}: {e}")
                failed += 1
            done += 1
        else:
            done += 1
            failed += 1

        if done % 20 == 0:
            await safe_edit(sts.edit, 
                f"<blockquote>{E_BOLT} <b>ᴀɴɴᴏᴜɴᴄᴇᴍᴇɴᴛ ɪɴ ᴘʀᴏɢʀᴇss</b>\n\n"
                f"{E_USERS} <b>ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> {total_users}\n"
                f"{E_SPARK} <b>ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> {done} / {total_users}\n"
                f"{E_CHECK} <b>sᴜᴄᴄᴇss:</b> {success}</blockquote>",
                parse_mode=enums.ParseMode.HTML
            )

    time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
    await safe_edit(sts.edit, 
        f"<blockquote>{E_CHECK} <b>ᴀɴɴᴏᴜɴᴄᴇᴍᴇɴᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ!</b>\n\n"
        f"{E_CLOCK} <b>ᴛɪᴍᴇ ᴛᴀᴋᴇɴ:</b> {time_taken}\n\n"
        f"{E_USERS} <b>ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> {total_users}\n"
        f"{E_CHECK} <b>sᴜᴄᴄᴇss:</b> {success}\n"
        f"{E_STOP} <b>ʙʟᴏᴄᴋᴇᴅ:</b> {blocked}\n"
        f"{E_CROSS} <b>ᴅᴇʟᴇᴛᴇᴅ:</b> {deleted}</blockquote>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("users") & filters.user(ADMINS))
async def users_count(bot: Client, message: Message):
    msg = await message.reply_text(
        f"<b>{E_CLOCK} Gathering User Data...</b>",
        parse_mode=enums.ParseMode.HTML,
        quote=True
    )
    try:
        total = await db.total_users_count()
        await safe_edit(msg.edit_text, 
            f"<blockquote>{E_STATS} <b>ᴜsᴇʀ ᴀɴᴀʟʏᴛɪᴄs</b>\n\n"
            f"{E_USERS} <b>ᴛᴏᴛᴀʟ ʀᴇɢɪsᴛᴇʀᴇᴅ ᴜsᴇʀs:</b> <code>{total}</code>\n"
            f"{E_GREEN} <b>sʏsᴛᴇᴍ sᴛᴀᴛᴜs:</b> Active\n"
            f"{E_BOLT} <b>ᴅᴀᴛᴀ sᴏᴜʀᴄᴇ:</b> MongoDB (async)</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

        users_cursor = await db.get_all_users()
        users_list = []
        async for user in users_cursor:
            users_list.append({
                "name":     user.get("name", "None"),
                "username": user.get("username", "None"),
                "id":       user.get("id")
            })

        tmp_path = "SaveRestricted.json"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(users_list, f, indent=2, ensure_ascii=False)

        await message.reply_document(
            document=tmp_path,
            caption=f"<b>{E_STATS} Recorded <code>{len(users_list)}</code> Users</b>",
            parse_mode=enums.ParseMode.HTML
        )
        try:
            os.remove(tmp_path)
        except Exception as e:
            logger.error(f"[!] Failed to delete file {tmp_path}: {e}")

    except Exception as e:
        await safe_edit(msg.edit_text, 
            f"<b>{E_WARN} Error Fetching User Data:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML
        )
        logger.error(f"[!] /users error: {e}")
