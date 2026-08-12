import os
import re
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import upload_file, run_subprocess_with_progress, format_progress, E_CHECK, E_CROSS, E_INFO, safe_edit
from Akbots.link_cache import try_send_cached
from config import MEGA_EMAIL, MEGA_PASSWORD

PATTERN = re.compile(r"(https?://)?(www\.)?mega\.nz/\S+", re.IGNORECASE)

# Retry knobs for transient megadl failures (network hiccup, Mega API
# timeout, ...). Kept local to this module rather than config.py since
# there's no reason an admin would need to tune these per-deploy.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

# Substrings that mean the LINK itself is the problem (deleted, expired,
# malformed, wrong decryption key, ...) rather than a one-off network blip -
# retrying a dead link just burns 3x the time before showing the same error,
# so these short-circuit out of the retry loop immediately instead.
# "eblocked" included: Mega's own API error for a share it has taken down
# (ToS/copyright) - retrying that can never succeed either.
_PERMANENT_ERROR_HINTS = ("invalid", "expired", "not found", "enoent", "decryption", "bad key", "eblocked")

# New-style mega.nz/file/<id>#<key> and mega.nz/folder/<id>#<key> links.
# The megatools build this bot runs against (apt's megatools package) only
# reliably parses the OLD #!<id>!<key> / #F!<id>!<key> URL scheme - given a
# new-style link it doesn't error, it just silently exits 0 having done
# nothing (no files, no message), which is what was showing up as a bare
# "No file was downloaded." with zero diagnostic info. Old-style links with
# the exact same id+key hit the real Mega API and surface a real error
# (e.g. EBLOCKED) or actually download - so normalize new-style to old-style
# before ever invoking megadl.
_NEW_FILE_RE = re.compile(r"mega\.nz/file/([\w-]+)#([\w-]+)", re.IGNORECASE)
_NEW_FOLDER_RE = re.compile(r"mega\.nz/folder/([\w-]+)#([\w-]+)", re.IGNORECASE)


def _normalize_mega_url(url: str) -> str:
    """Rewrites a new-style mega.nz/file|folder/<id>#<key> link to the
    old #!<id>!<key> / #F!<id>!<key> form megadl actually understands.
    Old-style links (or anything else) pass through unchanged."""
    m = _NEW_FOLDER_RE.search(url)
    if m:
        return f"https://mega.nz/#F!{m.group(1)}!{m.group(2)}"
    m = _NEW_FILE_RE.search(url)
    if m:
        return f"https://mega.nz/#!{m.group(1)}!{m.group(2)}"
    return url

# Minimum free space to require before starting a download. We can't know
# the actual file size upfront (megatools has no link-info-without-download
# command — see mega.py's earlier design notes), so this is a flat safety
# margin rather than a size-aware check: same approach the reference bot
# used (a fixed 2GB floor rather than "size + margin").
MIN_FREE_DISK_BYTES = 2 * 1024 * 1024 * 1024  # 2GB

_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_SIZE_RE = re.compile(r"([\d.]+)\s*/\s*([\d.]+)\s*([KMG]i?B)")
_UNIT_MULT = {"KB": 1024, "MB": 1024**2, "GB": 1024**3,
              "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def extract_url(text: str):
    m = PATTERN.search(text)
    return m.group(0) if m else None


def _megatools_available() -> bool:
    return shutil.which("megadl") is not None


def _parse_megadl_line(line: str, elapsed: float):
    """megatools' megadl prints an in-place-updating line containing a percentage
    (and usually a 'done/total MB' pair) while downloading. The exact wording
    can shift between megatools versions, so we grab whichever pieces are
    present rather than depending on one fixed format."""
    if not line:
        return None
    pct_m = _PERCENT_RE.search(line)
    if not pct_m:
        return None
    pct = float(pct_m.group(1))

    done_bytes = total_bytes = speed = None
    size_m = _SIZE_RE.search(line)
    if size_m:
        done_val, total_val, unit = size_m.groups()
        mult = _UNIT_MULT.get(unit, 1)
        done_bytes = float(done_val) * mult
        total_bytes = float(total_val) * mult
        speed = done_bytes / elapsed if elapsed > 0 else None

    return format_progress(pct, speed_bps=speed, done_bytes=done_bytes, total_bytes=total_bytes,
                            elapsed_secs=elapsed, eta_secs=None, title="Downloading from Mega.nz")


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} Mega.nz link detected...</b>", parse_mode=enums.ParseMode.HTML)
    if await try_send_cached(client, message, url, status):
        return

    if not _megatools_available():
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} 'megatools' is not installed on this host.</b>\n"
            f"<i>Install it first (Debian/Ubuntu: <code>apt install megatools</code>) "
            f"then this link will work.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    # Unique per-task folder — prevents concurrent downloads from different
    # users/messages colliding or getting mixed up in a shared directory.
    # message.id is only unique WITHIN a single chat, not globally, so two
    # users whose messages happen to share an id would otherwise collide;
    # include chat.id to keep folders globally unique.
    folder = os.path.join("downloads", "mega", f"task_{message.chat.id}_{message.id}")
    os.makedirs(folder, exist_ok=True)

    # Bail before even starting megadl if the disk is basically full —
    # otherwise the download runs for however long, then fails partway
    # through with a confusing "No space left on device" from megatools,
    # after already burning time/bandwidth on data that had nowhere to go.
    try:
        free_bytes = shutil.disk_usage(folder).free
    except OSError:
        free_bytes = None  # can't stat it - don't block the download over this
    if free_bytes is not None and free_bytes < MIN_FREE_DISK_BYTES:
        shutil.rmtree(folder, ignore_errors=True)
        free_gb = free_bytes / (1024 ** 3)
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Not enough disk space to start this download.</b>\n"
            f"<i>Only {free_gb:.2f}GB free on the server.</i>",
            parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_INFO} Downloading via megatools...</b>", parse_mode=enums.ParseMode.HTML)
    # Cache key / user-facing messages keep the URL as given; only the
    # actual megadl invocation gets the normalized (old-style) link - see
    # _normalize_mega_url's docstring for why.
    dl_url = _normalize_mega_url(url)
    cmd = ["megadl", "--no-ask-password", "--path", folder]
    # If MEGA_EMAIL/MEGA_PASSWORD are set (config.py or env), log in for this
    # download instead of staying anonymous — needed for files/folders
    # shared privately with that account, and also lifts Mega's per-IP
    # anonymous-download quota (a common cause of "quota exceeded" errors
    # on a shared server IP with several users hitting mega.nz all day).
    # megadl takes credentials per-invocation (--username/--password), so
    # unlike MegaCMD there's no separate persistent-session login step.
    if MEGA_EMAIL and MEGA_PASSWORD:
        cmd += ["--username", MEGA_EMAIL, "--password", MEGA_PASSWORD]
    cmd.append(dl_url)

    # Retry loop: transient failures (network blip, Mega API timeout, a
    # momentary "quota" hiccup) get up to MAX_RETRIES attempts with
    # increasing backoff, same idea as the reference bot's mega-get retry
    # logic - single-attempt-only meant one flaky moment failed the whole
    # download. A link that's actually invalid/expired/undecryptable is
    # detected from the error text and NOT retried, since retrying that
    # can't ever succeed and would just make the user wait 3x longer to
    # see the same "doesn't exist" error.
    last_err = "Unknown megatools error"
    returncode = 1
    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            await safe_edit(status.edit_text,
                f"<b>{E_INFO} Retrying download... (attempt {attempt}/{MAX_RETRIES})</b>",
                parse_mode=enums.ParseMode.HTML)
        returncode, tail = await run_subprocess_with_progress(
            cmd, status, "Downloading from Mega.nz", _parse_megadl_line,
            user_id=message.from_user.id, queue_label="Mega.nz download",
        )
        if returncode == 0:
            break
        last_err = (tail[:300] or "Unknown megatools error").strip()
        if any(hint in last_err.lower() for hint in _PERMANENT_ERROR_HINTS):
            break
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

    if returncode != 0:
        shutil.rmtree(folder, ignore_errors=True)
        if "eblocked" in last_err.lower():
            return await safe_edit(status.edit_text,
                f"<b>{E_CROSS} This Mega link has been blocked/taken down by Mega itself</b> "
                f"<i>(usually a copyright/ToS takedown) — it can't be downloaded from any bot.</i>",
                parse_mode=enums.ParseMode.HTML)
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Mega download failed after {attempt} attempt(s):</b>\n<code>{last_err}</code>",
            parse_mode=enums.ParseMode.HTML)

    new_files = []
    for root, _, fnames in os.walk(folder):
        for f in fnames:
            new_files.append(os.path.join(root, f))

    if not new_files:
        shutil.rmtree(folder, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No file was downloaded.</b>", parse_mode=enums.ParseMode.HTML)

    new_files.sort()
    for i, path in enumerate(new_files):
        fname = os.path.basename(path)
        await upload_file(client, message, path, status, f"<b>{E_CHECK} Mega File</b>\n<code>{fname}</code>", file_name=fname, cache_url=(url if len(new_files) == 1 else None))
        if i < len(new_files) - 1:
            status = await message.reply_text(f"<b>{E_INFO} Uploading next file...</b>", parse_mode=enums.ParseMode.HTML)

    shutil.rmtree(folder, ignore_errors=True)


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def mega_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("mega") & filters.private)
async def mega_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/mega &lt;mega.nz URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _handle(client, message, url)
