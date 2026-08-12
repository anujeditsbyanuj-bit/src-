# Akbots - Don't Remove Credit - @AkBots_Official
#
# Locator + async wrapper for the bundled Bento4 `mp4encrypt` binary
# (Akbots/bin/mp4encrypt) — encrypts a plain MP4/fragmented-MP4 into a
# CENC/CBCS/etc-protected one, counterpart to Akbots/mp4decrypt_util.py.
#
# mp4encrypt's own key syntax is `--key <track_id>:<key>:<iv>` plus a
# separate `--property <track_id>:KID:<kid>` to attach the KID a player
# needs to know which license/key to ask for — two flags for one logical
# "protect this track" action. This module bundles that into one
# `TRACK:KID:KEY:IV` unit per track so the command layer only has to deal
# with one thing per track, same shape as mp4decrypt's `KID:KEY`.

import os
import re
import shutil
import asyncio

_BUNDLED_PATH = os.path.join(os.path.dirname(__file__), "bin", "mp4encrypt")

# TRACK_ID : KID(32 hex) : KEY(32 hex) : IV(16 or 32 hex)
UNIT_RE = re.compile(r"^(\d+):([0-9a-fA-F]{32}):([0-9a-fA-F]{32}):([0-9a-fA-F]{16}|[0-9a-fA-F]{32})$")

METHODS = (
    "OMA-PDCF-CBC", "OMA-PDCF-CTR", "MARLIN-IPMP-ACBC", "MARLIN-IPMP-ACGK",
    "ISMA-IAEC", "PIFF-CBC", "PIFF-CTR", "MPEG-CENC", "MPEG-CBC1",
    "MPEG-CENS", "MPEG-CBCS",
)
DEFAULT_METHOD = "MPEG-CENC"  # the one actual DRM systems (Widevine/PlayReady CENC) use


def find_mp4encrypt() -> str | None:
    """Bundled copy first, falls back to a system-installed one on PATH."""
    if os.path.isfile(_BUNDLED_PATH) and os.access(_BUNDLED_PATH, os.X_OK):
        return _BUNDLED_PATH
    return shutil.which("mp4encrypt")


def parse_unit(unit: str):
    """`TRACK:KID:KEY:IV` -> (track_id, kid, key, iv) or None if malformed."""
    m = UNIT_RE.match(unit.strip())
    if not m:
        return None
    track_id, kid, key, iv = m.groups()
    return track_id, kid.lower(), key.lower(), iv.lower()


async def encrypt_mp4(input_path: str, output_path: str, units: list, method: str = DEFAULT_METHOD) -> tuple:
    """units: list of 'TRACK:KID:KEY:IV' strings, one per track to encrypt.
    Returns (True, "") on success, or (False, <error>) on failure."""
    binary = find_mp4encrypt()
    if not binary:
        return False, "mp4encrypt binary not found (expected at Akbots/bin/mp4encrypt)."

    method = method.upper()
    if method not in METHODS:
        return False, f"Unknown method '{method}'. Valid: {', '.join(METHODS)}"

    parsed = []
    for u in units:
        p = parse_unit(u)
        if not p:
            return False, (
                f"Invalid unit '{u}' — expected TRACK:KID:KEY:IV "
                f"(KID/KEY = 32 hex chars, IV = 16 or 32 hex chars)."
            )
        parsed.append(p)

    args = [binary, "--method", method]
    for track_id, kid, key, iv in parsed:
        args += ["--key", f"{track_id}:{key}:{iv}"]
        args += ["--property", f"{track_id}:KID:{kid}"]
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
        return False, err[-800:] if err else f"mp4encrypt exited with code {proc.returncode}"

    return True, ""
