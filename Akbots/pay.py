from datetime import date, timedelta
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
)
from database.db import db
from config import STAR_PLANS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_DIAMOND = '<emoji id=5217822164362739968>💎</emoji>'
E_CHECK   = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS   = '<emoji id=5210952531676504517>❌</emoji>'
E_STAR    = '⭐'


@Client.on_message(filters.command("pay") & filters.private)
async def pay_command(client: Client, message: Message):
    buttons = InlineKeyboardMarkup([
        [make_button(f"{E_STAR} {p['label']} — {p['stars']} Stars", callback_data=f"paystar_{key}", style=_BS.PRIMARY if _BS else None)]
        for key, p in STAR_PLANS.items()
    ])
    text = (
        f"<blockquote>{E_DIAMOND} <b>Choose your Premium plan</b>\n\n"
        + "\n".join(f"{E_STAR} <b>{p['label']}</b> — <code>{p['stars']} Stars</code>" for p in STAR_PLANS.values())
        + f"\n\n<i>Paid instantly via Telegram Stars — no manual approval needed.</i></blockquote>"
    )
    await message.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^paystar_(\w+)$"))
async def pay_plan_callback(client: Client, callback_query: CallbackQuery):
    key = callback_query.matches[0].group(1)
    plan = STAR_PLANS.get(key)
    if not plan:
        return await callback_query.answer("Invalid plan.", show_alert=True)

    try:
        await client.send_invoice(
            chat_id=callback_query.from_user.id,
            title=f"Premium — {plan['label']}",
            description=f"{plan['label']} of Premium access to the bot.",
            payload=f"premium_{key}_{callback_query.from_user.id}",
            currency="XTR",
            prices=[LabeledPrice(label=f"Premium {plan['label']}", amount=plan["stars"])],
        )
        await callback_query.answer("Invoice sent — check your chat! 💫")
    except Exception as e:
        await callback_query.answer(f"Error: {e}", show_alert=True)


@Client.on_pre_checkout_query()
async def approve_precheckout(client: Client, pre_checkout_query):
    try:
        await pre_checkout_query.answer(ok=True)
    except Exception:
        pass


@Client.on_message(filters.successful_payment)
async def successful_payment_handler(client: Client, message: Message):
    payment = message.successful_payment
    payload = payment.invoice_payload  # "premium_<key>_<user_id>"
    try:
        _, key, user_id_str = payload.split("_", 2)
        user_id = int(user_id_str)
        plan = STAR_PLANS[key]
    except Exception:
        return

    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    expiry_date = (date.today() + timedelta(days=plan["days"])).isoformat()
    await db.add_premium(user_id, expiry_date)

    await message.reply_text(
        f"<b>{E_CHECK} Payment successful — Premium activated!</b>\n\n"
        f"{E_DIAMOND} <b>Plan:</b> {plan['label']}\n"
        f"{E_STAR} <b>Paid:</b> {plan['stars']} Stars\n"
        f"⏳ <b>Valid until:</b> <code>{expiry_date}</code>\n\n"
        f"<i>Enjoy! 🎉</i>",
        parse_mode=enums.ParseMode.HTML
    )
