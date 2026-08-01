# Akbots - Don't Remove Credit - @AkBots_Official
#
# /deps + /install — self-service dependency installer, admin-only.
#
# Two kinds of dependency, both installable straight from Telegram:
#
#   kind="pip"  — optional Python packages. The plugin that uses one checks
#                 for it at import/runtime and falls back / no-ops if it's
#                 missing (see requirements.txt's own comments: pyzipper,
#                 py7zr, playwright, bgutil-ytdlp-pot-provider, TTS,
#                 upstash-redis, DrissionPage, camoufox, the Google Drive
#                 OAuth stack, gallery-dl, lk21, cloudscraper, cfscrape,
#                 speedtest-cli).
#
#   kind="apt"  — system binaries the Dockerfile/replit.nix normally bake
#                 in at build time (megatools, ffmpeg, aria2, mediainfo,
#                 java, p7zip/7z, unrar, xvfb, node). Those two files are
#                 the real fix for a fresh deploy — this is only the
#                 fallback for when a binary is missing at runtime anyway
#                 (image rebuilt without the full apt-get list, a host that
#                 skipped the Dockerfile, etc). Needs the process to be
#                 running as root and apt-get to actually be reachable —
#                 native (non-Docker) Render/Railway/Replit builds don't
#                 give you either, so /install will just report failure
#                 there instead of pretending to succeed.
#
# Both kinds share one UI: /deps shows a tap-to-install status list,
# /install <key> (or /install all) runs the right installer in a
# subprocess and streams its output into the message as it goes — the
# same asyncio.create_subprocess_exec pattern Akbots/headless.py already
# uses for its own one-time Chromium self-install.
#
# A freshly-installed package/binary needs the bot process restarted
# before any plugin that imports/spawns it picks it up — /install says
# so, and /restart (Akbots/botadmin.py) is right there.
#
# Commands:
#   /deps                — status of every optional dependency (pip +
#                           system), with per-item "install" buttons
#   /install <key>        — install one dependency by its short key
#   /install all           — install every currently-missing dependency
#   /install               — (no args) prints the list of valid keys

import asyncio
import importlib
import os
import shutil
import subprocess
import sys
import threading
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from config import ADMINS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_CLOCK  = '<emoji id=5386367538735104399>⌛</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'

PIP_DEPS = {
    "pyzipper":      ("Password-protected ZIP (/zip, /zipencryption)",
                       "pyzipper", "pyzipper>=0.3.6", "Akbots/archive.py", None),
    "py7zr":         ("7z archive creation (/zipformat 7z)",
                       "py7zr", "py7zr>=0.21.0", "Akbots/archive.py", None),
    "playwright":    ("Headless-browser bypass + video discovery",
                       "playwright", "playwright==1.54.0", "Akbots/headless.py, playwright_bypass.py",
                       ["playwright", "install", "chromium"]),
    "pot_provider":  ("YouTube PO Token auto-generation",
                       "bgutil_ytdlp_pot_provider", "bgutil-ytdlp-pot-provider>=1.2.0", "/yt, /yta", None),
    "tts":           ("Voice cloning for /dub (male1/male2/female)",
                       "TTS", "TTS", "Akbots/dub.py", None),
    "drission":      ("Cloudflare bypass — tier 1 (DrissionPage)",
                       "DrissionPage", "DrissionPage==4.0.5.6", "Akbots/cf_bypass.py", None),
    "camoufox":      ("Cloudflare bypass — tier 2 (camoufox)",
                       "camoufox", "camoufox[geoip]>=0.4", "Akbots/cf_bypass.py, cf_lib/", None),
    "gdrive":        ("Google Drive OAuth (private files/folders)",
                       "googleapiclient",
                       "google-api-python-client google-auth google-auth-oauthlib",
                       "/gdrive", None),
    "gallery_dl":    ("Gallery downloader (/gallery)",
                       "gallery_dl", "gallery-dl", "Akbots/gallery.py", None),
    "lk21":          ("Fembed-family mirror resolver",
                       "lk21", "lk21==1.6.0", "Akbots/fembed.py", None),
    "cloudscraper":  ("Shortlink/mirror bypass (cloudscraper)",
                       "cloudscraper", "cloudscraper", "LinkBypassTG/Link-Bypass mirror hosts", None),
    "cfscrape":      ("Shortlink/mirror bypass (cfscrape, legacy)",
                       "cfscrape", "cfscrape", "LinkBypassTG/Link-Bypass mirror hosts", None),
    "speedtest_cli": ("/speedtest",
                       "speedtest", "speedtest-cli", "Akbots/speedtest.py", None),
    "gemini":        ("Gemini AI chat (/gemini, /ai, /ask)",
                       "google.genai", "google-genai", "Akbots/gemini_chat.py", None),
    "groq":          ("Groq AI chat (/groq, /groqmodel, /resetgroq)",
                       "groq", "groq", "Akbots/groq_chat.py", None),
    "gpt":           ("GPT/Claude chat, vision, streaming, image gen (/gpt, /imagine)",
                       "openai", "openai", "Akbots/openai_chat.py", None),
}

