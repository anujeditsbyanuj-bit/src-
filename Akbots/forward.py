# Akbots - Don't Remove Credit - @AkBots_Official
#
# Forward tool — ported/simplified from the fwdbot project's core
# source->target forwarding engine (fwdbot's ak_manager.py / regix.py),
# rebuilt to fit Akbots as a single bot-token client (fwdbot's clone-bot /
# multi-tenant / subscription-tier machinery is intentionally NOT ported —
# it doesn't apply here).
#
# Uses copy_message (not forward_messages) for every message, same choice
# fwdbot makes, because copy_message re-sends the content instead of
# relaying it, so it also works on chats with forwarding restricted.
#
# USERBOT FALLBACK: the bot token alone can only touch chats it has been
# added to. For a private chat the bot isn't (and can't be) added to,
# /setsource and /settarget fall back to the user's own account — reusing
# the exact session string already stored by Akbots/session.py's /login
# flow (db.get_session), same pattern start.py already uses for restricted
# saves (Client(session_string=..., in_memory=True)). No new login system
# was built here; if the person hasn't run /login yet, they're told to.
#
# A single forward job needs ONE client that can read the source AND write
# the target, so at launch time we work out whether the bot alone can do
# both, or whether the personal account (userbot) has to do both — mixing
# (bot for one side, userbot for the other) isn't possible for copy_message
# and is reported back to the user as a clear limitation rather than
# silently failing.
#
# Commands:
#   /setsource <chat_id or @username>
#   /settarget <chat_id or @username>
#   /fwd <start_msg_id> <end_msg_id>   — forwards that id range, source->target
#   /fwdresume <end_msg_id>            — continues from where /fwd last left off
#   /fwdstatus                         — show source/target/progress
#   /fwdcancel                         — stop the running forward job
#   /reset                             — wipe all forward config (confirm first)
#   /fwd_caption <text> | off          — prefix prepended to each forwarded
#                                         message's caption/text (off to clear).
#                                         Also accepts a template using
#                                         {filename} {size} {caption} {year}
#                                         {language} {quality} {type} — if the
#                                         text contains "{" it's treated as the
#                                         FULL caption (not prepended above
#                                         the original).
#
# The button/filters/keywords/extension-block/size-limit/skip-duplicate/
# forward-tag settings this job reads (via db.get_fwd_settings) are managed
# from ftm_manager_menu.py's AK Manager inline menu and forward_engine.py's
# own commands (setbutton/setfilters/etc) — the /fwd_button, /fwd_filter,
# /fwd_keyword, /fwd_extblock and /fwd_settings commands that used to
# duplicate that here have been removed.

import asyncio
import os
import re
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError
from config import API_ID, API_HASH
from database.db import db

# Regex-based button syntax, ported from fwdbot's plugins/test.py (BTN_URL_REGEX /
# parse_buttons). Lets a button be written as [Text][buttonurl:https://...] and
# supports multiple buttons: put :same at the end of a pair to keep it on the
# same row as the one before it, otherwise it starts a new row.
#   [Join][buttonurl:https://t.me/chan]
#   [A][buttonurl:https://a.com][B][buttonurl:https://b.com:same]   -> A,B same row
BTN_URL_REGEX = re.compile(r"(\[([^\[]+?)]\[buttonurl:/{0,2}(.+?)(:same)?])")


def parse_buttons(text, markup=True):
    """Extract [Text][buttonurl:url] / [Text][buttonurl:url:same] pairs from
    text and build an InlineKeyboardMarkup (or the raw button rows if
    markup=False). Returns None if nothing matched."""
    if not text:
        return None
    buttons = []
    for match in BTN_URL_REGEX.finditer(text):
        n_escapes = 0
        to_check = match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1
        if n_escapes % 2 == 0:
            btn_text, btn_url, same_row = match.group(2), match.group(3).strip(), bool(match.group(4))
            try:
                button = make_button(btn_text, url=btn_url, style=_BS.PRIMARY if _BS else None)
            except Exception:
                continue
            if same_row and buttons:
                buttons[-1].append(button)
            else:
                buttons.append([button])
    if not buttons:
        return None
    return InlineKeyboardMarkup(buttons) if markup else buttons


