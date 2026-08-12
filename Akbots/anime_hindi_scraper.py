# Akbots - Don't Remove Credit - @AkBots_Official
#
# Hindi-dub anime episode scraper — ported from the Anime-Episode-Scraper
# project's main.py (Scraper class, get_provider_name, validate_url,
# generate_episode_urls). Covers Toonstream, AnimeDekho, HindiSubAnime,
# HindiAnimeVerse, WatchAnimeWorld and ToonsHub episode pages.
#
# This stays synchronous (requests + ThreadPoolExecutor, same as the
# original) — Akbots/anime_hindi.py calls into it via asyncio.to_thread(),
# same convention already used elsewhere in this bot for blocking scrapers.

import re
import logging
import threading
import concurrent.futures
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from Akbots.anime_hindi_extractors import get_link

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
DEFAULT_DETAIL_WORKERS = 20

KNOWN_PROVIDERS = {
    "short.icu": "HydraX",
    "emturbovid.com": "Omega",
    "as-cdn21.top": "ZephyrFlicks",
    "play.zephyrflick.top": "ZephyrFlicks",
    "rubystm.com": "StreamRuby",
    "streamruby.com": "StreamRuby",
    "filemoon.to": "FileMoon",
    "bysefujedu.com": "FileMoon",
}

SUPPORTED_DOMAINS = [
    "toonstream", "toon-stream", "animedekho", "hindisubanime",
    "hindianimeverse", "watchanimeworld", "toonshub",
]


