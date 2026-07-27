# Akbots - Don't Remove Credit - @AkBots_Official
#
# Zee5 & Voot — dedicated commands for two named streaming platforms.
#
# yt-dlp already ships native "Zee5" and "Voot" extractors, and neither
# domain is in ytdl.py's _EXCLUDED_DOMAINS list, so a bare zee5.com/voot.com
# link already gets picked up automatically by the generic auto-detect
# handler in ytdl.py (Tier 1 there recognises them by name and routes
# exclusively to yt-dlp, same as Twitch/TikTok/Vimeo). These two commands
# just give an explicit, branded entry point for people who'd rather type
# /zee5 or /voot than paste a bare link — and a clear error up front if the
# link isn't actually from that site, instead of falling through generic
# auto-detect's silent per-domain checks.
#
# --- Zee5 direct-stream resolver ---------------------------------------
# Ported from the Zee5-Downloader-1-main repo's plugins/zee5_dl.py: Zee5
# exposes a *public* token-exchange API (useraction.zee5.com/tokennd +
# .../token/platform_tokens.php + gwapi.zee5.com/content/details/<id>)
# that hands back a signed HLS manifest URL for a title without needing
# any account/login cookies. _resolve_zee5_direct_url() below does that
# same exchange; if it succeeds we hand yt-dlp the *resolved* .m3u8
# stream URL directly (generic HLS extraction, no site-specific
# cookie-gate involved). If Zee5 changes/locks the endpoint, or the title
# genuinely needs a paid account, this silently falls back to the
# original zee5.com page URL — same as before, which still needs Zee5
# login cookies via /setcookies zee5.com for premium content.
#
# Voot still goes straight through yt-dlp's own extractor + cookies
# (/setcookies voot.com) — the source repo above only covered Zee5.

import re
import asyncio
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.ytdl import _show_quality_picker

E_WARN = '<emoji id=5447644880824181073>⚠️</emoji>'

_ZEE5_RE = re.compile(r"https?://(?:www\.)?zee5\.com/\S+", re.IGNORECASE)
_VOOT_RE = re.compile(r"https?://(?:www\.)?voot\.com/\S+", re.IGNORECASE)

_ZEE5_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def _resolve_zee5_direct_url(zee5_url: str):
    """Best-effort: exchange Zee5's public tokens for a direct, signed HLS
    URL so the actual video can be pulled without login cookies. Returns
    (direct_url, title) on success, or (None, None) if anything about the
    exchange fails (locked title, endpoint change, network error, etc.) —
    callers should fall back to the plain zee5_url + cookies in that case.
    Blocking (uses requests), so always call via asyncio.to_thread."""
    try:
        content_id = "-".join(re.findall(r"([0-9]?\w+)", zee5_url)[-3:])

        video_token = requests.get(
            "https://useraction.zee5.com/tokennd", timeout=15
        ).json().get("video_token", "")

        access_token = requests.get(
            "https://useraction.zee5.com/token/platform_tokens.php",
            params={"platform_name": "web_app"}, timeout=15
        ).json().get("token")

        headers = dict(_ZEE5_HEADERS)
        if access_token:
            headers["X-Access-Token"] = access_token

        details = requests.get(
            f"https://gwapi.zee5.com/content/details/{content_id}",
            headers=headers, params={"translation": "en", "country": "IN"}, timeout=15
        ).json()

        hls = details.get("hls")
        title = details.get("title")
        if not hls:
            return None, None
        manifest = hls[0].replace("drm", "hls")

        if "movies" in zee5_url:
            direct_url = f"https://zee5vodnd.akamaized.net{manifest}{video_token}"
        else:
            # tvshows / originals / episodes
            if "netst" in manifest:
                netst_token = requests.get("https://useraction.zee5.com/token", timeout=15).json().get("video_token", "")
                direct_url = f"{manifest}{netst_token}"
            else:
                direct_url = f"https://zee5vodnd.akamaized.net{manifest}{video_token}"

        return direct_url, title
    except Exception:
        return None, None


async def _open_zee5(client: Client, message: Message, zee5_url: str):
    direct_url, title = await asyncio.to_thread(_resolve_zee5_direct_url, zee5_url)
    # Only pass title_override when we actually resolved the direct CDN URL —
    # if we fell back to the plain zee5.com page URL, yt-dlp's own Zee5
    # extractor will read the real title off the page itself anyway.
    await _show_quality_picker(
        client, message, direct_url or zee5_url,
        title_override=title if direct_url else None,
    )


@Client.on_message(filters.private & filters.command("zee5"))
async def zee5_cmd(client: Client, message: Message):
    text = message.text.split(None, 1)
    m = _ZEE5_RE.search(text[1]) if len(text) > 1 else None
    if not m:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/zee5 https://www.zee5.com/...</code>\n\n"
            f"<i>Most titles resolve directly, no login needed. If a premium show still "
            f"fails, add Zee5 login cookies — see /cookie.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    await _open_zee5(client, message, m.group(0))


