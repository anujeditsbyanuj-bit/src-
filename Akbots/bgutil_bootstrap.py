"""
Akbots — bgutil PO-token server bootstrap (Replit / non-Docker fallback)

On Docker deploys, Dockerfile clones+builds bgutil-ytdlp-pot-provider at
image-build time and entrypoint.sh starts its Node HTTP server before
bot.py ever runs — see Dockerfile / entrypoint.sh comments.

Replit (and any other "just run python3 bot.py" deploy target) skips ALL
of that: .replit's run/deployment commands go straight to bot.py, so the
PO-token server is simply never cloned, built, or started there. Without
it, yt-dlp's YouTube "web" client can't pass the bot-check, and the
tv_embedded/android fallback clients are increasingly rate-limited/blocked
too — the end result is yt-dlp failing on every single YouTube video,
which is why Akbots/youtube.py's last-resort scraper fallback also always
fails right after (it depends on yt-dlp having succeeded at getting the
real format ladder in the first place).

This module gives Replit-style deploys the same server, built and started
from Python at bot startup instead of at Docker image-build time. It's a
no-op (just logs OK) if the server is already reachable — e.g. under
Docker, where entrypoint.sh already started it — so it's safe to call
unconditionally from bot.py on every platform.
"""

import os
import shutil
import subprocess
import threading
import time

import requests

BGUTIL_VERSION = "1.3.1"
BGUTIL_REPO = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
BGUTIL_PORT = 4416
BGUTIL_PING_URL = f"http://127.0.0.1:{BGUTIL_PORT}/ping"

# Kept inside the project directory (not /opt) so it works regardless of
# filesystem permissions on whatever host this runs on, and persists
# across restarts on platforms with persistent storage like Replit.
BGUTIL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".bgutil-pot")
BGUTIL_SERVER_DIR = os.path.join(BGUTIL_DIR, "server")
BGUTIL_MAIN_JS = os.path.join(BGUTIL_SERVER_DIR, "build", "main.js")
BGUTIL_LOG = "/tmp/bgutil-pot.log"


def _is_reachable(timeout=2) -> bool:
    try:
        r = requests.get(BGUTIL_PING_URL, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _run(cmd, cwd=None, timeout=300):
    return subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True, text=True)


def _build_if_needed() -> bool:
    """Clones + builds bgutil-ytdlp-pot-provider into BGUTIL_DIR if it
    isn't already there. Returns True if a runnable build ends up present
    (either just-built or already existing from a previous run)."""
    if os.path.exists(BGUTIL_MAIN_JS):
        return True

    if not shutil.which("git"):
        print("[bgutil-pot] WARNING — git not found, can't clone the PO-token server.")
        return False
    if not shutil.which("node") or not shutil.which("npm"):
        print("[bgutil-pot] WARNING — node/npm not found (check replit.nix has pkgs.nodejs_20).")
        return False

    try:
        if not os.path.exists(BGUTIL_DIR):
            print(f"[bgutil-pot] Cloning bgutil-ytdlp-pot-provider ({BGUTIL_VERSION})...")
            r = _run(["git", "clone", "--single-branch", "--branch", BGUTIL_VERSION,
                       "--depth", "1", BGUTIL_REPO, BGUTIL_DIR])
            if r.returncode != 0:
                print(f"[bgutil-pot] WARNING — git clone failed:\n{r.stderr[-1000:]}")
                return False

        print("[bgutil-pot] Building server (npm ci && npx tsc) — first boot only, can take a minute...")
        r = _run(["npm", "ci"], cwd=BGUTIL_SERVER_DIR, timeout=600)
        if r.returncode != 0:
            print(f"[bgutil-pot] WARNING — npm ci failed:\n{r.stderr[-1000:]}")
            return False
        r = _run(["npx", "tsc"], cwd=BGUTIL_SERVER_DIR, timeout=300)
        if r.returncode != 0:
            print(f"[bgutil-pot] WARNING — npx tsc failed:\n{r.stderr[-1000:]}")
            return False
    except Exception as e:
        print(f"[bgutil-pot] WARNING — build step raised: {e}")
        return False

    return os.path.exists(BGUTIL_MAIN_JS)


def _start_server():
    try:
        log_f = open(BGUTIL_LOG, "a")
        subprocess.Popen(
            ["node", BGUTIL_MAIN_JS],
            stdout=log_f, stderr=log_f,
            cwd=BGUTIL_SERVER_DIR,
            start_new_session=True,
        )
    except Exception as e:
        print(f"[bgutil-pot] WARNING — failed to start server process: {e}")


def _bootstrap_worker():
    if _is_reachable():
        print("[bgutil-pot] OK — PO token server already up (Docker entrypoint.sh, or a previous run).")
        return

    if not _build_if_needed():
        print("[bgutil-pot] Bot will still run — YouTube just falls back to tv_embedded/android's lower-res formats.")
        return

    _start_server()
    for _ in range(10):
        time.sleep(1)
        if _is_reachable():
            print(f"[bgutil-pot] OK — PO token server is up on :{BGUTIL_PORT} "
                  "(YouTube 'web' client should get the full quality ladder).")
            return

    print("[bgutil-pot] WARNING — server didn't come up in time. Check", BGUTIL_LOG)
    print("[bgutil-pot] Bot will still run — YouTube just falls back to tv_embedded/android's lower-res formats.")


def ensure_bgutil_pot_server():
    """Call once at bot startup. Runs the whole check/clone/build/start
    flow in a background thread so a slow first-time build never delays
    the bot itself from coming online — YouTube downloads simply keep
    using tv_embedded/android until the server finishes starting."""
    threading.Thread(target=_bootstrap_worker, daemon=True).start()
