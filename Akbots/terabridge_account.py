"""
TeraBox account-transfer bridge — ported from TeraBridge-api's core
downloader.py + api/account_pool.py + api/redis_client.py
(https://github.com/saahiyo-cloud/TeraBridge-api).

Fundamentally different failure mode from the rest of Akbots/terabox.py's
cookie-less tiers (direct page scrape → guest-mode → teradownloader.com +
headless Chromium): those three never log in anywhere and only ever read
a *public* share. This module instead logs into a real TeraBox account
using its NDUS session cookie, TRANSFERS the shared file into that
account's own `/cloudvids` storage folder (TeraBox's `share/transfer`
API — the same "save to my cloud" action the TeraBox app/site does), then
resolves a direct `dlink` from the account's own copy via `filemetas` (and
upgrades it to a nearer CDN mirror via `locatedownload` when possible).

Because the file becomes a first-class file in *your* account rather than
someone else's share, this survives cases where TeraBox's share-page HTML
changes shape (breaking the scrape tier) or a share's guest/wap endpoints
get locked down — at the cost of needing a real account. A premium
account is strongly recommended: free accounts get bandwidth-throttled
and hit tiny daily transfer/storage caps, so this tier would fail almost
as often as it helps.

Configuration (see config.py):
  - TERABOX_NDUS_COOKIE   Single account's raw cookie string
                          ("ndus=...; PANWEB=1"). Sufficient on its own —
                          bdstoken/jsToken/logid are all auto-resolved
                          from this cookie (TeraBridge's "Dynamic Token
                          Resolution" feature), you do NOT need to hunt
                          those down manually in devtools.
  - TERABOX_ACCOUNTS      Optional: multiple accounts to rotate between,
                          as a JSON array of {"id","cookie"} objects, or
                          comma-separated "id:cookie" pairs. Only useful
                          together with Redis below — without Redis only
                          a single account (TERABOX_NDUS_COOKIE, or the
                          first entry here) is ever used, no rotation.
  - UPSTASH_REDIS_REST_URL / _TOKEN
                          Optional. When set, the account pool (health
                          state, round-robin last-used order) is stored
                          in Upstash Redis so rotation and unhealthy-
                          account tracking survive process restarts and
                          work across multiple bot instances. Without
                          Redis, pool state lives in this process's
                          memory only and resets on restart — fine for a
                          single account, weak for real rotation.
  - TERABOX_MAX_STORED_FILES
                          Files kept in the account's /cloudvids folder
                          before the oldest are auto-deleted to free up
                          storage (default 50). This is what keeps a
                          free/limited-storage account from filling up
                          after enough transfers — see prune step below.
  - TERABOX_MAX_REQUESTS_PER_HOUR / TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS
                          Ban-avoidance guard rails: caps how often any
                          one account is picked per rolling hour, and
                          enforces a minimum gap between two consecutive
                          uses of the same account, so multi-account
                          rotation actually spreads risk instead of just
                          spreading load evenly (which can still get a
                          single account flagged).
  - TERABOX_ACCOUNT_COOLDOWN_SECONDS / TERABOX_MAX_ACCOUNT_RETRIES
                          When a request hits a ban-like signal on the
                          current account (forbidden, frequency-limited,
                          session kicked), that account is put on a
                          self-expiring cooldown and the request retries
                          with a different account automatically, up to
                          TERABOX_MAX_ACCOUNT_RETRIES accounts total.
                          Cookie-dead accounts still get permanently
                          marked unhealthy (mark_account_unhealthy) —
                          cooldown is only for transient/ban-like
                          signals, not for a genuinely expired cookie.

If nothing is configured, `resolve_via_account()` raises immediately and
cheaply so terabox.py's existing no-login chain runs exactly as before —
this tier is purely additive.
"""
import os
import re
import json
import time
import random
import asyncio
import urllib.parse

import httpx

from Akbots.direct_utils import safe_filename, VIDEO_EXTS

try:
    from config import (
        TERABOX_NDUS_COOKIE, TERABOX_ACCOUNTS_RAW, TERABOX_MAX_STORED_FILES,
        UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN,
        TERABOX_MAX_REQUESTS_PER_HOUR, TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS,
        TERABOX_ACCOUNT_COOLDOWN_SECONDS, TERABOX_MAX_ACCOUNT_RETRIES,
    )
