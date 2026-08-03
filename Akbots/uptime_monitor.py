# Akbots - Don't Remove Credit - @AkBots_Official
#
# Uptime Monitor — /uptime
#
# Ported from the standalone "UptimeRobot-Shanks" bot into Akbotz as a
# normal plugin, running on the SAME bot token / Mongo DB / process as
# everything else (no separate bot, no separate FastAPI server — Akbotz
# already has its own health server in keep_alive.py).
#
# What it does:
#   /uptime  — opens a menu where a user can add website/API URLs to
#              watch. A background job (schedule_uptime, called from
#              bot.py's start(), same pattern as Akbots/rss.py) checks
#              every watched URL on an interval and:
#                - flips each URL's stored status (up/down)
#                - DMs the owner the moment a URL goes down, and again
#                  when it comes back up
#                - auto-removes + notifies if a URL stays down for 48h+
#
# Storage: one field ("uptime_urls") on the same per-user Mongo document
# RSS feeds use (see database/db.py), so no new collection/index needed.

import asyncio
import logging
import random
import time

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message, CallbackQuery,
)

from database.db import db

try:
    from pyrogram.enums import ButtonStyle
    BUTTON_STYLE_SUPPORTED = True
except ImportError:
    BUTTON_STYLE_SUPPORTED = False


def make_button(text, callback_data=None, style=None):
    kwargs = {"text": text, "callback_data": callback_data}
    if BUTTON_STYLE_SUPPORTED and style is not None:
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)

logger = logging.getLogger(__name__)

try:
    import aiohttp
except ImportError:
    aiohttp = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GLOBE  = '🌐'

MAX_URLS_PER_USER = 15
CHECK_INTERVAL_MINUTES = 5          # how often the background job runs
REQUEST_TIMEOUT_SECONDS = 10        # per-URL HTTP timeout
EXPIRE_AFTER_SECONDS = 2 * 24 * 60 * 60   # auto-remove after 48h down
ADD_TIMEOUT_SECONDS = 120           # how long /uptime "add" waits for a reply
MAX_CONCURRENT_CHECKS = 100         # global cap on simultaneous URL requests per cycle

