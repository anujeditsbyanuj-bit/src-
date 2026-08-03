import os
import re
import time
import uuid
import shutil
import asyncio
import subprocess
import requests
from urllib.parse import urlparse
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import YTDL_MAX_FILESIZE, YT_COOKIES, INSTA_COOKIES, FB_COOKIES, VK_COOKIES
from Akbots.direct_utils import (
    make_upload_progress, format_media_caption, send_full_description, format_progress,
    download_official_thumbnail, extract_thumbnail, get_video_metadata, build_description_button,
)
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit, strip_ansi
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    import yt_dlp
    from yt_dlp.networking.impersonate import ImpersonateTarget
    # BUG FIX (root cause of "Couldn't fetch info:" with a blank/empty error
    # on EVERY link — Instagram, Facebook, Snapchat, generic, all of them):
    # yt-dlp's Python API does NOT auto-convert a plain impersonate string
    # like "chrome" into an ImpersonateTarget the way its CLI (--impersonate
    # chrome) does. Passing the raw string straight into YoutubeDL(...) as
    # opts["impersonate"] made yt-dlp's own internal
    # _impersonate_target_available() call
    # `assert isinstance(target, ImpersonateTarget)` on a plain str, which
    # raises a completely BARE `AssertionError()` — no message at all — the
    # instant every single YoutubeDL(...) is constructed. That's exactly
    # why the error text after "Couldn't fetch info:" was empty: str(e) on
    # a bare AssertionError() is "". Pre-building the real target object
    # once here and reusing it below fixes this for every extractor.
    _CHROME_IMPERSONATE_TARGET = ImpersonateTarget.from_str("chrome")
except ImportError:
    yt_dlp = None
    ImpersonateTarget = None
    _CHROME_IMPERSONATE_TARGET = None

# Vendored, unmodified copy of youtube-dl (Akbots/../ytdl_legacy — repo root,
# NOT inside Akbots/, so pyrogram's plugin scanner never touches its 800+
# extractor files). yt-dlp is a fork of youtube-dl and already ships
# equivalent-or-better versions of nearly every one of these extractors, so
# this is only ever reached as a LAST-RESORT tier: something yt-dlp's own
# extractor (dedicated or generic) fully fails on, that youtube-dl's
# independent extractor implementation might still get through on (they've
# diverged enough since the fork that failure on one doesn't always mean
# failure on the other). Pure stdlib, no extra pip deps — safe to import
# unconditionally, but still guarded the same way as yt_dlp above in case
# the folder is ever removed.
try:
    import ytdl_legacy
except ImportError:
    ytdl_legacy = None

E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_BOLT   = '<emoji id=5456140674028019486>⚡️</emoji>'

DOWNLOAD_DIR = "yt_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# session_id -> {"url": str, "title": str, "thumbnail": str, "chat_id": int, "reply_to": int}
_SESSIONS = {}

INSTA_PATTERN = re.compile(r"(https?://)?(www\.)?(instagram\.com|instagr\.am)/\S+", re.IGNORECASE)
PINTEREST_PATTERN = re.compile(r"(https?://)?(www\.)?(pinterest\.[a-z.]+|pin\.it)/\S+", re.IGNORECASE)
YOUTUBE_PATTERN = re.compile(
    r"(https?://)?(www\.|m\.)?(youtube\.com/(watch|shorts|live|clip)\S+|youtu\.be/\S+)",
    re.IGNORECASE,
)
PLAYLIST_REGEX = re.compile(r'(.*)youtube\.com/(.*)[&|?]list=(?P<playlist>[^&]*)(.*)', re.IGNORECASE)

# Disabled on request — the headless-Chromium "render the page" fallback
# was failing on every link on this host (likely Playwright's chromium
# can't actually launch here — see replit.nix's note on missing OS-level
# shared libs like libnss3/libgbm1/libasound2 that Replit's base image
# doesn't ship). Set back to True to re-enable it once that's fixed;
# everything downstream already degrades gracefully when it's off, same
# as when headless.py itself isn't installed.
ENABLE_HEADLESS_FALLBACK = False
print(f"[ytdl] ENABLE_HEADLESS_FALLBACK = {ENABLE_HEADLESS_FALLBACK} "
      f"(startup check — if you still see 'rendering the page' messages "
      f"after this prints False, the running server is NOT loading this "
      f"file — check for a duplicate/cached copy of ytdl.py, an old "
      f"process still running, or a .pyc cache)")

# DASH manifests (.mpd) — same tier as YouTube/Instagram/Pinterest above: a
# dedicated group=1 handler that routes straight to the quality picker,
# instead of falling through to the generic group=2 handler (which first
# tries yt-dlp's generic extractor and, on failure, spins up a headless
# browser — both pointless extra work for a link that's unambiguously a
# DASH manifest already). yt-dlp reads .mpd URLs natively (its dashsegments
# downloader), so no extra dependency is needed here, unlike .m3u8 which
# Akbots/m3u8dl.py handles with the separate `m3u8` library for the
# multi-track/batch flow.
MPD_PATTERN = re.compile(r"https?://\S+\.mpd(?:\?\S*)?", re.IGNORECASE)

# VK.com is handled by the dedicated Akbots/vk.py plugin (group=1) — kept
# out of the generic fallback below so a vk.com link isn't processed twice.
# Generic bare-link fallback (any other yt-dlp-supported site: Twitch, TikTok,
# Vimeo, SoundCloud, Dailymotion, X/Twitter video, etc). Domains already
# owned by a more specific handler are excluded so a link isn't processed
# twice.
GENERIC_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
from Akbots.terabox import TERABOX_DOMAINS
from Akbots.premiumlinks import BYPASS_DOMAINS

_EXCLUDED_DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com", "instagr.am",
    "pinterest.", "pin.it",
    "facebook.com", "fb.watch", "fb.com",
    "mega.nz", "drive.google.com", "gofile.io", "mediafire.com",
    "pixeldrain.", "streamtape.", "stape.", "catbox.moe",
    *TERABOX_DOMAINS,
    *BYPASS_DOMAINS,
    "magnet:", ".torrent",
    "twitter.com", "x.com", "pixiv.net", "deviantart.com", "artstation.com",
    "flickr.com", "tumblr.com", "reddit.com", "imgur.com",
    "danbooru.donmai.us", "gelbooru.com", "konachan.com", "yande.re",
    "safebooru.org", "zerochan.net", "furaffinity.net", "bsky.app",
    "mxplayer.in", "mxplay.com",
    "fembed.com", "fembed-hd.com", "femax20.com", "vanfem.com", "suzihaza.com",
    "embedsito.com", "owodeuwu.xyz", "plusto.link", "watchse.icu", "feurl.com",
    "vk.com", "vk.ru", ".mpd", "ftp://", "ftps://",
    "jiosaavn.com", "open.spotify.com", "dailymotion.com", "dai.ly",
    "pocketfm.com",
    "t.me", "telegram.me", "telegram.dog", "telegram.org",
)


def _cookies_for(url: str):
    try:
        from Akbots.cookies_manager import get_cookies_for_url
        custom = get_cookies_for_url(url)
        if custom:
            return custom
    except Exception:
        pass
    if "instagram.com" in url and INSTA_COOKIES and os.path.exists(INSTA_COOKIES):
        return INSTA_COOKIES
    if ("facebook.com" in url or "fb.watch" in url) and FB_COOKIES and os.path.exists(FB_COOKIES):
        return FB_COOKIES
    if ("vk.com" in url or "vk.ru" in url) and VK_COOKIES and os.path.exists(VK_COOKIES):
        return VK_COOKIES
    if YT_COOKIES and os.path.exists(YT_COOKIES):
        return YT_COOKIES
    return None


_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _origin_referer(url: str) -> str | None:
    """Same-origin Referer for url — most hotlink-protected HTML5/JW-player
    CDNs (the sites yt-dlp's generic extractor falls back to) only check
    that the Referer header matches their own domain, not the exact page."""
    try:
        p = urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}/"
    except Exception:
        pass
    return None


_impersonate_ok = None  # cached tri-state: None = not checked yet, True/False after