except ImportError:
    TERABOX_NDUS_COOKIE = ""
    TERABOX_ACCOUNTS_RAW = ""
    TERABOX_MAX_STORED_FILES = 50
    UPSTASH_REDIS_REST_URL = ""
    UPSTASH_REDIS_REST_TOKEN = ""
    TERABOX_MAX_REQUESTS_PER_HOUR = 20
    TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS = 8
    TERABOX_ACCOUNT_COOLDOWN_SECONDS = 1800
    TERABOX_MAX_ACCOUNT_RETRIES = 3

BASE_PUBLIC = "https://www.terabox.com"
BASE_API = "https://dm.1024terabox.com"
ROOT_PATH = "/cloudvids"
UA = ("dubox;P2SP;2.2.91.249;dubox;4.2.0.1;I2404;android-android;16;"
      "JSbridge1.0.10;jointbridge;1.1.39;")

_SURL_MIN_LEN = 8
_LEADING_ONE_MAX_STRIPS = 4
_VALID_SURL = re.compile(r"^[A-Za-z0-9_-]+$")


def is_configured() -> bool:
    """Cheap upfront check so terabox.py can skip this tier's network
    calls entirely when no account is set up at all."""
    return bool(TERABOX_NDUS_COOKIE or TERABOX_ACCOUNTS_RAW)


# ── surl parsing (ported from downloader.py's parse_surl) ──────────────────

def _parse_surl(url: str) -> str:
    if not isinstance(url, str) or not url:
        raise ValueError("parse_surl: empty or non-string input")

    surl = None
    if "surl=" in url:
        surl = url.split("surl=", 1)[1].split("&", 1)[0]
    elif "/s/" in url:
        surl = url.split("/s/", 1)[1].split("?", 1)[0].split("#", 1)[0]
    else:
        stripped = url.strip()
        if "://" in stripped or "/" in stripped or "." in stripped:
            raise ValueError(f"parse_surl: no surl marker found in {url!r}")
        if not stripped.startswith("http") and _VALID_SURL.match(stripped) and len(stripped) >= _SURL_MIN_LEN:
            surl = stripped

    if not surl:
        raise ValueError(f"parse_surl: no surl found in {url!r}")

    surl = surl.rstrip("/").split("/")[-1]
    if not _VALID_SURL.match(surl):
        raise ValueError(f"parse_surl: extracted value {surl!r} contains invalid characters")

    if len(surl) > 22 and surl.startswith("1"):
        for _ in range(_LEADING_ONE_MAX_STRIPS):
            if not surl.startswith("1") or len(surl) - 1 < _SURL_MIN_LEN or len(surl) <= 22:
                break
            surl = surl[1:]

    if len(surl) < _SURL_MIN_LEN:
        raise ValueError(f"parse_surl: cleaned surl {surl!r} is shorter than {_SURL_MIN_LEN} chars")
    return surl


def _parse_cookies(cookie_str: str) -> dict:
    cookies = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


# ── Account pool: Upstash Redis if configured, else in-process memory ──────
# Mirrors api/account_pool.py + api/redis_client.py, but async throughout
# (upstash-redis's asyncio client) since this runs inside the bot's own
# asyncio event loop rather than a WSGI/Flask worker.

ACCOUNTS_HASH_KEY = "terabridge:accounts"

_redis = None
_redis_init_tried = False
_mem_accounts: dict | None = None  # fallback pool when Redis isn't configured


async def _get_redis():
    global _redis, _redis_init_tried
    if _redis_init_tried:
        return _redis
    _redis_init_tried = True
    if not (UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN):
        return None
    try:
        from upstash_redis.asyncio import Redis
        client = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)
        await client.ping()
        _redis = client
    except Exception:
        # Redis misconfigured/unreachable — fall back to in-memory pool
        # rather than breaking this whole tier over it.
        _redis = None
    return _redis


