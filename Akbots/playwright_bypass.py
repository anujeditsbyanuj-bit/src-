# Akbots
# Headless-browser bypass tier — runs the bundled "Bypass Shortlinks"
# Tampermonkey/Violentmonkey userscript (Akbots/shortener_lib/userscripts/,
# ported from github.com/nOneCode4u/bypass-shortlinks, Unlicense) inside a
# real headless Chromium page via Playwright, for shortlink sites that
# Akbots/shortener_lib/bypasser.py's plain-HTTP dispatcher doesn't cover.
#
# WHY THIS EXISTS SEPARATELY FROM bypasser.py/ddl.py:
# bypasser.py resolves links with plain HTTP requests — fast, cheap, no
# browser needed — but it only works for sites whose redirect mechanism
# is reproducible from raw HTTP calls (an API call, a predictable
# encoded-URL scheme, etc.). The bypass-shortlinks userscript covers 400+
# *additional* sites, but it does so by scripting real page behavior in
# the browser (skipping JS countdown timers, auto-clicking "continue"
# buttons, reading obfuscated in-page variables via `unsafeWindow`) — that
# can't be reproduced with plain HTTP calls, it genuinely needs a real
# DOM + JS engine running the page. Hence: a real (headless) browser.
#
# WHAT THIS CANNOT DO:
#   - Sites gated by an actual CAPTCHA the userscript expects a *human* to
#     solve once (the userscript itself says so in its README) — this
#     will time out on those, same as it would in a real browser with
#     nobody watching.
#   - It's genuinely heavy: a real Chromium page load per link (seconds,
#     not milliseconds), real RAM/CPU. shortener_bypass.py only reaches
#     for this as a fallback, after bypasser.py/ddl.py have already been
#     tried and only if the domain isn't one they cover.
#   - Chromium must actually be installed (`playwright install chromium`)
#     — see requirements.txt / Dockerfile. If it isn't, or launch fails
#     for any reason (no system deps on a constrained host, etc.), every
#     function below fails soft (returns None) instead of raising, so the
#     rest of the bot is unaffected either way.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import re
import time
import asyncio
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    from config import PLAYWRIGHT_BYPASS_ENABLED, PLAYWRIGHT_BYPASS_TIMEOUT_SECONDS
except ImportError:
    PLAYWRIGHT_BYPASS_ENABLED = True
    PLAYWRIGHT_BYPASS_TIMEOUT_SECONDS = 30

_HERE = os.path.dirname(os.path.abspath(__file__))
_US_DIR = os.path.join(_HERE, "shortener_lib", "userscripts")
_EXTRA_DIR = os.path.join(_US_DIR, "extra_bypasses")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ═══════════════════════════════════════════════════════════════════════
# Domain routing — which URLs the bundled userscripts actually cover
# ═══════════════════════════════════════════════════════════════════════
# match_rules.txt: Tampermonkey @match glob syntax ("*://*.domain.com/*")
# include_rules.txt: @include regex syntax ("/^https?:\/\/...$/")
# extra_bypasses/*.user.js: each has its own @match/@include header lines
# Parsed once at import time; used purely as a routing decision (should we
# even bother spinning up a browser for this URL?) — never used for
# anything security-sensitive, so best-effort parsing is fine.
def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _glob_to_regex(glob: str):
    # Special-case the extremely common "*://*.domain.tld/*" shape:
    # browsers' own @match-pattern matching treats "*.domain.com" as also
    # matching bare "domain.com" (no subdomain) — a plain glob->regex
    # conversion doesn't, since the literal "." before the domain would
    # then have nothing to match. A placeholder token (swapped back in
    # after escaping) turns that spot into a genuinely optional-subdomain
    # group instead.
    _TOKEN = "\x00SUBDOMAIN\x00"
    glob = glob.replace("://*.", f"://{_TOKEN}", 1)
    parts = [re.escape(p) for p in glob.split("*")]
    pattern = ".*".join(parts).replace(re.escape(_TOKEN), r"(?:[^/]*\.)?")
    try:
        return re.compile("^" + pattern + "$", re.IGNORECASE)
    except re.error:
        return None


