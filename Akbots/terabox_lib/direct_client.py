# Akbots - Don't Remove Credit - @AkBots_Official
#
# TeraBoxDirectClient — third/last-resort fallback tier for Akbots/terabox.py.
#
# v2: replaced the original best-effort reconstruction (no reference
# implementation available at the time) with a faithful port of two real,
# maintained open-source projects the user provided for comparison:
#   - saahiyo-cloud/TeraBridge-api (downloader.py) — errno-level handling
#     (400810 rate-limit retry, errno 2 = auto-create root dir and retry,
#     batched /api/filemetas dlink resolution, locatedownload CDN-mirror
#     upgrade), and the token-scraping regexes.
#   - a SpideyBot-main fork's spideybot/downloaders/terabox_downloader.py
#     ("Based on the TeraBridge-api project" per its own docstring) — the
#     aiohttp-based class structure (matches this repo's convention;
#     TeraBridge itself uses httpx), exponential-backoff retry wrapper,
#     and recursive subfolder traversal (TeraBridge only lists root).
# Not tested against a live account/share in this environment (no network
# here to exercise it) — ported faithfully from both, but if a specific
# step 400s/errors in practice, report the exact errno/response and this
# can be adjusted; TeraBox's private API has no official docs and drifts.
#
# Same "owned account, transfer shared file(s) into it, pull a real dlink
# for YOUR copy" reasoning as before — see Akbots/terabox_lib/__init__.py
# and config.py's TERABOX_NDUS comment for why this tier exists at all.

import re
import json
import time
import random
import string
import logging
import asyncio
import urllib.parse

import aiohttp

logger = logging.getLogger(__name__)

APP_ID = "250528"
BASE_API = "https://dm.1024terabox.com"      # account-storage / write operations
BASE_PUBLIC = "https://www.terabox.com"      # public share/list (no login needed for this one)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

_BDSTOKEN_RE = re.compile(r'bdstoken["\']?\s*[:=]\s*["\']([a-f0-9]{32})["\']', re.IGNORECASE)
_JSTOKEN_RE = re.compile(r'jstoken["\']?\s*[:=]\s*["\'](.*?)["\']', re.IGNORECASE)
_JSTOKEN_FN_RE = re.compile(r'fn\s*\(\s*["\']([a-f0-9]{128})["\']\s*\)', re.IGNORECASE)

_SURL_MIN_LEN = 8
_LEADING_ONE_MAX_STRIPS = 4
_VALID_SURL = re.compile(r"^[A-Za-z0-9_-]+$")

VIDEO_EXTS = ('.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.3gp', '.mpg', '.mpeg', '.ts')


class TeraBoxDirectClientError(Exception):
    pass


def _rand_dirname() -> str:
    return "akbots_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def parse_surl(url: str) -> str:
    """Extract and clean the shorturl key (`surl`) from a TeraBox share
    link -- handles /s/1ABC..., ?surl=ABC..., and a bare surl, including
    TeraBox's habit of prepending a '1' to the path-form identifier."""
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
        if stripped.startswith("http"):
            raise ValueError(f"parse_surl: malformed input {url!r}")
        if _VALID_SURL.match(stripped) and len(stripped) >= _SURL_MIN_LEN:
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
        raise ValueError(f"parse_surl: cleaned surl {surl!r} is shorter than the {_SURL_MIN_LEN}-char minimum")
    return surl


