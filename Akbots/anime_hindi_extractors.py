# Akbots - Don't Remove Credit - @AkBots_Official
#
# Generic embed-host stream extractor — ported nearly verbatim from the
# Anime-Episode-Scraper project's extractors.py (StreamTape, VidHide,
# Filemoon, StreamWish, Voe, Streamruby, Vidmoly, Filesim, and more).
# Used by Akbots/anime_hindi_scraper.py to resolve a Hindi-dub anime
# episode's embed iframe URL down to a raw playable stream link.
#
# The only functional change from the original: the global
# `requests.get = ...` monkeypatch was scoped to this module only (see
# _ScopedRequests below) so it can't silently change requests.get/post
# behavior for every other plugin in this bot process.

import re
import random
import binascii
import json
import base64
import ast
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, parse_qs
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import execjs
except Exception:
    execjs = None

try:
    import cloudscraper
except Exception:
    cloudscraper = None

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
except Exception:
    AES = None
    unpad = None

try:
    import numpy as np
except Exception:
    np = None


EXTRACTOR_REQUEST_TIMEOUT = 12
_extractor_session = requests.Session()
_extractor_retry = Retry(
    total=2,
    connect=2,
    read=2,
    backoff_factor=0.25,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST", "HEAD", "OPTIONS"],
)
_extractor_adapter = HTTPAdapter(pool_connections=200, pool_maxsize=200, max_retries=_extractor_retry)
_extractor_session.mount("http://", _extractor_adapter)
_extractor_session.mount("https://", _extractor_adapter)


def _session_get(url, **kwargs):
    kwargs.setdefault("timeout", EXTRACTOR_REQUEST_TIMEOUT)
    return _extractor_session.get(url, **kwargs)


def _session_post(url, **kwargs):
    kwargs.setdefault("timeout", EXTRACTOR_REQUEST_TIMEOUT)
    return _extractor_session.post(url, **kwargs)


# Ported note: the upstream script did `requests.get = _session_get` here,
# which mutates the *shared* requests module object — that would silently
# change requests.get/post behavior (pooling, retries, a default 12s
# timeout) for every other plugin in this bot process, since `import
# requests` everywhere returns the same module singleton. Instead we shadow
# the name `requests` only within this file's own namespace, so every
# `requests.get(...)`/`requests.post(...)` call site below keeps working
# unchanged, but nothing outside this module is affected.
class _ScopedRequests:
    get = staticmethod(_session_get)
    post = staticmethod(_session_post)

    def __getattr__(self, name):
        return getattr(_real_requests_module, name)


_real_requests_module = requests
requests = _ScopedRequests()