def _load_match_rules(path: str) -> list:
    patterns = []
    for line in _read(path).splitlines():
        line = line.strip()
        if line:
            p = _glob_to_regex(line)
            if p:
                patterns.append(p)
    return patterns


def _load_include_rules(path: str) -> list:
    patterns = []
    for line in _read(path).splitlines():
        line = line.strip()
        if line.startswith("/") and line.endswith("/") and len(line) > 2:
            try:
                patterns.append(re.compile(line[1:-1], re.IGNORECASE))
            except re.error:
                continue
    return patterns


def _load_extra_bypass_rules() -> list:
    patterns = []
    if not os.path.isdir(_EXTRA_DIR):
        return patterns
    for fname in sorted(os.listdir(_EXTRA_DIR)):
        if not fname.endswith(".user.js"):
            continue
        for line in _read(os.path.join(_EXTRA_DIR, fname)).splitlines():
            line = line.strip()
            if line.startswith("// @match"):
                rule = line[len("// @match"):].strip()
                p = _glob_to_regex(rule)
                if p:
                    patterns.append(p)
            elif line.startswith("// @include"):
                rule = line[len("// @include"):].strip()
                if rule.startswith("/") and rule.endswith("/") and len(rule) > 2:
                    try:
                        patterns.append(re.compile(rule[1:-1], re.IGNORECASE))
                    except re.error:
                        continue
    return patterns


_ROUTE_PATTERNS = None


def _route_patterns() -> list:
    global _ROUTE_PATTERNS
    if _ROUTE_PATTERNS is None:
        _ROUTE_PATTERNS = (
            _load_match_rules(os.path.join(_US_DIR, "match_rules.txt"))
            + _load_include_rules(os.path.join(_US_DIR, "include_rules.txt"))
            + _load_extra_bypass_rules()
        )
    return _ROUTE_PATTERNS


def is_playwright_bypass_site(url: str) -> bool:
    """True if the bundled userscript(s) claim to cover this URL."""
    for pattern in _route_patterns():
        try:
            if pattern.search(url):
                return True
        except Exception:
            continue
    return False


def supported_site_count() -> int:
    return len(_route_patterns())


# ═══════════════════════════════════════════════════════════════════════
# GM_* API shim + userscript bundle
# ═══════════════════════════════════════════════════════════════════════
# Tampermonkey/Violentmonkey inject these globals for userscripts; a plain
# Playwright page has none of them, so anything the script calls has to be
# faked here. GM_openInTab / GM_setClipboard / a direct location.replace()
# are the three ways these scripts typically hand back "here's the real
# link" — all three are wired to __ak_capture (exposed from Python,
# see playwright_bypass() below).
_SHIM_JS = r"""
(() => {
  if (window.__ak_shim_installed) return;
  window.__ak_shim_installed = true;
  const store = {};
  window.GM_setValue = (k, v) => { store[k] = v; };
  window.GM_getValue = (k, d) => (k in store ? store[k] : d);
  window.GM_addStyle = (css) => { try {
      const s = document.createElement('style'); s.textContent = css;
      (document.head || document.documentElement).appendChild(s);
  } catch (e) {} };
  window.GM_registerMenuCommand = () => {};
  window.GM_unregisterMenuCommand = () => {};
  window.GM_setClipboard = (text) => { try { window.__ak_capture && window.__ak_capture(String(text)); } catch (e) {} };
  window.GM_openInTab = (url) => {
    try { window.__ak_capture && window.__ak_capture(String((url && url.url) ? url.url : url)); } catch (e) {}
    return { close(){}, onclose: null };
  };
  window.GM_xmlhttpRequest = (details) => {
    try {
      fetch(details.url, {
        method: details.method || 'GET',
        headers: details.headers || {},
        body: details.data,
        credentials: 'include',
      }).then(async (r) => {
        const text = await r.text().catch(() => '');
        details.onload && details.onload({ status: r.status, responseText: text, response: text, finalUrl: r.url });
      }).catch((e) => { details.onerror && details.onerror(e); });
    } catch (e) { details.onerror && details.onerror(e); }
    return { abort(){} };
  };
  window.unsafeWindow = window;

  try {
    const origReplace = window.location.replace.bind(window.location);
    window.location.replace = (url) => {
      try { window.__ak_capture && window.__ak_capture(String(url)); } catch (e) {}
      return origReplace(url);
    };
  } catch (e) {}
})();
"""

