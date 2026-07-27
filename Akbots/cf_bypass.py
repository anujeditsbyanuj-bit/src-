# Generic Cloudflare / anti-bot bypass helper — import this from ANY module
# that needs to get past a Cloudflare "Just a moment..." / Turnstile
# challenge (mediafire.py, direct_utils.py, headless.py callers, filmyzilla,
# terabox mirrors, etc.) instead of writing a one-off bypass per plugin.
#
# Two bypass paths, tried in order:
#   1. DrissionPage (Akbots/cf_lib/) — in-process, no extra
#      service to run, lightweight (just DrissionPage + Chromium).
#   2. Akbots/cf_lib/turnstile_solver.py ("Violetics Solver", ported
#      in-process from the standalone TurnstileSolver/ project) — a
#      Camoufox-driven headed-browser solver, stealthier against sites
#      that fingerprint DrissionPage/WebDriver, used as a fallback when #1
#      fails or isn't installed. Runs in THIS process now (no separate
#      service/port to manage); needs camoufox + a running Xvfb (see
#      entrypoint.sh) since real Cloudflare deployments refuse to mount
#      the Turnstile widget for a headless browser.
#
# If neither path is available, every function here degrades to returning
# None so callers just fall through to their next option (plain
# requests/aiohttp, gallery-dl, etc.) — nothing breaks, exactly like
# headless.py's own fallback behavior.

import os
import re
import sys
import time
import asyncio
import logging
from urllib.parse import urlparse
from typing import Optional, Dict, Any

import aiohttp

logger = logging.getLogger(__name__)

# In-process Camoufox solver — heavier optional dependency (camoufox +
# playwright==1.54.0 + a running Xvfb), so it's imported the same
# degrade-don't-break way as DrissionPage above: if it's not installed,
# _solver_bypass() below just returns None and callers fall through to
# their next option.
try:
    from Akbots.cf_lib.turnstile_solver import solve_challenge_async as _ts_solve_challenge
    _SOLVER_AVAILABLE = True
except ImportError:
    _ts_solve_challenge = None
    _SOLVER_AVAILABLE = False

# CloudflareBypasser (a small DrissionPage-driven Chromium wrapper) lives
# in Akbots/cf_lib/ — a proper submodule of this bot, ported in from a
# standalone source project (only this one file was needed; that project's
# own FastAPI microservice/server.py/tests/Dockerfile weren't kept, since
# nothing here runs them — see TurnstileSolver/ instead for an actual
# separate anti-bot microservice this bot talks to over HTTP).
try:
    from DrissionPage import ChromiumPage, ChromiumOptions
    from Akbots.cf_lib.CloudflareBypasser import CloudflareBypasser
    _CF_AVAILABLE = True
except ImportError:
    ChromiumPage = ChromiumOptions = CloudflareBypasser = None
    _CF_AVAILABLE = False

try:
    # Reuse headless.py's Chromium-path detection (replit.nix / system
    # Chromium vs. Playwright's own download) so both fallbacks agree on
    # which browser binary to launch instead of drifting out of sync.
    from Akbots.headless import system_chromium_path
except ImportError:
    system_chromium_path = None

# DrissionPage launches a real OS-level browser process; serialize launches
# so many concurrent callers don't all spin up Chromium at once and exhaust
# memory on small hosts (Render/Railway free tiers, Replit, etc.).
_lock = asyncio.Lock()

# --- Anti-bot challenge detection -----------------------------------------
# Signatures that show up when Cloudflare (or a similarly-behaving anti-bot
# layer) intercepts a request instead of the real site. Used by fetch() to
# decide, automatically, whether a plain request needs a browser bypass.
_CHALLENGE_MARKERS = (
    "just a moment",              # Cloudflare's interstitial title
    "cf-browser-verification",
    "cf_chl_opt",
    "cf-chl-",
    "challenges.cloudflare.com",
    "attention required! | cloudflare",
    "checking your browser before accessing",
    "__cf_chl_rt_tk",
)
_CHALLENGE_STATUS_CODES = (403, 503, 429)


