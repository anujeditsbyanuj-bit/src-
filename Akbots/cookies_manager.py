# Generic per-domain cookies store.
#
# config.py only ever wired up cookies for three hardcoded sites (YouTube,
# Instagram, Facebook). A lot of "quality options are missing" / "site wants
# a login" problems on OTHER sites are simply because yt-dlp is fetching
# the page logged-out, and the page serves a smaller/lower-quality format
# list (or an entirely different, JS-stub page) to anonymous visitors.
#
# This lets an admin upload a Netscape-format cookies.txt for ANY domain,
# which ytdl.py's _cookies_for() then picks up automatically for every
# link from that domain (and its subdomains) - no code change needed per
# site.
#
# Usage:
#   /cookie                                 — button panel (Add / View / Delete)
#   /setcookies example.com                — then send the cookies.txt file
#   /setcookies example.com  (as a document caption, file attached directly)
#   /listcookies                            — see which domains have cookies set
#   /delcookies example.com                 — remove them

import os
import re
import time
import asyncio
from urllib.parse import urlparse
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageNotModified
from config import ADMINS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'

COOKIES_DIR = "cookies/custom"
os.makedirs(COOKIES_DIR, exist_ok=True)

PANEL_TEXT = (
    "🍪 <b>ᴄᴏᴏᴋɪᴇ ᴄᴏɴᴛʀᴏʟ ᴘᴀɴᴇʟ</b>\n\n"
    "Upload cookies to bypass login walls and age restrictions for virtually "
    "any website, including:\n"
    "• YouTube\n• Instagram\n• TikTok\n• XHamster\n• Twitter / X\n• Zee5\n• Voot\n• Hotstar & more!\n\n"
    "🛠 <b>ʜᴏᴡ ᴛᴏ ɢᴇᴛ ᴄᴏᴏᴋɪᴇs:</b>\n"
    "1. Install the <b>ᴄᴏᴏᴋɪᴇ-ᴇᴅɪᴛᴏʀ</b> extension in your PC/Mobile browser.\n"
    "2. Go to the website you want to download from (e.g. youtube.com) and log in.\n"
    "3. Click the Cookie-Editor extension button.\n"
    "4. Click <b>ᴇxᴘᴏʀᴛ → ᴇxᴘᴏʀᴛ ᴀs ɴᴇᴛsᴄᴀᴘᴇ</b> (Format must be Netscape!).\n"
    "5. Paste the copied text into a new text file and save it as <code>cookies.txt</code>.\n\n"
    "Select an option below to manage your cookies:"
)

ADD_TIMEOUT = 60  # seconds

# user_id -> domain, set by /setcookies while waiting for the file to follow
# as the user's next message.
_pending_setcookies: dict[int, str] = {}

# user_id -> {"expires": monotonic deadline, "chat_id": int, "msg_id": int}
# set by the "Add Cookie" button while waiting for the next document.
_pending_panel: dict[int, dict] = {}


def _panel_markup(mode: str = "root") -> InlineKeyboardMarkup:
    if mode == "delete":
        files = sorted(f[:-4] for f in os.listdir(COOKIES_DIR) if f.endswith(".txt"))
        rows = [[make_button(f"🗑 {d}", callback_data=f"ckpanel:del:{d}", style=_BS.PRIMARY if _BS else None)] for d in files]
        rows.append([make_button("⬅️ ʙᴀᴄᴋ", callback_data="ckpanel:root", style=_BS.DANGER if _BS else None)])
        return InlineKeyboardMarkup(rows)
    return InlineKeyboardMarkup([
        [
            make_button("➕ ᴀᴅᴅ ᴄᴏᴏᴋɪᴇ", callback_data="ckpanel:add", style=_BS.PRIMARY if _BS else None),
            make_button("👁 ᴠɪᴇᴡ ᴄᴏᴏᴋɪᴇs", callback_data="ckpanel:view", style=_BS.PRIMARY if _BS else None),
        ],
        [make_button("🗑 ᴅᴇʟᴇᴛᴇ ᴄᴏᴏᴋɪᴇ", callback_data="ckpanel:delmenu", style=_BS.DANGER if _BS else None)],
    ])


