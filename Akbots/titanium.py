# Akbots - Don't Remove Credit - @AkBots_Official
#
# Titanium Clone Mode — ported from fwdbot's plugins/titanium.py, trimmed
# to fit Akbots. One thing from the original was intentionally NOT
# ported:
#   - Plan-based slot limits (Config.TITANIUM_PLAN_LIMITS) — Akbots has no
#     subscription-tier system, so this uses one flat MAX_TITANIUM_BOTS cap
#     instead. If Akbots ever gets tiers, gate this the same way.
#
# Two ways to connect a clone bot:
#   1. Manual (/addbot <token>) — paste a @BotFather token directly.
#   2. Auto-create (Bot API 9.6 "Managed Bots", added April 2026) — tap a
#      button, Telegram shows a native "Create Bot?" dialog with a
#      pre-filled name/username, tap Create, done. See
#      _titanium_autocreate_callback / _handle_managed_bot_created below
#      for how this is wired.
#      REQUIRES a one-time manual setup step this code can't do for you:
#      open @BotFather's Mini App → enable "Bot Management Mode" for this
#      bot. Needs a recent kurigram build with the April-2026 TL
#      additions generated — see _managed_bots_available() below.
#
# What it does, in two parts:
#   1. Flood-pool sharing (original behaviour) — get_job_client() picks a
#      connected clone over the main bot for a job when it can reach the
#      same chats, spreading rate limits across bots.
#   2. FULL personal clone — once connected, the clone is a complete,
#      independent copy of Akbots (every command, every download source,
#      Help/Features/Settings/About menu — the whole thing) reachable
#      only by its owner. It reads/writes the exact same per-user data
#      (same MongoDB, same user_id) as the main bot, so it's really the
#      same account and the same Free/Plus/Pro limits — just reachable
#      through a bot token only the owner controls, on its own flood
#      pool.
#
# HOW "full but owner-only" is done safely:
# The clone Client below IS started with plugins=dict(root="Akbots") —
# the exact same plugin package the main bot loads, so every handler
# (downloads, settings, premium, everything) is registered on it
# identically. That alone would make the clone a fully public,
# unrestricted copy of Akbots for anyone who finds its username, because
# most of Akbots' ~50 plugins only check "does this Telegram user have an
# account", not "is this user the person who owns this specific bot
# token".
#
# So _register_owner_gate() below adds one extra handler, in a group
# number (OWNER_GATE_GROUP) lower than every other group used anywhere
# in Akbots (checked against the whole codebase — the lowest existing
# group in use is -10, so the gate sits well before that at -1000). It
# runs before anything else on every message and callback the clone
# receives:
#   - sender is the owner  -> raise ContinuePropagation, so pyrogram
#     moves on to the real plugin handlers exactly as normal.
#   - anyone else           -> return with no reply, so nothing else
#     in the clone ever runs for them.
# This is the entire safety boundary: one filter-less handler at the
# front of the queue, not a per-plugin audit. New Akbots plugins added
# later are automatically covered without touching this file.
#
# The clone bot still has to be manually added (as member/admin) to
# whatever chats a job touches, exactly like the main bot — connecting it
# here doesn't grant it access to anything by itself. Also note: because
# the gate restricts to the owner's user_id specifically, a clone added
# to a group chat will only ever respond to its owner in that group, not
# to other members — this is intentional (it's a *personal* clone), not
# a bug.

import asyncio
import time
import aiohttp
from pyrogram import Client, filters, enums, raw, ContinuePropagation
from pyrogram.errors import RPCError
from pyrogram.handlers import MessageHandler, CallbackQueryHandler, RawUpdateHandler
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import API_ID, API_HASH, BOT_TOKEN
from database.db import db
from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO
from logger import LOGGER
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

logger = LOGGER(__name__)

MAX_TITANIUM_BOTS = 5  # flat cap — see module docstring re: no plan system here

OWNER_GATE_GROUP = -1000  # lower than every group used elsewhere in Akbots (lowest found: -10)

_CLONE_CACHE = {}   # token -> fully started, owner-gated, fully-plugin-loaded clone Client

_MAIN_BOT_USERNAME = None


