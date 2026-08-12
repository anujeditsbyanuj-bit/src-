import logging
import re
import time
import asyncio
from typing import Dict, Optional

# Selenium/Chrome isn't part of this bot's base image (no browser deps
# bundled), so this stage is optional: if it's not installed, the
# universal bypasser above will simply have already returned/failed
# before this ever runs. Install `selenium` + a Chrome/Chromedriver
# binary on the host to enable it.
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    _SELENIUM_AVAILABLE = True
except ImportError:
    _SELENIUM_AVAILABLE = False

logger = logging.getLogger(__name__)

class AdvancedBypasser:
    """Advanced bypasser using browser automation for complex scenarios"""
    
    def __init__(self):
        self.driver = None
    
    def _get_driver(self):
        """Get configured Selenium WebDriver"""
        if self.driver:
            return self.driver
        
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        try:
            self.driver = webdriver.Chrome(options=options)
            # Disable webdriver detection
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            return self.driver
        except Exception as e:
            logger.error(f"Error creating WebDriver: {e}")
            return None
    
    async def bypass_with_browser(self, url: str) -> Dict:
        """Bypass using browser automation - handles JavaScript, timers, captchas"""
        if not _SELENIUM_AVAILABLE:
            return {"success": False, "error": "Browser automation unavailable (selenium not installed on this host)"}
        driver = None
        try:
            driver = self._get_driver()
            if not driver:
                return {"success": False, "error": "Failed to initialize browser"}
            
            logger.info(f"Browser bypass starting for: {url}")
            driver.get(url)
            
            # Wait for page load
            await asyncio.sleep(2)
            
            # Strategy 1: Wait for and click countdown/timer buttons
            result = await self._handle_countdown_timers(driver)
            if result:
                return result
            
            # Strategy 2: Handle reCAPTCHA (if present)
            result = await self._handle_recaptcha(driver)
            if result:
                return result
            
            # Strategy 3: Look for hidden forms that appear after delay
            result = await self._handle_delayed_forms(driver)
            if result:
                return result
            
            # Strategy 4: Check for dynamically loaded content
            result = await self._handle_dynamic_content(driver)
            if result:
                return result
            
            # Strategy 5: Execute page scripts and extract variables
            result = await self._extract_from_page_context(driver)
            if result:
                return result
            
            # Strategy 6: Monitor network requests
            result = await self._monitor_network_requests(driver)
            if result:
                return result
            
            # Final: Get current URL (might have been redirected)
            current_url = driver.current_url
            if current_url != url and self._is_download_url(current_url):
                return {
                    "success": True,
                    "bypassed_url": current_url,
                    "type": "browser_automation"
                }
            
            return {"success": False, "error": "No bypass method succeeded"}
            
        except Exception as e:
            logger.error(f"Browser bypass error: {str(e)}")
            return {"success": False, "error": str(e)}
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    async def _handle_countdown_timers(self, driver) -> Optional[Dict]:
        """Handle countdown timers and wait for buttons to become clickable"""
        try:
            # Common countdown selectors
            countdown_selectors = [
                (By.ID, 'timer'),
                (By.CLASS_NAME, 'countdown'),
                (By.CLASS_NAME, 'timer'),
                (By.XPATH, "//*[contains(text(), 'second')]"),
                (By.XPATH, "//*[contains(text(), 'Please wait')]"),
            ]
            
            # Check if countdown exists
            countdown_found = False
            for by, selector in countdown_selectors:
                try:
                    element = driver.find_element(by, selector)
                    if element.is_displayed():
                        countdown_found = True
                        logger.info("Countdown detected, waiting...")
                        break
                except NoSuchElementException:
                    continue
            
            if countdown_found:
                # Wait for countdown to finish (max 60 seconds)
                for _ in range(60):
                    await asyncio.sleep(1)
                    
                    # Check if continue/download button is now available
                    button_selectors = [
                        (By.ID, 'continue'),
                        (By.ID, 'proceed'),
                        (By.ID, 'download'),
                        (By.CLASS_NAME, 'continue-button'),
                        (By.CLASS_NAME, 'download-button'),
                        (By.XPATH, "//button[contains(text(), 'Continue')]"),
                        (By.XPATH, "//a[contains(text(), 'Download')]"),
                    ]
                    
                    for by, selector in button_selectors:
                        try:
                            button = driver.find_element(by, selector)
                            if button.is_displayed() and button.is_enabled():
                                logger.info("Clicking continue button")
                                button.click()
                                await asyncio.sleep(2)
                                
                                current_url = driver.current_url
                                if self._is_download_url(current_url):
                                    return {
                                        "success": True,
                                        "bypassed_url": current_url,
                                        "type": "countdown_bypass"
                                    }
                        except NoSuchElementException:
                            continue
            
            return None
            
        except Exception as e:
            logger.error(f"Countdown handler error: {e}")
            return None
    
    async def _handle_recaptcha(self, driver) -> Optional[Dict]:
        """Detect and handle reCAPTCHA"""
        try:
            # Check for reCAPTCHA iframe
            recaptcha_iframes = driver.find_elements(By.XPATH, "//iframe[contains(@src, 'recaptcha')]")
            
            if recaptcha_iframes:
                logger.info("reCAPTCHA detected - cannot bypass automatically")
                return {
                    "success": False,
                    "error": "Site protected by reCAPTCHA - manual verification required"
                }
            
            return None
            
        except Exception as e:
            logger.error(f"reCAPTCHA handler error: {e}")
            return None
    
    async def _handle_delayed_forms(self, driver) -> Optional[Dict]:
        """Handle forms that appear after delay"""
        try:
            # Wait for forms to appear (max 30 seconds)
            wait = WebDriverWait(driver, 30)
            
            form_appeared = wait.until(
                EC.presence_of_element_located((By.TAG_NAME, "form"))
            )
            
            if form_appeared:
                # Find and submit the form
                forms = driver.find_elements(By.TAG_NAME, "form")
                for form in forms:
                    # Check if form has submit button
                    submit_buttons = form.find_elements(By.XPATH, ".//button[@type='submit'] | .//input[@type='submit']")
                    
                    if submit_buttons:
                        submit_buttons[0].click()
                        await asyncio.sleep(2)
                        
                        current_url = driver.current_url
                        if self._is_download_url(current_url):
                            return {
                                "success": True,
                                "bypassed_url": current_url,
                                "type": "delayed_form"
                            }
            
            return None
            
        except TimeoutException:
            return None
        except Exception as e:
            logger.error(f"Delayed form handler error: {e}")
            return None
    
    async def _handle_dynamic_content(self, driver) -> Optional[Dict]:
        """Handle dynamically loaded content"""
        try:
            # Scroll to load lazy content
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(2)
            
            # Check for dynamically added download links
            download_selectors = [
                "a[href*='download']",
                "a[href*='file']",
                "a[download]",
                ".download-link",
                "#download-link"
            ]
            
            for selector in download_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        href = element.get_attribute('href')
                        if href and self._is_download_url(href):
                            return {
                                "success": True,
                                "bypassed_url": href,
                                "type": "dynamic_content"
                            }
                except:
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"Dynamic content handler error: {e}")
            return None
    
    async def _extract_from_page_context(self, driver) -> Optional[Dict]:
        """Extract download URL from page JavaScript context"""
        try:
            # Try to extract common JavaScript variables
            var_names = [
                'downloadUrl', 'download_url', 'downloadLink', 'download_link',
                'fileUrl', 'file_url', 'directLink', 'direct_link',
                'url', 'link', 'href'
            ]
            
            for var_name in var_names:
                try:
                    result = driver.execute_script(f"return window.{var_name};")
                    if result and isinstance(result, str) and self._is_download_url(result):
                        return {
                            "success": True,
                            "bypassed_url": result,
                            "type": "javascript_context"
                        }
                except:
                    continue
            
            # Try to extract from page source
            page_source = driver.page_source
            url_pattern = r'https?://[^\s<>"]+\.(mp4|mkv|avi|zip|rar|pdf|exe|apk)'
            matches = re.findall(url_pattern, page_source, re.IGNORECASE)
            
            if matches:
                return {
                    "success": True,
                    "bypassed_url": matches[0],
                    "type": "page_source_extraction"
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Page context extraction error: {e}")
            return None
    
    async def _monitor_network_requests(self, driver) -> Optional[Dict]:
        """Monitor network requests for download URLs"""
        try:
            # Get performance logs (Chrome only)
            logs = driver.get_log('performance')
            
            for log in logs:
                message = log.get('message', '')
                
                # Look for download URLs in network requests
                if 'download' in message.lower() or 'file' in message.lower():
                    url_match = re.search(r'https?://[^\s"]+', message)
                    if url_match:
                        url = url_match.group(0)
                        if self._is_download_url(url):
                            return {
                                "success": True,
                                "bypassed_url": url,
                                "type": "network_monitoring"
                            }
            
            return None
            
        except Exception as e:
            logger.error(f"Network monitoring error: {e}")
            return None
    
    def _is_download_url(self, url: str) -> bool:
        """Check if URL is a download link"""
        if not url or not isinstance(url, str):
            return False
        
        download_indicators = [
            '.mp4', '.mkv', '.avi', '.zip', '.rar', '.pdf',
            '.doc', '.docx', '.exe', '.apk', '.mp3', '.iso',
            '/download/', '/file/', '/get/', 'download=', 'file='
        ]
        
        url_lower = url.lower()
        return any(indicator in url_lower for indicator in download_indicators)
    
    def cleanup(self):
        """Cleanup browser resources"""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None

# Global instance
advanced_bypasser = AdvancedBypasser()