@Client.on_message(filters.private & filters.command("voot"))
async def voot_cmd(client: Client, message: Message):
    text = message.text.split(None, 1)
    m = _VOOT_RE.search(text[1]) if len(text) > 1 else None
    if not m:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/voot https://www.voot.com/...</code>\n\n"
            f"<i>Premium shows need Voot login cookies first — see /cookie.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    await _show_quality_picker(client, message, m.group(0))


# --- Zee5 series/season batch resolver ---------------------------------
# Ported from the zee5downloaderdas repo's zee5seriesscraper.py: Zee5 also
# exposes a public "tvshow" endpoint (gwapi.zee5.com/content/tvshow/<id>)
# that lists every season + episode web_url for a show in a single call —
# same unauthenticated, token-free API family as the single-episode
# resolver above, just a different endpoint. This lets /zee5series accept
# a show's *listing* page (not a single episode) and queue every episode
# through the same _open_zee5() resolver already used for one-off links.
#
# NOT ported: the same source repo also ships zee4k.py / zee5latestdownloader.py,
# which decrypt Widevine-DRM 4K streams using a hardcoded personal account's
# session tokens. That's DRM circumvention riding on somebody's paid
# subscription, not a public API, so it was left out.

_active_series_jobs: dict = {}  # user_id -> True while a /zee5series job is running


def _resolve_zee5_show_episodes(show_url: str):
    """Return an ordered [(title, episode_url), ...] for every episode across
    every season of a Zee5 show, or [] if the URL isn't a show page or the
    lookup fails. Blocking (uses requests) — always call via asyncio.to_thread."""
    try:
        content_id = "-".join(re.findall(r"([0-9]?\w+)", show_url)[-3:])
        details = requests.get(
            f"https://gwapi.zee5.com/content/tvshow/{content_id}",
            headers=_ZEE5_HEADERS, params={"translation": "en", "country": "IN"}, timeout=15
        ).json()
        episodes = []
        for season in details.get("seasons", []):
            for ep in season.get("episodes", []):
                web_url = ep.get("web_url")
                if web_url:
                    title = ep.get("title") or ep.get("original_title") or "Episode"
                    episodes.append((title, f"https://www.zee5.com/{web_url}"))
        return episodes
    except Exception:
        return []


@Client.on_message(filters.private & filters.command("zee5series"))
async def zee5_series_cmd(client: Client, message: Message):
    text = message.text.split(None, 1)
    m = _ZEE5_RE.search(text[1]) if len(text) > 1 else None
    if not m:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/zee5series https://www.zee5.com/tvshows/details/...</code>\n\n"
            f"<i>Paste a show's main page (not a single episode) — every episode across every "
            f"season gets queued and sent one by one. Use /zee5cancel to stop partway.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    uid = message.from_user.id
    if _active_series_jobs.get(uid):
        return await message.reply_text(
            f"<b>{E_WARN} You already have a series download running.</b> Use /zee5cancel first.",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>🔎 Looking up episodes...</b>", parse_mode=enums.ParseMode.HTML)
    episodes = await asyncio.to_thread(_resolve_zee5_show_episodes, m.group(0))
    if not episodes:
        return await status.edit_text(
            f"<b>{E_WARN} Couldn't find episodes for that link.</b> "
            f"Make sure it's a show's main page, not a single episode — for a single episode use /zee5.",
            parse_mode=enums.ParseMode.HTML
        )

    _active_series_jobs[uid] = True
    await status.edit_text(
        f"<b>🎬 Found {len(episodes)} episode(s).</b> Downloading one by one — use /zee5cancel to stop.",
        parse_mode=enums.ParseMode.HTML
    )
    try:
        for i, (title, ep_url) in enumerate(episodes, 1):
            if not _active_series_jobs.get(uid):
                break
            try:
                await message.reply_text(f"<b>📥 ({i}/{len(episodes)})</b> {title}", parse_mode=enums.ParseMode.HTML)
                await _open_zee5(client, message, ep_url)
            except Exception:
                continue
    finally:
        _active_series_jobs.pop(uid, None)


@Client.on_message(filters.private & filters.command("zee5cancel"))
async def zee5_series_cancel_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if _active_series_jobs.pop(uid, None):
        await message.reply_text(f"<b>{E_WARN} Series download cancelled.</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"<b>{E_WARN} No active series download.</b>", parse_mode=enums.ParseMode.HTML)


# --- Bare-link auto-detect --------------------------------------------
# Registered at group=1 (same tier as terabox.py, vk.py, etc.) so a plain
# zee5.com/voot.com link pasted without /zee5 or /voot is caught directly
# here — no need to fall through to ytdl.py's generic Tier-1 detection.
@Client.on_message(
    filters.text & filters.private & filters.regex(_ZEE5_RE) & ~filters.regex(r"^/"),
    group=1,
)
async def zee5_auto_detect(client: Client, message: Message):
    m = _ZEE5_RE.search(message.text)
    if m:
        await _open_zee5(client, message, m.group(0))


@Client.on_message(
    filters.text & filters.private & filters.regex(_VOOT_RE) & ~filters.regex(r"^/"),
    group=1,
)
async def voot_auto_detect(client: Client, message: Message):
    m = _VOOT_RE.search(message.text)
    if m:
        await _show_quality_picker(client, message, m.group(0))