def strip_buttons(text):
    """Removes [Text][buttonurl:url] markup from a caption so it isn't shown
    as literal text once the button has been extracted from it."""
    if not text:
        return text
    return BTN_URL_REGEX.sub("", text).strip()

from Akbots.direct_utils import E_CHECK, E_CROSS, E_INFO, E_BOLT, E_ROCKET, safe_edit
from Akbots.start import humanbytes, make_button, BUTTON_STYLE_SUPPORTED
from Akbots import task_manager
from Akbots import titanium

# Colored inline buttons (same ButtonStyle mechanism start.py uses) — kept
# behind BUTTON_STYLE_SUPPORTED so this still works on plain pyrogram forks
# that don't have kurigram's ButtonStyle/icon_custom_emoji_id support.
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

# --------------------------------------------------------------------------
# Caption template variables — {filename} {size} {caption} {year} {language}
# {quality} {type} — ported conceptually from the FTM Forward Bot's custom
# caption feature. A caption_prefix containing "{" is treated as a full
# template (its rendered output IS the whole caption, nothing auto-appended
# below it); one without "{" keeps the old plain-prefix-above-original
# behaviour for backward compatibility with captions saved before this.
# --------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_QUALITY_RE = re.compile(r"\d{3,4}p", re.IGNORECASE)
# Ordered specific-before-generic: "dual audio"/"dualaudio" must be checked
# (and consume "dual") before the bare "dual" token, or a filename like
# "Hindi.DualAudio.mkv" (no space between Dual and Audio) matches "dual" on
# its own and reports "Dual" instead of "Dual Audio".
_LANGUAGE_TOKENS = [
    ("dual audio", "Dual Audio"), ("dualaudio", "Dual Audio"),
    ("multi audio", "Multi Audio"), ("multiaudio", "Multi Audio"),
    ("hindi", "Hindi"), ("english", "English"), ("tamil", "Tamil"), ("telugu", "Telugu"),
    ("kannada", "Kannada"), ("malayalam", "Malayalam"), ("punjabi", "Punjabi"),
    ("bengali", "Bengali"), ("marathi", "Marathi"), ("gujarati", "Gujarati"), ("urdu", "Urdu"),
    ("korean", "Korean"), ("japanese", "Japanese"), ("chinese", "Chinese"),
    ("spanish", "Spanish"), ("french", "French"), ("german", "German"),
    ("dual", "Dual"), ("multi", "Multi"),
]


def _extract_year(name: str) -> str:
    m = _YEAR_RE.search(name or "")
    return m.group(0) if m else ""


def _extract_quality(name: str) -> str:
    m = _QUALITY_RE.search(name or "")
    return m.group(0) if m else ""


def _extract_language(name: str) -> str:
    low = (name or "").lower().replace(".", " ").replace("_", " ")
    found = []
    consumed = low
    for tok, label in _LANGUAGE_TOKENS:
        if tok in consumed:
            found.append(label)
            consumed = consumed.replace(tok, " ")  # so "dual" doesn't also match inside consumed "dualaudio"
    return " + ".join(dict.fromkeys(found)) if found else ""


def _render_caption_template(template: str, orig: Message, mtype: str) -> str:
    fname = _msg_file_name(orig) or ""
    size = _msg_size_bytes(orig)
    values = {
        "filename": fname,
        "size": humanbytes(size) if size else "",
        "caption": (orig.caption.html if orig.caption else (orig.text.html if orig.text else "")) or "",
        "year": _extract_year(fname),
        "language": _extract_language(fname),
        "quality": _extract_quality(fname),
        "type": mtype,
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError):
        # Unknown {placeholder} in the template — leave it untouched rather
        # than crashing the job on a typo'd variable name.
        out = template
        for k, v in values.items():
            out = out.replace("{" + k + "}", v)
        return out

