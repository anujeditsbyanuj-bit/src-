"""
Third-party bypass API engines — ported from the my-bypass-bot repo's
BypassService class. Unlike the rest of nova_bypasser/ (which does its
own HTML/CSS/JS extraction), these delegate to external "bypass as a
service" APIs — a useful extra fallback tier for whatever those services
already know how to handle, especially newer ad-lock schemes.

Wired into core.py's universal ladder as an extra tier between the
generic shortener/HTML extraction and the (optional) browser automation
/ AI fallback stages.
"""

import re
import logging
import aiohttp
from typing import Dict, Any, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/132.0.0.0 Mobile Safari/537.36"
)
_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _extract_destination_from_json(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    for key in ("destination", "result", "url", "bypassed", "bypassed_url", "direct_link", "link", "final", "final_url"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    nested = data.get("data")
    if isinstance(nested, dict):
        return _extract_destination_from_json(nested)
    return None


def _extract_from_html(text: str) -> Optional[str]:
    patterns = (
        r'"destination"\s*:\s*"([^"]+)"',
        r'"result"\s*:\s*"([^"]+)"',
        r'href="(https?://[^"]+)"[^>]*>\s*Continue',
        r'window\.location(?:\.href)?\s*=\s*[\'"](https?://[^\'"]+)[\'"]',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).replace("\\/", "/")
    return None


async def _read_json_safe(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except Exception:
        return None


async def bypass_city_get(url: str) -> Dict:
    api_url = f"https://bypass.city/bypass?bypass={quote(url, safe='')}"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as session:
            async with session.get(api_url, allow_redirects=False) as resp:
                if resp.status in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("Location")
                    if location and location.startswith(("http://", "https://")) and location.rstrip("/") != url.rstrip("/"):
                        return {"success": True, "bypassed_url": location, "type": "bypass_city_redirect"}

                body = await resp.text(errors="ignore")
                data = await _read_json_safe(resp)
                destination = _extract_destination_from_json(data) or _extract_from_html(body)
                if destination and destination.rstrip("/") != url.rstrip("/"):
                    return {"success": True, "bypassed_url": destination, "type": "bypass_city_get"}
                return {"success": False, "error": f"Bypass.city GET returned no destination (HTTP {resp.status})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def bypass_city_api(url: str) -> Dict:
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as session:
            headers = {"Content-Type": "application/json", "Origin": "https://bypass.city", "Referer": "https://bypass.city/"}
            async with session.post("https://api2.bypass.city/bypass", headers=headers, json={"url": url}, allow_redirects=False) as resp:
                data = await _read_json_safe(resp)
                destination = _extract_destination_from_json(data)
                if destination and destination.rstrip("/") != url.rstrip("/"):
                    return {"success": True, "bypassed_url": destination, "type": "bypass_city_api"}
                if resp.status >= 400:
                    return {"success": False, "error": f"Bypass.city API HTTP {resp.status}"}
                return {"success": False, "error": "Bypass.city API returned no destination"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def adbypass_mirror(url: str) -> Dict:
    api_url = f"https://adbypass.org/bypass?bypass={quote(url, safe='')}"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as session:
            async with session.get(api_url, allow_redirects=False) as resp:
                if resp.status in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("Location")
                    if location and location.startswith(("http://", "https://")) and location.rstrip("/") != url.rstrip("/"):
                        return {"success": True, "bypassed_url": location, "type": "adbypass_mirror_redirect"}

                data = await _read_json_safe(resp)
                destination = _extract_destination_from_json(data)
                if not destination:
                    body = await resp.text(errors="ignore")
                    destination = _extract_from_html(body)
                if destination and destination.rstrip("/") != url.rstrip("/"):
                    return {"success": True, "bypassed_url": destination, "type": "adbypass_mirror"}
                return {"success": False, "error": f"adbypass.org mirror failed (HTTP {resp.status})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def bypass_vip(url: str) -> Dict:
    api_url = f"https://api.bypass.vip/bypass?url={quote(url, safe='')}"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as session:
            async with session.get(api_url, allow_redirects=False) as resp:
                data = await _read_json_safe(resp)
                if isinstance(data, dict):
                    if data.get("status") == "success":
                        destination = data.get("result")
                        if isinstance(destination, str) and destination.startswith(("http://", "https://")):
                            return {"success": True, "bypassed_url": destination, "type": "bypass_vip"}
                    message = data.get("message")
                    if isinstance(message, str):
                        return {"success": False, "error": message}
                return {"success": False, "error": f"Bypass.vip failed (HTTP {resp.status})"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def bypass_via_third_party_apis(url: str) -> Dict:
    """Try all 4 third-party bypass services in order, first success wins."""
    engines = (bypass_city_get, bypass_city_api, adbypass_mirror, bypass_vip)
    last_error = "All third-party bypass APIs failed."
    for engine in engines:
        try:
            result = await engine(url)
            if result.get("success"):
                return result
            if result.get("error"):
                last_error = result["error"]
        except Exception as e:
            last_error = str(e)
            logger.debug(f"[nova_bypasser/thirdparty] {engine.__name__} crashed: {e}")
    return {"success": False, "error": last_error}
