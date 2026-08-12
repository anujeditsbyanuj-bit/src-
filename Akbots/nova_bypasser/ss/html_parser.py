import logging
import re
from typing import Optional
from urllib.parse import urlparse, urljoin

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


META_REFRESH_RE = re.compile(
    r'<meta\s+[^>]*?http-equiv=["\']refresh["\'][^>]*?content=["\']\s*(\d+)\s*;\s*url\s*=\s*([^"\']+?)["\']',
    re.IGNORECASE,
)
META_REFRESH_RE2 = re.compile(
    r'<meta\s+[^>]*?content=["\']\s*(\d+)\s*;\s*url\s*=\s*([^"\']+?)["\'][^>]*?http-equiv=["\']refresh["\']',
    re.IGNORECASE,
)

WINDOW_LOCATION_RE = re.compile(
    r'(?:window|document)\.location\s*[=:]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
WINDOW_LOCATION_HREF_RE = re.compile(
    r'(?:window|document)\.location\.href\s*[=:]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
TOP_LOCATION_RE = re.compile(
    r'top\.location\s*[=:]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
REDIRECT_URL_RE = re.compile(
    r'(?:redirect|go|next|to|url|link|target|dest)\s*(?:=|:)\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
WINDOW_OPEN_RE = re.compile(
    r'window\.open\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
LOCATION_REPLACE_RE = re.compile(
    r'(?:window|document|self|top)?\.location\.replace\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
LOCATION_ASSIGN_RE = re.compile(
    r'(?:window|document|self|top)?\.location\.assign\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
WINDOW_OPEN_SELF_RE = re.compile(
    r'window\.open\s*\(\s*["\']([^"\']+)["\'][^)]*?["\']_self["\']',
    re.IGNORECASE,
)
WINDOW_NAVIGATE_RE = re.compile(
    r'window\.navigate\s*\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
SETTIMEOUT_REDIRECT_RE = re.compile(
    r'(?:setTimeout|setInterval)\s*\((?:function\s*\(\)\s*)?\{[^}]*?(?:window|document|self|top)?\.(?:location(?:\.href)?|location\.(?:replace|assign))\s*[=:]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)


class HTMLRedirectParser:
    @staticmethod
    def extract_meta_refresh(html: str, base_url: str) -> Optional[str]:
        if not HAS_BS4:
            match = META_REFRESH_RE.search(html) or META_REFRESH_RE2.search(html)
            if match:
                return urljoin(base_url, match.group(2).strip())
            return None

        try:
            soup = BeautifulSoup(html, "lxml")
            meta = soup.find(
                "meta", attrs={"http-equiv": lambda x: x and x.lower() == "refresh"}
            )
            if meta and meta.get("content"):
                content = meta["content"]
                match = re.search(r"url\s*=\s*([^\s\"']+)", content, re.IGNORECASE)
                if match:
                    return urljoin(base_url, match.group(1).strip())
        except Exception:
            match = META_REFRESH_RE.search(html) or META_REFRESH_RE2.search(html)
            if match:
                return urljoin(base_url, match.group(2).strip())
        return None

    @staticmethod
    def extract_js_redirect(html: str, base_url: str) -> Optional[str]:
        patterns = [
            WINDOW_LOCATION_HREF_RE,
            WINDOW_LOCATION_RE,
            TOP_LOCATION_RE,
            LOCATION_REPLACE_RE,
            LOCATION_ASSIGN_RE,
            WINDOW_OPEN_SELF_RE,
            WINDOW_NAVIGATE_RE,
            SETTIMEOUT_REDIRECT_RE,
            REDIRECT_URL_RE,
            WINDOW_OPEN_RE,
        ]
        for pattern in patterns:
            match = pattern.search(html)
            if match:
                url = match.group(1).strip()
                if url.startswith(("http://", "https://", "//")):
                    if url.startswith("//"):
                        parsed = urlparse(base_url)
                        url = f"{parsed.scheme}:{url}"
                    return url
                if url.startswith("/"):
                    return urljoin(base_url, url)
                if "." in url and "/" in url:
                    return urljoin(base_url, url)
        return None

    @staticmethod
    def extract_html_redirect(html: str, base_url: str) -> Optional[str]:
        result = HTMLRedirectParser.extract_meta_refresh(html, base_url)
        if result:
            return result
        return HTMLRedirectParser.extract_js_redirect(html, base_url)

    @staticmethod
    def extract_all(html: str, base_url: str) -> list[dict]:
        results = []
        meta = HTMLRedirectParser.extract_meta_refresh(html, base_url)
        if meta:
            results.append({"type": "meta_refresh", "url": meta})
        js = HTMLRedirectParser.extract_js_redirect(html, base_url)
        if js:
            results.append({"type": "js_redirect", "url": js})
        return results