E_PENCIL = '✏️'
MAX_RANGE = 5000          # hard cap per /fwd call, matches fwdbot-style batch limits
DELAY_SECS = 1.2          # gap between copies — keeps well under Bot API flood limits
PROGRESS_EVERY = 15       # edit status message every N messages
SAVE_EVERY = 5            # persist resume-checkpoint every N messages

# "kw:word" / "maxsize:100MB" / "minsize:10MB" tokens inside a job's stored
# filters list — still read+enforced by _should_skip() below even though
# the /fwd_filter command that used to write them has been removed (that
# duplicated ftm_manager_menu.py / forward_engine.py's own filter UI).
import re as _re_filter
_SIZE_TOKEN_RE = _re_filter.compile(r"^(maxsize|minsize):(\d+(?:\.\d+)?)(b|kb|mb|gb)$")
_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}


def _parse_size_token(token: str):
    """Returns (kind, bytes) for a 'maxsize:100MB' / 'minsize:10MB' token,
    or None if it doesn't match that syntax."""
    m = _SIZE_TOKEN_RE.match(token)
    if not m:
        return None
    kind, num, unit = m.groups()
    return kind, int(float(num) * _SIZE_UNITS[unit])


class _SkipMessage(Exception):
    """Raised internally by _copy_with_extras() to signal 'count this as
    skipped and move on' — e.g. a filtered message type, or a message id
    that no longer exists. Never leaves _forward_loop()."""
    pass


# user_id -> asyncio.Task, so /fwdcancel can stop just the forward job
# (separate from task_manager's global /cancel_all, though it's registered
# there too for visibility/consistency with the rest of the bot).
_RUNNING = {}


def _parse_chat(raw: str):
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw if raw.startswith("@") else f"@{raw}"


async def _make_userbot(user_id: int):
    """Spins up a connected Client from the person's stored /login session
    string, or returns None if they haven't logged in / it's expired.
    Caller owns the connection and must disconnect() it when done."""
    session_str = await db.get_session(user_id)
    if not session_str:
        return None
    acc = Client(
        f"fwd_userbot_{user_id}",
        session_string=session_str,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
        max_concurrent_transmissions=10,
    )
    try:
        await acc.connect()
        return acc
    except Exception:
        return None


async def _resolve_chat(bot_client: Client, user_id: int, chat_ref):
    """Tries the bot client first, then the person's userbot session.
    Returns (chat, via, userbot_or_None). userbot is left connected if it
    was the one that worked, so the caller can reuse/disconnect it."""
    try:
        chat = await bot_client.get_chat(chat_ref)
        return chat, "bot", None
    except RPCError:
        pass

    acc = await _make_userbot(user_id)
    if acc is None:
        return None, None, None
    try:
        chat = await acc.get_chat(chat_ref)
        return chat, "user", acc
    except RPCError:
        await acc.disconnect()
        return None, None, None




async def _resolve_and_store(client: Client, message: Message, which: str):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/set{which} -1001234567890</code> or <code>/set{which} @channelusername</code>\n"
            f"<i>Tries the bot first; if the bot can't access it, falls back to your /fwd_login session.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    chat_ref = _parse_chat(message.command[1])
    chat, via, acc = await _resolve_chat(client, user_id, chat_ref)
    if acc:
        await acc.disconnect()

    if not chat:
        has_session = bool(await db.get_session(user_id))
        hint = (
            "Your login session can't see it either — double check the chat id/username."
            if has_session else
            f"The bot isn't in that chat, and you haven't run /fwd_login yet — do that if it's a "
            f"private chat, so your own account can be used instead."
        )
        return await message.reply_text(
            f"<b>{E_CROSS} Can't access that chat.</b> {hint}",
            parse_mode=enums.ParseMode.HTML
        )

    if which == "source":
        await db.set_fwd_source(user_id, chat.id, via)
    else:
        await db.set_fwd_target(user_id, chat.id, via)

    via_note = f" <i>(via {'your account' if via == 'user' else 'the bot'})</i>"
    await message.reply_text(
        f"<b>{E_CHECK} {which.capitalize()} set:</b> {chat.title or chat.first_name or chat.id} "
        f"(<code>{chat.id}</code>){via_note}",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("setsource"))
