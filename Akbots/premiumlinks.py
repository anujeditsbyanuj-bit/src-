# Akbots - Don't Remove Credit - @AkBots_Official
#
# Telegram-facing wrapper around Akbots/bypassers/{filepress,gdflix,hubcloud}.py.
# Those three files only SCRAPE a premium-link page and return a dict of
# extracted mirror links (Telegram bot link, Google Drive direct link,
# GoFile, PixelDrain, cloud-resume, zfile workers.dev links) — they don't
# talk to Telegram or download anything themselves. This file is what
# actually turns "paste a link" into "video shows up in the chat": pick the
# best directly-fetchable link out of that dict, then reuse the same
# download + thumbnail/duration + upload pipeline every other plugin here
# uses (Akbots/direct_utils.py).

import os
import re
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.bypassers.filepress import async_scrape_filepress
from Akbots.bypassers.gdflix import async_scrape_gdflix
from Akbots.bypassers.hubcloud import async_scrape_hubcloud
from Akbots.bypassers.lksfy import bypass_lksfy, LKSFY_PATTERN
from Akbots.bypassers.vidyarays import bypass_vidyarays, VIDYARAYS_PATTERN
from Akbots.bypassers.golink import bypass_golink, is_golink_url, GOLINK_DOMAINS
from Akbots.bypassers.hblinks import async_scrape_hblinks, HBLINKS_PATTERN
from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    E_CROSS, E_INFO, E_ROCKET,
)
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

OUTPUT_FOLDER = make_output_folder("premiumlinks")

# These sites are almost always run on ever-changing clone domains (that's
# the whole point — the "real" domain gets blocked, a new one pops up), so
# a domain allowlist can never be exhaustive. Auto-detect covers the common/
# well-known base names; anything on a fresh clone domain still works via
# the explicit /filepress, /gdflix, /hubcloud commands.
FILEPRESS_PATTERN = re.compile(
    r"(https?://)?[\w.\-]*(filepress|hubdrive|gdlink|gdtot|gdflix\.top)[\w.\-]*/file/[A-Za-z0-9]+",
    re.IGNORECASE,
)
GDFLIX_PATTERN = re.compile(
    r"(https?://)?[\w.\-]*gdflix[\w.\-]*/(file|zfile)/[A-Za-z0-9]+",
    re.IGNORECASE,
)
HUBCLOUD_PATTERN = re.compile(
    r"(https?://)?[\w.\-]*hubcloud[\w.\-]*/(drive|video|packs)/[A-Za-z0-9]+"
    r"|(https?://)?vifix\.site/hubcloud/\S+",
    re.IGNORECASE,
)

# Domain keywords these auto-detect patterns are built from — exported so
# urluploader.py's generic-link fallback can exclude them (same pattern
# already used for TERABOX_DOMAINS), otherwise a pasted link would get
# processed twice: once here, once as a raw file by urluploader.py.
BYPASS_DOMAINS = ("filepress", "hubdrive", "gdlink", "gdtot", "gdflix", "hubcloud", "vifix.site", "lksfy.com")


def _href(link_html) -> str | None:
    """The scrapers wrap every link as '<a href="...">𝗟𝗜𝗡𝗞</a>' — pull the
    raw URL back out. Returns None for empty/missing entries."""
    if not link_html:
        return None
    m = re.search(r'href="([^"]+)"', str(link_html))
    if m:
        return m.group(1)
    return link_html if str(link_html).startswith("http") else None


def _direct_downloadable(href: str) -> str:
    """A pixeldrain link scraped out of a GDFlix/etc. page's HTML is the
    human-facing share page (pixeldrain.<tld>/u/<id> — an HTML viewer, NOT
    the file bytes), never the direct-download API URL. Handing that
    straight to stream_download() got back the page's HTML instead of the
    video ("Server returned 'text/html' instead of a file" — this exact
    bug). Rewritten here to the real /api/file/<id> endpoint, the same
    conversion Akbots/pixeldrain.py's own /pd command already does for
    pasted pixeldrain links directly."""
    from Akbots.pixeldrain import _parse as _parse_pixeldrain
    url_type, item_id, domain = _parse_pixeldrain(href)
    if url_type == "file":
        return f"https://pixeldrain.{domain}/api/file/{item_id}"
    return href