def get_provider_name(stream_url: str) -> str:
    netloc = urlparse(stream_url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    for domain, provider in KNOWN_PROVIDERS.items():
        if netloc == domain or netloc.endswith(f".{domain}"):
            return provider
    return netloc.split(".")[0].capitalize() if netloc else "Unknown"


def validate_url(url: str) -> bool:
    parsed = urlparse(url)
    return all([parsed.scheme, parsed.netloc]) and any(d in parsed.netloc for d in SUPPORTED_DOMAINS)


def generate_episode_urls(base_url: str, seasons: Dict[int, int]) -> List[Tuple[str, int, int]]:
    """Given an episode-1 URL and {season: episode_count}, builds every
    episode URL in the batch by substituting the season/episode numbers
    into the site's URL pattern."""
    urls = []

    if "hindianimeverse" in base_url:
        base_slug = re.search(r"/episodes/([^/]+)-s\d+e\d+/?$", base_url)
    elif "toonshub" in base_url:
        base_slug = re.search(r"/episode/([^/]+)/\d+x\d+$", base_url)
    else:
        base_slug = re.search(r"(.+-\d+x)\d+$", base_url)

    if not base_slug:
        raise ValueError("Couldn't recognize this site's episode URL pattern.")

    for season, episodes in seasons.items():
        for ep in range(1, episodes + 1):
            if "hindianimeverse" in base_url:
                episode_url = f"https://hindianimeverse.org/episodes/{base_slug.group(1)}-s{season}e{ep}"
            elif "toonshub" in base_url:
                episode_url = f"https://links.toonshub.xyz/episode/{base_slug.group(1)}/{season}x{ep}"
            else:
                episode_url = f"{base_url.rsplit('-', 1)[0]}-{season}x{ep}"
            urls.append((episode_url, season, ep))

    return urls


class Scraper:
    def __init__(self):
        self.session = requests.Session()
        retry_cfg = Retry(
            total=3, connect=3, read=3, backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=retry_cfg)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:122.0) Gecko/20100101 Firefox/122.0"
        })
        self.page_cache: Dict[str, BeautifulSoup] = {}
        self.streaming_cache: Dict[str, object] = {}
        self._cache_lock = threading.Lock()

    def fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        with self._cache_lock:
            cached = self.page_cache.get(url)
        if cached is not None:
            return cached
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, "html.parser")
                with self._cache_lock:
                    self.page_cache[url] = soup
                return soup
            except requests.RequestException as e:
                logger.debug(f"anime_hindi_scraper: fetch failed ({attempt + 1}/{MAX_RETRIES}) {url}: {e}")
        return None

    def extract_animedekho_details(self, url: str) -> List[Dict[str, str]]:
        try:
            soup = self.fetch_page(url)
            if not soup:
                return []
            body_class = " ".join(soup.select_one("body").get("class", []))
            match = re.search(r"(?:term|postid)-(\d+)", body_class)
            if not match:
                return []
            term = match.group(1)
            results = []
            dynamic_urls = [f"https://animedekho.app/?trdekho={i}&trid={term}&trtype=2" for i in range(11)]
            workers = min(len(dynamic_urls), DEFAULT_DETAIL_WORKERS)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(self.fetch_page, u) for u in dynamic_urls]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        iframe_soup = future.result()
                        if not iframe_soup:
                            continue
                        iframe = iframe_soup.select_one("iframe[src]")
                        if not iframe:
                            continue
                        src = iframe["src"]
                        if src == "":
                            continue
                        if src.startswith("https://animedekho.app/aaa/ad/vidsrc"):
                            results.append({"Provider": "Animedekho - VidSrc", "Url": src})
                        elif src.startswith("https://animedekho.app/aaa/ad/beta"):
                            results.append({"Provider": "BETA", "Url": src})
                        else:
                            results.append({"Provider": get_provider_name(src), "Url": src})
                    except Exception:
                        continue
            return results
        except Exception as e:
            logger.debug(f"anime_hindi_scraper: animedekho error: {e}")
            return []

    def extract_hindisubanime_details(self, url: str) -> List[Dict[str, str]]:
        try:
            soup = self.fetch_page(url)
            if not soup:
                return []
            body_class = " ".join(soup.select_one("body").get("class", []))
            match = re.search(r"(?:term|postid)-(\d+)", body_class)
            if not match:
                return []
            term = match.group(1)
            results = []
            dynamic_urls = [f"https://hindisubanime.co/?trdekho={i}&trid={term}&trtype=2" for i in range(11)]
            workers = min(len(dynamic_urls), DEFAULT_DETAIL_WORKERS)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(self.fetch_page, u) for u in dynamic_urls]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        iframe_soup = future.result()
                        if not iframe_soup:
                            continue
                        iframe = iframe_soup.select_one("iframe[src]")
                        if not iframe:
                            continue
                        src = iframe["src"]
                        if src == "":
                            continue
                        results.append({"Provider": get_provider_name(src), "Url": src})
                    except Exception:
                        continue
            return results
        except Exception as e:
            logger.debug(f"anime_hindi_scraper: hindisubanime error: {e}")
            return []

    def extract_hindianimeverse_details(self, url: str) -> List[Dict[str, str]]:
        try:
            soup = self.fetch_page(url)
            if not soup:
                return []

            post_id = None
            if postid_input := soup.select_one("input[name='postid'][value]"):
                post_id = postid_input.get("value", "").strip()
            if not post_id and (body := soup.select_one("body")):
                body_class = " ".join(body.get("class", []))
                body_match = re.search(r"postid-(\d+)", body_class)
                if body_match:
                    post_id = body_match.group(1)
            if not post_id and (counter_meta := soup.select_one("meta#dooplay-ajax-counter[data-postid]")):
                post_id = counter_meta.get("data-postid", "").strip()
            if article := soup.select_one("article[id^='post-']"):
                post_id = post_id or article.get("id", "").replace("post-", "").strip()
            if not post_id and (with_data_post := soup.select_one("[data-post]")):
                post_id = with_data_post.get("data-post", "").strip()
            if not post_id:
                html = str(soup)
                match = (re.search(r'"post_id"\s*:\s*"?(\d+)"?', html)
                         or re.search(r'\bpost[-_ ]?id\b[^\d]{0,20}(\d+)', html, re.IGNORECASE)
                         or re.search(r'/\?p=(\d+)', html))
                post_id = match.group(1) if match else ""
            if not post_id:
                return []

            ajax_url = "https://hindianimeverse.org/wp-admin/admin-ajax.php"
            ajax_headers = {
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://hindianimeverse.org",
                "Referer": url,
                "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0"),
            }

            r = self.session.post(ajax_url, data={"action": "dl_fetch_api", "post_id": post_id},
                                   headers=ajax_headers, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            html_chunk = r.json().get("data", {}).get("html", "")
            if not html_chunk:
                return []

            chunk_soup = BeautifulSoup(html_chunk, "html.parser")
            encrypt_links = chunk_soup.select("a.doo-encrypt-link")

            api_index_meta: Dict[str, str] = {}
            for item in chunk_soup.select("div.download-file-item"):
                stream_link = item.select_one("a.doo-encrypt-link[data-type='stream'][data-api-index]")
                if not stream_link:
                    continue
                idx = stream_link.get("data-api-index", "").strip()
                if not idx:
                    continue
                badges = [b.get_text(strip=True) for b in item.select(".download-file-meta .download-badge")]
                resolution = next((b for b in badges if re.search(r"\d{3,4}p", b, re.IGNORECASE)), "")
                codec = next((b for b in badges if b.lower() in {"x264", "x265", "h264", "h265", "hevc", "av1"}), "")
                source = next((b for b in badges if b.upper() in {"WEB-DL", "WEBRIP", "BLURAY", "HDRIP", "DVDRIP"}), "")
                if resolution:
                    inner = " ".join(p for p in [codec, source] if p)
                    api_index_meta[idx] = f"{resolution} ({inner})" if inner else resolution
                elif codec or source:
                    api_index_meta[idx] = " ".join(p for p in [codec, source] if p)

            results = []
            seen_urls = set()
            api_candidates = []

            for link in encrypt_links:
                if link.get("data-source", "").strip().lower() != "api":
                    continue
                if link.get("data-type", "").strip().lower() != "stream":
                    continue
                api_index = link.get("data-api-index", "").strip()
                link_post_id = link.get("data-post", "").strip() or post_id
                fallback_url = link.get("data-url", "").strip()
                if not api_index:
                    if fallback_url and fallback_url not in seen_urls:
                        seen_urls.add(fallback_url)
                        results.append({"Provider": get_provider_name(fallback_url), "Url": fallback_url})
                    continue
                api_candidates.append({"api_index": api_index, "link_post_id": link_post_id, "fallback_url": fallback_url})

            def _resolve_candidate(candidate: Dict[str, str]) -> Optional[Dict[str, str]]:
                api_index = candidate["api_index"]
                link_post_id = candidate["link_post_id"]
                fallback_url = candidate["fallback_url"]
                payload = {"action": "doo_get_link", "post_id": link_post_id, "type": "stream",
                           "source": "api", "api_index": api_index}
                resolved_url = ""
                try:
                    r2 = self.session.post(ajax_url, data=payload, headers=ajax_headers, timeout=REQUEST_TIMEOUT)
                    r2.raise_for_status()
                    resolved_url = r2.json().get("data", {}).get("url", "").replace("\\/", "/")
                except Exception:
                    resolved_url = ""

                final_url = resolved_url or fallback_url
                if not final_url:
                    return None
                try:
                    link_response = self.session.get(
                        final_url, headers={"Referer": url, "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0")},
                        timeout=REQUEST_TIMEOUT)
                    m = re.search(r"var targetUrl = '([^']+)'", link_response.text)
                    if m:
                        final_url = m.group(1)
                except Exception:
                    pass

                provider_label = get_provider_name(final_url)
                quality_label = api_index_meta.get(api_index, "")
                if quality_label:
                    provider_label = f"{provider_label} - {quality_label}"
                return {"Provider": provider_label, "Url": final_url}

            if api_candidates:
                workers = min(DEFAULT_DETAIL_WORKERS, len(api_candidates))
                with concurrent.futures.ThreadPoolExecutor(max_workers=max(workers, 1)) as executor:
                    resolved_entries = list(executor.map(_resolve_candidate, api_candidates))
                for entry in resolved_entries:
                    if not entry:
                        continue
                    final_url = entry.get("Url", "")
                    if not final_url or final_url in seen_urls:
                        continue
                    seen_urls.add(final_url)
                    results.append(entry)

            return results
        except Exception as e:
            logger.debug(f"anime_hindi_scraper: hindianimeverse error: {e}")
            return []

    def extract_toonshub_details(self, url: str) -> List[Dict[str, str]]:
        soup = self.fetch_page(url)
        if not soup:
            return []
        result = []
        for card in soup.find_all("div", class_="card"):
            resolution_tag = card.find("h5")
            if not resolution_tag:
                continue
            resolution = resolution_tag.text.strip()
            for a_tag in card.find_all("a", href=True):
                href = a_tag["href"]
                if "/redirect/?url=" not in href:
                    continue
                result.append({
                    "Provider": a_tag.text.strip(),
                    "Url": "https://links.toonshub.xyz" + href,
                    "Resolution": resolution,
                })
        return result

    def scrape_generic(self, url: str) -> List[Dict[str, str]]:
        try:
            soup = self.fetch_page(url)
            if not soup:
                return []
            iframe_sources = []
            for iframe in soup.select("iframe[data-src]"):
                src = iframe.get("data-src", "").strip()
                if src:
                    iframe_sources.append(urljoin(url, src))

            def _resolve_iframe_src(src: str) -> Optional[Dict[str, str]]:
                try:
                    streaming_links = get_link(src) if src else ""
                    if streaming_links:
                        return {"Provider": get_provider_name(src), "Url": src, "Streaming Links": streaming_links}
                    if "pixfusion.in" in src:
                        return {"Provider": "pixfusion", "Referer": "https://watchanimeworld.in/",
                                 "Url": src, "Streaming Links": ""}
                    iframe_soup = self.fetch_page(src)
                    if not iframe_soup:
                        return None
                    nested_iframe = iframe_soup.select_one("iframe[src]")
                    if nested_iframe:
                        return {"Provider": get_provider_name(nested_iframe["src"]),
                                 "Url": nested_iframe["src"], "Streaming Links": ""}
                except Exception as e:
                    logger.debug(f"anime_hindi_scraper: skipping iframe: {e}")
                return None

            if not iframe_sources:
                return []
            workers = min(DEFAULT_DETAIL_WORKERS, len(iframe_sources))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(workers, 1)) as executor:
                resolved = list(executor.map(_resolve_iframe_src, iframe_sources))
            return [item for item in resolved if item]
        except Exception as e:
            logger.debug(f"anime_hindi_scraper: generic scrape error: {e}")
            return []

    def add_streaming_links(self, details: List[Dict[str, str]]) -> List[Dict[str, str]]:
        detail_items = details or []
        if not detail_items:
            return []

        def _resolve_item(item):
            if not isinstance(item, dict):
                return item
            entry = dict(item)
            target_url = entry.get("Url")
            if entry.get("Streaming Links"):
                return entry
            if target_url:
                with self._cache_lock:
                    cached_stream = self.streaming_cache.get(target_url)
                if cached_stream is not None:
                    entry["Streaming Links"] = cached_stream
                    return entry
            link_data = get_link(target_url) if target_url else ""
            entry["Streaming Links"] = link_data or ""
            if target_url:
                with self._cache_lock:
                    self.streaming_cache[target_url] = entry["Streaming Links"]
            return entry

        workers = min(DEFAULT_DETAIL_WORKERS, len(detail_items))
        if workers <= 1:
            return [_resolve_item(item) for item in detail_items]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(_resolve_item, detail_items))

    def extract_title(self, url: str, suffix: str) -> str:
        soup = self.fetch_page(url)
        title = soup.title.string if soup and soup.title else url.split("/")[-1]
        return title.replace(suffix, "").replace("Watch Online ", "").strip()

    def get_episode_data(self, url: str) -> Optional[Dict]:
        try:
            if "animedekho" in url:
                details = self.extract_animedekho_details(url)
                title = self.extract_title(url, " - AnimeDekho")
            elif "hindisubanime" in url:
                details = self.extract_hindisubanime_details(url)
                title = self.extract_title(url, " - Hindi Sub Anime")
            elif "hindianimeverse" in url:
                details = self.extract_hindianimeverse_details(url)
                title = self.extract_title(url, " - Hindi Anime Verse")
                title = re.sub(r"\s*-\s*(?:or\s+)?Download\s*-\s*Hindi\s+Anime\s+Verse\s*$", "", title, flags=re.IGNORECASE)
                title = re.sub(r"\s*-\s*Hindi\s+Anime\s+Verse\s*$", "", title, flags=re.IGNORECASE)
                title = title.replace("Watch & Download ", "").replace(" Free", "").strip()
            elif "watchanimeworld" in url:
                details = self.scrape_generic(url)
                title = self.extract_title(url, " - Anime World India - Best Source For Hindi, Tamil, Telugu Anime & Cartoons - Anime World India")
            elif "toonshub" in url:
                details = self.extract_toonshub_details(url)
                title = self.extract_title(url, " – ToonsHub")
            elif "toon-stream" in url:
                details = self.scrape_generic(url)
                title = self.extract_title(url, " - Toon Stream")
            else:
                details = self.scrape_generic(url)
                title = self.extract_title(url, " - Toonstream")

            details = self.add_streaming_links(details)
            return {"Title": title, "Details": details} if details else None
        except Exception as e:
            logger.debug(f"anime_hindi_scraper: scrape failed for {url}: {e}")
            return None
