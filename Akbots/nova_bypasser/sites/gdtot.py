import logging
import re
import requests
from typing import Dict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

async def bypass(url: str, crypt: str) -> Dict:
    """Bypass GDToT links"""
    try:
        logger.info(f"Bypassing GDToT: {url}")
        
        client = requests.Session()
        client.cookies.update({'crypt': crypt})
        
        res = client.get(url)
        
        # Extract token from page
        token_match = re.findall(r'name="token" value="(.*?)"', res.text)
        if not token_match:
            return {"success": False, "error": "Token not found"}
        
        token = token_match[0]
        
        # Get domain
        domain = urlparse(url).netloc
        
        # Make POST request to get download URL
        post_url = f"https://{domain}/file"
        data = {'token': token}
        
        headers = {
            'authority': domain,
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'origin': f'https://{domain}',
            'referer': url,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = client.post(post_url, data=data, headers=headers, allow_redirects=False)
        
        # Extract download URL from response
        if 'Location' in response.headers:
            download_url = response.headers['Location']
            return {
                "success": True,
                "bypassed_url": download_url,
                "type": "gdtot"
            }
        
        # Try to extract from HTML
        url_match = re.search(r'https://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)', response.text)
        if url_match:
            file_id = url_match.group(1)
            download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
            return {
                "success": True,
                "bypassed_url": download_url,
                "type": "gdtot"
            }
        
        return {"success": False, "error": "Could not extract download link"}
        
    except Exception as e:
        logger.error(f"GDToT bypass error: {str(e)}")
        return {"success": False, "error": str(e)}
