# Akbots - Don't Remove Credit - @AkBots_Official
#
# Bilibili Video Support (China: bilibili.com/b23.tv + Global: bilibili.tv/bili.im)
#
# bilibili.com (+ b23.tv short links) is the CN edition, handled by yt-dlp's
# BiliBiliIE / BiliBiliBangumiIE. bilibili.tv (+ bili.im short links) is the
# separate international/global "Bstation" edition — different domain,
# different backend, handled by yt-dlp's own BiliIntlIE. Both are NAMED
# extractors inside yt-dlp (DASH video+audio streams, auto-merged with
# ffmpeg), so unlike TikTok/Facebook there's no need to reimplement any HTML
# scraping here. This module is a thin, dedicated wrapper — same shape as
# Akbots/dailymotion.py and the facebook.py/tiktok.py "route straight to the
# shared quality picker" half — that exists purely so:
#   1. bilibili.com/b23.tv/bilibili.tv/bili.im links get their OWN
#      auto-detect handler (group=1, same tier as YouTube/Instagram/
#      Facebook) instead of falling through to the generic group=2
#      catch-all, giving instant, predictable routing and a clean
#      /bilibili + /bili command pair for either edition.
#   2. Akbots/ytdl.py's _cookies_for() picks up config.BILI_COOKIES for
#      1080p60/4K "quality-locked" formats / region-locked or members-only
#      videos that either edition reserves for logged-in accounts (see
#      config.py) — plain public videos need no cookies at all.
#   3. bilibili.com / b23.tv / bilibili.tv / bili.im are excluded from the
#      generic fallback's domain list (Akbots/ytdl.py _EXCLUDED_DOMAINS) so
#      a link is never processed twice.

import re
import asyncio
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_INFO   = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'

# Covers:
#   https://www.bilibili.com/video/BV1xx411c7mD
#   https://www.bilibili.com/bangumi/play/ep123456   (anime/series episodes)
#   https://m.bilibili.com/video/BV1xx411c7mD        (mobile, CN)
#   https://b23.tv/xxxxxxx                           (CN short link, redirects)
#   https://www.bilibili.tv/en/play/12345/678910      (global/Bstation, episode)
#   https://www.bilibili.tv/en/video/123456789        (global/Bstation, video)
#   https://bili.im/xxxxxxx                          (global short link, redirects)
BILIBILI_PATTERN = re.compile(
    r"(https?://)?(www\.|m\.)?bilibili\.com/(video|bangumi/play)/\S+"
    r"|(https?://)?b23\.tv/\S+"
    r"|(https?://)?(www\.)?bilibili\.tv/(?:[a-zA-Z]{2}/)?(play|video)/\S+"
    r"|(https?://)?bili\.im/\S+",
    re.IGNORECASE,
)

# CN short link (redirects to bilibili.com) and global short link (redirects
# to bilibili.tv) both need resolving to their canonical page before being
# handed to yt-dlp - same reasoning for both, see _resolve_short_url_sync.
_SHORT_LINK_RE = re.compile(r"b23\.tv/\S+|bili\.im/\S+", re.IGNORECASE)
_GLOBAL_SHORT_LINK_RE = re.compile(r"bili\.im/\S+", re.IGNORECASE)


def extract_bilibili_url(text: str):
    m = BILIBILI_PATTERN.search(text or "")
    return m.group(0) if m else None


def _resolve_short_url_sync(url: str) -> str:
    """b23.tv (CN) and bili.im (global/Bstation) links are one-time-shortened
    redirects to the real bilibili.com/video/BV.../bangumi/play/ep... or
    bilibili.tv/.../play|video/... page (often shared from the app, e.g.
    "【标题-哔哩哔哩】 https://b23.tv/xxxxxxx" or a bili.im link out of the
    Bstation app). Resolving to the canonical URL upfront - instead of
    handing the short link straight to yt-dlp - means: (1) Akbots/
    link_cache.py caches on the real, stable video URL rather than a short
    link, so two different short links to the same video correctly hit the
    cache; (2) one less redirect hop for yt-dlp's own extractor to follow,
    on sites already prone to 412 anti-bot responses (see ytdl.py's
    bilibili http_headers block)."""
    is_global = bool(_GLOBAL_SHORT_LINK_RE.search(url))
    target_domain = "bilibili.tv" if is_global else "bilibili.com"
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10)
        final_url = resp.url
        # Some CDNs don't honor HEAD for the redirect - fall back to GET.
        if final_url == url or target_domain not in final_url:
            resp = requests.get(url, allow_redirects=True, timeout=10, stream=True)
            final_url = resp.url
            resp.close()
        return final_url if target_domain in final_url else url
    except Exception:
        return url


async def _route_bilibili_download(client: Client, message: Message, url: str):
    """yt-dlp owns bilibili.com/b23.tv by name, so this goes straight to
    the shared quality picker (same full extraction/retry/headless
    resilience, cookie support, caching, and upload flow as /yt) — no
    separate fallback downloader needed for this site."""
    if _SHORT_LINK_RE.search(url):
        url = await asyncio.to_thread(_resolve_short_url_sync, url)
    from Akbots.ytdl import _show_quality_picker
    ok = await _show_quality_picker(client, message, url, notify_on_failure=False)
    if not ok:
        await message.reply_text(
            f"<b>{E_CROSS} Couldn't fetch this Bilibili video.</b>\n"
            f"<i>It may be region-locked, members-only, or removed. "
            f"Region-locked/members-only videos (either bilibili.com or the "
            f"global bilibili.tv edition) need a Bilibili cookies.txt "
            f"— see config.py's BILI_COOKIES.</i>",
            parse_mode=enums.ParseMode.HTML,
        )


# Registered in a SEPARATE handler group (1) so it runs independently of the
# main t.me link-saving handler in start.py — both get a chance to process
# the same message instead of one silently swallowing the other.
@Client.on_message(filters.text & filters.private & filters.regex(BILIBILI_PATTERN) & ~filters.regex(r"^/"), group=1)
async def bilibili_auto_detect(client: Client, message: Message):
    url = extract_bilibili_url(message.text)
    if url:
        await _route_bilibili_download(client, message, url)


@Client.on_message(filters.command(["bilibili", "bili"]) & filters.private)
async def bilibili_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/bilibili &lt;bilibili.com or bilibili.tv video URL&gt;</code>\n"
            f"<i>Or just paste a bilibili.com / b23.tv / bilibili.tv / bili.im link directly.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    url = extract_bilibili_url(message.command[1]) or message.command[1]
    await _route_bilibili_download(client, message, url)