def looks_like_challenge(status: int, headers: Dict[str, str], body_sample: str) -> bool:
    """Heuristic: does this response look like an anti-bot challenge page
    rather than the real content? Any plugin can call this directly on its
    own requests too, not just through fetch()."""
    if any(k.lower() == "cf-mitigated" for k in headers.keys()):
        return True
    if status in _CHALLENGE_STATUS_CODES:
        low = (body_sample or "").lower()
        if any(marker in low for marker in _CHALLENGE_MARKERS):
            return True
    low = (body_sample or "").lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


async def _solver_bypass(url: str, timeout: int) -> Optional[Dict[str, Any]]:
    """Fallback path: run the in-process Camoufox solver (Akbots/cf_lib/
    turnstile_solver.py) instead of DrissionPage. Same return shape as
    _run_bypass()'s dict, so bypass_cloudflare() can treat both paths
    interchangeably. Returns None (never raises) if camoufox isn't
    installed or the solve fails — matching this whole module's
    "degrade, don't break" behavior."""
    if not _SOLVER_AVAILABLE:
        return None
    try:
        data = await _ts_solve_challenge(url, req_id=url, timeout=timeout)
        cookies = {c["name"]: c["value"] for c in data.get("cookies", []) if c.get("name")}
        return {
            "cookies": cookies,
            "html": data.get("html", ""),
            "user_agent": data.get("user_agent", ""),
            "final_url": data.get("url", url),
        }
    except Exception as e:
        logger.info(f"cf_bypass: in-process Camoufox solve failed for {url} ({e}) — skipping this fallback")
        return None


# --- Multi-vendor anti-bot auto-detection ----------------------------------
# Not every "antibot protected" site is Cloudflare — this identifies WHICH
# anti-bot system (if any) a URL is sitting behind, from response headers,
# cookies, and body signatures, without needing to bypass anything first.
# Only Cloudflare can actually be bypassed by this module (see bypass_
# cloudflare()/fetch() above); the rest are reported so a plugin/admin knows
# *why* a site can't be scraped normally, instead of just seeing "403" and
# guessing.
_VENDOR_SIGNATURES = (
    # (vendor label, header-name substrings, cookie-name substrings, body substrings)
    ("Cloudflare", ("cf-ray", "cf-mitigated"), ("cf_clearance", "__cf_bm"),
     ("just a moment", "cf-browser-verification", "challenges.cloudflare.com", "attention required! | cloudflare")),
    ("DataDome", ("x-datadome",), ("datadome",),
     ("datadome", "geo.captcha-delivery.com")),
    ("Akamai Bot Manager", ("x-akamai-transformed",), ("_abck", "ak_bmsc", "bm_sv"),
     ("akamai", "ak-cache")),
    ("PerimeterX / HUMAN", ("x-px",), ("_px", "_pxvid", "_pxhd"),
     ("perimeterx", "please verify you are a human", "human security")),
    ("Imperva / Incapsula", ("x-iinfo", "x-cdn"), ("incap_ses", "visid_incap"),
     ("incapsula", "request unsuccessful. incapsula")),
    ("Kasada", (), ("kpsdk",), ("kasada", "kpsdk")),
    ("reCAPTCHA (Google)", (), (), ("recaptcha", "g-recaptcha")),
    ("hCaptcha", (), (), ("hcaptcha",)),
)

# Vendors this module can actually bypass in-process (see bypass_cloudflare()).
_BYPASSABLE_VENDORS = {"Cloudflare"}