def _impersonate_available() -> bool:
    """Checks ONCE per process whether curl_cffi's chrome impersonation
    target actually works on this host, without hitting the network.

    Some hosts (seen on Replit's Nix-based sandbox in particular) don't
    ship a compatible curl-impersonate binary for curl_cffi's wheel —
    constructing a Session with impersonate="chrome" then raises
    immediately. Since _base_opts() used to set opts["impersonate"]
    unconditionally, that turned into EVERY yt-dlp call on EVERY site
    failing before it ever reached the network, always falling straight
    through to the headless-browser fallback ("har site render js wala
    chal jata hai, ytdlp nahi chalta" was this exact symptom). Now it's
    checked once and skipped (falling back to plain urllib — still works
    fine for most sites, just without the anti-Cloudflare TLS spoofing)
    instead of silently breaking every single download."""
    global _impersonate_ok
    if _impersonate_ok is not None:
        return _impersonate_ok
    try:
        # Validate against yt-dlp's OWN registered impersonate targets, not
        # just a raw curl_cffi Session — a raw Session("chrome") can succeed
        # even when yt-dlp's own target-matching would still reject the
        # plain string "chrome" (see _CHROME_IMPERSONATE_TARGET below for
        # why the plain string can't be passed to YoutubeDL directly at
        # all). Checking through yt_dlp.YoutubeDL itself is what actually
        # proves impersonation will work when _base_opts() sets it below,
        # instead of giving a false "available" from curl_cffi alone that
        # then blows up later as a bare, message-less AssertionError from
        # deep inside yt-dlp's _impersonate_target_available().
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as _ydl:
            if not _ydl._impersonate_target_available(_CHROME_IMPERSONATE_TARGET):
                raise RuntimeError("chrome impersonate target not registered")
        _impersonate_ok = True
    except Exception as e:
        _impersonate_ok = False
        print(f"[ytdlp] WARNING — curl_cffi chrome impersonation unavailable on this host "
              f"({e}); falling back to plain requests for every yt-dlp call. Anti-Cloudflare "
              f"TLS spoofing (403 bypass) won't work until this is fixed.")
    return _impersonate_ok


def _base_opts(url: str, force_referer: bool = False) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    cookies = _cookies_for(url)
    if cookies:
        opts["cookiefile"] = cookies

    # Some CDNs are just slow to respond, or (more often, in practice)
    # geo-block/IP-block this host's network at the TCP level entirely —
    # "Got error: (ConnectTimeout, 'Connection to ... timed out (connect
    # timeout=20.0)')" is yt-dlp's default 20s connect timeout expiring.
    # A genuine IP/geo block can't be fixed by any timeout or retry count,
    # but a merely slow/loaded CDN can — give it more time and a few
    # retries instead of failing on the very first attempt.
    opts["socket_timeout"] = 30
    opts["retries"] = 5
    opts["fragment_retries"] = 5
    opts["extractor_retries"] = 3

    # Generic/HTML5 sites are very often sat behind Cloudflare's anti-bot
    # challenge, which 403s yt-dlp's plain request before it ever sees the
    # page's real formats ("Got HTTP Error 403 caused by Cloudflare
    # anti-bot challenge" - the exact error this was added for). yt-dlp can
    # get past this by impersonating a real browser's TLS/JA3 fingerprint
    # via curl_cffi (already in requirements.txt) instead of urllib's
    # request fingerprint, which Cloudflare flags immediately. Harmless to
    # set for every URL - named extractors (YouTube, Instagram, etc.) just
    # ignore an extractor-arg key that isn't theirs.
    #
    # Two settings, deliberately both set:
    #   - opts["impersonate"]: the TOP-LEVEL YoutubeDL option. This swaps
    #     the request handler used for EVERY network call yt-dlp makes
    #     (any extractor, not just Generic) to curl_cffi's browser-TLS
    #     impersonation. This is what actually gets past a Cloudflare
    #     TLS/JA3 challenge on the very first request.
    #   - extractor_args["generic"]["impersonate"]: extractor-scoped
    #     override some yt-dlp versions still check separately for the
    #     Generic extractor's own webpage fetch. Kept as a belt-and-braces
    #     fallback in case opts["impersonate"] alone isn't picked up.
    # "chrome" is passed explicitly (rather than "") so it always resolves
    # to a concrete, available target instead of relying on curl_cffi
    # auto-picking one — auto-pick silently no-ops (falls back to plain
    # urllib, i.e. the exact 403 this is meant to prevent) on some
    # curl_cffi builds if no default target is registered.
    #
    # Gated behind _impersonate_available(): on hosts where curl_cffi's
    # chrome-impersonation binary isn't actually functional (seen on
    # Replit's Nix sandbox), setting opts["impersonate"] unconditionally
    # made EVERY yt-dlp call on EVERY site fail immediately, before ever
    # reaching the network — see _impersonate_available()'s docstring.
    if _impersonate_available():
        opts["impersonate"] = _CHROME_IMPERSONATE_TARGET
        extractor_args = {"generic": {"impersonate": ["chrome"]}}
    else:
        extractor_args = {"generic": {}}

    # yt-dlp needs an external JS runtime to solve YouTube's signature/
    # n-param challenge since 2025.11.12 — without one, extract_info() can
    # still return a format list, but the googlevideo download itself 403s
    # (the exact "unable to download video data" / 403 symptom this fixes).
    # Deno is yt-dlp's default-enabled runtime, but this project provisions
    # Node.js instead (Dockerfile's nodesource install, done originally for
    # the bgutil PO-token server — make sure the same nodejs package is
    # added to replit.nix on Replit deploys, since Deno isn't installed
    # anywhere here), so it has to be pointed at "node" explicitly or it
    # silently finds no runtime at all. requirements.txt's yt-dlp[default]
    # extra is what actually installs the yt-dlp-ejs solver script that
    # uses this runtime.
    opts["js_runtimes"] = {"node": {}}

    # Note: YouTube PO tokens are auto-generated with zero config once
    # bgutil-ytdlp-pot-provider (see requirements.txt) is installed — it
    # registers itself as a yt-dlp plugin and yt-dlp picks it up on its own.
    if YOUTUBE_PATTERN.search(url) or PLAYLIST_REGEX.search(url):
        # Cookie-less YouTube fallback. Client order matters here in a way
        # it's easy to get backwards: tv_embedded/android skip the bot-
        # check/PO-token requirement, but YouTube's own player API caps
        # what those two clients return to a low-res (~360p) format set
        # regardless of what this bot requests — that's a server-side
        # restriction on YouTube's end, not something yt-dlp/skip options
        # control. "web" is the only client that exposes the real
        # 1080p/720p/480p/360p adaptive-format ladder, but it needs a
        # working PO token (bgutil-ytdlp-pot-provider — see requirements.txt
        # and its own setup docs, since that plugin needs a companion
        # process running alongside the bot, it isn't truly zero-infra).
        # So: try web FIRST for the full ladder, and let tv_embedded/android
        # still get merged in behind it as a fallback for videos where web
        # gets bot-blocked outright (all three get requested regardless of
        # order — order here only affects which one wins on conflicts).
        opts["geo_bypass"] = True
        extractor_args["youtube"] = {
            "player_client": ["web", "tv_embedded", "android"],
            # NOTE: do NOT skip dash/hls here — YouTube's real per-
            # resolution streams (1080p/720p/480p/360p/...) are only
            # exposed as separate DASH video-only formats. Skipping
            # dash leaves only the old "combined" muxed formats, which
            # today is usually just one low-res option — that's what
            # was collapsing every YouTube link down to a single
            # "Best available" button instead of a real quality ladder.
        }
    opts["extractor_args"] = extractor_args

    _NAMED_DOMAINS = ("youtube.com", "youtu.be", "instagram.com", "instagr.am",
                       "facebook.com", "fb.watch", "fb.com", "vk.com", "vk.ru")
    is_named_site = any(d in url.lower() for d in _NAMED_DOMAINS) or bool(PLAYLIST_REGEX.search(url))

    if "instagram.com" in url or "instagr.am" in url:
        # Instagram's CDN occasionally sends a gzip-labeled response that
        # isn't valid gzip (usually a truncated rate-limit/error response),
        # which surfaces as "Error reading response: ... content-encoding:
        # gzip, but failed to decode it ... inconsistent stream state".
        # Asking the server not to compress the response at all sidesteps
        # the broken-decode path entirely.
        opts["http_headers"] = {"Accept-Encoding": "identity"}
    elif force_referer or not is_named_site:
        # Generic/HTML5/CDN sites (anything without a named extractor above)
        # are very often hotlink- or anti-bot-protected and reject a bare
        # request with no Referer/User-Agent at all - sometimes with 403,
        # sometimes with a plain 404, sometimes just a broken/empty
        # response. Rather than waiting for that failure and retrying
        # (see _is_403_error / _is_hotlink_blockable_404 below), send a
        # same-origin Referer + normal desktop User-Agent on the very
        # first attempt for anything that isn't one of the named sites
        # above (which already send their own correct default headers) -
        # this is what actually gets most of these past the check instead
        # of needing a retry round-trip.
        referer = _origin_referer(url)
        headers = {"User-Agent": _DESKTOP_UA, "Accept-Encoding": "identity"}
        if referer:
            headers["Referer"] = referer
        opts["http_headers"] = headers
    else:
        # Named sites (YouTube/Facebook/VK) still go through curl_cffi's
        # "impersonate=chrome" handler when available, which sends a
        # browser-like "Accept-Encoding: gzip, deflate, br" by default.
        # For large/fragmented media downloads specifically (not the HTML
        # metadata fetch), that combination is what produces "unable to
        # download video data: ... content-encoding: gzip, but failed to
        # decode it ... inconsistent stream state" - the exact same broken-
        # gzip-decode bug worked around above for Instagram, just on a
        # different CDN. Media is already compressed (h264/aac/etc.), so
        # asking for no additional transport compression costs nothing and
        # sidesteps the bug everywhere instead of only on Instagram.
        opts["http_headers"] = {"Accept-Encoding": "identity"}
    return opts