def _parse_accounts_env() -> dict:
    accounts = {}
    raw = (TERABOX_ACCOUNTS_RAW or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            for item in parsed:
                accounts[str(item["id"])] = {
                    "cookie": item["cookie"], "status": "healthy", "last_used": 0,
                }
        except Exception:
            for pair in raw.split(","):
                pair = pair.strip()
                if ":" in pair:
                    acc_id, cookie = pair.split(":", 1)
                    accounts[acc_id.strip()] = {
                        "cookie": cookie.strip(), "status": "healthy", "last_used": 0,
                    }
    if TERABOX_NDUS_COOKIE and "default" not in accounts:
        accounts["default"] = {"cookie": TERABOX_NDUS_COOKIE, "status": "healthy", "last_used": 0}
    return accounts


async def _seed_redis_if_empty(redis):
    try:
        existing = await redis.hgetall(ACCOUNTS_HASH_KEY) or {}
    except Exception:
        existing = {}
    if existing:
        return
    for acc_id, data in _parse_accounts_env().items():
        try:
            await redis.hset(ACCOUNTS_HASH_KEY, acc_id, json.dumps(data))
        except Exception:
            pass


async def _get_all_accounts() -> dict:
    redis = await _get_redis()
    if redis:
        try:
            raw = await redis.hgetall(ACCOUNTS_HASH_KEY) or {}
            if not raw:
                await _seed_redis_if_empty(redis)
                raw = await redis.hgetall(ACCOUNTS_HASH_KEY) or {}
            return {k: json.loads(v) for k, v in raw.items()}
        except Exception:
            pass  # fall through to memory pool below
    global _mem_accounts
    if _mem_accounts is None:
        _mem_accounts = _parse_accounts_env()
    return _mem_accounts


async def _save_account(acc_id: str, data: dict):
    redis = await _get_redis()
    if redis:
        try:
            await redis.hset(ACCOUNTS_HASH_KEY, acc_id, json.dumps(data))
            return
        except Exception:
            pass
    global _mem_accounts
    if _mem_accounts is None:
        _mem_accounts = {}
    _mem_accounts[acc_id] = data


def _account_available(data: dict, now: int) -> bool:
    """True if this account isn't dead (unhealthy), isn't mid-cooldown
    from a ban-like signal, isn't over its rolling-hour request cap, and
    hasn't been used more recently than the minimum inter-use gap."""
    if data.get("status") == "unhealthy":
        return False
    if data.get("status") == "cooldown" and now < data.get("cooldown_until", 0):
        return False
    window_start = data.get("window_start", 0)
    count = data.get("window_count", 0)
    if now - window_start < 3600 and count >= TERABOX_MAX_REQUESTS_PER_HOUR:
        return False
    if now - data.get("last_used", 0) < TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS:
        return False
    return True


def _bump_usage(data: dict, now: int) -> dict:
    """Advances the rolling-hour request window and records this use."""
    window_start = data.get("window_start", 0)
    if now - window_start >= 3600:
        data["window_start"] = now
        data["window_count"] = 1
    else:
        data["window_count"] = data.get("window_count", 0) + 1
    data["last_used"] = now
    # A fresh successful pick clears any stale cooldown flag left over
    # from a previous ban-like signal that has since expired.
    if data.get("status") == "cooldown" and now >= data.get("cooldown_until", 0):
        data["status"] = "healthy"
    return data


async def get_next_healthy_account(_exclude: set | None = None):
    """Round-robin among accounts that are healthy, not cooling down from
    a ban-like signal, under their rolling-hour request cap, and not used
    more recently than the minimum inter-use gap — in that priority
    order. `_exclude` lets resolve_via_account() skip accounts it already
    tried in this same request. Returns (None, None) if nothing
    qualifies; callers should treat that as "try again later", not
    "unconfigured"."""
    _exclude = _exclude or set()
    accounts = await _get_all_accounts()
    now = int(time.time())

    candidates = {k: v for k, v in accounts.items() if k not in _exclude}
    fully_available = {k: v for k, v in candidates.items() if _account_available(v, now)}

    if fully_available:
        pool = fully_available
    else:
        # Nothing fully clears every guard rail right now — fall back to
        # any account that's at least not unhealthy/cooling, rather than
        # hard-failing the whole tier just because everyone's slightly
        # warm. Still respects unhealthy/cooldown so a flagged account is
        # never touched early.
        pool = {
            k: v for k, v in candidates.items()
            if v.get("status") != "unhealthy"
            and not (v.get("status") == "cooldown" and now < v.get("cooldown_until", 0))
        }
    if not pool:
        return None, None

    acc_id, data = sorted(pool.items(), key=lambda kv: kv[1].get("last_used", 0))[0]
    data = _bump_usage(data, now)
    await _save_account(acc_id, data)
    return acc_id, data


async def mark_account_cooldown(acc_id: str, reason: str = "ban-like signal", seconds: int = None):
    """Softer than mark_account_unhealthy: temporarily benches an account
    that showed a ban/rate-limit-like signal, but lets the pool retry it
    automatically once the cooldown window passes — no human needed to
    flip it back to healthy."""
    if not acc_id:
        return
    seconds = seconds if seconds is not None else TERABOX_ACCOUNT_COOLDOWN_SECONDS
    accounts = await _get_all_accounts()
    data = accounts.get(acc_id)
    if not data:
        return
    data["status"] = "cooldown"
    data["cooldown_until"] = int(time.time()) + seconds
    data["cooldown_reason"] = reason
    await _save_account(acc_id, data)


async def mark_account_unhealthy(acc_id: str, reason: str = "unknown"):
    """Called when an account's cookie turns out to be expired/invalid,
    so the pool skips it on future requests instead of retrying a dead
    account every time. Re-run /api/admin-style config update (or just
    fix the env var and restart) to bring it back to 'healthy'."""
    if not acc_id:
        return
    accounts = await _get_all_accounts()
    data = accounts.get(acc_id)
    if not data:
        return
    data["status"] = "unhealthy"
    data["unhealthy_reason"] = reason
    data["unhealthy_at"] = int(time.time())
    await _save_account(acc_id, data)


# Substrings/patterns in Terabox error text or errno codes that indicate
# the ACCOUNT itself got flagged (forbidden, frequency-limited, session
# kicked) rather than a one-off/transient or share-specific problem.
# Matching one of these rotates to a different account and cools this one
# down instead of just failing the whole request.
_BAN_SIGNAL_ERRNOS = {-6, -9, 111, 112, 122, 400811, 400812, 400813}
_BAN_SIGNAL_PATTERNS = (
    "forbidden", "not login", "user is not exist", "frequency", "too many",
    "blocked", "risk control", "captcha",
)


def _is_ban_like(errno=None, text: str = "") -> bool:
    if errno in _BAN_SIGNAL_ERRNOS:
        return True
    lowered = (text or "").lower()
    return any(p in lowered for p in _BAN_SIGNAL_PATTERNS)


# ── Per-account HTTP session + resolved-token cache ─────────────────────────

_sessions: dict[str, httpx.AsyncClient] = {}
_tokens: dict[str, dict] = {}


def _headers() -> dict:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.8",
        "Referer": f"{BASE_API}/main?category=all&path=%2F",
        "X-Requested-With": "XMLHttpRequest",
    }