async def detect(url: str, timeout: int = 15, proxy: Optional[str] = None) -> Dict[str, Any]:
    """
    Auto-detect whether `url` is behind anti-bot protection, and identify
    which vendor — without doing any bypass. One call from any plugin:

        from Akbots.cf_bypass import detect

        info = await detect(url)
        if info["protected"]:
            print(info["vendor"])          # e.g. "Cloudflare", "DataDome", ...
            print(info["bypassable"])      # True only for Cloudflare here

    Detection looks at response status, headers, cookies, and a sample of
    the body against known signatures for each vendor. If nothing matches
    but the status still looks blocked (403/503/429), it's reported as
    "protected" with vendor "Unknown".

    Returns:
        {
            "protected": bool,
            "vendor": str | None,        # e.g. "Cloudflare", "DataDome", "Unknown"
            "bypassable": bool,          # can Akbots.cf_bypass actually get past it?
            "status": int | None,
            "error": str | None,
        }
    """
    result: Dict[str, Any] = {
        "protected": False, "vendor": None, "bypassable": False,
        "status": None, "error": None,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=timeout),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
            ) as resp:
                status = resp.status
                headers = {k.lower(): v for k, v in resp.headers.items()}
                cookie_names = {c.lower() for c in resp.cookies.keys()}
                body = (await resp.text(errors="ignore"))[:20000].lower()
    except Exception as e:
        result["error"] = str(e)
        return result

    result["status"] = status

    for vendor, header_keys, cookie_keys, body_keys in _VENDOR_SIGNATURES:
        header_hit = any(any(hk in h for h in headers.keys()) for hk in header_keys) if header_keys else False
        cookie_hit = any(any(ck in c for c in cookie_names) for ck in cookie_keys) if cookie_keys else False
        body_hit = any(bk in body for bk in body_keys) if body_keys else False
        if header_hit or cookie_hit or body_hit:
            result["protected"] = True
            result["vendor"] = vendor
            result["bypassable"] = vendor in _BYPASSABLE_VENDORS
            return result

    if status in _CHALLENGE_STATUS_CODES:
        result["protected"] = True
        result["vendor"] = "Unknown"
        result["bypassable"] = False

    return result


# --- Per-hostname cookie cache ---------------------------------------------
# Avoids re-launching Chromium for every single request to the same site —
# the whole point of "seamless": bypass once, reuse the clearance cookies
# for every other call to that hostname until they expire. In-memory only
# (per-process); fine for a long-running bot process, resets on restart.
_COOKIE_CACHE: Dict[str, Dict[str, Any]] = {}
_COOKIE_TTL_SECONDS = 25 * 60  # Cloudflare clearance cookies are typically valid ~30 min


def _cache_get(hostname: str) -> Optional[Dict[str, Any]]:
    entry = _COOKIE_CACHE.get(hostname)
    if not entry:
        return None
    if time.time() - entry["cached_at"] > _COOKIE_TTL_SECONDS:
        _COOKIE_CACHE.pop(hostname, None)
        return None
    return entry


def _cache_set(hostname: str, cookies: Dict[str, str], user_agent: str) -> None:
    _COOKIE_CACHE[hostname] = {
        "cookies": cookies,
        "user_agent": user_agent,
        "cached_at": time.time(),
    }


def clear_cache(hostname: Optional[str] = None) -> None:
    """Drop cached clearance cookies — for one hostname, or all of them if
    called with no argument. Useful for a manual admin command/retry."""
    if hostname:
        _COOKIE_CACHE.pop(hostname, None)
    else:
        _COOKIE_CACHE.clear()


def is_available() -> bool:
    """Cheap check other modules can use before attempting a bypass, e.g.:

        if cf_bypass.is_available():
            ... offer a Cloudflare-protected source ...
    """
    return _CF_AVAILABLE


