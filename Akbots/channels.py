# Akbots - Don't Remove Credit - @AkBots_Official
#
# Channel Routes — a saved list of source→target pairs on top of
# forward.py's existing /setsource + /settarget. Akbots' forward job
# engine (Akbots/forward.py) is still single-job-per-user: one active
# fwd_source/fwd_target, one running task at a time (_RUNNING dict keyed
# by user_id, unchanged). This module doesn't touch that engine — it's an
# address book that sits in front of it. Instead of retyping two chat
# refs every time you switch what you're forwarding, save the pair once
# under a label and "activate" it with one tap.
#
# Commands:
#   /channels                        — list saved routes, tap to activate
#   /addroute <label> | <source> | <target>
#                                     — save a pair (resolved the same
#                                       way /setsource /settarget are —
#                                       bot first, then /fwd_login session)
#   /delroute <label>                — remove a saved pair
#
# NOTE ON SCOPE: this does not run several forwards simultaneously. Only
# one route can be "active" (= the current fwd_source/fwd_target) at a
# time, same one-job-at-a-time limit as before. True concurrent multi-job
# forwarding would mean reworking _RUNNING in forward.py into a
# per-route/per-job structure — a bigger change than this pass makes.

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import db
from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO
from Akbots.forward import _parse_chat, _resolve_chat
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

MAX_ROUTES = 20  # flat cap, same spirit as titanium.py's MAX_TITANIUM_BOTS


def _routes_keyboard(routes):
    rows = []
    for r in routes:
        rows.append([
            make_button(f"▶️ {r['label']}", callback_data=f"route_go#{r['label']}", style=_BS.PRIMARY if _BS else None),
            make_button("🗑", callback_data=f"route_del#{r['label']}", style=_BS.DANGER if _BS else None),
        ])
    return InlineKeyboardMarkup(rows) if rows else None


def _route_line(r) -> str:
    return f"<b>{r['label']}</b>: <code>{r['source']}</code> → <code>{r['target']}</code>"


