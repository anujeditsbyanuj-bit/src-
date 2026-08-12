import logging
import re
import requests
import time
import base64
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, parse_qs, unquote
import js2py

logger = logging.getLogger(__name__)

async def extract_direct_link(url: str) -> Dict:
    """Try to extract direct download link from page using multiple methods"""
    try:
        session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': url
        }
        
        response = session.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Method 1: HTML Form Bypass
        form_result = await extract_from_html_form(soup, url, session, headers)
        if form_result["success"]:
            return form_result
        
        # Method 2: CSS Hidden Elements
        css_result = await extract_from_css_hidden(soup, url, response.text)
        if css_result["success"]:
            return css_result
        
        # Method 3: JavaScript Execution
        js_result = await extract_from_javascript(soup, url, response.text)
        if js_result["success"]:
            return js_result
        
        # Method 4: Meta Refresh
        meta_result = await extract_from_meta_refresh(soup, url)
        if meta_result["success"]:
            return meta_result
        
        # Method 5: iframe/embed extraction
        iframe_result = await extract_from_iframe(soup, url)
        if iframe_result["success"]:
            return iframe_result
        
        # Method 6: Base64 encoded links
        base64_result = await extract_from_base64(response.text, url)
        if base64_result["success"]:
            return base64_result
        
        # Method 7: URL parameter extraction
        param_result = await extract_from_url_params(url, response.url)
        if param_result["success"]:
            return param_result
        
        # Method 8: Common download button selectors
        button_result = await extract_from_buttons(soup, url)
        if button_result["success"]:
            return button_result
        
        # Method 9: Data attributes
        data_result = await extract_from_data_attributes(soup, url)
        if data_result["success"]:
            return data_result
        
        # Method 10: File pattern matching
        file_result = await extract_file_patterns(response.text)
        if file_result["success"]:
            return file_result
        
        return {"success": False, "error": "No direct link found using any method"}
        
    except Exception as e:
        logger.error(f"Direct extraction error: {str(e)}")
        return {"success": False, "error": str(e)}

async def extract_from_html_form(soup: BeautifulSoup, url: str, session, headers: dict) -> Dict:
    """Extract link by submitting HTML forms"""
    try:
        # Find all forms on the page
        forms = soup.find_all('form')
        
        for form in forms:
            # Check if form likely leads to download
            form_action = form.get('action', '')
            form_method = form.get('method', 'get').lower()
            
            # Build form data
            form_data = {}
            for input_tag in form.find_all('input'):
                input_name = input_tag.get('name')
                input_value = input_tag.get('value', '')
                input_type = input_tag.get('type', 'text')
                
                if input_name:
                    # Handle different input types
                    if input_type == 'checkbox' and input_tag.has_attr('checked'):
                        form_data[input_name] = input_value or 'on'
                    elif input_type == 'radio' and input_tag.has_attr('checked'):
                        form_data[input_name] = input_value
                    elif input_type not in ['submit', 'button', 'reset', 'image']:
                        form_data[input_name] = input_value
            
            # Submit form
            form_url = urljoin(url, form_action) if form_action else url
            
            try:
                if form_method == 'post':
                    form_response = session.post(form_url, data=form_data, headers=headers, timeout=15, allow_redirects=True)
                else:
                    form_response = session.get(form_url, params=form_data, headers=headers, timeout=15, allow_redirects=True)
                
                # Check if we got redirected to a download link
                if form_response.url != url and is_direct_link(form_response.url):
                    return {
                        "success": True,
                        "bypassed_url": form_response.url,
                        "type": "html_form"
                    }
                
                # Check response content for links
                form_soup = BeautifulSoup(form_response.text, 'lxml')
                download_link = form_soup.find('a', {'class': re.compile(r'download', re.I)})
                if download_link and download_link.get('href'):
                    link = urljoin(url, download_link['href'])
                    return {
                        "success": True,
                        "bypassed_url": link,
                        "type": "html_form"
                    }
            except:
                continue
        
        return {"success": False, "error": "No valid form found"}
        
    except Exception as e:
        logger.error(f"HTML form extraction error: {str(e)}")
        return {"success": False, "error": str(e)}

