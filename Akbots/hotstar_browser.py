# Browser-based MPD + Widevine license URL capture for Hotstar — fallback
# for when services/hotstar-api/main.py's normal /api/resolve (a direct
# call to Hotstar's widget API) fails outright, e.g. because the widget
# API rejects the token/cookies/signature for that particular
# account/content even though a real logged-in browser session would
# still play it fine.
#
# Ported from an uploaded Windows-only "Ott-Bot" project's bot/browser.py
# (hardcoded chrome.exe paths, taskkill, %LOCALAPPDATA%) to this project's
# Linux/Playwright conventions — reuses Akbots/headless.py's
# system_chromium_path()/_ensure_chromium() self-install machinery instead
# of assuming a local Chrome install, and Akbots/cookie_utils.py instead
# of its own bespoke Netscape-cookies-file parser.
#
# Extension support: Akbots/hotstar_extension/ is a placeholder folder —
# see its README.md. If a real unpacked Chrome extension is dropped in
# there, it's loaded via --load-extension (Chromium only supports
# unpacked MV3 extensions with a real Chrome channel, not the
# Playwright-bundled Chromium — see _launch() below for what that
# implies). If the folder is empty/missing, or the extension fails to
# load, this transparently falls back to pure network interception with
# no extension at all — same as the original browser.py's own
# try/except around that launch.
#
# Requires Playwright + a Chrome/Chromium binary that supports
# --load-extension (Playwright's own bundled Chromium works for the no-
# extension path; the extension path needs a real Google Chrome or
# Chromium build — see find_chrome_for_extensions() below).

import asyncio
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from Akbots import headless

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

try:
    from aiohttp import web as _aiohttp_web
except ImportError:
    _aiohttp_web = None

logger = logging.getLogger(__name__)

_EXTENSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hotstar_extension")
_EXTENSION_RELAY_PORT = 8765  # matches the hardcoded fetch() target in hotstar_extension/background.js

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_PLAY_SELECTORS = (
    '[data-testid="player-play-button"]',
    '.player-play-button',
    '[class*="play-btn"]',
    'video',
)


def available() -> bool:
    return async_playwright is not None


def _has_extension() -> bool:
    return os.path.isdir(_EXTENSION_DIR) and os.path.exists(os.path.join(_EXTENSION_DIR, "manifest.json"))


def find_chrome_for_extensions() -> str | None:
    """--load-extension only works with a real Chrome/Chromium channel,
    not Playwright's own bundled Chromium build (which Playwright launches
    in a stripped-down mode that silently ignores extension flags). Looks
    for a system Google Chrome first (matches what most Docker images and
    `apt install google-chrome-stable` give you), then falls back to
    whatever system Chromium headless.py already knows how to find. None
    if neither is present — callers should skip --load-extension and fall
    through to the no-extension path instead of failing outright."""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return headless.system_chromium_path()


def _cookies_to_playwright(cookies: dict, domain: str = ".hotstar.com") -> list:
    return [
        {"name": name, "value": value, "domain": domain, "path": "/"}
        for name, value in (cookies or {}).items()
        if name and value
    ]


async def _start_extension_relay(captured: dict):
    """hotstar_extension/background.js POSTs whatever it captures (via its
    own page-injected fetch()/XHR overrides in content.js — catches some
    requests our own Playwright-level `on_request` listener below can miss,
    e.g. ones served from a service-worker cache) to
    http://localhost:8765/mpd. Nothing was ever listening on that port, so
    all of that captured data was silently dropped — this starts a tiny
    local listener for it so it actually reaches `captured`, same dict the
    network listener writes into. Returns the aiohttp runner to stop later,
    or None if aiohttp is missing or the port's already taken (non-fatal —
    the Playwright network listener alone still works fine either way)."""
    if _aiohttp_web is None:
        return None

    async def handle_mpd(request):
        try:
            data = await request.json()
        except Exception:
            return _aiohttp_web.json_response({"status": "bad json"}, status=400)
        url = data.get("url")
        kind = data.get("type")
        if url and kind == "mpd" and not captured["mpd_url"]:
            logger.info(f"hotstar_browser: MPD captured via extension relay: {url[:100]}")
            captured["mpd_url"] = url
        elif url and kind == "license" and not captured["license_url"]:
            logger.info(f"hotstar_browser: license captured via extension relay: {url[:100]}")
            captured["license_url"] = url
        return _aiohttp_web.json_response({"status": "ok"})

    app = _aiohttp_web.Application()
    app.router.add_post("/mpd", handle_mpd)
    runner = _aiohttp_web.AppRunner(app)
    try:
        await runner.setup()
        site = _aiohttp_web.TCPSite(runner, "127.0.0.1", _EXTENSION_RELAY_PORT)
        await site.start()
        return runner
    except OSError as e:
        logger.warning(f"hotstar_browser: couldn't bind extension relay on port {_EXTENSION_RELAY_PORT} ({e}) — continuing without it.")
        try:
            await runner.cleanup()
        except Exception:
            pass
        return None


