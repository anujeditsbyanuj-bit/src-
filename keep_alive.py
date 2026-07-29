import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)
_PORT = int(os.environ.get("PORT", 8080))
_PING_INTERVAL = 300
_SERVER: HTTPServer | None = None
_PING_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"lastperson07 alive")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def _ping_target() -> str:
    for env_name in ("PING_URL", "HEALTHCHECK_URL", "RENDER_EXTERNAL_URL", "APP_URL"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value.rstrip("/")

    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip().strip("/")
    if render_host:
        return f"https://{render_host}"

    return f"http://127.0.0.1:{_PORT}"


def _ping_loop():
    target = _ping_target()
    log.info("Keep-alive ping target: %s (every %ss)", target, _PING_INTERVAL)

    while True:
        try:
            req = Request(target, method="HEAD")
            with urlopen(req, timeout=20) as resp:
                log.info("Keep-alive ping ok: %s", getattr(resp, "status", 200))
        except URLError as exc:
            log.warning("Keep-alive ping failed: %s", exc)
        except Exception as exc:
            log.warning("Keep-alive ping error: %s", exc)

        time.sleep(_PING_INTERVAL)


keep_alive = None  # alias set below


def lastperson07_keep_alive():
    global _SERVER, _PING_THREAD

    with _LOCK:
        if _SERVER is None:
            try:
                _SERVER = HTTPServer(("0.0.0.0", _PORT), _HealthHandler)
            except OSError as exc:
                log.warning("Health server unavailable on :%s: %s", _PORT, exc)
                return False

            thread = threading.Thread(
                target=_SERVER.serve_forever,
                daemon=True,
                name="lastperson07-health",
            )
            thread.start()

        if _PING_THREAD is None or not _PING_THREAD.is_alive():
            _PING_THREAD = threading.Thread(
                target=_ping_loop,
                daemon=True,
                name="lastperson07-self-ping",
            )
            _PING_THREAD.start()

    log.info("Health server on :%s", _PORT)
    return True


# Alias so bot.py can import `keep_alive` directly
keep_alive = lastperson07_keep_alive
