# Akbots
# Forward Engine — the actual "AK Manager" core: monitors Source
# channels and copies every new message into Target channels, through
# the user's own connected bot (Akbots/multibot.py -> /setbot).
#
# This mirrors the reference forward-bot's "gamma mode": a live Pyrogram
# MessageHandler attached to the user's bot Client, filtered to their
# configured source chat IDs, that copies each message to every target,
# applying caption tools (Replacer / Remover / Prefix / Suffix), an
# optional custom Button, and optional message-type Filters.
#
# Commands:
#   /addsource <chat_id>      /rmsource <chat_id>      /sources
#   /addtarget <chat_id>      /rmtarget <chat_id>      /targets
#   /forwardmode on|off       /forwardstatus
#   /addreplacer <old> | <new>            /clearreplacer
#   /addremover <word>                    /clearremover
#   /setprefix <text>          /setsuffix <text>        /clearcaption
#   /setbutton <text> | <url>  /delbutton
#   /setfilters photo,video,document,...  /clearfilters
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import asyncio
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.handlers import MessageHandler
from pyrogram.errors import FloodWait
from database.db import db
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

logger = logging.getLogger("Akbots.forward_engine")

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_ROCKET= '<emoji id=5456140674028019486>🚀</emoji>'

# Message-type names understood by /setfilters, mapped to the pyrogram
# Message attribute that's truthy when a message is that type.
FILTER_TYPES = ("text", "photo", "video", "document", "audio", "animation", "sticker")

# Tracks the live listener per user so it can be restarted/stopped
# cleanly instead of piling up duplicate handlers.
# user_id -> (Client, MessageHandler, group_id)
_active_listeners: dict[int, tuple] = {}


async def _ensure_user(message: Message):
    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)


def _parse_chat_id(raw: str):
    raw = raw.strip()
    try:
        chat_id = int(raw)
    except ValueError:
        return None
    return chat_id if raw.startswith("-100") else None


async def _apply_caption_tools(user_id: int, caption: str) -> str:
    """Replacer -> Remover -> Prefix/Suffix, in that order (matches the
    reference bot's transformation pipeline)."""
    caption = caption or ""
    cfg = await db.get_forward_caption_config(user_id)
    for pair in cfg.get("replacer", []):
        caption = caption.replace(pair["old"], pair["new"])
    for word in cfg.get("remover", []):
        caption = caption.replace(word, "")
    prefix = cfg.get("prefix") or ""
    suffix = cfg.get("suffix") or ""
    if prefix or suffix:
        caption = f"{prefix}{caption}{suffix}"
    return caption


_EMOJI_DIGITS = ['0️⃣', '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣']


def _emoji_digit(d: str) -> str:
    return _EMOJI_DIGITS[int(d)]


NUMBERING_STYLES = {
    "dot": lambda n: f"{n}. ",
    "bracket": lambda n: f"{n}) ",
    "emoji": lambda n: "".join(_emoji_digit(d) for d in str(n)) + " ",
    "asterisk": lambda n: f"* {n} ",
    "dash": lambda n: f"- {n} ",
}


BULLET_STYLES = {
    "style1": "🚀 ", "style2": "🔥 ", "style3": "📌 ",
    "style4": "⭐ ", "style5": "💫 ", "style6": "✨ ",
}

_USERNAME_RE = None
_URL_RE = None


def _strip_usernames(text: str) -> str:
    import re as _re
    global _USERNAME_RE
    if _USERNAME_RE is None:
        _USERNAME_RE = _re.compile(r'(?<!\w)@\w{4,32}')
    return _USERNAME_RE.sub("", text)


def _strip_plain_links(text: str) -> str:
    import re as _re
    global _URL_RE
    if _URL_RE is None:
        _URL_RE = _re.compile(r'https?://\S+')
    return _URL_RE.sub("", text)