def _detect_domain_verbose(path: str):
    """Same detection as _detect_domain, but also returns the full Counter
    of domain -> cookie-count seen in the file, so the caller can show the
    admin what else was in there (helps catch a wrong guess immediately
    instead of it silently failing downloads later)."""
    from collections import Counter
    counts = Counter()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                if line.startswith("#"):
                    if not line.startswith("#HttpOnly_"):
                        continue
                    line = line[len("#HttpOnly_"):]
                cols = _cookie_line_cols(line)
                if len(cols) < 7:
                    continue
                d = cols[0].strip().lower().lstrip(".")
                if d and "." in d:
                    counts[d] += 1
    except Exception:
        return None, counts
    if not counts:
        return None, counts
    max_count = max(counts.values())
    tied = sorted([d for d, c in counts.items() if c == max_count], key=len)
    return tied[0], counts


def _detect_domain(path: str) -> str | None:
    """Best-effort parse of a Netscape cookies.txt to guess which domain it's
    for. Picks whichever domain has the MOST cookie lines in the file, not
    the shortest name. A real browser export often carries a handful of
    stray cookies from other domains too (Google/YouTube login, embedded
    players, ad/analytics pixels, ...) alongside the site you actually meant
    - and a short name like 'youtube.com' can easily out-rank the intended
    domain if we just sort by string length. The domain the user was
    actually logged into always has far more cookies (session id, auth
    tokens, preferences, ...) than an incidental one (usually 1-2 cookies),
    so counting is a much more reliable signal than name length. Ties are
    broken by shorter name (prefers the parent over a subdomain)."""
    domain, _ = _detect_domain_verbose(path)
    return domain


def _cookie_line_cols(line: str) -> list[str]:
    """Splits a Netscape cookies.txt line into its 7 tab-separated columns.

    Real .txt FILE uploads keep literal tabs intact, but a lot of users
    paste cookies straight into the chat as a text message instead — and
    Telegram's message-input box commonly collapses/strips literal tab
    characters on paste, replacing them with a run of spaces or nothing at
    all. A strict `line.split("\\t")` then finds zero valid cookie lines,
    _looks_like_netscape_cookies() returns False, and the paste silently
    falls through to ytdl.py's group=2 generic URL auto-detector — which
    matches ANY "https://" substring, including the one in the standard
    "# https://curl.se/docs/http-cookies.html" header comment most cookie
    exporters include, and tries to yt-dlp-download that docs page.

    Falling back to "2+ consecutive spaces" as a separator when there's no
    tab in the line covers that case without needing a literal tab."""
    if "\t" in line:
        return line.split("\t")
    return line.split(None, 6)


def _looks_like_netscape_cookies(text: str, min_hits: int = 2) -> bool:
    """Sniff raw text/file content for the Netscape cookies.txt format,
    independent of any pending /cookie or /setcookies flow — lets the
    handlers below catch cookies sent unprompted (a lot of users just send
    the file/paste straight away without reading the usage instructions)
    before urluploader/ytsearch's own catch-all auto-detection sees it and
    misfires on it instead (treats a stray URL in a comment line as a link
    to fetch, or the whole paste as a search query).

    min_hits defaults to 2 lines actually matching the format (domain \\t
    flag \\t path \\t secure \\t expiry \\t name \\t value) — a single
    stray tab-separated line isn't enough signal from a text-paste, which
    can coincidentally contain a tab-separated-looking line. Real .txt
    FILE uploads never suffer that ambiguity (Telegram doesn't mangle
    file bytes the way it can mangle a pasted message), and some
    legitimate exports only include a single essential cookie for a
    site — callers on the file-upload path pass min_hits=1 so those
    aren't rejected as "not cookies" and left to fall through to
    yt-dlp/urluploader instead."""
    head = text.lstrip()[:200].lower()
    if head.startswith("# netscape http cookie file") or head.startswith("# http cookie file"):
        return True
    hits = 0
    for line in text.splitlines():
        line = line.rstrip("\n")
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        cols = _cookie_line_cols(line)
        if len(cols) >= 7 and "." in cols[0]:
            hits += 1
            if hits >= min_hits:
                return True
    return False


