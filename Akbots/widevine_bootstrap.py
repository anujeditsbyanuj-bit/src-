"""
Akbots — Widevine device (.wvd) bootstrap

Akbots/hotstar_widevine.py and Akbots/crunchyroll_dl/crunchyroll.py both
need a single packaged `.wvd` device file, but what actually got uploaded
into this repo (Akbots/widevine_device/device_private_key.pem +
device_client_id_blob) are the two RAW components pywidevine needs to
BUILD that file — not the file itself. Building it requires running
pywidevine's own `create-device` command once.

This module does that automatically at bot startup, the same way
bgutil_bootstrap.py builds the PO-token server — so on a fresh Replit (or
any other) deploy, nothing needs to be run by hand. It's a no-op (just
logs OK) if l3.wvd already exists, so it's safe to call unconditionally
on every boot.
"""

import os
import shutil
import subprocess

WVD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widevine_device")
PRIVATE_KEY_PATH = os.path.join(WVD_DIR, "device_private_key.pem")
CLIENT_ID_PATH = os.path.join(WVD_DIR, "device_client_id_blob")
OUTPUT_WVD_PATH = os.path.join(WVD_DIR, "l3.wvd")

# Also mirrored to the repo root, since Akbots/crunchyroll_dl/crunchyroll.py
# loads "./l3.wvd" with a hardcoded relative path (not configurable).
ROOT_WVD_PATH = os.path.join(os.path.dirname(WVD_DIR), "l3.wvd")


def _run(cmd, cwd=None, timeout=60):
    return subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True, text=True)


def _build_wvd() -> bool:
    if not (os.path.exists(PRIVATE_KEY_PATH) and os.path.exists(CLIENT_ID_PATH)):
        # Nothing uploaded yet — not an error, just nothing to do.
        return False

    if shutil.which("pywidevine") is None:
        print("[widevine] WARNING — 'pywidevine' CLI not found on PATH even though "
              "it's in requirements.txt. If this is a fresh venv, it may need a moment "
              "after pip install, or the venv's bin/ isn't on PATH yet.")
        return False

    for device_type in ("ANDROID", "CHROME"):
        try:
            r = _run(["pywidevine", "create-device",
                      "-k", PRIVATE_KEY_PATH, "-c", CLIENT_ID_PATH,
                      "-t", device_type, "-l", "3", "-o", WVD_DIR])
        except Exception as e:
            print(f"[widevine] WARNING — create-device raised: {e}")
            continue

        if r.returncode == 0:
            # pywidevine names the output after the client_id's device
            # name, not a fixed filename — find whatever .wvd it just
            # wrote and rename it to what config.py's HOTSTAR_WVD_FILE
            # (and this module) expect.
            produced = [f for f in os.listdir(WVD_DIR) if f.endswith(".wvd")]
            if produced:
                produced_path = os.path.join(WVD_DIR, produced[0])
                if produced_path != OUTPUT_WVD_PATH:
                    os.replace(produced_path, OUTPUT_WVD_PATH)
                print(f"[widevine] OK — built l3.wvd from the uploaded device key ({device_type} type).")
                return True

        # -t ANDROID is the more common case, but a client_id blob can
        # be CHROME-type instead — if ANDROID's build didn't produce a
        # usable file, silently try CHROME before giving up.

    print("[widevine] WARNING — create-device didn't produce a .wvd with either "
          "device type. The uploaded key/client_id files may not be a matching pair.")
    return False


def ensure_widevine_device():
    """Call once at bot startup. Cheap and synchronous (create-device runs
    in well under a second — no need for a background thread the way the
    PO-token server's minutes-long npm build needs one)."""
    if os.path.exists(OUTPUT_WVD_PATH):
        print("[widevine] OK — l3.wvd already present.")
    elif not _build_wvd():
        return
    else:
        pass

    if not os.path.exists(ROOT_WVD_PATH) and os.path.exists(OUTPUT_WVD_PATH):
        try:
            shutil.copy(OUTPUT_WVD_PATH, ROOT_WVD_PATH)
            print("[widevine] Mirrored l3.wvd to repo root for crunchyroll.py's hardcoded path.")
        except Exception as e:
            print(f"[widevine] WARNING — couldn't mirror l3.wvd to repo root: {e}")