APT_DEPS = {
    "megatools": ("Mega.nz CLI (megadl/megacopy — Mega links)",
                  ["megadl", "megacopy"], "megatools", "Akbots/*mega*", None),
    "ffmpeg":    ("Video/audio processing (encode, thumbnails, mux)",
                  ["ffmpeg"], "ffmpeg", "Akbots/encode.py, sample_video.py, audio_extract.py", None),
    "aria2":     ("Multi-connection downloader (direct links, torrents)",
                  ["aria2c"], "aria2", "Akbots/direct_utils.py, torrent.py", None),
    "mediainfo": ("Media technical metadata (/mediainfo)",
                  ["mediainfo"], "mediainfo", "Akbots/mediainfo.py", None),
    "java":      ("Java runtime (JDownloader)",
                  ["java"], "default-jre-headless", "Akbots/jdownloader.py, jdownloader_core.py", None),
    "7z":        ("7-Zip archive extraction/creation (/zipformat 7z)",
                  ["7z", "7za"], "p7zip-full", "Akbots/archive.py", None),
    "unrar":     ("RAR extraction (/unzip)",
                  ["unrar", "unrar-free"], "unrar-free", "Akbots/archive.py", None),
    "xvfb":      ("Virtual display (some headless-browser bypass paths)",
                  ["Xvfb"], "xvfb", "Akbots/headless.py, playwright_bypass.py", None),
    "node":      ("Node.js JS runtime (yt-dlp PO-token server)",
                  ["node"], "nodejs", "Akbots/bgutil_bootstrap.py, ytdl.py", None),
}

_apt_updated = False