# Same background-photo pool UptimeRobot-Shanks used for every menu message
# (bot.py's PICS tuple), carried over so /uptime looks the same as before.
PICS = (
    "https://img3.teletype.in/files/67/73/67735f4f-933a-41d9-86b9-609fa03b6614.jpeg",
    "https://img3.teletype.in/files/a6/b6/a6b666ef-afa0-4793-bd6b-235265258840.jpeg",
    "https://img3.teletype.in/files/e8/01/e8013193-9299-4cdc-8222-f4e3801a05e8.jpeg",
    "https://img4.teletype.in/files/77/7f/777f2c2d-fa53-4298-9dee-ab39d9bddf81.jpeg",
    "https://img3.teletype.in/files/a1/9e/a19e9352-dfee-471a-ae3f-14eb2e1b975b.jpeg",
    "https://img1.teletype.in/files/84/84/8484934a-a247-4b1a-8f1f-74aac621bea6.jpeg",
    "https://img4.teletype.in/files/b2/89/b289d67c-2299-4cf6-91c3-b84c83c57caa.jpeg",
    "https://img3.teletype.in/files/a0/49/a049a7b1-2924-41c1-95d4-8c466c1a80ad.jpeg",
    "https://img2.teletype.in/files/59/b3/59b3a62e-e2ce-4f00-847d-9910f0498884.jpeg",
    "https://img2.teletype.in/files/91/d8/91d8838b-85ec-45ff-868f-24d66126ce55.jpeg",
    "https://img4.teletype.in/files/71/a5/71a5481f-2398-4520-8229-222d1cf733e7.jpeg",
    "https://img4.teletype.in/files/f4/b0/f4b007ec-fc8c-49fd-a1fb-b0d02985120a.jpeg",
    "https://img4.teletype.in/files/f6/3c/f63cee0d-10ff-4b8d-9ccc-943fa80a1344.jpeg",
    "https://img4.teletype.in/files/77/ff/77ff451d-0c8a-4aeb-aa9a-a1ae7ca74069.jpeg",
    "https://img4.teletype.in/files/bb/e9/bbe9e4f6-6226-4764-8169-b7d368e29e8c.jpeg",
    "https://img2.teletype.in/files/d4/b8/d4b806a2-c534-466f-85cb-f05a9e31dc92.jpeg",
    "https://img4.teletype.in/files/b6/aa/b6aab772-1d39-4b7e-bfe5-8d04b57ac31e.jpeg",
    "https://img4.teletype.in/files/f5/c3/f5c3a05e-ecfb-4a8e-b921-2b264d40d0ce.jpeg",
    "https://img4.teletype.in/files/3f/01/3f0102af-352a-4a0a-abbd-f18919c56dc9.jpeg",
    "https://img4.teletype.in/files/7f/f2/7ff228ef-6e74-4baf-a877-b35c016d6c7b.jpeg",
    "https://img1.teletype.in/files/8b/02/8b02924e-4f24-4ace-8b3f-be2f8044b8ec.jpeg",
    "https://img2.teletype.in/files/dc/16/dc1625b2-410c-48da-98c1-1956b87768e1.jpeg",
    "https://img2.teletype.in/files/97/f3/97f31df6-2cca-4f58-8269-97aebb6d9ea7.jpeg",
    "https://img2.teletype.in/files/97/65/9765707e-1855-429b-89ba-03401b734827.jpeg",
    "https://img4.teletype.in/files/f4/53/f45390f3-e1eb-4570-9d67-c4114db18589.jpeg",
    "https://img1.teletype.in/files/81/26/81265a94-68ff-47ed-b409-fad382e7a627.jpeg",
    "https://img1.teletype.in/files/0a/1b/0a1b5f17-095c-4826-84c8-39a8b9b9deef.jpeg",
    "https://img4.teletype.in/files/f5/94/f594fbe2-b52d-489a-86c9-23b2f2dbe4d7.jpeg",
    "https://img3.teletype.in/files/e3/76/e376be29-065b-4c1a-986d-aba69d08208f.jpeg",
    "https://img1.teletype.in/files/8f/e6/8fe67878-43a3-4b3d-851f-63727a6a2b0b.jpeg",
    "https://img2.teletype.in/files/1a/d3/1ad3fa24-c3bf-4ca8-a7ef-a79286b1e37c.jpeg",
    "https://img1.teletype.in/files/80/1a/801a77ad-bf05-4d7a-96c9-2b1cde09d04f.jpeg",
)

# user_id -> {"msg_id": int, "ts": float}  (awaiting a URL after "Add" tap)
_ADD_PENDING = {}

PAGE_SIZE = 8


def _split_rows(buttons, per_row=2):
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


async def _photo_reply(message: Message, text: str, buttons):
    return await message.reply_photo(
        random.choice(PICS), caption=text,
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML, quote=True,
    )


async def _photo_edit(query: CallbackQuery, text: str, buttons):
    try:
        return await query.edit_message_media(
            InputMediaPhoto(random.choice(PICS), caption=text),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        try:
            return await query.answer("Nothing changed.")
        except Exception:
            return


def _menu_page(urls: dict, page: int = 1):
    """Builds (text, buttons) for one page of a user's watched URLs."""
    items = list(urls.items())
    if not items:
        return None, None

    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    page_items = items[start:start + PAGE_SIZE]

    text = f"<b>{E_GLOBE} Uptime Monitor</b> — page {page}/{total_pages}\n\n"
    number_buttons = []
    for offset, (url, info) in enumerate(page_items):
        global_idx = start + offset
        icon = "🟢" if info.get("status") else "🔴"
        text += f"{global_idx + 1}. {icon} <code>{url}</code>\n"
        number_buttons.append(make_button(str(global_idx + 1), callback_data=f"ut_info:{global_idx}", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None))

    buttons = _split_rows(number_buttons, 4)

    nav = []
    if page > 1:
        nav.append(make_button("◀️", callback_data=f"ut_menu:{page - 1}", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None))
    if page < total_pages:
        nav.append(make_button("▶️", callback_data=f"ut_menu:{page + 1}", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None))
    if nav:
        buttons.append(nav)

    buttons.append([make_button("➕ ᴀᴅᴅ ᴜʀʟ", callback_data="ut_add", style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None)])
    buttons.append([
        make_button("♻️ ʀᴇꜰʀᴇsʜ", callback_data=f"ut_menu:{page}", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None),
        make_button("➖ ʀᴇᴍᴏᴠᴇ ᴀʟʟ", callback_data="ut_removeall", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None),
    ])
    buttons.append([make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="ut_close", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)])

    return text, buttons