def _get_session(acc_id: str, cookie_str: str) -> httpx.AsyncClient:
    if acc_id not in _sessions:
        _sessions[acc_id] = httpx.AsyncClient(
            headers=_headers(), cookies=_parse_cookies(cookie_str),
            timeout=httpx.Timeout(30.0), http2=True,
        )
    return _sessions[acc_id]


def _qp(js_token: str, logid: str) -> str:
    return f"app_id=250528&web=1&channel=dubox&clienttype=0&jsToken={js_token}&dp-logid={logid}"


async def _resolve_tokens(session: httpx.AsyncClient) -> dict:
    """Auto-scrapes bdstoken/jsToken/logid from /main using just the
    login cookie — this is why only the NDUS cookie is required, nothing
    else needs to be pulled from devtools by hand."""
    r = await session.get(f"{BASE_API}/main")
    if r.status_code != 200:
        raise RuntimeError(f"terabox /main returned HTTP {r.status_code} (cookie likely expired)")

    text = r.text
    m1 = re.findall(r'bdstoken["\']?\s*[:=]\s*["\']([a-f0-9]{32})["\']', text, re.IGNORECASE)
    bds_token = m1[0] if m1 else None
    if not bds_token:
        raise RuntimeError("bdstoken not found in /main response — cookie is expired or invalid")

    js_token = ""
    m3 = re.findall(r'jstoken["\']?\s*[:=]\s*["\'](.*?)["\']', text, re.IGNORECASE)
    if m3:
        decoded = urllib.parse.unquote(m3[0])
        arg = re.search(r'fn\s*\(\s*["\']([a-f0-9]{128})["\']\s*\)', decoded, re.IGNORECASE)
        if arg:
            js_token = arg.group(1)

    logid = ""
    for name, val in r.cookies.items():
        if name.lower() == "logid":
            logid = val
            break

    return {"bds_token": bds_token, "js_token": js_token, "logid": logid}


async def _get_tokens(acc_id: str, session: httpx.AsyncClient) -> dict:
    if acc_id not in _tokens:
        _tokens[acc_id] = await _resolve_tokens(session)
    return _tokens[acc_id]


def _invalidate_tokens(acc_id: str):
    _tokens.pop(acc_id, None)


# ── File management: list / delete / prune ──────────────────────────────
# Keeps the account's /cloudvids folder from silently filling up storage
# over time — the exact "purani files delete karna" piece asked for.