def _pip_installed(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except Exception:
        return False


# =========================================================
# Startup auto-bootstrap — YouTube PO Token pip package
# =========================================================
#
# /deps + /install above are admin-triggered (you have to run the command
# yourself). This is the one exception: bgutil-ytdlp-pot-provider (the pip
# side of PO Token auto-generation — see Akbots/bgutil_bootstrap.py for the
# Node server side) is a small, fast, always-safe-to-install package that
# every deploy needs for /yt and /yta to work well, so it's auto-installed
# in the background on every boot instead of waiting for an admin to run
# /install pot_provider. It's a no-op if already installed.

def _pot_pip_bootstrap_worker():
    label, check, args, used_by, post = PIP_DEPS["pot_provider"]
    if _pip_installed(check):
        print("[pot-provider] OK — bgutil-ytdlp-pot-provider already installed.")
        return
    print("[pot-provider] Installing bgutil-ytdlp-pot-provider (first boot only, can take ~10-20s)...")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--break-system-packages", *args.split()],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0 and _pip_installed(check):
            print("[pot-provider] OK — installed. /yt and /yta will use it right away; "
                  "run /restart if they don't pick it up first try.")
        else:
            print(f"[pot-provider] WARNING — pip install failed:\n{r.stderr[-800:]}")
    except Exception as e:
        print(f"[pot-provider] WARNING — install step raised: {e}")


def ensure_pot_provider_installed():
    """Call once at bot startup (see bot.py). Runs the pip-install check/
    install flow in a background thread so a slow first-time install never
    delays the bot from coming online."""
    threading.Thread(target=_pot_pip_bootstrap_worker, daemon=True).start()


def _apt_installed(binary_names) -> bool:
    return any(shutil.which(b) for b in binary_names)


def _is_installed(kind: str, check) -> bool:
    return _pip_installed(check) if kind == "pip" else _apt_installed(check)


def _all_deps():
    for key, (label, check, args, used_by, post) in PIP_DEPS.items():
        yield key, "pip", label, check, args, used_by, post
    for key, (label, check, args, used_by, post) in APT_DEPS.items():
        yield key, "apt", label, check, args, used_by, post


def _status_text() -> str:
    lines = [f"<b>{E_GEAR} Optional Dependency Status</b>\n"]
    missing = []

    lines.append("<b>Python packages (pip)</b>")
    for key, (label, check, args, used_by, post) in PIP_DEPS.items():
        ok = _pip_installed(check)
        icon = E_CHECK if ok else E_CROSS
        lines.append(f"{icon} <b>{label}</b>\n    <code>{key}</code> · <i>{used_by}</i>")
        if not ok:
            missing.append(key)

    lines.append("\n<b>System binaries (apt-get)</b>")
    for key, (label, check, args, used_by, post) in APT_DEPS.items():
        ok = _apt_installed(check)
        icon = E_CHECK if ok else E_CROSS
        lines.append(f"{icon} <b>{label}</b>\n    <code>{key}</code> · <i>{used_by}</i>")
        if not ok:
            missing.append(key)

    if os.geteuid() != 0:
        lines.append(f"\n{E_WARN} <i>Bot isn't running as root — apt-get installs will likely fail here"
                      f" (common on Render/Railway native builds). Rebuild from the Dockerfile instead.</i>")

    lines.append("")
    if missing:
        lines.append(f"{E_WARN} <b>{len(missing)} missing.</b> Tap below to install, or <code>/install all</code>.")
    else:
        lines.append(f"{E_CHECK} <b>Everything's installed.</b>")
    return "\n".join(lines)


def _status_buttons() -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, kind, label, check, args, used_by, post in _all_deps():
        if _is_installed(kind, check):
            continue
        row.append(make_button(f"⬇️ {label.split('(')[0].strip()}", callback_data=f"depinstall:{key}",
                                style=_BS.SUCCESS if _BS else None))
        if len(row) == 1:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if rows:
        rows.append([make_button("⬇️ Install All Missing", callback_data="depinstall:all",
                                  style=_BS.SUCCESS if _BS else None)])
    rows.append([
        make_button("🔄 Refresh", callback_data="depinstall:refresh", style=_BS.PRIMARY if _BS else None),
        make_button("❌ Close", callback_data="depinstall:close", style=_BS.DANGER if _BS else None),
    ])
    return InlineKeyboardMarkup(rows)


async def _run_streamed(args, status_cb):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    tail = []
    loop = asyncio.get_event_loop()
    last_update = loop.time()
    while True:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=600)
        except asyncio.TimeoutError:
            break
        if not line:
            break
        tail.append(line.decode(errors="ignore").rstrip())
        tail = tail[-8:]
        now = loop.time()
        if now - last_update > 2:
            last_update = now
            await status_cb("\n".join(tail))
    await proc.wait()
    return proc.returncode == 0, "\n".join(tail)


async def _run_pip_install(pip_args: str, status_cb):
    args = [sys.executable, "-m", "pip", "install", "--break-system-packages", *pip_args.split()]
    return await _run_streamed(args, status_cb)


async def _run_apt_install(apt_pkgs: str, status_cb):
    global _apt_updated
    if os.geteuid() != 0:
        await status_cb("not running as root — apt-get needs root, this host likely doesn't allow it")
        return False, "permission denied (not root)"
    if not shutil.which("apt-get"):
        await status_cb("apt-get not found on this host")
        return False, "apt-get not found"
    if not _apt_updated:
        await status_cb("running apt-get update (first time this session)...")
        ok, tail = await _run_streamed(["apt-get", "update"], status_cb)
        if not ok:
            return False, tail
        _apt_updated = True
    args = ["apt-get", "install", "-y", "--no-install-recommends", *apt_pkgs.split()]
    return await _run_streamed(args, status_cb)