def _pick_best_link(data: dict) -> str | None:
    """Priority order favors links that are a plain HTTP GET away from the
    actual file bytes. Telegram bot links (need /start with another bot)
    and GoFile page links (need GoFile's own API/token, not a raw file URL)
    are deliberately skipped here — they're still shown to the user as a
    fallback if nothing better is found."""
    for key in ("instantdl", "cloud_resume"):
        href = _href(data.get(key))
        if href:
            return href
    for item in data.get("zfile") or []:
        href = _href(item)
        if href:
            return href
    href = _href(data.get("pixeldrain"))
    if href:
        return _direct_downloadable(href)
    return None


def _format_links_summary(data: dict, service_label: str) -> str:
    lines = [f"<b>{E_INFO} {service_label}: {data.get('title', 'Unknown')}</b>"]
    if data.get("size"):
        lines.append(f"📦 Size: {data['size']}")
    for key, label in (
        ("instantdl", "⚡ Instant"), ("cloud_resume", "☁️ Cloud Resume"),
        ("telegram", "✈️ Telegram"), ("gofile", "📁 GoFile"),
        ("pixeldrain", "💧 PixelDrain"),
    ):
        if data.get(key):
            lines.append(f"{label}: {data[key]}")
    for i, z in enumerate(data.get("zfile") or [], start=1):
        lines.append(f"🗂️ Mirror {i}: {z}")
    return "\n".join(lines)


async def _run_bypass_download(client: Client, message: Message, url: str, scraper_fn, service_label: str):
    status = await message.reply_text(
        f"<b>{E_INFO} {service_label} link detected — extracting...</b>", parse_mode=enums.ParseMode.HTML
    )

    try:
        data = await scraper_fn(url)
    except Exception as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Extraction failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    if not data:
        return await status.edit_text(
            f"<b>{E_CROSS} Could not extract anything from this link.</b>\n"
            f"It may be dead, region-locked, or the site changed its layout.",
            parse_mode=enums.ParseMode.HTML,
        )

    if data.get("is_pack"):
        if data.get("episodes"):
            # GDFlix-style pack: each episode's links were already fully
            # resolved (see Akbots/bypassers/gdflix.py's per-segment
            # extraction) — show them all inline instead of making the
            # user tap through one at a time.
            lines = [f"<b>{E_INFO} {service_label} Pack: {data.get('title', 'Unknown')}</b>", ""]
            for i, ep in enumerate(data["episodes"], start=1):
                lines.append(f"{i}. 📚 <b>Title:</b> {ep.get('title', 'Unknown')}")
                lines.append(f"┃")
                lines.append(f"┠ 💾 Size: {ep.get('size', 'Unknown')}")
                shown_any = False
                for key, label, glyph in (
                    ("gofile", "GoFile", "📂"), ("telegram", "Telegram File", "🗄"),
                    ("instantdl", "Download", "📥"), ("cloud_resume", "Cloud Resume", "☁️"),
                    ("pixeldrain", "PixelDrain", "💧"),
                ):
                    if ep.get(key):
                        lines.append(f"┠ {glyph} {label}: {ep[key]}")
                        shown_any = True
                for j, z in enumerate(ep.get("zfile") or [], start=1):
                    lines.append(f"┠ 🗂️ Mirror {j}: {z}")
                    shown_any = True
                if not shown_any:
                    lines.append(f"┖ ❌ No links found for this episode.")
                else:
                    lines[-1] = lines[-1].replace("┠", "┖", 1)  # cap the last line of this episode's block
                lines.append("")
            return await status.edit_text(
                "\n".join(lines).strip(), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True
            )

        # A season/series pack is a page full of per-episode links, each of
        # which needs its own separate bypass pass — auto-downloading an
        # entire pack in one go is out of scope, so hand back the episode
        # links instead of guessing which one the user wants.
        text = (
            f"<b>{E_INFO} {service_label} Pack: {data.get('title', 'Unknown')}</b>\n"
            f"📦 Size: {data.get('size', 'Unknown')}\n\n"
            f"{data.get('pack_content', 'No episodes found.')}\n\n"
            f"<i>Send me one episode link at a time to download it.</i>"
        )
        return await status.edit_text(text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)

    best = _pick_best_link(data)
    if not best:
        return await status.edit_text(
            _format_links_summary(data, service_label)
            + f"\n\n<i>{E_INFO} No directly-downloadable link found — try one of the links above manually.</i>",
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
        )

    title = data.get("title") or "file"
    filename = safe_filename(f"{title}.mp4", "premiumlink_file")
    dest = os.path.join(OUTPUT_FOLDER, f"{message.id}_{filename}")

    try:
        await status.edit_text(f"<b>{E_ROCKET} Downloading: {title}</b>", parse_mode=enums.ParseMode.HTML)
        await stream_download(
            best, dest, status, f"Downloading from {service_label}",
            user_id=message.from_user.id, file_name=filename,
        )
        await upload_file(client, message, dest, status, _format_links_summary(data, service_label), file_name=filename)
    except Exception as e:
        try:
            await status.edit_text(
                _format_links_summary(data, service_label)
                + f"\n\n<b>{E_CROSS} Auto-download failed:</b>\n<code>{e}</code>\n"
                  f"<i>Try one of the links above manually.</i>",
                parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
            )
        except Exception:
            pass
    finally:
        try:
            os.remove(dest)
        except Exception:
            pass


