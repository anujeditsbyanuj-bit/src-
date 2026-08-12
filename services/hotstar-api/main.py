"""
Hotstar Stream API
- POST /api/resolve        → give content_id, get M3U8/MPD URL + quality options
- POST /api/download       → give stream URL, downloads it (HLS -> .ts, DASH/Widevine -> .mp4), returns job_id
- GET  /api/status/{job_id} → check download progress
- GET  /api/file/{job_id}   → download the finished file
- GET  /                    → web UI

DASH/Widevine-protected streams (premium/live content with no plain HLS
variant) are handled by extracting the content key via Akbots.hotstar_widevine
(PSSH -> Widevine CDM -> license -> key), downloading the encrypted video+
audio tracks with yt-dlp, decrypting each with Akbots.mp4decrypt_util (the
project's bundled Bento4 mp4decrypt binary), then muxing with ffmpeg. Only
available when this service runs in-process with the rest of the bot — see
DASH_SUPPORTED below.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import PriorityQueue
from typing import Optional
from urllib.parse import urljoin

import requests
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ─── Optional DASH/Widevine support ────────────────────────────────────────────
# Only importable when this service is running in-process inside the bot
# (Akbots/hotstar_local_server.py — the "normal setup", see config.py), since
# it needs the rest of the repo's Akbots package. If this file is deployed
# standalone (its own Dockerfile, without the rest of the repo alongside
# it), these imports fail and DASH content simply isn't downloadable from
# that deployment — HLS content is unaffected either way.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
try:
    from Akbots.hotstar_widevine import extract_key_sync
    from Akbots.mp4decrypt_util import find_mp4decrypt, valid_key
    DASH_SUPPORTED = find_mp4decrypt() is not None
    if not DASH_SUPPORTED:
        logger.warning("DASH/Widevine: mp4decrypt binary not found — DASH content will fail at download time.")
except ImportError as e:
    logger.warning(f"DASH/Widevine support unavailable ({e}) — only HLS content will work from this deployment.")
    DASH_SUPPORTED = False

# Track-selection preferences (config.py) — only importable in-process,
# same as DASH_SUPPORTED above. Sane defaults if this deployment doesn't
# have the parent config.py alongside it (standalone deploy).
try:
    from config import HOTSTAR_QUALITY, HOTSTAR_VCODEC, HOTSTAR_ALANG
except ImportError:
    HOTSTAR_QUALITY, HOTSTAR_VCODEC, HOTSTAR_ALANG = "1080", "h264", "hi,en"

# Browser-based MPD/license capture fallback (Akbots/hotstar_browser.py) —
# same in-process-only requirement as DASH support above, plus needs
# Playwright + a Chromium binary actually installed on the host. See that
# module's docstring for what it does and why it exists.
try:
    from Akbots.hotstar_browser import capture_mpd_license, available as _browser_available
    BROWSER_FALLBACK_SUPPORTED = _browser_available()
    if not BROWSER_FALLBACK_SUPPORTED:
        logger.warning("Browser fallback: playwright not installed — /api/resolve_browser will fail at call time.")
except ImportError as e:
    logger.warning(f"Browser fallback unavailable ({e}) — /api/resolve_browser will fail at call time.")
    BROWSER_FALLBACK_SUPPORTED = False

app = FastAPI(title="Hotstar Stream API", version="1.0.0")

DOWNLOAD_DIR = "/tmp/hotstar_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# In-memory job store
jobs: dict[str, dict] = {}

# ─── Hotstar API config ────────────────────────────────────────────────────────

WIDGET_URL = "https://www.hotstar.com/api/internal/bff/v2/pages/666/spaces/334/widgets/244"

CLIENT_CAPABILITIES = json.dumps({
    "ads": ["non_ssai"],
    "audio_channel": ["stereo"],
    "container": ["fmp4", "fmp4br", "ts"],
    "dvr": ["short"],
    "dynamic_range": ["sdr"],
    "encryption": ["plain"],   # plain = no DRM, gives us HLS we can actually use
    "ladder": ["web", "tv", "phone"],
    "package": ["hls"],        # force HLS over DASH
    "resolution": ["sd", "hd", "fhd"],
    "video_codec": ["h264"],
    "video_codec_non_secure": ["h264"]
})

DRM_PARAMETERS = json.dumps({
    "hdcp_version": [],
    "widevine_security_level": [],
    "playready_security_level": []
})

# ─── Pydantic models ───────────────────────────────────────────────────────────

class ResolveRequest(BaseModel):
    content_id: str
    user_token: Optional[str] = None   # x-hs-usertoken JWT — optional if cookies given
    cookies: Optional[dict] = {}

class DownloadRequest(BaseModel):
    m3u8_url: str
    output_name: Optional[str] = "output"
    workers: Optional[int] = 6
    # Set for DASH content resolved via /api/resolve_browser — a
    # browser-captured license URL is often more reliable than the one
    # Akbots.hotstar_widevine regex-scans out of the MPD manifest text
    # (some rollouts don't embed it there at all, or embed a stale one).
    # Ignored entirely for plain HLS downloads.
    license_url: Optional[str] = None

class ResolveBrowserRequest(BaseModel):
    content_id: str
    # The actual navigable hotstar.com watch-page URL — content_id alone
    # isn't something a browser can open. Callers that only have a bare
    # content_id (no URL the user originally pasted) can't use this
    # endpoint; see Akbots/hotstar.py's hotstar_command for how it
    # decides whether to even attempt this fallback.
    page_url: str
    cookies: Optional[dict] = {}


# A JWT is three base64url segments separated by dots (header.payload.sig).
# Hotstar's x-hs-usertoken is a JWT, and on the site it's mirrored into a
# cookie (name varies by rollout: userUP / userToken / identity, etc.), so
# instead of hardcoding a cookie name we scan for anything JWT-shaped.
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

# Prefer these cookie names if present (fast path); fall back to scanning
# every cookie value for JWT shape.
_TOKEN_COOKIE_NAMES = ("userUP", "userToken", "identity", "hotstarauth", "x-hs-usertoken")


def _token_from_cookies(cookies: Optional[dict]) -> Optional[str]:
    if not cookies:
        return None
    for name in _TOKEN_COOKIE_NAMES:
        val = cookies.get(name)
        if val and _JWT_RE.match(val):
            return val
    for val in cookies.values():
        if isinstance(val, str) and _JWT_RE.match(val):
            return val
    return None


_QUALITY_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
    'Origin': 'https://www.hotstar.com',
    'Referer': 'https://www.hotstar.com/',
}


def _list_qualities(master_url: str) -> list:
    """Fetches a master M3U8 playlist and returns its variant streams as
    [{"resolution": "1920x1080", "bandwidth_mbps": 4.2, "url": "..."}, ...],
    sorted highest quality first. Returns [] (never raises) if the URL
    isn't a variant playlist or can't be fetched — resolve() still works
    without quality info in that case, /api/download just auto-picks best."""
    try:
        r = requests.get(master_url, headers=_QUALITY_HEADERS, timeout=15)
        r.raise_for_status()
        playlist = m3u8.loads(r.text, uri=master_url)
        if not playlist.is_variant:
            return []
        variants = sorted(
            playlist.playlists,
            key=lambda p: p.stream_info.bandwidth if p.stream_info else 0,
            reverse=True,
        )
        out = []
        for v in variants:
            info = v.stream_info
            res = f"{info.resolution[0]}x{info.resolution[1]}" if info and info.resolution else "unknown"
            bw = (info.bandwidth / 1_000_000) if info and info.bandwidth else 0
            out.append({
                "resolution": res,
                "bandwidth_mbps": round(bw, 2),
                "url": urljoin(playlist.base_uri or master_url, v.uri),
            })
        return out
    except Exception as e:
        logger.warning(f"_list_qualities: failed for {master_url}: {e}")
        return []

# ─── HLS Downloader (from stream.py, adapted) ─────────────────────────────────

class HLSDownloader:
    def __init__(self, url, output_file, workers=6, job_id=None):
        self.url = url
        self.output_file = output_file
        self.workers = workers
        self.job_id = job_id

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9,en-IN;q=0.8',
            'Cache-Control': 'no-cache',
            'Origin': 'https://www.hotstar.com',
            'Pragma': 'no-cache',
            'Referer': 'https://www.hotstar.com/',
        })

        self.key_cache = {}
        self.segments_data = {}
        self.segments_lock = threading.Lock()
        self.total_segments = 0
        self.downloaded_count = 0
        self.download_lock = threading.Lock()

    def _update_job(self, **kwargs):
        if self.job_id and self.job_id in jobs:
            jobs[self.job_id].update(kwargs)

    def fetch_url(self, url):
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r.content

    def fetch_m3u8(self, url):
        content = self.fetch_url(url)
        return m3u8.loads(content.decode('utf-8'), uri=url)

    def get_media_playlist(self, playlist):
        if playlist.is_variant:
            variants = sorted(
                playlist.playlists,
                key=lambda p: p.stream_info.bandwidth if p.stream_info else 0,
                reverse=True
            )
            if not variants:
                raise ValueError("No variant streams in master playlist")
            best = variants[0]
            variant_url = urljoin(playlist.base_uri or self.url, best.uri)
            bw = best.stream_info.bandwidth if best.stream_info else 0
            res = best.stream_info.resolution if best.stream_info else "unknown"
            self._update_job(selected_quality=f"{res} @ {bw/1_000_000:.2f} Mbps")
            media = self.fetch_m3u8(variant_url)
            return media, variant_url
        return playlist, self.url

    def get_encryption_key(self, key_info, base_url):
        if not key_info or key_info.method == "NONE":
            return None
        if key_info.method != "AES-128":
            raise ValueError(f"Unsupported encryption: {key_info.method}")
        key_uri = urljoin(base_url, key_info.uri)
        if key_uri in self.key_cache:
            return self.key_cache[key_uri]
        key_data = self.fetch_url(key_uri)
        self.key_cache[key_uri] = key_data
        return key_data

    def get_iv(self, key_info, media_sequence):
        if key_info.iv:
            iv_hex = key_info.iv.lstrip("0xX")
            return bytes.fromhex(iv_hex.zfill(32))
        return media_sequence.to_bytes(16, byteorder='big')

    def decrypt_segment(self, data, key, iv):
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(data), AES.block_size)

    def download_segment(self, index, segment, base_url, encryption_key, media_sequence):
        seg_url = urljoin(base_url, segment.uri)
        data = self.fetch_url(seg_url)
        if encryption_key and segment.key and segment.key.method == "AES-128":
            iv = self.get_iv(segment.key, media_sequence + index)
            data = self.decrypt_segment(data, encryption_key, iv)
        with self.download_lock:
            self.downloaded_count += 1
            pct = int(self.downloaded_count / self.total_segments * 100)
            self._update_job(
                downloaded=self.downloaded_count,
                total=self.total_segments,
                progress=pct
            )
        return index, data

    def download(self):
        try:
            self._update_job(status="fetching_playlist")
            playlist = self.fetch_m3u8(self.url)
            media_playlist, base_url = self.get_media_playlist(playlist)
            segments = media_playlist.segments

            if not segments:
                raise ValueError("No segments in playlist")

            self.total_segments = len(segments)
            total_duration = sum(s.duration for s in segments if s.duration)
            self._update_job(
                status="downloading",
                total=self.total_segments,
                duration_seconds=round(total_duration, 1)
            )

            media_sequence = media_playlist.media_sequence or 0
            encryption_key = None
            first = segments[0]
            if first.key and first.key.method == "AES-128":
                encryption_key = self.get_encryption_key(first.key, base_url)
                self._update_job(encrypted=True)

            stop_event = threading.Event()

            with open(self.output_file, 'wb') as out:
                next_write = [0]
                buf = {}
                buf_lock = threading.Lock()

                def writer():
                    while next_write[0] < self.total_segments and not stop_event.is_set():
                        with buf_lock:
                            if next_write[0] in buf:
                                out.write(buf.pop(next_write[0]))
                                out.flush()
                                next_write[0] += 1
                            else:
                                pass
                        time.sleep(0.005)

                wt = threading.Thread(target=writer)
                wt.start()

                try:
                    with ThreadPoolExecutor(max_workers=self.workers) as ex:
                        futures = {
                            ex.submit(self.download_segment, i, seg, base_url, encryption_key, media_sequence): i
                            for i, seg in enumerate(segments)
                        }
                        for future in as_completed(futures):
                            idx, data = future.result()
                            with buf_lock:
                                buf[idx] = data
                finally:
                    wt.join(timeout=60)

            self._update_job(status="done", progress=100)
            return True

        except Exception as e:
            self._update_job(status="error", error=str(e))
            logger.error(f"Download failed: {e}")
            return False
        finally:
            self.session.close()


# ─── Background task runner ───────────────────────────────────────────────────

def run_download(job_id, m3u8_url, output_file, workers):
    dl = HLSDownloader(m3u8_url, output_file, workers=workers, job_id=job_id)
    dl.download()


# ─── DASH/Widevine download + decrypt + mux ────────────────────────────────────
# Wired per Akbots/hotstar_widevine.py's note: extract the content key, pull
# the best video/audio tracks with yt-dlp, decrypt each with the bundled
# mp4decrypt, mux with ffmpeg. Same overall approach as the reference
# downloader.py this was ported from, adapted to this project's job-tracking
# dict and its own mp4decrypt_util wrapper instead of raw shell strings.

_VIDEO_LINE_RE = re.compile(r"^([\w-]+)\s+\S+\s+(\d+)x(\d+).*?video only\b", re.MULTILINE | re.IGNORECASE)
_AUDIO_LINE_RE = re.compile(r"^([\w-]+)\s+.*?audio only\b", re.MULTILINE | re.IGNORECASE)
_CODEC_RE = re.compile(r"\b(avc1|h264|hev1|hvc1|h265|hevc|vp9|av01)\b", re.IGNORECASE)
_H265_ALIASES = ("hev1", "hvc1", "h265", "hevc")
_H264_ALIASES = ("avc1", "h264")

# language code -> alternate spellings that might show up in yt-dlp's
# format-note column (varies by extractor/manifest — cover the common ones).
_LANG_ALIASES = {
    "hi": ("hi", "hin", "hindi"), "en": ("en", "eng", "english"),
    "ta": ("ta", "tam", "tamil"), "te": ("te", "tel", "telugu"),
    "kn": ("kn", "kan", "kannada"), "ml": ("ml", "mal", "malayalam"),
    "bn": ("bn", "ben", "bengali"), "mr": ("mr", "mar", "marathi"),
    "pa": ("pa", "pan", "punjabi"), "gu": ("gu", "guj", "gujarati"),
}


def _select_video_format(fmt_out: str, preferred_vcodec: str = "", preferred_quality: str = "") -> str | None:
    """Picks a video-only format_id from yt-dlp -F output, preferring
    preferred_vcodec (h264/h265) and the closest height to
    preferred_quality (e.g. "1080"). Falls back to the highest-bitrate
    track (yt-dlp lists lowest-to-highest, so the LAST match) if nothing
    parses or no preference is set — same as the previous behavior."""
    matches = list(_VIDEO_LINE_RE.finditer(fmt_out))
    if not matches:
        return None
    try:
        target_height = int(preferred_quality) if preferred_quality else 0
    except ValueError:
        target_height = 0
    pref_codec = (preferred_vcodec or "").strip().lower()

    def score(m):
        fmt_id, h = m.group(1), int(m.group(3))
        line = m.group(0)
        codec_m = _CODEC_RE.search(line)
        codec = codec_m.group(1).lower() if codec_m else ""
        s = 0.0
        if pref_codec in ("h265", "hevc") and codec in _H265_ALIASES:
            s += 1000
        elif pref_codec == "h264" and codec in _H264_ALIASES:
            s += 1000
        if target_height:
            s -= abs(h - target_height)
        else:
            s += h / 100  # no quality preference -> higher wins, matches old "last=best" bias
        return s

    return max(matches, key=score).group(1)


def _select_audio_format(fmt_out: str, preferred_alang: str = "") -> str | None:
    """Picks an audio-only format_id, walking preferred_alang's
    comma-separated language priority list and returning the first track
    whose format line mentions that language. Falls back to the
    first-listed track (old behavior) if nothing matches / no
    preference is set."""
    matches = list(_AUDIO_LINE_RE.finditer(fmt_out))
    if not matches:
        return None
    pref_list = [p.strip().lower() for p in (preferred_alang or "").split(",") if p.strip()]
    for pref in pref_list:
        aliases = _LANG_ALIASES.get(pref, (pref,))
        for m in matches:
            line = m.group(0).lower()
            if any(re.search(rf"\b{re.escape(a)}\b", line) for a in aliases):
                return m.group(1)
    return matches[0].group(1)


def _ytdlp_list_formats(mpd_url: str) -> str:
    r = subprocess.run(
        ["yt-dlp", "--allow-unplayable-formats", "-F", mpd_url],
        capture_output=True, text=True, timeout=60,
    )
    return r.stdout or ""


def _ytdlp_download_track(mpd_url: str, fmt_id: str, out_path: str, timeout: int = 600) -> bool:
    try:
        r = subprocess.run(
            ["yt-dlp", "--allow-unplayable-formats", "-f", fmt_id, "--output", out_path, mpd_url],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0 or not os.path.exists(out_path):
            logger.error(f"yt-dlp track download failed: {(r.stderr or '')[-500:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"yt-dlp track download timed out ({fmt_id})")
        return False


def _mp4decrypt_sync(input_path: str, output_path: str, keys: list) -> tuple:
    # decrypt_mp4 is async (asyncio.create_subprocess_exec) — this function
    # runs inside a BackgroundTasks worker thread, which has no event loop
    # of its own, so asyncio.run() here is safe and won't collide with the
    # bot's own main loop.
    from Akbots.mp4decrypt_util import decrypt_mp4
    return asyncio.run(decrypt_mp4(input_path, output_path, keys))


def run_dash_download(job_id: str, mpd_url: str, output_file: str, license_url: str = None):
    def _update(**kw):
        if job_id in jobs:
            jobs[job_id].update(kw)

    paths = {"venc": None, "aenc": None, "vdec": None, "adec": None}
    try:
        if not DASH_SUPPORTED:
            _update(status="error", error=(
                "This stream is DASH/Widevine-protected, but DASH support isn't "
                "available on this deployment (mp4decrypt binary or Akbots package "
                "not found). Run this service in-process with the rest of the bot."
            ))
            return

        _update(status="extracting_key", progress=5)
        key = extract_key_sync(mpd_url, license_url)
        if not key or not valid_key(key):
            _update(status="error", error=f"Couldn't obtain a valid decryption key (got: {key!r}).")
            return

        _update(status="downloading", progress=15)
        fmt_out = _ytdlp_list_formats(mpd_url)
        video_fmt = _select_video_format(fmt_out, preferred_vcodec=HOTSTAR_VCODEC, preferred_quality=HOTSTAR_QUALITY)
        audio_fmt = _select_audio_format(fmt_out, preferred_alang=HOTSTAR_ALANG)
        if not video_fmt:
            _update(status="error", error="yt-dlp found no downloadable video track in this MPD manifest.")
            return

        paths["venc"] = os.path.join(DOWNLOAD_DIR, f"{job_id}_venc.mp4")
        if not _ytdlp_download_track(mpd_url, video_fmt, paths["venc"]):
            _update(status="error", error="Video track download failed.")
            return
        _update(progress=45)

        has_audio = False
        if audio_fmt:
            paths["aenc"] = os.path.join(DOWNLOAD_DIR, f"{job_id}_aenc.mp4")
            has_audio = _ytdlp_download_track(mpd_url, audio_fmt, paths["aenc"])
        _update(progress=60, status="decrypting")

        paths["vdec"] = os.path.join(DOWNLOAD_DIR, f"{job_id}_vdec.mp4")
        ok, err = _mp4decrypt_sync(paths["venc"], paths["vdec"], [key])
        if not ok:
            _update(status="error", error=f"Video decryption failed: {err}")
            return
        _update(progress=78)

        if has_audio:
            paths["adec"] = os.path.join(DOWNLOAD_DIR, f"{job_id}_adec.mp4")
            ok, err = _mp4decrypt_sync(paths["aenc"], paths["adec"], [key])
            if not ok:
                logger.warning(f"Audio decryption failed ({err}) — falling back to video-only.")
                has_audio = False
        _update(progress=88, status="muxing")

        if has_audio and os.path.exists(paths["adec"]):
            mux_cmd = ["ffmpeg", "-y", "-i", paths["vdec"], "-i", paths["adec"], "-c", "copy", output_file]
        else:
            mux_cmd = ["ffmpeg", "-y", "-i", paths["vdec"], "-c", "copy", output_file]
        r = subprocess.run(mux_cmd, capture_output=True, timeout=300)
        if r.returncode != 0 or not os.path.exists(output_file):
            logger.warning(f"ffmpeg mux failed ({(r.stderr or b'').decode(errors='replace')[-300:]}) — using video-only file as-is.")
            shutil.copy(paths["vdec"], output_file)

        if not os.path.exists(output_file):
            _update(status="error", error="Muxing failed and no fallback file was produced.")
            return

        _update(status="done", progress=100)
    except Exception as e:
        logger.error(f"DASH download failed: {e}")
        _update(status="error", error=str(e))
    finally:
        for p in paths.values():
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/api/resolve")
async def resolve(req: ResolveRequest):
    """
    Call Hotstar's widget 244 API with a content_id and either an explicit
    user_token OR cookies (the JWT is auto-extracted from the cookie jar
    if no token is passed directly).
    """
    token = req.user_token or _token_from_cookies(req.cookies)
    if not token:
        raise HTTPException(
            status_code=400,
            detail="No usable token: pass user_token, or cookies containing a JWT-shaped value.",
        )

    headers = {
        'authority': 'www.hotstar.com',
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'eng',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36',
        'x-country-code': 'in',
        'x-hs-accept-language': 'eng',
        'x-hs-app': '260306000',
        'x-hs-platform': 'web',
        'x-hs-client': 'platform:web;app_version:26.03.06.0;browser:Chrome;schema_version:0.0.1690;os:Linux;os_version:x86_64;browser_version:111;network_data:3g',
        'x-hs-usertoken': token,
        'referer': 'https://www.hotstar.com/in',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
    }

    params = {
        'content_id': req.content_id,
        'client_capabilities': CLIENT_CAPABILITIES,
        'drm_parameters': DRM_PARAMETERS,
    }

    try:
        r = requests.get(
            WIDGET_URL,
            params=params,
            headers=headers,
            cookies=req.cookies,
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Hotstar API error: {e.response.status_code} {e.response.text[:300]}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Navigate the response tree
    try:
        media_asset = data['success']['widget_wrapper']['widget']['data']['media_asset']
        primary = media_asset.get('primary', {})
        content_url = primary.get('content_url', '')
        content_type = primary.get('content_type', '')

        qualities = []
        is_dash = bool(content_url) and content_url.split("?")[0].endswith(".mpd")
        if content_url and content_url.split("?")[0].endswith(".m3u8"):
            qualities = _list_qualities(content_url)

        # Also grab any alternate streams if present
        alternates = []
        for key, val in media_asset.items():
            if key != 'primary' and isinstance(val, dict) and 'content_url' in val:
                alternates.append({
                    'type': key,
                    'url': val['content_url'],
                    'content_type': val.get('content_type', '')
                })

        return {
            "content_id": req.content_id,
            "stream_url": content_url,
            "content_type": content_type,
            "alternates": alternates,
            "qualities": qualities,
            "is_dash": is_dash,
            "dash_supported": DASH_SUPPORTED,
            "raw_media_asset": media_asset
        }
    except KeyError as e:
        return JSONResponse(
            status_code=422,
            content={"error": f"Unexpected response structure, missing key: {e}", "raw": data}
        )


@app.post("/api/resolve_browser")
async def resolve_browser(req: ResolveBrowserRequest):
    """
    Fallback for when /api/resolve fails outright (widget API rejects the
    token/cookies/signature) but the content might still be reachable
    through a real logged-in browser session. Opens req.page_url in
    Chromium (Akbots/hotstar_browser.py), captures the MPD manifest URL +
    Widevine license URL straight off the network, and returns them in
    the same shape /api/resolve uses so Akbots/hotstar.py's client code
    doesn't need a separate response format to handle.

    Always DASH (a browser only needs to intercept anything here for
    Widevine-protected content — plain HLS would have resolved via the
    normal /api/resolve widget-API path already), so no quality list;
    /api/download picks the best available track itself.
    """
    if not BROWSER_FALLBACK_SUPPORTED:
        raise HTTPException(
            status_code=400,
            detail="Browser fallback isn't available on this deployment "
                   "(playwright not installed, or its Chromium binary isn't set up — "
                   "run `playwright install --with-deps chromium`).",
        )

    try:
        result = await capture_mpd_license(req.page_url, cookies=req.cookies)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Browser capture failed: {e}")

    if not result or not result.get("mpd_url"):
        raise HTTPException(
            status_code=404,
            detail="Browser fallback didn't capture an MPD URL — the page may not have "
                   "played (login required, region-locked, or the content genuinely "
                   "isn't available).",
        )

    return {
        "content_id": req.content_id,
        "stream_url": result["mpd_url"],
        "content_type": result.get("title") or "",
        "alternates": [],
        "qualities": [],
        "is_dash": True,
        "dash_supported": DASH_SUPPORTED,
        "license_url": result.get("license_url"),
        "raw_media_asset": None,
    }


@app.post("/api/download")
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    """
    Queue a download job — HLS (.m3u8) goes through HLSDownloader to a .ts
    file; DASH/Widevine (.mpd) goes through the extract-key/download/decrypt/
    mux pipeline to a .mp4 file. Returns job_id immediately either way.
    """
    job_id = str(uuid.uuid4())[:8]
    is_dash = req.m3u8_url.split("?")[0].endswith(".mpd")
    ext = "mp4" if is_dash else "ts"
    output_file = os.path.join(DOWNLOAD_DIR, f"{job_id}_{req.output_name}.{ext}")

    jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "m3u8_url": req.m3u8_url,
        "output_file": output_file,
        "is_dash": is_dash,
        "progress": 0,
        "downloaded": 0,
        "total": 0,
        "created_at": time.time()
    }

    if is_dash:
        if not DASH_SUPPORTED:
            jobs.pop(job_id, None)
            raise HTTPException(
                status_code=400,
                detail="This stream is DASH/Widevine-protected, but DASH support isn't "
                       "available on this deployment. Run this service in-process with "
                       "the rest of the bot (the normal setup), not standalone.",
            )
        background_tasks.add_task(run_dash_download, job_id, req.m3u8_url, output_file, req.license_url)
    else:
        background_tasks.add_task(run_download, job_id, req.m3u8_url, output_file, req.workers)

    return {"job_id": job_id, "status": "queued", "is_dash": is_dash}


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = dict(jobs[job_id])
    job.pop("output_file", None)  # don't expose server paths
    return job


@app.get("/api/file/{job_id}")
async def download_file(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Job not ready. Status: {job['status']}")
    fp = job["output_file"]
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="File missing on disk")
    filename = os.path.basename(fp)
    media_type = "video/mp4" if fp.endswith(".mp4") else "video/mp2t"
    return FileResponse(fp, media_type=media_type, filename=filename)


@app.get("/api/jobs")
async def list_jobs():
    result = []
    for jid, j in jobs.items():
        entry = dict(j)
        entry.pop("output_file", None)
        result.append(entry)
    return sorted(result, key=lambda x: x["created_at"], reverse=True)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs.pop(job_id)
    fp = job.get("output_file")
    if fp and os.path.exists(fp):
        os.remove(fp)
    return {"deleted": job_id}


# ─── Web UI ───────────────────────────────────────────────────────────────────

_UI_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")


@app.get("/", response_class=HTMLResponse)
async def ui():
    # Prefers the file next to this module (works whether main.py is run
    # standalone, in the Dockerfile's /app, or imported in-process by
    # Akbots/hotstar_local_server.py from the bot's own working directory)
    # and falls back to the old fixed Docker path for compatibility.
    for path in (_UI_HTML_PATH, "/app/ui.html"):
        if os.path.exists(path):
            return open(path).read()
    return HTMLResponse("""
<html><body><h2>Hotstar Stream API</h2>
<p>Use POST /api/resolve to get stream URL, POST /api/download to queue download.</p>
<p><a href="/docs">Swagger Docs →</a></p>
</body></html>""")
