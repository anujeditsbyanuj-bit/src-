"""
HDhub4u Content Scraper
Adapted from the Kotlin provider code to Python
"""

import asyncio
import aiohttp
import re
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from datetime import datetime

logger = logging.getLogger(__name__)


class HDhub4uScraper:
    def __init__(self):
        self.main_url = "https://hdhub4u.rehab"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Cookie': 'xla=s4t'
        }
        self.session = None
    
    async def _get_session(self):
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session
    
    async def close(self):
        """Close the session"""
        if self.session:
            await self.session.close()
    
    async def get_latest_content(self, cache_manager) -> List[Dict]:
        """
        Get latest content from HDhub4u
        Uses cache to avoid excessive scraping
        """
        # Check cache first
        cached = cache_manager.get('latest_content')
        if cached:
            logger.info("Returning cached content")
            return cached
        
        try:
            session = await self._get_session()
            
            # Fetch main page
            async with session.get(
                f"{self.main_url}/page/1/",
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch: {response.status}")
                    return []
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Parse content items
                content_items = []
                items = soup.select('.recent-movies > li.thumb')
                
                for item in items[:10]:  # Limit to 10 items
                    parsed_item = self._parse_item(item)
                    if parsed_item:
                        content_items.append(parsed_item)
                
                # Cache the results for 5 minutes
                cache_manager.set('latest_content', content_items, ttl=300)
                
                logger.info(f"Scraped {len(content_items)} items")
                return content_items
                
        except Exception as e:
            logger.error(f"Error scraping content: {e}")
            return []
    
    def _parse_item(self, item) -> Optional[Dict]:
        """Parse a single content item from HTML"""
        try:
            # Extract title
            title_elem = item.select_one('figcaption:nth-child(2) > a:nth-child(1) > p:nth-child(1)')
            if not title_elem:
                return None
            
            title_text = title_elem.get_text(strip=True)
            title = self._clean_title(title_text)
            
            # Extract URL
            url_elem = item.select_one('figure:nth-child(1) > a:nth-child(2)')
            if not url_elem:
                return None
            url = url_elem.get('href', '')
            
            # Extract poster
            poster_elem = item.select_one('figure:nth-child(1) > img:nth-child(1)')
            poster_url = poster_elem.get('src', '') if poster_elem else ''
            
            # Extract quality
            quality = self._get_quality(title_text)
            
            return {
                'title': title,
                'url': url,
                'poster_url': poster_url,
                'quality': quality,
                'scraped_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error parsing item: {e}")
            return None
    
    def _clean_title(self, title: str) -> str:
        """Clean title from quality tags"""
        # Remove quality indicators
        cleaned = re.sub(r'\b(480p|720p|1080p|2160p|4K|HEVC|x264|x265|HDRip|WEB-DL|BluRay)\b', '', title, flags=re.IGNORECASE)
        # Remove extra spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned
    
    def _get_quality(self, text: str) -> str:
        """Extract quality from text"""
        patterns = [
            (r'\b(4k|uhd|2160p)\b', '4K UHD'),
            (r'\b(1080p)\b', '1080p FHD'),
            (r'\b(720p)\b', '720p HD'),
            (r'\b(480p)\b', '480p'),
            (r'\b(bluray)\b', 'BluRay'),
            (r'\b(web-?dl|webrip)\b', 'WEB-DL'),
        ]
        
        for pattern, quality in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return quality
        
        return 'HD'
    
    async def get_download_links(self, url: str, cache_manager) -> List[Dict]:
        """
        Get download links for a specific content item
        Enhanced to extract multiple quality options
        """
        cache_key = f'links_{url}'
        cached = cache_manager.get(cache_key)
        if cached:
            return cached
        
        try:
            session = await self._get_session()
            
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    return []
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                links = []
                seen_urls = set()  # Prevent duplicates
                
                # Find download links from multiple sections
                # Check h3, h4 headers and links in page body
                link_elements = soup.select('h3 a, h4 a, h5 a, .page-body > div a, .entry-content a')
                
                for elem in link_elements:
                    link_url = elem.get('href', '')
                    link_text = elem.get_text(strip=True)
                    
                    # Skip if already processed
                    if link_url in seen_urls:
                        continue
                    
                    # Filter for valid download links (from HDhub4u ecosystem)
                    valid_domains = [
                        'hdstream4u', 'hubstream', 'hubdrive', 'hubcloud', 
                        'hubcdn', 'pixeldrain', 'hblinks', 'buzzserver',
                        'mega.nz', 'mediafire', 'drive.google'
                    ]
                    
                    if any(domain in link_url.lower() for domain in valid_domains):
                        quality = self._extract_quality_from_text(link_text)
                        
                        # Add to links list
                        links.append({
                            'url': link_url,
                            'quality': quality,
                            'text': link_text,
                            'server': self._extract_server_name(link_url)
                        })
                        
                        seen_urls.add(link_url)
                
                # Sort links by quality (4K > 1080p > 720p > 480p)
                quality_order = {'4K': 0, '2160p': 0, '1080p': 1, '720p': 2, '480p': 3, 'Download': 4}
                links.sort(key=lambda x: quality_order.get(x['quality'], 5))
                
                # Cache for 1 hour
                cache_manager.set(cache_key, links, ttl=3600)
                
                return links
                
        except Exception as e:
            logger.error(f"Error getting download links: {e}")
            return []
    
    def _extract_quality_from_text(self, text: str) -> str:
        """
        Extract quality information from link text
        Enhanced with better pattern matching
        """
        text_upper = text.upper()
        
        # Check for specific quality patterns (order matters - check specific first)
        if '2160' in text or '4K' in text_upper or 'UHD' in text_upper:
            return '4K'
        elif '1440' in text or 'QHD' in text_upper:
            return '1440p'
        elif '1080' in text or 'FHD' in text_upper:
            return '1080p'
        elif '720' in text:
            return '720p'
        elif '480' in text or 'SD' in text_upper:
            return '480p'
        elif '360' in text:
            return '360p'
        elif 'HD' in text_upper and '1080' not in text and '720' not in text:
            return '720p'  # Generic HD defaults to 720p
        else:
            return 'Download'
    
    def _extract_server_name(self, url: str) -> str:
        """Extract server name from URL"""
        url_lower = url.lower()
        
        if 'hubdrive' in url_lower:
            return 'HubDrive'
        elif 'hubcloud' in url_lower:
            return 'HubCloud'
        elif 'hubstream' in url_lower:
            return 'HubStream'
        elif 'hdstream4u' in url_lower:
            return 'HDStream4u'
        elif 'pixeldrain' in url_lower:
            return 'PixelDrain'
        elif 'hubcdn' in url_lower:
            return 'HubCDN'
        elif 'mega.nz' in url_lower:
            return 'Mega'
        elif 'mediafire' in url_lower:
            return 'MediaFire'
        elif 'drive.google' in url_lower:
            return 'Google Drive'
        else:
            return 'Download'
    
    async def check_for_updates(self, existing_urls: List[str], cache_manager) -> List[Dict]:
        """
        Check if any existing content has updated download links
        """
        updated_items = []
        
        for url in existing_urls:
            try:
                # Get fresh links
                new_links = await self.get_download_links(url, cache_manager)
                
                # Compare with cached version (implement comparison logic)
                cache_key = f'links_prev_{url}'
                old_links = cache_manager.get(cache_key)
                
                if old_links and new_links != old_links:
                    updated_items.append({
                        'url': url,
                        'new_links': new_links
                    })
                
                # Update cache
                cache_manager.set(cache_key, new_links, ttl=86400)  # 24 hours
                
                # Rate limiting
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error checking updates for {url}: {e}")
                continue
        
        return updated_items