def _extract(pattern: re.Pattern, text: str):
    m = pattern.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Commands (work for ANY clone domain, since the URL is given explicitly)
# ---------------------------------------------------------------------------

@Client.on_message(filters.command(["filepress", "hubdrive"]) & filters.private)
async def filepress_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/filepress &lt;url&gt;</code>", parse_mode=enums.ParseMode.HTML
        )
    await _run_bypass_download(client, message, message.command[1], async_scrape_filepress, "FilePress")


@Client.on_message(filters.command(["gdflix"]) & filters.private)
async def gdflix_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/gdflix &lt;url&gt;</code>", parse_mode=enums.ParseMode.HTML
        )
    await _run_bypass_download(client, message, message.command[1], async_scrape_gdflix, "GDFlix")


@Client.on_message(filters.command(["hubcloud"]) & filters.private)
async def hubcloud_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/hubcloud &lt;url&gt;</code>", parse_mode=enums.ParseMode.HTML
        )
    await _run_bypass_download(client, message, message.command[1], async_scrape_hubcloud, "HubCloud")


# ---------------------------------------------------------------------------
# Auto-detect (bare link, no command) — same group=1 priority as the other
# dedicated site handlers (facebook.py, instagram.py, terabox.py, vk.py).
# ---------------------------------------------------------------------------

@Client.on_message(filters.text & filters.private & filters.regex(FILEPRESS_PATTERN) & ~filters.regex(r"^/"), group=1)
async def filepress_auto_detect(client: Client, message: Message):
    url = _extract(FILEPRESS_PATTERN, message.text)
    if url:
        await _run_bypass_download(client, message, url, async_scrape_filepress, "FilePress")


@Client.on_message(filters.text & filters.private & filters.regex(GDFLIX_PATTERN) & ~filters.regex(r"^/"), group=1)
async def gdflix_auto_detect(client: Client, message: Message):
    url = _extract(GDFLIX_PATTERN, message.text)
    if url:
        await _run_bypass_download(client, message, url, async_scrape_gdflix, "GDFlix")


@Client.on_message(filters.command(["lksfy"]) & filters.private)
async def lksfy_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/lksfy &lt;url&gt;</code>", parse_mode=enums.ParseMode.HTML
        )
    await _run_lksfy_bypass(client, message, message.command[1])


@Client.on_message(filters.text & filters.private & filters.regex(LKSFY_PATTERN) & ~filters.regex(r"^/"), group=1)
async def lksfy_auto_detect(client: Client, message: Message):
    url = _extract(LKSFY_PATTERN, message.text)
    if url:
        await _run_lksfy_bypass(client, message, url)


