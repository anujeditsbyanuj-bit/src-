from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from database.db import db
from config import ADMINS
from datetime import date, datetime, timedelta
from logger import LOGGER
from Akbots.direct_utils import safe_edit

logger = LOGGER(__name__)

try:
    from pyrogram.enums import ButtonStyle
    BUTTON_STYLE_SUPPORTED = True
except ImportError:
    BUTTON_STYLE_SUPPORTED = False

# =========================================================
# Custom Premium Emojis
# =========================================================

E_DIAMOND = '<emoji id=5427168083074628963>💎</emoji>'
E_CROWN   = '<emoji id=5217822164362739968>👑</emoji>'
E_CHECK   = '<emoji id=5039844895779455925>✔️</emoji>'
E_CROSS   = '<emoji id=5042112436648281096>❌</emoji>'
E_WARN    = '<emoji id=5420323339723881652>⚠️</emoji>'
E_BOLT    = '<emoji id=5456140674028019486>⚡️</emoji>'
E_ROCKET  = '<emoji id=5456140674028019486>🚀</emoji>'
E_SPARK   = '<emoji id=5325547803936572038>✨</emoji>'
E_INFO    = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_PAYMENT = '<emoji id=5325547803936572038>💳</emoji>'
E_BACK    = '<emoji id=5447183459602669338>⬅️</emoji>'
E_BATCH   = '<emoji id=5341498088408234504>💯</emoji>'
E_IMAGE   = '<emoji id=5395444784611480792>🖼</emoji>'
E_PENCIL  = '<emoji id=5395444784611480792>✏️</emoji>'
E_SHIELD  = '<emoji id=5251203410396458957>🛡</emoji>'
E_STATS   = '<emoji id=5042290883949495533>📊</emoji>'
E_CLOCK   = '<emoji id=5386367538735104399>⌛</emoji>'
E_TIP     = '<emoji id=5422439311196834318>💡</emoji>'
E_ARROW   = '<emoji id=5416117059207572332>➡️</emoji>'
E_GREEN   = '<emoji id=5416081784641168838>🟢</emoji>'
E_RED     = '<emoji id=5411225014148014586>🔴</emoji>'

# =========================================================
# Button Icon IDs
# =========================================================

ICON_PAYMENT = 5325547803936572038
ICON_BACK    = 5447183459602669338
ICON_CLOSE   = 5210952531676504517
ICON_PREMIUM = 5217822164362739968
ICON_INFO    = 5334544901428229844
ICON_CHECK   = 5206607081334906820


def make_button(text, callback_data=None, url=None,
                icon_custom_emoji_id=None, style=None):
    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if BUTTON_STYLE_SUPPORTED:
        if icon_custom_emoji_id:
            kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id
        if style is not None:
            kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)


# ======================================================
# USER COMMANDS
# ======================================================