def _looks_cookie_ish(text: str) -> bool:
    """Weaker, second-chance signal for text that's PROBABLY a mangled
    cookies paste but didn't pass the stricter _looks_like_netscape_cookies
    check above (e.g. tabs got collapsed to single spaces, or the header
    comment line is missing entirely). Used only to decide whether to warn
    the user and stop here rather than letting the text silently fall
    through to the URL/yt-dlp auto-detectors in later groups — NOT used to
    actually save anything as cookies, since we can't reliably parse it."""
    lowered = text.lower()
    if "netscape" in lowered and "cookie" in lowered:
        return True
    # A handful of lines that each look like "something.domain.tld <lots
    # of other tokens>" — e.g. tabs collapsed to single spaces so
    # _cookie_line_cols() undercounts columns, but the shape is still
    # clearly cookie-like rather than prose or a URL.
    hits = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 5 and re.match(r"^\.?[\w-]+(\.[\w-]+)+$", parts[0]):
            hits += 1
            if hits >= 1:
                return True
    return False


def _sanitize_domain(raw: str) -> str:
    raw = raw.strip().lower()
    raw = re.sub(r"^https?://", "", raw)
    raw = raw.split("/")[0].split(":")[0]
    if raw.startswith("www."):
        raw = raw[4:]
    return re.sub(r"[^a-z0-9.\-]", "", raw)


def _cookie_path(domain: str) -> str:
    return os.path.join(COOKIES_DIR, f"{domain}.txt")


def has_pending_cookie_flow(user_id: int) -> bool:
    """True if this user is mid-flow adding cookies — either via the
    /cookie panel's "Add Cookie" button, or the legacy /setcookies
    example.com command — and hasn't sent the file/paste yet.

    This module's own group=-1 message handlers (setcookies_file_receive /
    setcookies_text_receive) already run before ytdl.py's URL auto-detect
    handlers and call message.stop_propagation() once they claim the
    update, so on paper ytdl.py should never even see the cookies
    file/paste. But the very failure mode this file was written to avoid —
    a cookies.txt (containing e.g. a "youtube.com" domain line, or the
    standard "# https://curl.se/docs/http-cookies.html" header URL) getting
    matched by ytdl.py's own site patterns and yt-dlp-downloaded instead of
    saved — is bad enough (silently eats the cookies, admin has to
    re-discover why) that it's worth a second, independent check here
    rather than relying on handler-group ordering alone. ytdl.py's
    auto-detect handlers call this first and bail out immediately if it's
    True, so even if something upstream ever changes (a new handler
    registered at a lower group, a stop_propagation() call quietly dropped
    in a future edit, etc.) the cookies flow still wins the race."""
    panel = _pending_panel.get(user_id)
    if panel and time.monotonic() <= panel["expires"]:
        return True
    return user_id in _pending_setcookies


def get_cookies_for_url(url: str) -> str | None:
    """Used by ytdl.py: does this URL's domain (or a parent of it) have a
    custom cookies.txt an admin uploaded via /setcookies? Checks most
    specific to least specific (sub.example.com, then example.com)."""
    try:
        netloc = urlparse(url if "://" in url else f"https://{url}").netloc.lower().split(":")[0]
    except Exception:
        return None
    parts = netloc.split(".")
    for i in range(len(parts) - 1):  # never falls all the way to a bare TLD
        candidate = ".".join(parts[i:])
        path = _cookie_path(candidate)
        if os.path.exists(path):
            return path
    return None


