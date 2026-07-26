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

def scrape_gdflix(url):
    html, final_url = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    text = html

    instantdl = get_instantdl(url)
    google_video = get_google_from_instant(instantdl)

    pix = scan(text, r"https://pixeldrain\.dev/[^\"]+")
    if pix: pix = pix.replace("?embed", "")

    tg_link = scan(text, r"https://filesgram\.[a-z]+/\?start=[^\"'>]+")
    if not tg_link:
        tg_link = scan(text, r"https://(?:t\.me|telegram\.me)/[A-Za-z0-9_]+bot\?start=[A-Za-z0-9_=a-zA-Z\-]+")
    
    data = {
        "title": soup.find("title").text.strip() if soup.find("title") else "Unknown",
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
        "final_url": final_url
    })
    
    direct = scan(text, r"https://[^\"']+/zfile/[0-9]+/[A-Za-z0-9]+")
    if direct:
        zhtml, _ = fetch_html(direct)
        found = scan(zhtml, r"https://[A-Za-z0-9\.\-]+\.workers\.dev/[^\"]+")
        if found: data["zfile"].append(format_href(found))

    if not data["zfile"]:
        fb = try_zfile_fallback(final_url)
        if fb: data["zfile"].append(format_href(fb))
    
    validate = scan(text, r"https://validate\.mulitup\.workers\.dev/[A-Za-z0-9]+")
    if validate:
        try:
            vh = requests.get(validate, headers=HEADERS, impersonate="chrome120").text
            gf = scan(vh, r"https://gofile\.io/d/[A-Za-z0-9]+")
            data["gofile"] = format_href(gf)
        except: pass
    return data

async def async_scrape_gdflix(url):
    return await asyncio.to_thread(scrape_gdflix, url)
