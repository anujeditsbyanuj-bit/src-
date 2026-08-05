# Akbots - Don't Remove Credit - @AkBots_Official
#
# TeraBoxDirectClient — third/last-resort fallback tier for Akbots/terabox.py,
# used only after xAPIverse and terabox.beer both fail. Trimmed port of the
# approach used by several open-source TeraBox web-API clients (TeraBox
# exposes no public/official API for this — every endpoint below is the
# private endpoint the terabox.com web app itself calls, reverse-engineered
# from browser network traffic, so it can break if TeraBox changes their
# frontend).
#
# Why this tier exists at all: TeraBox will not hand out a real `dlink` for
# a share you don't own once the "quick" share/list call doesn't return one
# (throttled/expired/anti-hotlink shares). The workaround real TeraBox
# clients use is: transfer the shared file(s) into an account YOU own, then
# request the download link for your own copy. That needs a logged-in
# session — the `ndus` cookie of a dedicated TeraBox account (see
# config.py's TERABOX_NDUS; use a throwaway account, not your personal one).
#
# Flow: short_url_info (fetch the share page for jsToken/logid) ->
# short_url_list (share/list — sometimes already has a usable dlink) ->
# create_dir (ensure a scratch folder exists in the owned account) ->
# share_transfer (copy the shared fs_id(s) into that folder) ->
# poll task (transfer can come back with a taskid on multi-file shares) ->
# get_remote_dir (list the scratch folder to recover the new fs_id(s)) ->
# download (api/download -> dlink on the OWNED copy) ->
# get_locatedownload_mirrors (best-effort alternate CDN host, non-fatal).
#
# Gated entirely on config.TERABOX_NDUS being set — see terabox_lib/__init__.py.

import re
import json
import time
import random
import string
import logging
import asyncio

import aiohttp

logger = logging.getLogger(__name__)

APP_ID = "250528"
BASE = "https://www.terabox.com"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class TeraBoxDirectClientError(Exception):
    pass


def _rand_dirname() -> str:
    return "akbots_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