async def _list_existing_files(session: httpx.AsyncClient, tokens: dict) -> dict:
    encoded_dir = urllib.parse.quote(ROOT_PATH)
    url = (f"{BASE_API}/api/list?{_qp(tokens['js_token'], tokens['logid'])}"
           f"&dir={encoded_dir}&order=time&desc=1&showempty=0&page=1&num=1000"
           f"&bdstoken={tokens['bds_token']}")
    existing = {}
    try:
        r = await session.get(url)
        data = r.json()
        if data.get("errno") == 0:
            for entry in data.get("list", []):
                name = entry.get("server_filename")
                existing[name] = {
                    "fs_id": str(entry.get("fs_id", "")),
                    "path": entry.get("path", ""),
                    "size": int(entry.get("size", 0)),
                    "time": int(entry.get("server_mtime") or entry.get("ctime") or 0),
                }
    except Exception:
        pass  # folder may not exist yet on a brand-new account — fine
    return existing


async def _delete_files(session: httpx.AsyncClient, tokens: dict, paths: list) -> bool:
    if not paths:
        return True
    url = f"{BASE_API}/api/filemanager?opera=delete&async=0&{_qp(tokens['js_token'], tokens['logid'])}&bdstoken={tokens['bds_token']}"
    try:
        r = await session.post(url, data={"filelist": json.dumps(paths)})
        return r.json().get("errno") == 0
    except Exception:
        return False


async def _prune_old_files_if_needed(session: httpx.AsyncClient, tokens: dict,
                                      existing_files: dict, max_files: int):
    """Oldest-first deletion once the folder passes max_files, so storage
    never grows unbounded no matter how many links get resolved."""
    if len(existing_files) <= max_files:
        return
    sorted_items = sorted(existing_files.items(), key=lambda kv: kv[1].get("time", 0))
    to_delete = sorted_items[: len(existing_files) - max_files]
    paths = [f.get("path") for _, f in to_delete if f.get("path")]

    chunk_size = 100
    for i in range(0, len(paths), chunk_size):
        chunk = paths[i:i + chunk_size]
        if await _delete_files(session, tokens, chunk):
            chunk_set = set(chunk)
            for name in [n for n, f in existing_files.items() if f.get("path") in chunk_set]:
                existing_files.pop(name, None)


# ── Core: transfer a shared file into the account, then resolve its dlink ──

class _AccountFault(Exception):
    """Raised when a failure looks like it's about THIS account specifically
    (dead cookie, forbidden, rate/frequency-limited by Terabox) rather than
    about the share link itself. resolve_via_account() catches this to
    cool the account down and retry with a different one instead of
    failing the whole request over one flagged account."""


async def _jitter():
    """Small randomized delay before hitting Terabox, so multiple requests
    in a row don't produce an obviously bot-like, perfectly-regular
    request pattern on any one account."""
    import asyncio as _a
    await _a.sleep(random.uniform(0.4, 1.3))


async def _transfer_one(session: httpx.AsyncClient, tokens: dict, item: dict,
                         share_id, uk, existing_files: dict) -> dict:
    filename = item.get("server_filename")
    fs_id = item.get("fs_id")
    result = {"filename": filename, "fs_id": None, "path": None, "error": None, "account_fault": False}

    if str(item.get("isdir")) == "1":
        result["error"] = "is a directory"
        return result

    my_fs_id, my_path = "", ""
    existing = existing_files.get(filename)
    if existing and existing["size"] == int(item.get("size", 0)):
        # Already transferred by a previous request — skip re-transfer,
        # just reuse the account's own copy.
        my_fs_id, my_path = existing["fs_id"], existing["path"]
    else:
        payload = {
            "fsidlist": f"[{fs_id}]", "path": ROOT_PATH, "shareid": str(share_id),
            "from": str(uk), "ondup": "newcopy", "bdstoken": tokens["bds_token"],
        }
        transfer_url = f"{BASE_API}/share/transfer?{_qp(tokens['js_token'], tokens['logid'])}&bdstoken={tokens['bds_token']}"
        try:
            tr = await session.post(transfer_url, data=payload)
            tres = tr.json()
            if tres.get("errno") == 400810:  # transient rate-limit, one retry
                import asyncio as _a
                await _a.sleep(1.5)
                tr = await session.post(transfer_url, data=payload)
                tres = tr.json()
        except Exception as e:
            result["error"] = f"transfer request failed: {e}"
            return result

        if tres.get("errno") == 2:  # ROOT_PATH doesn't exist yet — create + retry
            try:
                cr = await session.post(
                    f"{BASE_API}/api/create?{_qp(tokens['js_token'], tokens['logid'])}&bdstoken={tokens['bds_token']}",
                    data={"path": ROOT_PATH, "isdir": "1", "size": "0", "block_list": "[]", "method": "post"},
                )
                if cr.json().get("errno") in (0, -8):
                    tr = await session.post(transfer_url, data=payload)
                    tres = tr.json()
            except Exception:
                pass

        if tres.get("errno") not in (0, 4):
            errno = tres.get("errno")
            result["error"] = f"transfer failed (errno {errno})"
            result["account_fault"] = _is_ban_like(errno=errno, text=json.dumps(tres)[:200])
            return result

        extra_list = (tres.get("extra") or {}).get("list", [])
        if extra_list:
            my_fs_id = str(extra_list[0].get("to_fs_id", ""))
            dest_path = extra_list[0].get("to", "")
            if dest_path:
                filename = dest_path.split("/")[-1]
                my_path = dest_path

        if not my_fs_id:
            try:
                encoded_dir = urllib.parse.quote(ROOT_PATH)
                r_list = await session.get(
                    f"{BASE_API}/api/list?{_qp(tokens['js_token'], tokens['logid'])}"
                    f"&dir={encoded_dir}&order=time&desc=1&showempty=0&page=1&num=20&bdstoken={tokens['bds_token']}"
                )
                for entry in r_list.json().get("list", []):
                    entry_name = entry.get("server_filename", "")
                    if filename in entry_name or entry_name in filename:
                        my_fs_id = str(entry.get("fs_id", ""))
                        filename = entry_name
                        my_path = entry.get("path", "")
                        break
            except Exception:
                pass

    if not my_fs_id:
        result["error"] = "could not resolve transferred file's fs_id"
        return result

    result["filename"] = filename
    result["fs_id"] = my_fs_id
    result["path"] = my_path
    return result


