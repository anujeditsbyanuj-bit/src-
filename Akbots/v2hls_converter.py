# Akbots - Don't Remove Credit - @AkBots_Official
#
# Ported from Video-to-HLS's main.py — video file -> adaptive-bitrate HLS
# (multi-quality video + multi-audio + subtitles + thumbnail + master
# playlist). Only the conversion core was kept: the original script's
# GitHub Pages / Internet Archive deployment functions (and its CLI/
# config.json loading) were left out — Akbots/v2hls_commands.py serves
# the generated output locally instead, through the same aiohttp app
# Akbots/hls_proxy_routes.py already rides (see config.STREAM_URL), so
# there's nothing extra to deploy or host.
#
# All functions here are synchronous/blocking (subprocess ffmpeg calls,
# same as the original) — callers run convert_to_hls() via
# asyncio.to_thread, same pattern Akbots/meow_downloader.py uses for
# yt-dlp.

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FFMPEG_PATH = "ffmpeg"
FFPROBE_PATH = "ffprobe"
DEFAULT_AUDIO_BITRATE = "128k"
DEFAULT_SEGMENT_DURATION = 6

VIDEO_VARIANTS = {
    "144p":  {"resolution": "256x144",   "bitrate": "300k",  "order": 10},
    "240p":  {"resolution": "426x240",   "bitrate": "500k",  "order": 20},
    "360p":  {"resolution": "640x360",   "bitrate": "800k",  "order": 30},
    "480p":  {"resolution": "854x480",   "bitrate": "1200k", "order": 40},
    "720p":  {"resolution": "1280x720",  "bitrate": "2500k", "order": 50},
    "1080p": {"resolution": "1920x1080", "bitrate": "4500k", "order": 60},
}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    logger.debug(f"[v2hls] Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"[v2hls] Command failed ({result.returncode}): {result.stderr[:500]}")
        raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)
    return result


def _get_video_metadata(input_file: Path) -> dict[str, Any]:
    result = _run([FFPROBE_PATH, "-v", "error", "-print_format", "json",
                   "-show_streams", "-show_format", str(input_file)])
    return json.loads(result.stdout)


def _bitrate_to_bandwidth(bitrate_str: str) -> int:
    bitrate_str = bitrate_str.lower()
    if "k" in bitrate_str:
        return int(float(bitrate_str.replace("k", "")) * 1000)
    if "m" in bitrate_str:
        return int(float(bitrate_str.replace("m", "")) * 1_000_000)
    return int(bitrate_str)


def _input_resolution(streams: list[dict]) -> tuple[int, int] | None:
    for s in streams:
        if s.get("codec_type") == "video" and s.get("width") and s.get("height"):
            return int(s["width"]), int(s["height"])
    return None


def _generate_video_renditions(input_file: Path, output_dir: Path, segment_duration: int,
                                ffmpeg_preset: str, selected_qualities: list[str],
                                input_height: int | None, on_progress=None) -> list[tuple[str, dict, str]]:
    video_paths = []
    sorted_variants = sorted(VIDEO_VARIANTS.items(), key=lambda item: item[1].get("order", 0))

    for quality_name, settings in sorted_variants:
        if quality_name not in selected_qualities:
            continue
        rendition_height = int(settings["resolution"].split("x")[1])
        if input_height and rendition_height > input_height:
            logger.info(f"[v2hls] Skipping {quality_name} — higher than source ({input_height}p).")
            continue

        if on_progress:
            on_progress(f"Encoding {quality_name}...")
        variant_path = output_dir / f"video_{quality_name}"
        variant_path.mkdir(parents=True, exist_ok=True)
        _run([
            FFMPEG_PATH, "-y", "-i", str(input_file),
            "-an", "-map", "0:v:0",
            "-c:v", "libx264", "-b:v", settings["bitrate"], "-s", settings["resolution"],
            "-profile:v", "main", "-level:v", "4.0", "-preset", ffmpeg_preset,
            "-force_key_frames", f"expr:gte(t,n_forced*{segment_duration})",
            "-f", "hls", "-hls_time", str(segment_duration), "-hls_playlist_type", "vod",
            "-hls_segment_filename", str(variant_path / "segment_%05d.ts"),
            str(variant_path / "index.m3u8"),
        ])
        video_paths.append((quality_name, settings, f"video_{quality_name}/index.m3u8"))
    return video_paths


def _generate_audio_renditions(input_file: Path, output_dir: Path, audio_streams: list[dict],
                                segment_duration: int, on_progress=None) -> list[tuple[str, str, str]]:
    audio_playlists = []
    for i, stream in enumerate(audio_streams):
        lang_code = stream.get("tags", {}).get("language", f"und{i}")
        lang_name = stream.get("tags", {}).get("title", f"Audio Track {i + 1}")
        if on_progress:
            on_progress(f"Encoding audio: {lang_name}...")
        audio_dir = output_dir / f"audio_{lang_code}_{i}"
        audio_dir.mkdir(parents=True, exist_ok=True)
        _run([
            FFMPEG_PATH, "-y", "-i", str(input_file),
            "-map", f"0:a:{i}", "-c:a", "aac", "-b:a", DEFAULT_AUDIO_BITRATE,
            "-f", "hls", "-hls_time", str(segment_duration), "-hls_playlist_type", "vod",
            "-hls_segment_filename", str(audio_dir / "segment_%05d.ts"),
            str(audio_dir / "index.m3u8"),
        ])
        audio_playlists.append((lang_code, lang_name, f"audio_{lang_code}_{i}/index.m3u8"))
    return audio_playlists