async def get_main_bot_username() -> str:
    """Resolves and caches the MAIN Akbots bot's own @username via the
    plain HTTP Bot API (independent of whichever Client instance calls
    this). Used by start.py to show a 'Created & Managed by @AkBots'
    footer on Titanium clones' /start message. Falls back to 'AkBots' if
    the one-time lookup ever fails, so a network hiccup never breaks
    message sending."""
    global _MAIN_BOT_USERNAME
    if _MAIN_BOT_USERNAME:
        return _MAIN_BOT_USERNAME
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getMe",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    _MAIN_BOT_USERNAME = data["result"]["username"]
    except Exception as e:
        logger.warning(f"Titanium: couldn't resolve main bot username: {e}")
    return _MAIN_BOT_USERNAME or "AkBots"


def _register_owner_gate(clone: Client, owner_id: int):
    """Registers the single handler that turns a fully-plugin-loaded
    clone into an OWNER-ONLY full copy of Akbots. See module docstring
    for the full explanation — short version: runs before every other
    handler, lets the owner's updates fall through to the real plugin
    handlers, silently drops everyone else's."""

    async def _gate(client: Client, update):
        sender = getattr(update, "from_user", None)
        if sender is not None and sender.id == owner_id:
            raise ContinuePropagation
        # not the owner: swallow the update, nothing further runs for them

    clone.add_handler(MessageHandler(_gate, filters.all), group=OWNER_GATE_GROUP)
    clone.add_handler(CallbackQueryHandler(_gate, filters.all), group=OWNER_GATE_GROUP)


async def _get_clone_client(token: str, owner_id: int) -> Client:
    """Returns a connected, fully-plugin-loaded, owner-gated clone Client
    for `token`, creating and starting one if it isn't already cached.
    This is the ONLY place clone Clients get constructed — every call
    site (addbot, auto-create, boot, get_job_client) goes through here so
    there's exactly one code path that can produce a clone, and it always
    has the gate attached before plugins ever see an update."""
    cached = _CLONE_CACHE.get(token)
    if cached is not None and cached.is_connected:
        return cached

    clone = Client(
        f"titanium_{token[:10]}",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=token,
        plugins=dict(root="Akbots"),   # full Akbots plugin set — same as the main bot
        workers=10,
        sleep_threshold=15,
        max_concurrent_transmissions=10,
        in_memory=True,
    )
    # Gate must be registered before start() — plugin loading happens
    # inside start(), and add_handler() is safe to call pre-start.
    _register_owner_gate(clone, owner_id)
    await clone.start()
    _CLONE_CACHE[token] = clone
    logger.info(f"Titanium: full clone started for owner {owner_id} (token ...{token[-6:]}), plugins=root:Akbots")
    return clone


def _managed_bots_available() -> bool:
    """Whether this pyrogram/kurigram build's raw-API layer has the Bot
    API 9.6 "Managed Bots" TL constructors generated yet
    (RequestPeerTypeCreateBot, InputKeyboardButtonRequestPeer,
    MessageActionManagedBotCreated, messages.SendBotRequestedPeer). This
    is a very new (April 2026) addition to the MTProto schema — if
    kurigram hasn't been updated past that point, these simply won't
    exist as attributes yet. Checked defensively with hasattr() rather
    than importing directly, so an outdated build degrades to "auto-create
    unavailable" instead of an ImportError crashing plugin loading.
    Run `pip install -U kurigram` if this unexpectedly returns False."""
    return (
        hasattr(raw.types, "RequestPeerTypeCreateBot")
        and hasattr(raw.types, "InputKeyboardButtonRequestPeer")
        and hasattr(raw.types, "MessageActionManagedBotCreated")
        and hasattr(raw.functions.messages, "SendBotRequestedPeer")
    )