class TeraBoxDirectClient:
    """One instance per (ndus cookie) — see terabox_lib/__init__.py's lazy
    singleton. Not thread-safe across event loops, but the bot only ever
    runs one."""

    def __init__(self, ndus: str, cleanup_minutes: int = 30):
        if not ndus:
            raise TeraBoxDirectClientError("TERABOX_NDUS is not set.")
        self._ndus = ndus.strip()
        self._cleanup_minutes = max(1, cleanup_minutes)
        self._js_token: str | None = None
        self._logid: str | None = None
        self._headers = {
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": f"ndus={self._ndus}; lang=en_US",
        }

    # ── session helper ────────────────────────────────────────────────
    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(headers=self._headers)

    # ── step 1: short_url_info — fetch the share page for jsToken/logid ──
    async def short_url_info(self, share_url: str) -> dict:
        async with self._session() as s:
            async with s.get(share_url, allow_redirects=True, timeout=20) as r:
                html = await r.text(errors="replace")
                final_url = str(r.url)

        m_surl = re.search(r"[?&]surl=([\w-]+)", final_url)
        if not m_surl:
            # some domains put it in the path (/s/<surl>) rather than a query param
            m_surl = re.search(r"/s/1?([\w-]+)", final_url)
        if not m_surl:
            raise TeraBoxDirectClientError("Couldn't extract surl from the share link.")
        surl = m_surl.group(1)

        m_token = re.search(r"fn%28%22(.*?)%22%29", html) or re.search(r'window\.jsToken\s*=\s*"(.*?)"', html)
        js_token = m_token.group(1) if m_token else ""
        m_logid = re.search(r"dp-logid=(\d+)", html)
        logid = m_logid.group(1) if m_logid else str(int(time.time() * 1000))

        self._js_token, self._logid = js_token, logid
        return {"surl": surl, "js_token": js_token, "logid": logid, "domain": re.sub(r"^https?://", "", final_url).split("/")[0]}

    # ── step 2: short_url_list — share/list; may already carry a dlink ──
    async def short_url_list(self, surl: str, password: str | None = None) -> dict:
        params = {
            "app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0",
            "jsToken": self._js_token or "", "dp-logid": self._logid or "",
            "shorturl": surl, "root": "1",
        }
        if password:
            params["pwd"] = password

        async with self._session() as s:
            async with s.get(f"{BASE}/share/list", params=params, timeout=20) as r:
                data = await r.json(content_type=None)

        if data.get("errno") not in (0, None):
            raise TeraBoxDirectClientError(f"share/list errno {data.get('errno')}: {data.get('errmsg', '')}")
        files = data.get("list") or []
        if not files:
            raise TeraBoxDirectClientError("share/list returned no files (dead/private/expired share).")
        return {
            "files": files,
            "shareid": data.get("share_id") or (files[0].get("share_id") if files else None),
            "uk": data.get("uk") or (files[0].get("uk") if files else None),
        }

    # ── step 3: create_dir — scratch folder in the owned account ────────
    async def create_dir(self, path: str) -> None:
        params = {"app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0",
                  "jsToken": self._js_token or "", "dp-logid": self._logid or "", "a": "commit"}
        body = {"path": path, "isdir": "1", "block_list": "[]"}
        async with self._session() as s:
            async with s.post(f"{BASE}/api/create", params=params, data=body, timeout=20) as r:
                data = await r.json(content_type=None)
        # errno -8 == "already exists", harmless.
        if data.get("errno") not in (0, -8):
            raise TeraBoxDirectClientError(f"create_dir errno {data.get('errno')}: {data.get('errmsg', '')}")

    # ── step 4: share_transfer — copy shared fs_id(s) into our account ──
    async def share_transfer(self, shareid, uk, fs_ids: list, dest_path: str) -> dict:
        params = {"app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0",
                  "jsToken": self._js_token or "", "dp-logid": self._logid or "",
                  "shareid": shareid, "from": uk, "ondup": "newcopy", "async": "1"}
        body = {"fsidlist": json.dumps([int(f) for f in fs_ids]), "path": dest_path}
        async with self._session() as s:
            async with s.post(f"{BASE}/share/transfer", params=params, data=body, timeout=30) as r:
                data = await r.json(content_type=None)
        if data.get("errno") != 0:
            raise TeraBoxDirectClientError(f"share_transfer errno {data.get('errno')}: {data.get('errmsg', '')}")
        return data

    # ── step 5: poll_task — only needed if the transfer came back async ──
    async def poll_task(self, taskid: str, tries: int = 6, delay: float = 1.5) -> bool:
        if not taskid:
            return True  # small/sync transfers don't hand back a taskid at all
        params = {"app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0",
                  "jsToken": self._js_token or "", "dp-logid": self._logid or "",
                  "method": "query", "taskid": taskid}
        for _ in range(tries):
            try:
                async with self._session() as s:
                    async with s.get(f"{BASE}/share/transfer", params=params, timeout=15) as r:
                        data = await r.json(content_type=None)
                status = str(data.get("status") or data.get("task_status") or "").lower()
                if status in ("finished", "success", "3") or data.get("errno") == 0:
                    return True
            except Exception as e:
                logger.debug("terabox_lib: poll_task check failed: %s", e)
            await asyncio.sleep(delay)
        return False  # timed out — caller falls through to a re-list attempt anyway

    # ── step 6: get_remote_dir — recover the new fs_id in our own folder ──
    async def get_remote_dir(self, path: str) -> list:
        params = {"app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0",
                  "jsToken": self._js_token or "", "dp-logid": self._logid or "",
                  "dir": path, "order": "time", "desc": "1", "showempty": "0"}
        async with self._session() as s:
            async with s.get(f"{BASE}/api/list", params=params, timeout=20) as r:
                data = await r.json(content_type=None)
        if data.get("errno") != 0:
            raise TeraBoxDirectClientError(f"get_remote_dir errno {data.get('errno')}: {data.get('errmsg', '')}")
        return data.get("list") or []

    # ── step 7: download — dlink for OUR copy of the file ────────────────
    async def download(self, fs_id) -> str | None:
        params = {"app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0",
                  "jsToken": self._js_token or "", "dp-logid": self._logid or "", "type": "dlink"}
        body = {"fidlist": json.dumps([int(fs_id)])}
        async with self._session() as s:
            async with s.post(f"{BASE}/api/download", params=params, data=body, timeout=20) as r:
                data = await r.json(content_type=None)
        dlinks = data.get("dlink") or []
        if not dlinks:
            return None
        return dlinks[0].get("dlink")

    # ── step 8 (best-effort): faster/alternate CDN mirror for the dlink ──
    async def get_locatedownload_mirrors(self, fs_id) -> str | None:
        params = {"app_id": APP_ID, "method": "locatedownload", "fidlist": json.dumps([int(fs_id)]),
                  "jsToken": self._js_token or "", "dp-logid": self._logid or ""}
        try:
            async with self._session() as s:
                async with s.get(f"{BASE}/rest/2.0/pcs/file", params=params, timeout=15) as r:
                    data = await r.json(content_type=None)
            urls = data.get("urls") or []
            return urls[0].get("url") if urls else None
        except Exception as e:
            logger.debug("terabox_lib: locatedownload mirror lookup failed (non-fatal): %s", e)
            return None

    # ── cleanup — delete the transferred copy after N minutes ───────────
    async def delete_path(self, path: str) -> None:
        params = {"app_id": APP_ID, "web": "1", "channel": "dubox", "clienttype": "0",
                  "jsToken": self._js_token or "", "dp-logid": self._logid or "", "opera": "delete"}
        body = {"filelist": json.dumps([path])}
        try:
            async with self._session() as s:
                async with s.post(f"{BASE}/api/filemanager", params=params, data=body, timeout=20) as r:
                    await r.json(content_type=None)
        except Exception as e:
            logger.warning("terabox_lib: cleanup delete of %s failed (will just sit in the account): %s", path, e)

    def schedule_cleanup(self, path: str) -> None:
        """Fire-and-forget delayed delete. Note: if the bot process restarts
        before this fires, the scheduled cleanup is lost and the file sits
        in the account until the next manual/periodic sweep — this is a
        best-effort per-transfer timer, not a persisted job queue."""
        async def _later():
            await asyncio.sleep(self._cleanup_minutes * 60)
            await self.delete_path(path)
        asyncio.create_task(_later())

    # ── orchestration ─────────────────────────────────────────────────
    async def resolve(self, share_url: str, password: str | None = None) -> list[dict]:
        """Runs the full chain and returns file dicts shaped exactly like
        Akbots/terabox.py's other resolvers: name, download_link, size_str,
        size_bytes, thumb, stream_link, qualities."""
        info = await self.short_url_info(share_url)
        listing = await self.short_url_list(info["surl"], password=password)
        files = listing["files"]

        # If share/list already handed back usable dlinks (no transfer
        # needed), skip the whole owned-account dance entirely.
        if all(f.get("dlink") for f in files):
            return [_to_result(f, f.get("dlink")) for f in files]

        shareid, uk = listing.get("shareid"), listing.get("uk")
        if not shareid or uk is None:
            raise TeraBoxDirectClientError("share/list didn't return shareid/uk — can't transfer.")

        fs_ids = [f["fs_id"] for f in files if f.get("fs_id")]
        if not fs_ids:
            raise TeraBoxDirectClientError("No fs_id in share/list response.")

        dest_path = f"/{_rand_dirname()}"
        await self.create_dir(dest_path)

        transfer = await self.share_transfer(shareid, uk, fs_ids, dest_path)
        await self.poll_task(transfer.get("taskid"))

        remote_files = await self.get_remote_dir(dest_path)
        if not remote_files:
            raise TeraBoxDirectClientError("Transfer reported success but the scratch folder is empty.")

        results = []
        for rf in remote_files:
            rf_fs_id = rf.get("fs_id")
            dlink = await self.download(rf_fs_id) if rf_fs_id else None
            if not dlink:
                continue
            mirror = await self.get_locatedownload_mirrors(rf_fs_id)
            results.append(_to_result(rf, mirror or dlink))

        # Best-effort cleanup regardless of how many files actually resolved
        # a dlink, so a partial failure doesn't leave the scratch folder
        # behind forever.
        self.schedule_cleanup(dest_path)

        if not results:
            raise TeraBoxDirectClientError("Transferred the file(s) but couldn't obtain a dlink for any of them.")
        return results


def _to_result(file_info: dict, dlink: str) -> dict:
    return {
        "name": file_info.get("server_filename") or file_info.get("name") or "download",
        "download_link": dlink,
        "size_str": file_info.get("size_str") or "Unknown",
        "size_bytes": int(file_info.get("size") or 0),
        "thumb": (file_info.get("thumbs") or {}).get("url3") or file_info.get("thumbnail") or "",
        "stream_link": "",
        "qualities": {},
    }