class TeraBoxDirectClient:
    """One instance per (ndus cookie) -- see terabox_lib/__init__.py's lazy
    singleton. Not thread-safe across event loops, but the bot only ever
    runs one."""

    def __init__(self, ndus: str, cleanup_minutes: int = 30, max_retries: int = 3):
        if not ndus:
            raise TeraBoxDirectClientError("TERABOX_NDUS is not set.")
        self._ndus = ndus.strip()
        self._cleanup_minutes = max(1, cleanup_minutes)
        self._max_retries = max_retries
        self._bds_token = ""
        self._js_token = ""
        self._logid = ""
        self._headers = {
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": f"ndus={self._ndus}; lang=en_US",
        }

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(headers=self._headers)

    def _qp(self) -> str:
        return f"app_id={APP_ID}&web=1&channel=dubox&clienttype=0&jsToken={self._js_token}&dp-logid={self._logid}"

    async def _request(self, method: str, url: str, **kwargs):
        """Exponential-backoff retry on 5xx/network errors."""
        last_exc = None
        for attempt in range(self._max_retries):
            try:
                async with self._session() as session:
                    async with session.request(method, url, **kwargs) as resp:
                        if resp.status in (500, 502, 503, 504) and attempt < self._max_retries - 1:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        body = await resp.read()
                        return resp.status, body
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exc = e
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
        raise last_exc or TeraBoxDirectClientError(f"Request failed after {self._max_retries} attempts: {url}")

    async def _request_json(self, method: str, url: str, **kwargs) -> dict:
        status, body = await self._request(method, url, **kwargs)
        try:
            return json.loads(body)
        except Exception:
            raise TeraBoxDirectClientError(f"Non-JSON response (HTTP {status}) from {url}: {body[:200]!r}")

    # -- token auto-resolution ------------------------------------------
    async def _resolve_tokens(self):
        """Scrapes bdstoken/jsToken/logid from the account's /main page --
        needed for every write operation (transfer/create/filemetas)."""
        status, body = await self._request("GET", f"{BASE_API}/main")
        if status != 200:
            raise TeraBoxDirectClientError(f"/main returned HTTP {status} -- cookie may be invalid/expired.")
        html = urllib.parse.unquote(body.decode(errors="replace"))

        m = _BDSTOKEN_RE.findall(html)
        if not m:
            raise TeraBoxDirectClientError("Couldn't find bdstoken on /main -- cookie may be invalid/expired.")
        self._bds_token = m[0]

        m = _JSTOKEN_RE.findall(html)
        if m:
            arg = _JSTOKEN_FN_RE.search(urllib.parse.unquote(m[0]))
            if arg:
                self._js_token = arg.group(1)
        if not self._js_token:
            logger.info("terabox_lib: couldn't resolve jsToken -- some calls may be rejected.")

        for cookie_name in ("logid", "dp-logid"):
            m = re.search(rf'{cookie_name}["\']?\s*[:=]\s*["\']?(\d{{10,}})', html, re.IGNORECASE)
            if m:
                self._logid = m.group(1)
                break
        if not self._logid:
            self._logid = str(int(time.time() * 1000))

    # -- share/list (public, no transfer needed for this call) ---------
    async def _get_share_list(self, surl: str, dir_path: str = "/", root: int = 1, password: str = None) -> dict:
        url = (f"{BASE_PUBLIC}/share/list?app_id={APP_ID}&shorturl={surl}&root={root}"
               f"&dir={urllib.parse.quote(dir_path)}&order=time&desc=1&num=100&page=1&web=1")
        if password:
            url += f"&pwd={password}"
        data = await self._request_json("GET", url)
        if data.get("errno") != 0:
            raise TeraBoxDirectClientError(f"share/list errno {data.get('errno')}: {data.get('errmsg', '')}")
        return {
            "share_id": data.get("shareid") or data.get("share_id"),
            "uk": data.get("uk"),
            "title": data.get("title", ""),
            "file_list": data.get("list") or [],
        }

    # -- own-account directory listing / creation -----------------------
    async def _list_dir(self, path: str) -> list:
        url = (f"{BASE_API}/api/list?{self._qp()}&dir={urllib.parse.quote(path)}"
               f"&order=time&desc=1&num=1000&bdstoken={self._bds_token}")
        try:
            data = await self._request_json("GET", url)
        except Exception as e:
            logger.info(f"terabox_lib: _list_dir({path}) failed: {e}")
            return []
        return data.get("list") or [] if data.get("errno") == 0 else []

    async def _get_existing_files(self, path: str) -> dict:
        items = await self._list_dir(path)
        return {
            item.get("server_filename"): {
                "fs_id": str(item.get("fs_id", "")), "path": item.get("path", ""),
                "size": int(item.get("size", 0)),
            }
            for item in items if item.get("server_filename")
        }

    async def _ensure_dir(self, path: str):
        url = f"{BASE_API}/api/create?{self._qp()}&bdstoken={self._bds_token}"
        body = {"path": path, "isdir": "1", "block_list": "[]"}
        data = await self._request_json("POST", url, data=body)
        if data.get("errno") not in (0, -8):  # -8 = already exists
            raise TeraBoxDirectClientError(f"create_dir errno {data.get('errno')}: {data.get('errmsg', '')}")

    # -- transfer shared file(s) into the owned account ------------------
    async def _transfer_file(self, share_id, uk, fs_id: str, dest_path: str, _retried_dir=False, _retried_rate=False) -> dict:
        url = f"{BASE_API}/share/transfer?{self._qp()}&shareid={share_id}&from={uk}&bdstoken={self._bds_token}"
        body = {"fsidlist": f"[{fs_id}]", "path": dest_path, "ondup": "newcopy"}
        data = await self._request_json("POST", url, data=body)
        errno = data.get("errno", -1)

        if errno == 0:
            to_fs_id = ""
            try:
                extra = data.get("extra", {}).get("list", [])
                if extra:
                    to_fs_id = str(extra[0].get("to_fs_id", ""))
            except Exception:
                pass
            return {"status": "success", "to_fs_id": to_fs_id}
        if errno in (12, -33):  # already exists / already processing
            return {"status": "already_exists", "to_fs_id": ""}
        if errno == 400810 and not _retried_rate:  # transient rate-limit
            await asyncio.sleep(1.5)
            return await self._transfer_file(share_id, uk, fs_id, dest_path, _retried_dir, True)
        if errno == 2 and not _retried_dir:  # dest dir doesn't exist yet
            await self._ensure_dir(dest_path)
            return await self._transfer_file(share_id, uk, fs_id, dest_path, True, _retried_rate)

        raise TeraBoxDirectClientError(f"share_transfer errno {errno}: {data.get('errmsg', '')}")

    # -- download link for OUR copy, batched across many fs_ids --------
    async def _batch_get_dlinks(self, fs_ids: list) -> dict:
        dlink_map = {}
        for i in range(0, len(fs_ids), 100):  # TeraBox filemetas has a per-call cap
            chunk = fs_ids[i:i + 100]
            encoded = urllib.parse.quote(json.dumps(chunk))
            url = f"{BASE_API}/api/filemetas?{self._qp()}&fsids={encoded}&dlink=1&thumb=0&bdstoken={self._bds_token}"
            try:
                data = await self._request_json("GET", url)
                for entry in data.get("list") or data.get("info") or []:
                    fid, dlink = str(entry.get("fs_id", "")), entry.get("dlink", "")
                    if fid and dlink:
                        dlink_map[fid] = dlink
            except Exception as e:
                logger.info(f"terabox_lib: batch filemetas chunk failed: {e}")
        return dlink_map

    # -- best-effort faster/alternate CDN mirror for one dlink ---------
    async def _upgrade_with_locate(self, path: str):
        url = (f"{BASE_API}/rest/2.0/pcs/file?ant=1&app_id={APP_ID}&channel=0&check_blue=1"
               f"&clienttype=17&method=locatedownload&path={urllib.parse.quote(path)}&vip=2")
        try:
            status, body = await self._request("POST", url, content=b" =")
            data = json.loads(body)
            urls = data.get("urls") or []
            return urls[0].get("url") if urls else None
        except Exception as e:
            logger.debug(f"terabox_lib: locatedownload mirror lookup failed (non-fatal): {e}")
            return None

    # -- cleanup -----------------------------------------------------------
    async def _delete_path(self, path: str):
        url = f"{BASE_API}/api/filemanager?opera=delete&async=0&{self._qp()}&bdstoken={self._bds_token}"
        try:
            await self._request_json("POST", url, data={"filelist": json.dumps([path])})
        except Exception as e:
            logger.warning(f"terabox_lib: cleanup delete of {path} failed (will just sit in the account): {e}")

    def schedule_cleanup(self, path: str):
        """Fire-and-forget delayed delete. Note: if the bot process restarts
        before this fires, the scheduled cleanup is lost and the file sits
        in the account until the next manual sweep -- best-effort per-
        transfer timer, not a persisted job queue."""
        async def _later():
            await asyncio.sleep(self._cleanup_minutes * 60)
            await self._delete_path(path)
        asyncio.create_task(_later())

    # -- orchestration -------------------------------------------------
    async def resolve(self, share_url: str, password: str = None) -> list:
        """Runs the full chain and returns file dicts shaped like
        Akbots/terabox.py's other resolvers: name, download_link, size_str,
        size_bytes, thumb, stream_link, qualities. Recurses into
        subfolders if the share is a folder link, not just a single file."""
        if not self._bds_token:
            await self._resolve_tokens()

        surl = parse_surl(share_url)
        root = await self._get_share_list(surl, dir_path="/", root=1, password=password)
        share_id, uk = root["share_id"], root["uk"]

        file_list = []
        queue = [("/", 1)]
        while queue:
            current_dir, is_root = queue.pop(0)
            try:
                info = await self._get_share_list(surl, dir_path=current_dir, root=is_root, password=password)
            except TeraBoxDirectClientError:
                continue  # a subfolder failing shouldn't kill the whole batch
            for item in info["file_list"]:
                file_list.append(item)
                if int(item.get("isdir", 0)) == 1:
                    queue.append((item.get("path"), 0))

        if not file_list:
            raise TeraBoxDirectClientError("Share is empty or has no accessible files.")

        dest_path = f"/{_rand_dirname()}"
        await self._ensure_dir(dest_path)
        existing = await self._get_existing_files(dest_path)

        own_fs_ids = []  # (fs_id_in_own_account, filename, item)
        for item in file_list:
            if int(item.get("isdir", 0)) == 1:
                continue
            filename = item.get("server_filename", "download")
            fs_id = str(item.get("fs_id", ""))
            if not fs_id:
                continue
            if filename in existing:
                own_fs_ids.append((existing[filename]["fs_id"], filename, item))
                continue
            try:
                transfer = await self._transfer_file(share_id, uk, fs_id, dest_path)
            except TeraBoxDirectClientError as e:
                logger.info(f"terabox_lib: transfer failed for {filename}: {e}")
                continue
            to_fs_id = transfer.get("to_fs_id") or fs_id
            own_fs_ids.append((to_fs_id, filename, item))

        if not own_fs_ids:
            raise TeraBoxDirectClientError("Transferred nothing -- every file failed or the share was empty.")

        dlink_map = await self._batch_get_dlinks([fid for fid, _, _ in own_fs_ids])

        results = []
        for fid, filename, item in own_fs_ids:
            dlink = dlink_map.get(fid)
            if not dlink:
                continue
            mirror = await self._upgrade_with_locate(f"{dest_path}/{filename}")
            results.append({
                "name": filename,
                "download_link": mirror or dlink,
                "size_str": item.get("size_str") or "Unknown",
                "size_bytes": int(item.get("size") or 0),
                "thumb": (item.get("thumbs") or {}).get("url3") or "",
                "stream_link": "",
                "qualities": {},
            })

        self.schedule_cleanup(dest_path)

        if not results:
            raise TeraBoxDirectClientError("Transferred the file(s) but couldn't obtain a dlink for any of them.")
        return results