def _empty_menu():
    text = (
        f"<b>{E_GLOBE} Uptime Monitor</b>\n\n"
        f"<i>You're not watching any URL yet. Add one below — I'll DM you the "
        f"moment it goes down (and again when it's back up).</i>"
    )
    buttons = [
        [make_button("➕ ᴀᴅᴅ ᴜʀʟ", callback_data="ut_add", style=ButtonStyle.SUCCESS if BUTTON_STYLE_SUPPORTED else None)],
        [make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="ut_close", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)],
    ]
    return text, buttons


async def _show_menu(target, user_id: int, page: int = 1, edit: bool = False):
    """`target` is a Message when edit=False (replies), or a CallbackQuery
    when edit=True (edits the query's own message via its shortcut)."""
    urls = await db.get_uptime_urls(user_id)
    text, buttons = _menu_page(urls, page) if urls else _empty_menu()

    if edit:
        return await _photo_edit(target, text, buttons)
    return await _photo_reply(target, text, buttons)


@Client.on_message(filters.command("uptime") & filters.private)
async def uptime_cmd(client: Client, message: Message):
    await _show_menu(message, message.from_user.id)


@Client.on_callback_query(filters.regex(r"^ut_menu:(\d+)$"))
async def ut_menu_cb(client: Client, query: CallbackQuery):
    page = int(query.data.split(":")[1])
    await query.answer()
    await _show_menu(query, query.from_user.id, page, edit=True)