@Client.on_message(filters.private & filters.command("channels"))
async def channels_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    routes = await db.get_fwd_routes(user_id)
    settings = await db.get_fwd_settings(user_id)
    active_note = (
        f"\n\n<b>Currently active:</b> <code>{settings['source']}</code> → <code>{settings['target']}</code>"
        if settings['source'] or settings['target'] else ""
    )

    if not routes:
        return await message.reply_text(
            f"<b>{E_INFO} No saved channels yet</b> (0/{MAX_ROUTES}){active_note}\n\n"
            f"<b>Usage:</b> <code>/addroute mylabel | -1001234567890 | @targetchannel</code>\n"
            f"<i>Save as many source→target pairs as you want, then tap one below to switch "
            f"the active pair — no retyping chat ids every time.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    lines = "\n".join(_route_line(r) for r in routes)
    await message.reply_text(
        f"<b>{E_INFO} Saved channels</b> ({len(routes)}/{MAX_ROUTES}){active_note}\n\n{lines}\n\n"
        f"<i>Tap ▶️ to activate a pair, 🗑 to remove it.</i>",
        reply_markup=_routes_keyboard(routes),
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("addroute"))
async def addroute_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if len(message.command) < 2 or "|" not in message.text:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/addroute label | source | target</code>\n"
            f"<i>Example: /addroute movies | -1001234567890 | @mytargetchannel</i>",
            parse_mode=enums.ParseMode.HTML
        )

    arg = message.text.split(" ", 1)[1]
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) != 3 or not all(parts):
        return await message.reply_text(
            f"<b>{E_CROSS} Usage:</b> <code>/addroute label | source | target</code>",
            parse_mode=enums.ParseMode.HTML
        )
    label, source_raw, target_raw = parts

    existing = await db.get_fwd_routes(user_id)
    if len(existing) >= MAX_ROUTES and not any(r["label"] == label for r in existing):
        return await message.reply_text(
            f"<b>{E_CROSS} Limit reached</b> ({MAX_ROUTES} saved channels). Remove one with /delroute first.",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>{E_INFO} Resolving both chats...</b>", parse_mode=enums.ParseMode.HTML)

    source_chat, source_via, acc1 = await _resolve_chat(client, user_id, _parse_chat(source_raw))
    if acc1:
        await acc1.disconnect()
    if not source_chat:
        return await status.edit_text(
            f"<b>{E_CROSS} Can't access source:</b> <code>{source_raw}</code>\n"
            f"<i>Check the bot (or your /fwd_login session) is actually in that chat.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    target_chat, target_via, acc2 = await _resolve_chat(client, user_id, _parse_chat(target_raw))
    if acc2:
        await acc2.disconnect()
    if not target_chat:
        return await status.edit_text(
            f"<b>{E_CROSS} Can't access target:</b> <code>{target_raw}</code>\n"
            f"<i>Check the bot (or your /fwd_login session) is actually in that chat.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    await db.add_fwd_route(user_id, label, source_chat.id, source_via, target_chat.id, target_via)
    await status.edit_text(
        f"<b>{E_CHECK} Saved \"{label}\":</b>\n"
        f"{source_chat.title or source_chat.first_name} (<code>{source_chat.id}</code>) → "
        f"{target_chat.title or target_chat.first_name} (<code>{target_chat.id}</code>)\n\n"
        f"<i>Use /channels to activate it.</i>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("delroute"))
async def delroute_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        return await message.reply_text(f"<b>{E_INFO} Usage:</b> <code>/delroute label</code>", parse_mode=enums.ParseMode.HTML)
    label = message.text.split(" ", 1)[1].strip()
    removed = await db.remove_fwd_route(user_id, label)
    if not removed:
        return await message.reply_text(f"<b>{E_INFO} No saved channel found with that label.</b>", parse_mode=enums.ParseMode.HTML)
    await message.reply_text(f"<b>{E_CHECK} Removed \"{label}\".</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^route_go#"))
async def route_go_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    label = callback_query.data.split("#", 1)[1]
    routes = await db.get_fwd_routes(user_id)
    route = next((r for r in routes if r["label"] == label), None)
    if not route:
        return await callback_query.answer("That saved channel no longer exists.", show_alert=True)

    await db.set_fwd_source(user_id, route["source"], route["source_via"])
    await db.set_fwd_target(user_id, route["target"], route["target_via"])
    await callback_query.answer(f"Activated \"{label}\"", show_alert=False)

    settings = await db.get_fwd_settings(user_id)
    active_note = f"\n\n<b>Currently active:</b> <code>{settings['source']}</code> → <code>{settings['target']}</code>"
    lines = "\n".join(_route_line(r) for r in routes)
    await callback_query.edit_message_text(
        f"<b>{E_INFO} Saved channels</b> ({len(routes)}/{MAX_ROUTES}){active_note}\n\n{lines}\n\n"
        f"<i>Tap ▶️ to activate a pair, 🗑 to remove it.</i>",
        reply_markup=_routes_keyboard(routes),
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex(r"^route_del#"))
async def route_del_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    label = callback_query.data.split("#", 1)[1]
    await db.remove_fwd_route(user_id, label)
    await callback_query.answer(f"Removed \"{label}\"", show_alert=False)

    routes = await db.get_fwd_routes(user_id)
    if not routes:
        return await callback_query.edit_message_text(
            f"<b>{E_INFO} No saved channels left.</b>\n\n"
            f"<b>Usage:</b> <code>/addroute mylabel | -1001234567890 | @targetchannel</code>",
            parse_mode=enums.ParseMode.HTML
        )
    lines = "\n".join(_route_line(r) for r in routes)
    await callback_query.edit_message_text(
        f"<b>{E_INFO} Saved channels</b> ({len(routes)}/{MAX_ROUTES})\n\n{lines}\n\n"
        f"<i>Tap ▶️ to activate a pair, 🗑 to remove it.</i>",
        reply_markup=_routes_keyboard(routes),
        parse_mode=enums.ParseMode.HTML
    )


# --------------------------------------------------------------------------
# /settings → "📡 channels" button — same listing as /channels
# --------------------------------------------------------------------------

@Client.on_callback_query(filters.regex("^channels_btn$"))
async def channels_btn_callback(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    routes = await db.get_fwd_routes(user_id)
    settings = await db.get_fwd_settings(user_id)
    active_note = (
        f"\n\n<b>Currently active:</b> <code>{settings['source']}</code> → <code>{settings['target']}</code>"
        if settings['source'] or settings['target'] else ""
    )
    if not routes:
        return await callback_query.edit_message_text(
            f"<b>{E_INFO} No saved channels yet</b> (0/{MAX_ROUTES}){active_note}\n\n"
            f"<b>Usage:</b> <code>/addroute mylabel | -1001234567890 | @targetchannel</code>",
            parse_mode=enums.ParseMode.HTML
        )
    lines = "\n".join(_route_line(r) for r in routes)
    await callback_query.edit_message_text(
        f"<b>{E_INFO} Saved channels</b> ({len(routes)}/{MAX_ROUTES}){active_note}\n\n{lines}\n\n"
        f"<i>Tap ▶️ to activate a pair, 🗑 to remove it.</i>",
        reply_markup=_routes_keyboard(routes),
        parse_mode=enums.ParseMode.HTML
    )
