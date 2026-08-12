import logging
import re
import requests
from typing import Dict

logger = logging.getLogger(__name__)

async def bypass(url: str, xsrf_token: str, laravel_session: str) -> Dict:
    """Bypass Sharer.pw links"""
    try:
        logger.info(f"Bypassing Sharer.pw: {url}")
        
        client = requests.Session()
        
        cookies = {
            'XSRF-TOKEN': xsrf_token,
            'laravel_session': laravel_session
        }
        
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'referer': url
        }
        
        client.cookies.update(cookies)
        
        # Get page
        response = client.get(url, headers=headers)
        
        # Extract token
        token_match = re.findall(r'_token:\s*"([^"]+)"', response.text)
        if not token_match:
            return {"success": False, "error": "Token not found"}
        
        token = token_match[0]
        
        # Extract link ID
        link_id_match = re.findall(r'id:\s*"([^"]+)"', response.text)
        if not link_id_match:
            return {"success": False, "error": "Link ID not found"}
        
        link_id = link_id_match[0]
        
        # Make API request
        api_url = 'https://sharer.pw/api/link/generate'
        data = {
            '_token': token,
            'id': link_id
        }
        
        api_response = client.post(api_url, data=data, headers=headers)
        result = api_response.json()
        
        if result.get('status') == 'success':
            download_url = result.get('url')
            return {
                "success": True,
                "bypassed_url": download_url,
                "type": "sharerw"
            }
        
        return {"success": False, "error": "Failed to generate download link"}
        
    except Exception as e:
        logger.error(f"Sharer.pw bypass error: {str(e)}")
        return {"success": False, "error": str(e)}
