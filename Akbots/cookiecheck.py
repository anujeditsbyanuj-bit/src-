"""Admin diagnostic: /cookiecheck — reports what's actually causing YouTube's
"Sign in to confirm you're not a bot" wall and similar login-wall errors on
other sites, instead of leaving the admin to guess. Checks:
  1. yt-dlp version, and whether the bgutil PO-token plugin is installed
  2. Whether the bgutil PO-token HTTP server is actually reachable (the pip
     package alone does NOT run it — a separate server process must be up)
  3. For YouTube / Instagram / Facebook / VK: which cookies file _cookies_for()
     will actually use (a /setcookies custom override always wins over the
     static config.py path), whether it exists, and how many days until its
     earliest-expiring critical auth cookie runs out
"""
import os
import time
import asyncio
import http.cookiejar
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import ADMINS, YT_COOKIES, INSTA_COOKIES, FB_COOKIES, VK_COOKIES

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'

# The default bgutil-ytdlp-pot-provider HTTP server address (used by both
# the Node/Deno and Rust builds when nothing custom is configured). If your
# setup runs it on a different host/port, set POT_SERVER_URL in the
# environment to match.
POT_SERVER_URL = os.environ.get("POT_SERVER_URL", "http://127.0.0.1:4416")

# Cookie names whose expiry actually matters for a signed-in session -
# short-lived housekeeping cookies (YSC, CONSISTENCY, ST-*, etc.) are
# ignored since their expiry churns constantly and doesn't indicate whether
# the real login session is still good.
_CRITICAL_COOKIES = {
    "youtube.com": ("SID", "HSID", "SSID", "__Secure-1PSID", "LOGIN_INFO"),
    "instagram.com": ("sessionid", "csrftoken", "ds_user_id"),
    "facebook.com": ("c_user", "xs"),
    "vk.com": ("remixsid",),
}


def _cookie_domain_key(path_hint: str) -> str:
    if "youtube" in path_hint:
        return "youtube.com"
    if "insta" in path_hint:
        return "instagram.com"
    if "facebook" in path_hint or "fb_" in path_hint:
        return "facebook.com"
    if "vk" in path_hint:
        return "vk.com"
    return ""


def _cookie_freshness(path: str, domain_key: str) -> str:
    """Returns a short human-readable freshness summary for the critical
    auth cookies in this file, or an explanation of why none could be read."""
    if not path or not os.path.exists(path):
        return "missing"
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        cleaned = raw.replace("#HttpOnly_", "")
        tmp_path = path + ".freshness_check.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(cleaned)
        jar = http.cookiejar.MozillaCookieJar(tmp_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        os.remove(tmp_path)
    except Exception as e:
        return f"unreadable ({e})"

    if len(jar) == 0:
        return "file present but has no cookies (placeholder?)"

    wanted = _CRITICAL_COOKIES.get(domain_key, ())
    if not wanted:
        return f"{len(jar)} cookie(s) present (freshness check not defined for this site)"

    now = time.time()
    found = []
    for c in jar:
        if c.name in wanted:
            if c.expires:
                days_left = (c.expires - now) / 86400
                found.append((c.name, days_left))
            else:
                found.append((c.name, None))  # session cookie, no fixed expiry

    if not found:
        return f"no auth cookies ({', '.join(wanted)}) found — probably a logged-out/consent-only export"

    expired = [n for n, d in found if d is not None and d < 0]
    if expired:
        return f"EXPIRED — {', '.join(expired)} already past expiry, re-export needed"

    finite = [d for _, d in found if d is not None]
    if finite:
        return f"OK — earliest critical cookie expires in {min(finite):.0f} day(s)"
    return "OK — session cookies present (no fixed expiry to check)"


def _report_site(label: str, url_hint: str, static_path: str, domain_key: str) -> str:
    try:
        from Akbots.cookies_manager import get_cookies_for_url
        custom = get_cookies_for_url(url_hint)
    except Exception:
        custom = None

    active_path = custom or (static_path if static_path and os.path.exists(static_path) else None)
    if not active_path:
        return f"<b>{label}:</b> no cookies file found at all ({static_path or 'not configured'})"

    source = "custom /setcookies override" if custom else "static config.py path"
    freshness = _cookie_freshness(active_path, domain_key)
    return f"<b>{label}:</b> using {source} (<code>{active_path}</code>)\n   └ {freshness}"


def _check_pot_server() -> str:
    try:
        resp = requests.get(POT_SERVER_URL, timeout=3)
        return f"reachable at <code>{POT_SERVER_URL}</code> (HTTP {resp.status_code})"
    except requests.exceptions.ConnectionError:
        return (
            f"NOT RUNNING at <code>{POT_SERVER_URL}</code> — the pip package alone doesn't "
            f"start it. Run:\n<code>docker run --name bgutil-provider -d --init -p 4416:4416 "
            f"brainicism/bgutil-ytdlp-pot-provider</code>"
        )
    except Exception as e:
        return f"check failed: {e}"


def _yt_dlp_version() -> str:
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception as e:
        return f"not importable ({e})"


def _pot_plugin_installed() -> str:
    try:
        import importlib.metadata as im
        return im.version("bgutil-ytdlp-pot-provider")
    except Exception:
        return "NOT installed (pip install bgutil-ytdlp-pot-provider)"


@Client.on_message(filters.command("cookiecheck") & filters.private & filters.user(ADMINS))
async def cookiecheck_command(client: Client, message: Message):
    status = await message.reply_text(f"<b>{E_INFO} Running diagnostics...</b>", parse_mode=enums.ParseMode.HTML)

    yt_ver = _yt_dlp_version()
    pot_pkg_ver = _pot_plugin_installed()
    pot_server = await asyncio.to_thread(_check_pot_server)

    sites = [
        _report_site("YouTube", "https://youtube.com/", YT_COOKIES, "youtube.com"),
        _report_site("Instagram", "https://instagram.com/", INSTA_COOKIES, "instagram.com"),
        _report_site("Facebook", "https://facebook.com/", FB_COOKIES, "facebook.com"),
        _report_site("VK", "https://vk.com/", VK_COOKIES, "vk.com"),
    ]

    text = (
        f"<b>{E_INFO} Cookie & PO-token diagnostics</b>\n\n"
        f"<b>yt-dlp version:</b> <code>{yt_ver}</code>\n"
        f"<b>bgutil-ytdlp-pot-provider (pip):</b> <code>{pot_pkg_ver}</code>\n"
        f"<b>PO-token server:</b> {pot_server}\n\n"
        + "\n\n".join(sites)
    )
    await status.edit_text(text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