async def extract_from_css_hidden(soup: BeautifulSoup, url: str, html: str) -> Dict:
    """Extract links hidden by CSS (display:none, visibility:hidden, etc)"""
    try:
        # Find elements with CSS hiding
        hidden_patterns = [
            {'style': re.compile(r'display:\s*none', re.I)},
            {'style': re.compile(r'visibility:\s*hidden', re.I)},
            {'class': re.compile(r'hidden', re.I)},
            {'class': 'd-none'},
        ]
        
        for pattern in hidden_patterns:
            hidden_elements = soup.find_all(attrs=pattern)
            
            for element in hidden_elements:
                # Look for links in hidden elements
                links = element.find_all('a', href=True)
                for link in links:
                    href = link.get('href')
                    if href and is_direct_link(href):
                        return {
                            "success": True,
                            "bypassed_url": urljoin(url, href),
                            "type": "css_hidden"
                        }
                
                # Check data attributes
                for attr in element.attrs:
                    if 'url' in attr.lower() or 'link' in attr.lower():
                        value = element[attr]
                        if is_direct_link(value):
                            return {
                                "success": True,
                                "bypassed_url": urljoin(url, value),
                                "type": "css_hidden"
                            }
        
        # Check for CSS-hidden text containing URLs
        url_pattern = r'https?://[^\s<>"\']+\.(mp4|mkv|avi|zip|rar|pdf|doc|docx|exe|apk)'
        hidden_urls = re.findall(url_pattern, html, re.I)
        if hidden_urls:
            return {
                "success": True,
                "bypassed_url": hidden_urls[0],
                "type": "css_hidden"
            }
        
        return {"success": False, "error": "No CSS hidden links found"}
        
    except Exception as e:
        logger.error(f"CSS hidden extraction error: {str(e)}")
        return {"success": False, "error": str(e)}

async def extract_from_javascript(soup: BeautifulSoup, url: str, html: str) -> Dict:
    """Execute JavaScript to extract links"""
    try:
        # Find all script tags
        scripts = soup.find_all('script')
        
        for script in scripts:
            if not script.string:
                continue
            
            script_content = script.string
            
            # Method 1: Direct JavaScript URL assignment
            js_patterns = [
                r'window\.location\.href\s*=\s*["\']([^"\']+)["\']',
                r'window\.location\s*=\s*["\']([^"\']+)["\']',
                r'document\.location\.href\s*=\s*["\']([^"\']+)["\']',
                r'location\.replace\(["\']([^"\']+)["\']\)',
                r'location\.href\s*=\s*["\']([^"\']+)["\']',
            ]
            
            for pattern in js_patterns:
                matches = re.findall(pattern, script_content, re.I)
                for match in matches:
                    if is_direct_link(match):
                        return {
                            "success": True,
                            "bypassed_url": urljoin(url, match),
                            "type": "javascript"
                        }
            
            # Method 2: Variable assignments containing URLs
            var_patterns = [
                r'var\s+\w+\s*=\s*["\']([^"\']+)["\']',
                r'let\s+\w+\s*=\s*["\']([^"\']+)["\']',
                r'const\s+\w+\s*=\s*["\']([^"\']+)["\']',
            ]
            
            for pattern in var_patterns:
                matches = re.findall(pattern, script_content)
                for match in matches:
                    if is_direct_link(match):
                        return {
                            "success": True,
                            "bypassed_url": urljoin(url, match),
                            "type": "javascript"
                        }
            
            # Method 3: Try to execute simple JavaScript
            try:
                # Extract simple variable assignments
                if 'downloadLink' in script_content or 'download_url' in script_content:
                    # Try js2py execution
                    context = js2py.EvalJs()
                    context.execute(script_content)
                    
                    # Check common variable names
                    var_names = ['downloadLink', 'download_url', 'fileUrl', 'directLink', 'url']
                    for var_name in var_names:
                        try:
                            result = context[var_name]
                            if result and is_direct_link(str(result)):
                                return {
                                    "success": True,
                                    "bypassed_url": urljoin(url, str(result)),
                                    "type": "javascript_execution"
                                }
                        except:
                            continue
            except:
                pass
            
            # Method 4: Atob (base64) decoding in JavaScript
            atob_pattern = r'atob\(["\']([^"\']+)["\']\)'
            atob_matches = re.findall(atob_pattern, script_content)
            for encoded in atob_matches:
                try:
                    decoded = base64.b64decode(encoded).decode('utf-8')
                    if is_direct_link(decoded):
                        return {
                            "success": True,
                            "bypassed_url": urljoin(url, decoded),
                            "type": "javascript_base64"
                        }
                except:
                    continue
        
        return {"success": False, "error": "No JavaScript links found"}
        
    except Exception as e:
        logger.error(f"JavaScript extraction error: {str(e)}")
        return {"success": False, "error": str(e)}