async def _resolve_with_account(acc_id: str, acc_data: dict, url: str, max_files: int):
    """Does the actual transfer+resolve work against one specific account.
    Raises _AccountFault for anything that looks account-specific (so the
    caller rotates to another account and cools this one down) or a plain
    ValueError for anything that's about the share link itself (so the
    caller stops retrying — a different account won't fix an invalid
    link)."""
    await _jitter()
    session = _get_session(acc_id, acc_data["cookie"])
    try:
        tokens = await _get_tokens(acc_id, session)
    except Exception as e:
        _invalidate_tokens(acc_id)
        raise _AccountFault(f"account '{acc_id}' cookie rejected: {e}")

    surl = _parse_surl(url)
    list_url = (f"{BASE_PUBLIC}/share/list?app_id=250528&shorturl={surl}&root=1"
                f"&order=name&desc=0&showempty=0&web=1&page=1&num=100")
    try:
        r = await session.get(list_url)
        share_data = r.json()
    except Exception as e:
        raise ValueError(f"failed to query share list: {e}")

    if share_data.get("errno") != 0:
        errno = share_data.get("errno")
        if _is_ban_like(errno=errno, text=share_data.get("errmsg", "")):
            raise _AccountFault(f"account '{acc_id}' blocked from viewing shares (errno={errno})")
        raise ValueError(f"share link invalid/expired (errno={errno})")

    share_id = share_data.get("share_id")
    uk = share_data.get("uk")
    files_list = list(share_data.get("list") or [])
    if not files_list:
        raise ValueError("no files found in this share")

    existing_files = await _list_existing_files(session, tokens)
    await _prune_old_files_if_needed(session, tokens, existing_files, max_files)

    transferred = []
    for item in files_list:
        transferred.append(await _transfer_one(session, tokens, item, share_id, uk, existing_files))

    resolvable = [t for t in transferred if t.get("fs_id") and not t.get("error")]
    if not resolvable:
        if any(t.get("account_fault") for t in transferred):
            raise _AccountFault(f"account '{acc_id}' flagged during transfer (rate/frequency-limited or forbidden)")
        first_err = next((t["error"] for t in transferred if t.get("error")), "transfer produced no usable files")
        raise ValueError(first_err)

    # Batch-resolve direct dlinks for everything transferred successfully.
    dlink_map = {}
    fs_ids = [t["fs_id"] for t in resolvable]
    for i in range(0, len(fs_ids), 100):
        chunk = fs_ids[i:i + 100]
        encoded = urllib.parse.quote(json.dumps(chunk))
        try:
            mr = await session.get(
                f"{BASE_API}/api/filemetas?{_qp(tokens['js_token'], tokens['logid'])}"
                f"&fsids={encoded}&dlink=1&thumb=0&bdstoken={tokens['bds_token']}",
                timeout=20.0,
            )
            entries = mr.json().get("list", mr.json().get("info", []))
            for entry in entries:
                fid = str(entry.get("fs_id", ""))
                dl = entry.get("dlink", "")
                if fid and dl:
                    dlink_map[fid] = dl
        except Exception:
            pass

    # Upgrade to a nearer CDN mirror where possible (best-effort).
    async def _upgrade(t):
        path = t.get("path")
        if not path:
            return
        try:
            encoded_path = urllib.parse.quote(path)
            mr = await session.post(
                f"{BASE_API}/rest/2.0/pcs/file?ant=1&app_id=250528&channel=0&check_blue=1"
                f"&clienttype=17&method=locatedownload&path={encoded_path}&vip=2",
                content=" =", timeout=15.0,
            )
            if mr.status_code == 200:
                urls = mr.json().get("urls", [])
                if urls and urls[0].get("url"):
                    dlink_map[t["fs_id"]] = urls[0]["url"]
        except Exception:
            pass

    import asyncio as _a
    await _a.gather(*[_upgrade(t) for t in resolvable])

    results = []
    for t in resolvable:
        dlink = dlink_map.get(t["fs_id"])
        if dlink:
            results.append((dlink, safe_filename(t["filename"], "terabox_file")))

    if not results:
        raise ValueError("files transferred but no direct dlink could be resolved")
    return results