def _generate_subtitle_renditions(input_file: Path, output_dir: Path,
                                   subtitle_streams: list[dict]) -> list[tuple[str, str, str]]:
    subtitle_playlists = []
    for i, stream in enumerate(subtitle_streams):
        lang_code = stream.get("tags", {}).get("language", f"sub{i}")
        lang_name = stream.get("tags", {}).get("title", f"Subtitle {i + 1}")
        subtitle_dir = output_dir / f"sub_{lang_code}_{i}"
        subtitle_dir.mkdir(parents=True, exist_ok=True)
        vtt_path = subtitle_dir / f"subtitles_{lang_code}_{i}.vtt"
        try:
            _run([FFMPEG_PATH, "-y", "-i", str(input_file), "-map", f"0:s:{i}", "-c:s", "webvtt", str(vtt_path)])
            subtitle_playlists.append((lang_code, lang_name, f"sub_{lang_code}_{i}/subtitles_{lang_code}_{i}.vtt"))
        except subprocess.CalledProcessError as e:
            logger.warning(f"[v2hls] Couldn't extract subtitle {i} ({lang_name}): {e.stderr[:300] if e.stderr else e}")
    return subtitle_playlists


def _generate_master_playlist(output_dir: Path, video_paths: list, audio_playlists: list, subtitle_playlists: list):
    with open(output_dir / "master.m3u8", "w") as f:
        f.write("#EXTM3U\n#EXT-X-VERSION:3\n\n")

        for i, (lang_code, lang_name, path) in enumerate(audio_playlists):
            f.write(f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio-aac",NAME="{lang_name}",LANGUAGE="{lang_code}",'
                    f'DEFAULT={"YES" if i == 0 else "NO"},AUTOSELECT=YES,URI="{path}"\n')
        f.write("\n")

        for lang_code, lang_name, path in subtitle_playlists:
            f.write(f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{lang_name}",LANGUAGE="{lang_code}",'
                    f'DEFAULT=NO,AUTOSELECT=YES,URI="{path}"\n')
        f.write("\n")

        video_paths = sorted(video_paths, key=lambda x: _bitrate_to_bandwidth(x[1]["bitrate"]))
        for quality_name, settings, path in video_paths:
            bandwidth = _bitrate_to_bandwidth(settings["bitrate"])
            if audio_playlists:
                bandwidth += _bitrate_to_bandwidth(DEFAULT_AUDIO_BITRATE)
            f.write(f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={settings["resolution"]},'
                    f'CODECS="avc1.4D401F,mp4a.40.2",AUDIO="audio-aac",SUBTITLES="subs"\n')
            f.write(f"{path}\n")


def _generate_thumbnail(input_file: Path, output_dir: Path, thumbnail_time: str = "00:00:05") -> Path | None:
    thumb_path = output_dir / f"{input_file.stem}_thumbnail.jpg"
    try:
        _run([FFMPEG_PATH, "-y", "-ss", thumbnail_time, "-i", str(input_file),
              "-vframes", "1", "-q:v", "2", str(thumb_path)])
        return thumb_path
    except subprocess.CalledProcessError:
        return None


def convert_to_hls(
    input_file: Path,
    output_dir: Path,
    segment_duration: int = DEFAULT_SEGMENT_DURATION,
    ffmpeg_preset: str = "medium",
    video_qualities: list[str] | None = None,
    generate_thumb: bool = True,
    on_progress=None,
) -> Path:
    """Blocking. Converts input_file into an adaptive-bitrate HLS package
    under output_dir (video renditions + audio renditions + subtitles +
    thumbnail + master.m3u8). Returns the master.m3u8 path. Raises on
    failure — caller (Akbots/v2hls_commands.py) catches and reports it."""
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if on_progress:
        on_progress("Probing video...")
    metadata = _get_video_metadata(input_file)
    all_streams = metadata.get("streams", [])
    input_res = _input_resolution(all_streams)
    input_height = input_res[1] if input_res else None

    audio_streams = [s for s in all_streams if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in all_streams if s.get("codec_type") == "subtitle"]

    selected_qualities = video_qualities or list(VIDEO_VARIANTS.keys())

    video_paths = _generate_video_renditions(
        input_file, output_dir, segment_duration, ffmpeg_preset, selected_qualities, input_height, on_progress)
    if not video_paths:
        raise RuntimeError("No video renditions were generated (input may be lower-res than every configured quality).")

    audio_playlists = _generate_audio_renditions(input_file, output_dir, audio_streams, segment_duration, on_progress)
    subtitle_playlists = _generate_subtitle_renditions(input_file, output_dir, subtitle_streams)
    _generate_master_playlist(output_dir, video_paths, audio_playlists, subtitle_playlists)

    if generate_thumb:
        if on_progress:
            on_progress("Generating thumbnail...")
        _generate_thumbnail(input_file, output_dir)

    return output_dir / "master.m3u8"