async def extract_from_meta_refresh(soup: BeautifulSoup, url: str) -> Dict:
    """Extract from meta refresh redirects"""
    try:
        meta_refresh = soup.find('meta', attrs={'http-equiv': re.compile(r'refresh', re.I)})
        if meta_refresh and meta_refresh.get('content'):
            content = meta_refresh.get('content')
            # Extract URL from content (format: "5;url=http://example.com")
            url_match = re.search(r'url\s*=\s*["\']?([^"\'\s]+)', content, re.I)
            if url_match:
                redirect_url = url_match.group(1)
                return {
                    "success": True,
                    "bypassed_url": urljoin(url, redirect_url),
                    "type": "meta_refresh"
                }
        
        return {"success": False, "error": "No meta refresh found"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

async def extract_from_iframe(soup: BeautifulSoup, url: str) -> Dict:
    """Extract from iframes and embeds"""
    try:
        # Check iframes
        iframes = soup.find_all('iframe', src=True)
        for iframe in iframes:
            src = iframe.get('src')
            if is_direct_link(src):
                return {
                    "success": True,
                    "bypassed_url": urljoin(url, src),
                    "type": "iframe"
                }
        
        # Check embed tags
        embeds = soup.find_all('embed', src=True)
        for embed in embeds:
            src = embed.get('src')
            if is_direct_link(src):
                return {
                    "success": True,
                    "bypassed_url": urljoin(url, src),
                    "type": "embed"
                }
        
        # Check object tags
        objects = soup.find_all('object', data=True)
        for obj in objects:
            data = obj.get('data')
            if is_direct_link(data):
                return {
                    "success": True,
                    "bypassed_url": urljoin(url, data),
                    "type": "object"
                }
        
        return {"success": False, "error": "No iframe/embed links found"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

async def extract_from_base64(html: str, url: str) -> Dict:
    """Extract base64 encoded links"""
    try:
        # Pattern for base64 encoded strings
        base64_pattern = r'[A-Za-z0-9+/]{20,}={0,2}'
        matches = re.findall(base64_pattern, html)
        
        for match in matches:
            try:
                decoded = base64.b64decode(match).decode('utf-8', errors='ignore')
                if is_direct_link(decoded):
                    return {
                        "success": True,
                        "bypassed_url": decoded,
                        "type": "base64"
                    }
            except:
                continue
        
        return {"success": False, "error": "No base64 links found"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

async def extract_from_url_params(original_url: str, final_url: str) -> Dict:
    """Extract from URL parameters"""
    try:
        # Check if redirect happened
        if final_url != original_url and is_direct_link(final_url):
            return {
                "success": True,
                "bypassed_url": final_url,
                "type": "url_redirect"
            }
        
        # Parse URL parameters
        parsed = urlparse(final_url)
        params = parse_qs(parsed.query)
        
        # Check common parameter names
        param_names = ['url', 'link', 'download', 'file', 'redirect', 'target', 'go', 'out']
        
        for param_name in param_names:
            if param_name in params:
                value = params[param_name][0]
                decoded_value = unquote(value)
                if is_direct_link(decoded_value):
                    return {
                        "success": True,
                        "bypassed_url": decoded_value,
                        "type": "url_param"
                    }
        
        return {"success": False, "error": "No URL param links found"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

async def extract_from_buttons(soup: BeautifulSoup, url: str) -> Dict:
    """Extract from download buttons with various selectors"""
    try:
        # Common download button selectors
        selectors = [
            ('a', {'class': re.compile(r'download', re.I)}),
            ('a', {'id': re.compile(r'download', re.I)}),
            ('button', {'class': re.compile(r'download', re.I)}),
            ('a', {'class': 'btn-primary'}),
            ('a', {'class': 'btn-success'}),
            ('a', {'role': 'button'}),
            ('a', {'title': re.compile(r'download', re.I)}),
        ]
        
        for tag, attrs in selectors:
            elements = soup.find_all(tag, attrs)
            for element in elements:
                href = element.get('href')
                if href and is_direct_link(href):
                    return {
                        "success": True,
                        "bypassed_url": urljoin(url, href),
                        "type": "button_extraction"
                    }
                
                # Check onclick attribute
                onclick = element.get('onclick', '')
                if onclick:
                    url_match = re.search(r'["\']([^"\']+)["\']', onclick)
                    if url_match:
                        potential_url = url_match.group(1)
                        if is_direct_link(potential_url):
                            return {
                                "success": True,
                                "bypassed_url": urljoin(url, potential_url),
                                "type": "button_onclick"
                            }
        
        return {"success": False, "error": "No button links found"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

async def extract_from_data_attributes(soup: BeautifulSoup, url: str) -> Dict:
    """Extract from HTML5 data attributes"""
    try:
        # Find all elements with data attributes
        all_elements = soup.find_all(True)
        
        for element in all_elements:
            for attr, value in element.attrs.items():
                # Check data-* attributes
                if attr.startswith('data-') and isinstance(value, str):
                    if is_direct_link(value):
                        return {
                            "success": True,
                            "bypassed_url": urljoin(url, value),
                            "type": "data_attribute"
                        }
        
        return {"success": False, "error": "No data attribute links found"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

async def extract_file_patterns(html: str) -> Dict:
    """Extract direct file links using pattern matching"""
    try:
        # Comprehensive file extension patterns
        file_patterns = [
            r'https?://[^\s<>"\']+\.(?:mp4|mkv|avi|mov|flv|wmv|webm|m4v)',  # Video
            r'https?://[^\s<>"\']+\.(?:mp3|wav|flac|aac|ogg|m4a|wma)',  # Audio
            r'https?://[^\s<>"\']+\.(?:zip|rar|7z|tar|gz|bz2|xz)',  # Archives
            r'https?://[^\s<>"\']+\.(?:pdf|doc|docx|xls|xlsx|ppt|pptx)',  # Documents
            r'https?://[^\s<>"\']+\.(?:exe|msi|apk|dmg|deb|rpm)',  # Executables
            r'https?://[^\s<>"\']+\.(?:jpg|jpeg|png|gif|bmp|webp|svg)',  # Images
            r'https?://[^\s<>"\']+\.(?:iso|img|bin)',  # Disk images
        ]
        
        for pattern in file_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                # Return the first valid match
                for match in matches:
                    if not any(skip in match.lower() for skip in ['icon', 'logo', 'thumb', 'preview']):
                        return {
                            "success": True,
                            "bypassed_url": match,
                            "type": "file_pattern"
                        }
        
        return {"success": False, "error": "No file patterns found"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

def is_direct_link(url: str) -> bool:
    """Check if URL is a direct download link"""
    if not url or not isinstance(url, str):
        return False
    
    # Must be valid HTTP/HTTPS URL
    if not url.startswith(('http://', 'https://')):
        return False
    
    # Skip common non-download patterns
    skip_patterns = [
        'javascript:', 'mailto:', '#', 'void(0)',
        'facebook.com', 'twitter.com', 'instagram.com',
        'login', 'signin', 'register', 'signup',
        'icon', 'logo', 'banner', 'ad'
    ]
    
    url_lower = url.lower()
    if any(pattern in url_lower for pattern in skip_patterns):
        return False
    
    # Check for file extensions or download indicators
    download_indicators = [
        '.mp4', '.mkv', '.avi', '.zip', '.rar', '.pdf',
        '.doc', '.docx', '.exe', '.apk', '.mp3', '.iso',
        '/download/', '/get/', '/file/', '/direct/',
        'download=', 'file=', 'url='
    ]
    
    return any(indicator in url_lower for indicator in download_indicators)

async def bypass_shortener(url: str, site_type: str) -> Dict:
    """Generic URL shortener bypass"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Try to follow redirects
        response = requests.get(url, headers=headers, allow_redirects=True, timeout=15)
        
        final_url = response.url
        
        # Check if we got redirected to a different domain
        if urlparse(final_url).netloc != urlparse(url).netloc:
            return {
                "success": True,
                "bypassed_url": final_url,
                "type": f"shortener_{site_type}"
            }
        
        # Try to extract from page
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Look for skip/continue buttons
        skip_button = soup.find('a', {'id': ['skip_button', 'proceed', 'continue']})
        if skip_button and skip_button.get('href'):
            final_url = urljoin(url, skip_button['href'])
            return {
                "success": True,
                "bypassed_url": final_url,
                "type": f"shortener_{site_type}"
            }
        
        return {"success": False, "error": "Could not bypass shortener"}
        
    except Exception as e:
        logger.error(f"Shortener bypass error: {str(e)}")
        return {"success": False, "error": str(e)}

async def bypass_uptobox(url: str, token: str = None) -> Dict:
    """Bypass Uptobox links"""
    try:
        if not token:
            return {"success": False, "error": "Uptobox token required"}
        
        # Extract file ID from URL
        file_id_match = re.search(r'uptobox\.com/([a-zA-Z0-9]+)', url)
        if not file_id_match:
            return {"success": False, "error": "Invalid Uptobox URL"}
        
        file_id = file_id_match.group(1)
        
        # Use Uptobox API
        api_url = f"https://uptobox.com/api/link?token={token}&file_code={file_id}"
        
        response = requests.get(api_url, timeout=15)
        data = response.json()
        
        if data.get('statusCode') == 0:
            download_url = data['data']['dlLink']
            return {
                "success": True,
                "bypassed_url": download_url,
                "type": "uptobox"
            }
        
        return {"success": False, "error": "Uptobox API error"}
        
    except Exception as e:
        logger.error(f"Uptobox bypass error: {str(e)}")
        return {"success": False, "error": str(e)}

async def bypass_terabox(url: str, cookie: str = None) -> Dict:
    """Bypass Terabox links"""
    try:
        if not cookie:
            return {"success": False, "error": "Terabox cookie required"}
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Cookie': f'ndus={cookie}'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        
        # Extract download link from response
        dlink_match = re.search(r'"dlink":"([^"]+)"', response.text)
        if dlink_match:
            download_url = dlink_match.group(1).replace('\\/', '/')
            return {
                "success": True,
                "bypassed_url": download_url,
                "type": "terabox"
            }
        
        return {"success": False, "error": "Could not extract Terabox link"}
        
    except Exception as e:
        logger.error(f"Terabox bypass error: {str(e)}")
        return {"success": False, "error": str(e)}

async def generic_bypass(url: str) -> Dict:
    """Generic bypass method for unknown sites"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Follow redirects and get final URL
        response = requests.get(url, headers=headers, allow_redirects=True, timeout=15)
        
        # Try multiple extraction methods
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Method 1: Look for download links
        download_links = soup.find_all('a', href=True)
        for link in download_links:
            href = link.get('href', '')
            text = link.get_text(strip=True).lower()
            
            if any(word in text for word in ['download', 'get', 'fetch', 'retrieve']):
                if href.startswith('http'):
                    return {
                        "success": True,
                        "bypassed_url": href,
                        "type": "generic"
                    }
        
        # Method 2: Check if final URL is different
        if response.url != url:
            return {
                "success": True,
                "bypassed_url": response.url,
                "type": "generic_redirect"
            }
        
        # Method 3: Look for file links in page source
        file_urls = re.findall(
            r'https?://[^\s<>"]+?\.(?:mp4|mkv|avi|mp3|zip|rar|pdf|doc|docx|exe|apk|jpg|png)',
            response.text,
            re.IGNORECASE
        )
        
        if file_urls:
            return {
                "success": True,
                "bypassed_url": file_urls[0],
                "type": "generic_file"
            }
        
        return {"success": False, "error": "No bypass method worked"}
        
    except Exception as e:
        logger.error(f"Generic bypass error: {str(e)}")
        return {"success": False, "error": str(e)}

# Alternative methods when credentials are not available

async def bypass_gdtot_alternative(url: str) -> Dict:
    """Alternative GDToT bypass without crypt"""
    try:
        # Use alternative API or method
        return await generic_bypass(url)
    except Exception as e:
        return {"success": False, "error": str(e)}

async def bypass_sharerw_alternative(url: str) -> Dict:
    """Alternative Sharer.pw bypass without credentials"""
    try:
        return await generic_bypass(url)
    except Exception as e:
        return {"success": False, "error": str(e)}
