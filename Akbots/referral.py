import logging
from datetime import date, timedelta, datetime
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import db
from config import ADMINS, REFERRAL_REWARD_BUCKS, REFERRAL_TRIAL_DAYS, BUCKS_PER_PREMIUM_DAY
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_GIFT  = '🎁'
E_COIN  = '💰'
E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'


async def process_referral_start(client: Client, message: Message, code: str) -> bool:
    """Called from start.py when /start refer_<code> is received.
    Returns True if it handled the message."""
    new_user_id = message.from_user.id

    referrer = await db.get_user_by_referral_code(code)
    if not referrer:
        return False  # invalid code — let start.py show the normal welcome

    referrer_id = referrer.get("id")
    if referrer_id == new_user_id:
        return False  # can't refer yourself — just show normal welcome

    is_new = not await db.is_user_exist(new_user_id)
    if not is_new:
        return False  # only reward referrals for brand-new users

    await db.add_user(new_user_id, message.from_user.first_name)
    await db.ensure_referral_data(new_user_id)
    await db.add_referral(referrer_id, new_user_id, message.from_user.first_name, REFERRAL_REWARD_BUCKS)

    if REFERRAL_TRIAL_DAYS > 0:
        expiry = (date.today() + timedelta(days=REFERRAL_TRIAL_DAYS)).isoformat()
        await db.add_premium(new_user_id, expiry)

    await message.reply_text(
        f"<b>{E_GIFT} Welcome! You joined via a referral link.</b>\n\n"
        f"<i>You've got a {REFERRAL_TRIAL_DAYS}-day Premium trial — enjoy! 🎉</i>",
        parse_mode=enums.ParseMode.HTML
    )
    try:
        await client.send_message(
            referrer_id,
            f"<b>{E_GIFT} Someone joined using your referral link!</b>\n\n"
            f"{E_COIN} <b>+{REFERRAL_REWARD_BUCKS} bucks</b> added to your balance.",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logging.warning(f"Could not notify referrer {referrer_id}: {e}")

    return True


def _referral_menu_buttons():
    return InlineKeyboardMarkup([
        [make_button("👥 My Referred Users", callback_data="referral_view", style=_BS.PRIMARY if _BS else None)],
        [make_button("💰 Redeem Bucks", callback_data="referral_redeem", style=_BS.PRIMARY if _BS else None)],
        [make_button("🔄 Refresh", callback_data="referral_refresh", style=_BS.PRIMARY if _BS else None)],
    ])


async def _referral_text(user_id: int) -> str:
    await db.ensure_referral_data(user_id)
    info = await db.get_referral_info(user_id)
    return (
        f"<blockquote>{E_GIFT} <b>Referral Program</b>\n\n"
        f"{E_COIN} <b>Bucks Balance:</b> <code>{info.get('ak_bucks', 0)}</code>\n"
        f"👥 <b>Total Referrals:</b> <code>{info.get('total_referrals', 0)}</code>\n\n"
        f"<b>How it works:</b>\n"
        f"• Share your referral link below\n"
        f"• You earn <b>+{REFERRAL_REWARD_BUCKS} bucks</b> per new user who joins\n"
        f"• They get a <b>{REFERRAL_TRIAL_DAYS}-day Premium trial</b>\n\n"
        f"<b>Redeem:</b> every <code>{BUCKS_PER_PREMIUM_DAY}</code> bucks = <b>1 day Premium</b></blockquote>"
    )


@Client.on_message(filters.command("referral") & filters.private)
async def referral_command(client: Client, message: Message):
    user_id = message.from_user.id
    await db.ensure_referral_data(user_id)
    info = await db.get_referral_info(user_id)
    bot = await client.get_me()
    link = f"https://t.me/{bot.username}?start=refer_{info['code']}"

    text = await _referral_text(user_id)
    text += f"\n\n🔗 <b>Your link:</b>\n<code>{link}</code>"
    await message.reply_text(text, reply_markup=_referral_menu_buttons(), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex("^referral_refresh$"))
async def referral_refresh_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    info = await db.get_referral_info(user_id)
    bot = await client.get_me()
    link = f"https://t.me/{bot.username}?start=refer_{info['code']}"
    text = await _referral_text(user_id) + f"\n\n🔗 <b>Your link:</b>\n<code>{link}</code>"
    try:
        await callback_query.edit_message_text(text, reply_markup=_referral_menu_buttons(), parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass
    await callback_query.answer("Refreshed ✅")


@Client.on_callback_query(filters.regex("^referral_redeem$"))
async def referral_redeem_menu(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    info = await db.get_referral_info(user_id)
    bucks = info.get("ak_bucks", 0) if info else 0
    max_days = bucks // BUCKS_PER_PREMIUM_DAY

    if max_days < 1:
        return await callback_query.answer(
            f"You need at least {BUCKS_PER_PREMIUM_DAY} bucks to redeem 1 day.", show_alert=True
        )

    options = [d for d in (1, 5, 10, 30) if d <= max_days] or [max_days]
    buttons = [[make_button(f"{E_CHECK} {d} day(s) — {d * BUCKS_PER_PREMIUM_DAY} bucks",
                                      callback_data=f"referral_redeem_confirm:{d}", style=_BS.PRIMARY if _BS else None)] for d in options]
    buttons.append([make_button("↩ Back", callback_data="referral_refresh", style=_BS.DANGER if _BS else None)])
    await callback_query.edit_message_text(
        f"<b>{E_COIN} Redeem Bucks for Premium</b>\n\n"
        f"Your balance: <code>{bucks}</code> bucks — up to <b>{max_days} day(s)</b> available.",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^referral_redeem_confirm:(\d+)$"))
async def referral_redeem_confirm(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    days = int(callback_query.matches[0].group(1))
    cost = days * BUCKS_PER_PREMIUM_DAY

    # The balance check and the deduction happen in one atomic MongoDB
    # update (see deduct_referral_bucks) rather than as two separate steps,
    # so a double-tap or two racing requests can't both pass a stale check
    # before either deduction lands.
    deducted = await db.deduct_referral_bucks(user_id, cost)
    if not deducted:
        return await callback_query.answer("Not enough bucks anymore.", show_alert=True)

    existing = await db.check_premium(user_id)
    try:
        base_date = datetime.fromisoformat(str(existing)).date() if existing and isinstance(existing, str) \
            and datetime.fromisoformat(str(existing)).date() > date.today() else date.today()
    except Exception:
        base_date = date.today()
    new_expiry = (base_date + timedelta(days=days)).isoformat()
    await db.add_premium(user_id, new_expiry)

    await callback_query.answer(f"Redeemed! +{days} day(s) Premium 🎉", show_alert=True)
    text = await _referral_text(user_id)
    await callback_query.edit_message_text(text, reply_markup=_referral_menu_buttons(), parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex("^referral_view$"))
async def referral_view_callback(client: Client, callback_query: CallbackQuery):
    await _show_referred_users(callback_query, callback_query.from_user.id, 0)


PER_PAGE = 8


async def _show_referred_users(target, user_id: int, page: int):
    info = await db.get_referral_info(user_id) or {}
    referred = info.get("referred_users", []) or []
    total = len(referred)

    if not referred:
        text = f"<b>{E_INFO} You haven't referred anyone yet.</b>"
        buttons = InlineKeyboardMarkup([[make_button("↩ Back", callback_data="referral_refresh", style=_BS.DANGER if _BS else None)]])
        if isinstance(target, CallbackQuery):
            return await target.edit_message_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        return await target.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

    skip = page * PER_PAGE
    chunk = sorted(referred, key=lambda x: x.get("referred_at", ""), reverse=True)[skip:skip + PER_PAGE]

    lines = [f"<b>👥 Your Referred Users (Page {page + 1})</b>\n<i>Total: {total}</i>\n"]
    for i, u in enumerate(chunk, start=skip + 1):
        lines.append(f"{i}. <b>{u.get('name', 'Unknown')}</b> — <code>{u.get('id')}</code>")
    text = "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(make_button("⬅️ Prev", callback_data=f"reflist_page:{page-1}", style=_BS.PRIMARY if _BS else None))
    if skip + PER_PAGE < total:
        nav.append(make_button("➡️ Next", callback_data=f"reflist_page:{page+1}", style=_BS.SUCCESS if _BS else None))
    rows = [nav] if nav else []
    rows.append([make_button("↩ Back", callback_data="referral_refresh", style=_BS.DANGER if _BS else None)])
    buttons = InlineKeyboardMarkup(rows)

    if isinstance(target, CallbackQuery):
        await target.edit_message_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    else:
        await target.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^reflist_page:(\d+)$"))
async def reflist_page_callback(client: Client, callback_query: CallbackQuery):
    page = int(callback_query.matches[0].group(1))
    await _show_referred_users(callback_query, callback_query.from_user.id, page)
    await callback_query.answer()


# =========================================================
# /referral_list - Owner-only leaderboard
# =========================================================

@Client.on_message(filters.command("referral_list") & filters.private)
async def referral_list_command(client: Client, message: Message):
    is_owner = message.from_user.id in ADMINS
    args = message.command[1:]

    if is_owner and not args:
        return await _show_leaderboard(message, 0)

    target_id = message.from_user.id
    if args and args[0].isdigit():
        if not is_owner:
            return await message.reply_text(f"<b>{E_CROSS} Only admins can view other users' referrals.</b>",
                                              parse_mode=enums.ParseMode.HTML)
        target_id = int(args[0])

    await _show_referred_users(message, target_id, 0)


async def _show_leaderboard(target, page: int):
    total = await db.count_referral_leaderboard()
    users = await db.get_referral_leaderboard(skip=page * 10, limit=10)

    if not users:
        text = f"<b>{E_INFO} No referrals yet.</b>"
        if isinstance(target, CallbackQuery):
            return await target.answer(text, show_alert=True)
        return await target.reply_text(text, parse_mode=enums.ParseMode.HTML)

    lines = [f"<b>🏆 Referral Leaderboard (Page {page + 1})</b>\n"]
    for i, u in enumerate(users, start=page * 10 + 1):
        ref = u.get("referral", {}) or {}
        lines.append(f"{i}. <b>{u.get('name', 'Unknown')}</b> (<code>{u.get('id')}</code>) — "
                      f"{ref.get('total_referrals', 0)} refs, {ref.get('ak_bucks', 0)} bucks")
    text = "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(make_button("⬅️ Prev", callback_data=f"reflead:{page-1}", style=_BS.PRIMARY if _BS else None))
    if (page + 1) * 10 < total:
        nav.append(make_button("➡️ Next", callback_data=f"reflead:{page+1}", style=_BS.SUCCESS if _BS else None))
    buttons = InlineKeyboardMarkup([nav]) if nav else None

    if isinstance(target, CallbackQuery):
        await target.edit_message_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    else:
        await target.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^reflead:(\d+)$"))
async def reflead_callback(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        return await callback_query.answer("🚫 Not authorized.", show_alert=True)
    page = int(callback_query.matches[0].group(1))
    await _show_leaderboard(callback_query, page)
    await callback_query.answer()