async def setsource_cmd(client: Client, message: Message):
    await _resolve_and_store(client, message, "source")


@Client.on_message(filters.private & filters.command("settarget"))
async def settarget_cmd(client: Client, message: Message):
    await _resolve_and_store(client, message, "target")


@Client.on_message(filters.private & filters.command("fwdstatus"))
async def fwdstatus_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    s = await db.get_fwd_settings(user_id)
    running = "🟢 running" if user_id in _RUNNING and not _RUNNING[user_id].done() else "⚪ idle"
    login_note = f"{E_CHECK} logged in" if s['has_login'] else f"{E_CROSS} not logged in — /fwd_login"
    await message.reply_text(
        f"<b>{E_INFO} Forward status</b>\n\n"
        f"<b>Source:</b> <code>{s['source'] or 'not set'}</code> <i>({s['source_via']})</i>\n"
        f"<b>Target:</b> <code>{s['target'] or 'not set'}</code> <i>({s['target_via']})</i>\n"
        f"<b>Last forwarded id:</b> <code>{s['last_id'] or '-'}</code>\n"
        f"<b>Login:</b> {login_note}\n"
        f"<b>Status:</b> {running}\n\n"
        f"<i>Use /fwd_caption for the caption prefix, or /akanager for the button/filters/keywords/extension/size-limit/tag panel.</i>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("fwdcancel"))
async def fwdcancel_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    task = _RUNNING.get(user_id)
    if not task or task.done():
        return await message.reply_text(f"<b>{E_INFO} No forward job is running.</b>", parse_mode=enums.ParseMode.HTML)
    task.cancel()
    await message.reply_text(f"<b>🚫 Stopping...</b> current message will finish, then it'll halt.", parse_mode=enums.ParseMode.HTML)


# --------------------------------------------------------------------------
# /reset — wipe all forward config (source/target/caption/button/filters),
# ported from fwdbot's confirm-then-reset flow.
# --------------------------------------------------------------------------

@Client.on_message(filters.private & filters.command("reset"))
async def reset_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    task = _RUNNING.get(user_id)
    if task and not task.done():
        return await message.reply_text(
            f"<b>{E_CROSS} A forward job is running.</b> Run /fwdcancel first, then /reset.",
            parse_mode=enums.ParseMode.HTML
        )
    confirm_kb = InlineKeyboardMarkup([[
        make_button("✅ Yes, reset", callback_data=f"fwdreset:yes:{user_id}", style=_BS.SUCCESS if _BS else None),
        make_button("❌ Cancel", callback_data="fwdreset:no", style=_BS.DANGER if _BS else None),
    ]])
    await message.reply_text(
        f"<b>⚠️ Reset confirmation</b>\n\n"
        f"This will clear your <b>source</b>, <b>target</b>, <b>progress</b>, "
        f"<b>caption</b>, <b>button</b>, <b>filters</b>, <b>keywords</b>, "
        f"<b>blocked extensions</b>, <b>size limit</b>, <b>skip-duplicate history</b>, "
        f"and <b>forward tag</b> setting.\n"
        f"<i>Your /login session is not affected.</i>\n\n"
        f"Are you sure?",
        reply_markup=confirm_kb, parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex(r"^fwdreset:"))
async def reset_confirm_cb(client: Client, callback_query: CallbackQuery):
    action = callback_query.data.split(":")
    if action[1] == "no":
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_INFO} Reset cancelled.</b>", parse_mode=enums.ParseMode.HTML)
    owner_id = int(action[2])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("This isn't your reset request.", show_alert=True)
    await db.reset_fwd_settings(owner_id)
    await safe_edit(callback_query.message.edit_text, 
        f"<b>{E_CHECK} Forward settings reset.</b> All forward settings are cleared.",
        parse_mode=enums.ParseMode.HTML
    )


# --------------------------------------------------------------------------
# Caption prefix (only forward-config command left here — button/filters/
# keywords/extension-block/size-limit/tag are all managed via /akanager
# and forward_engine.py's own commands now)
# --------------------------------------------------------------------------