async def _install_one(key: str, target_message: Message):
    if key in PIP_DEPS:
        kind = "pip"
        label, check, args, used_by, post = PIP_DEPS[key]
    else:
        kind = "apt"
        label, check, args, used_by, post = APT_DEPS[key]

    install_cmd_shown = f"pip install {args}" if kind == "pip" else f"apt-get install {args}"

    async def status_cb(tail):
        try:
            await target_message.edit_text(
                f"<b>{E_CLOCK} Installing:</b> {label}\n<code>{install_cmd_shown}</code>\n\n"
                f"<pre>{tail[-800:]}</pre>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

    await status_cb("starting...")
    if kind == "pip":
        ok, tail = await _run_pip_install(args, status_cb)
    else:
        ok, tail = await _run_apt_install(args, status_cb)

    if ok and post:
        await status_cb("done — running post-install step (e.g. browser download)...")
        try:
            proc = await asyncio.create_subprocess_exec(
                *post, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=600)
        except Exception:
            pass

    still_ok = _is_installed(kind, check)
    icon = E_CHECK if still_ok else E_CROSS
    if still_ok:
        result = f"{E_CHECK} <b>Installed.</b> Run /restart so plugins that use it pick it up."
    else:
        result = f"{E_CROSS} <b>Install failed</b> — see the output above, or check /logs."
    await target_message.edit_text(
        f"{icon} <b>{label}</b>\n<code>{key}</code>\n\n{result}",
        parse_mode=enums.ParseMode.HTML,
    )


def _missing_keys():
    return [key for key, kind, label, check, args, used_by, post in _all_deps() if not _is_installed(kind, check)]


@Client.on_message(filters.command("deps") & filters.user(ADMINS))
async def deps_cmd(client: Client, message: Message):
    await message.reply_text(_status_text(), reply_markup=_status_buttons(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("install") & filters.user(ADMINS))
async def install_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        keys = "\n".join(f"• <code>{k}</code> — {v[0]} <i>(pip)</i>" for k, v in PIP_DEPS.items())
        keys += "\n" + "\n".join(f"• <code>{k}</code> — {v[0]} <i>(apt)</i>" for k, v in APT_DEPS.items())
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/install &lt;key&gt;</code> or <code>/install all</code>\n\n"
            f"<b>Available keys:</b>\n{keys}\n\n<i>Or just run /deps for a tap-to-install list.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1].strip().lower()
    status_msg = await message.reply_text(f"<b>{E_CLOCK} Working...</b>", parse_mode=enums.ParseMode.HTML)

    if arg == "all":
        missing = _missing_keys()
        if not missing:
            return await status_msg.edit_text(f"{E_CHECK} <b>Everything's already installed.</b>", parse_mode=enums.ParseMode.HTML)
        for key in missing:
            await _install_one(key, status_msg)
        return

    if arg not in PIP_DEPS and arg not in APT_DEPS:
        return await status_msg.edit_text(
            f"{E_CROSS} <b>Unknown key:</b> <code>{arg}</code>. Run /deps or /install with no args to see valid keys.",
            parse_mode=enums.ParseMode.HTML,
        )
    await _install_one(arg, status_msg)


@Client.on_callback_query(filters.regex(r"^depinstall:(.+)$") & filters.user(ADMINS))
async def deps_callback(client: Client, callback_query: CallbackQuery):
    action = callback_query.matches[0].group(1)

    if action == "close":
        await callback_query.message.delete()
        return await callback_query.answer()

    if action == "refresh":
        await callback_query.message.edit_text(_status_text(), reply_markup=_status_buttons(), parse_mode=enums.ParseMode.HTML)
        return await callback_query.answer("Refreshed")

    await callback_query.answer("Installing...")

    if action == "all":
        missing = _missing_keys()
        if not missing:
            return await callback_query.message.edit_text(f"{E_CHECK} <b>Everything's already installed.</b>", parse_mode=enums.ParseMode.HTML)
        for key in missing:
            await _install_one(key, callback_query.message)
        return

    if action not in PIP_DEPS and action not in APT_DEPS:
        return await callback_query.answer(f"Unknown key: {action}", show_alert=True)

    await _install_one(action, callback_query.message)
