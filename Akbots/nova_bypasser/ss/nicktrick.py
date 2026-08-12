import asyncio
import json
import logging
from typing import Optional
from urllib.parse import urlparse, urljoin

logger = logging.getLogger(__name__)

try:
    import curl_cffi.requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    curl_requests = None

TIMEOUT = 15

NICKTRICK_DOMAINS = [
    "vplink.in",
    "arolinks.com",
    "babylinks.in",
    "earnlinks.in",
    "get2short.com",
    "nowshort.com",
    "shrinkme.io",
    "shrinkme.click",
    "hypershort.com",
]

NICKTRICK_ENDPOINTS = [
    "/links?nicktrick={path}",
    "/api?url={path}",
    "/api?nicktrick={path}",
    "/api?id={path}",
    "/links?url={path}",
    "/redirect?to={path}",
    "/go?url={path}",
]

NICKTRICK_POST_ENDPOINTS = [
    ("/links", {"nicktrick": "{path}"}),
    ("/links", {"url": "{path}"}),
    ("/api", {"url": "{path}"}),
    ("/api", {"id": "{path}"}),
    ("/api", {"nicktrick": "{path}"}),
]


class NicktrickResolver:
    def __init__(self):
        self._session = None

    async def _ensure_session(self):
        if not HAS_CURL_CFFI:
            return
        if self._session is None:
            self._session = curl_requests.Session()
            self._session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/html,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            })

    async def resolve(self, url: str) -> Optional[str]:
        if not HAS_CURL_CFFI:
            return None

        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.strip("/")
        if not path:
            return None

        path_part = path.split("/")[0]

        await self._ensure_session()

        base = f"{parsed.scheme}://{parsed.netloc}"

        for endpoint in NICKTRICK_ENDPOINTS:
            target = base + endpoint.format(path=path_part)
            try:
                resp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._session.get(
                        target,
                        timeout=TIMEOUT,
                        impersonate="chrome",
                        allow_redirects=False,
                    )
                )
                result = self._parse_response(resp)
                if result and self._is_valid(result, url):
                    logger.debug(f"Nicktrick GET {target} -> {result}")
                    return result
            except Exception as e:
                logger.debug(f"Nicktrick GET {target} failed: {e}")

        for endpoint, data_template in NICKTRICK_POST_ENDPOINTS:
            target = base + endpoint
            data = {k: v.format(path=path_part) for k, v in data_template.items()}
            try:
                resp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._session.post(
                        target,
                        data=data,
                        timeout=TIMEOUT,
                        impersonate="chrome",
                        allow_redirects=False,
                    )
                )
                result = self._parse_response(resp)
                if result and self._is_valid(result, url):
                    logger.debug(f"Nicktrick POST {target} -> {result}")
                    return result
            except Exception as e:
                logger.debug(f"Nicktrick POST {target} failed: {e}")

        return None

    def _parse_response(self, resp) -> Optional[str]:
        ct = resp.headers.get("Content-Type", "")
        if "json" in ct:
            try:
                data = resp.json()
            except Exception:
                return None
            for key in ("url", "redirect", "destination", "link", "data", "result"):
                val = data.get(key)
                if val and isinstance(val, str) and val.startswith("http"):
                    return val
            if isinstance(data, dict):
                for val in data.values():
                    if isinstance(val, str) and val.startswith("http"):
                        return val
            return None
        body = resp.text.strip()
        if body.startswith("http"):
            return body
        location = resp.headers.get("Location") or resp.headers.get("location")
        if location and location.startswith("http"):
            return location
        return None

    @staticmethod
    def _is_valid(result: str, original_url: str) -> bool:
        if result == original_url:
            return False
        parsed = urlparse(result)
        if not parsed.netloc:
            return False
        return True

    async def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