@Client.on_message(filters.command("myplan") & filters.private)
async def my_plan(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    user_data    = await db.col.find_one({'id': user_id})
    is_premium   = user_data.get('is_premium', False)
    expiry       = user_data.get('premium_expiry')
    daily_usage  = user_data.get('daily_usage', 0)
    total_saves  = user_data.get('total_saves', 0)

    if is_premium:
        if expiry:
            try:
                if isinstance(expiry, (date, datetime)):
                    exp_date = expiry
                else:
                    exp_date = date.fromisoformat(str(expiry))
                days_left   = (exp_date - date.today()).days if isinstance(exp_date, date) else 999
                expiry_text = f"<code>{expiry}</code> ({days_left} days left)"
            except Exception:
                expiry_text = "<code>Active</code>"
        else:
            expiry_text = "<code>Permanent</code>"

        plan_text = (
            f"<blockquote>{E_CROWN} <b>ᴘʀᴇᴍɪᴜᴍ sᴛᴀᴛᴜs: ᴀᴄᴛɪᴠᴇ</b>\n\n"
            f"{E_CLOCK} <b>ᴇxᴘɪʀʏ:</b> {expiry_text}\n\n"
            f"{E_BOLT} <b>ᴅᴀɪʟʏ ᴛᴏᴋᴇɴs:</b> ♾️ Unlimited\n"
            f"{E_BATCH} <b>ʙᴀᴛᴄʜ ʟɪᴍɪᴛ:</b> ♾️ Unlimited\n"
            f"{E_STATS} <b>ᴛᴏᴛᴀʟ ʟɪғᴇᴛɪᴍᴇ sᴀᴠᴇs:</b> <code>{total_saves}</code>\n\n"
            f"{E_SPARK} <i>Thank you for supporting the bot! 🎉</i></blockquote>"
        )
    else:
        daily_limit  = 10
        tokens_left  = max(0, daily_limit - daily_usage)
        plan_text = (
            f"<blockquote>{E_INFO} <b>ᴘʟᴀɴ: ғʀᴇᴇ ᴛɪᴇʀ</b>\n\n"
            f"{E_BOLT} <b>ᴅᴀɪʟʏ ᴛᴏᴋᴇɴs:</b> <code>{tokens_left} / {daily_limit}</code>\n"
            f"{E_BATCH} <b>ғɪʟᴇ sɪᴢᴇ ʟɪᴍɪᴛ:</b> <code>2 GB</code>\n"
            f"{E_STATS} <b>ᴛᴏᴛᴀʟ ʟɪғᴇᴛɪᴍᴇ sᴀᴠᴇs:</b> <code>{total_saves}</code>\n\n"
            f"{E_TIP} <i>Upgrade to Premium for unlimited access!</i></blockquote>"
        )

    if BUTTON_STYLE_SUPPORTED:
        buttons = InlineKeyboardMarkup([
            [make_button(" 💎 ᴠɪᴇᴡ ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴs ", callback_data="premium_plans_btn",
                         icon_custom_emoji_id=ICON_PREMIUM, style=ButtonStyle.PRIMARY)],
            [make_button(" 📞 ᴄᴏɴᴛᴀᴄᴛ ᴀᴅᴍɪɴ ", url="https://t.me/anujedits76",
                         icon_custom_emoji_id=ICON_INFO, style=ButtonStyle.PRIMARY)],
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [make_button(" 💎 ᴠɪᴇᴡ ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴs ", callback_data="premium_plans_btn",
                         icon_custom_emoji_id=ICON_PREMIUM, style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" 📞 ᴄᴏɴᴛᴀᴄᴛ ᴀᴅᴍɪɴ ", url="https://t.me/anujedits76",
                         icon_custom_emoji_id=ICON_INFO, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
        ])

    await message.reply_text(plan_text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("premium") & filters.private)
async def premium_info(client: Client, message: Message):
    await show_premium_plans(message)


# ======================================================
# PREMIUM PLANS VIEW (Reusable)
# ======================================================

async def show_premium_plans(message_or_query):
    text = (
        f"<blockquote>{E_DIAMOND} <b>ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴs</b>\n\n"
        f"{E_SPARK} <b>ᴡʜʏ ɢᴏ ᴘʀᴇᴍɪᴜᴍ?</b>\n"
        f"{E_BATCH} <b>ᴜɴʟɪᴍɪᴛᴇᴅ</b> Daily Saves\n"
        f"{E_BOLT} <b>4ɢʙ+</b> File Support\n"
        f"{E_ROCKET} <b>ᴢᴇʀᴏ</b> Processing Delay\n"
        f"{E_IMAGE} <b>ᴄᴜsᴛᴏᴍ</b> Thumbnails & Captions\n"
        f"{E_CROWN} <b>ᴘʀᴇᴍɪᴜᴍ</b> Badge</blockquote>\n\n"
        f"<blockquote>{E_PAYMENT} <b>ᴘʀɪᴄɪɴɢ:</b>\n"
        f"{E_ARROW} <b>1 ᴍᴏɴᴛʜ:</b> ₹554 / $6\n"
        f"{E_ARROW} <b>3 ᴍᴏɴᴛʜ:</b> ₹1660 / $17.98\n"
        f"{E_CROWN} <b>ʟɪғᴇᴛɪᴍᴇ:</b> ₹3000 / $32.5</blockquote>\n\n"
        f"<i>{E_TIP} Tap the button below to buy instantly.</i>"
    )

    if BUTTON_STYLE_SUPPORTED:
        buttons = InlineKeyboardMarkup([
            [make_button(" 💳 ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ ɴᴏᴡ ", url="https://t.me/anujedits76",
                         icon_custom_emoji_id=ICON_PAYMENT, style=ButtonStyle.SUCCESS)],
            [make_button(" ⬅️ ʙᴀᴄᴋ ᴛᴏ ᴍʏ ᴘʟᴀɴ ", callback_data="myplan_back_btn",
                         icon_custom_emoji_id=ICON_BACK, style=ButtonStyle.PRIMARY)],
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [make_button(" 💳 ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ ɴᴏᴡ ", url="https://t.me/anujedits76",
                         icon_custom_emoji_id=ICON_PAYMENT, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
            [make_button(" ⬅️ ʙᴀᴄᴋ ᴛᴏ ᴍʏ ᴘʟᴀɴ ", callback_data="myplan_back_btn",
                         icon_custom_emoji_id=ICON_BACK, style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)],
        ])

    if isinstance(message_or_query, Message):
        await message_or_query.reply_text(
            text, reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True
        )
    else:
        await safe_edit(message_or_query.edit_message_text, 
            text, reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True
        )


# ======================================================
# ADMIN COMMANDS
# ======================================================

@Client.on_message(filters.command("add_premium") & filters.user(ADMINS) & filters.private)
async def add_premium_admin(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>ᴀᴅᴍɪɴ ᴜsᴀɢᴇ:</b>\n"
            f"<code>/add_premium &lt;user_id&gt; &lt;days&gt;</code>\n\n"
            f"<i>{E_TIP} Use 0 for permanent premium.</i></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        user_id  = int(message.command[1])
        days     = int(message.command[2])
        if days == 0:
            expiry_date   = None
            duration_text = "Permanent"
        else:
            expiry_date   = (date.today() + timedelta(days=days)).isoformat()
            duration_text = f"{days} days (until {expiry_date})"
        await db.add_premium(user_id, expiry_date)
        await message.reply_text(
            f"<blockquote>{E_CHECK} <b>ᴘʀᴇᴍɪᴜᴍ ᴀᴅᴅᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ</b>\n\n"
            f"{E_BOLT} <b>ᴜsᴇʀ ɪᴅ:</b> <code>{user_id}</code>\n"
            f"{E_CLOCK} <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> {duration_text}</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    except ValueError:
        await message.reply_text(
            f"<b>{E_CROSS} Error:</b> User ID and Days must be numbers.",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(
            f"<b>{E_CROSS} Error:</b> {e}", parse_mode=enums.ParseMode.HTML
        )


@Client.on_message(filters.command("remove_premium") & filters.user(ADMINS) & filters.private)
async def remove_premium_admin(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/remove_premium &lt;user_id&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        user_id = int(message.command[1])
        await db.remove_premium(user_id)
        await message.reply_text(
            f"<b>{E_CHECK} Premium removed from <code>{user_id}</code>.</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(f"<b>{E_CROSS} Error:</b> {e}", parse_mode=enums.ParseMode.HTML)


# ======================================================
# /transfer - Gift your Premium plan to another user
# ======================================================

@Client.on_message(filters.command("transfer") & filters.private)
async def transfer_premium(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/transfer &lt;user_id&gt;</code>\n"
            f"<i>{E_TIP} Moves your remaining Premium time to another user. You lose Premium in return.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        new_user_id = int(message.command[1])
    except ValueError:
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid user ID.</b>", parse_mode=enums.ParseMode.HTML
        )

    sender_id = message.from_user.id
    if new_user_id == sender_id:
        return await message.reply_text(
            f"<b>{E_CROSS} You can't transfer Premium to yourself.</b>", parse_mode=enums.ParseMode.HTML
        )

    expiry = await db.check_premium(sender_id)
    if not expiry:
        return await message.reply_text(
            f"<b>{E_WARN} You are not a Premium user.</b>\n<i>Only Premium users can transfer their plan.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        new_user = await client.get_users(new_user_id)
    except Exception:
        return await message.reply_text(
            f"<b>{E_CROSS} Couldn't find that user.</b>\n"
            f"<i>{E_TIP} They must have started this bot at least once.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    if not await db.is_user_exist(new_user_id):
        await db.add_user(new_user_id, new_user.first_name)

    await db.remove_premium(sender_id)
    await db.add_premium(new_user_id, expiry)

    expiry_str = expiry.strftime("%d-%m-%Y %I:%M %p") if hasattr(expiry, "strftime") else str(expiry)

    await message.reply_text(
        f"<b>{E_CHECK} Premium Plan Transferred Successfully!</b>\n\n"
        f"{E_ARROW} <b>ᴛᴏ:</b> {new_user.mention}\n"
        f"{E_CLOCK} <b>ᴇxᴘɪʀʏ:</b> <code>{expiry_str}</code>",
        parse_mode=enums.ParseMode.HTML
    )

    try:
        await client.send_message(
            chat_id=new_user_id,
            text=(
                f"<b>{E_SPARK} You just received a Premium Plan!</b>\n\n"
                f"{E_ARROW} <b>ғʀᴏᴍ:</b> {message.from_user.mention}\n"
                f"{E_CLOCK} <b>ᴇxᴘɪʀʏ:</b> <code>{expiry_str}</code>\n\n"
                f"<i>Enjoy the perks! 🚀</i>"
            ),
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not notify transfer recipient {new_user_id}: {e}")


# ======================================================
# CALLBACK QUERIES
# ======================================================

@Client.on_callback_query(filters.regex("^premium_plans_btn$"))
async def premium_plans_callback(client: Client, callback_query: CallbackQuery):
    await show_premium_plans(callback_query)


@Client.on_callback_query(filters.regex("^myplan_back_btn$"))
async def myplan_back_callback(client: Client, callback_query: CallbackQuery):
    await my_plan(client, callback_query.message)