async def _get_managed_bot_token(bot_id: int) -> str | None:
    """Retrieves the actual bot token for a just-created managed bot, via
    the HTTP Bot API's getManagedBotToken method — called with THIS bot's
    own token for auth, independent of the MTProto (Pyrogram) connection
    used for everything else in this file, since no separate MTProto
    raw-API method for this was found (see module docstring).

    CAVEAT: the *exact* HTTP parameter name below (user_id) is inferred
    from third-party documentation/reference implementations, not
    directly confirmed against Telegram's own parameter table for this
    specific method — if this starts failing, check the "description"
    field of the returned error first; Telegram's Bot API errors are
    normally specific enough to show the right parameter name to fix
    here. Bots are represented as User objects in Telegram's data model,
    which is why a bot's own id is passed as "user_id" here."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getManagedBotToken"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"user_id": bot_id}, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(f"getManagedBotToken failed for bot_id={bot_id}: {data.get('description')}")
                    return None
                result = data["result"]
                # Result shape isn't confirmed either — handle both a bare
                # token string and an object with a "token" field.
                return result if isinstance(result, str) else result.get("token")
    except Exception as e:
        logger.warning(f"getManagedBotToken request failed for bot_id={bot_id}: {e}")
        return None


@Client.on_callback_query(filters.regex(r"^titanium_autocreate$"))
async def titanium_autocreate_callback(client: Client, query: CallbackQuery):
    if not _managed_bots_available():
        return await query.answer(
            "This bot's kurigram build doesn't have Managed Bots support yet "
            "(needs a very recent version). Use /addbot with a @BotFather "
            "token instead for now.",
            show_alert=True,
        )

    owner_id = query.from_user.id
    bots = await db.get_titanium_bots(owner_id)
    if len(bots) >= MAX_TITANIUM_BOTS:
        return await query.answer(f"Limit reached ({MAX_TITANIUM_BOTS} bots). Disconnect one with /delbot first.", show_alert=True)

    await query.answer()
    suggested_username = f"akbots_{owner_id}_{int(time.time()) % 100000}bot"

    try:
        button = raw.types.InputKeyboardButtonRequestPeer(
            text="🤖 Create my clone bot",
            button_id=1,
            peer_type=raw.types.RequestPeerTypeCreateBot(
                bot_managed=True,
                suggested_name="My Akbots Clone",
                suggested_username=suggested_username,
            ),
            max_quantity=1,
        )
        markup = raw.types.ReplyKeyboardMarkup(
            rows=[raw.types.KeyboardButtonRow(buttons=[button])],
            resize=True, single_use=True, selective=True,
        )
        await client.invoke(raw.functions.messages.SendMessage(
            peer=await client.resolve_peer(query.message.chat.id),
            message=(
                "⚡ AUTO-CREATE YOUR TITANIUM BOT\n\n"
                "Tap the button below. Telegram will show a pre-filled name "
                "and username for your clone bot — edit them if you like, "
                "then tap Create.\n\n"
                "Your bot will be activated automatically — no token copying needed."
            ),
            random_id=client.rnd_id(),
            reply_markup=markup,
        ))
    except RPCError as e:
        logger.warning(f"titanium_autocreate: SendMessage with request-peer button failed: {e}")
        await query.message.reply_text(
            f"<b>{E_CROSS} Couldn't start auto-create:</b> <code>{e}</code>\n\n"
            f"<i>Make sure \"Bot Management Mode\" is enabled for this bot in "
            f"@BotFather's Mini App — this feature won't work without it. "
            f"Falling back to /addbot with a manual token is always available.</i>",
            parse_mode=enums.ParseMode.HTML,
        )


async def _handle_managed_bot_created(client: Client, update, users, chats):
    """Raw-update handler (registered below) — catches the
    messageActionManagedBotCreated service message Telegram sends after a
    user completes the auto-create flow. Deliberately hooked at the raw
    level rather than via @Client.on_message: pyrogram's higher-level
    Message parser may not know how to build a friendly Message object
    around an action type this new, depending on the installed kurigram
    version, so reading the raw update directly avoids depending on that.

    messageActionManagedBotCreated only carries the new bot's id — no
    button_id/correlation data — but that's fine: this message is sent
    directly from the requesting user to this bot's own chat with them,
    so the message's sender IS the owner, with no extra bookkeeping needed."""
    upd_msg = getattr(update, "message", None)
    if upd_msg is None or not isinstance(getattr(upd_msg, "action", None), raw.types.MessageActionManagedBotCreated):
        return

    bot_id = upd_msg.action.bot_id
    owner_id = getattr(upd_msg, "from_id", None)
    owner_id = getattr(owner_id, "user_id", None) if owner_id else None
    if owner_id is None:
        # Fallback: in a private chat the peer_id IS the other party.
        peer = getattr(upd_msg, "peer_id", None)
        owner_id = getattr(peer, "user_id", None)
    if owner_id is None:
        logger.warning(f"managed_bot_created: couldn't determine owner for new bot_id={bot_id}")
        return

    token = await _get_managed_bot_token(bot_id)
    if not token:
        try:
            await client.send_message(
                owner_id,
                f"<b>{E_CROSS} Your clone bot was created, but Akbots couldn't retrieve its token</b> "
                f"(getManagedBotToken failed). Try /addbot with a manual @BotFather token instead, "
                f"or contact the bot owner if this keeps happening.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return

    try:
        verify_client = Client(
            f"titanium_verify_{owner_id}_{int(time.time())}",
            api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True,
        )
        await verify_client.start()
        me = await verify_client.get_me()
        await verify_client.stop()
    except Exception as e:
        logger.warning(f"managed_bot_created: token verification failed for bot_id={bot_id}: {e}")
        return

    bots = await db.get_titanium_bots(owner_id)
    if len(bots) >= MAX_TITANIUM_BOTS or any(b["token"] == token for b in bots):
        return

    await db.add_titanium_bot(owner_id, token, me.username, source="managed")
    try:
        await _get_clone_client(token, owner_id)
        personal_note = f"\n<i>@{me.username} is now your full personal Akbots clone — every command works, try /start on it.</i>"
    except Exception as e:
        logger.warning(f"managed_bot_created: full clone start failed for @{me.username}: {e}")
        personal_note = ""

    try:
        await client.send_message(
            owner_id,
            f"<b>{E_CHECK} @{me.username} created and connected — no BotFather needed!</b>\n"
            f"<i>Add it as admin to your chats — it'll be picked up automatically for jobs "
            f"that can use it.</i>{personal_note}",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


def register_managed_bot_handler(app: Client):
    """Call once from bot.py's startup (alongside boot_personal_bots()) —
    a plain @Client.on_raw_update decorator can't be used here since this
    handler needs to check _managed_bots_available() and the raw types it
    references may not exist at all on an outdated kurigram build; adding
    it this way keeps that check in one place instead of scattering
    hasattr() guards through a decorator-registered function."""
    if not _managed_bots_available():
        logger.info("Titanium: Managed Bots auto-create not available on this kurigram build — /addbot still works.")
        return
    app.add_handler(RawUpdateHandler(_handle_managed_bot_created))
    logger.info("Titanium: Managed Bots auto-create handler registered.")


async def boot_personal_bots():
    """Reconnects every user's saved Titanium bots as full, owner-gated
    clones. Call once from bot.py's startup — without this, a connected
    clone stops answering commands after every process restart until its
    owner happens to trigger get_job_client (e.g. by running a forward
    job), which is the only other place a clone gets reconnected.
    """
    connected = 0
    try:
        users = await db.get_all_users()
        async for user in users:
            bots = user.get("titanium_bots", [])
            if not bots:
                continue
            owner_id = user["id"]
            for b in bots:
                try:
                    await _get_clone_client(b["token"], owner_id)
                    connected += 1
                except Exception as e:
                    logger.warning(f"Titanium boot: couldn't reconnect @{b.get('username', '?')} for {owner_id}: {e}")
    except Exception as e:
        logger.error(f"Titanium boot_personal_bots failed: {e}")
    if connected:
        logger.info(f"Titanium: {connected} personal clone bot(s) reconnected on boot.")
    return connected


def _titanium_panel_buttons(bots):
    rows = [[
        make_button("📊 STATUS", callback_data="titanium_status", style=_BS.PRIMARY if _BS else None),
        make_button("🏓 PING", callback_data="titanium_ping", style=_BS.PRIMARY if _BS else None),
    ]]
    if bots:
        rows.append([make_button("🔄 REPLACE BOT", callback_data="titanium_replace", style=_BS.PRIMARY if _BS else None)])
    if _managed_bots_available() and len(bots) < MAX_TITANIUM_BOTS:
        rows.append([make_button("🤖 AUTO-CREATE BOT", callback_data="titanium_autocreate", style=_BS.PRIMARY if _BS else None)])
    if bots:
        rows.append([make_button("❌ DISABLE TITANIUM", callback_data="titanium_disable", style=_BS.DANGER if _BS else None)])
    rows.append([make_button("⬅️ BACK", callback_data="start_btn", style=_BS.PRIMARY if _BS else None)])
    return InlineKeyboardMarkup(rows)


def _titanium_panel_text(bots) -> str:
    lines = ["<b>⚡ Titanium Clone Mode</b>", ""]
    if not bots:
        lines += [
            "Connect your own @BotFather bot(s) so your jobs run on a separate "
            "flood-limit pool instead of sharing the main bot's with everyone else. "
            "Each connected bot also becomes a FULL personal clone of Akbots — every "
            "command, every download source, the whole menu — reachable only by you, "
            "on your own token.",
            "",
            "<code>/addbot &lt;token&gt;</code> — connect one manually (get a token from "
            "@BotFather → /newbot), or use Auto-Create below.",
        ]
    else:
        for b in bots:
            src = "🤖 Managed Bots API" if b.get("source") == "managed" else "🔑 Manual (/addbot)"
            state = "🟢 RUNNING (active)" if (_CLONE_CACHE.get(b["token"]) and _CLONE_CACHE[b["token"]].is_connected) else "🟡 not connected yet"
            lines += [
                f"<b>BOT:</b> @{b['username']}",
                f"<b>SOURCE:</b> {src}",
                f"<b>STATUS:</b> {state}",
                "",
            ]
        lines += [
            "Your clone bot(s) handle downloads independently, preventing "
            "FloodWait on the main bot. Each is also a full personal copy of "
            "Akbots, reachable only by you.",
            "",
            f"<b>Connected:</b> {len(bots)}/{MAX_TITANIUM_BOTS}",
        ]
    return "\n".join(lines)


@Client.on_message(filters.private & filters.command("titanium"))
async def titanium_cmd(client: Client, message: Message):
    bots = await db.get_titanium_bots(message.from_user.id)
    await message.reply_text(
        _titanium_panel_text(bots),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_titanium_panel_buttons(bots),
    )


@Client.on_callback_query(filters.regex(r"^titanium_status$"))
async def titanium_status_callback(client: Client, query: CallbackQuery):
    bots = await db.get_titanium_bots(query.from_user.id)
    await query.answer()
    try:
        await query.edit_message_text(
            _titanium_panel_text(bots),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=_titanium_panel_buttons(bots),
        )
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^titanium_ping$"))
async def titanium_ping_callback(client: Client, query: CallbackQuery):
    owner_id = query.from_user.id
    bots = await db.get_titanium_bots(owner_id)
    if not bots:
        return await query.answer("No clone bots connected yet.", show_alert=True)

    lines = []
    for b in bots:
        start = time.monotonic()
        try:
            clone = await _get_clone_client(b["token"], owner_id)
            await clone.get_me()
            ms = int((time.monotonic() - start) * 1000)
            lines.append(f"🏓 @{b['username']}: {ms}ms")
        except Exception as e:
            lines.append(f"🏓 @{b['username']}: unreachable ({e})")
    await query.answer("\n".join(lines), show_alert=True)


# user_id -> {"old_username": str|None, "expires": monotonic}. None old_username
# means "connect a new one" rather than replacing a specific existing bot.
_pending_replace = {}
REPLACE_TIMEOUT = 180


@Client.on_callback_query(filters.regex(r"^titanium_replace$"))
async def titanium_replace_callback(client: Client, query: CallbackQuery):
    owner_id = query.from_user.id
    bots = await db.get_titanium_bots(owner_id)
    if not bots:
        return await query.answer("No clone bots connected yet — use Auto-Create or /addbot first.", show_alert=True)

    if len(bots) == 1:
        return await _start_replace_wait(client, query, owner_id, bots[0]["username"])

    rows = [[make_button(f"@{b['username']}", callback_data=f"titanium_replace_pick:{b['username']}")] for b in bots]
    rows.append([make_button("⬅️ BACK", callback_data="titanium_status")])
    await query.answer()
    await query.edit_message_text(
        "<b>Which connected bot do you want to replace?</b>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


@Client.on_callback_query(filters.regex(r"^titanium_replace_pick:"))
async def titanium_replace_pick_callback(client: Client, query: CallbackQuery):
    owner_id = query.from_user.id
    username = query.data.split(":", 1)[1]
    await _start_replace_wait(client, query, owner_id, username)


async def _start_replace_wait(client: Client, query: CallbackQuery, owner_id: int, old_username: str):
    _pending_replace[owner_id] = {"old_username": old_username, "expires": time.monotonic() + REPLACE_TIMEOUT}
    await query.answer()
    try:
        await query.edit_message_text(
            f"<b>{E_INFO} Replacing @{old_username}.</b>\n\n"
            f"Send the new @BotFather token now (paste it as a plain message). "
            f"You have {REPLACE_TIMEOUT} seconds — send /cancel to abort.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass
    asyncio.create_task(_expire_pending_replace(owner_id))


async def _expire_pending_replace(owner_id: int):
    await asyncio.sleep(REPLACE_TIMEOUT)
    _pending_replace.pop(owner_id, None)


# group=-5: needs to run early enough to intercept a bare token message
# before any generic text/document catch-all, but well after the -1000
# owner gate. Narrow filter (pending-dict membership, checked first
# thing) means this is a no-op for the overwhelming majority of messages.
@Client.on_message(filters.private & filters.text & filters.command("cancel"), group=-5)
async def titanium_replace_cancel(client: Client, message: Message):
    if message.from_user.id in _pending_replace:
        _pending_replace.pop(message.from_user.id, None)
        await message.reply_text(f"<b>{E_INFO} Replace cancelled.</b>", parse_mode=enums.ParseMode.HTML)
        raise ContinuePropagation
    raise ContinuePropagation


@Client.on_message(filters.private & filters.text & ~filters.command(["addbot", "delbot", "titanium", "cancel"]), group=-5)
async def titanium_replace_token_catch(client: Client, message: Message):
    owner_id = message.from_user.id
    pending = _pending_replace.get(owner_id)
    if not pending:
        raise ContinuePropagation
    _pending_replace.pop(owner_id, None)

    new_token = message.text.strip()
    status = await message.reply_text(f"<b>{E_INFO} Verifying new token...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        test_client = Client(
            f"titanium_verify_{owner_id}_{int(time.time())}",
            api_id=API_ID, api_hash=API_HASH, bot_token=new_token, in_memory=True
        )
        await test_client.start()
        me = await test_client.get_me()
        await test_client.stop()
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Invalid token:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    old_username = pending["old_username"]
    bots = await db.get_titanium_bots(owner_id)
    old_match = next((b for b in bots if b["username"] == old_username), None)

    await db.remove_titanium_bot(owner_id, old_username)
    if old_match:
        cached = _CLONE_CACHE.pop(old_match["token"], None)
        if cached is not None:
            try:
                await cached.stop()
            except Exception as e:
                logger.debug(f"replace: stop() on old clone failed (likely already stopped): {e}")

    await db.add_titanium_bot(owner_id, new_token, me.username, source="manual")
    try:
        await _get_clone_client(new_token, owner_id)
        note = f"\n<i>@{me.username} is now your full personal Akbots clone — every command works, try /start on it.</i>"
    except Exception as e:
        logger.warning(f"replace: full clone start failed for @{me.username}: {e}")
        note = ""

    await status.edit_text(
        f"<b>{E_CHECK} Replaced @{old_username} with @{me.username}.</b>{note}",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^titanium_disable$"))
async def titanium_disable_callback(client: Client, query: CallbackQuery):
    owner_id = query.from_user.id
    bots = await db.get_titanium_bots(owner_id)
    if not bots:
        return await query.answer("No clone bots connected.", show_alert=True)
    await query.answer()
    rows = [[
        make_button(" ✅ YES, DISABLE ", callback_data="titanium_disable_confirm", style=_BS.DANGER if _BS else None),
        make_button(" ❌ CANCEL ", callback_data="titanium_status", style=_BS.PRIMARY if _BS else None),
    ]]
    await query.edit_message_text(
        f"<b>{E_CROSS} Disconnect all {len(bots)} connected bot(s)?</b>\n"
        f"<i>Your clone(s) will stop responding entirely. You can reconnect anytime with /addbot or Auto-Create.</i>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


@Client.on_callback_query(filters.regex(r"^titanium_disable_confirm$"))
async def titanium_disable_confirm_callback(client: Client, query: CallbackQuery):
    owner_id = query.from_user.id
    bots = await db.get_titanium_bots(owner_id)
    for b in bots:
        await db.remove_titanium_bot(owner_id, b["username"])
        cached = _CLONE_CACHE.pop(b["token"], None)
        if cached is not None:
            try:
                await cached.stop()
            except Exception as e:
                logger.debug(f"disable: stop() on clone failed (likely already stopped): {e}")
    await query.answer("Titanium disabled.", show_alert=True)
    await query.edit_message_text(
        _titanium_panel_text([]),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_titanium_panel_buttons([]),
    )


@Client.on_message(filters.private & filters.command("addbot"))
async def addbot_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/addbot 123456:ABC-your-bot-token</code>\n"
            f"<i>Create one with @BotFather (/newbot) first, then paste the token here.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    token = message.command[1].strip()
    bots = await db.get_titanium_bots(user_id)
    if len(bots) >= MAX_TITANIUM_BOTS:
        return await message.reply_text(
            f"<b>{E_CROSS} Limit reached</b> ({MAX_TITANIUM_BOTS} bots). Disconnect one with /delbot first.",
            parse_mode=enums.ParseMode.HTML
        )
    if any(b["token"] == token for b in bots):
        return await message.reply_text(f"<b>{E_INFO} That bot is already connected.</b>", parse_mode=enums.ParseMode.HTML)

    status = await message.reply_text(f"<b>{E_INFO} Verifying token...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        test_client = Client(
            f"titanium_verify_{user_id}_{int(time.time())}",
            api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True
        )
        await test_client.start()
        me = await test_client.get_me()
        await test_client.stop()
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Invalid token:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if any(b["username"] == me.username for b in bots):
        return await status.edit_text(f"<b>{E_INFO} @{me.username} is already connected.</b>", parse_mode=enums.ParseMode.HTML)

    await db.add_titanium_bot(user_id, token, me.username)
    try:
        await _get_clone_client(token, user_id)
        personal_note = f"\n<i>@{me.username} is now your full personal Akbots clone — every command works, try /start on it.</i>"
    except Exception as e:
        logger.warning(f"addbot: full clone start failed for @{me.username}: {e}")
        personal_note = ""
    await status.edit_text(
        f"<b>{E_CHECK} Connected @{me.username}.</b>\n"
        f"<i>Add it as admin to your chats — it'll be picked up automatically for jobs that can use it.</i>"
        f"{personal_note}",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("delbot"))
async def delbot_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        return await message.reply_text(f"<b>{E_INFO} Usage:</b> <code>/delbot username</code>", parse_mode=enums.ParseMode.HTML)
    username = message.command[1].strip().lstrip("@")

    bots = await db.get_titanium_bots(user_id)
    match = next((b for b in bots if b["username"] == username), None)

    removed = await db.remove_titanium_bot(user_id, username)
    if not removed:
        return await message.reply_text(f"<b>{E_INFO} No connected bot found with that username.</b>", parse_mode=enums.ParseMode.HTML)

    # Stop it from listening entirely — otherwise it keeps answering
    # /fwd etc. (still correctly, since the DB record is gone this just
    # means "as if just reconnected" — but simplest and safest is to
    # shut it down until re-added).
    if match:
        token = match["token"]
        cached = _CLONE_CACHE.pop(token, None)
        if cached is not None:
            try:
                await cached.stop()
            except Exception as e:
                logger.debug(f"delbot: stop() on evicted clone failed (likely already stopped): {e}")

    await message.reply_text(f"<b>{E_CHECK} Disconnected @{username}.</b>", parse_mode=enums.ParseMode.HTML)


async def get_job_client(user_id: int, fallback_client: Client, *chats_to_check):
    """Picks the least-recently-used client — main bot or a connected
    Titanium clone — that can access every chat in chats_to_check. Falls
    back to fallback_client if the person has no clones connected, or if
    none of them (nor the main bot) can reach every chat listed.

    Returns (client, is_clone: bool, username: str|None).

    This is the integration point other plugins call into — currently
    wired into Akbots/forward.py's job launch. Other long-running plugins
    (ytdl.py, terabox.py, etc.) can call this the same way to get the same
    flood-pool benefit; that wasn't done for all of them in this pass to
    keep the change reviewable.
    """
    bots = await db.get_titanium_bots(user_id)
    if not bots:
        return fallback_client, False, None

    candidates = [("__main__", fallback_client, None)]
    for b in sorted(bots, key=lambda x: x.get("last_used", 0)):
        try:
            clone = await _get_clone_client(b["token"], user_id)
            candidates.append((b["token"], clone, b["username"]))
        except Exception:
            continue

    for token, cand_client, username in candidates:
        try:
            for chat in chats_to_check:
                await cand_client.get_chat(chat)
        except RPCError:
            continue
        if token != "__main__":
            await db.touch_titanium_bot(user_id, token)
        return cand_client, token != "__main__", username

    return fallback_client, False, None