async def resolve_via_account(url: str, max_files: int = None):
    """Public entry point matching the same (dlink, filename) tuple-list
    contract as terabox.py's other `_fetch_via_*` tiers, so it drops
    straight into `_extract_terabox_files`'s try-chain.

    Tries up to TERABOX_MAX_ACCOUNT_RETRIES different accounts when a
    failure looks account-specific (dead cookie, forbidden, rate/
    frequency-limited) — each such account is put on cooldown so it isn't
    hammered again while flagged, and the next-least-recently-used
    healthy account is tried instead. A failure about the share link
    itself (invalid/expired/no files) is NOT retried across accounts,
    since switching accounts can't fix a bad link.

    Raises on total failure (unconfigured, no accounts available, share
    itself invalid, or every tried account was account-faulted) so the
    caller falls through to terabox.py's other tiers."""
    if not is_configured():
        raise ValueError("no TeraBox account configured (TERABOX_NDUS_COOKIE/TERABOX_ACCOUNTS unset)")

    max_files = max_files or TERABOX_MAX_STORED_FILES
    tried: set = set()
    last_account_fault = None

    for _ in range(max(1, TERABOX_MAX_ACCOUNT_RETRIES)):
        acc_id, acc_data = await get_next_healthy_account(_exclude=tried)
        if not acc_id:
            break
        tried.add(acc_id)
        try:
            return await _resolve_with_account(acc_id, acc_data, url, max_files)
        except _AccountFault as e:
            last_account_fault = e
            await mark_account_cooldown(acc_id, reason=str(e))
            continue  # try the next healthy account
        except ValueError:
            raise  # share-level problem — no point trying another account

    if last_account_fault:
        raise ValueError(f"all tried accounts were flagged: {last_account_fault}")
    raise ValueError("no healthy TeraBox account available in the pool right now")


# ── HLS streaming + transcoding handling (ported from downloader.py's
# action="s" path) ──────────────────────────────────────────────────────
# TeraBox transcodes a freshly-transferred video into HLS (.m3u8) lazily,
# in the background, on ITS OWN servers. Requesting a stream manifest
# before that finishes doesn't error — it comes back with errno 130
# ("transcoding in progress"), a genuinely different state from a real
# failure. This tries every resolution from highest to lowest (a lower
# resolution sometimes finishes transcoding first), and — only if the
# caller opts into `wait=True` — polls with a delay until either a
# manifest is ready or `max_wait_seconds` elapses, instead of treating
# errno 130 as a hard error.
#
# Not part of terabox.py's default download tier: Akbots delivers files
# by streaming raw bytes into Telegram (stream_download/upload_file),
# which a browser-only .m3u8 manifest can't feed directly. This is
# exposed as a standalone entry point for anything that wants the raw
# HLS link itself (e.g. an admin/power-user command, or a future local
# streaming-proxy route signed with terabox.py's _sign_token()/
# _verify_token()).
STREAM_QUALITIES = ("M3U8_AUTO_1080", "M3U8_AUTO_720", "M3U8_AUTO_480", "M3U8_AUTO_360")

# errno values TeraBox's streaming API returns that mean "transcoding not
# done yet" (retryable) vs. genuinely fatal for this file.
_ERRNO_TRANSCODING_IN_PROGRESS = 130
_ERRNO_FORMAT_UNSUPPORTED = {31066}
_ERRNO_PATH_INVALID = {31341, 31023}