def _run_bypass(url: str, proxy: Optional[str], timeout: int, headless: bool,
                 extra_cookies: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Blocking worker — runs in a thread via bypass_cloudflare(); never call
    this directly from async code, DrissionPage is fully synchronous."""
    if not _CF_AVAILABLE:
        return None

    options = ChromiumOptions().auto_port()
    if headless:
        options.headless(True)
    if system_chromium_path:
        chromium_path = system_chromium_path()
        if chromium_path:
            options.set_browser_path(chromium_path)
    if proxy:
        options.set_proxy(proxy)
    # Common flags for running Chromium as root in containers (Docker/CI) —
    # same reasoning as headless.py's own launch args.
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-dev-shm-usage")

    driver = None
    try:
        driver = ChromiumPage(addr_or_opts=options)
        if extra_cookies:
            driver.set.cookies(extra_cookies)
        driver.get(url, timeout=timeout)

        CloudflareBypasser(driver, max_retries=5, log=False).bypass()

        cookies = {c["name"]: c["value"] for c in driver.cookies()}
        return {
            "cookies": cookies,
            "html": driver.html,
            "user_agent": driver.user_agent,
            "final_url": driver.url,
        }
    except Exception as e:
        logger.warning(f"cf_bypass: bypass failed for {url}: {e}")
        return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


async def bypass_cloudflare(
    url: str,
    proxy: Optional[str] = None,
    timeout: int = 30,
    headless: bool = True,
    extra_cookies: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get Cloudflare clearance cookies + rendered HTML for `url`, from any
    plugin in this repo. Never raises — returns None if DrissionPage/
    Chromium isn't available or the bypass fails, so callers can always
    safely fall through to their next option:

        from Akbots.cf_bypass import bypass_cloudflare

        result = await bypass_cloudflare("https://example.com/some/page")
        if result:
            cookies = result["cookies"]        # dict, ready for aiohttp/requests
            html = result["html"]               # fully rendered page source
            ua = result["user_agent"]           # matching User-Agent to reuse
        else:
            ... fall back to plain requests/aiohttp, or fail gracefully ...

    Args:
        url: The Cloudflare-protected page to load.
        proxy: Optional proxy URL (e.g. "http://user:pass@host:port").
        timeout: Seconds to wait for the initial page load.
        headless: Run Chromium headless (default True; some sites detect
            headless mode more aggressively — set False only if you also
            have an X server/virtual display available).
        extra_cookies: Optional cookies to seed the browser with before
            navigating (e.g. an existing session cookie). Only applies to
            the DrissionPage path — the TurnstileSolver fallback doesn't
            accept pre-seeded cookies.

    Returns:
        {"cookies": dict, "html": str, "user_agent": str, "final_url": str}
        or None if both paths are unavailable/fail.
    """
    if _CF_AVAILABLE:
        async with _lock:
            result = await asyncio.to_thread(
                _run_bypass, url, proxy, timeout, headless, extra_cookies
            )
        if result:
            return result
        logger.info(f"cf_bypass: DrissionPage bypass failed for {url}, trying TurnstileSolver fallback.")
    else:
        logger.info("cf_bypass: DrissionPage not installed, trying TurnstileSolver fallback.")

    # Fallback: the in-process Camoufox solver ("Violetics Solver", ported
    # from TurnstileSolver/) — stealthier against sites that fingerprint
    # DrissionPage, at the cost of needing camoufox installed + Xvfb
    # running (see entrypoint.sh). Silently skipped (returns None) if it's
    # not available — this function still never raises either way.
    return await _solver_bypass(url, timeout)


async def get_clearance(
    url: str, proxy: Optional[str] = None, force: bool = False, timeout: int = 30
) -> Optional[Dict[str, Any]]:
    """
    Cache-aware version of bypass_cloudflare(), for callers that only need
    cookies + a matching User-Agent to retry a *different* request (e.g. a
    file-streaming download) rather than the rendered HTML itself — this is
    what stream_download() in direct_utils.py uses.

    Checks the same per-hostname cookie cache fetch() uses first, so a
    download link and a page fetch for the same site share one bypass
    instead of each triggering their own Chromium launch.

        from Akbots.cf_bypass import get_clearance

        clearance = await get_clearance(download_url)
        if clearance:
            cookies, ua = clearance["cookies"], clearance["user_agent"]

    Returns {"cookies": dict, "user_agent": str} or None on failure /
    unavailability.
    """
    hostname = urlparse(url).netloc
    if not force:
        cached = _cache_get(hostname)
        if cached:
            return {"cookies": cached["cookies"], "user_agent": cached["user_agent"]}

    bypassed = await bypass_cloudflare(url, proxy=proxy, timeout=timeout)
    if not bypassed:
        return None

    _cache_set(hostname, bypassed["cookies"], bypassed["user_agent"])
    return {"cookies": bypassed["cookies"], "user_agent": bypassed["user_agent"]}


async def fetch(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Any = None,
    proxy: Optional[str] = None,
    force_bypass: bool = False,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    "Seamless" scraping entry point — one call, anti-bot handled
    automatically. Any plugin can use this in place of a raw aiohttp
    request when it doesn't know in advance whether a target is behind
    Cloudflare:

        from Akbots.cf_bypass import fetch

        res = await fetch("https://example.com/page")
        if res["ok"]:
            html = res["text"]
        else:
            ... give up / try a different source ...

    What it does under the hood:
      1. If this hostname was bypassed recently, reuse the cached
         clearance cookies + User-Agent — no browser launch at all.
      2. Otherwise, make a plain aiohttp request first (fast path — most
         URLs aren't behind Cloudflare, no need to pay the browser-launch
         cost for those).
      3. If the response looks like an anti-bot challenge page (or
         `force_bypass=True`), automatically run bypass_cloudflare() for
         that URL, cache the resulting cookies for this hostname, and
         retry the original request with them.
      4. Returns the final response either way — callers never have to
         write their own Cloudflare-detection or retry logic.

    Returns a dict:
        {
            "ok": bool,              # True if a normal-looking response came back
            "status": int | None,
            "text": str,             # response body (HTML/JSON/etc as text)
            "headers": dict,
            "cookies": dict,         # cookies used for the successful request
            "used_bypass": bool,     # whether a browser bypass was needed
            "error": str | None,
        }
    """
    hostname = urlparse(url).netloc
    result: Dict[str, Any] = {
        "ok": False, "status": None, "text": "", "headers": {},
        "cookies": {}, "used_bypass": False, "error": None,
    }

    cached = None if force_bypass else _cache_get(hostname)
    req_headers = dict(headers or {})
    cookies = {}
    if cached:
        cookies = cached["cookies"]
        req_headers.setdefault("User-Agent", cached["user_agent"])
        result["used_bypass"] = True

    async def _do_request(cookies_in: Dict[str, str]) -> Optional[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession(cookies=cookies_in) as session:
                async with session.request(
                    method, url, headers=req_headers, data=data,
                    proxy=proxy, timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    text = await resp.text(errors="ignore")
                    return {
                        "status": resp.status,
                        "text": text,
                        "headers": dict(resp.headers),
                    }
        except Exception as e:
            logger.warning(f"cf_bypass.fetch: request failed for {url}: {e}")
            return None

    first = await _do_request(cookies)
    if first is None:
        result["error"] = "request_failed"
        return result

    needs_bypass = force_bypass or (
        not cached and looks_like_challenge(first["status"], first["headers"], first["text"])
    )

    if not needs_bypass:
        result.update(ok=True, status=first["status"], text=first["text"],
                      headers=first["headers"], cookies=cookies)
        return result

    if not is_available():
        # Detected a challenge but can't do anything about it — return
        # what we got (the challenge page) rather than pretending success.
        result.update(status=first["status"], text=first["text"],
                      headers=first["headers"],
                      error="challenge_detected_but_cf_bypass_unavailable")
        return result

    bypassed = await bypass_cloudflare(url, proxy=proxy, timeout=max(timeout, 30))
    if not bypassed:
        result.update(status=first["status"], text=first["text"],
                      headers=first["headers"], error="bypass_failed")
        return result

    _cache_set(hostname, bypassed["cookies"], bypassed["user_agent"])
    req_headers["User-Agent"] = bypassed["user_agent"]

    second = await _do_request(bypassed["cookies"])
    if second is None:
        # Bypass worked but the retry request itself failed — the
        # already-rendered HTML from the bypass is still useful, hand it back.
        result.update(ok=True, status=200, text=bypassed["html"],
                      headers={}, cookies=bypassed["cookies"], used_bypass=True)
        return result

    result.update(ok=True, status=second["status"], text=second["text"],
                  headers=second["headers"], cookies=bypassed["cookies"], used_bypass=True)
    return result
