# Akbots - Don't Remove Credit - @AkBots_Official
#
# Downloader for the Meow* providers (Akbots/meow_commands.py) — ported
# from the meowtv CLI project's meowtv/downloader.py (which shelled out to
# yt-dlp/ffmpeg with a `rich` progress bar for local playback). This
# version drives yt-dlp through its Python API instead of a subprocess and
# reports progress the same way Akbots/ytdl.py does — editing the status
# message via a progress_hooks callback bounced back onto the bot's event
# loop — so a resolved meow* stream (direct mp4/HLS URL + Referer/Cookie
# headers, exactly what meowtv_provider.fetch_stream_url() & co. return)
# can be pulled to disk and then uploaded to Telegram like any other
# /yt-style download.
#
# Needs yt-dlp (already required by Akbots/ytdl.py) and ffmpeg on PATH for
# HLS -> mp4 remuxing; both are already project requirements.

import os
import time
import shutil
import asyncio
import tempfile

from pyrogram.types import Message

from Akbots.direct_utils import safe_edit, format_progress

try:
    import yt_dlp
    _YTDLP_AVAILABLE = True
except ImportError:
    _YTDLP_AVAILABLE = False


# ── Availability checks ──────────────────────────────────────────────────

def find_ffmpeg() -> str | None:
    """Mirrors the CLI's find_ffmpeg() — yt-dlp needs this on PATH to remux
    HLS streams / merge separate video+audio into a single mp4."""
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def is_download_available() -> bool:
    """Mirrors the CLI's is_download_available()."""
    return _YTDLP_AVAILABLE and find_ffmpeg() is not None


# ── Progress reporting (same pattern as Akbots/ytdl.py) ─────────────────

async def _edit_status(status: Message, text: str):
    if status.photo:
        return await safe_edit(status.edit_caption, text, parse_mode="html")
    return await safe_edit(status.edit_text, text, parse_mode="html")


def _make_progress_hook(status: Message, loop: asyncio.AbstractEventLoop,
                         file_name: str = None, quality: str = None):
    """yt-dlp calls progress_hooks synchronously from its own worker thread,
    so edits are handed back to the bot's event loop via
    run_coroutine_threadsafe — identical to Akbots/ytdl.py's
    _make_download_progress_hook()."""
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
                title="Downloading stream...",
                file_name=file_name,
                quality=quality,
            )
            asyncio.run_coroutine_threadsafe(_edit_status(status, text), loop)
        elif status_type == "finished":
            asyncio.run_coroutine_threadsafe(
                _edit_status(status, "<b>⚙️ Merging / processing...</b>"), loop)

    return _hook


# ── Core download ─────────────────────────────────────────────────────────

def _safe_filename(title: str) -> str:
    """Mirrors the CLI's filename sanitizing in downloader.download()."""
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip()
    return (safe or "meow_video")[:100]


def _pick_url(stream: dict, quality: str | None) -> str | None:
    """Mirrors the CLI's quality-URL selection logic."""
    url = stream.get("videoUrl") or stream.get("video_url")
    if quality:
        for q in stream.get("qualities") or []:
            if (q.get("quality") or "").lower() == quality.lower():
                return q.get("url") or url
    return url


async def download_stream(stream: dict, title: str, status: Message,
                           quality: str | None = None, out_dir: str | None = None) -> str:
    """
    Download a resolved meow* stream to disk.

    stream: dict shaped like meow*_provider.fetch_stream_url()'s return
            value: {"videoUrl": str, "qualities": [{"quality","url"}, ...],
                    "headers": {...}}.
    Returns the downloaded file path. Raises RuntimeError on failure
    (caller should catch and show the error, same as ytdl.py does).
    """
    if not _YTDLP_AVAILABLE:
        raise RuntimeError("yt-dlp isn't installed (pip install yt-dlp).")
    if not find_ffmpeg():
        raise RuntimeError("ffmpeg isn't available on PATH — required to remux/merge the stream.")

    url = _pick_url(stream, quality)
    if not url:
        raise RuntimeError("No playable URL in the resolved stream.")

    headers = stream.get("headers") or {}
    out_dir = out_dir or tempfile.mkdtemp(prefix="meow_dl_")
    os.makedirs(out_dir, exist_ok=True)
    safe_title = _safe_filename(title)
    out_tmpl = os.path.join(out_dir, f"{safe_title}.%(ext)s")

    loop = asyncio.get_event_loop()
    hook = _make_progress_hook(status, loop, file_name=safe_title, quality=quality)

    # Robust format chain (adapted from the CLI's quality_map / default
    # chain in downloader.download_with_ytdlp): prefer separate
    # video+audio merged to mp4, fall back to best single file.
    format_str = (
        "bestvideo+bestaudio[format_id*=English]/bestvideo+bestaudio/best"
    )

    ydl_opts = {
        "outtmpl": out_tmpl,
        "format": format_str,
        "merge_output_format": "mp4",
        "http_headers": headers,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "progress_hooks": [hook],
    }

    def _run() -> str:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            # merge_output_format can change the extension post-download
            base, _ext = os.path.splitext(path)
            for ext in (".mp4", ".mkv", ".ts", ".m4a"):
                candidate = base + ext
                if os.path.exists(candidate):
                    return candidate
            if os.path.exists(path):
                return path
            raise RuntimeError("yt-dlp reported success but the output file is missing.")

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        raise RuntimeError(f"Download failed: {e}") from e


def cleanup(path: str):
    """Best-effort removal of the downloaded file + its temp directory
    after upload — mirrors the CLI's failed-download cleanup, extended to
    also run on success once the file's been sent to Telegram."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
        parent = os.path.dirname(path) if path else None
        if parent and os.path.basename(parent).startswith("meow_dl_"):
            shutil.rmtree(parent, ignore_errors=True)
    except Exception:
        pass
