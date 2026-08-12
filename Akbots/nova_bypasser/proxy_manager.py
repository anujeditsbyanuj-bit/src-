"""
Proxy Manager — ported from the Nova-Link-Bypasser-Bot repo's
bypass/proxy_manager.py. Handles proxy rotation for bypass requests, so
adlink/shortener sites can't just blocklist this bot's one server IP.

Setup (all optional — with nothing set, everything runs on a direct
connection exactly like before this was added):
    PROXY_LIST = "http://user:pass@host1:port,http://user:pass@host2:port"
    WEBSHARE_API_KEY = "..."     (webshare.io rotating proxies)
    PROXYSCRAPE_API  = "..."     (proxyscrape.com)
    NOVA_BYPASS_FREE_PROXIES = "true"  — opt-in to falling back to free
        public proxies (unreliable, only use if you have nothing else)

Unlike the original, loading is lazy: nothing hits the network at import
time. The proxy list is only fetched the first time get_proxy() is
actually called, and only if a source is configured.
"""

import os
import random
import time
import logging
import requests
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class ProxyManager:
    """Rotating proxy manager. Automatically removes dead proxies and
    rotates on failure."""

    def __init__(self):
        self.proxies: List[str] = []
        self.dead_proxies: set = set()
        self.proxy_fail_count: Dict = {}
        self.last_refresh: float = 0
        self.refresh_interval: int = 3600  # re-fetch proxies every 1 hour
        self._loaded = False

    def _load_proxies(self):
        loaded = []

        proxy_list_env = os.environ.get("PROXY_LIST", "")
        if proxy_list_env:
            loaded = [p.strip() for p in proxy_list_env.split(",") if p.strip()]
            logger.info(f"[nova_bypasser/proxy] Loaded {len(loaded)} proxies from PROXY_LIST env")

        webshare_key = os.environ.get("WEBSHARE_API_KEY", "")
        if webshare_key and not loaded:
            loaded.extend(self._fetch_webshare(webshare_key))

        proxyscrape_key = os.environ.get("PROXYSCRAPE_API", "")
        if proxyscrape_key and not loaded:
            loaded.extend(self._fetch_proxyscrape(proxyscrape_key))

        if not loaded and os.environ.get("NOVA_BYPASS_FREE_PROXIES", "").strip().lower() in ("1", "true", "yes"):
            logger.warning("[nova_bypasser/proxy] No proxy config found — fetching free public proxies (opted in, less reliable)")
            loaded.extend(self._fetch_free_proxies())

        self.proxies = loaded
        self.dead_proxies.clear()
        self.proxy_fail_count.clear()
        self.last_refresh = time.time()
        self._loaded = True
        if self.proxies:
            logger.info(f"[nova_bypasser/proxy] Total proxies available: {len(self.proxies)}")

    def _fetch_webshare(self, api_key: str) -> List[str]:
        try:
            resp = requests.get(
                "https://proxy.webshare.io/api/v2/proxy/list/",
                headers={"Authorization": f"Token {api_key}"},
                params={"mode": "direct", "page": 1, "page_size": 100},
                timeout=10,
            )
            proxies = [
                f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                for p in resp.json().get("results", [])
            ]
            logger.info(f"[nova_bypasser/proxy] Fetched {len(proxies)} proxies from Webshare")
            return proxies
        except Exception as e:
            logger.error(f"[nova_bypasser/proxy] Webshare fetch failed: {e}")
            return []

    def _fetch_proxyscrape(self, api_key: str) -> List[str]:
        try:
            resp = requests.get(
                "https://api.proxyscrape.com/v3/free-proxy-list/get",
                params={
                    "request": "displayproxies", "protocol": "http", "timeout": 5000,
                    "country": "all", "ssl": "all", "anonymity": "elite", "apikey": api_key,
                },
                timeout=10,
            )
            proxies = [f"http://{line.strip()}" for line in resp.text.splitlines() if line.strip()]
            logger.info(f"[nova_bypasser/proxy] Fetched {len(proxies)} proxies from ProxyScrape")
            return proxies
        except Exception as e:
            logger.error(f"[nova_bypasser/proxy] ProxyScrape fetch failed: {e}")
            return []

    def _fetch_free_proxies(self) -> List[str]:
        try:
            resp = requests.get(
                "https://api.proxyscrape.com/v2/",
                params={
                    "request": "getproxies", "protocol": "http", "timeout": 5000,
                    "country": "all", "ssl": "all", "anonymity": "elite",
                },
                timeout=10,
            )
            proxies = [f"http://{line.strip()}" for line in resp.text.splitlines() if line.strip()]
            logger.info(f"[nova_bypasser/proxy] Fetched {len(proxies)} free proxies")
            return proxies
        except Exception as e:
            logger.error(f"[nova_bypasser/proxy] Free proxy fetch failed: {e}")
            return []

    def get_proxy(self) -> Optional[Dict]:
        """Random working proxy as a {"http":..., "https":...} dict for
        `requests`, or None (= direct connection) if none are configured."""
        if not self._loaded:
            self._load_proxies()
        elif time.time() - self.last_refresh > self.refresh_interval:
            self._load_proxies()

        alive = [p for p in self.proxies if p not in self.dead_proxies]
        if not alive:
            if self.dead_proxies:
                self._load_proxies()
                alive = [p for p in self.proxies if p not in self.dead_proxies]
            if not alive:
                return None

        proxy = random.choice(alive)
        return {"http": proxy, "https": proxy}

    def get_proxy_url(self) -> Optional[str]:
        proxy_dict = self.get_proxy()
        return proxy_dict["http"] if proxy_dict else None

    def mark_dead(self, proxy_url: str):
        self.proxy_fail_count[proxy_url] = self.proxy_fail_count.get(proxy_url, 0) + 1
        if self.proxy_fail_count[proxy_url] >= 3:
            self.dead_proxies.add(proxy_url)
            logger.debug(f"[nova_bypasser/proxy] Marked dead: {proxy_url[:30]}...")

    def mark_working(self, proxy_url: str):
        self.proxy_fail_count.pop(proxy_url, None)
        self.dead_proxies.discard(proxy_url)

    def get_cloudscraper(self, **kwargs):
        """cloudscraper.create_scraper(), with a proxy attached if any are configured."""
        import cloudscraper
        client = cloudscraper.create_scraper(allow_brotli=False, **kwargs)
        proxy = self.get_proxy()
        if proxy:
            client.proxies.update(proxy)
        return client

    def get_requests_session(self, headers: dict = None) -> requests.Session:
        """requests.Session(), with a proxy attached if any are configured."""
        session = requests.Session()
        proxy = self.get_proxy()
        if proxy:
            session.proxies.update(proxy)
        if headers:
            session.headers.update(headers)
        return session

    @property
    def status(self) -> str:
        total = len(self.proxies)
        dead = len(self.dead_proxies)
        return f"Proxies: {total - dead} alive / {dead} dead / {total} total"


# Import this in any nova_bypasser module that wants proxy support:
#   from Akbots.nova_bypasser.proxy_manager import proxy_manager
#   session = proxy_manager.get_requests_session(headers={...})
proxy_manager = ProxyManager()