def _is_403_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "403" in msg or "forbidden" in msg


def _cf_bypass_headers(url: str) -> dict | None:
    """Last-resort fallback for generic links: curl_cffi's TLS impersonation
    (_impersonate_available) and the plain Referer/UA retry above both get
    past *some* anti-bot checks, but not a real Cloudflare Turnstile
    challenge — that needs an actual browser to click through. Runs the
    same Akbots.cf_bypass helper other downloaders use, gets real clearance
    cookies + a matching User-Agent, and returns them as yt-dlp http_headers
    ({} or None if cf_bypass isn't installed/the bypass failed) so the
    caller can retry with a real Cloudflare-cleared session instead of
    giving up.

    Safe to call from these sync functions even though get_clearance() is
    async: _extract_info/_download_selected only ever run inside
    asyncio.to_thread() (see call sites), i.e. a plain worker thread with
    no event loop of its own, so asyncio.run() here can't collide with one.
    """
    try:
        from Akbots.cf_bypass import is_available, get_clearance
    except ImportError:
        return None
    if not is_available():
        return None
    try:
        clearance = asyncio.run(get_clearance(url))
    except Exception:
        return None
    if not clearance:
        return None
    cookie_header = "; ".join(f"{k}={v}" for k, v in clearance["cookies"].items())
    headers = {"User-Agent": clearance["user_agent"]}
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _is_hotlink_blockable_404(exc: Exception) -> bool:
    """Many hotlink-protected generic/CDN links return a plain 404 (not 403)
    when a request arrives with no Referer/User-Agent, instead of a proper
    Forbidden - it's the same "you're not coming from our player page"
    check as _is_403_error, just a different status code some CDNs choose
    to obscure the real reason. Worth the same same-origin Referer +
    desktop User-Agent retry before treating the link as truly dead."""
    msg = str(exc).lower()
    return "404" in msg and "not found" in msg


# Errors known to be transient/network-level rather than "this video is
# really unavailable" - worth one retry with a fresh YoutubeDL instance
# before giving up.
_TRANSIENT_ERROR_MARKERS = (
    "content-encoding: gzip, but failed to decode it",
    "inconsistent stream state",
    "the page needs to be reloaded",
)


def _is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_ERROR_MARKERS)


def _fmt_size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f" ({n:.1f}{unit})"
        n /= 1024
    return f" ({n:.1f}TB)"


def _fmt_duration(seconds):
    if not seconds:
        return "Unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_YTDLP_BOILERPLATE_RE = re.compile(
    r";?\s*please report this issue on.*$", re.IGNORECASE | re.DOTALL
)


def _clean_ytdlp_error(e) -> str:
    """yt-dlp appends a long 'please report this issue on github... confirm
    you're on the latest version...' tail to almost every internal error,
    even ones that are really just 'this page doesn't have what we expected'
    rather than a bug worth reporting. Strip that noise so the user sees the
    actual reason, not a GitHub-issue template."""
    msg = _YTDLP_BOILERPLATE_RE.sub("", str(e)).strip()
    return msg or str(e)


class _SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _legacy_extract_info(url: str) -> dict | None:
    """Last-resort metadata fetch via the vendored youtube-dl (ytdl_legacy),
    tried only after every yt-dlp tier above has already failed. Returns
    None (never raises) on any failure so callers can just check truthiness
    and fall through to the original yt-dlp error — this tier is a bonus,
    not a replacement, so it should never mask what yt-dlp actually said."""
    if ytdl_legacy is None:
        return None
    try:
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "noplaylist": True, "socket_timeout": 20, "logger": _SilentLogger(),
        }
        with ytdl_legacy.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


def _extract_info(url: str) -> dict:
    """Fetch metadata + available formats WITHOUT downloading."""
    last_err = None
    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL({**_base_opts(url), "skip_download": True}) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            last_err = e
            if attempt < 2 and _is_transient_error(e):
                time.sleep(2 * (attempt + 1))
                continue
            if _is_403_error(e) or _is_hotlink_blockable_404(e):
                # One extra attempt with a same-origin Referer + desktop
                # User-Agent before giving up — see _base_opts(force_referer=True).
                # Covers both proper 403s and the CDNs that 404 instead of
                # 403 for the same missing-Referer/UA reason.
                try:
                    with yt_dlp.YoutubeDL({**_base_opts(url, force_referer=True), "skip_download": True}) as ydl:
                        return ydl.extract_info(url, download=False)
                except Exception as e2:
                    last_err = e2
                    if _is_403_error(e2):
                        # Still blocked — a real anti-bot challenge, not
                        # just a missing Referer/UA. Get real browser
                        # clearance cookies and try one final time.
                        cf_headers = _cf_bypass_headers(url)
                        if cf_headers:
                            try:
                                opts = {**_base_opts(url), "http_headers": cf_headers, "skip_download": True}
                                with yt_dlp.YoutubeDL(opts) as ydl:
                                    return ydl.extract_info(url, download=False)
                            except Exception as e3:
                                last_err = e3
            # Every yt-dlp tier exhausted — one bonus attempt via the
            # vendored youtube-dl extractors before giving up entirely.
            legacy_info = _legacy_extract_info(url)
            if legacy_info:
                return legacy_info
            raise last_err
    raise last_err


def _resolve_height(f):
    """Recover a resolution height even when yt-dlp's own "height" field is
    empty - common for HLS master playlists pulled in via the generic
    extractor, where the EXT-X-STREAM-INF entries are still parsed into
    separate formats but the numeric height isn't always propagated onto
    each one. Checks, in order: the "resolution" field (e.g. "640x360"),
    then any WxH or NNNp pattern inside format_note/format/format_id (yt-dlp
    often stitches the label straight from the manifest into one of these,
    e.g. format_id "hls-720" or format_note "720p")."""
    h = f.get("height")
    if h:
        return int(h)
    for key in ("resolution", "format_note", "format", "format_id"):
        val = str(f.get(key) or "")
        m = re.search(r"(\d{3,4})x(\d{3,4})", val)
        if m:
            return int(m.group(2))
        m = re.search(r"(\d{3,4})p\b", val, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 0


def _pick_qualities(info: dict):
    """Reduce yt-dlp's raw format list to one best option per resolution tier.

    Generic HTML5/JW-player pages (yt-dlp's "Generic" extractor — the case for
    most random video-hosting sites that aren't a named extractor) usually
    expose formats with NO numeric "height" field, even though the site does
    distinguish qualities via yt-dlp's text label instead (format_note, e.g.
    "Auto", "360p", "480p", "720p" — same fields a plain `yt-dlp -j` dump
    shows). Previously all of those got collapsed into a single "Best
    available" tier, throwing away every quality but one. Now: real numeric
    heights (including ones recovered from resolution/label text via
    _resolve_height) are grouped/deduped by height; anything still left with
    no resolvable height falls back to its bitrate (tbr/vbr) as the
    distinguishing key, so bandwidth-only HLS variants (master playlist has
    BANDWIDTH but no RESOLUTION per stream) still get their own separate
    tier instead of all merging into one button; only truly identical,
    unlabelled formats collapse into a single "Best available" tier.
    """
    formats = info.get("formats") or []
    by_height = {}
    by_label = {}

    for f in formats:
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        # vcodec == "none" means yt-dlp EXPLICITLY confirmed this format is
        # audio-only. vcodec is None/missing (not the string "none") means
        # yt-dlp never probed the codec at all — very common for generic-
        # extractor formats pulled straight from a page's <source> tags or
        # a JS quality-switcher list, which usually don't get probed for
        # speed. Treating an unprobed vcodec as "not video" (the old check)
        # silently dropped EVERY quality on exactly these sites, leaving
        # only the single synthetic "Best available"/"Auto" fallback tier
        # even when the page genuinely offered 360p/480p/720p/1080p.
        has_video = vcodec != "none"
        has_audio = acodec not in (None, "none")
        if not has_video:
            continue
        size = f.get("filesize") or f.get("filesize_approx") or 0
        score = (2 if has_audio else 1, size)
        height = _resolve_height(f)

        if height:
            current = by_height.get(height)
            if not current or score > current["_score"]:
                by_height[height] = {
                    "format_id": f["format_id"], "height": height, "ext": f.get("ext", "mp4"),
                    "filesize": size, "label": f"{height}p", "_score": score,
                }
            continue

        # No resolvable height at all — fall back to yt-dlp's own text
        # label, then to bitrate, so distinct variants still get distinct
        # buttons instead of collapsing together.
        note = (f.get("format_note") or "").strip()
        if "dash" in note.lower() or "dash" in (f.get("format") or "").lower():
            continue  # DASH manifests aren't directly downloadable as-is
        tbr = f.get("tbr") or f.get("vbr")
        if note and note.lower() not in ("unknown", "default", "auto", ""):
            label = note
        elif tbr:
            label = f"~{int(tbr)}kbps"
        else:
            label = "Best available"
        current = by_label.get(label)
        if not current or score > current["_score"]:
            by_label[label] = {
                "format_id": f["format_id"], "height": 0, "ext": f.get("ext", "mp4"),
                "filesize": size, "label": label, "_score": score,
            }

    tiers = sorted(by_height.values(), key=lambda x: x["height"], reverse=True)
    tiers += sorted(by_label.values(), key=lambda x: x["label"].lower())

    if not tiers and (info.get("url") or info.get("format_id")):
        # yt-dlp didn't even list a "formats" array (single-format generic
        # result) — build one synthetic tier from the top level.
        tiers = [{
            "format_id": info.get("format_id") or "best",
            "height": 0,
            "ext": info.get("ext") or "mp4",
            "filesize": info.get("filesize") or info.get("filesize_approx") or 0,
            "label": "Best available",
        }]

    return tiers  # show every distinct quality available, no cap


def _legacy_download(url: str, out_dir: str, audio_only: bool, progress_hook=None) -> tuple | None:
    """Last-resort actual download via the vendored youtube-dl, mirroring
    _legacy_extract_info's role for _download_selected: only reached once
    every yt-dlp retry tier below has already failed. Returns None (never
    raises) so the caller falls through to yt-dlp's original error instead
    of this bonus tier's own failure message."""
    if ytdl_legacy is None:
        return None
    try:
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 20, "logger": _SilentLogger(),
            "outtmpl": os.path.join(out_dir, "%(title).70s.%(ext)s"),
            "format": "bestaudio/best" if audio_only else "best",
        }
        if progress_hook is not None:
            opts["progress_hooks"] = [progress_hook]
        if audio_only:
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ]
        with ytdl_legacy.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            path = os.path.splitext(path)[0] + (".mp3" if audio_only else "." + (info.get("ext") or "mp4"))
            return path, info
    except Exception:
        return None


def _download_selected(url: str, out_dir: str, format_id, audio_only: bool, height=None, progress_hook=None,
                        audio_bitrate: str = "192"):
    opts = {
        **_base_opts(url),
        "outtmpl": os.path.join(out_dir, "%(title).70s.%(ext)s"),
        "max_filesize": YTDL_MAX_FILESIZE,
    }
    if progress_hook is not None:
        opts["progress_hooks"] = [progress_hook]

    def _run(fmt):
        opts["format"] = fmt
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if audio_only:
                path = os.path.splitext(path)[0] + ".mp3"
            else:
                path = os.path.splitext(path)[0] + "." + (info.get("ext") or "mp4")
            return path, info

    if audio_only:
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": audio_bitrate}]
        try:
            return _run("bestaudio/best")
        except Exception as e:
            if not (_is_403_error(e) or _is_hotlink_blockable_404(e)):
                raise
            opts["http_headers"] = _base_opts(url, force_referer=True).get("http_headers", {})
            try:
                return _run("bestaudio/best")
            except Exception as e2:
                if not _is_403_error(e2):
                    raise
                cf_headers = _cf_bypass_headers(url)
                if not cf_headers:
                    legacy = _legacy_download(url, out_dir, audio_only=True, progress_hook=progress_hook)
                    if legacy:
                        return legacy
                    raise
                opts["http_headers"] = cf_headers
                try:
                    return _run("bestaudio/best")
                except Exception:
                    legacy = _legacy_download(url, out_dir, audio_only=True, progress_hook=progress_hook)
                    if legacy:
                        return legacy
                    raise

    opts["merge_output_format"] = "mp4"
    height_cap = f"[height<={height}]" if height else ""
    # Formats can go stale between building the quality menu and the tap
    # (itags rotate/throttle/disappear) — that's the "Requested format is
    # not available" error. Chain fallbacks: exact tier tapped -> an
    # equivalent height-capped combo -> plain best-of-everything, and if the
    # whole chain still errors, retry once with the simplest selector.
    fmt_chain = f"{format_id}+bestaudio/bestvideo{height_cap}+bestaudio/best{height_cap}/best"
    last_err = None
    for attempt in range(3):
        try:
            return _run(fmt_chain)
        except Exception as e:
            last_err = e
            if _is_transient_error(e) and attempt < 2:
                # CDN sometimes sends a broken gzip stream on consecutive
                # requests too - short backoff before hammering it again.
                time.sleep(2 * (attempt + 1))
                continue
            if _is_403_error(e) or _is_hotlink_blockable_404(e):
                # Same same-origin Referer + desktop User-Agent retry used
                # in _extract_info — the CDN link resolved fine but the
                # actual download request got hotlink-blocked (some CDNs
                # signal this with 404 instead of 403).
                opts["http_headers"] = _base_opts(url, force_referer=True).get("http_headers", {})
                try:
                    return _run(fmt_chain)
                except Exception as e2:
                    last_err = e2
                    if _is_403_error(e2):
                        # Still blocked — real anti-bot challenge. Get
                        # actual browser clearance cookies and retry once
                        # more before giving up.
                        cf_headers = _cf_bypass_headers(url)
                        if cf_headers:
                            opts["http_headers"] = cf_headers
                            try:
                                return _run(fmt_chain)
                            except Exception as e3:
                                last_err = e3
            if "Requested format is not available" in str(last_err):
                try:
                    return _run("best")
                except Exception:
                    pass
            # Every yt-dlp retry tier exhausted — one bonus attempt via the
            # vendored youtube-dl extractors before giving up entirely.
            legacy = _legacy_download(url, out_dir, audio_only=False, progress_hook=progress_hook)
            if legacy:
                return legacy
            raise last_err
    raise last_err


def _has_real_video_stream(path: str) -> bool:
    """Sanity check applied after every video download, regardless of site.

    Sites with no dedicated yt-dlp extractor fall through to yt-dlp's
    generic HTML scanner, which sometimes locks onto the wrong URL on the
    page (an og:image / poster thumbnail) instead of the real video —
    especially on JS-rendered players yt-dlp can't execute. That produces
    exactly the 'preview downloads instead of the video' symptom, on any
    site, with no way to special-case it in advance since it depends on
    that specific page's markup.

    What we CAN do generically: verify the file we actually got has a real
    video stream before uploading it as one. If it doesn't, this raises
    instead of silently delivering a mislabeled image as 'video.mp4'.
    """
    if shutil.which("ffprobe") is None:
        return True  # can't verify, don't block on it
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        return "video" in result.stdout
    except Exception:
        return True  # probe itself failed - don't block a possibly-fine file on that


def _download_thumbnail(info_or_url, out_dir, video_path=None):
    """Get a Telegram-ready JPEG thumbnail.

    Saving the raw response bytes straight to a ".jpg" file (the old
    approach) silently produced a broken/non-rendering thumbnail whenever
    the site's thumbnail was actually WebP or PNG (very common — YouTube's
    own thumbnails are WebP) since the bytes were never actually a JPEG.
    download_official_thumbnail() re-encodes through ffmpeg so the output
    is a real JPEG regardless of the source format. If that fails (no
    thumbnail URL, download error, bad image), fall back to grabbing an
    actual frame out of the downloaded video with extract_thumbnail() —
    that always works as long as we have the file.
    """
    path = os.path.join(out_dir, "thumb.jpg")
    info = info_or_url if isinstance(info_or_url, dict) else {"thumbnail": info_or_url}
    try:
        if download_official_thumbnail(info, path):
            return path
    except Exception:
        pass
    if video_path and os.path.exists(video_path):
        try:
            if extract_thumbnail(video_path, path):
                return path
        except Exception:
            pass
    return None


