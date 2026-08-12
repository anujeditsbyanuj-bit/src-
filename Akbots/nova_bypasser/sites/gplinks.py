"""
GPLinks dedicated bypass — merged in from the Nova-Link-Bypasser-Bot repo's
bypass/gplinks_bypass.py (token-extraction + API-call technique), adapted
to this package's plain async-function style (see gdtot.py / sharerw.py)
instead of that repo's BaseBypass/register_bypass class framework, which
isn't present here.

Supports: gplinks.co, gplinks.in, gplinks.online, gplinks.net
"""

import re
import logging
import requests
from typing import Dict, Optional
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from ..proxy_manager import proxy_manager

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

_TOKEN_PATTERNS = [
    r'var\s+_0x\w+\s*=\s*["\']([a-zA-Z0-9_\-]+)["\']',
    r'token["\']?\s*[:=]\s*["\']([a-zA-Z0-9_\-]+)["\']',
    r'name=["\']token["\']\s+value=["\']([^"\']+)["\']',
    r'input[^>]*name=["\']_token["\']\s*value=["\']([^"\']+)["\']',
    r'"_token"\s*:\s*"([^"]+)"',
    r"'_token'\s*:\s*'([^']+)'",
    r'var\s+token\s*=\s*["\']([^"\']+)["\']',
    r'data-token=["\']([^"\']+)["\']',
]


def _extract_token(html: str) -> Optional[str]:
    for pattern in _TOKEN_PATTERNS:
        match = re.search(pattern, html, re.IGNORECASE)
        if match and len(match.group(1)) > 8:
            return match.group(1)
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for inp in soup.find_all('input', {'type': 'hidden'}):
            name = inp.get('name', '')
            value = inp.get('value', '')
            if ('token' in name.lower() or name == '_token') and value:
                return value
    except Exception:
        pass
    return None


def _call_api(session: requests.Session, domain: str, token: str, referer: str) -> Optional[str]:
    for endpoint in (f"{domain}/links/go", f"{domain}/go", f"{domain}/api/links/go", f"{domain}/link/go"):
        try:
            resp = session.post(
                endpoint,
                data={'token': token, '_token': token},
                headers={'X-Requested-With': 'XMLHttpRequest', 'Referer': referer, 'Origin': domain},
                timeout=20,
            )
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except ValueError:
                text = resp.text.strip()
                if text.startswith('http'):
                    return text
                continue
            for key in ('url', 'link', 'redirect', 'data', 'target'):
                val = data.get(key)
                if isinstance(val, str) and val.startswith('http'):
                    return val
                if isinstance(val, dict):
                    for k in ('url', 'link'):
                        if isinstance(val.get(k), str) and val[k].startswith('http'):
                            return val[k]
        except requests.RequestException as e:
            logger.debug(f"[gplinks] endpoint {endpoint} failed: {e}")
    return None


def _extract_from_html(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, 'html.parser')
    base_domain = urlparse(base_url).netloc
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        if not href or href.startswith('#'):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.netloc and parsed.netloc != base_domain and parsed.scheme in ('http', 'https'):
            return full
    meta = soup.find('meta', attrs={'http-equiv': re.compile('refresh', re.I)})
    if meta:
        match = re.search(r'url=([^\s;]+)', meta.get('content', ''), re.I)
        if match:
            return match.group(1).strip('"\'')
    return None


async def bypass(url: str) -> Dict:
    """Dedicated GPLinks bypass: try the token+API technique first, then
    fall back to scraping any external link straight out of the page."""
    session = requests.Session()
    session.headers.update(_HEADERS)
    proxy = proxy_manager.get_proxy()
    if proxy:
        session.proxies.update(proxy)
    try:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        response = session.get(url, timeout=20, allow_redirects=True)
        if response.status_code == 403:
            session.headers.update({'Referer': 'https://www.google.com/'})
            response = session.get(url, timeout=20, allow_redirects=True)
        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}"}

        html = response.text
        final_url = response.url

        token = _extract_token(html)
        if token:
            direct = _call_api(session, domain, token, final_url)
            if direct:
                return {"success": True, "bypassed_url": direct, "type": "gplinks_api_token"}

        direct = _extract_from_html(html, final_url)
        if direct:
            return {"success": True, "bypassed_url": direct, "type": "gplinks_html_extraction"}

        return {"success": False, "error": "GPLinks bypass failed - no token or link found"}

    except Exception as e:
        logger.error(f"[gplinks] error: {e}")
        return {"success": False, "error": str(e)}