async def _apply_caption_tools(user_id: int, caption: str) -> str:
    """Replacer -> username/link remover -> word Remover -> Prefix/Suffix
    -> Numbering -> Bullets (matches the reference bot's transformation
    order: content edits first, decorations last)."""
    caption = caption or ""
    cfg = await db.get_forward_caption_config(user_id)
    extra = await db.get_forward_extra(user_id)

    for pair in cfg.get("replacer", []):
        caption = caption.replace(pair["old"], pair["new"])

    if extra.get("course_sellers_mode") or extra.get("username_remover"):
        caption = _strip_usernames(caption)
    if extra.get("course_sellers_mode") or extra.get("link_remover"):
        caption = _strip_plain_links(caption)

    for word in cfg.get("remover", []):
        caption = caption.replace(word, "")

    prefix = cfg.get("prefix") or ""
    suffix = cfg.get("suffix") or ""
    if prefix or suffix:
        caption = f"{prefix}{caption}{suffix}"

    if extra.get("numbering_enabled"):
        n = await db.inc_forward_numbering(user_id)
        style_fn = NUMBERING_STYLES.get(extra.get("numbering_style", "dot"), NUMBERING_STYLES["dot"])
        caption = f"{style_fn(n)}{caption}"

    if extra.get("bullets_enabled"):
        bullet = BULLET_STYLES.get(extra.get("bullet_style", "style1"), BULLET_STYLES["style1"])
        caption = f"{bullet}{caption}"

    return caption


def _passes_extra_filters(message: Message, extra: dict) -> bool:
    """Theta mode: only forward messages that have BOTH an image and a
    caption (matches the reference bot's theta filter)."""
    if extra.get("theta_mode"):
        if not (message.photo and message.caption):
            return False
    return True


def _delta_source_link(extra: dict, message: Message) -> str:
    """Delta mode: appends a link back to the original source message.
    V1: emoji watermark + 'Source: click here' hyperlink.
    V2: plain t.me link, no watermark text."""
    if not extra.get("delta_enabled"):
        return ""
    try:
        chat = message.chat
        internal_id = str(chat.id).replace("-100", "") if str(chat.id).startswith("-100") else str(chat.id)
        link = f"https://t.me/c/{internal_id}/{message.id}"
    except Exception:
        return ""
    if extra.get("delta_version") == "v2":
        return f"\n\n{link}"
    return f"\n\n🔥 <b>1 Delta Mode</b> 🔥\n📤 Source : <a href=\"{link}\">Click Here</a>"


def _passes_filters(message: Message, allowed_types: list) -> bool:
    if not allowed_types:
        return True  # no filter configured == forward everything
    return any(getattr(message, t, None) for t in allowed_types)


async def _build_reply_markup(user_id: int):
    btn = await db.get_forward_button(user_id)
    if not btn or not btn.get("enabled"):
        return None
    return InlineKeyboardMarkup([[make_button(btn["text"], url=btn["url"], style=_BS.PRIMARY if _BS else None)]])


def stop_forwarding(user_id: int):
    """Detaches the live handler (if any) without touching the user's
    saved sources/targets/config, so /forwardmode on can cleanly restart
    it later."""
    entry = _active_listeners.pop(user_id, None)
    if not entry:
        return
    cli, handler, group = entry
    try:
        cli.remove_handler(handler, group=group)
    except Exception:
        pass


