"""
Network speed test endpoint for the File-to-Link server.

Gives end users (or a future /speedtest bot command that just links here)
a way to measure real throughput to this box, same idea as fast.com:

    GET  /speedtest/ping            -> tiny json, round-trip latency
    GET  /speedtest/download?mb=10  -> streams `mb` MB of junk bytes
    POST /speedtest/upload          -> reads+discards the body, reports timing

Junk bytes are sliced out of one os.urandom buffer generated once at
import time (not per-request) — cheap, and non-compressible enough that
a gzip-happy reverse proxy in front of this won't quietly shrink the
transfer and skew the result the way plain zeros/text would.

Whole module no-ops (404s) if STREAM_SPEEDTEST_ENABLED is off.
"""

import os
import time

from aiohttp import web

from config import STREAM_SPEEDTEST_ENABLED, STREAM_SPEEDTEST_MAX_MB
from .rate_limit import check_http_rate_limit

routes = web.RouteTableDef()

_CHUNK_SIZE = 256 * 1024  # 256KB per write, keeps memory flat regardless of total size
_JUNK = os.urandom(_CHUNK_SIZE)  # generated once; requests just re-slice/repeat this


def _require_enabled():
    if not STREAM_SPEEDTEST_ENABLED:
        raise web.HTTPNotFound(text="Speed test endpoint is disabled on this server.")


@routes.get("/speedtest/ping")
async def speedtest_ping(request: web.Request):
    _require_enabled()
    check_http_rate_limit(request)
    return web.json_response({"pong": True, "ts": time.time()})


@routes.get("/speedtest/download")
async def speedtest_download(request: web.Request):
    _require_enabled()
    check_http_rate_limit(request)

    try:
        mb = float(request.rel_url.query.get("mb", "10"))
    except ValueError:
        raise web.HTTPBadRequest(text="`mb` must be a number.")

    if mb <= 0:
        raise web.HTTPBadRequest(text="`mb` must be greater than 0.")
    if mb > STREAM_SPEEDTEST_MAX_MB:
        raise web.HTTPBadRequest(
            text=f"`mb` cannot exceed {STREAM_SPEEDTEST_MAX_MB} (server limit)."
        )

    total_bytes = int(mb * 1024 * 1024)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(total_bytes),
            "Content-Disposition": 'attachment; filename="speedtest.bin"',
            "Cache-Control": "no-store",
        },
    )
    await response.prepare(request)

    sent = 0
    try:
        while sent < total_bytes:
            remaining = total_bytes - sent
            piece = _JUNK if remaining >= _CHUNK_SIZE else _JUNK[:remaining]
            await response.write(piece)
            sent += len(piece)
    finally:
        await response.write_eof()

    return response


@routes.post("/speedtest/upload")
async def speedtest_upload(request: web.Request):
    _require_enabled()
    check_http_rate_limit(request)

    max_bytes = int(STREAM_SPEEDTEST_MAX_MB * 1024 * 1024)
    received = 0
    start = time.perf_counter()

    reader = request.content
    while True:
        chunk = await reader.read(_CHUNK_SIZE)
        if not chunk:
            break
        received += len(chunk)
        if received > max_bytes:
            raise web.HTTPRequestEntityTooLarge(
                max_size=max_bytes, actual_size=received
            )

    elapsed = time.perf_counter() - start
    mbps = (received * 8 / 1_000_000) / elapsed if elapsed > 0 else 0.0

    return web.json_response({
        "received_bytes": received,
        "server_time_seconds": round(elapsed, 4),
        "mbps": round(mbps, 2),
    })
