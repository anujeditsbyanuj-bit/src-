# M3U8 Batch Downloader — Multi-URL + Queue + Cancel
#
# Ported from a standalone python-telegram-bot script (m3u8_fix.py) into this
# bot's Pyrogram plugin style, on top of what Akbots/mxplayer.py already does
# for MX Player's single-link m3u8 flow. This plugin adds the parts that were
# still missing here:
#
#   - /m3u8 url1 url2 url3 ...  — one command, many links, one quality/audio
#     choice applied to the whole batch (instead of one link at a time).
#   - A real queue: MAX_CONCURRENT users download at once, everyone else
#     waits in line with a visible position and a "your turn!" ping when a
#     slot frees up. /allcancel drops a user's active + queued work.
#   - Segment-level parallel download (ThreadPoolExecutor, SEGMENT_WORKERS
#     threads per stream) using the `m3u8` + `requests` libraries directly,
#     instead of handing the whole stream to yt-dlp — mirrors how the
#     original script fetched .ts/.m4s segments itself.
#   - Audio track buttons get a country-flag prefix (LANG_FLAGS) instead of
#     just the bare language/name string.
#   - A 60-minute per-task ceiling; a stuck download auto-cancels and the
#     batch moves to the next URL instead of hanging forever.
#
# Upload, splitting, thumbnailing and usage-stat accounting are NOT
# reimplemented here — Akbots/direct_utils.upload_file() already does all
# of that for every other plugin in this bot, so the downloader's only job
# is to hand it a finished .mp4.

import os
import re
import time
import uuid
import shutil
import asyncio
from urllib.parse import urljoin, urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.direct_utils import (
    upload_file, fmt_bytes, fmt_duration, draw_bar, make_output_folder,
    safe_filename, get_video_metadata, run_subprocess_with_progress,
    make_ffmpeg_progress_parser, E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_BOLT, E_CLOCK,
)
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    import m3u8 as m3u8_lib
except ImportError:
    m3u8_lib = None

# ------------------------------------------------------------------ config
MAX_CONCURRENT   = int(os.environ.get("M3U8_MAX_WORKERS", 15))
MAX_QUEUE        = int(os.environ.get("M3U8_MAX_QUEUE", 100))
SEGMENT_WORKERS  = int(os.environ.get("M3U8_SEGMENT_WORKERS", 20))
TASK_TIMEOUT     = int(os.environ.get("M3U8_TASK_TIMEOUT", 3600))  # 60 min per URL
MUX_TIMEOUT      = int(os.environ.get("M3U8_MUX_TIMEOUT", 900))    # 15 min ffmpeg safety cap
# Optional post-mux H.265/HEVC re-encode (smaller files, much slower). Off by
# default per batch unless the user opts in via the "Compress?" step —
# capped by its own timeout so a slow host/huge file can't hang forever the
# way an unbounded ffmpeg re-encode would.
COMPRESS_TIMEOUT = int(os.environ.get("M3U8_COMPRESS_TIMEOUT", 1800))  # 30 min HEVC encode safety cap

DOWNLOAD_DIR = make_output_folder("m3u8")
_BATCH_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="m3u8batch")

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

LANG_FLAGS = {
    'hi': '🇮🇳 Hindi', 'hin': '🇮🇳 Hindi', 'hindi': '🇮🇳 Hindi',
    'ja': '🇯🇵 Japanese', 'jpn': '🇯🇵 Japanese', 'japanese': '🇯🇵 Japanese',
    'en': '🇺🇸 English', 'eng': '🇺🇸 English', 'english': '🇺🇸 English',
    'ko': '🇰🇷 Korean', 'kor': '🇰🇷 Korean',
    'ta': '🇮🇳 Tamil', 'tam': '🇮🇳 Tamil',
    'te': '🇮🇳 Telugu', 'tel': '🇮🇳 Telugu',
    'bn': '🇮🇳 Bengali', 'ben': '🇮🇳 Bengali',
    'mr': '🇮🇳 Marathi', 'mar': '🇮🇳 Marathi',
    'ml': '🇮🇳 Malayalam', 'mal': '🇮🇳 Malayalam',
    'kn': '🇮🇳 Kannada', 'kan': '🇮🇳 Kannada',
    'gu': '🇮🇳 Gujarati', 'guj': '🇮🇳 Gujarati',
    'pa': '🇮🇳 Punjabi', 'pan': '🇮🇳 Punjabi',
    'ur': '🇵🇰 Urdu', 'urd': '🇵🇰 Urdu',
    'zh': '🇨🇳 Chinese', 'fr': '🇫🇷 French', 'de': '🇩🇪 German',
    'es': '🇪🇸 Spanish', 'pt': '🇵🇹 Portuguese', 'ru': '🇷🇺 Russian',
    'ar': '🇸🇦 Arabic', 'it': '🇮🇹 Italian', 'th': '🇹🇭 Thai',
}


def lang_display(language, name):
    if language:
        k = language.lower().strip()
        if k in LANG_FLAGS:
            return LANG_FLAGS[k]
    if name:
        n = name.lower().strip()
        if n in LANG_FLAGS:
            return LANG_FLAGS[n]
        for key, val in LANG_FLAGS.items():
            if key in n:
                return val
        return f"🔊 {name}"
    return f"🔊 {language}" if language else "🔊 Unknown"


def is_hindi_track(t):
    l = (t.get("language") or "").lower()
    n = (t.get("name") or "").lower()
    return l in ("hi", "hin", "hindi") or "hindi" in n


# -------------------------------------------------------------- cancel/queue
_cancel_flags = {}  # chat_id -> 'none' | 'current' | 'all'


def is_cancelled(cid):
    return _cancel_flags.get(cid, "none") != "none"


def is_all_cancelled(cid):
    return _cancel_flags.get(cid, "none") == "all"


class TaskQueue:
    """One active batch per user; MAX_CONCURRENT batches across all users
    at once; everyone else waits in a FIFO line up to MAX_QUEUE deep."""

    def __init__(self, max_concurrent, max_queue):
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self.active = {}
        self.waiting = deque()
        self.lock = asyncio.Lock()

    async def add_task(self, cid, task_info):
        async with self.lock:
            if cid in self.active or any(c == cid for c, _ in self.waiting):
                return False, f"{E_CROSS} You already have a download running. Use /allcancel first."
            if len(self.waiting) >= self.max_queue:
                return False, f"{E_CROSS} Queue is full ({self.max_queue}). Try again later."
            if len(self.active) < self.max_concurrent:
                self.active[cid] = task_info
                return True, f"{E_ROCKET} Starting now. (Active: {len(self.active)}/{self.max_concurrent})"
            self.waiting.append((cid, task_info))
            return True, f"{E_CLOCK} Queued — position {len(self.waiting)}."

    async def complete_task(self, cid):
        async with self.lock:
            self.active.pop(cid, None)
            if self.waiting and len(self.active) < self.max_concurrent:
                next_cid, next_task = self.waiting.popleft()
                self.active[next_cid] = next_task
                return next_cid, next_task
            return None, None

    async def get_position(self, cid):
        async with self.lock:
            if cid in self.active:
                return 0
            for i, (c, _) in enumerate(self.waiting):
                if c == cid:
                    return i + 1
            return -1

    async def remove_all(self, cid):
        async with self.lock:
            self.active.pop(cid, None)
            self.waiting = deque((c, t) for c, t in self.waiting if c != cid)


task_queue = TaskQueue(MAX_CONCURRENT, MAX_QUEUE)

# session_id -> selection state while user is picking quality/audio
_SESSIONS = {}


# ------------------------------------------------------------------ parsing
def extract_urls(text: str):
    urls = []
    for token in URL_RE.findall(text or ""):
        if token not in urls:
            urls.append(token)
    return urls


def get_video_name(url, idx=0, total=1):
    try:
        parts = unquote(urlparse(url).path).strip("/").split("/")
        skip = {"hls", "video", "stream", "media", "content", "master",
                "index", "playlist", "manifest", "0", "1", "2", "3"}
        for part in reversed(parts):
            name = part.replace(".m3u8", "").replace(".m3u", "")
            if name and len(name) > 3 and name.lower() not in skip:
                return safe_filename(name, f"Video_{idx + 1}")[:40]
    except Exception:
        pass
    return f"Video_{idx + 1}" if total > 1 else "video"


def _parse_playlist_sync(url):
    playlist = m3u8_lib.load(url)
    if not playlist.is_variant:
        return {"qualities": [{"name": "default", "resolution": "Unknown", "bandwidth": 0,
                                "url": url, "height": 0, "audio_url": None}],
                "audio_tracks": []}

    audio_tracks, seen = [], set()
    if playlist.media:
        for media in playlist.media:
            if media.type == "AUDIO" and media.uri:
                au = urljoin(url, media.uri)
                if au in seen:
                    continue
                seen.add(au)
                lang = getattr(media, "language", None) or ""
                name = getattr(media, "name", None) or ""
                audio_tracks.append({
                    "language": lang, "name": name, "url": au,
                    "display": lang_display(lang, name),
                })

    qualities = []
    for p in playlist.playlists:
        res = p.stream_info.resolution
        bw = p.stream_info.bandwidth
        if res:
            qualities.append({
                "name": f"{res[1]}p", "resolution": f"{res[0]}x{res[1]}",
                "bandwidth": bw or 0, "url": urljoin(url, p.uri),
                "height": res[1], "audio_url": None,
            })
    qualities.sort(key=lambda x: x["height"])
    return {"qualities": qualities, "audio_tracks": audio_tracks}


async def parse_m3u8(url):
    if m3u8_lib is None:
        raise RuntimeError("The `m3u8` package isn't installed on this host (pip install m3u8).")
    return await asyncio.to_thread(_parse_playlist_sync, url)


def find_quality(qualities, target_height):
    if not qualities:
        return None
    if target_height is None:
        return qualities[-1]
    for q in qualities:
        if q["height"] == target_height:
            return q
    return min(qualities, key=lambda q: abs(q["height"] - target_height))