async def start_forwarding(user_id: int) -> tuple[bool, str]:
    """(Re)starts the live source->target listener for this user on their
    connected bot. Returns (ok, message)."""
    from Akbots.multibot import get_user_bot

    cli = await get_user_bot(user_id)
    if not cli:
        return False, f"{E_CROSS} Connect a bot first: <code>/setbot &lt;token&gt;</code>"

    sources = await db.get_forward_sources(user_id)
    targets = await db.get_forward_targets(user_id)
    if not sources:
        return False, f"{E_CROSS} Add at least one source: <code>/addsource -100xxxxxxxxxx</code>"
    if not targets:
        return False, f"{E_CROSS} Add at least one target: <code>/addtarget -100xxxxxxxxxx</code>"

    stop_forwarding(user_id)  # idempotent restart — no duplicate handlers
    source_ids = [s["chat_id"] for s in sources]

    async def _handler(client, message: Message):
        try:
            if not await db.get_forward_mode(user_id):
                return  # user flipped it off mid-run; handler removes itself on next stop
            live_targets = await db.get_forward_targets(user_id)
            if not live_targets:
                return

            allowed = await db.get_forward_filters(user_id)
            if not _passes_filters(message, allowed):
                return
            extra = await db.get_forward_extra(user_id)
            if not _passes_extra_filters(message, extra):
                return

            raw_caption = ""
            if message.caption:
                raw_caption = message.caption.html
            elif message.text:
                raw_caption = message.text.html
            new_caption = await _apply_caption_tools(user_id, raw_caption)
            new_caption += _delta_source_link(extra, message)
            markup = await _build_reply_markup(user_id)

            async def _send_to(target):
                target_id = target["chat_id"]
                try:
                    await client.copy_message(
                        chat_id=target_id, from_chat_id=message.chat.id, message_id=message.id,
                        caption=new_caption if (message.caption or message.text) else None,
                        reply_markup=markup, parse_mode=enums.ParseMode.HTML,
                    )
                except FloodWait as fw:
                    wait_s = getattr(fw, "value", None) or getattr(fw, "x", 10)
                    await asyncio.sleep(wait_s)
                    try:
                        await client.copy_message(
                            chat_id=target_id, from_chat_id=message.chat.id, message_id=message.id,
                            caption=new_caption if (message.caption or message.text) else None,
                            reply_markup=markup, parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception as e2:
                        logger.debug(f"forward retry failed user={user_id} target={target_id}: {e2}")
                except Exception as e:
                    logger.debug(f"forward failed user={user_id} target={target_id}: {e}")

            if extra.get("blast_mode"):
                # Blast Mode: fire all targets in parallel instead of one
                # at a time — faster, but hits Telegram's flood limits
                # sooner if there are many targets.
                await asyncio.gather(*(_send_to(t) for t in live_targets), return_exceptions=True)
            else:
                for target in live_targets:
                    await _send_to(target)
        except Exception as e:
            logger.debug(f"forward handler error user={user_id}: {e}")

    group = hash(f"fwd_{user_id}") % 100000
    handler = MessageHandler(_handler, filters.chat(source_ids) & ~filters.service)
    cli.add_handler(handler, group=group)
    _active_listeners[user_id] = (cli, handler, group)

    return True, f"{E_CHECK} Forwarding live: {len(source_ids)} source(s) → {len(targets)} target(s)."


# ---------------------------------------------------------------------
# Source / Target management
# ---------------------------------------------------------------------
async def _resolve_and_add(client: Client, message: Message, raw_arg: str, add_fn, list_label: str):
    chat_id = _parse_chat_id(raw_arg)
    if chat_id is None:
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid ID.</b> Must start with <code>-100</code>, e.g. <code>-1001234567890</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        chat = await client.get_chat(chat_id)
        title = chat.title or "Private Chat"
    except Exception as e:
        return await message.reply_text(
            f"<b>{E_CROSS} Can't access that chat.</b> Add the bot there first.\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    await add_fn(message.from_user.id, chat_id, title)
    await message.reply_text(
        f"<b>{E_CHECK} Added to {list_label}:</b> {title} (<code>{chat_id}</code>)",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("addsource") & filters.private)
async def addsource_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/addsource -100xxxxxxxxxx</code>", parse_mode=enums.ParseMode.HTML
        )
    await _resolve_and_add(client, message, message.command[1], db.add_forward_source, "Sources")


@Client.on_message(filters.command("rmsource") & filters.private)
async def rmsource_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/rmsource -100xxxxxxxxxx</code>", parse_mode=enums.ParseMode.HTML
        )
    chat_id = _parse_chat_id(message.command[1])
    if chat_id is None:
        return await message.reply_text(f"<b>{E_CROSS} Invalid ID.</b>", parse_mode=enums.ParseMode.HTML)
    await db.remove_forward_source(message.from_user.id, chat_id)
    await message.reply_text(f"<b>{E_CHECK} Source removed:</b> <code>{chat_id}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("sources") & filters.private)
async def sources_command(client: Client, message: Message):
    await _ensure_user(message)
    sources = await db.get_forward_sources(message.from_user.id)
    if not sources:
        return await message.reply_text(
            f"<blockquote>{E_INFO} <b>No source channels yet.</b>\nAdd one: <code>/addsource -100xxxxxxxxxx</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    lines = [f"• {s['title']} — <code>{s['chat_id']}</code>" for s in sources]
    await message.reply_text(
        f"<blockquote>{E_INFO} <b>Sources ({len(sources)})</b>\n\n" + "\n".join(lines) + "</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("addtarget") & filters.private)
async def addtarget_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/addtarget -100xxxxxxxxxx</code>", parse_mode=enums.ParseMode.HTML
        )
    await _resolve_and_add(client, message, message.command[1], db.add_forward_target, "Targets")


@Client.on_message(filters.command("rmtarget") & filters.private)
async def rmtarget_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/rmtarget -100xxxxxxxxxx</code>", parse_mode=enums.ParseMode.HTML
        )
    chat_id = _parse_chat_id(message.command[1])
    if chat_id is None:
        return await message.reply_text(f"<b>{E_CROSS} Invalid ID.</b>", parse_mode=enums.ParseMode.HTML)
    await db.remove_forward_target(message.from_user.id, chat_id)
    await message.reply_text(f"<b>{E_CHECK} Target removed:</b> <code>{chat_id}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("targets") & filters.private)
async def targets_command(client: Client, message: Message):
    await _ensure_user(message)
    targets = await db.get_forward_targets(message.from_user.id)
    if not targets:
        return await message.reply_text(
            f"<blockquote>{E_INFO} <b>No target channels yet.</b>\nAdd one: <code>/addtarget -100xxxxxxxxxx</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    lines = [f"• {t['title']} — <code>{t['chat_id']}</code>" for t in targets]
    await message.reply_text(
        f"<blockquote>{E_INFO} <b>Targets ({len(targets)})</b>\n\n" + "\n".join(lines) + "</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


# ---------------------------------------------------------------------
# Forwarding on/off + status
# ---------------------------------------------------------------------
@Client.on_message(filters.command("forwardmode") & filters.private)
async def forwardmode_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/forwardmode on</code> or <code>/forwardmode off</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    user_id = message.from_user.id
    enabled = message.command[1].lower() == "on"
    await db.set_forward_mode(user_id, enabled)

    if not enabled:
        stop_forwarding(user_id)
        return await message.reply_text(f"<b>{E_CHECK} Forwarding stopped.</b>", parse_mode=enums.ParseMode.HTML)

    ok, msg = await start_forwarding(user_id)
    if not ok:
        await db.set_forward_mode(user_id, False)  # don't leave it "on" with nothing running
    await message.reply_text(msg, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("forwardstatus") & filters.private)
async def forwardstatus_command(client: Client, message: Message):
    await _ensure_user(message)
    user_id = message.from_user.id
    mode = await db.get_forward_mode(user_id)
    sources = await db.get_forward_sources(user_id)
    targets = await db.get_forward_targets(user_id)
    live = user_id in _active_listeners
    await message.reply_text(
        f"<blockquote>{E_ROCKET} <b>Forward Engine Status</b>\n\n"
        f"<b>Mode:</b> {'🟢 On' if mode else '🔴 Off'} {'(live)' if live else ''}\n"
        f"<b>Sources:</b> {len(sources)}\n"
        f"<b>Targets:</b> {len(targets)}</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


# ---------------------------------------------------------------------
# Caption tools: Replacer / Remover / Prefix / Suffix
# ---------------------------------------------------------------------
@Client.on_message(filters.command("addreplacer") & filters.private)
async def addreplacer_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2 or "|" not in message.text:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/addreplacer old text | new text</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    raw = message.text.split(None, 1)[1]
    old, _, new = raw.partition("|")
    old, new = old.strip(), new.strip()
    if not old:
        return await message.reply_text(f"<b>{E_CROSS} 'old text' can't be empty.</b>", parse_mode=enums.ParseMode.HTML)
    await db.add_forward_replacer(message.from_user.id, old, new)
    await message.reply_text(f"<b>{E_CHECK} Replacer added:</b> <code>{old}</code> → <code>{new}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("clearreplacer") & filters.private)
async def clearreplacer_command(client: Client, message: Message):
    await _ensure_user(message)
    await db.clear_forward_replacer(message.from_user.id)
    await message.reply_text(f"<b>{E_CHECK} All replacers cleared.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("addremover") & filters.private)
async def addremover_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/addremover word or phrase</code>", parse_mode=enums.ParseMode.HTML
        )
    word = message.text.split(None, 1)[1].strip()
    await db.add_forward_remover(message.from_user.id, word)
    await message.reply_text(f"<b>{E_CHECK} Remover added:</b> <code>{word}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("clearremover") & filters.private)
async def clearremover_command(client: Client, message: Message):
    await _ensure_user(message)
    await db.clear_forward_remover(message.from_user.id)
    await message.reply_text(f"<b>{E_CHECK} All removers cleared.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("setprefix") & filters.private)
async def setprefix_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2:
        return await message.reply_text(f"<b>{E_WARN} Usage:</b> <code>/setprefix text</code>", parse_mode=enums.ParseMode.HTML)
    text = message.text.split(None, 1)[1]
    await db.set_forward_caption_field(message.from_user.id, "prefix", text)
    await message.reply_text(f"<b>{E_CHECK} Prefix set.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("setsuffix") & filters.private)
async def setsuffix_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2:
        return await message.reply_text(f"<b>{E_WARN} Usage:</b> <code>/setsuffix text</code>", parse_mode=enums.ParseMode.HTML)
    text = message.text.split(None, 1)[1]
    await db.set_forward_caption_field(message.from_user.id, "suffix", text)
    await message.reply_text(f"<b>{E_CHECK} Suffix set.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("clearcaption") & filters.private)
async def clearcaption_command(client: Client, message: Message):
    await _ensure_user(message)
    user_id = message.from_user.id
    await db.set_forward_caption_field(user_id, "prefix", "")
    await db.set_forward_caption_field(user_id, "suffix", "")
    await db.clear_forward_replacer(user_id)
    await db.clear_forward_remover(user_id)
    await message.reply_text(f"<b>{E_CHECK} Caption tools reset.</b>", parse_mode=enums.ParseMode.HTML)


# ---------------------------------------------------------------------
# Custom Button
# ---------------------------------------------------------------------
@Client.on_message(filters.command("setbutton") & filters.private)
async def setbutton_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2 or "|" not in message.text:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/setbutton Button Text | https://example.com</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    raw = message.text.split(None, 1)[1]
    text, _, url = raw.partition("|")
    text, url = text.strip(), url.strip()
    if not text or not url.startswith(("http://", "https://")):
        return await message.reply_text(
            f"<b>{E_CROSS} Need button text and a valid https:// URL.</b>", parse_mode=enums.ParseMode.HTML
        )
    await db.set_forward_button(message.from_user.id, text, url)
    await message.reply_text(f"<b>{E_CHECK} Button set:</b> {text} → {url}", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("delbutton") & filters.private)
async def delbutton_command(client: Client, message: Message):
    await _ensure_user(message)
    await db.clear_forward_button(message.from_user.id)
    await message.reply_text(f"<b>{E_CHECK} Button removed.</b>", parse_mode=enums.ParseMode.HTML)


# ---------------------------------------------------------------------
# Filters (message-type allow-list)
# ---------------------------------------------------------------------
@Client.on_message(filters.command("setfilters") & filters.private)
async def setfilters_command(client: Client, message: Message):
    await _ensure_user(message)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>Usage:</b> <code>/setfilters photo,video,document</code>\n\n"
            f"<b>Available types:</b> {', '.join(FILTER_TYPES)}</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    raw_types = [t.strip().lower() for t in message.text.split(None, 1)[1].split(",") if t.strip()]
    invalid = [t for t in raw_types if t not in FILTER_TYPES]
    if invalid:
        return await message.reply_text(
            f"<b>{E_CROSS} Unknown type(s):</b> {', '.join(invalid)}\n"
            f"<b>Available:</b> {', '.join(FILTER_TYPES)}",
            parse_mode=enums.ParseMode.HTML,
        )
    await db.set_forward_filters(message.from_user.id, raw_types)
    await message.reply_text(f"<b>{E_CHECK} Only forwarding:</b> {', '.join(raw_types)}", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("textonlymode") & filters.private)
async def textonlymode_command(client: Client, message: Message):
    """Alias for the reference bot's 'Text Only Mode' — implemented as a
    single-type Filters preset since the effect is identical (only plain
    text messages get forwarded, everything else is skipped)."""
    await _ensure_user(message)
    user_id = message.from_user.id
    if len(message.command) < 2:
        current = await db.get_forward_filters(user_id)
        is_on = current == ["text"]
        return await message.reply_text(
            f"<b>{E_INFO} Text Only Mode:</b> {'🟢 On' if is_on else '🔴 Off'}\n"
            f"<i>(shortcut for /setfilters text)</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    enabled = _on_off(message.command[1])
    if enabled is None:
        return await message.reply_text(f"<b>{E_WARN} Usage:</b> <code>/textonlymode on|off</code>", parse_mode=enums.ParseMode.HTML)
    await db.set_forward_filters(user_id, ["text"] if enabled else [])
    await message.reply_text(
        f"<b>{E_CHECK} Text Only Mode:</b> {'Enabled — only plain text will be forwarded' if enabled else 'Disabled'}",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("pimode") & filters.private)
async def pimode_command(client: Client, message: Message):
    """The reference bot's 'Pi Mode' just unlocks selecting multiple
    target channels — Akbots' /addtarget already supports unlimited
    targets natively, so this is a no-op that explains that."""
    await message.reply_text(
        f"<blockquote>{E_INFO} <b>Pi Mode</b>\n\n"
        f"<i>Nothing to toggle — multiple target channels are already "
        f"supported by default. Just add more with /addtarget.</i></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )
async def clearfilters_command(client: Client, message: Message):
    await _ensure_user(message)
    await db.set_forward_filters(message.from_user.id, [])
    await message.reply_text(f"<b>{E_CHECK} Filters cleared — forwarding every message type.</b>", parse_mode=enums.ParseMode.HTML)


# ---------------------------------------------------------------------
# Extra AK Manager toggles: Numbering, Bullets, Username/Link Remover,
# Delta (source-link), Theta (image+caption only), Blast (parallel
# sends), Course Sellers (remover preset)
# ---------------------------------------------------------------------
def _on_off(raw: str):
    v = (raw or "").strip().lower()
    if v in ("on", "yes", "true", "enable", "enabled", "1"):
        return True
    if v in ("off", "no", "false", "disable", "disabled", "0"):
        return False
    return None


@Client.on_message(filters.command("numbering") & filters.private)
async def numbering_command(client: Client, message: Message):
    await _ensure_user(message)
    user_id = message.from_user.id
    if len(message.command) < 2:
        extra = await db.get_forward_extra(user_id)
        return await message.reply_text(
            f"<blockquote>{E_INFO} <b>Auto-Numbering</b>\n\n"
            f"<b>Current:</b> {'🟢 On' if extra['numbering_enabled'] else '🔴 Off'} "
            f"(style: <code>{extra['numbering_style']}</code>)\n\n"
            f"<code>/numbering on|off</code>\n"
            f"<code>/numbering style dot|bracket|emoji|asterisk|dash</code>\n"
            f"<code>/numbering reset</code> — restart the counter</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1].lower()
    if arg == "reset":
        await db.reset_forward_numbering(user_id)
        return await message.reply_text(f"<b>{E_CHECK} Numbering counter reset to 0.</b>", parse_mode=enums.ParseMode.HTML)
    if arg == "style":
        if len(message.command) < 3 or message.command[2].lower() not in NUMBERING_STYLES:
            return await message.reply_text(
                f"<b>{E_CROSS} Choose one:</b> {', '.join(NUMBERING_STYLES)}", parse_mode=enums.ParseMode.HTML
            )
        await db.set_forward_extra_field(user_id, "numbering_style", message.command[2].lower())
        return await message.reply_text(f"<b>{E_CHECK} Numbering style set:</b> {message.command[2].lower()}", parse_mode=enums.ParseMode.HTML)
    enabled = _on_off(arg)
    if enabled is None:
        return await message.reply_text(f"<b>{E_WARN} Usage:</b> <code>/numbering on|off|style|reset</code>", parse_mode=enums.ParseMode.HTML)
    await db.set_forward_extra_field(user_id, "numbering_enabled", enabled)
    await message.reply_text(f"<b>{E_CHECK} Auto-Numbering:</b> {'Enabled' if enabled else 'Disabled'}", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("bullets") & filters.private)
async def bullets_command(client: Client, message: Message):
    await _ensure_user(message)
    user_id = message.from_user.id
    if len(message.command) < 2:
        extra = await db.get_forward_extra(user_id)
        return await message.reply_text(
            f"<blockquote>{E_INFO} <b>Bullets</b>\n\n"
            f"<b>Current:</b> {'🟢 On' if extra['bullets_enabled'] else '🔴 Off'} "
            f"(style: <code>{extra['bullet_style']}</code>)\n\n"
            f"<code>/bullets on|off</code>\n"
            f"<code>/bullets style style1|style2|style3|style4|style5|style6</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1].lower()
    if arg == "style":
        if len(message.command) < 3 or message.command[2].lower() not in BULLET_STYLES:
            return await message.reply_text(
                f"<b>{E_CROSS} Choose one:</b> {', '.join(BULLET_STYLES)}", parse_mode=enums.ParseMode.HTML
            )
        await db.set_forward_extra_field(user_id, "bullet_style", message.command[2].lower())
        return await message.reply_text(f"<b>{E_CHECK} Bullet style set:</b> {message.command[2].lower()}", parse_mode=enums.ParseMode.HTML)
    enabled = _on_off(arg)
    if enabled is None:
        return await message.reply_text(f"<b>{E_WARN} Usage:</b> <code>/bullets on|off|style</code>", parse_mode=enums.ParseMode.HTML)
    await db.set_forward_extra_field(user_id, "bullets_enabled", enabled)
    await message.reply_text(f"<b>{E_CHECK} Bullets:</b> {'Enabled' if enabled else 'Disabled'}", parse_mode=enums.ParseMode.HTML)


async def _simple_extra_toggle(message: Message, field: str, label: str):
    await _ensure_user(message)
    user_id = message.from_user.id
    if len(message.command) < 2:
        extra = await db.get_forward_extra(user_id)
        return await message.reply_text(
            f"<b>{E_INFO} {label}:</b> {'🟢 On' if extra[field] else '🔴 Off'}",
            parse_mode=enums.ParseMode.HTML,
        )
    enabled = _on_off(message.command[1])
    if enabled is None:
        return await message.reply_text(f"<b>{E_WARN} Usage:</b> <code>on</code> or <code>off</code>", parse_mode=enums.ParseMode.HTML)
    await db.set_forward_extra_field(user_id, field, enabled)
    await message.reply_text(f"<b>{E_CHECK} {label}:</b> {'Enabled' if enabled else 'Disabled'}", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("usernameremover") & filters.private)
async def usernameremover_command(client: Client, message: Message):
    await _simple_extra_toggle(message, "username_remover", "Username Remover")


@Client.on_message(filters.command("linkremover") & filters.private)
async def linkremover_command(client: Client, message: Message):
    await _simple_extra_toggle(message, "link_remover", "Link Remover")


@Client.on_message(filters.command("thetamode") & filters.private)
async def thetamode_command(client: Client, message: Message):
    await _simple_extra_toggle(message, "theta_mode", "Theta Mode (image + caption only)")


@Client.on_message(filters.command("blastmode") & filters.private)
async def blastmode_command(client: Client, message: Message):
    await _simple_extra_toggle(message, "blast_mode", "Blast Mode (parallel sends)")


@Client.on_message(filters.command("coursesellers") & filters.private)
async def coursesellers_command(client: Client, message: Message):
    """One-click preset: strips links + deeplinks + usernames so buyers
    can't trace the original source — mirrors the reference bot's
    Course Sellers Mode exactly."""
    await _simple_extra_toggle(message, "course_sellers_mode", "Course Sellers Mode (strips links & usernames)")


@Client.on_message(filters.command("deltamode") & filters.private)
async def deltamode_command(client: Client, message: Message):
    await _ensure_user(message)
    user_id = message.from_user.id
    if len(message.command) < 2:
        extra = await db.get_forward_extra(user_id)
        return await message.reply_text(
            f"<blockquote>{E_INFO} <b>Delta Mode</b> — adds a source-message link to the caption\n\n"
            f"<b>Current:</b> {'🟢 On' if extra['delta_enabled'] else '🔴 Off'} "
            f"(version: <code>{extra['delta_version']}</code>)\n\n"
            f"<code>/deltamode on|off</code>\n"
            f"<code>/deltamode v1</code> — watermark + hyperlink\n"
            f"<code>/deltamode v2</code> — plain link only</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1].lower()
    if arg in ("v1", "v2"):
        await db.set_forward_extra_field(user_id, "delta_version", arg)
        return await message.reply_text(f"<b>{E_CHECK} Delta Mode version:</b> {arg}", parse_mode=enums.ParseMode.HTML)
    enabled = _on_off(arg)
    if enabled is None:
        return await message.reply_text(f"<b>{E_WARN} Usage:</b> <code>/deltamode on|off|v1|v2</code>", parse_mode=enums.ParseMode.HTML)
    await db.set_forward_extra_field(user_id, "delta_enabled", enabled)
    await message.reply_text(f"<b>{E_CHECK} Delta Mode:</b> {'Enabled' if enabled else 'Disabled'}", parse_mode=enums.ParseMode.HTML)
