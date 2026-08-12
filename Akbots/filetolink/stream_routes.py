import re
import math
import time
import logging
import secrets

from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine

from config import STREAM_LINK_EXPIRY
from database.db import db
from . import work_loads, class_cache, multi_clients, StartTime, __version__
from .exceptions import FileNotFound, InvalidHash
from .custom_dl import ByteStreamer
from .render_template import render_page
from .rate_limit import check_http_rate_limit
from config import STREAM_TRUST_PROXY, STREAM_SPEEDTEST_ENABLED

routes = web.RouteTableDef()

# id -> creation timestamp, used only for STREAM_LINK_EXPIRY enforcement.
# Populated immediately on link creation (link_builder.py) and lazily
# (from Mongo) here on first request after a restart.
_link_created_at = {}


def _readable_time(seconds: float) -> str:
    seconds = int(seconds)
    periods = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    parts = []
    for name, secs in periods:
        val, seconds = divmod(seconds, secs)
        if val:
            parts.append(f"{val}{name}")
    return " ".join(parts) or "0s"


def note_link_created(id: int):
    _link_created_at[id] = time.time()


# In-process cache for the (possibly admin-overridden via /set_expiry)
# expiry window, so every single request doesn't hit Mongo. Mirrors the
# pattern used by Akbots/maintenance.py's enabled-flag cache.
_expiry_cache = {"seconds": None, "checked_at": 0.0}
_EXPIRY_CACHE_TTL = 15


async def _current_expiry_seconds() -> int:
    now = time.time()
    if _expiry_cache["seconds"] is None or now - _expiry_cache["checked_at"] > _EXPIRY_CACHE_TTL:
        try:
            override = await db.get_link_expiry_seconds()
        except Exception:
            override = None
        _expiry_cache["seconds"] = STREAM_LINK_EXPIRY if override is None else override
        _expiry_cache["checked_at"] = now
    return _expiry_cache["seconds"]


def note_expiry_changed(seconds: int):
    """Called by /set_expiry right after a successful write, so the new
    value takes effect immediately in this process instead of waiting up
    to _EXPIRY_CACHE_TTL seconds."""
    _expiry_cache["seconds"] = seconds
    _expiry_cache["checked_at"] = time.time()


async def _check_expiry(id: int):
    expiry_seconds = await _current_expiry_seconds()
    if expiry_seconds <= 0:
        return
    created_at = _link_created_at.get(id)
    if created_at is None:
        # Not in this process's memory (e.g. bot restarted since the link
        # was made) — fall back to the persisted timestamp in Mongo.
        try:
            created_at = await db.get_stream_link_timestamp(id)
        except Exception:
            created_at = None
        if created_at:
            _link_created_at[id] = created_at
    if created_at and time.time() - created_at > expiry_seconds:
        raise web.HTTPGone(text="This link has expired. Please request a new one from the bot.")


def _get_bot():
    from . import BOT as _bot
    return _bot


def _pick_client():
    """Returns (index, client) for the least-loaded available client.
    With no extra STREAM_EXTRA_TOKENS configured, multi_clients only ever
    has the single primary bot at index 0."""
    if not multi_clients:
        return 0, _get_bot()
    index = min(work_loads, key=lambda k: work_loads.get(k, 0))
    return index, multi_clients.get(index) or _get_bot()


def _parse_path(path: str, request: web.Request):
    match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
    if match:
        secure_hash = match.group(1)
        id = int(match.group(2))
    else:
        m = re.search(r"(\d+)(?:/\S+)?", path)
        if not m:
            raise FileNotFound("No file id in path")
        id = int(m.group(1))
        secure_hash = request.rel_url.query.get("hash")
    return id, secure_hash


@routes.get("/", allow_head=True)
async def root_route_handler(_):
    return web.json_response({
        "server_status": "running",
        "uptime": _readable_time(time.time() - StartTime),
        "connected_bots": len(multi_clients) or 1,
        "loads": {f"bot{i + 1}": load for i, load in enumerate(work_loads.values())},
        "version": __version__,
        "trust_proxy": STREAM_TRUST_PROXY,
        "speedtest_enabled": STREAM_SPEEDTEST_ENABLED,
    })


@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def stream_watch_handler(request: web.Request):
    try:
        id, secure_hash = _parse_path(request.match_info["path"], request)
        check_http_rate_limit(request)
        await _check_expiry(id)
        bot = _get_bot()
        return web.Response(text=await render_page(bot, id, secure_hash), content_type="text/html")
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FileNotFound as e:
        raise web.HTTPNotFound(text=str(e))
    except web.HTTPException:
        raise
    except (AttributeError, BadStatusLine, ConnectionResetError):
        return web.Response(status=400, text="Bad Request")
    except Exception as e:
        logging.critical(f"[filetolink] {e}")
        return web.Response(status=500, text=str(e))


@routes.get(r"/{path:\S+}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        id, secure_hash = _parse_path(request.match_info["path"], request)
        check_http_rate_limit(request)
        await _check_expiry(id)
        return await media_streamer(request, id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FileNotFound as e:
        raise web.HTTPNotFound(text=str(e))
    except web.HTTPException:
        raise
    except (AttributeError, BadStatusLine, ConnectionResetError):
        return web.Response(status=400, text="Bad Request")
    except Exception as e:
        logging.critical(f"[filetolink] {e}")
        return web.Response(status=500, text=str(e))


async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", None)
    index, client = _pick_client()
    if client is None:
        return web.Response(status=503, text="Streamer not ready yet, try again shortly.")

    tg_connect = class_cache.get(client) or ByteStreamer(client)
    class_cache[client] = tg_connect

    file_id = await tg_connect.get_file_properties(id)

    if not secure_hash or file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    file_size = file_id.file_size

    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not m:
            return web.Response(status=400, text="Invalid Range header")
        from_bytes = int(m.group(1))
        until_bytes = int(m.group(2)) if m.group(2) else file_size - 1
    else:
        from_bytes = 0
        until_bytes = file_size - 1

    if until_bytes >= file_size or from_bytes < 0 or until_bytes < from_bytes:
        return web.Response(
            status=416,
            text="416: Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024
    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
    req_length = until_bytes - from_bytes + 1

    mime_type = file_id.mime_type or "application/octet-stream"
    file_name = file_id.file_name or f"{secrets.token_hex(2)}.bin"

    response = web.StreamResponse(
        status=206 if range_header else 200,
        reason="Partial Content" if range_header else "OK",
        headers={
            "Content-Type": mime_type,
            "Content-Length": str(req_length),
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Disposition": f'inline; filename="{file_name}"',
            "Accept-Ranges": "bytes",
        },
    )
    await response.prepare(request)

    try:
        async for chunk in tg_connect.yield_file(
            file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
        ):
            await response.write(chunk)
    except Exception as e:
        logging.exception(f"[filetolink] Error streaming file {file_id.unique_id}: {e}")
    finally:
        await response.write_eof()

    return response