def _quality_keyboard(session_id: str, tiers, minimal: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for t in tiers:
        res_label = t.get("label") or (f"{t['height']}p" if t['height'] else "Best available")
        if minimal:
            # Folder-style: "📁 Auto - unknown mp4" / "📁 360p - 360p mp4"
            display_label = "Auto" if res_label == "Best available" else res_label
            quality_text = "unknown" if res_label == "Best available" else res_label
            ext = t.get("ext") or "mp4"
            label = f"📁 {display_label} - {quality_text} {ext}"
        else:
            label = f"🎬 {res_label}{_fmt_size(t['filesize'])}"
        rows.append([make_button(label, callback_data=f"ytq:{session_id}:{t['format_id']}|{t['height']}", style=_BS.PRIMARY if _BS else None)])
    rows.append([
        make_button("🎵 ᴍᴘ3 64ᴋʙᴘs", callback_data=f"ytq:{session_id}:mp3_64", style=_BS.PRIMARY if _BS else None),
        make_button("🎵 ᴍᴘ3 128ᴋʙᴘs", callback_data=f"ytq:{session_id}:mp3_128", style=_BS.PRIMARY if _BS else None),
    ])
    rows.append([make_button("🎵 ᴍᴘ3 320ᴋʙᴘs", callback_data=f"ytq:{session_id}:mp3_320", style=_BS.PRIMARY if _BS else None)])
    rows.append([make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"ytq:{session_id}:cancel", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


def _fmt_count(n):
    if not n:
        return None
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


async def _edit_status(status_msg: Message, text: str, reply_markup=None):
    """Works whether status_msg is a plain text message or a photo message."""
    if status_msg.photo:
        return await safe_edit(status_msg.edit_caption, text, reply_markup=reply_markup, parse_mode=enums.ParseMode.HTML)
    return await safe_edit(status_msg.edit_text, text, reply_markup=reply_markup, parse_mode=enums.ParseMode.HTML)


def _make_download_progress_hook(status: Message, loop: asyncio.AbstractEventLoop,
                                  file_name=None, duration=None, quality=None):
    """yt-dlp calls progress_hooks synchronously from the worker thread that's
    running the actual download, so we can't just `await _edit_status(...)`
    here directly. Instead we hand the edit coroutine back to the bot's main
    event loop with run_coroutine_threadsafe. Throttled the same way the
    upload progress is, so it doesn't hammer Telegram's edit rate limit.

    file_name/duration/quality are optional — pass them when already known
    (e.g. from the quality picker session) so the progress box shows them."""
    state = {"last_edit": 0.0, "last_pct": -1}

    def _hook(d):
        status_type = d.get("status")
        if status_type == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            now = time.time()
            pct = (downloaded * 100 / total) if total else 0
            finished_chunk = total and downloaded >= total
            if not finished_chunk and (now - state["last_edit"] < 2.5 or int(pct) == state["last_pct"]):
                return
            state["last_edit"] = now
            state["last_pct"] = int(pct)
            text = format_progress(
                pct,
                speed_bps=d.get("speed"),
                done_bytes=downloaded,
                total_bytes=total,
                elapsed_secs=d.get("elapsed"),
                eta_secs=d.get("eta"),
                title="Downloading...",
                file_name=file_name,
                duration=duration,
                quality=quality,
            )
            asyncio.run_coroutine_threadsafe(_edit_status(status, text), loop)
        elif status_type == "finished":
            asyncio.run_coroutine_threadsafe(
                _edit_status(status, f"<b>{E_BOLT} Processing...</b>"), loop
            )

    return _hook


def _has_quality_formats_sync(url: str) -> bool:
    """Quick check: does yt-dlp find usable video formats for this URL?
    Used by other modules (e.g. facebook.py) to decide whether to show the
    shared quality picker or fall back to a site-specific method."""
    if yt_dlp is None:
        return False
    try:
        info = _extract_info(url)
        return bool(_pick_qualities(info))
    except Exception:
        return False


async def has_quality_formats(url: str) -> bool:
    return await asyncio.to_thread(_has_quality_formats_sync, url)


async def _show_quality_picker(client: Client, message: Message, url: str, fallback_candidates=None, minimal: bool = False, title_override: str = None, notify_on_failure: bool = True):
    """notify_on_failure=False lets a caller (the generic auto-detect router)
    attempt the FULL picker flow — same extraction/retry/headless resilience
    as the /yt command — without showing an error message if it genuinely
    can't fetch the link, so the message can silently fall through to
    gallery-dl / the raw-file fallback next instead of getting a confusing
    yt-dlp-specific error for a site yt-dlp was never meant to own. Returns
    True on success (something was posted), False on failure."""
    if yt_dlp is None:
        await message.reply_text(
            f"<b>{E_CROSS} yt-dlp not installed.</b>\n<i>Run <code>pip install yt-dlp</code> on the host.</i>",
            parse_mode=enums.ParseMode.HTML
        )
        return False

    cache_status = await message.reply_text(f"<b>{E_BOLT} Checking cache...</b>", parse_mode=enums.ParseMode.HTML)
    from Akbots.link_cache import try_send_cached
    if await try_send_cached(client, message, url, cache_status):
        return True
    try:
        await cache_status.delete()
    except Exception:
        pass

    fetching = await message.reply_text(f"<b>{E_ROCKET} Fetching available qualities...</b>", parse_mode=enums.ParseMode.HTML)
    remaining_candidates = list(fallback_candidates or [])
    tier_urls = None
    tiers_from_menu = None
    try:
        info = await asyncio.to_thread(_extract_info, url)
    except Exception as e:
        # yt-dlp's generic extractor can crash on a page's own malformed/
        # non-standard JS (e.g. a "flashvars" block that isn't valid JSON
        # because the site builds it with string concatenation instead of
        # a literal) - that's a page-parsing bug, not proof the video is
        # unreachable. Before giving up, try actually rendering the page in
        # headless Chromium and grabbing real media URLs from network
        # traffic - the same last-resort the auto-detect link handler
        # already uses, now available here too so /yt, /dl, and anywhere
        # else that calls this picker directly get the same resilience.
        info = None
        tier_urls = None  # set below if the player's own quality menu yields >1 distinct resolution
        headless_available = lambda: False
        find_media_urls = find_media_urls_by_quality = None
        if ENABLE_HEADLESS_FALLBACK:
            try:
                from Akbots.headless import (
                    available as headless_available, find_media_urls, find_media_urls_by_quality,
                )
            except Exception:
                headless_available = lambda: False
                find_media_urls = find_media_urls_by_quality = None
        if headless_available():
            await safe_edit(fetching.edit_text, 
                f"<b>{E_ROCKET} Direct extraction failed — rendering the page...</b>",
                parse_mode=enums.ParseMode.HTML
            )
            # First, see if the player exposes its own quality menu (Video.js/
            # JW Player/Plyr and clones) - if so, each option there is a
            # DIFFERENT resolved URL for a genuinely different resolution,
            # which the plain single-candidate capture below can never see
            # (it only ever grabs whichever one auto-played first).
            by_quality = await find_media_urls_by_quality(url)
            if len(by_quality) > 1:
                labels_sorted = sorted(
                    by_quality.items(),
                    key=lambda kv: (0 if kv[0].lower() == "auto" else 1,
                                    -(int(m.group(1)) if (m := re.match(r"(\d+)p", kv[0], re.I)) else 0))
                )
                probed_tiers = []
                tier_urls = {}
                for i, (label, qurl) in enumerate(labels_sorted):
                    fmt_id = f"qm_{i}"
                    height_m = re.match(r"(\d+)p", label, re.IGNORECASE)
                    try:
                        qinfo = await asyncio.to_thread(_extract_info, qurl)
                        size = qinfo.get("filesize") or qinfo.get("filesize_approx") or 0
                    except Exception:
                        qinfo, size = None, 0
                    probed_tiers.append({
                        "format_id": fmt_id, "height": int(height_m.group(1)) if height_m else 0,
                        "ext": "mp4", "filesize": size, "label": label,
                    })
                    tier_urls[fmt_id] = qurl
                    if qinfo is not None and info is None:
                        info = qinfo  # just need any one for title/uploader/duration/thumbnail below
                if info is None:
                    tier_urls = None  # menu was found but every option failed to actually resolve - fall through
                else:
                    tiers_from_menu = probed_tiers

            if info is None:
                candidates = await find_media_urls(url)
                # Try each candidate in turn (manifests/largest-first) - the
                # first "video-shaped" response isn't always the real content;
                # a small looping teaser/poster clip can show up before it.
                # _pick_qualities here just confirms yt-dlp can read the URL at
                # all; whether it's actually the FULL video (vs. a preview) can
                # only be confirmed after downloading, so any leftover
                # candidates are carried into the session for the download step
                # to fall back to if this one turns out to be a preview.
                for i, candidate in enumerate(candidates):
                    try:
                        candidate_info = await asyncio.to_thread(_extract_info, candidate)
                    except Exception:
                        continue
                    if _pick_qualities(candidate_info):
                        info = candidate_info
                        url = candidate  # download the resolved media url, not the original page
                        remaining_candidates = candidates[i + 1:]
                        break
        if info is None:
            # Last resort, YouTube only: yt-dlp's own extraction (direct +
            # headless) has already failed above. Hand off to the raw
            # fallback scraper in youtube.py before giving up entirely — it
            # edits `fetching` itself either way (success or a definitive
            # failure message), so just return once it's run.
            if YOUTUBE_PATTERN.search(url):
                try:
                    from Akbots.youtube import _handle_youtube_fallback
                    if await _handle_youtube_fallback(client, message, url, status=fetching):
                        return True
                except Exception:
                    pass
            if not notify_on_failure:
                try:
                    await fetching.delete()
                except Exception:
                    pass
                return False
            await safe_edit(fetching.edit_text, 
                f"<b>{E_CROSS} Couldn't fetch info:</b>\n<code>{_clean_ytdlp_error(e)}</code>",
                parse_mode=enums.ParseMode.HTML
            )
            return False

    tiers = tiers_from_menu if tier_urls else _pick_qualities(info)
    if not tiers:
        if not notify_on_failure:
            try:
                await fetching.delete()
            except Exception:
                pass
            return False
        await safe_edit(fetching.edit_text, f"<b>{E_CROSS} No downloadable video formats found.</b>", parse_mode=enums.ParseMode.HTML)
        return False

    title = title_override or info.get("title", "Video")
    uploader = info.get("uploader", "Unknown")
    views = _fmt_count(info.get("view_count"))
    upload_date = info.get("upload_date")  # YYYYMMDD
    if upload_date and len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    lines = [f"<b>{E_ROCKET} {title[:80]}</b>", ""]
    lines.append(f"👤 <b>ʙʏ:</b> {uploader}")
    lines.append(f"⏱ <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> {_fmt_duration(info.get('duration'))}")
    if views:
        lines.append(f"👁 <b>ᴠɪᴇᴡs:</b> {views}")
    if upload_date:
        lines.append(f"📅 <b>ᴜᴘʟᴏᴀᴅᴇᴅ:</b> {upload_date}")
    lines.append("")
    lines.append("<b>ᴀᴠᴀɪʟᴀʙʟᴇ ǫᴜᴀʟɪᴛɪᴇs:</b>")
    for t in tiers:
        res_label = t.get("label") or (f"{t['height']}p" if t['height'] else "Best available")
        lines.append(f"✅ {res_label}{_fmt_size(t['filesize'])}")
    lines.append("🎵 MP3 (audio)")
    lines.append("")
    lines.append("<i>Tap a quality below to download:</i>")
    text = "<blockquote>" + "\n".join(lines) + "</blockquote>"

    keyboard = None  # built below once session_id is known
    thumb_url = info.get("thumbnail")

    session_id = uuid.uuid4().hex[:10]
    _SESSIONS[session_id] = {
        "url": url,
        "title": title,
        "thumbnail": thumb_url,
        "uploader": uploader,
        "duration": info.get("duration", 0),
        "chat_id": message.chat.id,
        "reply_to": message.id,
        "fallback_candidates": remaining_candidates,
        "tier_urls": tier_urls,
    }
    keyboard = _quality_keyboard(session_id, tiers, minimal=minimal)

    await fetching.delete()
    if thumb_url:
        try:
            await client.send_photo(
                message.chat.id, photo=thumb_url, caption=text, reply_markup=keyboard,
                reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML
            )
            return True
        except Exception:
            pass
    await message.reply_text(text, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
    return True


@Client.on_callback_query(filters.regex(r"^ytq:([a-f0-9]+):(\S+)$"))
async def quality_pick_callback(client: Client, callback_query: CallbackQuery):
    session_id, choice = callback_query.matches[0].group(1), callback_query.matches[0].group(2)
    session = _SESSIONS.get(session_id)
    if not session:
        return await callback_query.answer("This session expired. Send the link again.", show_alert=True)

    if choice == "cancel":
        _SESSIONS.pop(session_id, None)
        await callback_query.message.delete()
        return await callback_query.answer("Cancelled.")

    await callback_query.answer("Downloading...")
    audio_only = choice.startswith("mp3")
    audio_bitrate = "192"
    format_id, height = None, None
    if audio_only:
        # "mp3" alone is kept for backward-compat with any pre-existing
        # session started before this bitrate-picker was added.
        if "_" in choice:
            audio_bitrate = choice.split("_", 1)[1]
    else:
        if "|" in choice:
            format_id, height_str = choice.split("|", 1)
            try:
                height = int(height_str)
            except ValueError:
                height = None
        else:
            format_id = choice  # backward-compat with any pre-existing session

    session_dir = os.path.join(DOWNLOAD_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    status = callback_query.message

    task_id = None
    try:
        await _edit_status(status, f"<b>{E_ROCKET} Downloading...</b>")
        loop = asyncio.get_running_loop()
        if audio_only:
            quality_label = f"MP3 {audio_bitrate}kbps"
        elif height:
            quality_label = f"{height}p"
        else:
            quality_label = "Best available"

        from Akbots import task_manager
        try:
            task_id = task_manager.register(
                callback_query.from_user.id, asyncio.current_task(),
                f"YouTube/yt-dlp: {session.get('title', 'video')[:40]}"
            )
        except Exception:
            task_id = None

        async with task_manager.queue_slot(callback_query.from_user.id, status_msg=status):
            await _edit_status(status, f"<b>{E_ROCKET} Downloading...</b>")
            hook = _make_download_progress_hook(
                status, loop,
                file_name=session.get("title"),
                duration=session.get("duration"),
                quality=quality_label,
            )

            filepath, info = None, None
            last_exc = None
            tier_url = (session.get("tier_urls") or {}).get(format_id) if not audio_only else None
            if tier_url:
                candidate_urls = [tier_url]
                format_id, height = None, None  # tier_url is already the one resolved file — no format to pick within it
            else:
                candidate_urls = [session["url"]] + (session.get("fallback_candidates") or [])
            for idx, candidate_url in enumerate(candidate_urls):
                try:
                    # Only the originally-chosen URL (idx 0) should use the
                    # exact format_id/height the user tapped; any fallback
                    # candidate is a different underlying stream found by
                    # headless rendering, so its format_id would be meaningless
                    # here - just grab its best available format instead.
                    use_format_id = format_id if idx == 0 else None
                    use_height = height if idx == 0 else None
                    candidate_path, candidate_info = await asyncio.to_thread(
                        _download_selected, candidate_url, session_dir, use_format_id, audio_only, use_height, hook,
                        audio_bitrate
                    )
                    if not os.path.exists(candidate_path):
                        raise FileNotFoundError("Download finished but file was not found (likely size limit).")

                    if not audio_only and not await asyncio.to_thread(_has_real_video_stream, candidate_path):
                        # This candidate was a preview/poster clip, not the real
                        # video - discard it and try the next ranked candidate
                        # (if headless rendering found more than one), instead
                        # of giving up on the very first "video-shaped" URL seen.
                        try:
                            os.remove(candidate_path)
                        except OSError:
                            pass
                        last_exc = ValueError(
                            "Extracted file has no actual video stream — this site's real video "
                            "couldn't be located (likely a JS-rendered player yt-dlp can't see through), "
                            "only a static preview/thumbnail was found."
                        )
                        continue

                    filepath, info = candidate_path, candidate_info
                    break
                except Exception as e:
                    last_exc = e
                    continue

            if filepath is None:
                raise last_exc or FileNotFoundError("Download failed.")

            size = os.path.getsize(filepath)
            if size > YTDL_MAX_FILESIZE:
                raise ValueError(f"File too large ({round(size / (1024*1024))} MB) to upload to Telegram.")

            await _edit_status(status, f"<b>{E_BOLT} Uploading...</b>")
            thumb_path = None
            real_duration, real_width, real_height = 0, 0, 0
            if not audio_only:
                real_duration, real_width, real_height = await asyncio.to_thread(get_video_metadata, filepath)
                thumb_path = await asyncio.to_thread(_download_thumbnail, info, session_dir, filepath)
            caption = format_media_caption(info, credit="anujedits76")
            desc_markup = build_description_button(info, credit="anujedits76")
            progress = make_upload_progress(
                status,
                file_name=session.get("title"),
                duration=session.get("duration") or real_duration,
                quality=quality_label,
            )

            if audio_only:
                sent = await client.send_audio(
                    session["chat_id"], filepath, thumb=thumb_path, caption=caption,
                    reply_to_message_id=session["reply_to"], parse_mode=enums.ParseMode.HTML,
                    reply_markup=desc_markup, progress=progress
                )
            else:
                # info.get("duration") is what the site claims; real_duration is
                # ffprobe reading the actual downloaded file - trust ffprobe
                # whenever the site's own value is missing (common on generic/
                # HTML5 pages, which is exactly when the thumbnail was also
                # unreliable), so the Telegram player doesn't show 0:00.
                duration = int(info.get("duration") or real_duration or 0)
                sent = await client.send_video(
                    session["chat_id"], filepath, thumb=thumb_path, caption=caption,
                    duration=duration, width=real_width or None, height=real_height or None,
                    reply_to_message_id=session["reply_to"], parse_mode=enums.ParseMode.HTML,
                    supports_streaming=True, reply_markup=desc_markup, progress=progress
                )
            try:
                from Akbots.backup import backup_message
                await backup_message(client, sent)
            except Exception:
                pass
            try:
                from Akbots.link_cache import store as _cache_store
                cache_key = session["url"] + ("#audio" if audio_only else "")
                await _cache_store(cache_key, sent, caption=caption)
            except Exception:
                pass
            try:
                from database.db import db as _db
                await _db.add_download_history(callback_query.from_user.id, {
                    "title": session.get("title") or "Unknown",
                    "url": session.get("url"),
                    "type": "audio" if audio_only else "video",
                    "quality": quality_label,
                })
            except Exception:
                pass
            await status.delete()
    except Exception as e:
        await _edit_status(status, f"<b>{E_CROSS} Download failed:</b>\n<code>{strip_ansi(e)}</code>")
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(callback_query.from_user.id, task_id)
            except Exception:
                pass
        _SESSIONS.pop(session_id, None)
        shutil.rmtree(session_dir, ignore_errors=True)


@Client.on_message(filters.command(["yt", "dl"]) & filters.private)
async def yt_video_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_BOLT} Usage:</b> <code>/yt &lt;video URL&gt;</code>\n"
            f"<i>Supports YouTube, Instagram, and 1000+ other yt-dlp-compatible sites. "
            f"Shows a quality picker with real thumbnails.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    await _show_quality_picker(client, message, message.command[1])


@Client.on_message(filters.command(["yta", "song", "adl"]) & filters.private)
async def yt_audio_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_BOLT} Usage:</b> <code>/yta &lt;video URL&gt;</code> — extracts audio (mp3) directly",
            parse_mode=enums.ParseMode.HTML
        )
    url = message.command[1]
    if yt_dlp is None:
        return await message.reply_text(
            f"<b>{E_CROSS} yt-dlp not installed.</b>", parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>{E_ROCKET} Downloading audio...</b>", parse_mode=enums.ParseMode.HTML)
    from Akbots.link_cache import try_send_cached, store as _cache_store
    if await try_send_cached(client, message, url + "#audio", status):
        return
    session_dir = os.path.join(DOWNLOAD_DIR, uuid.uuid4().hex[:10])
    os.makedirs(session_dir, exist_ok=True)
    try:
        loop = asyncio.get_running_loop()
        hook = _make_download_progress_hook(status, loop)
        filepath, info = await asyncio.to_thread(_download_selected, url, session_dir, None, True, None, hook)
        if not os.path.exists(filepath):
            raise FileNotFoundError("Download finished but file was not found.")
        await safe_edit(status.edit_text, f"<b>{E_BOLT} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
        thumb_path = await asyncio.to_thread(_download_thumbnail, info, session_dir)
        progress = make_upload_progress(
            status,
            file_name=info.get("title"),
            duration=info.get("duration"),
            quality="MP3 (Audio)",
        )
        desc_markup = build_description_button(info, credit="anujedits76")
        sent = await client.send_audio(
            message.chat.id, filepath, thumb=thumb_path,
            caption=format_media_caption(info, credit="anujedits76"),
            reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML,
            reply_markup=desc_markup, progress=progress
        )
        try:
            from Akbots.backup import backup_message
            await backup_message(client, sent)
        except Exception:
            pass
        try:
            await _cache_store(url + "#audio", sent, caption=format_media_caption(info, credit="anujedits76"))
        except Exception:
            pass
        await status.delete()
    except Exception as e:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b>\n<code>{strip_ansi(e)}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(session_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Playlist support — a link carrying &list= downloads every video at best
# quality (no per-video picker, that'd mean tapping through N menus).
# ---------------------------------------------------------------------------

def _extract_playlist_video_urls(url: str):
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    urls = []
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        webpage_url = e.get("url") or e.get("webpage_url")
        vid = e.get("id")
        if webpage_url and webpage_url.startswith("http"):
            urls.append(webpage_url)
        elif vid:
            urls.append(f"https://www.youtube.com/watch?v={vid}")
    return urls


async def _run_playlist(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_ROCKET} Fetching playlist info...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        video_urls = await asyncio.to_thread(_extract_playlist_video_urls, url)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't read playlist:</b>\n<code>{strip_ansi(e)}</code>", parse_mode=enums.ParseMode.HTML)
    if not video_urls:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No videos found in this playlist.</b>", parse_mode=enums.ParseMode.HTML)

    total = len(video_urls)
    await safe_edit(status.edit_text, f"<b>{E_ROCKET} Playlist detected — {total} video(s), best quality. Starting...</b>", parse_mode=enums.ParseMode.HTML)

    for i, video_url in enumerate(video_urls, 1):
        item_status = await message.reply_text(f"<b>{E_ROCKET} Video {i}/{total}: downloading...</b>", parse_mode=enums.ParseMode.HTML)
        session_dir = os.path.join(DOWNLOAD_DIR, uuid.uuid4().hex[:10])
        os.makedirs(session_dir, exist_ok=True)
        try:
            filepath, info = await asyncio.to_thread(_download_selected, video_url, session_dir, "bestvideo", False)
            if not os.path.exists(filepath):
                raise FileNotFoundError("Download finished but file was not found (likely size limit).")
            await safe_edit(item_status.edit_text, f"<b>{E_BOLT} Video {i}/{total}: uploading...</b>", parse_mode=enums.ParseMode.HTML)
            real_duration, real_width, real_height = await asyncio.to_thread(get_video_metadata, filepath)
            thumb_path = await asyncio.to_thread(_download_thumbnail, info, session_dir, filepath)
            progress = make_upload_progress(
                item_status,
                file_name=info.get("title"),
                duration=info.get("duration") or real_duration,
                quality="Best available",
            )
            desc_markup = build_description_button(info, credit="anujedits76")
            sent = await client.send_video(
                message.chat.id, filepath, thumb=thumb_path,
                caption=format_media_caption(info, credit="anujedits76"),
                duration=int(info.get("duration") or real_duration or 0),
                width=real_width or None, height=real_height or None,
                reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML,
                supports_streaming=True, reply_markup=desc_markup, progress=progress
            )
            try:
                from Akbots.backup import backup_message
                await backup_message(client, sent)
            except Exception:
                pass
            await item_status.delete()
        except Exception as e:
            await safe_edit(item_status.edit_text, f"<b>{E_CROSS} Video {i}/{total} failed:</b>\n<code>{strip_ansi(e)}</code>", parse_mode=enums.ParseMode.HTML)
        finally:
            shutil.rmtree(session_dir, ignore_errors=True)

    await status.delete()


# ---------------------------------------------------------------------------
# Auto-detect: bare links (no /yt, /insta, /pin command) route straight into
# the same quality picker used by the commands above. Registered in group=1
# so they don't clash with other files' handlers, and exclude "^/" so
# commands aren't processed twice.
# ---------------------------------------------------------------------------

# Instagram auto-detect now lives in Akbots/instagram.py (mirrors
# facebook.py: tries this same yt-dlp quality picker first, then falls back
# to its own HTML-scraping downloader if yt-dlp can't handle the link) — do
# NOT add an instagram.com handler here too, or every Instagram link would
# be processed twice.


@Client.on_message(filters.text & filters.private & filters.regex(PINTEREST_PATTERN) & ~filters.regex(r"^/"), group=1)
async def pinterest_auto_detect(client: Client, message: Message):
    m = PINTEREST_PATTERN.search(message.text)
    if not m:
        return
    url = m.group(0)
    # Probe first: video pins get the quality picker here; image pins/boards
    # get handed off to gallery.py's gallery-dl handler.
    is_video = await has_quality_formats(url)
    if is_video:
        await _show_quality_picker(client, message, url)
    else:
        from Akbots.gallery import _handle as gallery_handle
        await gallery_handle(client, message, url)


@Client.on_message(
    filters.text & filters.private
    & (filters.regex(YOUTUBE_PATTERN) | filters.regex(PLAYLIST_REGEX))
    & ~filters.regex(r"^/"),
    group=1,
)
async def youtube_auto_detect(client: Client, message: Message):
    text = message.text.strip()
    if PLAYLIST_REGEX.search(text):
        await _run_playlist(client, message, text)
        return
    m = YOUTUBE_PATTERN.search(text)
    if m:
        await _show_quality_picker(client, message, m.group(0))


@Client.on_message(filters.text & filters.private & filters.regex(MPD_PATTERN) & ~filters.regex(r"^/"), group=1)
async def mpd_auto_detect(client: Client, message: Message):
    """Dedicated DASH (.mpd) support — sends straight to the yt-dlp quality
    picker, same as m3u8/YouTube/Instagram, instead of the slower generic
    fallback path."""
    m = MPD_PATTERN.search(message.text)
    if m:
        await _show_quality_picker(client, message, m.group(0))


def _extract_generic_url(text: str):
    m = GENERIC_URL_PATTERN.search(text)
    if not m:
        return None
    url = m.group(0)
    lower = url.lower()
    return None if any(d in lower for d in _EXCLUDED_DOMAINS) else url


def _dedicated_extractor_ie(url: str):
    """Cheap, offline check (no network call) for whether yt-dlp has a
    NAMED extractor for this URL (Twitch, TikTok, Vimeo, Dailymotion,
    X/Twitter video, SoundCloud, ...) as opposed to only its generic
    HTML-scraping extractor. ie.suitable() is a pure regex match, so this
    is fast and safe to call before doing any real network extraction.

    Used to decide: if has_quality_formats(url) comes back False, is that
    because yt-dlp genuinely doesn't own this site (safe to let gallery-dl
    / the raw-file fallback try next), or because yt-dlp DOES own this
    site but the extraction itself failed (private video, login wall,
    geo-block, deleted post, temporary API hiccup, etc.)? In the second
    case the link should never be handed to gallery-dl - gallery-dl has no
    concept of this site's video formats at all, so it either bails with
    an unrelated error or (if its own generic extractor is enabled on this
    host) grabs a poster/thumbnail image and calls it a success. Either
    way the user ends up staring at the wrong error for the wrong reason -
    the "gallery-dl grabs it before yt-dlp and then errors on yt-dlp
    sites" symptom."""
    if yt_dlp is None:
        return None
    try:
        from yt_dlp.extractor import gen_extractor_classes
        for ie in gen_extractor_classes():
            key = ie.ie_key()
            if key == "Generic":
                continue
            try:
                if ie.suitable(url):
                    return key
            except Exception:
                continue
    except Exception:
        return None
    return None


@Client.on_message(
    filters.text & filters.private & filters.regex(GENERIC_URL_PATTERN) & ~filters.regex(r"^/"),
    group=2,  # after the specific group=1 handlers above and in other files
)
async def generic_ytdlp_auto_detect(client: Client, message: Message):
    url = _extract_generic_url(message.text)
    if not url:
        return

    # Tier 1 - yt-dlp owns this domain by name (Twitch, TikTok, Vimeo,
    # Dailymotion, X/Twitter video, SoundCloud, ...). Handle it here
    # exclusively, success or failure - gallery-dl never gets a turn on it.
    ie_key = _dedicated_extractor_ie(url)
    if ie_key:
        # One full attempt - same extraction/retry resilience as the /yt
        # command itself - success or a real, informative failure either
        # way; gallery-dl never gets a turn on a site yt-dlp owns by name.
        await _show_quality_picker(client, message, url, minimal=True)
        message.stop_propagation()
        return

    # Tier 2 - not a named yt-dlp site (could be a named gallery-dl site, or
    # unknown to both tools by name). Policy: yt-dlp always gets first try,
    # no matter which site the link is from - and it gets the SAME full
    # attempt /yt would (extraction + retries + 403/legacy fallbacks),
    # not just a bare probe, so a link that would work via /yt also works
    # automatically here. notify_on_failure=False keeps this attempt quiet
    # on failure (no "couldn't fetch" message) so the link can still fall
    # through to gallery-dl's probe (group=3) and finally the raw-file
    # fallback (group=4) without a confusing yt-dlp-specific error first.
    if await _show_quality_picker(client, message, url, minimal=True, notify_on_failure=False):
        message.stop_propagation()
        return

    # yt-dlp's generic extractor only scans the HTML/JS it downloads - it
    # never runs JavaScript, so on JS-rendered players the real video URL
    # (built at runtime by the page's own player script) simply isn't in
    # what yt-dlp saw. Last resort: actually render the page in headless
    # Chromium and watch network traffic for the real media request.
    if not ENABLE_HEADLESS_FALLBACK:
        return  # disabled - let gallery-dl/raw fallback try next
    from Akbots.headless import available as headless_available, find_media_urls
    if not headless_available():
        return  # not installed on this host - let gallery-dl/raw fallback try next

    probing = await message.reply_text(
        f"<b>{E_ROCKET} Couldn't find a direct video link — rendering the page...</b>",
        parse_mode=enums.ParseMode.HTML
    )
    media_urls = await find_media_urls(url)
    chosen_idx = None
    for i, candidate in enumerate(media_urls):
        if await has_quality_formats(candidate):
            chosen_idx = i
            break
    if chosen_idx is None:
        await probing.delete()
        return  # still nothing usable - let gallery-dl/raw fallback try next

    await probing.delete()
    # Any candidates ranked below the chosen one are carried along as a
    # download-time fallback - some players fire a small preview/poster
    # clip before the real stream, and that can still pass the format
    # check above yet turn out to have no real video content once
    # downloaded (checked later via _has_real_video_stream).
    await _show_quality_picker(
        client, message, media_urls[chosen_idx],
        fallback_candidates=media_urls[chosen_idx + 1:],
        minimal=True,
    )
    # media_urls[chosen_idx] (the rendered/derived link) is what
    # has_quality_formats was actually true for - NOT the original page url.
    # gallery.py's group=3 guard only re-checks has_quality_formats(url) on
    # the ORIGINAL url, so without this it can't tell this link was already
    # handled here and goes on to try gallery-dl on it too, producing a
    # stray error message.
    message.stop_propagation()


# --------------------------------------------------------
# Subtitle & Transcript Download
#
# /subtitle <url> — fetches whatever subtitles/captions are available for
# a video (manual ones preferred over auto-generated), sends the raw
# subtitle file (.srt/.vtt), and also a cleaned plain-text transcript
# (timestamps and formatting tags stripped) for reading/searching.
# --------------------------------------------------------

def _vtt_or_srt_to_transcript(path: str) -> str:
    """Strips timing lines, cue numbers, and tags from a .vtt/.srt file,
    collapsing it down to plain readable text."""
    import re as _re
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()
    lines = []
    seen = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT":
            continue
        if _re.match(r"^\d+$", line):
            continue
        if "-->" in line:
            continue
        line = _re.sub(r"<[^>]+>", "", line)  # strip inline vtt tags like <c>
        if line and line not in seen:
            lines.append(line)
            seen.add(line)
    return "\n".join(lines)


def _download_subtitles_sync(url: str, out_dir: str, lang: str = None):
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "writesubtitles": True, "writeautomaticsub": True,
        "subtitleslangs": [lang] if lang else ["en", "en-US", "en-orig"],
        "outtmpl": os.path.join(out_dir, "%(title).70s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    subs_dir_files = [f for f in os.listdir(out_dir) if f.endswith((".vtt", ".srt"))]
    if not subs_dir_files:
        return None, info
    # Prefer a manually-authored track (usually shorter filename, no
    # "auto"/"a." markers) over an auto-generated one if both exist.
    subs_dir_files.sort(key=lambda f: ("auto" in f.lower(), f))
    return os.path.join(out_dir, subs_dir_files[0]), info


@Client.on_message(filters.command(["subtitle", "subs", "transcript"]) & filters.private)
async def subtitle_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/subtitle &lt;video URL&gt; [lang]</code>\n"
            f"<i>Example: <code>/subtitle https://youtu.be/xyz hi</code></i>",
            parse_mode=enums.ParseMode.HTML,
        )
    if yt_dlp is None:
        return await message.reply_text(
            f"<b>{E_CROSS} yt-dlp isn't available on this server.</b>", parse_mode=enums.ParseMode.HTML
        )

    url = message.command[1].strip()
    lang = message.command[2].strip() if len(message.command) > 2 else None
    status = await message.reply_text(f"<b>{E_INFO} Fetching subtitles/transcript...</b>", parse_mode=enums.ParseMode.HTML)

    session_dir = os.path.join(DOWNLOAD_DIR, f"subs_{uuid.uuid4().hex[:10]}")
    os.makedirs(session_dir, exist_ok=True)
    try:
        sub_path, info = await asyncio.to_thread(_download_subtitles_sync, url, session_dir, lang)
        if not sub_path:
            return await safe_edit(status.edit_text, 
                f"<b>{E_CROSS} No subtitles/transcript available for this video</b>"
                + (f" in '{lang}'." if lang else " (try specifying a language code, e.g. /subtitle <url> en)."),
                parse_mode=enums.ParseMode.HTML,
            )

        title = info.get("title") or "subtitles"
        transcript_text = await asyncio.to_thread(_vtt_or_srt_to_transcript, sub_path)
        transcript_path = os.path.join(session_dir, f"{title[:60]}_transcript.txt")
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(transcript_text or "(empty transcript)")

        await safe_edit(status.edit_text, f"<b>{E_ROCKET} Uploading subtitle & transcript...</b>", parse_mode=enums.ParseMode.HTML)
        await client.send_document(
            message.chat.id, sub_path, caption=f"<blockquote><b>{E_CHECK} {title[:100]} — raw subtitle file</b></blockquote>",
            reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML,
        )
        await client.send_document(
            message.chat.id, transcript_path, caption=f"<blockquote><b>{E_CHECK} {title[:100]} — plain-text transcript</b></blockquote>",
            reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML,
        )
        try:
            from database.db import db as _db
            await _db.add_download_history(message.from_user.id, {
                "title": title, "url": url, "type": "subtitle", "quality": lang or "auto",
            })
        except Exception:
            pass
        await status.delete()
    except Exception as e:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to fetch subtitles:</b>\n<code>{strip_ansi(e)}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(session_dir, ignore_errors=True)
