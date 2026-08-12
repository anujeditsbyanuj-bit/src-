# Akbots - Don't Remove Credit - @AkBots_Official
#
# Static file server for locally-generated HLS packages (Akbots/
# v2hls_converter.py's output). Mounted onto the SAME aiohttp app +
# public port everything else in Akbots/hls_proxy_routes.py already
# rides (see that file's docstring and config.STREAM_URL) — nothing new
# to deploy, no GitHub Pages / Internet Archive account needed, unlike
# the original Video-to-HLS script's deploy options (deliberately not
# ported, see Akbots/v2hls_converter.py's docstring).
#
# Route:
#   GET /v2hls/{job_id}/{filename:.*} — serves output_dir/{job_id}/{filename}
#     (master.m3u8, video_*/index.m3u8 + segments, audio_*/..., sub_*/...,
#     the thumbnail — whatever Akbots/v2hls_converter.py wrote there).

import logging
import mimetypes
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

# Kept in sync with Akbots/v2hls_commands.py's OUTPUT_ROOT — imported
# from there rather than redefined, so there's exactly one place that
# decides where converted packages live on disk.
from .v2hls_commands import OUTPUT_ROOT  # noqa: E402


@routes.get(r"/v2hls/{job_id}/{filename:.*}")
async def v2hls_static(request: web.Request) -> web.StreamResponse:
    job_id = request.match_info["job_id"]
    filename = request.match_info["filename"]

    # No .. traversal, no absolute paths — job_id/filename both come
    # straight from the URL.
    if ".." in job_id or ".." in filename or job_id.startswith("/") or filename.startswith("/"):
        raise web.HTTPBadRequest(text="Invalid path")

    file_path = (OUTPUT_ROOT / job_id / filename).resolve()
    try:
        file_path.relative_to(OUTPUT_ROOT.resolve())
    except ValueError:
        raise web.HTTPForbidden(text="Path escapes output root")

    if not file_path.is_file():
        raise web.HTTPNotFound(text="Not found — link may have expired or the job ID is wrong.")

    content_type, _ = mimetypes.guess_type(str(file_path))
    if file_path.suffix == ".m3u8":
        content_type = "application/vnd.apple.mpegurl"
    elif file_path.suffix == ".vtt":
        content_type = "text/vtt"
    elif file_path.suffix == ".ts":
        content_type = "video/mp2t"

    resp = web.FileResponse(file_path)
    if content_type:
        resp.content_type = content_type
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp
