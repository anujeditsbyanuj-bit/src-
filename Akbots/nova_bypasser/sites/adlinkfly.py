"""
AdLinkFly-family bypass — ported from the bypassx11-bot repo's
bypass_adlinkfly(). Many ad-locker/shortener sites run on the same
commercial "AdLinkFly" PHP script (just re-skinned/re-domained), so one
technique — extract the page's CSRF `_token`, POST it to `/links/go`
(or one of several alternate endpoints) — covers dozens of otherwise
unrelated-looking domains.
"""

import re
import time
import logging
import requests
from typing import Dict, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Domains known to run the AdLinkFly script (or a close clone of it).
ADLINKFLY_DOMAINS = [
    'lksfy.com', 'shrinkme.io', 'exe.io', 'earnow.online', 'za.gl', 'ouo.io', 'bc.vc',
    'adshort.co', 'adshort.in', 'cuty.io', 'shorte.st', 'adf.ly', 'droplink.co',
    'cpmshort.com', 'softurl.in', 'teraboxlinks.com', 'arolinks.com', 'shareus.io',
    'gplinks.in', 'gplinks.co', 'fc.lc', 'clksh.com', 'linkvertise.com',
    'linksly.co', 'clicksfly.com', 'shrink.me', 'earnl.com', 'cashurl.in',
    'paidurl.com', 'earncash.co', 'linkshrink.net', 'earnlnk.com', 'adlinkfly.com',
    'adsrt.com', 'flylink.me', 'shortz.me', 'urlmin.com', 'lnkr.co', 'mflinks.com',
    'mflinks.xyz', 'shortlink.top', 'yelink.co', 'adfoc.us', 'shortest.io',
    'shortzon.com', 'smartlinks.pro', 'upfiles.io', 'clik.pw', 'yolink.co',
    'rewl.co', 'shortgo.me',
]

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

_EXTRACT_PATTERNS = (
    r'content=["\'][0-9]*;?\s*url=([^"\'>\s]+)',
    r'window\.location(?:\.href)?\s*[=\(]\s*["\`]([^"\`\n]+)["\`]',
    r'location\.replace\(["\']([^"\']+)["\']',
    r'"(?:destination|url|link|redirect_url|longUrl|bypass_url)"\s*:\s*"([^"\\]+)"',
    r"'(?:destination|url|link|redirect_url)'\s*:\s*'([^'\\]+)'",
    r'href=["\']([^"\']+)["\'][^>]*>(?:Continue|Proceed|Skip|Go|Click|Download)',
    r'<a[^>]+class=["\'][^"\']*(?:btn|button|skip)[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
)


def is_adlinkfly_domain(url: str) -> bool:
    domain = urlparse(url).netloc.lower().replace('www.', '')
    return any(d in domain for d in ADLINKFLY_DOMAINS)


def _extract_from_html(html: str, original_url: str = '') -> Optional[str]:
    for pattern in _EXTRACT_PATTERNS:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            found = match.group(1).strip().strip('"\'')
            if found.startswith('http') and found != original_url:
                return found
    return None


async def bypass(url: str) -> Dict:
    """Sync under the hood (matches this file's `requests`-based sibling
    modules in nova_bypasser/sites/) — the 2-step AdLinkFly technique:
    extract the CSRF token from the landing page, POST it to /links/go
    (falling back to /go, /out, /redirect, /visit if that 404s)."""
    try:
        session = requests.Session()
        session.headers.update(_HEADERS)
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        code = parsed.path.strip('/')

        r1 = session.get(url, timeout=15, allow_redirects=True)

        if r1.url != url and not is_adlinkfly_domain(r1.url):
            return {"success": True, "bypassed_url": r1.url, "type": "adlinkfly_redirect"}

        html = r1.text
        soup = BeautifulSoup(html, 'html.parser')

        quick = _extract_from_html(html, url)
        if quick:
            return {"success": True, "bypassed_url": quick, "type": "adlinkfly_html_extraction"}

        token = ''
        token_input = soup.find('input', {'name': '_token'})
        if token_input:
            token = token_input.get('value', '')
        if not token:
            meta_csrf = soup.find('meta', {'name': 'csrf-token'})
            if meta_csrf:
                token = meta_csrf.get('content', '')
        if not token:
            token_match = re.search(r'_token["\'\s:=]+["\']([^"\']{20,})["\']', html)
            if token_match:
                token = token_match.group(1)

        if not token:
            return {"success": False, "error": "AdLinkFly bypass: no CSRF token found on page"}

        post_headers = {
            **_HEADERS,
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Referer': url,
            'Origin': base,
        }
        post_data = {'_token': token, 'code': code}
        time.sleep(1.5)  # matches upstream — small delay avoids naive bot-timing checks

        r2 = session.post(f"{base}/links/go", data=post_data, headers=post_headers, timeout=15)
        try:
            data = r2.json()
            for key in ('url', 'destination', 'link', 'redirect', 'target', 'bypass_url'):
                value = data.get(key, '')
                if isinstance(value, str) and value.startswith('http'):
                    return {"success": True, "bypassed_url": value, "type": "adlinkfly_api_token"}
        except ValueError:
            pass

        found = _extract_from_html(r2.text, url)
        if found:
            return {"success": True, "bypassed_url": found, "type": "adlinkfly_api_token_html"}

        for endpoint in ('/go', '/out', '/redirect', '/visit'):
            try:
                r3 = session.post(f"{base}{endpoint}", data=post_data, headers=post_headers, timeout=10)
                try:
                    data = r3.json()
                    for key in ('url', 'destination', 'link', 'redirect'):
                        value = data.get(key, '')
                        if isinstance(value, str) and value.startswith('http'):
                            return {"success": True, "bypassed_url": value, "type": f"adlinkfly_endpoint{endpoint}"}
                except ValueError:
                    pass
                found = _extract_from_html(r3.text, url)
                if found:
                    return {"success": True, "bypassed_url": found, "type": f"adlinkfly_endpoint{endpoint}"}
            except requests.RequestException:
                continue

        return {"success": False, "error": "AdLinkFly bypass: token extracted but no endpoint returned a link"}

    except Exception as e:
        logger.error(f"[adlinkfly] error: {e}")
        return {"success": False, "error": str(e)}
