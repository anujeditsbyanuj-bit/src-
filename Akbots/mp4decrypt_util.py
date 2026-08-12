# Akbots - Don't Remove Credit - @AkBots_Official
#
# Locator + async wrapper for the bundled Bento4 `mp4decrypt` binary
# (Akbots/bin/mp4decrypt) — decrypts CENC/CBCS-encrypted MP4/fragmented-MP4
# given the content key(s) the caller already has (KID:KEY hex pairs,
# obtained the same way any legitimate CENC decryption workflow gets
# them — this tool only decrypts with a key you already hold, it doesn't
# extract or crack one). Not an apt/pip package, so it doesn't go through
# Akbots/dependency_manager.py's installer — it's a prebuilt binary
# committed straight into the repo since Bento4 isn't in the standard
# Debian/Ubuntu apt repos.

import os
import re
import shutil
import asyncio

_BUNDLED_PATH = os.path.join(os.path.dirname(__file__), "bin", "mp4decrypt")

KEY_RE = re.compile(r"^[0-9a-fA-F]{32}:[0-9a-fA-F]{32}$")


def find_mp4decrypt() -> str | None:
    """Bundled copy first (always present in this repo), falls back to
    a system-installed one on PATH if someone removed the bundled binary
    or is running on an unsupported architecture and built their own."""
    if os.path.isfile(_BUNDLED_PATH) and os.access(_BUNDLED_PATH, os.X_OK):
        return _BUNDLED_PATH
    return shutil.which("mp4decrypt")


def valid_key(key: str) -> bool:
    """A Bento4 --key value is `KID:KEY`, each a 32-char hex string
    (128-bit AES). Validating this before spawning the subprocess turns a
    silent/cryptic mp4decrypt CLI error into a clear one up front."""
    return bool(KEY_RE.match(key.strip()))


async def decrypt_mp4(input_path: str, output_path: str, keys: list) -> tuple:
    """Runs `mp4decrypt --key K1 --key K2 ... input output`.
    Returns (True, "") on success, or (False, <stderr tail>) on failure —
    caller decides how to surface that message."""
    binary = find_mp4decrypt()
    if not binary:
        return False, "mp4decrypt binary not found (expected at Akbots/bin/mp4decrypt)."

    bad = [k for k in keys if not valid_key(k)]
    if bad:
        return False, f"Invalid key format (expected KID:KEY, 32 hex chars each): {bad[0]}"

    args = [binary]
    for k in keys:
        args += ["--key", k.strip()]
    args += [input_path, output_path]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return False, "Timed out after 10 minutes."
    except Exception as e:
        return False, str(e)

    if proc.returncode != 0 or not os.path.exists(output_path):
        err = (stderr or b"").decode(errors="replace").strip()
        return False, err[-800:] if err else f"mp4decrypt exited with code {proc.returncode}"

    return True, ""
