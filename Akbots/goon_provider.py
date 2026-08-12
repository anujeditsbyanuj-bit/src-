# Akbots - Don't Remove Credit - @AkBots_Official
#
# Login-gated site resolver, ported from a standalone script's AkClient
# class. Only the RESOLVE half was kept — the original script also had a
# Flask app serving a browser video-player page (index/play_video/get_m3u8
# routes + a full HTML/JS player) built on top of the same client; that
# half was deliberately left out here. This module only turns a video page
# URL into a direct m3u8 stream link, for Akbots/meow_downloader.py to then
# download and Akbots/goon_commands.py to send to the chat as a file —
# nothing here ever serves a player UI or a browser-playable link.
#
# Off by default: every function below is a no-op / returns None until
# config.GOON_BASE_URL is set (see config.py's comment on it for how).

import asyncio
import gzip
import logging
import re
import zlib
from functools import lru_cache

import requests

from config import GOON_BASE_URL, GOON_EMAIL, GOON_PASSWORD, GOON_COOKIES
# Shared with hotstar.py so both use identical Netscape-vs-pasted
# cookie-string detection instead of duplicating the parsing logic.
from Akbots.cookie_utils import parse_cookies
# Generic per-domain cookies store (/setcookies, /cookie panel) — if an
# admin has already uploaded cookies for GOON_BASE_URL's domain there,
# auto-detect and use those instead of needing a separate GOON_COOKIES.
from Akbots.cookies_manager import get_cookies_for_url

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(GOON_BASE_URL)


def _has_credentials() -> bool:
    return bool(GOON_EMAIL and GOON_PASSWORD)


def _has_cookies() -> bool:
    return bool(GOON_COOKIES and GOON_COOKIES.strip())