@Client.on_callback_query(filters.regex(r"^ut_add$"))
async def ut_add_cb(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    urls = await db.get_uptime_urls(user_id)
    if len(urls) >= MAX_URLS_PER_USER:
        return await query.answer(f"Limit reached — max {MAX_URLS_PER_USER} URLs.", show_alert=True)

    await query.answer()
    await _photo_edit(
        query,
        f"<b>{E_INFO} Send me the URL you want to watch</b> "
        f"(must start with <code>http://</code> or <code>https://</code>).\n\n"
        f"<i>Send /cancel to abort.</i>",
        [[make_button(" ᴄᴀɴᴄᴇʟ ", callback_data="ut_cancel_add", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]],
    )
    _ADD_PENDING[user_id] = {"msg_id": query.message.id, "ts": time.time()}


@Client.on_callback_query(filters.regex(r"^ut_cancel_add$"))
async def ut_cancel_add_cb(client: Client, query: CallbackQuery):
    _ADD_PENDING.pop(query.from_user.id, None)
    await query.answer("Cancelled.")
    await _show_menu(query, query.from_user.id, edit=True)


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=7)
async def ut_add_receive(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ADD_PENDING.get(user_id)
    if not session:
        return

    if time.time() - session["ts"] > ADD_TIMEOUT_SECONDS:
        _ADD_PENDING.pop(user_id, None)
        return

    url = message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return await _photo_reply(
            message,
            f"<b>{E_WARN} That doesn't look like a valid URL.</b> "
            f"It must start with <code>http://</code> or <code>https://</code>. Try again, or /cancel.",
            [[make_button(" ᴄᴀɴᴄᴇʟ ", callback_data="ut_cancel_add", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)]],
        )

    _ADD_PENDING.pop(user_id, None)
    added = await db.add_uptime_url(user_id, url)
    try:
        await message.delete()
    except Exception:
        pass

    if not added:
        return await _photo_reply(
            message, f"<b>{E_WARN} You're already watching that URL.</b>",
            [[make_button("🌐 ᴏᴘᴇɴ ᴍᴇɴᴜ", callback_data="ut_menu:1", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]],
        )

    await _photo_reply(
        message,
        f"<b>{E_CHECK} Added.</b> I'll check <code>{url}</code> every {CHECK_INTERVAL_MINUTES} min.",
        [[make_button("🌐 ᴏᴘᴇɴ ᴍᴇɴᴜ", callback_data="ut_menu:1", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None)]],
    )


@Client.on_message(filters.command("cancel") & filters.private, group=7)
async def ut_cancel_cmd(client: Client, message: Message):
    if message.from_user.id in _ADD_PENDING:
        _ADD_PENDING.pop(message.from_user.id, None)
        await message.reply_text(f"<b>{E_CHECK} Cancelled.</b>", parse_mode=enums.ParseMode.HTML, quote=True)
        return
    return message.continue_propagation()


@Client.on_callback_query(filters.regex(r"^ut_info:(\d+)$"))
async def ut_info_cb(client: Client, query: CallbackQuery):
    idx = int(query.data.split(":")[1])
    urls = await db.get_uptime_urls(query.from_user.id)
    items = list(urls.items())
    if idx >= len(items):
        return await query.answer("Not found — refresh the menu.", show_alert=True)

    url, info = items[idx]
    await query.answer()

    last_check = info.get("response_time") or 0
    if last_check:
        ago = time.strftime("%Mm %Ss", time.gmtime(max(0, time.time() - last_check)))
    else:
        ago = "never checked yet"

    text = (
        f"<b>ᴜʀʟ:</b> <code>{url}</code>\n"
        f"<b>sᴛᴀᴛᴜs:</b> {'🟢 Online' if info.get('status') else '🔴 Offline'}\n"
        f"<b>ʜᴛᴛᴘ sᴛᴀᴛᴜs:</b> <code>{info.get('response_status', '—')}</code>\n"
        f"<b>ʟᴀsᴛ ᴄʜᴇᴄᴋᴇᴅ:</b> <code>{ago}</code>"
    )
    buttons = [
        [make_button("➖ ʀᴇᴍᴏᴠᴇ", callback_data=f"ut_remove:{idx}", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)],
        [make_button("🏠 ʙᴀᴄᴋ", callback_data="ut_menu:1", style=ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None), make_button(" ❌ ᴄʟᴏsᴇ ", callback_data="ut_close", style=ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)],
    ]
    await _photo_edit(query, text, buttons)


@Client.on_callback_query(filters.regex(r"^ut_remove:(\d+)$"))
async def ut_remove_cb(client: Client, query: CallbackQuery):
    idx = int(query.data.split(":")[1])
    urls = await db.get_uptime_urls(query.from_user.id)
    items = list(urls.items())
    if idx < len(items):
        url = items[idx][0]
        await db.remove_uptime_url(query.from_user.id, url)
        await query.answer("Removed.")
    else:
        await query.answer("Already removed.")
    await _show_menu(query, query.from_user.id, edit=True)


@Client.on_callback_query(filters.regex(r"^ut_removeall$"))
async def ut_removeall_cb(client: Client, query: CallbackQuery):
    await db.remove_all_uptime_urls(query.from_user.id)
    await query.answer("All URLs removed.")
    await _show_menu(query, query.from_user.id, edit=True)


@Client.on_callback_query(filters.regex(r"^ut_close$"))
async def ut_close_cb(client: Client, query: CallbackQuery):
    try:
        await query.answer()
    except Exception:
        pass
    try:
        await query.message.delete()
    except Exception:
        pass


# ------------------------------------------------------------------
# Background checker
# ------------------------------------------------------------------

async def _check_one(session, sem: asyncio.Semaphore, url: str) -> tuple:
    """Returns (up: bool, http_status: int|None)."""
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS), allow_redirects=True) as resp:
                return resp.status < 400, resp.status
        except Exception:
            return False, None


async def _notify(client: Client, user_id, text: str):
    try:
        await client.send_message(int(user_id), text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


async def check_all_uptime_urls(client: Client):
    """Concurrent bulk checker — fires all watched URLs (across all users)
    at once through a semaphore-capped pool (MAX_CONCURRENT_CHECKS), same
    idea as the original's ThreadPoolExecutor-based ultra_light_check, just
    async instead of threaded. Updates are batched into ONE write per user
    (built from a fresh per-cycle snapshot) so concurrent checks for the
    same user's different URLs can't race/clobber each other."""
    if aiohttp is None:
        logger.warning("Uptime Monitor needs aiohttp (already in requirements.txt) — skipping check.")
        return

    cursor = await db.get_all_uptime_users()
    user_docs = [doc async for doc in cursor]
    if not user_docs:
        return

    sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    tasks, task_meta = [], []  # task_meta[i] = (user_id, url, info)

    async with aiohttp.ClientSession() as session:
        for user_doc in user_docs:
            user_id = user_doc["id"]
            urls = user_doc.get("uptime_urls") or {}
            for url, info in urls.items():
                tasks.append(_check_one(session, sem, url))
                task_meta.append((user_id, url, info))

        results = await asyncio.gather(*tasks) if tasks else []

    now = time.time()
    per_user_urls = {}   # user_id -> {url: new_info}  (full replacement set per user)
    notifications = []   # (user_id, text)

    for (user_id, url, info), (up, http_status) in zip(task_meta, results):
        was_up = bool(info.get("status"))
        bucket = per_user_urls.setdefault(user_id, {})

        if up:
            bucket[url] = {**info, "status": True, "response_time": now,
                            "response_status": http_status, "down_since": None}
            if not was_up and info.get("down_since"):
                notifications.append((user_id, f"<b>{E_CHECK} Back online:</b> <code>{url}</code>"))
            continue

        down_since = info.get("down_since") or now
        if now - down_since >= EXPIRE_AFTER_SECONDS:
            # Drop it from this user's replacement set entirely == removed.
            notifications.append((user_id, f"<b>{E_WARN} Removed <code>{url}</code></b> — it's been down for 48h+."))
            continue

        bucket[url] = {**info, "status": False, "response_time": now,
                        "response_status": http_status or 0, "down_since": down_since}
        if was_up:
            notifications.append((user_id, f"<b>{E_CROSS} Went down:</b> <code>{url}</code>"))

    # One write per user, replacing their whole uptime_urls map with the
    # freshly-computed set (expired URLs simply aren't in it anymore).
    await asyncio.gather(*(db.replace_uptime_urls(uid, urls) for uid, urls in per_user_urls.items()))

    if notifications:
        await asyncio.gather(*(_notify(client, uid, text) for uid, text in notifications))


_scheduler = None  # set by schedule_uptime() if apscheduler is available


def schedule_uptime(app: Client):
    """Starts the periodic URL-check job. Same pattern as Akbots/rss.py's
    schedule_rss(). No-ops (with a log warning) if apscheduler or aiohttp
    isn't installed."""
    global _scheduler
    if aiohttp is None:
        logger.warning("Uptime Monitor feature needs aiohttp — add it to requirements.txt.")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("Uptime Monitor feature needs apscheduler — add it to requirements.txt.")
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        lambda: asyncio.create_task(check_all_uptime_urls(app)),
        "interval", minutes=CHECK_INTERVAL_MINUTES
    )
    _scheduler.start()
    logger.info(f"Uptime Monitor scheduler started (every {CHECK_INTERVAL_MINUTES} min).")