async def capture_mpd_license(page_url: str, cookies: dict = None, timeout: int = 60) -> dict | None:
    """Opens page_url (a real hotstar.com watch page, NOT a bare
    content_id — the browser needs something navigable) in Chromium,
    optionally with cookies pre-loaded and the extension from
    Akbots/hotstar_extension/ if present, and watches network traffic
    for a `.mpd` manifest request and a `license` request.

    Returns {"mpd_url": str, "license_url": str|None, "title": str|None},
    or None if Playwright/Chromium isn't available, or nothing was
    captured before timeout runs out."""
    if async_playwright is None:
        logger.warning("hotstar_browser: playwright not installed — browser fallback unavailable.")
        return None

    try:
        await asyncio.wait_for(headless._ensure_chromium(), timeout=45)
    except asyncio.TimeoutError:
        pass  # best-effort; launch below will just fail cleanly if the browser truly isn't there

    captured = {"mpd_url": None, "license_url": None, "title": None}
    relay_runner = await _start_extension_relay(captured)

    use_extension = _has_extension()
    chrome_path = find_chrome_for_extensions() if use_extension else headless.system_chromium_path()
    if use_extension and not chrome_path:
        logger.warning("hotstar_browser: extension present but no real Chrome/Chromium binary found "
                        "(Playwright's bundled Chromium can't load extensions) — continuing without it.")
        use_extension = False

    profile_dir = tempfile.mkdtemp(prefix="hotstar_chrome_profile_")

    async with async_playwright() as p:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--remote-allow-origins=*",
        ]
        context = None
        try:
            if use_extension:
                # Extensions require a *persistent* context (launch_persistent_context),
                # not the browser.new_context() headless.py otherwise uses —
                # Chromium only loads --load-extension into a real profile dir.
                ext_args = launch_args + [
                    f"--disable-extensions-except={_EXTENSION_DIR}",
                    f"--load-extension={_EXTENSION_DIR}",
                ]
                try:
                    context = await p.chromium.launch_persistent_context(
                        profile_dir, headless=False, executable_path=chrome_path,
                        args=ext_args, user_agent=_UA,
                    )
                except Exception as e:
                    logger.warning(f"hotstar_browser: extension launch failed ({e}) — retrying without it.")
                    context = None

            if context is None:
                browser = await p.chromium.launch(
                    headless=True, executable_path=headless.system_chromium_path(), args=launch_args,
                )
                context = await browser.new_context(user_agent=_UA)

            if cookies:
                try:
                    await context.add_cookies(_cookies_to_playwright(cookies))
                except Exception as e:
                    logger.warning(f"hotstar_browser: failed to load cookies into context: {e}")

            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => false });"
            )

            def on_request(request):
                url = request.url
                low = url.lower()
                if ".mpd" in low and not captured["mpd_url"]:
                    captured["mpd_url"] = url
                elif "license" in low and "hotstar" in low and not captured["license_url"]:
                    captured["license_url"] = url

            context.on("request", on_request)

            page = await context.new_page()
            try:
                await page.goto(page_url, timeout=30000, wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"hotstar_browser: page.goto warning: {e}")

            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            for selector in _PLAY_SELECTORS:
                try:
                    el = page.locator(selector).first
                    if await el.count() > 0:
                        await el.click(timeout=2000)
                        break
                except Exception:
                    continue

            start = time.time()
            while not captured["mpd_url"] and (time.time() - start) < timeout:
                await asyncio.sleep(0.5)

            if not captured["mpd_url"]:
                return None

            # A little extra time after the MPD shows up — the license
            # request usually follows within a second or two as the
            # player actually starts decrypting, but isn't guaranteed to
            # have fired the instant the manifest request does.
            extra_start = time.time()
            while not captured["license_url"] and (time.time() - extra_start) < 8:
                await asyncio.sleep(0.5)

            try:
                captured["title"] = await page.title()
            except Exception:
                pass

            return captured
        except Exception as e:
            logger.warning(f"hotstar_browser: capture failed: {e}")
            return None
        finally:
            try:
                if context:
                    await context.close()
            except Exception:
                pass
            shutil.rmtree(profile_dir, ignore_errors=True)
            if relay_runner:
                try:
                    await relay_runner.cleanup()
                except Exception:
                    pass
