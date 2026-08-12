# Akbots - Don't Remove Credit - @AkBots_Official
#
# cookie_utils.py — shared cookie-string parsing, extracted out of
# hotstar.py so other providers (goon_provider.py, etc.) don't duplicate
# the same "is this Netscape cookies.txt or a pasted document.cookie
# string?" detection logic.

import re

__all__ = ["parse_cookies"]


def _parse_cookie_string(raw: str) -> dict:
    """Parses a pasted `document.cookie`-style string ('a=1; b=2') into a dict."""
    cookies = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k:
            cookies[k] = v
    return cookies


def _parse_netscape_cookies(raw: str) -> dict:
    """Parses a Netscape/Mozilla cookies.txt file (tab-separated, 7 fields:
    domain, include_subdomains, path, secure, expiry, name, value). Lines
    starting with '#' are comments, EXCEPT the '#HttpOnly_' prefix which
    Chrome/yt-dlp exports use to mark HttpOnly cookies — those are real
    cookie lines and must still be parsed."""
    cookies = {}
    for line in raw.splitlines():
        line = line.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("#"):
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            else:
                continue
        fields = line.split("\t")
        if len(fields) < 7:
            # some exporters use runs of spaces instead of real tabs
            fields = re.split(r" {2,}", line.strip())
        if len(fields) < 7:
            continue
        name, value = fields[5].strip(), fields[6].strip()
        if name:
            cookies[name] = value
    return cookies


def _looks_like_netscape(raw: str) -> bool:
    stripped = raw.strip()
    if stripped.startswith("# Netscape") or stripped.startswith("# HTTP Cookie File") or stripped.startswith("#HttpOnly_"):
        return True
    # heuristic: first non-comment, non-blank line has >=7 tab-separated fields
    for line in stripped.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        return len(line.split("\t")) >= 7
    return False


def parse_cookies(raw: str) -> dict:
    """Auto-detects Netscape cookies.txt vs a pasted document.cookie string
    and parses accordingly. Returns {} for empty/blank input."""
    if not raw or not raw.strip():
        return {}
    if _looks_like_netscape(raw):
        parsed = _parse_netscape_cookies(raw)
        if parsed:
            return parsed
    return _parse_cookie_string(raw)