_bundle_cache = None


def _build_bundle() -> str:
    global _bundle_cache
    if _bundle_cache is not None:
        return _bundle_cache
    parts = [_SHIM_JS, _read(os.path.join(_US_DIR, "MonkeyConfig-Mod.js")),
             _read(os.path.join(_US_DIR, "Bypass_Shortlinks.user.js"))]
    if os.path.isdir(_EXTRA_DIR):
        for fname in sorted(os.listdir(_EXTRA_DIR)):
            if fname.endswith(".user.js"):
                parts.append(_read(os.path.join(_EXTRA_DIR, fname)))
    # userscript header blocks are `//`-commented, so they're harmless as
    # plain JS comments — no need to strip the ==UserScript== metadata.
    _bundle_cache = "\n;\n".join(p for p in parts if p)
    return _bundle_cache


# ═══════════════════════════════════════════════════════════════════════
# Browser lifecycle (one Chromium instance reused across requests)
# ═══════════════════════════════════════════════════════════════════════
_playwright = None
_browser = None
_launch_failed = False
_lock = asyncio.Lock()


async def _get_browser():
    global _playwright, _browser, _launch_failed
    if _browser is not None:
        return _browser
    if _launch_failed:
        return None
    async with _lock:
        if _browser is not None or _launch_failed:
            return _browser
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("playwright_bypass: `playwright` package not installed — headless-browser bypass disabled.")
            _launch_failed = True
            return None

        # Reuses Akbots/headless.py's shared self-install helper instead of
        # duplicating it — on Docker deploys the Dockerfile already ran
        # `playwright install --with-deps chromium` so this is a no-op; on
        # non-Docker hosts (Procfile/buildpack Render/Railway, Replit) it
        # installs Chromium on first use here, same as headless.py does for
        # its own JS-rendering fallback (shared cache — whichever module
        # runs first pays the one-time install cost, the other reuses it).
        try:
            from Akbots.headless import _ensure_chromium
            await asyncio.wait_for(_ensure_chromium(), timeout=45)
        except Exception:
            pass  # best-effort — launch() below will just fail if this didn't help

        try:
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
        except Exception as e:
            logger.warning(f"playwright_bypass: Chromium launch failed ({e}) — run `playwright install --with-deps chromium`. Headless-browser bypass disabled for this process.")
            _launch_failed = True
            _browser = None
    return _browser


async def close_browser():
    """Optional cleanup hook for bot shutdown; safe to skip."""
    global _browser, _playwright
    try:
        if _browser is not None:
            await _browser.close()
        if _playwright is not None:
            await _playwright.stop()
    except Exception:
        pass
    _browser = _playwright = None


# ═══════════════════════════════════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════════════════════════════════
async def playwright_bypass(url: str, timeout: int = None) -> str:
    """Runs the bundled userscript(s) against `url` in a real headless
    Chromium page and returns the first captured "bypassed" URL, or None
    if nothing was captured / the browser tier is unavailable."""
    if not PLAYWRIGHT_BYPASS_ENABLED:
        return None
    browser = await _get_browser()
    if browser is None:
        return None

    timeout = timeout or PLAYWRIGHT_BYPASS_TIMEOUT_SECONDS
    original_host = urlparse(url).netloc
    captured = {"url": None}

    async def _on_capture(value):
        if value and not captured["url"]:
            captured["url"] = value

    context = None
    try:
        context = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        await page.expose_function("__ak_capture", _on_capture)
        await page.add_init_script(_build_bundle())

        def _on_nav(frame):
            if frame == page.main_frame:
                new_host = urlparse(frame.url).netloc
                if new_host and new_host != original_host and not captured["url"]:
                    captured["url"] = frame.url

        page.on("framenavigated", _on_nav)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        except Exception as e:
            logger.info(f"playwright_bypass: goto({url}) raised (continuing to poll anyway): {e}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not captured["url"]:
            await asyncio.sleep(0.5)

    except Exception as e:
        logger.warning(f"playwright_bypass: run failed for {url}: {e}")
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass

    result = captured["url"]
    if result and urlparse(result).netloc and urlparse(result).netloc != original_host:
        return result
    return None
