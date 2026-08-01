# Akbots - Don't Remove Credit - @AkBots_Official
#
# JioCinema movie downloader — /jiocinema <link>, or just paste a
# jiocinema.com/movies/... link and it's auto-detected.
#
# Ported from a "Jio-Cinema-Bot-main" archive. The movie catalog comes from
# JioCinema's own public metadata API, and the actual stream is a plain,
# unauthenticated HLS manifest built purely from published CDN path
# components (thumbnail-id segments) — no login, no bearer token, no DRM
# involved. Same "public CDN URL" shape as the Zee5 integration, so this
# just feeds the resolved URL into the same shared quality picker
# (Akbots/ytdl.py) every other downloader here already uses.
#
# NOT ported from the source repo, deliberately:
#  - `accounts/` — that zip shipped 101 REAL Google Cloud service-account
#    JSON files with live private keys, used to spread Google Drive uploads
#    across a pool of accounts. These are leaked credentials with no
#    legitimate place in this bot. Nothing from that folder was used for
#    anything beyond identifying what it was — if this is your own repo,
#    treat those keys as compromised and rotate/revoke them.
#  - `mp4decrypt_new.exe` — bundled in the source repo but not actually
#    called anywhere in the movie-download flow anyway; left out regardless
#    since this bot doesn't do DRM decryption.
#  - Google Drive upload mode — skipped entirely so this plugin has no
#    reason to go near the accounts/ pool; downloads go through this bot's
#    normal Telegram upload path instead (same as every other downloader).

import re
import asyncio
import requests

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.ytdl import _show_quality_picker

E_WARN = '<emoji id=5447644880824181073>⚠️</emoji>'

_JIO_URL_RE = re.compile(
    r"https?://(?:www\.)?jiocinema\.com/(?:watch/)?movies/(?:.+?/\d+/\d+/|.*?[?&]id=)?([a-zA-Z0-9]+)",
    re.IGNORECASE,
)


def _get_jio_metadata(m_id: str):
    """Blocking — call via asyncio.to_thread. Public, unauthenticated
    metadata lookup; returns None if the id doesn't resolve to a movie."""
    try:
        resp = requests.get(
            f"https://prod.media.jio.com/apis/common/v3/metamore/get/{m_id}",
            headers={"os": "Android"}, timeout=20,
        )
        data = resp.json()
        if not data.get("name"):
            return None
        return data
    except Exception:
        return None


def _build_jio_hls_url(m_id: str, metadata: dict):
    """The thumb field encodes the two CDN path segments the HLS manifest
    lives under, e.g. 'xx/123/456' -> f1='123', f2='456'."""
    try:
        parts = metadata["thumb"].split("/")
        f1, f2 = parts[1], parts[2]
    except Exception:
        return None
    return (
        f"http://jiobeats.cdn.jio.com/vod/_definst_/smil:vodpublic/"
        f"{f1}/{f2}/{m_id}.smil/index.m3u8"
    )


async def _open_jiocinema(client: Client, message: Message, url: str):
    m = _JIO_URL_RE.search(url)
    if not m:
        return await message.reply_text(
            f"<b>{E_WARN} That doesn't look like a JioCinema movie link.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
    m_id = m.group(1)

    status = await message.reply_text(f"<b>🔎 Looking up movie...</b>", parse_mode=enums.ParseMode.HTML)
    metadata = await asyncio.to_thread(_get_jio_metadata, m_id)
    if not metadata:
        return await status.edit_text(f"<b>{E_WARN} Movie not found — check the link.</b>",
                                       parse_mode=enums.ParseMode.HTML)

    hls_url = _build_jio_hls_url(m_id, metadata)
    if not hls_url:
        return await status.edit_text(
            f"<b>{E_WARN} Couldn't resolve a playable stream for this title.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    title = metadata.get("name", "Movie")
    year = metadata.get("year")
    full_title = f"{title} ({year})" if year else title

    await status.delete()
    await _show_quality_picker(client, message, hls_url, title_override=full_title)


@Client.on_message(filters.private & filters.command("jiocinema"))
async def jiocinema_cmd(client: Client, message: Message):
    text = message.text.split(None, 1)
    if len(text) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/jiocinema https://www.jiocinema.com/movies/...</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _open_jiocinema(client, message, text[1].strip())


# --- Bare-link auto-detect --------------------------------------------
@Client.on_message(
    filters.text & filters.private & filters.regex(_JIO_URL_RE) & ~filters.regex(r"^/"),
    group=1,
)
async def jiocinema_auto_detect(client: Client, message: Message):
    await _open_jiocinema(client, message, message.text)
