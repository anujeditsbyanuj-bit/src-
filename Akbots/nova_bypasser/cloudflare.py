import logging
import asyncio
import cloudscraper
from typing import Dict
from config import Config
from .proxy_manager import proxy_manager

logger = logging.getLogger(__name__)

class CloudflareBypasser:
    """Bypass Cloudflare protection"""
    
    def __init__(self):
        self.scraper = proxy_manager.get_cloudscraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )
    
    async def bypass(self, url: str) -> Dict:
        """Bypass Cloudflare protected URL"""
        try:
            logger.info(f"Attempting Cloudflare bypass for: {url}")
            
            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self._make_request,
                url
            )
            
            if response and response.status_code == 200:
                # Try to extract direct link from response
                direct_link = self._extract_link(response.text, url)
                
                if direct_link:
                    return {
                        "success": True,
                        "bypassed_url": direct_link,
                        "type": "cloudflare"
                    }
                else:
                    # If no direct link found, return the accessed URL
                    return {
                        "success": True,
                        "bypassed_url": response.url,
                        "type": "cloudflare"
                    }
            else:
                return {
                    "success": False,
                    "error": "Failed to bypass Cloudflare protection"
                }
                
        except Exception as e:
            logger.error(f"Cloudflare bypass error: {str(e)}")
            return {
                "success": False,
                "error": f"Cloudflare bypass failed: {str(e)}"
            }
    
    def _make_request(self, url: str):
        """Make request with Cloudflare bypass"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            
            # Add Cloudflare cookie if available
            cookies = {}
            if Config.CLOUDFLARE_COOKIE:
                cookies['cf_clearance'] = Config.CLOUDFLARE_COOKIE
            
            response = self.scraper.get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=30,
                allow_redirects=True
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Request error: {str(e)}")
            return None
    
    def _extract_link(self, html: str, original_url: str) -> str:
        """Extract direct link from HTML"""
        import re
        from bs4 import BeautifulSoup
        
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # Try to find download button or direct link
            patterns = [
                r'https?://[^\s<>"]+?(?:\.mp4|\.mkv|\.avi|\.zip|\.rar|\.pdf|\.doc|\.docx)',
                r'href=["\']([^"\']+)["\'].*?download',
                r'data-url=["\']([^"\']+)["\']',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, html, re.IGNORECASE)
                if matches:
                    return matches[0] if isinstance(matches[0], str) else matches[0][0]
            
            # Check for meta refresh
            meta_refresh = soup.find('meta', attrs={'http-equiv': 'refresh'})
            if meta_refresh and meta_refresh.get('content'):
                content = meta_refresh.get('content')
                url_match = re.search(r'url=(.*)', content, re.IGNORECASE)
                if url_match:
                    return url_match.group(1)
            
            # Check for JavaScript redirects
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    redirect_match = re.search(r'window\.location\.href\s*=\s*["\']([^"\']+)["\']', script.string)
                    if redirect_match:
                        return redirect_match.group(1)
            
            return None
            
        except Exception as e:
            logger.error(f"Link extraction error: {str(e)}")
            return None
    
    async def bypass_with_selenium(self, url: str) -> Dict:
        """Bypass using Selenium (for tougher protection)"""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            driver = webdriver.Chrome(options=options)
            driver.get(url)
            
            # Wait for page load
            await asyncio.sleep(5)
            
            # Try to find download link
            try:
                download_btn = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//a[contains(@class, 'download')]"))
                )
                direct_link = download_btn.get_attribute('href')
            except:
                direct_link = driver.current_url
            
            driver.quit()
            
            return {
                "success": True,
                "bypassed_url": direct_link,
                "type": "cloudflare_selenium"
            }
            
        except Exception as e:
            logger.error(f"Selenium bypass error: {str(e)}")
            return {
                "success": False,
                "error": f"Selenium bypass failed: {str(e)}"
          }