@Client.on_message(filters.text & filters.private & filters.regex(VIDYARAYS_PATTERN) & ~filters.regex(r"^/"), group=1)
async def vidyarays_auto_detect(client: Client, message: Message):
    url = _extract(VIDYARAYS_PATTERN, message.text)
    if url:
        await _run_vidyarays_bypass(client, message, url)


@Client.on_message(filters.text & filters.private & filters.regex(r"(https?://)?[\w.-]*(" + "|".join(re.escape(d) for d in GOLINK_DOMAINS) + r")") & ~filters.regex(r"^/"), group=1)
async def golink_auto_detect(client: Client, message: Message):
    match = re.search(r"https?://\S+", message.text)
    url = match.group(0) if match else None
    if url and is_golink_url(url):
        await _run_golink_bypass(client, message, url)


async def _run_lksfy_bypass(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} lksfy link detected — bypassing...</b>", parse_mode=enums.ParseMode.HTML
    )

    final = await bypass_lksfy(url)
    if not final:
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't bypass this link.</b>\n"
            f"<i>Most likely cause: the Turnstile CAPTCHA solver in "
            f"Akbots/bypassers/lksfy.py's solve_turnstile() isn't wired up to a "
            f"real solving service yet — it's a stub by design (see that file's "
            f"docstring). Could also be a dead link or a site layout change.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    # The resolved link often just points straight at one of the mirror
    # hosts already handled above — chain into that bypasser instead of
    # treating it as a raw file, same as pasting the resolved link directly.
    if FILEPRESS_PATTERN.search(final):
        return await _run_bypass_download(client, message, final, async_scrape_filepress, "FilePress")
    if GDFLIX_PATTERN.search(final):
        return await _run_bypass_download(client, message, final, async_scrape_gdflix, "GDFlix")
    if HUBCLOUD_PATTERN.search(final):
        return await _run_bypass_download(client, message, final, async_scrape_hubcloud, "HubCloud")

    # Otherwise treat it as a plain direct-ish link: try downloading it, and
    # fall back to just handing the resolved URL back if that fails (it
    # might need a browser/cookies/a different flow we don't know about).
    filename = safe_filename(os.path.basename(final.split("?")[0]) or "lksfy_file", "lksfy_file")
    dest = os.path.join(OUTPUT_FOLDER, f"{message.id}_{filename}")
    try:
        await status.edit_text(f"<b>{E_ROCKET} Resolved — downloading...</b>", parse_mode=enums.ParseMode.HTML)
        await stream_download(final, dest, status, "Downloading resolved link",
                               user_id=message.from_user.id, file_name=filename)
        await upload_file(client, message, dest, status,
                           f"<b>{E_INFO} lksfy bypass</b>\n<code>{filename}</code>", file_name=filename)
    except Exception:
        await status.edit_text(
            f"<b>{E_CHECK} Bypassed — resolved link:</b>\n{final}\n\n"
            f"<i>Couldn't auto-download it (not a plain direct file); open it manually.</i>",
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
        )
    finally:
        try:
            os.remove(dest)
        except Exception:
            pass


async def _run_vidyarays_bypass(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} vidyarays link detected — bypassing (polling, can take up to a minute)...</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    final = await bypass_vidyarays(url)
    if not final:
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't bypass this link.</b>\n"
            f"<i>Timed out waiting for the server-side validation to clear, or this "
            f"isn't the ?id= prolink.php flow Akbots/bypassers/vidyarays.py handles.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _finish_resolved_link(client, message, status, final, "vidyarays")


async def _run_golink_bypass(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} Link detected — bypassing...</b>", parse_mode=enums.ParseMode.HTML
    )
    final = await bypass_golink(url)
    if not final:
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't bypass this link.</b>\n"
            f"<i>The page's go-link form may have changed, or the destination link "
            f"wasn't in the expected JSON response field.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _finish_resolved_link(client, message, status, final, "go-link")


