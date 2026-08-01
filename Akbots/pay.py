from datetime import date, timedelta
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
try:
    from pyrogram.types import LabeledPrice
except ImportError:
    from pyrogram.types.bots_and_keyboards.labeled_price import LabeledPrice
from database.db import db
from config import STAR_PLANS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

# Patch missing on_pre_checkout_query decorator onto Client if this kurigram
# build doesn't ship it. Mirrors Pyrogram's own on_message/on_callback_query
# implementation exactly: an *unbound* function assigned as a class
# attribute, so `@Client.on_pre_checkout_query()` (self=None) stashes the
# handler on the function itself for the plugins=dict(root="Akbots") loader
# to pick up, while `@some_client_instance.on_pre_checkout_query()` would
# register directly via add_handler.
#
# Fully defensive: some kurigram builds are missing PreCheckoutQueryHandler
# itself (not just the decorator), which previously raised an ImportError
# here and crashed the *entire* bot at plugin-load time. If we can't find
# the handler class anywhere, we skip this patch and disable the Stars
# checkout flow instead of taking the whole bot down.
_PreCheckoutQueryHandler = None
if not hasattr(Client, "on_pre_checkout_query"):
    for _path in (
        "pyrogram.handlers",
        "pyrogram.handlers.pre_checkout_query_handler",
    ):
        try:
            _mod = __import__(_path, fromlist=["PreCheckoutQueryHandler"])
            _PreCheckoutQueryHandler = getattr(_mod, "PreCheckoutQueryHandler", None)
            if _PreCheckoutQueryHandler:
                break
        except ImportError:
            continue

    if _PreCheckoutQueryHandler is None:
        import logging
        logging.getLogger(__name__).warning(
            "pay.py: PreCheckoutQueryHandler not found in this pyrogram/kurigram "
            "build — Telegram Stars checkout approval won't be wired up. "
            "/pay will still show the invoice, but payment may not auto-approve."
        )
    else:
        from pyrogram.filters import Filter as _Filter

        def _on_pre_checkout_query(self=None, filters=None, group=0):
            def decorator(func):
                if isinstance(self, Client):
                    self.add_handler(_PreCheckoutQueryHandler(func, filters), group)
                elif isinstance(self, _Filter) or self is None:
                    if not hasattr(func, "handlers"):
                        func.handlers = []
                    func.handlers.append(
                        (_PreCheckoutQueryHandler(func, self), group if filters is None else filters)
                    )
                return func
            return decorator

        Client.on_pre_checkout_query = _on_pre_checkout_query

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


async def approve_precheckout(client: Client, pre_checkout_query):
    try:
        await pre_checkout_query.answer(ok=True)
    except Exception:
        pass


if hasattr(Client, "on_pre_checkout_query"):
    approve_precheckout = Client.on_pre_checkout_query()(approve_precheckout)


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