def _normalize_cookie_file(path: str) -> None:
    """Rewrites any line whose columns were split via the space-fallback in
    _cookie_line_cols() back into proper tab-separated form. yt-dlp's own
    cookie loader (http.cookiejar's MozillaCookieJar) requires literal tabs
    — it won't accept the space-separated fallback we tolerate for
    *detection* purposes above, so a cookies.txt that arrived as a
    Telegram text-paste (tabs collapsed to spaces) would otherwise save
    successfully but still silently fail to authenticate every download."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    except Exception:
        return
    out = []
    changed = False
    for line in lines:
        if not line or line.startswith("#") or "\t" in line:
            out.append(line)
            continue
        cols = _cookie_line_cols(line)
        if len(cols) >= 7:
            out.append("\t".join(cols))
            changed = True
        else:
            out.append(line)
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")


async def _finalize_panel_cookies(message: Message, tmp_path: str):
    domain, all_counts = _detect_domain_verbose(tmp_path)
    if not domain:
        os.remove(tmp_path)
        await message.reply_text(
            f"<b>{E_CROSS} Couldn't detect a domain in that.</b>\n"
            f"<i>Make sure it's a valid Netscape-format cookies.txt, or use "
            f"</i><code>/setcookies example.com</code><i> to set it manually.</i>",
            parse_mode=enums.ParseMode.HTML
        )
        message.stop_propagation()
        return

    _normalize_cookie_file(tmp_path)
    os.replace(tmp_path, _cookie_path(domain))
    others = [f"{d} ({c})" for d, c in all_counts.most_common(4) if d != domain]
    note = f"\n<i>Other domains also seen: {', '.join(others)}</i>" if others else ""
    await message.reply_text(
        f"<b>{E_CHECK} Success!</b>\n\n"
        f"Cookies automatically assigned to domain: <code>{domain}</code> "
        f"({all_counts[domain]} cookie(s))"
        f"{note}\n\n"
        f"<i>Wrong site? Re-send with </i><code>/setcookies correct-domain.com</code><i> instead.</i>",
        parse_mode=enums.ParseMode.HTML
    )
    message.stop_propagation()


async def _save_cookie_file(message: Message, domain: str):
    dest = _cookie_path(domain)
    try:
        await message.download(file_name=dest)
    except Exception as e:
        return await message.reply_text(
            f"<b>{E_CROSS} Failed to save cookies:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )
    await message.reply_text(
        f"<b>{E_CHECK} Cookies saved for <code>{domain}</code></b>\n"
        f"<i>Links from this domain (and its subdomains) will now use these cookies automatically.</i>",
        parse_mode=enums.ParseMode.HTML
    )


async def _save_cookie_text(message: Message, domain: str, text: str):
    dest = _cookie_path(domain)
    try:
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        _normalize_cookie_file(dest)
    except Exception as e:
        return await message.reply_text(
            f"<b>{E_CROSS} Failed to save cookies:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )
    await message.reply_text(
        f"<b>{E_CHECK} Cookies saved for <code>{domain}</code></b>\n"
        f"<i>Links from this domain (and its subdomains) will now use these cookies automatically.</i>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("setcookies") & filters.private & filters.user(ADMINS))
async def setcookies_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/setcookies example.com</code>\n"
            f"<i>Then send the Netscape-format cookies.txt as your next message — "
            f"as a file, or pasted directly as text.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    domain = _sanitize_domain(message.command[1])
    if not domain or "." not in domain:
        return await message.reply_text(f"<b>{E_CROSS} Invalid domain.</b>", parse_mode=enums.ParseMode.HTML)

    if message.document:
        return await _save_cookie_file(message, domain)

    _pending_setcookies[message.from_user.id] = domain
    await message.reply_text(
        f"<b>{E_INFO} Got it.</b> Now send the cookies.txt file for <code>{domain}</code>.",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("cookie") & filters.private & filters.user(ADMINS))
async def cookie_panel_command(client: Client, message: Message):
    await message.reply_text(PANEL_TEXT, parse_mode=enums.ParseMode.HTML, reply_markup=_panel_markup())


@Client.on_callback_query(filters.regex(r"^ckpanel:") & filters.user(ADMINS))
async def cookie_panel_callback(client: Client, cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]

    if action == "root":
        _pending_panel.pop(cq.from_user.id, None)
        try:
            await safe_edit(cq.message.edit_text, PANEL_TEXT, parse_mode=enums.ParseMode.HTML, reply_markup=_panel_markup())
        except MessageNotModified:
            pass
        return await cq.answer()

    if action == "add":
        _pending_panel[cq.from_user.id] = {
            "expires": time.monotonic() + ADD_TIMEOUT,
            "chat_id": cq.message.chat.id,
            "msg_id": cq.message.id,
        }
        await safe_edit(cq.message.edit_text, 
            f"📁 <b>sᴇɴᴅ ᴍᴇ ʏᴏᴜʀ ᴄᴏᴏᴋɪᴇs.ᴛxᴛ ɴᴏᴡ</b> — as a file, or pasted directly as text.\n\n"
            f"<i>(Make sure it is in Netscape HTTP Cookie File format)</i>\n\n"
            f"You have {ADD_TIMEOUT} seconds.",
            parse_mode=enums.ParseMode.HTML
        )
        asyncio.create_task(_expire_add_prompt(client, cq.from_user.id, cq.message.chat.id, cq.message.id))
        return await cq.answer()

    if action == "view":
        files = sorted(f[:-4] for f in os.listdir(COOKIES_DIR) if f.endswith(".txt"))
        text = (f"<b>{E_INFO} Custom cookies set for:</b>\n" + "\n".join(f"• <code>{d}</code>" for d in files)) \
            if files else f"<b>{E_INFO} No custom cookies set.</b>"
        try:
            await safe_edit(cq.message.edit_text, 
                text, parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[make_button("⬅️ ʙᴀᴄᴋ", callback_data="ckpanel:root", style=_BS.DANGER if _BS else None)]])
            )
        except MessageNotModified:
            pass
        return await cq.answer()

    if action == "delmenu":
        if not any(f.endswith(".txt") for f in os.listdir(COOKIES_DIR)):
            return await cq.answer("No custom cookies set.", show_alert=True)
        await safe_edit(cq.message.edit_text, PANEL_TEXT, parse_mode=enums.ParseMode.HTML, reply_markup=_panel_markup("delete"))
        return await cq.answer()

    if action.startswith("del:"):
        domain = action.split(":", 1)[1]
        path = _cookie_path(domain)
        if os.path.exists(path):
            os.remove(path)
            await cq.answer(f"Removed cookies for {domain}", show_alert=True)
        else:
            await cq.answer("Already removed.", show_alert=True)
        if any(f.endswith(".txt") for f in os.listdir(COOKIES_DIR)):
            await safe_edit(cq.message.edit_text, PANEL_TEXT, parse_mode=enums.ParseMode.HTML, reply_markup=_panel_markup("delete"))
        else:
            await safe_edit(cq.message.edit_text, PANEL_TEXT, parse_mode=enums.ParseMode.HTML, reply_markup=_panel_markup())
        return


async def _expire_add_prompt(client: Client, user_id: int, chat_id: int, msg_id: int):
    """After ADD_TIMEOUT seconds, if the user still hasn't sent a file for
    this exact 'Add Cookie' prompt, clear the pending state and let them
    know so a stray document later doesn't silently get treated as cookies.

    Takes the `client` that handled the original callback (main bot or a
    Titanium clone) instead of always falling back to the main bot's
    global instance — otherwise this notice would show up from the main
    bot even when the user was talking to their clone."""
    await asyncio.sleep(ADD_TIMEOUT)
    pending = _pending_panel.get(user_id)
    if not pending or pending["msg_id"] != msg_id:
        return  # already resolved (file received) or superseded by a newer prompt
    _pending_panel.pop(user_id, None)
    try:
        await client.send_message(
            chat_id,
            f"<b>{E_CROSS} Cookie upload timed out.</b> Send <code>/cookie</code> again.",
            parse_mode=enums.ParseMode.HTML,
            reply_to_message_id=msg_id,
        )
    except Exception:
        pass


# group=-1 so this is checked BEFORE rename.py's group=0 catch-all document
# handler (and urluploader/ytsearch's own group=4/9 auto-detection further
# down). Acts (and calls stop_propagation) when a /setcookies OR the "Add
# Cookie" panel button is pending for this user, OR — even with nothing
# pending — when the document itself sniffs as a Netscape cookies.txt, so
# cookies sent unprompted don't fall through to urluploader/ytsearch
# treating them as a random file/URL/search query. Any other document
# upload passes straight through untouched.
@Client.on_message(filters.private & filters.document, group=-1)
async def setcookies_file_receive(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        return

    # Panel-driven "Add Cookie" flow (auto-detects domain from the file).
    panel_pending = _pending_panel.get(user_id)
    if panel_pending:
        if time.monotonic() > panel_pending["expires"]:
            _pending_panel.pop(user_id, None)
            await message.reply_text(
                f"<b>{E_CROSS} Timed out.</b> Send <code>/cookie</code> again and re-upload.",
                parse_mode=enums.ParseMode.HTML
            )
            message.stop_propagation()
            return
        _pending_panel.pop(user_id, None)

        tmp_path = os.path.join(COOKIES_DIR, f".tmp_{user_id}_{int(time.time())}")
        try:
            await message.download(file_name=tmp_path)
        except Exception as e:
            await message.reply_text(f"<b>{E_CROSS} Failed to save cookies:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
            message.stop_propagation()
            return
        return await _finalize_panel_cookies(message, tmp_path)

    # Legacy /setcookies example.com flow (domain typed up front).
    domain = _pending_setcookies.pop(user_id, None)
    if domain:
        await _save_cookie_file(message, domain)
        message.stop_propagation()
        return

    # Auto-detect: no /cookie or /setcookies flow was pending, but this
    # document IS a Netscape cookies.txt anyway — sniff it before handing
    # off to urluploader/ytsearch's own catch-all auto-detection (group=4
    # / group=9), which would otherwise treat it as a generic file/URL and
    # respond with something unrelated instead of saving it. Only sniffs
    # small text-like files, so ordinary video/document uploads elsewhere
    # in the bot aren't touched.
    doc = message.document
    is_txt_like = (doc.mime_type or "").startswith("text/") or (doc.file_name or "").lower().endswith(".txt")
    if not (is_txt_like and (doc.file_size or 0) < 2_000_000):
        return
    tmp_path = os.path.join(COOKIES_DIR, f".sniff_{user_id}_{int(time.time())}")
    try:
        await message.download(file_name=tmp_path)
        with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return
    if _looks_like_netscape_cookies(content, min_hits=1):
        return await _finalize_panel_cookies(message, tmp_path)
    if _looks_cookie_ish(content):
        # Almost certainly a cookies.txt (header line missing, or too few
        # lines matched the strict 7-column check), but not confirmed
        # enough to save. Stop here and say so — same as the text-paste
        # path — instead of silently dropping it and letting it fall
        # through to rename.py/urluploader/ytdl.py's catch-all handlers,
        # which would treat it as a generic file (and can end up trying
        # to yt-dlp-download a stray URL from inside it).
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        await message.reply_text(
            f"<b>{E_INFO} That looks like a cookies.txt file, but I couldn't parse it "
            f"reliably.</b>\n<i>Make sure it's in Netscape format (the standard "
            f"\"# Netscape HTTP Cookie File\" export), or use </i><code>/setcookies example.com</code>"
            f"<i> first so I know which domain it's for.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        message.stop_propagation()
        return
    try:
        os.remove(tmp_path)
    except OSError:
        pass


# Same flows as the document handler above (panel "Add Cookie", legacy
# /setcookies example.com, and unprompted auto-detect via content-sniffing)
# but for cookies pasted directly as a text message instead of uploaded as
# a .txt file — saves the round trip of saving it locally first just to
# re-upload it. group=-1 for the same before-other-catch-alls reason as
# the document handler.
@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=-1)
async def setcookies_text_receive(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        return
    if user_id not in _pending_panel and user_id not in _pending_setcookies:
        # No /cookie or /setcookies flow pending — but sniff it anyway in
        # case it's cookies pasted directly (same reasoning as the
        # document handler above), before urluploader/ytsearch's own
        # catch-all auto-detection (group=4 / group=9) gets a chance to
        # misfire on it.
        text = message.text or ""
        if len(text.strip()) < 20:
            return  # ordinary text message, not cookies — ignore
        if _looks_like_netscape_cookies(text):
            tmp_path = os.path.join(COOKIES_DIR, f".sniff_{user_id}_{int(time.time())}")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception:
                return
            return await _finalize_panel_cookies(message, tmp_path)
        if _looks_cookie_ish(text):
            # Almost certainly a cookies.txt paste, but the strict parser
            # couldn't confirm it (commonly: Telegram's text box collapsed
            # the literal tabs to spaces and the columns didn't line back
            # up cleanly). Stop here and say so — better than silently
            # falling through to the URL auto-detectors below, which would
            # otherwise grab a stray "https://" from a header comment (e.g.
            # the standard curl.se cookie-format reference link) and try to
            # yt-dlp-download it.
            await message.reply_text(
                f"<b>{E_INFO} That looks like a cookies.txt paste, but I couldn't parse it "
                f"reliably.</b>\n<i>Telegram sometimes mangles tab characters when you paste "
                f"text — send it as an uploaded <code>.txt</code> file instead (attach it, "
                f"don't paste), or use </i><code>/setcookies example.com</code><i> first.</i>",
                parse_mode=enums.ParseMode.HTML,
            )
            message.stop_propagation()
            return
        return  # ordinary text message, not cookies — ignore

    text = message.text or ""
    if len(text.strip()) < 20:
        return  # too short to plausibly be a cookies.txt paste — ignore, not our concern

    panel_pending = _pending_panel.get(user_id)
    if panel_pending:
        if time.monotonic() > panel_pending["expires"]:
            _pending_panel.pop(user_id, None)
            await message.reply_text(
                f"<b>{E_CROSS} Timed out.</b> Send <code>/cookie</code> again.",
                parse_mode=enums.ParseMode.HTML
            )
            message.stop_propagation()
            return
        _pending_panel.pop(user_id, None)

        tmp_path = os.path.join(COOKIES_DIR, f".tmp_{user_id}_{int(time.time())}")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            await message.reply_text(f"<b>{E_CROSS} Failed to save cookies:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
            message.stop_propagation()
            return
        return await _finalize_panel_cookies(message, tmp_path)

    # Legacy /setcookies example.com flow (domain typed up front).
    domain = _pending_setcookies.pop(user_id, None)
    if not domain:
        return
    await _save_cookie_text(message, domain, text)
    message.stop_propagation()


@Client.on_message(filters.command("listcookies") & filters.private & filters.user(ADMINS))
async def listcookies_command(client: Client, message: Message):
    files = sorted(f[:-4] for f in os.listdir(COOKIES_DIR) if f.endswith(".txt"))
    if not files:
        return await message.reply_text(f"<b>{E_INFO} No custom cookies set.</b>", parse_mode=enums.ParseMode.HTML)
    text = f"<b>{E_INFO} Custom cookies set for:</b>\n" + "\n".join(f"• <code>{d}</code>" for d in files)
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["delcookies", "clearcookies"]) & filters.private & filters.user(ADMINS))
async def delcookies_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(f"<b>{E_INFO} Usage:</b> <code>/delcookies example.com</code>", parse_mode=enums.ParseMode.HTML)
    domain = _sanitize_domain(message.command[1])
    path = _cookie_path(domain)
    if os.path.exists(path):
        os.remove(path)
        await message.reply_text(f"<b>{E_CHECK} Removed cookies for <code>{domain}</code></b>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"<b>{E_CROSS} No cookies found for <code>{domain}</code></b>", parse_mode=enums.ParseMode.HTML)