class Extractor:
    class AWSStream:
        name = "AWSStream"
        main_url = "https://z.awstream.net"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _extract_video_source(self, payload):
            if isinstance(payload, dict):
                for key in ["videoSource", "source", "file", "url"]:
                    value = payload.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value

                for value in payload.values():
                    found = self._extract_video_source(value)
                    if found:
                        return found

            if isinstance(payload, list):
                for item in payload:
                    found = self._extract_video_source(item)
                    if found:
                        return found

            return None

        def _extract_subtitles(self, html_text):
            subtitles = []
            seen = set()

            def add_subtitle(file_url, label="English", kind="captions"):
                if not file_url:
                    return
                normalized = file_url.replace('\\/', '/').replace('\\\\', '')
                if normalized in seen:
                    return
                subtitles.append({
                    "label": label or "English",
                    "file": normalized,
                    "kind": kind or "captions"
                })
                seen.add(normalized)

            # Pattern: var playerjsSubtitle = "[English]https://...vtt"
            # Some pages may include non-vtt subtitle links (provider-specific), so we accept any https URL.
            default_label_match = re.search(
                r'playerjsDefaultSubtitle\s*=\s*"([^"]+)"',
                html_text,
                re.IGNORECASE
            )
            default_label = default_label_match.group(1).strip() if default_label_match else "English"

            for m in re.finditer(r'playerjsSubtitle\s*=\s*"([^"]+)"', html_text, re.IGNORECASE):
                raw_value = m.group(1).strip()
                # Handle multi-subtitle values separated by commas if present.
                for part in [p.strip() for p in raw_value.split(',') if p.strip()]:
                    bracket_match = re.match(r'^\[([^\]]+)\](https?://.+)$', part)
                    if bracket_match:
                        add_subtitle(bracket_match.group(2), bracket_match.group(1), "captions")
                    elif part.startswith("http"):
                        add_subtitle(part, default_label, "captions")

            # Matches objects like: {"label":"English","file":"https://...vtt","kind":"captions"}
            # Also works with escaped quotes from packed/unpacked scripts.
            block_pattern = re.compile(
                r'\{[^{}]*?(?:\\?"kind\\?"\s*:\s*\\?"captions\\?")[^{}]*?\}',
                re.IGNORECASE
            )

            for block in block_pattern.findall(html_text):
                file_match = re.search(r'\\?"file\\?"\s*:\s*\\?"(https?[^"\\]+(?:\\\\/[^"\\]+)*)\\?"', block, re.IGNORECASE)
                if not file_match:
                    continue

                label_match = re.search(r'\\?"label\\?"\s*:\s*\\?"([^"\\]+)\\?"', block, re.IGNORECASE)
                kind_match = re.search(r'\\?"kind\\?"\s*:\s*\\?"([^"\\]+)\\?"', block, re.IGNORECASE)

                add_subtitle(
                    file_match.group(1),
                    label_match.group(1) if label_match else default_label,
                    kind_match.group(1) if kind_match else "captions"
                )

            # Fallback: capture direct .vtt links from packed scripts when JSON keys are obfuscated.
            for vtt in re.findall(r'https?://[^"\'\s]+\.vtt(?:\?[^"\'\s]*)?', html_text, re.IGNORECASE):
                add_subtitle(vtt, default_label, "captions")

            return subtitles

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0",
            }

            if referer:
                headers["Referer"] = referer
            else:
                headers["Referer"] = f"{self.main_url}/"

            extracted_hash = url.rstrip("/").split("/")[-1]
            page_res = requests.get(url, headers=headers)
            soup = BeautifulSoup(page_res.text, "html.parser")

            m3u8_api = f"{self.main_url}/player/index.php?data={extracted_hash}&do=getVideo"
            post_headers = {
                **headers,
                "x-requested-with": "XMLHttpRequest"
            }
            form_data = {
                "hash": extracted_hash,
                "r": self.main_url
            }

            m3u8_url = None
            try:
                response = requests.post(m3u8_api, headers=post_headers, data=form_data)
                response_json = response.json()
                m3u8_url = self._extract_video_source(response_json)
            except Exception:
                m3u8_url = None

            subtitles = self._extract_subtitles(str(soup))

            return {
                "source": m3u8_url,
                "referer": headers["Referer"] if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    class Ascdn21( AWSStream ):
        name = "Zephyrflick"
        main_url = "https://as-cdn21.top"
        requires_referer = True

    class Zephyrflick(AWSStream):
        name = "Zephyrflick"
        main_url = "https://play.zephyrflick.top"
        requires_referer = True

    class BetaAwstream(AWSStream):
        name = "AWSStream"
        main_url = "https://beta.awstream.net"
        requires_referer = True

    class StreamSB:
        name = "StreamSB"
        main_url = "https://watchsb.com"
        requires_referer = False
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _create_hash_table(self):
            return "".join(random.choice(self.alphabet) for _ in range(12))

        def _encode_id(self, stream_id):
            code = f"{self._create_hash_table()}||{stream_id}||{self._create_hash_table()}||streamsb"
            return "".join(format(ord(char), "x") for char in code)

        def _extract_id(self, url):
            regex = re.compile(r"(embed-[a-zA-Z\d]{0,8}[a-zA-Z\d_-]+|/e/[a-zA-Z\d]{0,8}[a-zA-Z\d_-]+)")
            match = regex.search(url)
            if not match:
                return None
            return re.sub(r"^(embed-|/e/)", "", match.group(0))

        def get_url(self, url, referer=None):
            stream_id = self._extract_id(url)
            if not stream_id:
                return {
                    "source": None,
                    "referer": None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }

            encoded_id = self._encode_id(stream_id)
            master = f"{self.main_url}/375664356a494546326c4b797c7c6e756577776778623171737/{encoded_id}".lower()

            request_headers = {
                "watchsb": "sbstream",
                "User-Agent": "Mozilla/5.0"
            }

            parsed = {}
            try:
                response = requests.get(master, headers=request_headers)
                parsed = response.json() if response.content else {}
            except Exception:
                parsed = {}

            stream_data = parsed.get("stream_data", {}) if isinstance(parsed, dict) else {}
            source = stream_data.get("file") if isinstance(stream_data, dict) else None

            subtitles = []
            subs = stream_data.get("subs", []) if isinstance(stream_data, dict) else []
            if isinstance(subs, list):
                for sub in subs:
                    if not isinstance(sub, dict):
                        continue
                    sub_file = sub.get("file")
                    if not sub_file:
                        continue
                    subtitles.append({
                        "label": sub.get("label") or "English",
                        "file": sub_file,
                        "kind": "captions"
                    })

            return {
                "source": source,
                "referer": None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    class Sblona(StreamSB):
        name = "Sblona"
        main_url = "https://sblona.com"

    class Lvturbo(StreamSB):
        name = "Lvturbo"
        main_url = "https://lvturbo.com"

    class Sbrapid(StreamSB):
        name = "Sbrapid"
        main_url = "https://sbrapid.com"

    class Sbface(StreamSB):
        name = "Sbface"
        main_url = "https://sbface.com"

    class Sbsonic(StreamSB):
        name = "Sbsonic"
        main_url = "https://sbsonic.com"

    class Vidgomunimesb(StreamSB):
        main_url = "https://vidgomunimesb.xyz"

    class Sbasian(StreamSB):
        name = "Sbasian"
        main_url = "https://sbasian.pro"

    class Sbnet(StreamSB):
        name = "Sbnet"
        main_url = "https://sbnet.one"

    class Keephealth(StreamSB):
        name = "Keephealth"
        main_url = "https://keephealth.info"

    class Sbspeed(StreamSB):
        name = "Sbspeed"
        main_url = "https://sbspeed.com"

    class Streamsss(StreamSB):
        main_url = "https://streamsss.net"

    class Sbflix(StreamSB):
        name = "Sbflix"
        main_url = "https://sbflix.xyz"

    class Vidgomunime(StreamSB):
        main_url = "https://vidgomunime.xyz"

    class Sbthe(StreamSB):
        main_url = "https://sbthe.com"

    class Ssbstream(StreamSB):
        main_url = "https://ssbstream.net"

    class SBfull(StreamSB):
        main_url = "https://sbfull.com"

    class StreamSB1(StreamSB):
        main_url = "https://sbplay1.com"

    class StreamSB2(StreamSB):
        main_url = "https://sbplay2.com"

    class StreamSB3(StreamSB):
        main_url = "https://sbplay3.com"

    class StreamSB4(StreamSB):
        main_url = "https://cloudemb.com"

    class StreamSB5(StreamSB):
        main_url = "https://sbplay.org"

    class StreamSB6(StreamSB):
        main_url = "https://embedsb.com"

    class StreamSB7(StreamSB):
        main_url = "https://pelistop.co"

    class StreamSB8(StreamSB):
        main_url = "https://streamsb.net"

    class StreamSB9(StreamSB):
        main_url = "https://sbplay.one"

    class StreamSB10(StreamSB):
        main_url = "https://sbplay2.xyz"

    class StreamSB11(StreamSB):
        main_url = "https://sbbrisk.com"

    class Sblongvu(StreamSB):
        main_url = "https://sblongvu.com"

    class rubystm:
        name = "StreamRuby"
        main_url = "https://rubystm.com"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url.rstrip("/")
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _extract_code(self, url):
            path = urlparse(url).path.rstrip("/")
            # Handles /e/<code>.html, /d/<code>.html, /v/<code>, etc.
            last = path.split("/")[-1] if path else ""
            last = last.replace(".html", "").strip()
            return last

        def _to_base_n(self, num, base):
            digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if num == 0:
                return "0"
            out = ""
            while num > 0:
                out = digits[num % base] + out
                num //= base
            return out

        def _unpack(self, p, c, k, base):
            unpacked = p
            for i in range(c):
                idx = c - i - 1
                if idx < len(k) and k[idx]:
                    token = self._to_base_n(idx, base)
                    unpacked = re.sub(r'\b' + re.escape(token) + r'\b', k[idx], unpacked)
            return unpacked

        def _extract_packed_data(self, text):
            pattern = (
                r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\("
                r"'(.*?)',\s*(\d+),\s*(\d+),\s*'(.*?)'\s*\.split\('\|'\)\)\)"
            )
            m = re.search(pattern, text or "", re.DOTALL)
            if not m:
                return None

            try:
                payload = m.group(1).replace("\\'", "'")
                base = int(m.group(2))
                count = int(m.group(3))
                keywords = m.group(4).split("|")

                if base < 2 or base > 62:
                    return None
                if count <= 0 or not isinstance(keywords, list):
                    return None
                return self._unpack(payload, count, keywords, base)
            except Exception:
                return None

        def _extract_m3u8(self, text):
            patterns = [
                r'sources\s*:\s*\[(?:\{src:|\{file:)?\s*["\']([^"\']+)',
                r'file\s*:\s*"(https?://[^"\']+\.m3u8[^"\']*)"',
                r'file\s*:\s*\"(https?://[^"\']+\.m3u8[^"\']*)\"',
                r'file\s*:\s*\'(https?://[^"\']+\.m3u8[^"\']*)\'',
                r'"file"\s*:\s*"(https?://[^"\']+\.m3u8[^"\']*)"',
                r'sources\s*:\s*\[\s*\{[^\}]*file\s*:\s*"(https?://[^"\']+\.m3u8[^"\']*)"',
                r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)',
            ]
            for pattern in patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    return m.group(1).replace('\\/', '/')
            return None

        def _extract_subtitles(self, text, web_url=None):
            subtitles = []
            seen = set()

            for m in re.finditer(r'\{[^{}]*file\s*:\s*["\']([^"\']+\.(?:vtt|srt|ass|ssa)[^"\']*)["\'][^{}]*\}', text or "", re.IGNORECASE):
                block = m.group(0)
                sub_file = m.group(1).replace('\\/', '/')
                if web_url and not sub_file.startswith("http"):
                    sub_file = urljoin(web_url, sub_file)
                if sub_file in seen:
                    continue
                label_match = re.search(r'label\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
                subtitles.append({
                    "label": label_match.group(1) if label_match else "English",
                    "file": sub_file,
                    "kind": "captions"
                })
                seen.add(sub_file)

            for m in re.finditer(r'\{[^{}]*file\s*:\s*["\']([^"\']+)["\'][^{}]*\}', text or "", re.IGNORECASE):
                block = m.group(0)
                sub_file = m.group(1).replace('\\/', '/')
                is_sub = bool(
                    re.search(r'kind\s*:\s*["\'](?:captions|subtitles)["\']', block, re.IGNORECASE)
                    or "get_vtt" in sub_file.lower()
                    or "subtitle" in sub_file.lower()
                )
                if not is_sub:
                    continue
                if web_url and not sub_file.startswith("http"):
                    sub_file = urljoin(web_url, sub_file)
                if sub_file in seen:
                    continue
                label_match = re.search(r'label\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
                kind_match = re.search(r'kind\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
                subtitles.append({
                    "label": label_match.group(1) if label_match else "English",
                    "file": sub_file,
                    "kind": kind_match.group(1) if kind_match else "captions"
                })
                seen.add(sub_file)

            return subtitles

        def _fetch_candidate_pages(self, url, code, headers, base_url):
            pages = []

            # 1) Embed form endpoint used by /e/*.html pages
            if code:
                try:
                    post_data = {
                        "op": "embed",
                        "file_code": code,
                        "auto": "1",
                        "referer": headers.get("Referer", ""),
                    }
                    resp = requests.post(f"{base_url}/dl", data=post_data, headers=headers, timeout=20)
                    if resp.text:
                        pages.append(resp.text)
                except Exception:
                    pass

            # 2) Common alternate endpoints that sometimes contain direct sources
            alt_urls = [
                url,
                f"{base_url}/embed-{code}.html" if code else "",
                f"{base_url}/e/{code}.html" if code else "",
                f"{base_url}/d/{code}.html" if code else "",
                f"{base_url}/v/{code}" if code else "",
                f"{base_url}/f/{code}" if code else "",
            ]

            for candidate in alt_urls:
                if not candidate:
                    continue
                try:
                    r = requests.get(candidate, headers=headers, timeout=20)
                    if r.text:
                        pages.append(r.text)
                except Exception:
                    continue

            return pages

        def get_url(self, url, referer=None):
            code = self._extract_code(url)
            default_domain = self._get_base_url(url)
            base_url = default_domain.rstrip("/")
            headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36",
                "Referer": referer or default_domain,
                "Origin": base_url,
            }

            m3u8 = None
            subtitles = []
            for page in self._fetch_candidate_pages(url, code, headers, base_url):
                unpacked = self._extract_packed_data(page)
                if unpacked:
                    m3u8 = self._extract_m3u8(unpacked)
                    if not subtitles:
                        subtitles = self._extract_subtitles(unpacked, base_url)
                    if m3u8:
                        break
                m3u8 = self._extract_m3u8(page)
                if not subtitles:
                    subtitles = self._extract_subtitles(page, base_url)
                if m3u8:
                    break

            return {
                "source": m3u8,
                "referer": default_domain if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

        def _get_base_url(self, url):
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}/"
            return f"{self.main_url}/"

    class StreamRuby(rubystm):
        main_url = "https://streamruby.com"

    class Streamruby(rubystm):
        name = "Streamruby"
        main_url = "https://streamruby.com"

    class StreamTape:
        name = "StreamTape"
        main_url = "https://streamtape.com"
        requires_referer = False

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url.rstrip("/")
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _get_base_url(self, url):
            try:
                parsed = urlparse(url)
                if parsed.scheme and parsed.netloc:
                    return f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass
            return self.main_url

        def _extract_result(self, html_text):
            soup = BeautifulSoup(html_text or "", "html.parser")
            for script in soup.find_all("script"):
                script_text = script.get_text() or script.string or script.decode_contents() or ""
                if "botlink').innerHTML" not in script_text:
                    continue

                for line in script_text.splitlines():
                    if "botlink').innerHTML" not in line:
                        continue
                    script_content = line.split(").innerHTML", 1)[-1].replace("=", "var url =", 1)
                    if execjs is None:
                        return None

                    rhino = execjs.compile(script_content)
                    try:
                        result = rhino.eval("url")
                        return result
                    except Exception:
                        return None

            return None

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
            }

            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                result = self._extract_result(response.text)

                if result:
                    extracted_url = f"https:{result}&stream=1" if result.startswith("//") else result
                    return {
                        "source": extracted_url,
                        "referer": url if self.requires_referer else None,
                        "requiresReferer": self.requires_referer,
                        "subtitles": []
                    }
            except Exception:
                pass

            return {
                "source": None,
                "referer": None,
                "requiresReferer": self.requires_referer,
                "subtitles": []
            }

    class Watchadsontape(StreamTape):
        main_url = "https://watchadsontape.com"

    class StreamTapeNet(StreamTape):
        main_url = "https://streamtape.net"

    class StreamTapeXyz(StreamTape):
        main_url = "https://streamtape.xyz"

    class ShaveTape(StreamTape):
        main_url = "https://shavetape.cash"

    class StreamTapeSite(StreamTape):
        main_url = "https://streamtape.site"

    class Voe:
        name = "Voe"
        main_url = "https://voe.sx"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url.rstrip("/")
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _get_base_url(self, url):
            try:
                parsed = urlparse(url)
                if parsed.scheme and parsed.netloc:
                    return f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass
            return self.main_url

        def _rot13(self, value):
            out = []
            for ch in value or "":
                if "A" <= ch <= "Z":
                    out.append(chr(((ord(ch) - ord("A") + 13) % 26) + ord("A")))
                elif "a" <= ch <= "z":
                    out.append(chr(((ord(ch) - ord("a") + 13) % 26) + ord("a")))
                else:
                    out.append(ch)
            return "".join(out)

        def _replace_patterns(self, value):
            patterns = ["@$", "^^", "~@", "%?", "*~", "!!", "#&"]
            result = value or ""
            for pattern in patterns:
                result = result.replace(pattern, "_")
            return result

        def _remove_underscores(self, value):
            return (value or "").replace("_", "")

        def _base64_decode(self, value):
            if not value:
                return ""
            padded = value + "=" * (-len(value) % 4)
            try:
                return base64.b64decode(padded).decode("utf-8", errors="ignore")
            except Exception:
                return ""

        def _char_shift(self, value, shift):
            return "".join(chr(ord(ch) - shift) for ch in (value or ""))

        def _decrypt_f7(self, encoded_string):
            try:
                v_f = self._rot13(encoded_string)
                v_f2 = self._replace_patterns(v_f)
                v_f3 = self._remove_underscores(v_f2)
                v_f4 = self._base64_decode(v_f3)
                v_f5 = self._char_shift(v_f4, 3)
                v_f6 = v_f5[::-1]
                v_atob = self._base64_decode(v_f6)
                return json.loads(v_atob) if v_atob.strip().startswith("{") else {}
            except Exception:
                return {}

        def _extract_encoded_string(self, html_text):
            soup = BeautifulSoup(html_text or "", "html.parser")
            script = soup.select_one('script[type="application/json"]')
            if not script:
                return None

            raw = (script.get_text() or script.string or script.decode_contents() or "").strip()
            start = raw.find('["')
            end = raw.rfind('"]')
            if start == -1 or end == -1 or end <= start + 2:
                return None
            return raw[start + 2:end]

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
                "Referer": referer or f"{self.main_url}/",
            }

            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                html_text = response.text or ""

                redirect_match = re.search(r"window\.location\.href\s*=\s*'([^']+)';", html_text)
                if redirect_match:
                    redirect_url = urljoin(response.url or url, redirect_match.group(1))
                    response = requests.get(redirect_url, headers={**headers, "Referer": referer or f"{self.main_url}/"})
                    response.raise_for_status()
                    html_text = response.text or ""

                encoded_string = self._extract_encoded_string(html_text)
                if not encoded_string:
                    return {
                        "source": None,
                        "referer": referer or f"{self.main_url}/" if self.requires_referer else None,
                        "requiresReferer": self.requires_referer,
                        "subtitles": []
                    }

                decrypted_json = self._decrypt_f7(encoded_string)
                source = decrypted_json.get("source") if isinstance(decrypted_json, dict) else None
                direct_access_url = decrypted_json.get("direct_access_url") if isinstance(decrypted_json, dict) else None

                return {
                    "source": source or direct_access_url,
                    "referer": f"{self.main_url}/" if self.requires_referer else None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }
            except Exception:
                return {
                    "source": None,
                    "referer": None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }

    class Tubeless(Voe):
        name = "Tubeless"
        main_url = "https://tubelessceliolymph.com"

    class Simpulumlamerop(Voe):
        name = "Simplum"
        main_url = "https://simpulumlamerop.com"

    class Urochsunloath(Voe):
        name = "Uroch"
        main_url = "https://urochsunloath.com"

    class NathanFromSubject(Voe):
        main_url = "https://nathanfromsubject.com"

    class Yipsu(Voe):
        name = "Yipsu"
        main_url = "https://yip.su"

    class MetaGnathTuggers(Voe):
        name = "Metagnath"
        main_url = "https://metagnathtuggers.com"

    class Voe1(Voe):
        main_url = "https://donaldlineelse.com"

    class StreamWishExtractor:
        name = "Streamwish"
        main_url = "https://streamwish.to"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _resolve_embed_url(self, input_url):
            if "/f/" in input_url:
                video_id = input_url.split("/f/")[-1].split("?")[0].split("#")[0]
                return f"{self.main_url}/{video_id}"
            if "/e/" in input_url:
                video_id = input_url.split("/e/")[-1].split("?")[0].split("#")[0]
                return f"{self.main_url}/{video_id}"
            return input_url

        def _extract_m3u8(self, text):
            patterns = [
                r'file\s*:\s*"(https?://[^"\']+\.m3u8[^"\']*)"',
                r'"file"\s*:\s*"(https?://[^"\']+\.m3u8[^"\']*)"',
                r'sources\s*:\s*\[\s*\{[^\}]*file\s*:\s*"(https?://[^"\']+\.m3u8[^"\']*)"',
                r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)',
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return match.group(1).replace('\\/', '/')
            return None

        def get_url(self, url, referer=None):
            headers = {
                "Accept": "*/*",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
                "Referer": f"{self.main_url}/",
                "Origin": f"{self.main_url}/",
                "User-Agent": "Mozilla/5.0",
            }

            resolved_url = self._resolve_embed_url(url)
            page_response = requests.get(resolved_url, headers=headers)
            html_text = page_response.text

            m3u8_url = self._extract_m3u8(html_text)

            return {
                "source": m3u8_url,
                "referer": headers["Referer"] if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": []
            }

    class Mwish(StreamWishExtractor):
        name = "Mwish"
        main_url = "https://mwish.pro"

    class Dwish(StreamWishExtractor):
        name = "Dwish"
        main_url = "https://dwish.pro"

    class Ewish(StreamWishExtractor):
        name = "Embedwish"
        main_url = "https://embedwish.com"

    class WishembedPro(StreamWishExtractor):
        name = "Wishembed"
        main_url = "https://wishembed.pro"

    class Kswplayer(StreamWishExtractor):
        name = "Kswplayer"
        main_url = "https://kswplayer.info"

    class Wishfast(StreamWishExtractor):
        name = "Wishfast"
        main_url = "https://wishfast.top"

    class Streamwish2(StreamWishExtractor):
        main_url = "https://streamwish.site"

    class SfastwishCom(StreamWishExtractor):
        name = "Sfastwish"
        main_url = "https://sfastwish.com"

    class Strwish(StreamWishExtractor):
        name = "Strwish"
        main_url = "https://strwish.xyz"

    class Strwish2(StreamWishExtractor):
        name = "Strwish"
        main_url = "https://strwish.com"

    class FlaswishCom(StreamWishExtractor):
        name = "Flaswish"
        main_url = "https://flaswish.com"

    class Awish(StreamWishExtractor):
        name = "Awish"
        main_url = "https://awish.pro"

    class Obeywish(StreamWishExtractor):
        name = "Obeywish"
        main_url = "https://obeywish.com"

    class Jodwish(StreamWishExtractor):
        name = "Jodwish"
        main_url = "https://jodwish.com"

    class Swhoi(StreamWishExtractor):
        name = "Swhoi"
        main_url = "https://swhoi.com"

    class Multimovies(StreamWishExtractor):
        name = "Multimovies"
        main_url = "https://multimovies.cloud"

    class UqloadsXyz(StreamWishExtractor):
        name = "Uqloads"
        main_url = "https://uqloads.xyz"

    class Doodporn(StreamWishExtractor):
        name = "Doodporn"
        main_url = "https://doodporn.xyz"

    class CdnwishCom(StreamWishExtractor):
        name = "Cdnwish"
        main_url = "https://cdnwish.com"

    class Asnwish(StreamWishExtractor):
        name = "Asnwish"
        main_url = "https://asnwish.com"

    class Nekowish(StreamWishExtractor):
        name = "Nekowish"
        main_url = "https://nekowish.my.id"

    class Nekostream(StreamWishExtractor):
        name = "Nekostream"
        main_url = "https://neko-stream.click"

    class Swdyu(StreamWishExtractor):
        name = "Swdyu"
        main_url = "https://swdyu.com"

    class Wishonly(StreamWishExtractor):
        name = "Wishonly"
        main_url = "https://wishonly.site"

    class Playerwish(StreamWishExtractor):
        name = "Playerwish"
        main_url = "https://playerwish.com"

    class StreamHLS(StreamWishExtractor):
        name = "StreamHLS"
        main_url = "https://streamhls.to"

    class HlsWish(StreamWishExtractor):
        name = "HlsWish"
        main_url = "https://hlswish.com"

    class Filesim:
        name = "Filesim"
        main_url = "https://files.im"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url.rstrip("/")
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _extract_code(self, url):
            parsed = urlparse(url)
            path = parsed.path.rstrip("/")
            if not path:
                return ""
            parts = [p for p in path.split("/") if p]
            if not parts:
                return ""
            if parts[0] in {"download", "e", "embed", "v", "f"} and len(parts) > 1:
                return parts[1]
            return parts[-1]

        def _build_embed_url(self, input_url):
            parsed = urlparse(input_url)
            base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else self.main_url
            media_id = self._extract_code(input_url)
            if not media_id:
                return input_url
            return f"{base}/e/{media_id}"

        def _to_base_n(self, num, base):
            digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if num == 0:
                return "0"
            out = ""
            while num > 0:
                out = digits[num % base] + out
                num //= base
            return out

        def _unpack(self, p, c, k, base):
            unpacked = p
            for i in range(c):
                idx = c - i - 1
                if idx < len(k) and k[idx]:
                    token = self._to_base_n(idx, base)
                    unpacked = re.sub(r'\b' + re.escape(token) + r'\b', k[idx], unpacked)
            return unpacked

        def _extract_packed_data(self, text):
            pattern = (
                r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\("
                r"'(.*?)',\s*(\d+),\s*(\d+),\s*'(.*?)'\s*\.split\('\|'\)\)\)"
            )
            m = re.search(pattern, text or "", re.DOTALL)
            if not m:
                return None

            try:
                payload = m.group(1).replace("\\'", "'")
                base = int(m.group(2))
                count = int(m.group(3))
                keywords = m.group(4).split("|")

                if base < 2 or base > 62:
                    return None
                if count <= 0 or not isinstance(keywords, list):
                    return None

                return self._unpack(payload, count, keywords, base)
            except Exception:
                return None

        def _extract_var_links_source(self, text, web_url):
            # Common in streamhg/filesim pages: var links={hls4:...,hls3:...,hls2:...}
            links_match = re.search(r'var\s*links\s*=\s*([^;]+)', text or "", re.IGNORECASE)
            if links_match:
                blob = links_match.group(1).strip()
                try:
                    links = ast.literal_eval(blob)
                except Exception:
                    links = None

                if isinstance(links, dict):
                    source = links.get("hls4") or links.get("hls3") or links.get("hls2")
                    if isinstance(source, str) and source:
                        source = source.replace('\\/', '/')
                        return source if source.startswith("http") else urljoin(web_url, source)

            # Alternate form: var o={hls4:...,hls3:...,hls2:...}
            o_match = re.search(r'var\s+o\s*=\s*(\{.*?\})\s*;', text or "", re.DOTALL | re.IGNORECASE)
            if o_match:
                blob = o_match.group(1)
                file_match = re.search(
                    r'(?:hls4|hls3|hls2|file|src)\s*:\s*["\']([^"\']+)["\']',
                    blob,
                    re.IGNORECASE
                )
                if file_match:
                    source = file_match.group(1).replace('\\/', '/')
                    return source if source.startswith("http") else urljoin(web_url, source)

            return None

        def _extract_stream_candidates(self, text, web_url):
            candidates = []
            seen = set()

            def add_candidate(u):
                if not u or not isinstance(u, str):
                    return
                u = u.replace('\\/', '/')
                if u.startswith('//'):
                    parsed = urlparse(web_url)
                    u = f"{(parsed.scheme or 'https')}:{u}"
                elif u.startswith('/'):
                    u = urljoin(web_url, u)
                if u in seen:
                    return
                seen.add(u)
                candidates.append(u)

            # Prefer explicit links object order hls4 -> hls3 -> hls2
            links_match = re.search(r'var\s*links\s*=\s*([^;]+)', text or "", re.IGNORECASE)
            if links_match:
                try:
                    links = ast.literal_eval(links_match.group(1).strip())
                    if isinstance(links, dict):
                        for key in ["hls4", "hls3", "hls2", "file", "src"]:
                            add_candidate(links.get(key))
                except Exception:
                    pass

            # Generic key patterns used by Filesim/VidHide clones
            patterns = [
                r'"hls\d+"\s*:\s*"(https?://[^"\']+)"',
                r'(?:hls4|hls3|hls2|file|src|stream|master)\s*:\s*["\'](https?://[^"\']+)["\']',
                r'file\s*:\s*["\'](https?://[^"\']+?\.m3u8[^"\']*)["\']',
                r'file\s*:\s*["\'](https?://[^"\']+?\.txt[^"\']*)["\']',
                r'["\'](https?://[^"\']+master\.txt[^"\']*)["\']',
                r'file\s*:\s*["\'](https?://[^"\']+urlset/master\.m3u8[^"\']*)["\']',
                r'<source[^>]+src=["\']([^"\']+)["\']',
                r'(https?://[^"\'\s]+\.(?:m3u8|txt)[^"\'\s]*)',
            ]
            for pattern in patterns:
                for m in re.finditer(pattern, text or "", re.IGNORECASE):
                    add_candidate(m.group(1))

            return candidates

        def _extract_m3u8(self, text, web_url):
            patterns = [
                r'file\s*:\s*"(https?://[^"\']+\.(?:m3u8|txt)[^"\']*)"',
                r'file\s*:\s*\'(https?://[^"\']+\.(?:m3u8|txt)[^"\']*)\'',
                r'"hls\d+"\s*:\s*"(https?://[^"\']+)"',
                r'file\s*:\s*"(/[^"\']+\.(?:m3u8|txt)[^"\']*)"',
                r'file\s*:\s*\'(/[^"\']+\.(?:m3u8|txt)[^"\']*)\'',
                r'["\'](https?://[^"\']+master\.txt[^"\']*)["\']',
                r'(https?://[^"\'\s]+\.(?:m3u8|txt)[^"\'\s]*)',
                r'(/[^"\'\s]+\.(?:m3u8|txt)[^"\'\s]*)',
                r'<source[^>]+src=["\']([^"\']+)["\']',
            ]
            for pattern in patterns:
                m = re.search(pattern, text or "", re.IGNORECASE)
                if m:
                    source = m.group(1).replace('\\/', '/')
                    return source if source.startswith("http") else urljoin(web_url, source)
            return None

        def _extract_subtitles(self, text, web_url):
            subtitles = []
            seen = set()
            for m in re.finditer(r'\{[^{}]*file\s*:\s*["\']([^"\']+\.(?:vtt|srt|ass|ssa)[^"\']*)["\'][^{}]*\}', text or "", re.IGNORECASE):
                block = m.group(0)
                sub_file = m.group(1).replace('\\/', '/')
                if not sub_file:
                    continue
                full_file = sub_file if sub_file.startswith("http") else urljoin(web_url, sub_file)
                if full_file in seen:
                    continue
                label_match = re.search(r'label\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
                subtitles.append({
                    "label": label_match.group(1) if label_match else "English",
                    "file": full_file,
                    "kind": "captions"
                })
                seen.add(full_file)

            # Fallback for packed JW config objects where subtitle URL is not .vtt filename
            for m in re.finditer(r'\{[^{}]*file\s*:\s*["\']([^"\']+)["\'][^{}]*\}', text or "", re.IGNORECASE):
                block = m.group(0)
                sub_file = m.group(1).replace('\\/', '/')
                if not sub_file:
                    continue
                is_sub = bool(
                    re.search(r'kind\s*:\s*["\'](?:captions|subtitles)["\']', block, re.IGNORECASE)
                    or re.search(r'label\s*:\s*["\'][^"\']+["\']', block, re.IGNORECASE)
                    or "get_vtt" in sub_file.lower()
                    or "subtitle" in sub_file.lower()
                )
                if not is_sub:
                    continue
                full_file = sub_file if sub_file.startswith("http") else urljoin(web_url, sub_file)
                if full_file in seen:
                    continue
                label_match = re.search(r'label\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
                kind_match = re.search(r'kind\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
                subtitles.append({
                    "label": label_match.group(1) if label_match else "English",
                    "file": full_file,
                    "kind": kind_match.group(1) if kind_match else "captions"
                })
                seen.add(full_file)
            return subtitles

        def get_url(self, url, referer=None):
            embed_url = self._build_embed_url(url)
            parsed_embed = urlparse(embed_url)
            base = f"{parsed_embed.scheme}://{parsed_embed.netloc}" if parsed_embed.scheme and parsed_embed.netloc else self.main_url

            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "en-US,en;q=0.5",
                "Sec-Fetch-Dest": "iframe",
                "Referer": referer or f"{base}/",
            }

            source = None
            subtitles = []

            try:
                response = requests.get(embed_url, headers=headers, timeout=20)
                web_url = response.url or embed_url
                html_text = response.text
                soup = BeautifulSoup(html_text, "html.parser")

                iframe = soup.find("iframe")
                if iframe and iframe.get("src"):
                    iframe_url = urljoin(web_url, iframe.get("src"))
                    iframe_resp = requests.get(iframe_url, headers={**headers, "Referer": web_url}, timeout=20)
                    web_url = iframe_resp.url or iframe_url
                    html_text = iframe_resp.text
                    soup = BeautifulSoup(html_text, "html.parser")

                script_data = self._extract_packed_data(html_text)
                if not script_data:
                    for script in soup.find_all("script"):
                        script_text = script.get_text() or script.string or ""
                        if (
                            "sources:" in script_text
                            or "file:" in script_text
                            or "hls2" in script_text
                            or "hls3" in script_text
                            or "hls4" in script_text
                            or "master.txt" in script_text
                        ):
                            script_data = script_text
                            break

                merged_text = (script_data or "") + "\n" + (html_text or "")
                candidates = self._extract_stream_candidates(merged_text, web_url)
                source = candidates[0] if candidates else None
                if not source:
                    source = self._extract_var_links_source(merged_text, web_url)
                if not source:
                    source = self._extract_m3u8(merged_text, web_url)
                subtitles = self._extract_subtitles(script_data or html_text, web_url)
            except Exception:
                source = None

            return {
                "source": source,
                "referer": f"{base}/" if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    class Multimoviesshg(Filesim):
        main_url = "https://multimoviesshg.com"

    class Guccihide(Filesim):
        name = "Guccihide"
        main_url = "https://guccihide.com"

    class Ahvsh(Filesim):
        name = "Ahvsh"
        main_url = "https://ahvsh.com"

    class Moviesm4u(Filesim):
        name = "Moviesm4u"
        main_url = "https://moviesm4u.com"

    class StreamhideTo(Filesim):
        name = "Streamhide"
        main_url = "https://streamhide.to"

    class StreamhideCom(Filesim):
        name = "Streamhide"
        main_url = "https://streamhide.com"

    class Movhide(Filesim):
        name = "Movhide"
        main_url = "https://movhide.pro"

    class Ztreamhub(Filesim):
        name = "Zstreamhub"
        main_url = "https://ztreamhub.com"

    class VidStack:
        name = "Vidstack"
        main_url = "https://vidstack.io"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _get_base_url(self, url):
            try:
                parsed = urlparse(url)
                return f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                return self.main_url

        def _extract_video_id(self, url):
            try:
                parsed = urlparse(url)

                if parsed.fragment:
                    fragment_id = parsed.fragment.strip().strip("/")
                    if fragment_id:
                        return fragment_id.split("/")[-1]

                query = parse_qs(parsed.query)
                for key in ["id", "v", "hash"]:
                    if key in query and query[key]:
                        return query[key][0].strip()

                path_last = parsed.path.rstrip("/").split("/")[-1].strip()
                return path_last or None
            except Exception:
                return None

        def _decrypt_aes(self, input_hex, key, iv):
            if AES is None:
                raise RuntimeError("pycryptodome is required for VidStack decryption")

            encrypted_bytes = binascii.unhexlify(input_hex.strip())
            cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
            decrypted = cipher.decrypt(encrypted_bytes)

            try:
                decrypted = unpad(decrypted, AES.block_size) if unpad else decrypted
            except Exception:
                pass

            return decrypted.decode("utf-8", errors="ignore")

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0"
            }

            extracted_hash = self._extract_video_id(url)
            if not extracted_hash:
                return {
                    "source": None,
                    "referer": referer or url if self.requires_referer else None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }

            base_url = self._get_base_url(url)

            encoded = ""
            try:
                api_headers = {
                    **headers,
                    "Referer": referer or url,
                    "Origin": base_url,
                    "X-Requested-With": "XMLHttpRequest"
                }
                raw_response = requests.get(
                    f"{base_url}/api/v1/video?id={extracted_hash}",
                    headers=api_headers,
                    timeout=20
                )
                raw_text = raw_response.text.strip()

                # API may return plain encrypted text or wrapped JSON payload.
                try:
                    raw_json = raw_response.json()
                    if isinstance(raw_json, dict):
                        encoded = (
                            raw_json.get("data")
                            or raw_json.get("result")
                            or raw_json.get("encoded")
                            or raw_json.get("cipher")
                            or ""
                        )
                    elif isinstance(raw_json, str):
                        encoded = raw_json
                except Exception:
                    encoded = raw_text.strip('"')
            except Exception:
                encoded = ""

            decrypted_text = ""
            key = "kiemtienmua911ca"
            iv_list = ["1234567890oiuytr", "0123456789abcdef"]
            for iv in iv_list:
                try:
                    decrypted_text = self._decrypt_aes(encoded, key, iv)
                    if decrypted_text:
                        break
                except Exception:
                    continue

            m3u8_match = re.search(r'"source":"(.*?)"', decrypted_text)
            m3u8 = m3u8_match.group(1).replace('\\/', '/') if m3u8_match else None

            subtitles = []
            subtitle_section_match = re.search(r'"subtitle":\{(.*?)\}', decrypted_text, re.DOTALL)
            if subtitle_section_match:
                section = subtitle_section_match.group(1)
                subtitle_pattern = re.compile(r'"([^"]+)":\s*"([^"]+)"')

                for match in subtitle_pattern.finditer(section):
                    lang = match.group(1)
                    raw_path = match.group(2).split("#")[0]
                    if not raw_path:
                        continue

                    path = raw_path.replace('\\/', '/')
                    sub_url = path if path.startswith("http") else urljoin(self.main_url, path)
                    subtitles.append({
                        "label": lang,
                        "file": sub_url,
                        "kind": "captions"
                    })

            return {
                "source": m3u8,
                "referer": referer or url if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    class Server1uns(VidStack):
        name = "Vidstack"
        main_url = "https://server1.uns.bio"
        requires_referer = True

    class Cloudy(VidStack):
        main_url = "https://cloudy.upns.one"
        requires_referer = True

    class CloudyP2P(VidStack):
        main_url = "https://cloudy.p2pplay.pro"
        requires_referer = True
    
    class Cloudytpx(VidStack):
        name = "StreamP2p"
        main_url = "https://tpx.p2pstream.vip"
        requires_referer = True
    class Cloudyzoro(VidStack):
        name = "CloudyZoro"
        main_url = "https://zoro.rpmplay.xyz"
        requires_referer = True

    class VidHidePro:
        name = "VidHidePro"
        main_url = "https://vidhidepro.com"
        requires_referer = True
        fallback_host = "callistanise.com"
        dead_domains = {
            "filelions.com", "filelions.to", "ajmidyadfihayh.sbs", "alhayabambi.sbs", "vidhideplus.com",
            "azipcdn.com", "mlions.pro", "alions.pro", "dlions.pro", "mivalyo.com", "vidhidefast.com",
            "filelions.live", "motvy55.store", "filelions.xyz", "lumiawatch.top", "filelions.online",
            "fviplions.com", "egsyxutd.sbs", "filelions.site", "filelions.co", "vidhidepre.com",
            "vidhidepro.com", "vidhidevip.com", "e4xb5c2xnz.sbs", "taylorplayer.com", "ryderjet.com",
            "techradar.ink", "anime7u.com", "coolciima.online", "gsfomqu.sbs", "bingezove.com",
            "katomen.online", "vidhide.fun", "6sfkrspw4u.sbs", "dingtezuni.com", "dinisglows.com",
            "dintezuvio.com"
        }

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _get_embed_url(self, url):
            if "/d/" in url:
                return url.replace("/d/", "/v/")
            if "/download/" in url:
                return url.replace("/download/", "/v/")
            if "/file/" in url:
                return url.replace("/file/", "/v/")
            return url.replace("/f/", "/v/")

        def _normalize_target_url(self, url):
            embed_url = self._get_embed_url(url)
            parsed = urlparse(embed_url)
            host = parsed.netloc.lower()
            if host in self.dead_domains:
                replaced = parsed._replace(netloc=self.fallback_host)
                return replaced.geturl()
            return embed_url

        def _extract_var_links_source(self, html_text, web_url):
            links_match = re.search(r'var\s*links\s*=\s*([^;]+)', html_text or "", re.IGNORECASE)
            if not links_match:
                return None

            blob = links_match.group(1).strip()
            try:
                links = ast.literal_eval(blob)
            except Exception:
                try:
                    normalized = blob.replace("'", '"')
                    links = json.loads(normalized)
                except Exception:
                    return None

            if not isinstance(links, dict):
                return None

            source = links.get("hls4") or links.get("hls3") or links.get("hls2")
            if not source or not isinstance(source, str):
                return None

            if source.startswith("/"):
                return urljoin(web_url, source)
            return source

        def _to_base_n(self, num, base):
            digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if num == 0:
                return "0"
            out = ""
            while num > 0:
                out = digits[num % base] + out
                num //= base
            return out

        def _unpack(self, p, c, k, base):
            unpacked = p
            for i in range(c):
                idx = c - i - 1
                if idx < len(k) and k[idx]:
                    token = self._to_base_n(idx, base)
                    unpacked = re.sub(r'\b' + re.escape(token) + r'\b', k[idx], unpacked)
            return unpacked

        def _extract_packed_data(self, text):
            pattern = (
                r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\("
                r"'(.*?)',\s*(\d+),\s*(\d+),\s*'(.*?)'\s*\.split\('\|'\)\)\)"
            )
            m = re.search(pattern, text or "", re.DOTALL)
            if not m:
                return None

            try:
                payload = m.group(1).replace("\\'", "'")
                base = int(m.group(2))
                count = int(m.group(3))
                keywords = m.group(4).split("|")

                if base < 2 or base > 62:
                    return None
                if count <= 0 or not isinstance(keywords, list):
                    return None

                return self._unpack(payload, count, keywords, base)
            except Exception:
                return None

        def _extract_var_o_source(self, html_text, web_url):
            # Common packed layout: var o={hls4:...,hls3:...,hls2:...}
            m = re.search(r'var\s+o\s*=\s*(\{.*?\})\s*;', html_text or "", re.DOTALL | re.IGNORECASE)
            if not m:
                return None

            blob = m.group(1)
            source = None

            try:
                normalized = re.sub(r'([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', blob)
                normalized = normalized.replace("'", '"')
                data = json.loads(normalized)
                if isinstance(data, dict):
                    source = (
                        data.get("hls4")
                        or data.get("hls3")
                        or data.get("hls2")
                        or data.get("file")
                        or data.get("src")
                        or data.get("master")
                        or data.get("stream")
                    )
            except Exception:
                source = None

            if not source:
                direct = re.search(
                    r'(?:hls4|hls3|hls2|file|src|master|stream)\s*:\s*["\']([^"\']+)["\']',
                    blob,
                    re.IGNORECASE
                )
                if direct:
                    source = direct.group(1)

            # Dynamic key chain used by packed JW setup:
            # e.g. sources:[{file:o.hls4||o.hls3||o.hls2}] or obfuscated key names.
            if not source:
                key_values = {}
                for km in re.finditer(r'["\']?([A-Za-z0-9_]+)["\']?\s*:\s*["\']([^"\']+)["\']', blob):
                    key_values[km.group(1)] = km.group(2)

                # Prefer explicit chain order if present near `sources`/`file` configuration.
                chain_order = []
                chain_match = re.search(r'(?:file|src|source)\s*:\s*([^,\]\}]+)', html_text or "", re.IGNORECASE)
                if chain_match:
                    for name in re.findall(r'o\.([A-Za-z0-9_]+)', chain_match.group(1)):
                        if name not in chain_order:
                            chain_order.append(name)

                # Fallback: any o.<key> references found in script.
                if not chain_order:
                    for name in re.findall(r'o\.([A-Za-z0-9_]+)', html_text or ""):
                        if name not in chain_order:
                            chain_order.append(name)

                # Append common preferred keys at the end.
                for k in ["hls4", "hls3", "hls2", "file", "src", "master", "stream", "url"]:
                    if k not in chain_order:
                        chain_order.append(k)

                for key in chain_order:
                    candidate = key_values.get(key)
                    if isinstance(candidate, str) and candidate:
                        source = candidate
                        break

            if not source or not isinstance(source, str):
                return None

            source = source.replace('\\/', '/')
            if source.startswith('//'):
                parsed = urlparse(web_url)
                scheme = parsed.scheme or 'https'
                return f"{scheme}:{source}"
            return urljoin(web_url, source) if source.startswith('/') else source

        def _extract_m3u8_urls(self, html_text, web_url):
            urls = []
            seen = set()

            patterns = [
                r'(?:hls4|hls3|hls2|file|src|stream|master)\s*:\s*["\'](https?://[^"\']+)["\']',
                r":\s*\"(https?://[^\"\\']*?m3u8[^\"\\']*)\"",
                r":\s*\"(https?://[^\"\\']*?\.txt[^\"\\']*)\"",
                r"file\s*:\s*\"(https?://[^\"\\']*?m3u8[^\"\\']*)\"",
                r"file\s*:\s*\"(https?://[^\"\\']*?\.txt[^\"\\']*)\"",
                r"source\s*:\s*\"(https?://[^\"\\']*?m3u8[^\"\\']*)\"",
                r"source\s*:\s*\"(https?://[^\"\\']*?\.txt[^\"\\']*)\"",
                r"(https?://[^\"\\'\s]+?m3u8[^\"\\'\s]*)",
                r"(https?://[^\"\\'\s]+?\.txt[^\"\\'\s]*)",
                r"(:?\"|\')(/[^\"\']*?(?:m3u8|\.txt)[^\"\']*)(?:\"|\')",
            ]

            for pattern in patterns:
                for match in re.finditer(pattern, html_text, re.IGNORECASE):
                    candidate = match.group(1).replace('\\/', '/')
                    if candidate.startswith('/'):
                        candidate = urljoin(web_url, candidate)
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    urls.append(candidate)

            return urls

        def _extract_subtitles(self, text, web_url):
            subtitles = []
            seen = set()

            for m in re.finditer(r'\{[^{}]*file\s*:\s*["\']([^"\']+)["\'][^{}]*\}', text or "", re.IGNORECASE):
                block = m.group(0)
                sub_file = m.group(1).replace('\\/', '/')
                if not sub_file:
                    continue
                is_sub = bool(
                    re.search(r'kind\s*:\s*["\'](?:captions|subtitles)["\']', block, re.IGNORECASE)
                    or "get_vtt" in sub_file.lower()
                    or "subtitle" in sub_file.lower()
                    or sub_file.lower().endswith((".vtt", ".srt", ".ass", ".ssa"))
                )
                if not is_sub:
                    continue
                if not sub_file.startswith("http"):
                    sub_file = urljoin(web_url, sub_file)
                if sub_file in seen:
                    continue
                label_match = re.search(r'label\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
                kind_match = re.search(r'kind\s*:\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
                subtitles.append({
                    "label": label_match.group(1) if label_match else "English",
                    "file": sub_file,
                    "kind": kind_match.group(1) if kind_match else "captions"
                })
                seen.add(sub_file)

            return subtitles

        def get_url(self, url, referer=None):
            web_url = self._normalize_target_url(url)
            parsed_web = urlparse(web_url)
            web_origin = f"{parsed_web.scheme}://{parsed_web.netloc}" if parsed_web.scheme and parsed_web.netloc else self.main_url
            headers = {
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
                "Origin": web_origin,
                "User-Agent": "Mozilla/5.0",
            }

            response = requests.get(web_url, headers={**headers, "Referer": referer or f"{web_origin}/"})
            soup = BeautifulSoup(response.text, "html.parser")

            page_blob = response.text + "\n" + "\n".join(
                (script.string or script.get_text() or "") for script in soup.find_all("script")
            )
            unpacked_blob = self._extract_packed_data(page_blob) or ""
            subtitles = self._extract_subtitles(unpacked_blob or page_blob, web_url)

            source = self._extract_var_links_source(unpacked_blob, web_url) or self._extract_var_links_source(page_blob, web_url)
            if not source:
                source = self._extract_var_o_source(unpacked_blob, web_url) or self._extract_var_o_source(page_blob, web_url)
            if not source:
                m3u8_urls = self._extract_m3u8_urls((unpacked_blob + "\n" + page_blob), web_url)
                source = m3u8_urls[0] if m3u8_urls else None

            return {
                "source": source,
                "referer": referer or f"{web_origin}/" if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    class Ryderjet(VidHidePro):
        main_url = "https://ryderjet.com"

    class VidHideHub(VidHidePro):
        main_url = "https://vidhidehub.com"

    class VidHidePro1(VidHidePro):
        main_url = "https://filelions.live"

    class VidHidePro2(VidHidePro):
        main_url = "https://filelions.online"

    class VidHidePro3(VidHidePro):
        main_url = "https://filelions.to"

    class VidHidePro4(VidHidePro):
        main_url = "https://kinoger.be"

    class VidHidePro5(VidHidePro):
        main_url = "https://vidhidevip.com"

    class VidHidePro6(VidHidePro):
        main_url = "https://vidhidepre.com"

    class Smoothpre(VidHidePro):
        name = "EarnVids"
        main_url = "https://smoothpre.com"

    class Dhtpre(VidHidePro):
        name = "EarnVids"
        main_url = "https://dhtpre.com"

    class Peytonepre(VidHidePro):
        name = "EarnVids"
        main_url = "https://peytonepre.com"

    class Vidmoly:
        name = "Vidmoly"
        main_url = "https://vidmoly.net"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _add_marks(self, text, key):
            return re.sub(rf'"?{re.escape(key)}"?', f'"{key}"', text)

        def _parse_source_file(self, script_text):
            match = re.search(r'sources\s*:\s*\[(.*?)\]', script_text, re.DOTALL | re.IGNORECASE)
            if not match:
                return None

            source_blob = match.group(1)
            source_blob = self._add_marks(source_blob, "file").replace("'", '"')

            file_match = re.search(r'"file"\s*:\s*"(.*?)"', source_blob, re.DOTALL | re.IGNORECASE)
            if file_match:
                return file_match.group(1).replace('\\/', '/')
            return None

        def _parse_subtitles(self, script_text):
            subtitles = []
            match = re.search(r'tracks\s*:\s*\[(.*?)\]', script_text, re.DOTALL | re.IGNORECASE)
            if not match:
                return subtitles

            tracks_blob = match.group(1)
            for item in re.finditer(r'\{(.*?)\}', tracks_blob, re.DOTALL):
                entry = item.group(1)
                entry = self._add_marks(entry, "file")
                entry = self._add_marks(entry, "label")
                entry = self._add_marks(entry, "kind")
                entry = entry.replace("'", '"')

                file_match = re.search(r'"file"\s*:\s*"(.*?)"', entry, re.DOTALL | re.IGNORECASE)
                label_match = re.search(r'"label"\s*:\s*"(.*?)"', entry, re.DOTALL | re.IGNORECASE)
                kind_match = re.search(r'"kind"\s*:\s*"(.*?)"', entry, re.DOTALL | re.IGNORECASE)

                if file_match and (kind_match is None or kind_match.group(1) == "captions"):
                    subtitles.append({
                        "label": label_match.group(1) if label_match else "English",
                        "file": file_match.group(1).replace('\\/', '/'),
                        "kind": kind_match.group(1) if kind_match else "captions"
                    })

            return subtitles

        def get_url(self, url, referer=None):
            headers = {
                "user-agent": "Mozilla/5.0",
                "Sec-Fetch-Dest": "iframe"
            }

            if referer:
                headers["Referer"] = referer
            else:
                headers["Referer"] = f"{self.main_url}/"

            new_url = url.replace("/w/", "/embed-") + ".html" if "/w/" in url else url
            response = requests.get(new_url, headers=headers)
            soup = BeautifulSoup(response.text, "html.parser")
            script_text = None

            for script in soup.find_all("script"):
                text = script.get_text() or script.string or ""
                if "sources:" in text:
                    script_text = text
                    break

            m3u8_url = self._parse_source_file(script_text or "") if script_text else None
            subtitles = self._parse_subtitles(script_text or "") if script_text else []

            return {
                "source": m3u8_url,
                "referer": referer or f"{self.main_url}/" if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    class Vidmolyme(Vidmoly):
        main_url = "https://vidmoly.me"

    class Vidmolyto(Vidmoly):
        main_url = "https://vidmoly.to"

    class Vidmolybiz(Vidmoly):
        main_url = "https://vidmoly.biz"

    class GDMirrorbot:
        name = "Gdmirrorbot"
        main_url = "https://gdmirrorbot.nl"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _get_base_url(self, url):
            try:
                parsed = urlparse(url)
                return f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                return self.main_url

        def _decode_mresult(self, value):
            if isinstance(value, dict):
                return value
            if not isinstance(value, str):
                return {}

            raw = value.strip()
            if not raw:
                return {}

            try:
                decoded = base64.b64decode(raw).decode("utf-8")
                return json.loads(decoded) if decoded.strip().startswith("{") else {}
            except Exception:
                try:
                    return json.loads(raw)
                except Exception:
                    return {}

        def _safe_json(self, text):
            try:
                return json.loads(text)
            except Exception:
                return {}

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": referer or f"{self.main_url}/"
            }

            page_text = requests.get(url, headers=headers).text

            sid = url.split("embed/")[-1].split("?")[0].split("#")[0]
            host = self._get_base_url(requests.get(url, headers=headers).url)

            if "key=" in url:
                final_id = re.search(r'FinalID\s*=\s*"([^"]+)"', page_text)
                my_key = re.search(r'myKey\s*=\s*"([^"]+)"', page_text)
                id_type = re.search(r'idType\s*=\s*"([^"]+)"', page_text)
                base_url_match = re.search(r'let\s+baseUrl\s*=\s*"([^"]+)"', page_text)
                host_url = self._get_base_url(base_url_match.group(1)) if base_url_match else None

                if final_id and my_key:
                    if "/tv/" in url:
                        season = re.search(r"/tv/\d+/(\d+)/", url)
                        episode = re.search(r"/tv/\d+/\d+/(\d+)", url)
                        api_url = (
                            f"{self.main_url}/myseriesapi?tmdbid={final_id.group(1)}"
                            f"&season={season.group(1) if season else '1'}"
                            f"&epname={episode.group(1) if episode else '1'}"
                            f"&key={my_key.group(1)}"
                        )
                    else:
                        api_url = f"{self.main_url}/mymovieapi?{(id_type.group(1) if id_type else 'imdbid')}={final_id.group(1)}&key={my_key.group(1)}"

                    page_text = requests.get(api_url, headers=headers).text
                    if host_url:
                        host = host_url

            json_object = self._safe_json(page_text)
            if not isinstance(json_object, dict):
                json_object = {}

            embed_id = url.split("/")[-1].split("?")[0].split("#")[0]
            sid_value = embed_id
            data = json_object.get("data")
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    sid_value = first.get("fileslug") or sid_value

            post_data = {"sid": sid_value}
            response_text = requests.post(f"{host}/embedhelper.php", data=post_data, headers=headers).text

            root = self._safe_json(response_text)
            if not isinstance(root, dict):
                return []

            site_urls = root.get("siteUrls") or {}
            site_friendly_names = root.get("siteFriendlyNames") or {}
            mresult = self._decode_mresult(root.get("mresult"))

            stream_items = []
            for key in site_urls.keys():
                if key not in mresult:
                    continue

                base = str(site_urls.get(key, "")).rstrip("/")
                path = str(mresult.get(key, "")).lstrip("/")
                if not base or not path:
                    continue

                full_url = f"{base}/{path}"
                friendly_name = site_friendly_names.get(key) or key

                try:
                    nested = get_link(full_url)
                except Exception:
                    nested = None

                if nested:
                    stream_items.append({
                        "Provider": friendly_name,
                        "Url": full_url,
                        "Streaming Links": nested
                    })
                else:
                    stream_items.append({
                        "Provider": friendly_name,
                        "Url": full_url,
                        "Streaming Links": ""
                    })

            return stream_items

    class AbyssProvider:
        name = "HydraX"
        main_url = "https://abysscdn.com"
        requires_referer = True
        charset = "RB0fpH8ZEyVLkv7c2i6MAJ5u3IKFDxlS1NTsnGaqmXYdUrtzjwObCgQP94hoeW+/="

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url.rstrip("/")
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _custom_decode(self, encoded):
            out = bytearray()
            for i in range(0, len(encoded), 4):
                chunk = encoded[i:i + 4].ljust(4, "=")
                c = [self.charset.index(ch) if ch in self.charset else 64 for ch in chunk]
                out.append((c[0] << 2) | (c[1] >> 4))
                if c[2] != 64:
                    out.append(((c[1] & 15) << 4) | (c[2] >> 2))
                if c[3] != 64:
                    out.append(((c[2] & 3) << 6) | c[3])
            return out.decode("utf-8", "ignore")

        def _hex_to_int(self, m):
            return str(int(m.group(0), 16))

        def _to_base_36(self, n):
            if n == 0:
                return ""
            alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
            return self._to_base_36(n // 36) + alphabet[n % 36]

        def _replacer(self, m, offset, arr):
            val = int(m.group(1))
            idx = val - offset
            if 0 <= idx < len(arr):
                return f"'{arr[idx]}'"
            return m.group(0)

        def _join_strings(self, m):
            parts = re.findall(r"'([^']*)'", m.group(0))
            return "'" + "".join(parts) + "'"

        def _deobfuscate(self, code):
            if not code or execjs is None:
                return code

            code = re.sub(r"0[xX][0-9a-fA-F]+", self._hex_to_int, code)

            offset = 0
            m = re.search(r"function\s*[\w$]+\([\w$]+,[\w$]+\)\{.*?return.*?[\w$]+=[\w$]+-(.*?);.*?\}", code, re.DOTALL)
            if m:
                try:
                    offset = int(eval(m.group(1)))
                except Exception:
                    offset = 0

            m = re.search(r"\(function\(.*?\)\{.*?var\s([\w$]+)=.*?while.*?var\s\w+=(.*?);.*?\}\([\w$]+,(.*?)\)\)", code, re.DOTALL)
            if not m:
                return code

            fun_name, start_value, target_expr = m.group(1), m.group(2), m.group(3)
            try:
                target = int(eval(target_expr))
            except Exception:
                return code

            arr_m = re.search(r"function\s[\w$]+\(\)\{var\s[\w$]+=(\[.*?\]);.*?\}.*?\}", code, re.DOTALL)
            if not arr_m:
                return code

            array_literal = arr_m.group(1)
            js = (
                f"arr={array_literal};"
                f"function {fun_name}(i){{offset={offset};return arr[i-offset]}};"
                f"while(true){{s_val={start_value};if(s_val==={target}){{break;}}arr.push(arr.shift())}}"
                "arr;"
            )
            try:
                rotated = execjs.compile(js).eval("arr")
            except Exception:
                return code

            code = re.sub(r"[\w$]+\((\d+)\)", lambda x: self._replacer(x, offset, rotated), code)
            code = re.sub(r"'(?:[^']*)'(?:\s*\+\s*'[^']*')+", self._join_strings, code)
            return code

        def _extract_datas_payload(self, html_text):
            match = re.search(r'(?:const|var)\s+datas\s*=\s*"([^"]+)"', html_text or "")
            if not match:
                return {}

            try:
                raw = base64.b64decode(match.group(1).strip())
            except Exception:
                return {}

            # Fast path: well-formed JSON payload.
            try:
                decoded_utf8 = raw.decode("utf-8")
                payload = json.loads(decoded_utf8)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass

            # Fallback path: some pages embed a JSON-like blob where `media` carries binary bytes.
            decoded = raw.decode("latin-1", "ignore")
            payload = {}

            slug_match = re.search(r'"slug"\s*:\s*"([^"]+)"', decoded)
            md5_match = re.search(r'"md5_id"\s*:\s*(\d+)', decoded)
            user_match = re.search(r'"user_id"\s*:\s*(\d+)', decoded)

            if slug_match:
                payload["slug"] = slug_match.group(1)
            if md5_match:
                try:
                    payload["md5_id"] = int(md5_match.group(1))
                except Exception:
                    payload["md5_id"] = md5_match.group(1)
            if user_match:
                try:
                    payload["user_id"] = int(user_match.group(1))
                except Exception:
                    payload["user_id"] = user_match.group(1)

            config_match = re.search(r'"config"\s*:\s*(\{.*?\})(?:,|\s*\})', decoded, re.DOTALL)
            if config_match:
                try:
                    payload["config"] = json.loads(config_match.group(1))
                except Exception:
                    pass

            # Preferred: extract raw escaped media body from the decoded bytes.
            media_marker = b'"media":"'
            config_marker = b'","config"'
            m_idx = raw.find(media_marker)
            c_idx = raw.find(config_marker)
            media_escaped = None
            if m_idx >= 0 and c_idx > m_idx:
                try:
                    media_escaped = raw[m_idx + len(media_marker):c_idx].decode("latin-1", "ignore")
                except Exception:
                    media_escaped = None

            # Fallback regex when byte markers are absent.
            if media_escaped is None:
                media_match = re.search(r'"media"\s*:\s*"((?:\\.|[^"\\])*)"', decoded, re.DOTALL)
                media_escaped = media_match.group(1) if media_match else None

            if media_escaped is not None:
                payload["media"] = self._decode_escaped_binary_string(media_escaped)

            return payload if payload else {}

        def _decode_escaped_binary_string(self, escaped_value):
            if escaped_value is None:
                return ""

            out = []
            i = 0
            esc_map = {
                "n": "\n",
                "r": "\r",
                "t": "\t",
                "b": "\b",
                "f": "\f",
                "\\": "\\",
                '"': '"',
                "/": "/",
            }

            while i < len(escaped_value):
                ch = escaped_value[i]
                if ch == "\\" and i + 1 < len(escaped_value):
                    nxt = escaped_value[i + 1]
                    if nxt == "u" and i + 5 < len(escaped_value):
                        hex_part = escaped_value[i + 2:i + 6]
                        try:
                            out.append(chr(int(hex_part, 16)))
                            i += 6
                            continue
                        except Exception:
                            pass

                    if nxt in esc_map:
                        out.append(esc_map[nxt])
                        i += 2
                        continue

                out.append(ch)
                i += 1

            return "".join(out)

        def _derive_md5_key(self, value):
            """
            Mirrors AbyssVideoDownloader CryptoHelper.getKey(value):
            - Number => each digit converted to numeric byte (0-9), then MD5
            - Others => UTF-8 bytes, then MD5
            """
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
                key_bytes = bytearray()
                for ch in text:
                    if ch.isdigit():
                        key_bytes.append(int(ch))
                    else:
                        key_bytes.append(ord(ch) & 0xFF)
                digest_source = bytes(key_bytes)
            else:
                digest_source = str(value).encode("utf-8")

            return hashlib.md5(digest_source).hexdigest()

        def _aes_ctr_transform(self, input_bytes, key_seed):
            if AES is None:
                return None

            md5_hex = self._derive_md5_key(key_seed)
            key_bytes = md5_hex.encode("utf-8")
            iv = key_bytes[:16]

            try:
                cipher = AES.new(
                    key_bytes,
                    AES.MODE_CTR,
                    nonce=b"",
                    initial_value=int.from_bytes(iv, "big")
                )
                return cipher.encrypt(input_bytes)
            except Exception:
                return None

        def _decrypt_media(self, encrypted_text, user_id, slug, md5_id):
            if not encrypted_text or not user_id or not slug or not md5_id:
                return {}

            key_seed = f"{user_id}:{slug}:{md5_id}"
            if np is not None:
                raw_bytes = np.fromiter((ord(ch) & 0xFF for ch in encrypted_text), dtype=np.uint8).tobytes()
            else:
                raw_bytes = bytes((ord(ch) & 0xFF) for ch in encrypted_text)
            transformed = self._aes_ctr_transform(raw_bytes, key_seed)
            if not transformed:
                return {}

            try:
                media_text = transformed.decode("utf-8", "ignore")
                media_json = json.loads(media_text)
                return media_json if isinstance(media_json, dict) else {}
            except Exception:
                return {}

        def _build_sora_token(self, path_value, size_value):
            if not path_value or not size_value:
                return None

            transformed = self._aes_ctr_transform(path_value.encode("utf-8"), size_value)
            if not transformed:
                return None

            try:
                first = base64.b64encode(transformed).decode("utf-8").replace("=", "")
                second = base64.b64encode(first.encode("utf-8")).decode("utf-8").replace("=", "")
                return second
            except Exception:
                return None

        def _collect_subtitles(self, media_payload, md5_id):
            subtitles = []
            seen = set()

            def add_sub(label, file_url, kind="captions"):
                if not isinstance(file_url, str) or not file_url:
                    return
                normalized = file_url.replace('\\/', '/')
                if not normalized.startswith("http"):
                    normalized = urljoin("https://st.iamcdn.net/", normalized)
                if normalized in seen:
                    return
                subtitles.append({
                    "label": (label or "English"),
                    "file": normalized,
                    "kind": (kind or "captions")
                })
                seen.add(normalized)

            # Common abyss payload: [{slug, type, lang}, ...]
            sub_list = media_payload.get("subtitles") if isinstance(media_payload, dict) else []
            if isinstance(sub_list, list):
                for item in sub_list:
                    if not isinstance(item, dict):
                        continue
                    sub_slug = item.get("slug")
                    sub_type = item.get("type")
                    lang = item.get("lang") or item.get("label")

                    direct_url = item.get("url") or item.get("file")
                    if isinstance(direct_url, str) and direct_url:
                        add_sub(lang, direct_url, item.get("kind") or "captions")
                        continue

                    if sub_slug and sub_type and md5_id:
                        add_sub(lang, f"https://st.iamcdn.net/proxy/subtitle/{md5_id}/{sub_slug}.{sub_type}", "captions")

            # Alternate payload shapes: dict mapping language->url
            if isinstance(sub_list, dict):
                for lang, raw_url in sub_list.items():
                    if isinstance(raw_url, str):
                        add_sub(str(lang), raw_url, "captions")

            # Track-based fallback found in some players.
            tracks_candidates = []
            if isinstance(media_payload, dict):
                tracks_candidates.append(media_payload.get("tracks"))
                mp4 = media_payload.get("mp4") if isinstance(media_payload.get("mp4"), dict) else {}
                hls = media_payload.get("hls") if isinstance(media_payload.get("hls"), dict) else {}
                tracks_candidates.append(mp4.get("tracks"))
                tracks_candidates.append(hls.get("tracks"))

            for tracks in tracks_candidates:
                if not isinstance(tracks, list):
                    continue
                for tr in tracks:
                    if not isinstance(tr, dict):
                        continue
                    kind = tr.get("kind") or "captions"
                    file_url = tr.get("file") or tr.get("url") or tr.get("src")
                    is_sub = bool(
                        kind in {"captions", "subtitles"}
                        or (isinstance(file_url, str) and file_url.lower().endswith((".vtt", ".srt", ".ass", ".ssa")))
                    )
                    if not is_sub:
                        continue
                    add_sub(tr.get("label") or tr.get("lang"), file_url, kind)

            return subtitles

        def _collect_mp4_source_lists(self, media_payload):
            all_sources = []
            first_data_sources = []
            sources_detail = []
            first_data_sources_detail = []
            seen_main = set()
            seen_fd = set()

            if not isinstance(media_payload, dict):
                return all_sources, first_data_sources, sources_detail, first_data_sources_detail

            mp4 = media_payload.get("mp4") if isinstance(media_payload.get("mp4"), dict) else {}
            quality_by_res_id = {}

            # Real full sources.
            for src in mp4.get("sources") if isinstance(mp4.get("sources"), list) else []:
                if not isinstance(src, dict):
                    continue
                label = src.get("label") or "unknown"
                res_id = src.get("res_id")
                if res_id is not None:
                    quality_by_res_id[res_id] = label

                direct_file = src.get("file")
                if isinstance(direct_file, str) and direct_file:
                    normalized = direct_file.replace('\\/', '/')
                    if normalized not in seen_main:
                        all_sources.append(normalized)
                        sources_detail.append({
                            "quality": label,
                            "url": normalized,
                            "size": src.get("size"),
                            "codec": src.get("codec")
                        })
                        seen_main.add(normalized)
                    continue

                src_url = src.get("url")
                src_path = src.get("path")
                if isinstance(src_url, str) and src_url and isinstance(src_path, str) and src_path:
                    normalized = f"{src_url.rstrip('/')}/{src_path.lstrip('/')}".replace('\\/', '/')
                    if normalized not in seen_main:
                        all_sources.append(normalized)
                        sources_detail.append({
                            "quality": label,
                            "url": normalized,
                            "size": src.get("size"),
                            "codec": src.get("codec")
                        })
                        seen_main.add(normalized)

            # First-data URLs are often partial chunks (~4MB).
            for fd in mp4.get("fristDatas") if isinstance(mp4.get("fristDatas"), list) else []:
                if not isinstance(fd, dict):
                    continue
                fd_url = fd.get("url")
                if isinstance(fd_url, str) and fd_url:
                    normalized = fd_url.replace('\\/', '/')
                    if normalized not in seen_fd:
                        first_data_sources.append(normalized)
                        fd_res_id = fd.get("res_id")
                        fd_quality = quality_by_res_id.get(fd_res_id) if fd_res_id is not None else None
                        first_data_sources_detail.append({
                            "quality": fd_quality or f"res-{fd_res_id}" if fd_res_id is not None else "unknown",
                            "url": normalized,
                            "size": fd.get("size"),
                            "codec": fd.get("codec")
                        })
                        seen_fd.add(normalized)

            return all_sources, first_data_sources, sources_detail, first_data_sources_detail

        def _extract_from_media_payload(self, media_payload, slug, md5_id):
            if not isinstance(media_payload, dict):
                return None, [], [], [], [], []

            subtitles = self._collect_subtitles(media_payload, md5_id)
            all_sources, first_data_sources, sources_detail, first_data_sources_detail = self._collect_mp4_source_lists(media_payload)

            if all_sources:
                return all_sources[0], subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail

            # Prefer direct file source when present.
            mp4 = media_payload.get("mp4") if isinstance(media_payload.get("mp4"), dict) else {}
            sources = mp4.get("sources") if isinstance(mp4.get("sources"), list) else []
            for src in sources:
                if not isinstance(src, dict):
                    continue

                direct_file = src.get("file")
                if isinstance(direct_file, str) and direct_file:
                    return direct_file.replace('\\/', '/'), subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail

                src_url = src.get("url")
                src_path = src.get("path")
                if isinstance(src_url, str) and src_url and isinstance(src_path, str) and src_path:
                    return f"{src_url.rstrip('/')}/{src_path.lstrip('/')}".replace('\\/', '/'), subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail

            # Direct HLS forms used by some Abyss variants.
            hls = media_payload.get("hls") if isinstance(media_payload.get("hls"), dict) else {}
            for key in ["file", "url", "master", "src", "source"]:
                val = hls.get(key)
                if isinstance(val, str) and val:
                    return val.replace('\\/', '/'), subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail

            hls_sources = hls.get("sources") if isinstance(hls.get("sources"), list) else []
            for hs in hls_sources:
                if not isinstance(hs, dict):
                    continue
                hls_file = hs.get("file") or hs.get("url") or hs.get("src")
                if isinstance(hls_file, str) and hls_file:
                    return hls_file.replace('\\/', '/'), subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail

            # Build sorastream URL from domains + source metadata if direct file is absent.
            domains = mp4.get("domains") if isinstance(mp4.get("domains"), list) else []
            for src in sources:
                if not isinstance(src, dict):
                    continue
                size = src.get("size")
                res_id = src.get("res_id")
                sub = src.get("sub")
                if not size or not res_id or not sub or not md5_id or not slug:
                    continue

                domain = next((d for d in domains if isinstance(d, str) and sub in d), None)
                if not domain:
                    continue

                path_value = f"/mp4/{md5_id}/{res_id}/{size}?v={slug}"
                size_key = int(size) if isinstance(size, str) and size.isdigit() else size
                token = self._build_sora_token(path_value, size_key)
                if token:
                    normalized_domain = domain if domain.startswith("http") else f"https://{domain}"
                    return f"{normalized_domain.rstrip('/')}/sora/{size}/{token}", subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail

            # HLS fallback (service-worker dependent in browser, still useful as hint).
            hls_id = hls.get("id")
            if hls_id:
                return f"{self.main_url.rstrip('/')}/#hls/{hls_id}/master.m3u8", subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail

            return None, subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail

        def get_url(self, url, referer=None):
            # short.icu / short.ink often carry the raw video id in path; normalize to abysscdn query format.
            parsed_input = urlparse(url)
            if (
                (parsed_input.netloc.lower().endswith("short.icu")
                 or parsed_input.netloc.lower().endswith("short.ink")
                 or parsed_input.netloc.lower().endswith("abyssplayer.com"))
                and "v=" not in (parsed_input.query or "")
            ):
                token = (parsed_input.path or "").strip("/").split("/")[0]
                if token:
                    url = f"https://abysscdn.com/?v={token}"

            user_agent = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"
            uri = urlparse(url)
            default_domain = f"{uri.scheme}://{uri.netloc}/"
            headers = {
                "Referer": default_domain,
                "User-Agent": user_agent,
            }

            try:
                session = cloudscraper.create_scraper() if cloudscraper else requests
                html = session.get(url, headers=headers, timeout=20).text
            except Exception:
                html = ""

            if not html:
                return {
                    "source": None,
                    "referer": default_domain if self.requires_referer else None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }

            soup = BeautifulSoup(html, "html.parser")

            # New player variant: base64 payload in `datas` then AES-CTR encrypted `media` blob.
            datas_payload = self._extract_datas_payload(html)
            if datas_payload:
                slug = datas_payload.get("slug")
                md5_id = datas_payload.get("md5_id")
                user_id = datas_payload.get("user_id")
                media_blob = datas_payload.get("media")
                media_payload = media_blob if isinstance(media_blob, dict) else self._decrypt_media(media_blob, user_id, slug, md5_id)
                source, subtitles, all_sources, first_data_sources, sources_detail, first_data_sources_detail = self._extract_from_media_payload(media_payload, slug, md5_id)

                if source:
                    return {
                        "source": source,
                        "sources": all_sources,
                        "sourcesDetail": sources_detail,
                        "firstDataSources": first_data_sources,
                        "firstDataSourcesDetail": first_data_sources_detail,
                        "referer": default_domain if self.requires_referer else None,
                        "requiresReferer": self.requires_referer,
                        "subtitles": subtitles
                    }

            scripts = soup.find_all("script")
            script_obfuscated = (scripts[7].get_text() if len(scripts) > 7 else "") or ""
            if not script_obfuscated:
                for s in scripts:
                    t = s.get_text() or ""
                    if "md5_id" in t or "slug" in t or "eval(function" in t:
                        script_obfuscated = t
                        break

            deobfuscated = self._deobfuscate(script_obfuscated)

            metadata = {}
            em = re.search(r"[\w$]+=\'(.*?)_\'", deobfuscated or "")
            if em:
                try:
                    metadata = json.loads(self._custom_decode(em.group(1)))
                except Exception:
                    metadata = {}

            def g(pattern):
                m = re.search(pattern, deobfuscated or "")
                return m.group(1) if m else ""

            metadata["id"] = metadata.get("id") or g(r"'id':'(edns.*?)'")
            metadata["slug"] = metadata.get("slug") or g(r"'slug':'(.*?)'")
            metadata["md5_id"] = metadata.get("md5_id") or g(r"'md5_id':(\d+)")
            metadata["domain"] = metadata.get("domain") or g(r"'domain':'(.*?)'")

            source = None
            if metadata.get("domain") and metadata.get("id"):
                source = f"https://{metadata['domain'].strip('/')}/{metadata['id']}"

            return {
                "source": source,
                "sources": [source] if source else [],
                "sourcesDetail": [{"quality": "unknown", "url": source, "size": None, "codec": None}] if source else [],
                "firstDataSources": [],
                "firstDataSourcesDetail": [],
                "referer": default_domain if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": []
            }

    class HydraXcdnBiz(AbyssProvider):
        main_url = "https://hydraxcdn.biz"

    class ShortIcu(AbyssProvider):
        main_url = "https://short.icu"

    class ShortInk(AbyssProvider):
        main_url = "https://short.ink"

    class ByseExtractor:
        name = "Byse"
        main_url = "https://streamlyplayer.online"
        requires_referer = True
        redirect_domains = {"boosteradx.online", "byse.sx"}

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url.rstrip("/")
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _b64_url_decode(self, value):
            value = value.replace('-', '+').replace('_', '/')
            value += '=' * (-len(value) % 4)
            return base64.b64decode(value)

        def _extract_code(self, url):
            m = re.search(r'/(?:e|d|download)/([^/?#]+)', url)
            if m:
                return m.group(1)
            parsed = urlparse(url)
            last = parsed.path.rstrip('/').split('/')[-1]
            return last.replace('.html', '') if last else ""

        def _get_api_host(self, url):
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            scheme = parsed.scheme or "https"
            if host in self.redirect_domains:
                return self.main_url
            if host:
                return f"{scheme}://{host}"
            return self.main_url

        def _decode_playback(self, payload):
            if not isinstance(payload, dict):
                return {}

            iv_b64 = payload.get("iv")
            key_parts = payload.get("key_parts") or []
            ciphertext_b64 = payload.get("payload")

            if AES is None or not iv_b64 or not key_parts or not ciphertext_b64:
                return {}

            try:
                iv = self._b64_url_decode(iv_b64)
                key = b"".join(self._b64_url_decode(part) for part in key_parts)
                ciphertext = self._b64_url_decode(ciphertext_b64)

                ciphertext_data = ciphertext[:-16]
                tag = ciphertext[-16:]

                cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
                plaintext = cipher.decrypt_and_verify(ciphertext_data, tag)
                decoded = json.loads(plaintext.decode("latin-1", "ignore"))
                return decoded if isinstance(decoded, dict) else {}
            except Exception:
                return {}

        def _pick_source(self, sources):
            if not isinstance(sources, list):
                return None

            def quality_value(label):
                if not isinstance(label, str):
                    return 0
                m = re.search(r"(\d{3,4})", label)
                return int(m.group(1)) if m else 0

            candidates = []
            for item in sources:
                if not isinstance(item, dict):
                    continue
                src = item.get("url") or item.get("file") or item.get("src")
                if not src:
                    continue
                candidates.append((quality_value(item.get("label")), src))

            if not candidates:
                return None

            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]

        def get_url(self, url, referer=None):
            parsed = urlparse(url)
            default_domain = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else self.main_url
            api_host = self._get_api_host(url)
            user_agent = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36"
            headers = {
                "Accept": "*/*",
                "Referer": (referer or f"{default_domain.rstrip('/')}/"),
                "X-Embed-Parent": url,
                "User-Agent": user_agent,
            }

            code = self._extract_code(url)
            if not code:
                return {
                    "source": None,
                    "referer": default_domain if self.requires_referer else None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }

            source = None
            subtitles = []
            referer_domain = f"{api_host.rstrip('/')}/"

            try:
                payload = {}
                # Current API (v>=3): metadata + encrypted playback are returned
                # together by GET /api/videos/{code}; older deployments used the
                # separate /embed/playback endpoint.
                for endpoint in (
                    f"{api_host.rstrip('/')}/api/videos/{code}",
                    f"{api_host.rstrip('/')}/api/videos/{code}/embed/playback",
                ):
                    try:
                        resp = requests.get(endpoint, headers=headers, timeout=20)
                        if resp.status_code == 200 and resp.content:
                            payload = resp.json() if resp.content else {}
                            break
                    except Exception:
                        continue

                # Direct mode: {"sources": [...]}.
                source = self._pick_source(payload.get("sources") if isinstance(payload, dict) else [])

                # Encrypted mode: {"playback": {"payload", "key_parts", "iv"}}.
                if not source and isinstance(payload, dict):
                    decrypted = self._decode_playback(payload.get("playback") or {})
                    source = self._pick_source(decrypted.get("sources") if isinstance(decrypted, dict) else [])

                    tracks = decrypted.get("tracks") if isinstance(decrypted, dict) else []
                    if isinstance(tracks, list):
                        for tr in tracks:
                            if not isinstance(tr, dict):
                                continue
                            sub_file = tr.get("file") or tr.get("url")
                            if not sub_file:
                                continue
                            subtitles.append({
                                "label": tr.get("label") or "English",
                                "file": sub_file,
                                "kind": tr.get("kind") or "captions"
                            })
            except Exception:
                source = None

            if not source:
                try:
                    fallback_text = requests.get(url, headers=headers, timeout=20).text
                    m = re.search(r'file\s*:\s*"(https?://[^"\']+\.m3u8[^"\']*)"', fallback_text, re.IGNORECASE)
                    if m:
                        source = m.group(1).replace('\\/', '/')
                except Exception:
                    pass

            return {
                "source": source,
                "referer": referer_domain if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    class FilemoonExtractor(ByseExtractor):
        # Backward-compatible alias
        name = "Byse"
        main_url = "https://streamlyplayer.online"

    class KrakenFiles:
        name = "KrakenFiles"
        main_url = "https://krakenfiles.com"
        requires_referer = True

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url.rstrip("/")
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def _extract_id(self, url):
            m = re.search(r'/(?:view|embed-video)/([0-9a-zA-Z]+)', url)
            if m:
                return m.group(1)
            parsed = urlparse(url)
            last = parsed.path.rstrip('/').split('/')[-1]
            return last if re.fullmatch(r'[0-9a-zA-Z]+', last or "") else None

        def _embed_url(self, url):
            media_id = self._extract_id(url)
            if not media_id:
                return url

            parsed = urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else self.main_url
            return f"{base}/embed-video/{media_id}"

        def get_url(self, url, referer=None):
            parsed = urlparse(url)
            default_domain = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else self.main_url
            embed_url = self._embed_url(url)

            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": referer or f"{default_domain}/",
            }

            try:
                response = requests.get(embed_url, headers=headers, timeout=20)
                html_text = response.text
            except Exception:
                html_text = ""

            source = None
            subtitles = []

            if html_text:
                soup = BeautifulSoup(html_text, "html.parser")

                source_tag = soup.find("source")
                if source_tag and source_tag.get("src"):
                    source = source_tag.get("src")

                if not source:
                    source_match = re.search(r'<source[^>]+src=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
                    if source_match:
                        source = source_match.group(1)

                for tr in soup.find_all("track"):
                    track_file = tr.get("src")
                    if not track_file:
                        continue
                    subtitles.append({
                        "label": tr.get("label") or tr.get("srclang") or "English",
                        "file": urljoin(embed_url, track_file),
                        "kind": tr.get("kind") or "captions"
                    })

            return {
                "source": source,
                "referer": f"{default_domain}/" if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    class Blakite:
        name = "Blakite"
        main_url = "https://blakiteapi.xyz"
        requires_referer = False

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
            }

            try:
                # Extract tmdbId and id from URL: /embed/{tmdbId}/{id}
                parts = url.split("/embed/")
                if len(parts) < 2:
                    return {"source": None, "referer": None, "requiresReferer": self.requires_referer, "subtitles": []}

                id_parts = parts[1].rstrip("/").split("/")
                if len(id_parts) < 2:
                    return {"source": None, "referer": None, "requiresReferer": self.requires_referer, "subtitles": []}

                tmdb_id = id_parts[0]
                episode_id = id_parts[1]

                # Call API to get video data
                api_url = f"https://blakiteapi.xyz/api/get.php?id={episode_id}&tmdbId={tmdb_id}"
                response = requests.get(api_url, headers=headers, timeout=15)
                response.raise_for_status()
                data = response.json()

                if data.get("success") and data.get("data"):
                    data_id = data["data"].get("dataId")
                    if data_id:
                        # Construct video URL
                        video_url = f"https://hugh.cdn.rumble.cloud/video/{data_id}.caa.mp4"
                        return {
                            "source": video_url,
                            "referer": None,
                            "requiresReferer": self.requires_referer,
                            "subtitles": []
                        }

                return {"source": None, "referer": None, "requiresReferer": self.requires_referer, "subtitles": []}
            except Exception:
                return {"source": None, "referer": None, "requiresReferer": self.requires_referer, "subtitles": []}

    class VidSrc:
        name = "Animedekho - VidSrc"
        main_url = "https://animedekho.app/aaa/ad/vidsrc/"
        requires_referer = False

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
                "Referer": referer or self.main_url,
            }

            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                # Extract source from #sourceSelector option value
                source_selector = soup.select_one("#sourceSelector option[value]")
                source = source_selector.get("value") if source_selector else None

                return {
                    "source": source,
                    "referer": referer or self.main_url if self.requires_referer else None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }
            except Exception:
                return {
                    "source": None,
                    "referer": None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }

    class Pixel:
        name = "Pixel"
        main_url = "https://animedekho.app/aaa/pixel/"
        requires_referer = False

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
                "Referer": referer or self.main_url,
            }

            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                
                # Extract file URL from Playerjs initialization
                match = re.search(r'file:\s*["\']([^"\'>]+)["\']', response.text)
                source = match.group(1) if match else None

                return {
                    "source": source,
                    "referer": referer or self.main_url if self.requires_referer else None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }
            except Exception:
                return {
                    "source": None,
                    "referer": None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }

    class OPlayer:
        name = "OPlayer"
        main_url = "https://animedekho.app/aaa/ad/beta/"
        requires_referer = False

        def __init__(self, main_url=None, name=None, requires_referer=None):
            if main_url is not None:
                self.main_url = main_url
            if name is not None:
                self.name = name
            if requires_referer is not None:
                self.requires_referer = requires_referer

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
                "Referer": referer or self.main_url,
            }

            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                # Extract all sources from #sourceSelector options
                selector = soup.select_one("#sourceSelector")
                if selector:
                    options = selector.find_all("option")
                    sources = []
                    for option in options:
                        if option.get("value"):
                            sources.append(option.get("value"))
                    
                    # Return first available source, or all sources as fallback
                    source = sources[0] if sources else None
                else:
                    source = None

                return {
                    "source": source,
                    "referer": referer or self.main_url if self.requires_referer else None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }
            except Exception:
                return {
                    "source": None,
                    "referer": None,
                    "requiresReferer": self.requires_referer,
                    "subtitles": []
                }

    class EmturbovidExtractor:
        main_url = "https://emturbovid.com"
        requires_referer = False

        @staticmethod
        def _extract_play_and_sub(text):
            if not text:
                return None, None

            m3u8_url = None
            sub_url = None

            m3u8_match = re.search(r"var\s+urlPlay\s*=\s*['\"]([^'\"]+)['\"]", text)
            if m3u8_match:
                m3u8_url = m3u8_match.group(1).replace('\\/', '/')

            sub_match = re.search(r"var\s+urlSub\s*=\s*['\"]([^'\"]+)['\"]", text)
            if sub_match:
                sub_url = sub_match.group(1).replace('\\/', '/')

            return m3u8_url, sub_url

        @staticmethod
        def _decode_eval_payload(script_text):
            if not script_text or "eval(function" not in script_text or execjs is None:
                return ""

            try:
                eval_pos = script_text.find("eval(function")
                if eval_pos == -1:
                    return ""

                # Keep only helper decoder function + first eval-packed call.
                helper_start = script_text.rfind("function _0x", 0, eval_pos)
                if helper_start == -1:
                    helper_start = max(0, eval_pos - 800)

                open_paren_pos = script_text.find("(", eval_pos)
                if open_paren_pos == -1:
                    return ""

                depth = 0
                eval_end = -1
                for i in range(open_paren_pos, len(script_text)):
                    ch = script_text[i]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            eval_end = i + 1
                            break

                if eval_end == -1:
                    return ""

                focused_js = script_text[helper_start:eval_end]

                runner = f"""
var __decoded = "";
var eval = function(s) {{
  var out = String(s || "");
  if (out.indexOf("urlPlay") >= 0 || out.indexOf("urlSub") >= 0) {{
    __decoded = out;
  }} else if (!__decoded) {{
    __decoded = out;
  }}
  return s;
}};
try {{
{focused_js}
}} catch (e) {{}}
function __getDecoded() {{ return __decoded; }}
"""
                ctx = execjs.compile(runner)
                decoded = ctx.call("__getDecoded") or ""
                if "urlPlay" in decoded or "urlSub" in decoded:
                    return decoded
                return ""
            except Exception:
                return ""

        @staticmethod
        def _extract_video_id(text):
            if not text:
                return ""
            match = re.search(r"var\s+videoID\s*=\s*['\"]([a-zA-Z0-9]+)['\"]", text)
            return match.group(1) if match else ""

        @staticmethod
        def _parse_subtitle_payload(sub_data):
            subtitles = []

            def _append_sub(item, fallback_label="English"):
                if not isinstance(item, dict):
                    return
                file_url = item.get("file") or item.get("src") or item.get("url")
                if not file_url:
                    return
                subtitles.append({
                    "label": item.get("label") or item.get("language") or fallback_label,
                    "file": file_url,
                    "kind": item.get("kind") or "captions"
                })

            if isinstance(sub_data, list):
                for sub in sub_data:
                    _append_sub(sub)
            elif isinstance(sub_data, dict):
                # common shapes:
                # {"tracks": [{...}]}, {"subtitles": [{...}]}, {"data": [{...}]}, or {"eng": "...vtt"}
                for key in ("tracks", "subtitles", "data", "captions"):
                    candidate = sub_data.get(key)
                    if isinstance(candidate, list):
                        for sub in candidate:
                            _append_sub(sub)
                        if subtitles:
                            return subtitles

                for label, file_url in sub_data.items():
                    if isinstance(file_url, str) and file_url.startswith("http"):
                        subtitles.append({
                            "label": str(label),
                            "file": file_url,
                            "kind": "captions"
                        })

            return subtitles

        def get_url(self, url, referer=None):
            headers = {
                "User-Agent": "Mozilla/5.0",
            }

            if referer:
                headers["Referer"] = referer
            else:
                headers["Referer"] = f"{self.main_url}/"

            res = requests.get(url, headers=headers)
            html_text = res.text or ""
            soup = BeautifulSoup(html_text, "html.parser")

            scripts = soup.find_all("script")

            m3u8_url = None
            sub_url = None

            # 1) Try extracting from script tags first.
            for script in scripts:
                text = script.string or script.get_text() or ""
                parsed_m3u8, parsed_sub = self._extract_play_and_sub(text)
                if parsed_m3u8 and not m3u8_url:
                    m3u8_url = parsed_m3u8
                if parsed_sub and not sub_url:
                    sub_url = parsed_sub

                if (m3u8_url or sub_url):
                    break

                # Some /t/<id> pages use eval-packed payloads.
                decoded_text = self._decode_eval_payload(text)
                parsed_m3u8, parsed_sub = self._extract_play_and_sub(decoded_text)
                if parsed_m3u8 and not m3u8_url:
                    m3u8_url = parsed_m3u8
                if parsed_sub and not sub_url:
                    sub_url = parsed_sub

                if m3u8_url or sub_url:
                    break

            # 2) Fallback for /t/<id> and JS payload pages where no <script> wrapper is present.
            parsed_m3u8, parsed_sub = self._extract_play_and_sub(html_text)
            if parsed_m3u8 and not m3u8_url:
                m3u8_url = parsed_m3u8
            if parsed_sub and not sub_url:
                sub_url = parsed_sub

            if (not m3u8_url or not sub_url) and "eval(function" in html_text:
                decoded_html_payload = self._decode_eval_payload(html_text)
                parsed_m3u8, parsed_sub = self._extract_play_and_sub(decoded_html_payload)
                if parsed_m3u8 and not m3u8_url:
                    m3u8_url = parsed_m3u8
                if parsed_sub and not sub_url:
                    sub_url = parsed_sub

            # 2b) Build subtitle URL fallback from videoID if missing.
            if not sub_url:
                video_id = self._extract_video_id(html_text)
                if not video_id and "decoded_html_payload" in locals():
                    video_id = self._extract_video_id(decoded_html_payload)
                if not video_id and m3u8_url:
                    m = re.search(r"/([a-zA-Z0-9]{8,})/\1\.m3u8", m3u8_url)
                    if m:
                        video_id = m.group(1)
                if video_id:
                    sub_url = f"https://sub.turboviplay.to/sub/{video_id}/{video_id}.json"

            # 3) Last fallback: direct m3u8 in payload.
            if not m3u8_url:
                direct_m3u8 = re.search(r"(https?://[^\"'\s]+\.m3u8[^\"'\s]*)", html_text, re.IGNORECASE)
                if direct_m3u8:
                    m3u8_url = direct_m3u8.group(1).replace('\\/', '/')

            # 3b) Retry subtitle URL fallback after m3u8 fallback.
            if not sub_url and m3u8_url:
                m = re.search(r"/([a-zA-Z0-9]{8,})/\1\.m3u8", m3u8_url)
                if m:
                    video_id = m.group(1)
                    sub_url = f"https://sub.turboviplay.to/sub/{video_id}/{video_id}.json"

            subtitles = []

            if sub_url:
                try:
                    sub_res = requests.get(sub_url, headers=headers, timeout=12)
                    sub_text = (sub_res.text or "").strip()
                    sub_data = None

                    if sub_text:
                        try:
                            sub_data = sub_res.json()
                        except Exception:
                            try:
                                sub_data = json.loads(sub_text)
                            except Exception:
                                sub_data = None

                    subtitles = self._parse_subtitle_payload(sub_data)
                except Exception:
                    pass

            return {
                "source": m3u8_url,
                "referer": headers["Referer"] if self.requires_referer else None,
                "requiresReferer": self.requires_referer,
                "subtitles": subtitles
            }

    @staticmethod
    def get_link(page_url):
        page_url_lower = page_url.lower()
        netloc = urlparse(page_url_lower).netloc

        awstream_extractors = {
            "z.awstream.net": Extractor.AWSStream,
            "as-cdn21.top": Extractor.Ascdn21,
            "play.zephyrflick.top": Extractor.Zephyrflick,
            "beta.awstream.net": Extractor.BetaAwstream,
        }

        for domain, extractor_class in awstream_extractors.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        abyss_extractors = {
            "abysscdn.com": Extractor.AbyssProvider,
            "hydraxcdn.biz": Extractor.HydraXcdnBiz,
            "short.icu": Extractor.ShortIcu,
            "short.ink": Extractor.ShortInk,
            "abyssplayer.com": Extractor.AbyssProvider,
        }

        for domain, extractor_class in abyss_extractors.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        streamsb_extractors = {
            "watchsb.com": Extractor.StreamSB,
            "sblona.com": Extractor.Sblona,
            "lvturbo.com": Extractor.Lvturbo,
            "sbrapid.com": Extractor.Sbrapid,
            "sbface.com": Extractor.Sbface,
            "sbsonic.com": Extractor.Sbsonic,
            "vidgomunimesb.xyz": Extractor.Vidgomunimesb,
            "sbasian.pro": Extractor.Sbasian,
            "sbnet.one": Extractor.Sbnet,
            "keephealth.info": Extractor.Keephealth,
            "sbspeed.com": Extractor.Sbspeed,
            "streamsss.net": Extractor.Streamsss,
            "sbflix.xyz": Extractor.Sbflix,
            "vidgomunime.xyz": Extractor.Vidgomunime,
            "sbthe.com": Extractor.Sbthe,
            "ssbstream.net": Extractor.Ssbstream,
            "sbfull.com": Extractor.SBfull,
            "sbplay1.com": Extractor.StreamSB1,
            "sbplay2.com": Extractor.StreamSB2,
            "sbplay3.com": Extractor.StreamSB3,
            "cloudemb.com": Extractor.StreamSB4,
            "sbplay.org": Extractor.StreamSB5,
            "embedsb.com": Extractor.StreamSB6,
            "pelistop.co": Extractor.StreamSB7,
            "streamsb.net": Extractor.StreamSB8,
            "sbplay.one": Extractor.StreamSB9,
            "sbplay2.xyz": Extractor.StreamSB10,
            "sbbrisk.com": Extractor.StreamSB11,
            "sblongvu.com": Extractor.Sblongvu,
        }

        for domain, extractor_class in streamsb_extractors.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        blakite_domains = ["blakiteapi.xyz"]
        if any(netloc == domain or netloc.endswith(f".{domain}") for domain in blakite_domains):
            return Extractor.Blakite().get_url(page_url)

        vidsrc_domains = ["animedekho.app/aaa/ad/vidsrc"]
        if any(domain in page_url_lower for domain in vidsrc_domains):
            return Extractor.VidSrc().get_url(page_url, referer=page_url)

        pixel_domains = ["animedekho.app/aaa/pixel"]
        if any(domain in page_url_lower for domain in pixel_domains):
            return Extractor.Pixel().get_url(page_url, referer=page_url)

        oplayer_domains = ["animedekho.app/aaa/ad/beta"]
        if any(domain in page_url_lower for domain in oplayer_domains):
            return Extractor.OPlayer().get_url(page_url, referer=page_url)

        streamruby_domains = {
            "streamruby.com": Extractor.Streamruby,
            "sruby.xyz": Extractor.Streamruby,
            "rubystream.xyz": Extractor.Streamruby,
            "tuktukcimamulti.buzz": Extractor.Streamruby,
            "stmruby.com": Extractor.Streamruby,
            "rubystm.com": Extractor.Streamruby,
            "rubyvid.com": Extractor.Streamruby,
            "kinoger.be": Extractor.Streamruby,
        }

        for domain, extractor_class in streamruby_domains.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        streamtape_domains = {
            "streamtape.com": Extractor.StreamTape,
            "watchadsontape.com": Extractor.Watchadsontape,
            "streamtape.net": Extractor.StreamTapeNet,
            "streamtape.xyz": Extractor.StreamTapeXyz,
            "streamtape.site": Extractor.StreamTapeSite,
            "shavetape.cash": Extractor.ShaveTape,
        }

        for domain, extractor_class in streamtape_domains.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        voe_domains = {
            "voe.sx": Extractor.Voe,
            "tubelessceliolymph.com": Extractor.Tubeless,
            "simpulumlamerop.com": Extractor.Simpulumlamerop,
            "urochsunloath.com": Extractor.Urochsunloath,
            "nathanfromsubject.com": Extractor.NathanFromSubject,
            "yip.su": Extractor.Yipsu,
            "metagnathtuggers.com": Extractor.MetaGnathTuggers,
            "donaldlineelse.com": Extractor.Voe1,
        }

        for domain, extractor_class in voe_domains.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        filemoon_domains = [
            "f16px.com", "bysesayeveum.com", "bysetayico.com", "bysevepoin.com", "bysezejataos.com",
            "bysekoze.com", "bysesukior.com", "bysejikuar.com", "bysefujedu.com", "bysedikamoum.com",
            "bysebuho.com", "byse.sx", "filemoon.sx", "filemoon.to", "filemoon.in", "filemoon.link",
            "filemoon.nl", "filemoon.wf", "cinegrab.com", "filemoon.eu", "filemoon.art", "moonmov.pro",
            "96ar.com", "kerapoxy.cc", "furher.in", "1azayf9w.xyz", "81u6xl9d.xyz", "smdfs40r.skin",
            "c1z39.com", "bf0skv.org", "z1ekv717.fun", "l1afav.net", "222i8x.lol", "8mhlloqo.fun",
            "f51rm.com", "xcoic.com", "boosteradx.online", "streamlyplayer.online", "bysewihe.com"
        ]
        if any(netloc == domain or netloc.endswith(f".{domain}") for domain in filemoon_domains):
            return Extractor.ByseExtractor().get_url(page_url)

        vidstack_extractors = {
            "vidstack.io": Extractor.VidStack,
            "server1.uns.bio": Extractor.Server1uns,
            "cloudy.upns.one": Extractor.Cloudy,
            "cloudy.p2pplay.pro": Extractor.CloudyP2P,
            "tpx.p2pstream.vip": Extractor.Cloudytpx,
            "zoro.rpmplay.xyz": Extractor.Cloudyzoro,
        }

        for domain, extractor_class in vidstack_extractors.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        vidhide_extractors = {
            "vidhidepro.com": Extractor.VidHidePro,
            "ryderjet.com": Extractor.Ryderjet,
            "vidhidehub.com": Extractor.VidHideHub,
            "filelions.live": Extractor.VidHidePro1,
            "filelions.online": Extractor.VidHidePro2,
            "filelions.to": Extractor.VidHidePro3,
            "kinoger.be": Extractor.VidHidePro4,
            "vidhidevip.com": Extractor.VidHidePro5,
            "vidhidepre.com": Extractor.VidHidePro6,
            "smoothpre.com": Extractor.Smoothpre,
            "dhtpre.com": Extractor.Dhtpre,
            "peytonepre.com": Extractor.Peytonepre,
        }

        for domain, extractor_class in vidhide_extractors.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        vidhide_domain_family = [
            "filelions.com", "filelions.to", "ajmidyadfihayh.sbs", "alhayabambi.sbs", "vidhideplus.com",
            "moflix-stream.click", "azipcdn.com", "mlions.pro", "alions.pro", "dlions.pro",
            "filelions.live", "motvy55.store", "filelions.xyz", "lumiawatch.top", "filelions.online",
            "javplaya.com", "fviplions.com", "egsyxutd.sbs", "filelions.site", "filelions.co",
            "vidhide.com", "vidhidepro.com", "vidhidevip.com", "javlion.xyz", "fdewsdc.sbs",
            "techradar.ink", "anime7u.com", "coolciima.online", "gsfomqu.sbs", "vidhidepre.com",
            "katomen.online", "vidhide.fun", "vidhidehub.com", "dhtpre.com", "6sfkrspw4u.sbs",
            "streamvid.su", "movearnpre.com", "bingezove.com", "dingtezuni.com", "dinisglows.com",
            "ryderjet.com", "e4xb5c2xnz.sbs", "smoothpre.com", "videoland.sbs", "taylorplayer.com",
            "mivalyo.com", "vidhidefast.com", "peytonepre.com", "dintezuvio.com", "callistanise.com",
            "minochinos.com", "earnvids.xyz", "lookmovie2.skin",
            "hanerix.com", "hanerix.club", "hanerix.cfd"
        ]

        if any(netloc == domain or netloc.endswith(f".{domain}") for domain in vidhide_domain_family):
            return Extractor.VidHidePro(main_url=f"https://{netloc}").get_url(page_url)

        vidmoly_extractors = {
            "vidmoly.net": Extractor.Vidmoly,
            "vidmoly.me": Extractor.Vidmolyme,
            "vidmoly.to": Extractor.Vidmolyto,
            "vidmoly.biz": Extractor.Vidmolybiz,
        }

        for domain, extractor_class in vidmoly_extractors.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        gdmirrorbot_domains = ["gdmirrorbot.nl"]
        if any(netloc == domain or netloc.endswith(f".{domain}") for domain in gdmirrorbot_domains):
            return Extractor.GDMirrorbot().get_url(page_url)

        krakenfiles_domains = ["krakenfiles.com"]
        if any(netloc == domain or netloc.endswith(f".{domain}") for domain in krakenfiles_domains):
            return Extractor.KrakenFiles().get_url(page_url)

        filesim_extractors = {
            "files.im": Extractor.Filesim,
            "multimoviesshg.com": Extractor.Multimoviesshg,
            "guccihide.com": Extractor.Guccihide,
            "ahvsh.com": Extractor.Ahvsh,
            "moviesm4u.com": Extractor.Moviesm4u,
            "streamhide.to": Extractor.StreamhideTo,
            "streamhide.com": Extractor.StreamhideCom,
            "movhide.pro": Extractor.Movhide,
            "ztreamhub.com": Extractor.Ztreamhub,
        }

        for domain, extractor_class in filesim_extractors.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        streamwish_extractors = {
            "streamwish.to": Extractor.StreamWishExtractor,
            "mwish.pro": Extractor.Mwish,
            "dwish.pro": Extractor.Dwish,
            "embedwish.com": Extractor.Ewish,
            "wishembed.pro": Extractor.WishembedPro,
            "kswplayer.info": Extractor.Kswplayer,
            "wishfast.top": Extractor.Wishfast,
            "streamwish.site": Extractor.Streamwish2,
            "sfastwish.com": Extractor.SfastwishCom,
            "strwish.xyz": Extractor.Strwish,
            "strwish.com": Extractor.Strwish2,
            "flaswish.com": Extractor.FlaswishCom,
            "awish.pro": Extractor.Awish,
            "obeywish.com": Extractor.Obeywish,
            "jodwish.com": Extractor.Jodwish,
            "swhoi.com": Extractor.Swhoi,
            "multimovies.cloud": Extractor.Multimovies,
            "uqloads.xyz": Extractor.UqloadsXyz,
            "doodporn.xyz": Extractor.Doodporn,
            "cdnwish.com": Extractor.CdnwishCom,
            "asnwish.com": Extractor.Asnwish,
            "nekowish.my.id": Extractor.Nekowish,
            "neko-stream.click": Extractor.Nekostream,
            "swdyu.com": Extractor.Swdyu,
            "wishonly.site": Extractor.Wishonly,
            "playerwish.com": Extractor.Playerwish,
            "streamhls.to": Extractor.StreamHLS,
            "hlswish.com": Extractor.HlsWish,
        }

        for domain, extractor_class in streamwish_extractors.items():
            if netloc == domain or netloc.endswith(f".{domain}"):
                return extractor_class().get_url(page_url)

        emturbovid_domains = ["emturbovid.com", "turboviplay"]
        if any(domain in page_url_lower for domain in emturbovid_domains):
            return Extractor.EmturbovidExtractor().get_url(page_url)

        return None


def get_link(page_url):
    return Extractor.get_link(page_url)