@Client.on_message(filters.private & filters.command("fwd_caption"))
async def fwd_caption_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if len(message.command) < 2:
        s = await db.get_fwd_settings(user_id)
        current = f"<code>{s['caption']}</code>" if s['caption'] else "<i>not set</i>"
        return await message.reply_text(
            f"<b>{E_PENCIL} Forward caption prefix</b>\n\n"
            f"<b>Current:</b> {current}\n\n"
            f"<b>Usage:</b> <code>/fwd_caption &lt;text&gt;</code>\n"
            f"<code>/fwd_caption off</code> — clear it\n\n"
            f"<i>Prepended to each forwarded message's caption/text (HTML formatting allowed). "
            f"The original caption/text is kept below it.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    arg = message.text.split(" ", 1)[1].strip()
    if arg.lower() == "off":
        await db.clear_fwd_caption(user_id)
        return await message.reply_text(f"<b>{E_CHECK} Caption prefix cleared.</b>", parse_mode=enums.ParseMode.HTML)

    await db.set_fwd_caption(user_id, arg)
    await message.reply_text(
        f"<b>{E_CHECK} Caption prefix saved.</b>\n\n<b>Preview:</b>\n{arg}",
        parse_mode=enums.ParseMode.HTML
    )



async def _pick_job_client(bot_client: Client, user_id: int, source, target, source_via, target_via):
    """Works out ONE client that can both read `source` and write `target`
    for this run. Returns (client, owns_it) — owns_it=True means the caller
    must disconnect() it when the job ends (it's a fresh userbot client);
    False means it's the shared bot client, leave it alone."""
    if source_via == "bot" and target_via == "bot":
        job_client, is_clone, clone_username = await titanium.get_job_client(user_id, bot_client, source, target)
        return job_client, False  # clone bots live in titanium._CLONE_CACHE and are reused — never disconnect here

    # Either side needs the personal account — the same account has to be
    # able to reach both, since copy_message runs on a single client.
    acc = await _make_userbot(user_id)
    if acc is None:
        return None, False
    try:
        await acc.get_chat(source)
        await acc.get_chat(target)
        return acc, True
    except RPCError:
        await acc.disconnect()
        return None, False


def _build_button_markup(button):
    """button is whatever db.get_fwd_settings() returns for 'button': either
    the legacy {'text':..., 'url':...} dict (old installs), the new raw
    [Text][buttonurl:url] string (supports multiple buttons/rows via
    :same), or None. Returns an InlineKeyboardMarkup, or None if nothing's
    set — wrapped in try/except since this runs at job-launch time against
    whatever's in the DB."""
    if not button:
        return None
    try:
        if isinstance(button, dict):
            return InlineKeyboardMarkup([[make_button(button["text"], url=button["url"], style=_BS.PRIMARY if _BS else None)]])
        return parse_buttons(button)
    except Exception:
        return None


def _msg_file_unique_id(msg: Message):
    """Returns the file_unique_id of msg's media (stable content-hash across
    re-uploads, unlike file_id) for the Skip Duplicate feature, or None for
    text/messages with no media."""
    for attr in ("document", "video", "audio", "voice", "animation", "photo", "sticker"):
        obj = getattr(msg, attr, None)
        if obj and getattr(obj, "file_unique_id", None):
            return obj.file_unique_id
    return None


def _msg_file_name(msg: Message):
    """Returns the file_name of msg's media (any type that carries one), or
    None for photos/text/other types that don't have a filename."""
    for attr in ("document", "video", "audio", "voice", "animation"):
        obj = getattr(msg, attr, None)
        if obj and getattr(obj, "file_name", None):
            return obj.file_name
    return None


def _msg_type_and_ext(msg: Message):
    """Returns (type_name, extension_or_None) for filter matching. extension
    includes the leading dot and is lowercased, e.g. '.pdf'."""
    for attr, name in (
        ("photo", "photo"), ("video", "video"), ("document", "document"),
        ("audio", "audio"), ("voice", "voice"), ("sticker", "sticker"),
        ("animation", "animation"),
    ):
        obj = getattr(msg, attr, None)
        if obj:
            fname = getattr(obj, "file_name", None)
            ext = os.path.splitext(fname)[1].lower() if fname else None
            return name, ext
    if msg.text:
        return "text", None
    return "other", None


def _msg_size_bytes(msg: Message):
    """Returns the byte size of the media in msg, or None for text/no media."""
    for attr in ("document", "video", "audio", "voice", "animation", "photo"):
        obj = getattr(msg, attr, None)
        if obj:
            return getattr(obj, "file_size", None)
    return None


def _should_skip(msg: Message, filters_set: set, keywords=None, ext_block=None, size_limit_mb=0) -> bool:
    mtype, ext = _msg_type_and_ext(msg)

    # Keywords allowlist ("File with these keywords in file name will
    # forward") — only applies to media with a filename; if set, anything
    # whose filename doesn't contain one of the keywords is skipped.
    if keywords:
        fname = (_msg_file_name(msg) or "").lower()
        if not fname or not any(kw in fname for kw in keywords):
            return True

    # Extension blocklist ("Files with these extensions will not forward")
    if ext_block and ext and ext in ext_block:
        return True

    # Size limit — skip media above this many MB (0 = unlimited)
    if size_limit_mb:
        size = _msg_size_bytes(msg)
        if size and size > size_limit_mb * 1024 * 1024:
            return True

    if not filters_set:
        return False
    if mtype in filters_set:
        return True
    if ext and ext in filters_set:
        return True

    haystack = None
    size = None
    for item in filters_set:
        if item.startswith("kw:"):
            if haystack is None:
                haystack = ((msg.text or "") + " " + (msg.caption or "")).lower()
            if item[3:] in haystack:
                return True
        elif item.startswith(("maxsize:", "minsize:")):
            parsed = _parse_size_token(item)
            if not parsed:
                continue
            if size is None:
                size = _msg_size_bytes(msg)
            if size is None:
                continue
            kind, limit = parsed
            if kind == "maxsize" and size > limit:
                return True
            if kind == "minsize" and size < limit:
                return True
    return False


async def _copy_with_extras(job_client: Client, source, target, msg_id: int,
                             caption_prefix, button_markup, filters_set: set,
                             keywords=None, ext_block=None, size_limit_mb=0,
                             skip_duplicate=False, user_id=None, tag_mode=False):
    """The slow path — used whenever a caption prefix, filters, dedup, or
    tag mode are configured, since all of them need the original message
    fetched first. copy_message()'s own caption param is ignored for
    plain-text messages (Bot API only applies it to media), so text
    messages with a caption prefix are re-sent via send_message() instead
    of copy_message() — everything else still goes through copy_message()
    so restricted-forwarding chats keep working.

    tag_mode=True switches to forward_messages() instead — Telegram only
    lets you change the caption/attach a button on a COPY, never on a real
    forward (that's what keeps the "Forwarded from" tag intact), so caption
    prefix and button are silently not applied while tag_mode is on. That
    tradeoff is surfaced in the settings panel, not hidden here."""
    orig = await job_client.get_messages(source, msg_id)
    if orig is None or getattr(orig, "empty", False):
        raise _SkipMessage()

    if _should_skip(orig, filters_set, keywords=keywords, ext_block=ext_block, size_limit_mb=size_limit_mb):
        raise _SkipMessage()

    file_unique_id = _msg_file_unique_id(orig)
    if skip_duplicate and file_unique_id and user_id is not None:
        if await db.is_fwd_duplicate(user_id, file_unique_id):
            raise _SkipMessage()

    if tag_mode:
        await job_client.forward_messages(chat_id=target, from_chat_id=source, message_ids=msg_id)
    elif not caption_prefix:
        await job_client.copy_message(chat_id=target, from_chat_id=source, message_id=msg_id, reply_markup=button_markup)
    elif orig.media:
        if "{" in caption_prefix:
            mtype, _ = _msg_type_and_ext(orig)
            new_caption = _render_caption_template(caption_prefix, orig, mtype)
        else:
            original_html = orig.caption.html if orig.caption else ""
            new_caption = f"{caption_prefix}\n\n{original_html}" if original_html else caption_prefix
        await job_client.copy_message(
            chat_id=target, from_chat_id=source, message_id=msg_id,
            caption=new_caption, parse_mode=enums.ParseMode.HTML, reply_markup=button_markup
        )
    elif orig.text:
        if "{" in caption_prefix:
            new_text = _render_caption_template(caption_prefix, orig, "text")
        else:
            new_text = f"{caption_prefix}\n\n{orig.text.html}"
        await job_client.send_message(
            target, new_text, parse_mode=enums.ParseMode.HTML,
            reply_markup=button_markup, disable_web_page_preview=True
        )
    else:
        # Service message or something with neither text nor media —
        # nothing to prefix, just copy it through as-is.
        await job_client.copy_message(chat_id=target, from_chat_id=source, message_id=msg_id, reply_markup=button_markup)

    if skip_duplicate and file_unique_id and user_id is not None:
        await db.record_fwd_duplicate(user_id, file_unique_id)


async def _forward_loop(job_client: Client, owns_client: bool, message: Message, status: Message, user_id: int,
                         source, target, start_id: int, end_id: int,
                         caption_prefix=None, button_markup=None, filters_set=None,
                         keywords=None, ext_block=None, size_limit_mb=0,
                         skip_duplicate=False, tag_mode=False):
    filters_set = filters_set or set()
    keywords = keywords or []
    ext_block = ext_block or []
    need_extras = bool(caption_prefix) or bool(filters_set) or bool(keywords) or bool(ext_block) or bool(size_limit_mb) or skip_duplicate or tag_mode
    done = skipped = failed = 0
    total = end_id - start_id + 1
    msg_id = start_id
    try:
        while msg_id <= end_id:
            try:
                if need_extras:
                    await _copy_with_extras(
                        job_client, source, target, msg_id, caption_prefix, button_markup, filters_set,
                        keywords=keywords, ext_block=ext_block, size_limit_mb=size_limit_mb,
                        skip_duplicate=skip_duplicate, user_id=user_id, tag_mode=tag_mode,
                    )
                else:
                    await job_client.copy_message(chat_id=target, from_chat_id=source, message_id=msg_id, reply_markup=button_markup)
                done += 1
            except _SkipMessage:
                skipped += 1
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                continue  # retry same msg_id
            except RPCError:
                skipped += 1  # deleted / service message / no access to that one message
            except Exception:
                failed += 1

            if msg_id % SAVE_EVERY == 0:
                await db.set_fwd_progress(user_id, msg_id, {"source": source, "target": target})

            if (done + skipped + failed) % PROGRESS_EVERY == 0:
                processed = done + skipped + failed
                pct = processed * 100 // total
                await safe_edit(status.edit_text, 
                    f"<b>{E_ROCKET} Forwarding...</b> {pct}%\n"
                    f"<code>{processed}/{total}</code> | ✅ {done} | ⏭ {skipped} | ❌ {failed}\n"
                    f"<i>Currently at id {msg_id}</i>",
                    parse_mode=enums.ParseMode.HTML
                )

            msg_id += 1
            await asyncio.sleep(DELAY_SECS)

        await db.set_fwd_progress(user_id, end_id, {"source": source, "target": target})
        await safe_edit(status.edit_text, 
            f"<b>{E_CHECK} Forward complete.</b>\n"
            f"<b>Range:</b> <code>{start_id}-{end_id}</code>\n"
            f"✅ {done} sent | ⏭ {skipped} skipped | ❌ {failed} failed",
            parse_mode=enums.ParseMode.HTML
        )
    except asyncio.CancelledError:
        await db.set_fwd_progress(user_id, msg_id - 1, {"source": source, "target": target})
        await safe_edit(status.edit_text, 
            f"<b>🚫 Forward stopped at id {msg_id - 1}.</b>\n"
            f"✅ {done} sent | ⏭ {skipped} skipped | ❌ {failed} failed\n"
            f"<i>Resume anytime with /fwdresume {end_id}</i>",
            parse_mode=enums.ParseMode.HTML
        )
        raise
    finally:
        _RUNNING.pop(user_id, None)
        if owns_client:
            try:
                await job_client.disconnect()
            except Exception:
                pass


async def _launch(client: Client, message: Message, start_id: int, end_id: int):
    user_id = message.from_user.id
    s = await db.get_fwd_settings(user_id)
    if not s["source"] or not s["target"]:
        return await message.reply_text(
            f"<b>{E_INFO} Set both first:</b> <code>/setsource</code> and <code>/settarget</code>.",
            parse_mode=enums.ParseMode.HTML
        )
    if user_id in _RUNNING and not _RUNNING[user_id].done():
        return await message.reply_text(f"<b>{E_INFO} A forward job is already running.</b> Use /fwdcancel to stop it first.", parse_mode=enums.ParseMode.HTML)
    if start_id > end_id:
        return await message.reply_text(f"<b>{E_CROSS} Start id must be ≤ end id.</b>", parse_mode=enums.ParseMode.HTML)
    if end_id - start_id + 1 > MAX_RANGE:
        return await message.reply_text(f"<b>{E_CROSS} Range too big — max {MAX_RANGE} messages per run.</b> Split it up.", parse_mode=enums.ParseMode.HTML)

    status = await message.reply_text(f"<b>{E_BOLT} Starting forward job...</b>", parse_mode=enums.ParseMode.HTML)

    job_client, owns_client = await _pick_job_client(
        client, user_id, s["source"], s["target"], s["source_via"], s["target_via"]
    )
    if job_client is None:
        via_needed = "your /login account" if "user" in (s["source_via"], s["target_via"]) else "the bot"
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Can't run this job.</b> {via_needed} can't reach both the source and "
            f"target chat at once — since source and target were set through different access "
            f"methods, no single connection can do the copy. Re-run /setsource and /settarget so "
            f"both resolve the same way (both via the bot, or both via /login).",
            parse_mode=enums.ParseMode.HTML
        )

    task = asyncio.ensure_future(_forward_loop(
        job_client, owns_client, message, status, user_id, s["source"], s["target"], start_id, end_id,
        caption_prefix=s.get("caption"),
        button_markup=None if s.get("tag") else _build_button_markup(s.get("button")),
        filters_set=set(s.get("filters") or []),
        keywords=[kw.lower() for kw in (s.get("keywords") or [])],
        ext_block=set(s.get("ext_block") or []),
        size_limit_mb=s.get("size_limit_mb", 0),
        skip_duplicate=s.get("skip_duplicate", False),
        tag_mode=s.get("tag", False),
    ))
    _RUNNING[user_id] = task
    task_id = task_manager.register(user_id, task, f"Forward {start_id}-{end_id}")
    task.add_done_callback(lambda t: task_manager.unregister(user_id, task_id))


@Client.on_message(filters.private & filters.command(["fwd", "forward"]))
async def fwd_cmd(client: Client, message: Message):
    if len(message.command) < 3 or not all(p.lstrip("-").isdigit() for p in message.command[1:3]):
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/fwd &lt;start_msg_id&gt; &lt;end_msg_id&gt;</code>\n"
            f"<i>Message id = the number you see when you copy a message link, e.g. "
            f"t.me/c/12345/<b>678</b> → id is 678.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    await _launch(client, message, int(message.command[1]), int(message.command[2]))


@Client.on_message(filters.private & filters.command("fwdresume"))
async def fwdresume_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    s = await db.get_fwd_settings(user_id)
    if not s["last_id"]:
        return await message.reply_text(f"<b>{E_INFO} Nothing to resume.</b> Use /fwd to start a fresh job.", parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2 or not message.command[1].lstrip("-").isdigit():
        return await message.reply_text(f"<b>{E_INFO} Usage:</b> <code>/fwdresume &lt;end_msg_id&gt;</code>", parse_mode=enums.ParseMode.HTML)
    await _launch(client, message, int(s["last_id"]) + 1, int(message.command[1]))
