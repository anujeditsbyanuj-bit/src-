# Akbots - Don't Remove Credit - @AkBots_Official
#
# Starts services/hotstar-api's FastAPI app (main.py) in-process, inside
# the bot's own asyncio event loop, bound to 127.0.0.1:HOTSTAR_LOCAL_PORT.
# This is what makes config.HOTSTAR_API_URL's default (see config.py)
# actually reachable without deploying that service separately or setting
# any env var by hand — same idea as Akbots/filetolink/web_server.py and
# Akbots/mediainfo_lib/streamer.py already do for their own features,
# just applied to the Hotstar resolver's FastAPI service instead of an
# aiohttp one.
#
# Bound to 127.0.0.1, not 0.0.0.0, on purpose: unlike file-to-link/HLS
# proxy, nothing outside the bot process needs to reach this — only
# Akbots/hotstar.py calls it (over loopback), so there's no reason to
# expose it publicly or fight those other servers for a public port.
#
# main.py is imported under a private module name (not "main") via
# importlib.util so it can never collide with any other top-level "main"
# module elsewhere in the project.

import asyncio
import importlib.util
import logging
import os
import sys

logger = logging.getLogger(__name__)

_SERVICE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "hotstar-api")
_MAIN_PATH = os.path.join(_SERVICE_DIR, "main.py")

_server = None  # keep a reference alive so it isn't garbage-collected mid-serve


def _load_app():
    """Imports services/hotstar-api/main.py's FastAPI `app` object under a
    private module name (hotstar_api_service) instead of touching
    sys.path or sys.modules['main']."""
    spec = importlib.util.spec_from_file_location("hotstar_api_service", _MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["hotstar_api_service"] = module
    spec.loader.exec_module(module)
    return module.app


async def start(port: int) -> bool:
    """Starts the Hotstar API server on 127.0.0.1:port inside the
    currently-running event loop (no new loop/thread/process — same loop
    Pyrogram itself is running in). Returns True on success, False if
    the service's own deps (fastapi/uvicorn/m3u8/pycryptodome) aren't
    importable or anything else goes wrong — callers should treat that
    the same as any other optional feature being unavailable, not a hard
    crash of the bot itself."""
    global _server
    if not os.path.exists(_MAIN_PATH):
        logger.warning(f"hotstar_local_server: {_MAIN_PATH} not found — /hotstar will stay unavailable.")
        return False

    try:
        import uvicorn
    except ImportError:
        logger.warning("hotstar_local_server: uvicorn not installed (pip install uvicorn) — /hotstar will stay unavailable.")
        return False

    try:
        app = _load_app()
    except Exception as e:
        logger.warning(f"hotstar_local_server: failed to import services/hotstar-api/main.py ({e}) — /hotstar will stay unavailable.")
        return False

    try:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        _server = uvicorn.Server(config)
        # uvicorn.Server.serve() installs SIGINT/SIGTERM handlers by
        # default when called from the main thread, which would fight
        # Pyrogram's own shutdown handling. This embeds it the same way
        # uvicorn's own docs recommend for running inside an existing
        # event loop: https://www.uvicorn.org/deployment/#embedding-uvicorn
        _server.install_signal_handlers = lambda: None
        asyncio.get_event_loop().create_task(_server.serve())
        return True
    except Exception as e:
        logger.warning(f"hotstar_local_server: failed to start ({e}) — /hotstar will stay unavailable.")
        return False
