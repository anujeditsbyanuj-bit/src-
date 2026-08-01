import os
import re
import shutil
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import upload_file, run_subprocess_with_progress, format_progress, E_CHECK, E_CROSS, E_INFO
from Akbots.link_cache import try_send_cached

PATTERN = re.compile(r"(https?://)?(www\.)?mega\.nz/\S+", re.IGNORECASE)

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
        return await status.edit_text(
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

    await status.edit_text(f"<b>{E_INFO} Downloading via megatools...</b>", parse_mode=enums.ParseMode.HTML)
    cmd = ["megadl", "--no-ask-password", "--path", folder, url]
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, "Downloading from Mega.nz", _parse_megadl_line,
        user_id=message.from_user.id, queue_label="Mega.nz download",
    )

    if returncode != 0:
        err = tail[:300] or "Unknown megatools error"
        shutil.rmtree(folder, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} Mega download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

    new_files = []
    for root, _, fnames in os.walk(folder):
        for f in fnames:
            new_files.append(os.path.join(root, f))

    if not new_files:
        shutil.rmtree(folder, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} No file was downloaded.</b>", parse_mode=enums.ParseMode.HTML)

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