async def _finish_resolved_link(client: Client, message: Message, status, final: str, label: str):
    """Shared tail end for the polling/form-based bypassers above — chain
    into a known mirror-host bypasser if the resolved link is one, else try
    a plain direct download, else just hand back the resolved link."""
    if FILEPRESS_PATTERN.search(final):
        return await _run_bypass_download(client, message, final, async_scrape_filepress, "FilePress")
    if GDFLIX_PATTERN.search(final):
        return await _run_bypass_download(client, message, final, async_scrape_gdflix, "GDFlix")
    if HUBCLOUD_PATTERN.search(final):
        return await _run_bypass_download(client, message, final, async_scrape_hubcloud, "HubCloud")

    filename = safe_filename(os.path.basename(final.split("?")[0]) or f"{label}_file", f"{label}_file")
    dest = os.path.join(OUTPUT_FOLDER, f"{message.id}_{filename}")
    try:
        await status.edit_text(f"<b>{E_ROCKET} Resolved — downloading...</b>", parse_mode=enums.ParseMode.HTML)
        await stream_download(final, dest, status, "Downloading resolved link",
                               user_id=message.from_user.id, file_name=filename)
        await upload_file(client, message, dest, status,
                           f"<b>{E_INFO} {label} bypass</b>\n<code>{filename}</code>", file_name=filename)
    except Exception:
        await status.edit_text(
            f"<b>{E_CHECK} Bypassed — resolved link:</b>\n{final}\n\n"
            f"<i>Couldn't auto-download it (not a plain direct file); open it manually.</i>",
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
        )
    finally:
        try:
            os.remove(dest)
        except Exception:
            pass


@Client.on_message(filters.text & filters.private & filters.regex(HBLINKS_PATTERN) & ~filters.regex(r"^/"), group=1)
async def hblinks_auto_detect(client: Client, message: Message):
    url = _extract(HBLINKS_PATTERN, message.text)
    if url:
        await _run_hblinks_bypass(client, message, url)


# Tapping a link inside a Telegram message just opens it in a browser, it
# does NOT re-send it as a new message — so listing plain links wouldn't
# actually let a tap continue the chain. Real inline buttons + a callback
# are needed instead; the options list lives here keyed by a short id
# (same reasoning as hdhub.py's _LINKS dict — callback_data has a 64-byte
# limit, real URLs don't fit).
_HBLINKS_OPTIONS: dict = {}


async def _run_hblinks_bypass(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} hblinks archive detected — fetching quality options...</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    result = await async_scrape_hblinks(url)
    if not result or not result.get("options"):
        return await status.edit_text(
            f"<b>{E_CROSS} Couldn't find any quality options on this page.</b>\n"
            f"<i>hblinks.co's layout may differ from what Akbots/bypassers/hblinks.py "
            f"expects — this bypasser was written without being able to test against "
            f"a live page, so its selectors may need adjusting.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    options_id = str(message.id)
    _HBLINKS_OPTIONS[options_id] = result["options"]
    buttons = [
        [make_button(label, callback_data=f"hbl:{options_id}:{i}", style=_BS.PRIMARY if _BS else None)]
        for i, (label, _link) in enumerate(result["options"])
    ]
    await status.edit_text(
        f"<b>{E_CHECK} {result['title']}</b>\nPick a quality:",
        parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons),
    )


@Client.on_callback_query(filters.regex(r"^hbl:"))
async def hblinks_option_callback(client: Client, query: CallbackQuery):
    _, options_id, idx = query.data.split(":")
    options = _HBLINKS_OPTIONS.get(options_id)
    if not options or int(idx) >= len(options):
        return await query.answer("This list expired — resend the hblinks link.", show_alert=True)

    label, link = options[int(idx)]
    await query.answer()
    await query.message.edit_text(f"<b>{E_INFO} {label}</b> — resolving...", parse_mode=enums.ParseMode.HTML)

    # The chosen option is itself a HubCloud-family link, so hand it
    # straight to the existing hubcloud bypasser — same chain
    # _finish_resolved_link() uses elsewhere in this file.
    if HUBCLOUD_PATTERN.search(link):
        return await _run_bypass_download(client, query.message, link, async_scrape_hubcloud, "HubCloud")

    await query.message.edit_text(
        f"<b>{E_CHECK} Resolved:</b>\n{link}",
        parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
    )
