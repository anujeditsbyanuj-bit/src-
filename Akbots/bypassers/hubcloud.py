from curl_cffi import requests
import re
import asyncio
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, quote

# ==========================================
# 1. CORE UTILITIES
# ==========================================

def clean_link(href, base_url):
    """Format link properly and handle PixelDrain API conversion"""
    if not href: 
        return None
    if href.startswith("/"): 
        return urljoin(base_url, href)
    if "pixeldrain.dev/u/" in href: 
        return href.replace("/u/", "/api/file/")
    return href

def get_proxy_urls(target_url):
    """Returns a list of URLs to try: Direct first, then Proxies"""
    encoded_url = quote(target_url)
    
    return [
        # 1. Direct try (Fastest)
        target_url,
        
        # 2. Your Premium/Favorite: CorsProxy.io
        f"https://corsproxy.io/?{encoded_url}",
        
        # 3. Friend's Repo Proxies (Fallbacks)
        f"https://api.allorigins.win/get?url={encoded_url}",
        f"https://thingproxy.freeboard.io/fetch/{target_url}"
    ]

# ==========================================
# 2. THE MULTI-PROXY FETCHER
# ==========================================

def fetch_html(url, session, referer=None):
    """Attempts to fetch HTML using direct connection, then falls back to multiple proxies."""
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }
    
    if referer: 
        headers["Referer"] = referer

    urls_to_try = get_proxy_urls(url)

    for attempt_url in urls_to_try:
        try:
            res = session.get(attempt_url, headers=headers, timeout=15)
            
            # Check if it's a successful response
            if res.status_code == 200:
                
                # Special handling for allorigins API response
                if "allorigins.win" in attempt_url:
                    data = res.json()
                    if "contents" in data and "Just a moment" not in data["contents"]:
                        return data["contents"]
                
                # Normal HTML response
                else:
                    if "Just a moment" not in res.text:
                        return res.text
                        
        except Exception:
            continue # If proxy timeouts or fails, move to the next one immediately
            
    return ""

# ==========================================
# 3. SECOND PAGE PARSER (LINKS EXTRACTION)
# ==========================================

def scrape_single_page(html, base_url):
    """Extracts direct links from the final HTML source"""
    links = {"gofile": None, "pixeldrain": None, "zfile": [], "cloud_resume": None, "telegram": None}
    
    if not html: 
        return links
        
    soup = BeautifulSoup(html, "html.parser")
    
    for a in soup.find_all("a", href=True):
        href = clean_link(a['href'], base_url)
        text = a.text.lower() if a.text else ""
        
        if not href or href.startswith("javascript"): 
            continue
        
        # Telegram Bot/Payload Link Filter
        if "t.me" in href or "telegram.me" in href:
            if "?start=" in href or "_bot" in href.lower():
                links["telegram"] = f'<a href="{href}">𝗟𝗜𝗡𝗞</a>'
            continue 
        
        # Direct Links Classification
        if "pixeldrain" in href:
            links["pixeldrain"] = f'<a href="{href}">𝗟𝗜𝗡𝗞</a>'
        elif "gofile.io" in href:
            links["gofile"] = f'<a href="{href}">𝗟𝗜𝗡𝗞</a>'
        elif any(domain in href.lower() for domain in ["r2.dev", "cloudflare", "workers.dev", "googleusercontent.com", "drive.google.com"]):
            if not links["cloud_resume"]: 
                links["cloud_resume"] = f'<a href="{href}">𝗟𝗜𝗡𝗞</a>'
        elif re.search(r"\.(zip|rar|7z|mkv|mp4|avi|mov)$", href, re.IGNORECASE):
            tag = f'<a href="{href}">𝗟𝗜𝗡𝗞</a>'
            if tag not in links["zfile"]: 
                links["zfile"].append(tag)
                
    return links

# ==========================================
# 4. MAIN HUBCLOUD BYPASSER
# ==========================================

def scrape_hubcloud(url):
    session = requests.Session(impersonate="chrome120")
    
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    
    # Handle vifix mapping
    if "vifix.site/hubcloud/" in url:
        url_id = url.split("/")[-1]
        url = f"https://hubcloud.one/drive/{url_id}"
        
    title = "HubCloud File"
    size = "Unknown"
    is_pack = "/packs/" in url
    
    # Step 1: Fetch Page 1 (Using Multi-Proxy Engine)
    html = fetch_html(url, session)
    if not html: 
        return None
        
    soup = BeautifulSoup(html, "html.parser")
    
    if title_tag := soup.find("title"):
        title = title_tag.text.replace("HubCloud", "").replace("-", "").strip()
        
    if size_match := re.search(r"([\d\.]+\s*(GB|MB|KB))", html, re.IGNORECASE):
        size = size_match.group(1).upper()

    # Step 2A: Handle Packs
    if is_pack:
        episode_links = []
        for a in soup.find_all("a", href=True):
            href = a['href']
            # Find URLs containing drive/video with long IDs
            if ("/drive/" in href or "/video/" in href) and len(href.split("/")[-1]) > 10 and "packs" not in href: 
                episode_links.append(clean_link(href, base_url))
        
        episode_links = list(dict.fromkeys(episode_links)) # Remove duplicates
        
        if episode_links:
            pack_content = "\n".join([f"• <a href='{link}'>Episode {i+1}</a>" for i, link in enumerate(episode_links)])
        else:
            pack_content = "❌ No episodes found inside pack."
            
        return {"is_pack": True, "title": title, "size": size, "pack_content": pack_content}
        
    # Step 2B: Handle Single Files
    else:
        # Find the main download button
        download_btn = soup.find("a", id="download")
        if not download_btn:
            for a in soup.find_all("a", href=True):
                if "download" in a.text.lower() and len(a['href']) > 10:
                    download_btn = a
                    break
                    
        if not download_btn or not download_btn.get('href'):
            return None
            
        next_page_url = clean_link(download_btn['href'], base_url)
        
        # Step 3: Fetch Page 2 (Using Multi-Proxy Engine & Referer)
        next_html = fetch_html(next_page_url, session, referer=url)
        
        extracted_links = scrape_single_page(next_html, base_url)
        
        if extracted_links:
            extracted_links.update({
                "title": title, 
                "size": size, 
                "is_pack": False, 
                "instantdl": extracted_links.get("cloud_resume")
            })
            return extracted_links
            
    return None

async def async_scrape_hubcloud(url):
    return await asyncio.to_thread(scrape_hubcloud, url)
