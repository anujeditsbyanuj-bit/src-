import os
import re
import shutil
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import upload_file, run_subprocess_with_progress, format_progress, E_CHECK, E_CROSS, E_INFO
from Akbots.link_cache import try_send_cached

MAGNET_PATTERN = re.compile(r"magnet:\?xt=urn:btih:[a-zA-Z0-9]+\S*")
TORRENT_URL_PATTERN = re.compile(r"https?://\S+\.torrent", re.IGNORECASE)

# aria2c's live status line looks like:
#   [#2089b0 12MiB/100MiB(12%) CN:5 DL:1.2MiB ETA:27s]
_ARIA2_RE = re.compile(
    r"\[#\S+\s+([\d.]+\S*)/([\d.]+\S*)\((\d+)%\).*?(?:DL:(\S+))?.*?(?:ETA:(\S+))?\]"
)
_SIZE_UNIT_RE = re.compile(r"([\d.]+)\s*([KMGT]i?B)?", re.IGNORECASE)
_UNIT_MULT = {"KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
              "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}
_DURATION_RE = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def _size_to_bytes(s):
    if not s:
        return None
    m = _SIZE_UNIT_RE.match(s.strip())
    if not m or not m.group(1):
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    return val * _UNIT_MULT.get(unit, 1)


def _duration_to_secs(s):
    if not s:
        return None
    m = _DURATION_RE.match(s.strip())
    if not m:
        return None
    h, mi, sec = (int(g) if g else 0 for g in m.groups())
    total = h * 3600 + mi * 60 + sec
    return total if (h or mi or sec) else None


def extract_link(text: str):
    m = MAGNET_PATTERN.search(text) or TORRENT_URL_PATTERN.search(text)
    return m.group(0) if m else None


def _aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def _parse_aria2_line(line: str, elapsed: float):
    if not line or "[#" not in line:
        return None
    m = _ARIA2_RE.search(line)
    if not m:
        return None
    done, total, pct, speed, eta = m.groups()
    return format_progress(
        float(pct),
        speed_bps=_size_to_bytes(speed),
        done_bytes=_size_to_bytes(done),
        total_bytes=_size_to_bytes(total),
        elapsed_secs=elapsed,
        eta_secs=_duration_to_secs(eta),
        title="Downloading torrent",
    )


async def _handle(client: Client, message: Message, link: str):
    status = await message.reply_text(f"<b>{E_INFO} Torrent/magnet link detected...</b>", parse_mode=enums.ParseMode.HTML)
    if await try_send_cached(client, message, link, status):
        return

    if not _aria2c_available():
        return await status.edit_text(
            f"<b>{E_CROSS} 'aria2c' is not installed on this host.</b>\n"
            f"<i>Install it first (Debian/Ubuntu: <code>apt install aria2</code>) "
            f"then torrent/magnet links will work.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    # Unique per-task folder — prevents concurrent downloads from different
    # users/messages colliding or getting mixed up in a shared directory.
    # message.id is only unique WITHIN a single chat, not globally, so two
    # different users' chats can easily land on the same id (e.g. both
    # users' 5th message each being id=5) — chat.id is folded in too so
    # their downloads never share a folder.
    folder = os.path.join("downloads", "torrent", f"task_{message.chat.id}_{message.id}")
    os.makedirs(folder, exist_ok=True)

    cmd = [
        "aria2c",
        f"--dir={folder}",
        "--seed-time=0",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--continue=true",
        "--summary-interval=5",
        "--enable-dht=true",
        "--bt-enable-lpd=true",
        # Without this, a dead magnet (no peers/seeds) makes aria2c wait
        # forever for metadata/pieces with zero feedback to the user, and
        # (until the /queue + cancel-kill fix) no way to stop it either.
        # Aborts if download speed has been 0 for this many consecutive
        # seconds — covers both the metadata-fetch stall and a mid-download
        # stall equally, since both look like "0 speed" to aria2c.
        "--bt-stop-timeout=120",
        "--console-log-level=warn",
        link,
    ]

    await status.edit_text(f"<b>{E_INFO} Downloading via aria2c...</b>", parse_mode=enums.ParseMode.HTML)

    returncode, tail = await run_subprocess_with_progress(
        cmd, status, "Downloading torrent", _parse_aria2_line,
        user_id=message.from_user.id, queue_label="Torrent/Magnet download",
    )

    if returncode != 0:
        shutil.rmtree(folder, ignore_errors=True)
        if "time out" in tail.lower() or "timeout" in tail.lower():
            return await status.edit_text(
                f"<b>{E_CROSS} No peers/seeds found for this torrent.</b>\n"
                f"<i>It stalled for 2 minutes with zero activity, so the download was stopped. "
                f"The magnet/torrent may be dead or have too few seeders.</i>",
                parse_mode=enums.ParseMode.HTML
            )
        err = tail[:300] or f"aria2c exited with code {returncode}"
        return await status.edit_text(f"<b>{E_CROSS} Torrent download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

    files = []
    for root, _, fnames in os.walk(folder):
        for f in fnames:
            if f.endswith(('.aria2',)):  # skip aria2 control files
                continue
            files.append(os.path.join(root, f))
    files.sort()

    if not files:
        shutil.rmtree(folder, ignore_errors=True)
        return await status.edit_text(f"<b>{E_CROSS} No file was downloaded.</b>", parse_mode=enums.ParseMode.HTML)

    for i, path in enumerate(files):
        fname = os.path.basename(path)
        await upload_file(client, message, path, status, f"<b>{E_CHECK} Torrent File</b>\n<code>{fname}</code>", file_name=fname, cache_url=(link if len(files) == 1 else None))
        if i < len(files) - 1:
            status = await message.reply_text(f"<b>{E_INFO} Uploading next file...</b>", parse_mode=enums.ParseMode.HTML)

    shutil.rmtree(folder, ignore_errors=True)


@Client.on_message(filters.text & filters.private & filters.regex(MAGNET_PATTERN), group=1)
async def torrent_auto_detect_magnet(client: Client, message: Message):
    link = extract_link(message.text)
    if link:
        await _handle(client, message, link)


# .torrent URL (e.g. https://example.com/file.torrent) pasted with no /torrent
# command — magnet links already had auto-detect above, this closes the gap
# for direct .torrent file links.
@Client.on_message(
    filters.text & filters.private & filters.regex(TORRENT_URL_PATTERN) & ~filters.regex(r"^/"),
    group=1,
)
async def torrent_auto_detect_url(client: Client, message: Message):
    link = extract_link(message.text)
    if link:
        await _handle(client, message, link)


@Client.on_message(filters.command("torrent") & filters.private)
async def torrent_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/torrent &lt;magnet link or .torrent URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    link = extract_link(message.command[1]) or message.command[1]
    await _handle(client, message, link)