def _autodetect_cookies() -> str | None:
    """Has an admin already uploaded cookies for GOON_BASE_URL's domain via
    /setcookies or the /cookie panel? Returns the raw cookies.txt content
    if so, else None."""
    if not GOON_BASE_URL:
        return None
    path = get_cookies_for_url(GOON_BASE_URL)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"[goon] Couldn't read auto-detected cookies file {path}: {e}")
        return None


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class _GoonClient:
    """1:1 port of the original AkClient — same login flow, same
    encoding-detection, same m3u8-regex extraction. Only change: BASE_URL/
    EMAIL/PASSWORD now come from config.py instead of being hardcoded, so
    an operator who never sets GOON_BASE_URL gets a client that simply
    never logs in (ensure_session() below short-circuits on that)."""

    def __init__(self):
        self.session: requests.Session | None = None
        self.logged_in = False

    def ensure_session(self):
        if not is_configured():
            return None
        if not self.session or not self.logged_in:
            self.session = requests.Session()
            self.session.headers.update({
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            })
            auto_cookies = _autodetect_cookies()
            if auto_cookies:
                logger.info("[goon] Creating new session (auto-detected cookies for this domain via /setcookies)...")
                self.login_with_cookies(auto_cookies)
            elif _has_cookies():
                logger.info("[goon] Creating new session (via GOON_COOKIES)...")
                self.login_with_cookies(GOON_COOKIES)
            elif _has_credentials():
                logger.info("[goon] Creating new session (via GOON_EMAIL/GOON_PASSWORD)...")
                self.login()
            else:
                logger.info("[goon] Creating new session (no cookies for this domain and no GOON_EMAIL/"
                             "GOON_PASSWORD set — guest-only, get_m3u8_url() will fall back to its "
                             "unauthenticated fetch attempt).")
        return self.session

    def login_with_cookies(self, raw_cookies: str) -> bool:
        """Loads cookies straight into the session — either auto-detected
        from the generic /setcookies store for this domain, or the manual
        GOON_COOKIES config fallback. No /api/auth/signin POST at all,
        since these are an already-logged-in session's cookies."""
        cookies = parse_cookies(raw_cookies)
        if not cookies:
            logger.warning("[goon] Cookies were set but couldn't be parsed — falling back to guest.")
            self.logged_in = False
            return False
        try:
            domain = GOON_BASE_URL.split("://", 1)[-1].split("/", 1)[0]
            for name, value in cookies.items():
                self.session.cookies.set(name, value, domain=domain)
            self.logged_in = True
            logger.info(f"[goon] Loaded {len(cookies)} cookie(s) — session ready.")
            return True
        except Exception as e:
            logger.error(f"[goon] Loading GOON_COOKIES failed: {e}")
            self.logged_in = False
            return False

    def login(self) -> bool:
        logger.info(f"[goon] Attempting login with email: {GOON_EMAIL[:5]}...")
        self.session.headers.update({
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Content-Type": "application/json",
            "Origin": GOON_BASE_URL,
            "Referer": f"{GOON_BASE_URL}/",
            "DNT": "1",
            "Connection": "keep-alive",
        })
        try:
            self.session.get(GOON_BASE_URL, timeout=10)

            payload = {
                "login": GOON_EMAIL,
                "password": GOON_PASSWORD,
                "rememberMe": "1",
                "recaptcha": "",
                "trackingParamsBag": (
                    "eyJwcm9tb19pZCI6IiIsInZpZGVvX2lkIjpudWxsLCJzdHVkaW9faWQiOm51bGwsInByb2R1Y2VyX2lkIjpudWxsLC"
                    "JvcmllbnRhdGlvbiI6InN0cmFpZ2h0IiwibWxfcGFnZSI6Im1haW5fcGFnZSIsIm1sX3BhZ2VfdmFsdWVfaWQiOm51"
                    "bGwsIm1sX3BhZ2VfdmFsdWUiOm51bGwsIm1sX3BhZ2VfbnVtYmVyIjpudWxsLCJtbF9yZWZfcGFnZV92YWx1ZV9pZC"
                    "I6bnVsbCwibWxfcmVmX3BhZ2VfdmFsdWUiOiIiLCJtbF9yZWZfcGFnZV9udW1iZXIiOm51bGwsIm1sX3JlZl9wYWdl"
                    "IjoiZGlyZWN0In0="
                ),
            }
            login_res = self.session.post(f"{GOON_BASE_URL}/api/auth/signin", json=payload, timeout=15)

            if login_res.status_code == 200:
                try:
                    data = login_res.json()
                    if data.get("success") or data.get("data"):
                        self.logged_in = True
                        logger.info("[goon] Login successful!")
                        return True
                except Exception:
                    pass
                if len(self.session.cookies) > 0:
                    self.logged_in = True
                    logger.info("[goon] Login successful (session established)!")
                    return True

            self.logged_in = False
            return False
        except Exception as e:
            logger.error(f"[goon] Login error: {e}")
            self.logged_in = False
            return False

    def _decode_response(self, response) -> str:
        try:
            content_encoding = response.headers.get("Content-Encoding", "")
            if "gzip" in content_encoding:
                try:
                    return gzip.decompress(response.content).decode("utf-8", errors="ignore")
                except Exception:
                    pass
            if "deflate" in content_encoding:
                try:
                    return zlib.decompress(response.content).decode("utf-8", errors="ignore")
                except Exception:
                    try:
                        return zlib.decompress(response.content, -zlib.MAX_WBITS).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
            if "br" in content_encoding:
                try:
                    import brotli
                    return brotli.decompress(response.content).decode("utf-8", errors="ignore")
                except ImportError:
                    logger.warning("[goon] brotli not installed, skipping...")
                except Exception:
                    pass
            return response.text
        except Exception as e:
            logger.error(f"[goon] Decoding error: {e}")
            return response.text if response.text else str(response.content)

    @lru_cache(maxsize=100)
    def get_m3u8_url(self, video_url: str) -> str | None:
        logger.info(f"[goon] Processing video URL: {video_url[:80]}...")
        if "#" in video_url:
            video_url = video_url.split("#")[0]

        session = self.ensure_session()
        if session:
            try:
                logger.info("[goon] Attempt 1: authenticated session...")
                headers = {
                    "User-Agent": _UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Referer": GOON_BASE_URL,
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                }
                response = session.get(video_url, timeout=15, headers=headers)
                if response.status_code == 200:
                    html = self._decode_response(response)
                    if html:
                        m3u8 = self._extract_m3u8(html)
                        if m3u8:
                            logger.info("[goon] Found M3U8 URL with session!")
                            return m3u8
            except Exception as e:
                logger.warning(f"[goon] Session attempt failed: {e}")

        logger.info("[goon] Attempt 2: guest fetch...")
        try:
            guest_session = requests.Session()
            guest_session.headers.update({
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": GOON_BASE_URL,
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            })
            response = guest_session.get(video_url, timeout=15)
            if response.status_code == 200:
                html = self._decode_response(response)
                if html:
                    m3u8 = self._extract_m3u8(html)
                    if m3u8:
                        logger.info("[goon] Found M3U8 URL with guest!")
                        return m3u8
        except Exception as e:
            logger.warning(f"[goon] Guest attempt failed: {e}")

        logger.error("[goon] Failed to find M3U8 URL with all attempts.")
        return None

    def _extract_m3u8(self, html_content: str) -> str | None:
        if not html_content:
            return None
        html_content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", html_content)

        patterns = [
            r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
            r'//[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
            r'src\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'href\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'url\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'source\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        ]

        found_urls = []
        for pattern in patterns:
            for match in re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL):
                m3u8_url = (match[0] if isinstance(match, tuple) else match).strip()
                m3u8_url = m3u8_url.split('"')[0].split("'")[0].replace("&amp;", "&")
                if m3u8_url.startswith("//"):
                    m3u8_url = "https:" + m3u8_url
                if m3u8_url.startswith("http") and ".m3u8" in m3u8_url:
                    found_urls.append(m3u8_url)

        unique_urls = list(dict.fromkeys(found_urls))
        if unique_urls:
            logger.info(f"[goon] Found {len(unique_urls)} M3U8 URL(s)")
            return unique_urls[0]
        return None


