from curl_cffi import requests
import re
import time
import asyncio
from bs4 import BeautifulSoup
from urllib.parse import urlparse

def format_href(link):
    if not link: return None
    if not str(link).startswith("http"):
        link = f"https://{link}"
    return f'<a href="{link}">𝗟𝗜𝗡𝗞</a>'

def build_valid_url(raw_data, domain):
    """FilePress API returns varying formats. This parses them to valid URLs."""
    if not raw_data: 
        return None
    raw_str = str(raw_data).strip()
    
    if raw_str.startswith("http"):
        return raw_str
    elif raw_str.startswith("/"):
        return f"{domain}{raw_str}"
    elif len(raw_str) > 20 and " " not in raw_str:
        # Handles raw Google Drive IDs
        return f"https://drive.google.com/uc?id={raw_str}"
    return None

def extract_telegram_link(tgfiles_url, session):
    """Extracts final bot URL from tgfiles.baby redirects."""
    if not tgfiles_url or "tgfiles.baby" not in tgfiles_url: 
        return tgfiles_url
        
    try:
        r = session.get(tgfiles_url, timeout=15)
        bot_match = re.search(r"const\s+botName\s*=\s*['\"]([^'\"]+)['\"]", r.text)
        start_id_match = re.search(r"\?start=([A-Za-z0-9_]+)", tgfiles_url)
        
        if bot_match and start_id_match:
            bot_name = bot_match.group(1)
            start_id = start_id_match.group(1)
            return f"https://t.me/{bot_name}?start={start_id}"
    except Exception:
        pass
    return None

def scrape_filepress(url):
    session = requests.Session(impersonate="chrome120")
    
    parsed = urlparse(url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    file_id_match = re.search(r"/file/([a-zA-Z0-9]+)", url)
    if not file_id_match:
        return None
    url_id = file_id_match.group(1)
    
    title = "FilePress Link"
    size = "Unknown"
    
    # Smart-Retry Loop for Cloudflare Turnstile Bypass
    for attempt in range(3):
        try:
            page_req = session.get(url, timeout=10)
            
            # Agar Cloudflare ne block nahi kiya
            if "Just a moment" not in page_req.text:
                soup = BeautifulSoup(page_req.text, "html.parser")
                
                # Naye UI mein H1 tag ke andar clean title hota hai
                h1_tag = soup.find("h1")
                if h1_tag:
                    title = h1_tag.text.strip()
                
                # Size exact GB/MB ke format mein extract karna
                size_match = re.search(r"([\d\.]+\s*(GB|MB|KB))", page_req.text)
                if size_match:
                    size = size_match.group(1)
                
                break # Success! Loop se bahar niklo
            
            # Agar IUAM block kar de, toh 1 sec ruk kar dubara try karo
            time.sleep(1)
        except Exception:
            pass

    data = {
        "title": title,
        "size": size,
        "instantdl": None,
        "cloud_resume": None,
        "telegram": None,
        "gofile": None,
        "pixeldrain": None,
        "zfile": []
    }

    api_headers = {
        "Content-Type": "application/json",
        "Origin": domain,
        "Referer": url,
        "Accept": "application/json, text/plain, */*"
    }

    api_endpoints = [
        f"{domain}/api/file/downlaod2/", 
        f"{domain}/api/file/downlaod/"
    ]

    # 1. Fetch Telegram Link
    for api in api_endpoints:
        try:
            res_tg = session.post(api, headers=api_headers, json={"captchaValue": None, "id": url_id, "method": "telegramDownload"}, timeout=15)
            if res_tg.status_code == 200 and res_tg.json().get("data"):
                final_tg_link = extract_telegram_link(res_tg.json()["data"], session)
                data["telegram"] = format_href(final_tg_link)
                break
        except Exception:
            continue

    # 2. Fetch Instant/Direct Download Link
    methods_to_try = ["indexDownlaod", "publicDownlaod", "directDownload"]
    
    for api in api_endpoints:
        if data["instantdl"]: break
        for method in methods_to_try:
            try:
                res_dl = session.post(api, headers=api_headers, json={"captchaValue": None, "id": url_id, "method": method}, timeout=15)
                if res_dl.status_code == 200 and res_dl.json().get("data"):
                    valid_url = build_valid_url(res_dl.json()["data"], domain)
                    if valid_url:
                        data["instantdl"] = format_href(valid_url)
                        break
            except Exception:
                continue
                
    return data

async def async_scrape_filepress(url):
    return await asyncio.to_thread(scrape_filepress, url)