def find_audio(tracks, lang, name):
    if not tracks:
        return None
    if not lang and not name:
        return tracks[0]
    for t in tracks:
        tl, tn = (t.get("language") or "").lower(), (t.get("name") or "").lower()
        if lang and lang.lower() in (tl, tn):
            return t
        if name and name.lower() == tn:
            return t
    return tracks[0]


# --------------------------------------------------------------- downloader
class DownloadCancelled(Exception):
    pass


class DownloadTimeout(Exception):
    pass


class Downloader:
    """Fetches every segment of one quality (+ optional separate audio
    playlist) with a thread pool, concatenates them, then muxes with ffmpeg.
    Runs entirely synchronously — the caller wraps it in a thread."""

    def __init__(self, cid, video_url, audio_url, qname, vname, progress_cb):
        self.cid = cid
        self.video_url = video_url
        self.audio_url = audio_url
        self.qname = qname
        self.vname = vname
        self.progress_cb = progress_cb  # thread-safe: def cb(text)

        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=30, pool_maxsize=30, max_retries=3)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

        self.tmp_dir = os.path.join(DOWNLOAD_DIR, f"tmp_{cid}_{uuid.uuid4().hex[:8]}")
        self.v_dir = os.path.join(self.tmp_dir, "v")
        self.a_dir = os.path.join(self.tmp_dir, "a")
        os.makedirs(self.v_dir, exist_ok=True)
        self.out_path = os.path.join(DOWNLOAD_DIR, f"{safe_filename(vname, 'video')}_{qname}_{uuid.uuid4().hex[:6]}.mp4")

    def _load_segments(self, url, seg_dir):
        pl = m3u8_lib.load(url)
        base = url.rsplit("/", 1)[0] + "/"
        has_init = False
        if pl.segment_map:
            for sm in pl.segment_map:
                if sm.uri:
                    iu = sm.uri if sm.uri.startswith("http") else urljoin(base, sm.uri)
                    r = self.session.get(iu, timeout=30)
                    with open(os.path.join(seg_dir, "init.mp4"), "wb") as f:
                        f.write(r.content)
                    has_init = True
                    break
        segs = [sg.uri if sg.uri.startswith("http") else urljoin(base, sg.uri) for sg in pl.segments]
        return segs, has_init

    def _download_segments(self, segs, seg_dir, label, t0):
        total, done, total_bytes = len(segs), 0, 0
        failed = []
        last_update = 0.0

        def fetch_one(args):
            i, url = args
            path = os.path.join(seg_dir, f"{i:05d}.seg")
            for _ in range(3):
                try:
                    r = self.session.get(url, timeout=30)
                    if r.status_code == 200:
                        with open(path, "wb") as f:
                            f.write(r.content)
                        return i, True, len(r.content)
                except Exception:
                    time.sleep(0.5)
            return i, False, 0

        with ThreadPoolExecutor(max_workers=SEGMENT_WORKERS) as ex:
            futures = {ex.submit(fetch_one, (i, u)): i for i, u in enumerate(segs)}
            for fut in as_completed(futures):
                if is_cancelled(self.cid):
                    ex.shutdown(wait=False, cancel_futures=True)
                    raise DownloadCancelled()
                if time.time() - t0 > TASK_TIMEOUT:
                    ex.shutdown(wait=False, cancel_futures=True)
                    raise DownloadTimeout()

                i, ok, size = fut.result()
                done += 1
                if ok:
                    total_bytes += size
                else:
                    failed.append(i)

                now = time.time()
                if now - last_update >= 2 or done == total:
                    elapsed = now - t0
                    speed = total_bytes / elapsed if elapsed > 0 else 0
                    pct = (done / total) * 100 if total else 100
                    eta = ((total - done) / done) * elapsed if done > 0 else 0
                    remaining = max(0, TASK_TIMEOUT - elapsed)
                    try:
                        self.progress_cb(
                            f"<b>{E_BOLT} {label} [{self.qname}]</b>\n"
                            f"<code>{draw_bar(pct)}</code> {pct:.1f}%\n\n"
                            f"{done}/{total} segments · {fmt_bytes(total_bytes)}\n"
                            f"{fmt_bytes(int(speed))}/s · ETA {fmt_duration(eta)}\n"
                            f"{E_CLOCK} Timeout in {fmt_duration(remaining)}"
                        )
                    except Exception:
                        pass  # e.g. event loop already closed during shutdown
                    last_update = now

        for i in failed:
            fetch_one((i, segs[i]))

    def _concat(self, seg_dir, count, has_init, out_path):
        with open(out_path, "wb") as of:
            if has_init:
                init_path = os.path.join(seg_dir, "init.mp4")
                if os.path.exists(init_path):
                    with open(init_path, "rb") as f:
                        of.write(f.read())
            for i in range(count):
                seg_path = os.path.join(seg_dir, f"{i:05d}.seg")
                if os.path.exists(seg_path):
                    with open(seg_path, "rb") as f:
                        of.write(f.read())

    def prepare(self):
        """Sync: fetch every segment and concat them into raw v.mp4 / a.mp4
        (no re-encode yet). Runs inside the batch executor thread. The
        actual mux happens afterwards, back on the event loop, so it can
        show a real ffmpeg progress bar via run_subprocess_with_progress."""
        t0 = time.time()
        video_segs, v_has_init = self._load_segments(self.video_url, self.v_dir)
        self._download_segments(video_segs, self.v_dir, "📹 Video", t0)

        audio_segs, a_has_init = [], False
        if self.audio_url:
            os.makedirs(self.a_dir, exist_ok=True)
            try:
                audio_segs, a_has_init = self._load_segments(self.audio_url, self.a_dir)
                self._download_segments(audio_segs, self.a_dir, "🔊 Audio", t0)
            except (DownloadCancelled, DownloadTimeout):
                raise
            except Exception:
                audio_segs = []

        v_concat = os.path.join(self.tmp_dir, "v.mp4")
        self._concat(self.v_dir, len(video_segs), v_has_init, v_concat)

        a_concat = None
        if audio_segs:
            a_concat = os.path.join(self.tmp_dir, "a.mp4")
            self._concat(self.a_dir, len(audio_segs), a_has_init, a_concat)

        return v_concat, a_concat

    def cleanup(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


async def mux_with_progress(v_concat, a_concat, out_path, qname, status, user_id: int = None):
    """Muxes video + (optional) separate audio into one mp4 with a live
    ffmpeg progress bar, falling back to a plain video-only copy if the
    audio-video mux fails, and to a raw file copy as a last resort."""
    duration, _, _ = await asyncio.to_thread(get_video_metadata, v_concat)
    title = f"Muxing [{qname}]"
    parse_line = make_ffmpeg_progress_parser(duration or 0, title=title)

    if a_concat and os.path.exists(a_concat) and os.path.getsize(a_concat) > 500:
        for acodec in ("aac", "copy"):
            cmd = ["ffmpeg", "-y", "-i", v_concat, "-i", a_concat,
                   "-c:v", "copy", "-c:a", acodec, "-map", "0:v:0", "-map", "1:a:0",
                   "-movflags", "+faststart", "-shortest", out_path]
            try:
                await asyncio.wait_for(
                    run_subprocess_with_progress(
                        cmd, status, title, parse_line,
                        user_id=user_id, queue_label="M3U8 mux",
                    ),
                    timeout=MUX_TIMEOUT,
                )
            except asyncio.TimeoutError:
                continue  # try the next codec / fall through to video-only below
            if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
                return out_path

    cmd = ["ffmpeg", "-y", "-i", v_concat, "-c", "copy", "-map", "0",
           "-movflags", "+faststart", out_path]
    try:
        await asyncio.wait_for(
            run_subprocess_with_progress(
                cmd, status, title, parse_line,
                user_id=user_id, queue_label="M3U8 mux",
            ),
            timeout=MUX_TIMEOUT,
        )
    except asyncio.TimeoutError:
        pass
    if not (os.path.exists(out_path) and os.path.getsize(out_path) > 10000):
        shutil.copy(v_concat, out_path)
    return out_path


async def compress_to_hevc(in_path: str, status, user_id: int = None) -> str:
    """Optional post-mux re-encode to H.265/HEVC for a smaller file, at the
    cost of a lot more CPU time than the copy-mux above.

    Ported from a standalone bot that always forced this step on with no
    timeout and a very slow preset — here it's opt-in per batch, uses a
    much more reasonable preset/CRF trade-off, and is capped by
    COMPRESS_TIMEOUT so a slow host or a long video can't hang the whole
    batch forever. On timeout or failure, raises so the caller can fall
    back to uploading the original (already-muxed) file instead of losing
    the download entirely.
    """
    duration, _, _ = await asyncio.to_thread(get_video_metadata, in_path)
    title = "Compressing (H.265)"
    parse_line = make_ffmpeg_progress_parser(duration or 0, title=title)

    base, _ = os.path.splitext(in_path)
    out_path = f"{base}_hevc.mp4"

    # "medium" preset + CRF 23 is a deliberately gentler trade-off than the
    # "slow"/CRF 18 the original script always used — that combination can
    # take longer than the video's own runtime to encode, which is exactly
    # what COMPRESS_TIMEOUT below exists to cut off if it runs away anyway.
    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-c:v", "libx265", "-preset", "medium", "-crf", "23",
        "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]

    try:
        await asyncio.wait_for(
            run_subprocess_with_progress(
                cmd, status, title, parse_line,
                user_id=user_id, queue_label="M3U8 HEVC compress",
            ),
            timeout=COMPRESS_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise TimeoutError(f"HEVC compression timed out after {COMPRESS_TIMEOUT // 60} min.")

    if not (os.path.exists(out_path) and os.path.getsize(out_path) > 10000):
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise RuntimeError("HEVC compression failed — ffmpeg produced no usable output.")

    return out_path


# --------------------------------------------------------------- UI helpers
def _quality_kb(session_id, qualities):
    rows = []
    if len(qualities) > 1:
        rows.append([make_button(f"📥 ALL QUALITIES ({len(qualities)})", callback_data=f"m3qall:{session_id}", style=_BS.PRIMARY if _BS else None)])
    for i, q in enumerate(qualities):
        label = f"📺 {q['name']} • {q['resolution']} • {q['bandwidth'] // 1000}kbps" if q["name"] != "default" else "📺 Default"
        rows.append([make_button(label, callback_data=f"m3q:{session_id}:{i}", style=_BS.PRIMARY if _BS else None)])
    rows.append([make_button("❌ Cancel", callback_data=f"m3cancel:{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


def _audio_kb(session_id, tracks):
    rows = []
    for i, t in enumerate(tracks):
        prefix = "⭐" if is_hindi_track(t) else "🔊"
        label = f"{prefix} {t['display']}" + (f" ({t['name']})" if t.get("name") else "")
        rows.append([make_button(label, callback_data=f"m3a:{session_id}:{i}", style=_BS.PRIMARY if _BS else None)])
    rows.append([make_button("❌ Cancel", callback_data=f"m3cancel:{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


def _compress_kb(session_id):
    return InlineKeyboardMarkup([
        [make_button("🗜 Compress (H.265, smaller/slower)", callback_data=f"m3c:{session_id}:yes", style=_BS.PRIMARY if _BS else None)],
        [make_button("⚡ Skip — original quality/speed", callback_data=f"m3c:{session_id}:no", style=_BS.PRIMARY if _BS else None)],
        [make_button("❌ Cancel", callback_data=f"m3cancel:{session_id}", style=_BS.DANGER if _BS else None)],
    ])


def _batch_kb(multi: bool):
    if multi:
        return InlineKeyboardMarkup([[
            make_button("⏭ Skip", callback_data="m3skip", style=_BS.PRIMARY if _BS else None),
            make_button("❌ Cancel All", callback_data="m3cancelall", style=_BS.DANGER if _BS else None),
        ]])
    return InlineKeyboardMarkup([[make_button("❌ Cancel", callback_data="m3skip", style=_BS.DANGER if _BS else None)]])


# ------------------------------------------------------------------ commands

# Bare .m3u8 link(s) pasted with no /m3u8 command — same pattern as the
# other auto-detect handlers (mega.py, gdrive.py, terabox.py, ytdl.py's
# MPD_PATTERN, ...). Deliberately matches only the .m3u8 extension itself
# (not a bare "https://..." like m3u8_cmd's own URL_RE does for its
# space-separated batch args), so this can't misfire on unrelated links —
# anything else still falls through to ytdl.py/gallery.py's generic
# fallbacks further down the group chain.
M3U8_AUTO_PATTERN = re.compile(r"https?://\S+\.m3u8(?:\?\S*)?", re.IGNORECASE)


async def _handle_m3u8_urls(client: Client, message: Message, urls: list):
    """Shared entry point for both /m3u8 <url(s)> and bare-link auto-detect
    — analyzes the first URL and shows the quality picker for the batch."""
    status = await message.reply_text(
        f"<b>{E_INFO} Analyzing {len(urls)} URL(s)...</b>", parse_mode=enums.ParseMode.HTML
    )
    try:
        result = await parse_m3u8(urls[0])
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Failed to parse:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    qualities = result["qualities"]
    if not qualities:
        return await status.edit_text(f"<b>{E_CROSS} No playable qualities found.</b>", parse_mode=enums.ParseMode.HTML)

    session_id = uuid.uuid4().hex[:10]
    _SESSIONS[session_id] = {
        "urls": urls, "qualities": qualities, "audio_tracks": result["audio_tracks"],
        "is_all": False, "sel_height": None, "sel_lang": "", "sel_name": "", "compress": False,
        "chat_id": message.chat.id, "message": message,
    }

    audio_tracks = result["audio_tracks"]
    if len(audio_tracks) > 1:
        audio_info = f"🔊 <b>{len(audio_tracks)} audio tracks</b> — choose after quality.\n\n"
    elif audio_tracks:
        audio_info = f"🔊 Audio: {audio_tracks[0]['display']}\n\n"
    else:
        audio_info = "🔊 Audio: muxed with video\n\n"

    url_lines = ""
    if len(urls) > 1:
        url_lines = "\n".join(f"  {i + 1}. <code>{u[:55]}</code>" for i, u in enumerate(urls[:10]))
        if len(urls) > 10:
            url_lines += f"\n  ... +{len(urls) - 10} more"
        url_lines += "\n\n"

    await status.edit_text(
        f"<b>{E_ROCKET} Step 1: Select quality</b>\n\n"
        f"📦 {len(urls)} URL(s)\n\n{url_lines}{audio_info}"
        f"<i>Applies to the whole batch.</i>",
        reply_markup=_quality_kb(session_id, qualities),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("m3u8") & filters.private)
async def m3u8_cmd(client: Client, message: Message):
    raw = message.text.split(None, 1)
    urls = extract_urls(raw[1]) if len(raw) > 1 else []
    if not urls:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b>\n"
            f"<code>/m3u8 &lt;url&gt;</code>\n"
            f"<code>/m3u8 url1 url2 url3</code> (batch)",
            parse_mode=enums.ParseMode.HTML,
        )
    await _handle_m3u8_urls(client, message, urls)


@Client.on_message(
    filters.text & filters.private & filters.regex(M3U8_AUTO_PATTERN) & ~filters.regex(r"^/"),
    group=1,
)
async def m3u8_auto_detect(client: Client, message: Message):
    urls = extract_urls(message.text)
    # Auto-detect stays scoped to genuine .m3u8 links only — filter out any
    # other bare URL that might be in the same message (extract_urls() is
    # generic http(s), but the trigger regex already guarantees at least
    # one .m3u8 link is present).
    urls = [u for u in urls if M3U8_AUTO_PATTERN.match(u)] or urls
    if urls:
        await _handle_m3u8_urls(client, message, urls)


async def cancel_all(chat_id: int) -> bool:
    """Cancels every active + queued M3U8 task for a chat. Returns True if
    there was anything to cancel. Exposed so the global /cancel command in
    start.py can stop M3U8 batches too, not just this module's own
    /allcancel and m3cancelall button."""
    had_something = (
        chat_id in task_queue.active
        or any(c == chat_id for c, _ in task_queue.waiting)
    )
    _cancel_flags[chat_id] = "all"
    await task_queue.remove_all(chat_id)
    return had_something


@Client.on_message(filters.command("allcancel") & filters.private)
async def allcancel_cmd(client: Client, message: Message):
    cid = message.chat.id
    _cancel_flags[cid] = "all"
    await task_queue.remove_all(cid)
    await message.reply_text(f"<b>{E_CHECK} All your M3U8 tasks (active + queued) were cancelled.</b>", parse_mode=enums.ParseMode.HTML)


# --------------------------------------------------------------- callbacks
@Client.on_callback_query(filters.regex(r"^m3cancel:([a-f0-9]+)$"))
async def _cancel_selection(client: Client, cq: CallbackQuery):
    _SESSIONS.pop(cq.matches[0].group(1), None)
    await cq.message.edit_text(f"{E_CROSS} Cancelled.")
    await cq.answer()


@Client.on_callback_query(filters.regex(r"^m3qall:([a-f0-9]+)$"))
async def _pick_all_qualities(client: Client, cq: CallbackQuery):
    await _after_quality_pick(client, cq, cq.matches[0].group(1), is_all=True, height=None)


@Client.on_callback_query(filters.regex(r"^m3q:([a-f0-9]+):(\d+)$"))
async def _pick_quality(client: Client, cq: CallbackQuery):
    session_id, idx = cq.matches[0].group(1), int(cq.matches[0].group(2))
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Expired, send /m3u8 again.", show_alert=True)
    try:
        height = session["qualities"][idx]["height"]
    except IndexError:
        return await cq.answer("That button is stale — send /m3u8 again.", show_alert=True)
    await _after_quality_pick(client, cq, session_id, is_all=False, height=height)


async def _after_quality_pick(client, cq, session_id, is_all, height):
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Expired, send /m3u8 again.", show_alert=True)
    session["is_all"], session["sel_height"] = is_all, height
    await cq.answer("Quality selected.")

    tracks = session["audio_tracks"]
    if len(tracks) > 1:
        return await cq.message.edit_text(
            f"<b>{E_ROCKET} Step 2: Select audio track</b>\n\n"
            f"👇 ⭐ marks Hindi tracks.",
            reply_markup=_audio_kb(session_id, tracks), parse_mode=enums.ParseMode.HTML,
        )
    if tracks:
        session["sel_lang"], session["sel_name"] = tracks[0]["language"], tracks[0]["name"]
    await _ask_compress(client, cq, session_id)


@Client.on_callback_query(filters.regex(r"^m3a:([a-f0-9]+):(\d+)$"))
async def _pick_audio(client: Client, cq: CallbackQuery):
    session_id, idx = cq.matches[0].group(1), int(cq.matches[0].group(2))
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Expired, send /m3u8 again.", show_alert=True)
    try:
        track = session["audio_tracks"][idx]
    except IndexError:
        return await cq.answer("That button is stale — send /m3u8 again.", show_alert=True)
    session["sel_lang"], session["sel_name"] = track["language"], track["name"]
    await cq.answer("Audio selected.")
    await _ask_compress(client, cq, session_id)


async def _ask_compress(client: Client, cq: CallbackQuery, session_id: str):
    session = _SESSIONS.get(session_id)
    if not session:
        return
    await cq.message.edit_text(
        f"<b>{E_ROCKET} Step 3: Compress?</b>\n\n"
        f"🗜 <b>Compress</b> re-encodes to H.265/HEVC after download — noticeably "
        f"smaller file, but takes much longer (capped at {COMPRESS_TIMEOUT // 60} min; "
        f"falls back to the original file if it times out).\n"
        f"⚡ <b>Skip</b> uploads the original stream as-is — faster.\n\n"
        f"<i>Applies to the whole batch.</i>",
        reply_markup=_compress_kb(session_id), parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^m3c:([a-f0-9]+):(yes|no)$"))
async def _pick_compress(client: Client, cq: CallbackQuery):
    session_id, choice = cq.matches[0].group(1), cq.matches[0].group(2)
    session = _SESSIONS.get(session_id)
    if not session:
        return await cq.answer("Expired, send /m3u8 again.", show_alert=True)
    session["compress"] = (choice == "yes")
    await cq.answer("Will compress to H.265." if session["compress"] else "Skipping compression.")
    await _confirm_and_enqueue(client, cq, session_id)


async def _confirm_and_enqueue(client: Client, cq: CallbackQuery, session_id: str):
    session = _SESSIONS.pop(session_id, None)
    if not session:
        return
    cid = session["chat_id"]

    quality_txt = "ALL" if session["is_all"] else f"{session['sel_height']}p"
    audio_txt = lang_display(session["sel_lang"], session["sel_name"]) if session["sel_lang"] else "muxed"
    compress_txt = "🗜 H.265" if session.get("compress") else "⚡ Original"
    await cq.message.edit_text(
        f"<b>{E_CHECK} Quality: {quality_txt} · 🔊 {audio_txt} · {compress_txt} · 📦 {len(session['urls'])} URL(s)</b>\n\n"
        f"<i>Joining the queue...</i>",
        parse_mode=enums.ParseMode.HTML,
    )

    task_info = {
        "urls": session["urls"], "is_all": session["is_all"], "sel_height": session["sel_height"],
        "sel_lang": session["sel_lang"], "sel_name": session["sel_name"],
        "compress": session.get("compress", False),
        "orig_message": session["message"],
    }
    ok, note = await task_queue.add_task(cid, task_info)
    if not ok:
        return await client.send_message(cid, note, parse_mode=enums.ParseMode.HTML)
    if "Queued" in note or "position" in note.lower():
        await client.send_message(cid, note, parse_mode=enums.ParseMode.HTML)
    asyncio.create_task(process_batch(client, cid, task_info))


@Client.on_callback_query(filters.regex(r"^m3skip$"))
async def _skip_current(client: Client, cq: CallbackQuery):
    _cancel_flags[cq.message.chat.id] = "current"
    await cq.answer("Skipping current URL...")


@Client.on_callback_query(filters.regex(r"^m3cancelall$"))
async def _cancel_all_batch(client: Client, cq: CallbackQuery):
    cid = cq.message.chat.id
    _cancel_flags[cid] = "all"
    await task_queue.remove_all(cid)
    await cq.answer("Cancelling everything...")


# ------------------------------------------------------------------- batch
async def process_batch(client: Client, cid: int, task_info: dict):
    _cancel_flags[cid] = "none"
    urls = task_info["urls"]
    is_all = task_info["is_all"]
    target_height = task_info["sel_height"]
    sel_lang, sel_name = task_info["sel_lang"], task_info["sel_name"]
    orig_message = task_info["orig_message"]

    status = await client.send_message(cid, f"<b>{E_ROCKET} Starting batch...</b>", parse_mode=enums.ParseMode.HTML)
    loop = asyncio.get_running_loop()

    def make_progress_cb(kb):
        def cb(text):
            async def _edit():
                try:
                    await status.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(_edit(), loop)
        return cb

    uploaded = failed = skipped = 0
    total_bytes = 0
    t_start = time.time()
    kb = _batch_kb(len(urls) > 1)

    task_id = None
    try:
        from Akbots import task_manager
        label = f"M3U8 batch: {len(urls)} URL(s)" if len(urls) > 1 else f"M3U8: {get_video_name(urls[0], 0, 1)}"
        task_id = task_manager.register(orig_message.from_user.id, asyncio.current_task(), label)
    except Exception:
        task_id = None

    try:
        for ui, url in enumerate(urls):
            if is_all_cancelled(cid):
                skipped += len(urls) - ui
                break
            if _cancel_flags.get(cid) == "current":
                _cancel_flags[cid] = "none"
                skipped += 1
                continue

            tag = f"[{ui + 1}/{len(urls)}]" if len(urls) > 1 else ""
            vname = get_video_name(url, ui, len(urls))
            try:
                await status.edit_text(f"<b>{E_INFO} Parsing {tag} {vname}</b>", reply_markup=kb, parse_mode=enums.ParseMode.HTML)
                result = await parse_m3u8(url)
            except Exception as e:
                failed += 1
                await client.send_message(cid, f"<b>{E_CROSS} Skip {tag} {vname}:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
                continue

            qualities = result["qualities"]
            if not qualities:
                failed += 1
                continue

            chosen = qualities if is_all else [find_quality(qualities, target_height)]
            audio_match = find_audio(result["audio_tracks"], sel_lang, sel_name)

            for qi, q in enumerate(chosen):
                if is_cancelled(cid):
                    break
                qtag = f"[Q{qi + 1}/{len(chosen)}]" if len(chosen) > 1 else ""
                progress_cb = make_progress_cb(kb)
                downloader = Downloader(
                    cid=cid, video_url=q["url"],
                    audio_url=audio_match["url"] if audio_match else None,
                    qname=q["name"], vname=vname, progress_cb=progress_cb,
                )
                out_path = None
                try:
                    v_concat, a_concat = await loop.run_in_executor(_BATCH_EXECUTOR, downloader.prepare)
                    if is_cancelled(cid):
                        downloader.cleanup()
                        break

                    out_path = await mux_with_progress(
                        v_concat, a_concat, downloader.out_path, q["name"], status,
                        user_id=orig_message.from_user.id,
                    )
                    downloader.cleanup()

                    if is_cancelled(cid):
                        # Skip/Cancel was hit while ffmpeg was muxing — don't upload it.
                        try:
                            os.remove(out_path)
                        except OSError:
                            pass
                        skipped += 1
                        break

                    compress_note = ""
                    if task_info.get("compress"):
                        try:
                            hevc_path = await compress_to_hevc(
                                out_path, status, user_id=orig_message.from_user.id,
                            )
                            # Compression succeeded — the original muxed file is no
                            # longer needed, only the smaller HEVC copy gets uploaded.
                            try:
                                os.remove(out_path)
                            except OSError:
                                pass
                            out_path = hevc_path
                            compress_note = " · 🗜 H.265"
                        except Exception as e:
                            # Don't lose the whole download over a failed/timed-out
                            # re-encode — fall back to uploading what mux already
                            # produced, just flag that compression didn't happen.
                            await client.send_message(
                                cid,
                                f"<b>{E_CROSS} Compression skipped {tag} {vname}:</b> "
                                f"<code>{e}</code>\n<i>Uploading original instead.</i>",
                                parse_mode=enums.ParseMode.HTML,
                            )

                    if is_cancelled(cid):
                        try:
                            os.remove(out_path)
                        except OSError:
                            pass
                        skipped += 1
                        break

                    size = os.path.getsize(out_path)
                    caption = f"<b>{E_CHECK} {vname}</b> {tag} {qtag}\n📐 {q['resolution']} · 📦 {fmt_bytes(size)}{compress_note}"
                    await upload_file(
                        client, orig_message, out_path, status, caption,
                        file_name=vname, quality=q.get("name"),
                    )
                    uploaded += 1
                    total_bytes += size
                except DownloadCancelled:
                    downloader.cleanup()
                    skipped += 1
                except DownloadTimeout:
                    downloader.cleanup()
                    failed += 1
                    await client.send_message(cid, f"<b>{E_CROSS} Timeout {tag} {vname}</b> — over {TASK_TIMEOUT // 60} min.", parse_mode=enums.ParseMode.HTML)
                except Exception as e:
                    downloader.cleanup()
                    failed += 1
                    await client.send_message(cid, f"<b>{E_CROSS} {tag} {vname} failed:</b> <code>{str(e)[:200]}</code>", parse_mode=enums.ParseMode.HTML)
                finally:
                    # upload_file deletes out_path itself on success, but if
                    # mux/compress succeeded and something failed after that
                    # (upload error, cancellation, etc.) the finished file
                    # would otherwise be orphaned on disk forever. out_path
                    # may be the HEVC copy (different from downloader.out_path)
                    # if compression ran, so both are checked.
                    for leftover in {downloader.out_path, out_path}:
                        if leftover:
                            try:
                                if os.path.exists(leftover):
                                    os.remove(leftover)
                            except OSError:
                                pass

            if is_all_cancelled(cid):
                skipped += max(0, len(urls) - ui - 1)
                break

        elapsed = time.time() - t_start
        was_cancelled = is_all_cancelled(cid)
        summary = (
            f"<b>{'🚫 Cancelled' if was_cancelled else '🎉 Batch complete'}</b>\n\n"
            f"📦 URLs: {len(urls)}\n{E_CHECK} Uploaded: {uploaded}\n"
            f"{E_CROSS} Failed: {failed}\n⏭ Skipped: {skipped}\n"
            f"💾 Size: {fmt_bytes(total_bytes)}\n{E_CLOCK} Time: {fmt_duration(elapsed)}"
        )
        try:
            await status.delete()
        except Exception:
            pass
        await client.send_message(cid, summary, parse_mode=enums.ParseMode.HTML)
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(orig_message.from_user.id, task_id)
            except Exception:
                pass
        _cancel_flags.pop(cid, None)
        next_cid, next_task = await task_queue.complete_task(cid)
        if next_cid and next_task:
            try:
                await client.send_message(next_cid, f"<b>{E_ROCKET} Your turn! Starting your M3U8 batch...</b>", parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
            asyncio.create_task(process_batch(client, next_cid, next_task))