async def _try_stream_quality(session: httpx.AsyncClient, tokens: dict, path: str, stype: str):
    """One HLS manifest request at a single resolution. Returns
    (ok, errno_or_none, text_or_error)."""
    res_val = "1080p" if "1080" in stype else ("720p" if "720" in stype else ("480p" if "480" in stype else "360p"))
    encoded_path = urllib.parse.quote(path)
    url = (
        f"{BASE_API}/api/streaming?{_qp(tokens['js_token'], tokens['logid'])}"
        f"&path={encoded_path}&type={stype}&bdstoken={tokens['bds_token']}"
        f"&isplayer=1&check_blue=1&clienttype=1&resolution={res_val}"
    )
    try:
        r = await session.get(url, timeout=20.0)
        if r.status_code == 200 and "#EXTM3U" in r.text:
            return True, 0, r.text
        errno = None
        try:
            errno = r.json().get("errno")
        except Exception:
            pass
        return False, errno, r.text[:200]
    except Exception as e:
        return False, -1, str(e)


async def resolve_stream_via_account(url: str, quality: str = None, wait: bool = False,
                                      max_wait_seconds: int = 120, poll_interval: int = 10):
    """HLS-streaming counterpart to resolve_via_account(): transfers the
    first video file in the share into the account (same as the download
    tier) and resolves an HLS (.m3u8) manifest instead of a direct dlink.

    Returns a dict:
      {"status": "ready", "quality": <M3U8_AUTO_xxx>, "m3u8": <manifest text>, "filename": ...}
      {"status": "transcoding", "filename": ..., "message": ...}   (errno 130, wait=False)
    Raises ValueError on anything that isn't a transcoding-in-progress
    state (invalid share, no account, unsupported format, etc.) — same
    contract as resolve_via_account().
    """
    if not is_configured():
        raise ValueError("no TeraBox account configured (TERABOX_NDUS_COOKIE/TERABOX_ACCOUNTS unset)")

    acc_id, acc_data = await get_next_healthy_account()
    if not acc_id:
        raise ValueError("no healthy TeraBox account available in the pool right now")

    await _jitter()
    session = _get_session(acc_id, acc_data["cookie"])
    try:
        tokens = await _get_tokens(acc_id, session)
    except Exception as e:
        _invalidate_tokens(acc_id)
        await mark_account_cooldown(acc_id, reason=f"cookie rejected: {e}")
        raise ValueError(f"account '{acc_id}' cookie rejected: {e}")

    surl = _parse_surl(url)
    list_url = (f"{BASE_PUBLIC}/share/list?app_id=250528&shorturl={surl}&root=1"
                f"&order=name&desc=0&showempty=0&web=1&page=1&num=100")
    r = await session.get(list_url)
    share_data = r.json()
    if share_data.get("errno") != 0:
        raise ValueError(f"share link invalid/expired (errno={share_data.get('errno')})")

    share_id, uk = share_data.get("share_id"), share_data.get("uk")
    files_list = [f for f in (share_data.get("list") or []) if str(f.get("isdir")) != "1"]
    video_files = [f for f in files_list if str(f.get("server_filename", "")).lower().endswith(VIDEO_EXTS)]
    if not video_files:
        raise ValueError("no video files found in this share (HLS streaming only applies to video)")

    existing_files = await _list_existing_files(session, tokens)
    transferred = await _transfer_one(session, tokens, video_files[0], share_id, uk, existing_files)
    if transferred.get("error") or not transferred.get("path"):
        raise ValueError(transferred.get("error") or "transfer failed")

    path = transferred["path"]
    filename = transferred["filename"]
    stream_types = [quality] if quality else list(STREAM_QUALITIES)
    deadline = time.time() + max_wait_seconds
    hit_transcoding = False

    while True:
        for stype in stream_types:
            ok, errno, text = await _try_stream_quality(session, tokens, path, stype)
            if ok:
                return {"status": "ready", "quality": stype, "m3u8": text, "filename": filename}
            if errno == _ERRNO_TRANSCODING_IN_PROGRESS:
                hit_transcoding = True
                continue  # try the next (lower) resolution — it may already be done
            if errno in _ERRNO_FORMAT_UNSUPPORTED:
                raise ValueError(f"file format not supported for HLS streaming (errno {errno})")
            if errno in _ERRNO_PATH_INVALID:
                raise ValueError(f"streamed file path error (errno {errno})")
            # unknown errno on this resolution — just try the next one

        if not (hit_transcoding and wait and time.time() < deadline):
            break
        await asyncio.sleep(poll_interval)

    if hit_transcoding:
        return {
            "status": "transcoding", "filename": filename,
            "message": "TeraBox is still transcoding this file to HLS (errno 130) — retry in a bit.",
        }
    raise ValueError("no streamable HLS quality available for this file")
