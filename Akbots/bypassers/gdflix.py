from curl_cffi import requests
import re
from bs4 import BeautifulSoup
import urllib.parse
import asyncio

HEADERS = {"User-Agent": "Mozilla/5.0"}

def clean_google_link(link):
    if not link: return None
    return re.sub(r"https://fastcdn-dl\.pages\.dev/\?url=", "", link)

def format_href(link):
    if not link: return None
    return f'<a href="{link}">𝗟𝗜𝗡𝗞</a>'

def get_instantdl(gd_url):
    try:
        r = requests.get(gd_url, headers=HEADERS, impersonate="chrome120", timeout=15)
    except: return None
    match = re.search(r"https://instant\.busycdn\.xyz/[A-Za-z0-9:]+", r.text)
    return match.group(0) if match else None

def get_google_from_instant(instant_url):
    if not instant_url: return None
    try:
        r = requests.get(instant_url, headers=HEADERS, allow_redirects=True, impersonate="chrome120", timeout=20)
    except: return None
    final = r.url
    if "video-downloads.googleusercontent.com" in final: return clean_google_link(final)
    if "fastcdn-dl.pages.dev" in final and "url=" in final:
        pure = final.split("url=")[-1]
        if "video-downloads.googleusercontent.com" in pure: return clean_google_link(pure)
    return None

def fetch_html(url):
    try:
        r = requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=15)
        return r.text, str(r.url)
    except: return "", url

def scan(text, pattern):
    m = re.search(pattern, text)
    return m.group(0) if m else None

def try_zfile_fallback(final_url):
    file_id = final_url.split("/file/")[-1]
    folders = ["2870627993","8213224819","7017347792","5011320428","5069651375","3279909168","9065812244","1234567890","1111111111","8841111600"]
    for folder in folders:
        url = f"https://new7.gdflix.net/zfile/{folder}/{file_id}"
        html, _ = fetch_html(url)
        found = scan(html, r"https://[A-Za-z0-9\.\-]+\.workers\.dev/[^\"]+")
        if found: return found
    return None

# A GDFlix "episode" filename always ends in .mkv/.mp4 and is a long,
# dot/dash-separated release-name string — used both to detect whether a
# page is a single file or a multi-episode season pack, and (for packs)
# as the boundary between one episode's data and the next.
_FILENAME_RE = re.compile(r"[\w][\w\.\-\+]{12,}\.(?:mkv|mp4)", re.IGNORECASE)


def _extract_one(text: str, base_final_url: str) -> dict:
    """Runs the full single-file extraction against a chunk of page text —
    shared by both a plain single-file page and each individual episode's
    slice of a season-pack page, so both paths get the exact same
    gofile/telegram/pixeldrain/zfile logic instead of two copies drifting
    apart over time."""
    instantdl = get_instantdl(base_final_url) if base_final_url else None
    google_video = get_google_from_instant(instantdl)

    pix = scan(text, r"https://pixeldrain\.dev/[^\"]+")
    if pix: pix = pix.replace("?embed", "")

    tg_link = scan(text, r"https://filesgram\.[a-z]+/\?start=[^\"'>]+")
    if not tg_link:
        tg_link = scan(text, r"https://(?:t\.me|telegram\.me)/[A-Za-z0-9_]+bot\?start=[A-Za-z0-9_=a-zA-Z\-]+")

    data = {
        "size": scan(text, r"[\d\.]+\s*(GB|MB)") or "Unknown",
        "instantdl": format_href(google_video),
    }

    cloud_raw = scan(text, r"https://fastcdn-dl\.pages\.dev/\?url=[^\"']+")
    if cloud_raw:
        cleaned_cloud = urllib.parse.unquote(re.sub(r"https://fastcdn-dl\.pages\.dev/\?url=", "", cloud_raw))
        data["cloud_resume"] = format_href(cleaned_cloud)
    else:
        data["cloud_resume"] = None

    data.update({
        "pixeldrain": format_href(pix),
        "telegram": format_href(tg_link),
        "zfile": [],
        "gofile": format_href(None),
    })

    direct = scan(text, r"https://[^\"']+/zfile/[0-9]+/[A-Za-z0-9]+")
    if direct:
        zhtml, _ = fetch_html(direct)
        found = scan(zhtml, r"https://[A-Za-z0-9\.\-]+\.workers\.dev/[^\"]+")
        if found: data["zfile"].append(format_href(found))

    validate = scan(text, r"https://validate\.mulitup\.workers\.dev/[A-Za-z0-9]+")
    if validate:
        try:
            vh = requests.get(validate, headers=HEADERS, impersonate="chrome120").text
            gf = scan(vh, r"https://gofile\.io/d/[A-Za-z0-9]+")
            data["gofile"] = format_href(gf)
        except: pass

    return data


def scrape_gdflix(url):
    html, final_url = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    page_title = soup.find("title").text.strip() if soup.find("title") else "Unknown"
    page_size = scan(html, r"[\d\.]+\s*(GB|MB)") or "Unknown"

    filename_matches = list(_FILENAME_RE.finditer(html))

    # Single file (0 or 1 filename match on the page) — original behaviour,
    # unchanged, just routed through the shared _extract_one() helper.
    if len(filename_matches) <= 1:
        data = _extract_one(html, url)
        data["title"] = page_title
        data["final_url"] = final_url
        if not data["zfile"]:
            fb = try_zfile_fallback(final_url)
            if fb: data["zfile"].append(format_href(fb))
        return data

    # Season pack (2+ filenames found) — slice the page into one segment
    # per episode using each filename's position as the boundary, so each
    # episode's gofile/telegram/download links are extracted from ONLY
    # its own segment. This matters: naively grabbing "the first gofile
    # link, the first telegram link, etc." for the whole page (the old
    # behaviour) silently returned just ONE episode's data for the entire
    # pack — this is exactly the bug being fixed here.
    episodes = []
    for i, m in enumerate(filename_matches):
        seg_start = m.start()
        seg_end = filename_matches[i + 1].start() if i + 1 < len(filename_matches) else len(html)
        segment = html[seg_start:seg_end]
        ep_data = _extract_one(segment, None)  # skip instantdl's extra network round-trip per-episode — see note below
        ep_data["title"] = m.group(0)
        episodes.append(ep_data)

    return {
        "is_pack": True,
        "title": page_title,
        "size": page_size,
        "episodes": episodes,
        "final_url": final_url,
    }


async def async_scrape_gdflix(url):
    return await asyncio.to_thread(scrape_gdflix, url)