_client = _GoonClient()

# Fixed resolution ladder the quality-choose menu shows (same 6 rungs the
# terabox.py picker uses) — every real HLS variant found in the master
# playlist gets snapped to whichever of these its height is closest to, so
# the UI stays a clean 6-item grid even if the source advertises odd
# heights like 1088 or 852.
_STANDARD_HEIGHTS = [2160, 1080, 720, 480, 240, 144]


def _nearest_standard_label(height: int) -> str:
    closest = min(_STANDARD_HEIGHTS, key=lambda h: abs(h - height))
    return f"{closest}p"


def _parse_master_playlist(playlist_text: str, base_url: str) -> list[dict]:
    """Parses #EXT-X-STREAM-INF variant lines out of an HLS master
    playlist into [{"height": int, "url": str, "bandwidth": int}, ...].
    Returns [] if playlist_text isn't a master playlist (e.g. it's already
    a media/variant playlist with #EXTINF segments, not #EXT-X-STREAM-INF
    variants) — the caller falls back to treating the single URL as Auto."""
    if not playlist_text or "#EXT-X-STREAM-INF" not in playlist_text:
        return []
    lines = playlist_text.splitlines()
    variants = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        res_m = re.search(r"RESOLUTION=\d+x(\d+)", line)
        bw_m = re.search(r"BANDWIDTH=(\d+)", line)
        url_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not url_line or url_line.startswith("#"):
            continue
        if url_line.startswith("http"):
            variant_url = url_line
        elif url_line.startswith("//"):
            variant_url = "https:" + url_line
        else:
            base = base_url[:base_url.rfind("/") + 1] if "/" in base_url else base_url
            variant_url = base + url_line
        variants.append({
            "height": int(res_m.group(1)) if res_m else 0,
            "url": variant_url,
            "bandwidth": int(bw_m.group(1)) if bw_m else 0,
        })
    return variants


def _fetch_playlist_text(url: str) -> str | None:
    """Plain GET for the master playlist body — uses the same client
    session as get_m3u8_url() so any auth cookies from login() carry over,
    with a fresh guest request as fallback."""
    session = _client.ensure_session()
    headers = {"User-Agent": _UA, "Referer": GOON_BASE_URL or "", "Accept": "*/*"}
    if session:
        try:
            r = session.get(url, timeout=10, headers=headers)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception as e:
            logger.warning(f"[goon] playlist fetch (session) failed: {e}")
    try:
        r = requests.get(url, timeout=10, headers=headers)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logger.warning(f"[goon] playlist fetch (guest) failed: {e}")
    return None


def _build_qualities(m3u8_url: str) -> list[dict]:
    """Auto entry always first (points at the master playlist — yt-dlp
    picks the best variant from it on its own), followed by one entry per
    real resolution variant found inside it, snapped to the standard
    ladder and de-duped (keeping the highest-bandwidth variant per label)."""
    qualities = [{"quality": "Auto", "url": m3u8_url}]
    playlist_text = _fetch_playlist_text(m3u8_url)
    if not playlist_text:
        return qualities
    variants = _parse_master_playlist(playlist_text, m3u8_url)
    if not variants:
        return qualities

    best_per_label: dict[str, dict] = {}
    for v in variants:
        if not v["height"]:
            continue
        label = _nearest_standard_label(v["height"])
        current = best_per_label.get(label)
        if not current or v["bandwidth"] > current["bandwidth"]:
            best_per_label[label] = v

    for label in sorted(best_per_label, key=lambda l: int(l[:-1]), reverse=True):
        qualities.append({"quality": label, "url": best_per_label[label]["url"]})
    return qualities


async def resolve_goon_stream(video_url: str) -> dict | None:
    """Returns a stream dict shaped like every other Akbots/meow*_provider
    fetch_stream_url() result — {"videoUrl", "qualities", "headers"} — so
    it plugs straight into Akbots/meow_downloader.download_stream()
    unchanged. `qualities` now has one entry per real resolution found
    inside the master playlist (2160p/1080p/720p/480p/240p/144p, snapped
    to the nearest standard rung) plus "Auto" pointing at the master
    itself, instead of always just a single "Auto" entry — so
    Akbots/goon_commands.py can show a real quality-choose menu. None if
    not configured or nothing found."""
    if not is_configured():
        return None
    m3u8 = await asyncio.to_thread(_client.get_m3u8_url, video_url)
    if not m3u8:
        return None
    qualities = await asyncio.to_thread(_build_qualities, m3u8)
    headers = {"User-Agent": _UA, "Referer": GOON_BASE_URL, "Origin": GOON_BASE_URL}
    return {"videoUrl": m3u8, "qualities": qualities, "headers": headers}
