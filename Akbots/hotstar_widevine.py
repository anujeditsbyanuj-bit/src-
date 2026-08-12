# Widevine PSSH / decryption-key extraction for Hotstar DASH (.mpd) streams.
#
# services/hotstar-api (Akbots/hotstar_local_server.py) only ever requests
# HLS packaging from Hotstar ("package": ["hls"] in main.py) and its own
# decrypt_segment() only understands plain AES-128 HLS keys — it has no
# path at all for DASH/MPD streams, which is what Hotstar serves for some
# premium/live content when no HLS variant exists. Those streams are
# Widevine-DRM-protected (CENC), which needs a real Widevine CDM session
# (PSSH -> license challenge -> license response -> content key), not a
# simple AES key fetch. This module does that part.
#
# Ported from an uploaded extractor.py (originally written for a
# differently-structured "Ott-Bot" project using `bot.config.WVD_FILE`)
# to this project's config.py + async conventions. Follows the same
# pywidevine pattern already used in Akbots/crunchyroll_dl/crunchyroll.py
# for this project's other Widevine-protected source.
#
# NOTE: services/hotstar-api/main.py's resolve/download path now has a DASH
# branch that calls extract_key_sync() below then decrypts the fetched MPD
# tracks with Akbots/mp4decrypt_util.py — see the "DASH/Widevine" section
# in main.py. This module is that key-extraction half of the pipeline.

import re
import asyncio
import logging

import requests

try:
    from config import HOTSTAR_WVD_FILE
except ImportError:
    HOTSTAR_WVD_FILE = "./l3.wvd"

logger = logging.getLogger(__name__)


def _extract_key_sync(mpd_url: str, license_url: str = None) -> str:
    """Blocking implementation — always call via extract_key() instead,
    which runs this off the event loop."""
    try:
        from pywidevine.cdm import Cdm
        from pywidevine.device import Device
        from pywidevine.pssh import PSSH
    except ImportError:
        logger.error("pywidevine not installed — add it to requirements.txt")
        return None

    import os
    if not os.path.exists(HOTSTAR_WVD_FILE):
        logger.error(f"Widevine device file not found: {HOTSTAR_WVD_FILE} "
                     f"(set HOTSTAR_WVD_FILE in config/env to point at your .wvd)")
        return None

    try:
        resp = requests.get(mpd_url, timeout=30)
        resp.raise_for_status()
        mpd_text = resp.text
    except Exception as e:
        logger.error(f"Failed to download MPD manifest: {e}")
        return None

    pssh_match = re.search(r"<cenc:pssh>(.*?)</cenc:pssh>", mpd_text) or \
        re.search(r"<PSSH>(.*?)</PSSH>", mpd_text)
    if not pssh_match:
        logger.error("No PSSH found in MPD manifest — this stream may not be Widevine-protected.")
        return None
    pssh = pssh_match.group(1)

    if not license_url:
        for pattern in (
            r'(https://apix\.hotstar\.com/v2/fetch/license\?token=[^"\s]+)',
            r'(https://[^"\s]+license[^"\s]*)',
            r'<laurl[^>]*value="([^"]+)"',
        ):
            m = re.search(pattern, mpd_text, re.IGNORECASE)
            if m:
                license_url = m.group(1)
                break
    if not license_url:
        logger.error("No license URL found in MPD manifest and none was provided.")
        return None

    try:
        device = Device.load(HOTSTAR_WVD_FILE)
        cdm = Cdm.from_device(device)
        session_id = cdm.open()
    except Exception as e:
        logger.error(f"Failed to open Widevine CDM session: {e}")
        return None

    try:
        challenge = cdm.get_license_challenge(session_id, PSSH(pssh))
        resp = requests.post(
            license_url, data=challenge, timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.hotstar.com/",
            },
        )
        resp.raise_for_status()
        cdm.parse_license(session_id, resp.content)

        for key in cdm.get_keys(session_id):
            if key.type == "CONTENT":
                return f"{key.kid.hex()}:{key.key.hex()}"
        logger.error("License parsed but no CONTENT key was returned.")
        return None
    except Exception as e:
        logger.error(f"Widevine license/key exchange failed: {e}")
        return None
    finally:
        cdm.close(session_id)


async def extract_key(mpd_url: str, license_url: str = None) -> str:
    """Extracts the "KID:KEY" decryption key for a Widevine-protected
    Hotstar DASH (.mpd) stream. Returns None (and logs why) on any
    failure — pywidevine missing, no .wvd device file, no PSSH/license URL
    found, or the license server rejecting the request."""
    return await asyncio.to_thread(_extract_key_sync, mpd_url, license_url)


# Public sync entry point — for callers that are already running inside a
# worker thread of their own (e.g. services/hotstar-api/main.py's
# FastAPI BackgroundTasks, which run off the event loop already) so they
# can call this directly instead of bouncing through asyncio.to_thread()
# from a thread that has no running loop to bounce back to.
extract_key_sync = _extract_key_sync
