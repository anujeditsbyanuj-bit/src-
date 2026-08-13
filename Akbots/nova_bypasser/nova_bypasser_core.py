import logging
import re
import asyncio
from urllib.parse import urlparse, parse_qs
from typing import Dict, Optional
from config import Config, config
from .cloudflare import CloudflareBypasser
from .advanced import advanced_bypasser
from .ai_fallback import ai_bypasser
from .sites import gdtot, sharerw, universal, gplinks, thirdparty_apis, adlinkfly, urlking
from .ss.domain_specific import DomainSpecificHandler
from .ss.nicktrick import NicktrickResolver
from .ss.smart_resolver import SmartResolver
from .ss.api_fallback import RotatingBypassAPI, BypassVIPAPI
from .ss.online_fallback import OnlineBypassFallback
from .domain_db.db import DomainDB
from .domain_db.checker import DomainChecker

logger = logging.getLogger(__name__)

class LinkBypasser:
    """Main link bypasser class"""
    
    def __init__(self):
        self.cf_bypasser = CloudflareBypasser()
        # Ported from SS_Bypass_bot: ~60 dedicated domain routes (drives,
        # file hosters, video hosters, shorteners) + a shrinkme-clone-
        # cluster resolver + 2 more bypass-API rotators + a live SQLite
        # domain reputation DB (community-sourced active/inactive/
        # malicious shortener lists).
        self.domain_specific = DomainSpecificHandler()
        self.nicktrick = NicktrickResolver()
        self.smart_resolver = SmartResolver()
        self.rotating_api = RotatingBypassAPI()
        self.bypass_vip_api = BypassVIPAPI(config.bypass_vip_api_key)
        self.online_fallback = OnlineBypassFallback()
        self.domain_db = DomainDB(config.db_path)
        self.domain_checker = DomainChecker(self.domain_db)
        self.supported_sites = {
            # GDToT and variants
            'gdtot': ['gdtot', 'gdflix', 'gd.com'],
            'sharerw': ['sharer.pw', 'filepress'],
            'uptobox': ['uptobox.com'],
            'terabox': ['terabox.com', '1024tera.com', 'teraboxapp.com'],
            'anonfiles': ['anonfiles.com', 'bayfiles.com'],
            'linkvertise': ['linkvertise.com', 'link-to.net', 'up-to-down.net'],
            'adfly': ['adf.ly', 'ay.gy', 'j.gs'],
            'gplinks': ['gplinks.co', 'gplinks.in'],
            'ouo': ['ouo.io', 'ouo.press'],
            'shortingly': ['shortingly.in', 'bit.ly'],
            'droplink': ['droplink.co', 'droplink.org'],
            'linkbox': ['linkbox.to'],
            'wetransfer': ['wetransfer.com'],
            # Add more sites as needed
        }
    
    async def bypass(self, url: str) -> Dict:
        """Main bypass method"""
        try:
            logger.info(f"Starting bypass for: {url}")
            domain = urlparse(url).netloc.lower().replace('www.', '')

            # Domain reputation check (Akbots/nova_bypasser/domain_db/,
            # ported from SS_Bypass_bot) — community-sourced shortener
            # lists + our own self-probing. Only short-circuits on
            # "malicious" (phishing/malware-flagged); "inactive" is NOT
            # trusted alone to block a bypass attempt, since a stale/
            # over-eager probe shouldn't stop a method that still works.
            try:
                domain_status = await self.domain_checker.check_url(url)
                if domain_status.get("status") == "malicious":
                    return {
                        "success": False,
                        "error": f"Domain {domain_status.get('domain')} is flagged malicious/phishing — refusing to bypass.",
                    }
            except Exception as e:
                logger.debug(f"Domain reputation check skipped ({e})")

            # Domain-specific routes ported from SS_Bypass_bot (~60 exact
            # regex → dedicated-function routes covering GDrive clones,
            # file hosters, video hosters, and region-specific shorteners)
            # get first shot when one matches — same priority SS_Bypass_bot
            # itself gives them, since a precise route beats generic
            # extraction almost every time.
            if self.domain_specific.has_route(url):
                logger.info("Domain-specific route matched, trying it first...")
                direct = await self.domain_specific.resolve(url)
                if direct:
                    await self._record(domain, "domain_specific", True)
                    return {"success": True, "bypassed_url": direct, "type": "domain_specific"}

            # AdLinkFly-family (ported from bypassx11-bot) — ~50 ad-locker
            # sites built on the same commercial script, one 2-step
            # token+POST technique covers all of them.
            if adlinkfly.is_adlinkfly_domain(url):
                logger.info("AdLinkFly-family domain matched, trying dedicated bypass...")
                result = await adlinkfly.bypass(url)
                if result.get("success"):
                    await self._record(domain, result.get("type", "adlinkfly"), True)
                    return result

            # urlking (ported from SHUVO-BYPASS-API) — sits behind an
            # interactive Cloudflare Turnstile challenge none of the other
            # methods above can solve (they're all plain HTTP sessions;
            # this needs an actual rendering browser). Checked here, not
            # folded into the generic universal ladder, since it's the one
            # site in this file that deliberately launches a real browser
            # up front — worth paying that cost only once we know it's
            # actually urlking, not on every unmatched URL.
            if urlking.is_urlking_domain(url):
                logger.info("urlking domain matched, trying dedicated bypass...")
                result = await urlking.bypass(url)
                if result.get("success"):
                    await self._record(domain, result.get("type", "urlking"), True)
                    return result

            # Lightweight learning: if this domain has a proven-best method
            # from past attempts, try it first (see database/db.py's
            # record_bypass_result / get_best_bypass_method).
            best_method = await self._get_learned_method(domain)
            if best_method:
                logger.info(f"Trying learned method '{best_method}' for {domain} first")
                result = await self._dispatch(best_method, url)
                if result.get("success"):
                    await self._record(domain, best_method, True)
                    return result
                # fall through to the normal ladder below

            # Identify site type
            site_type = self._identify_site(url)
            
            if not site_type:
                result = await self._bypass_universal(url)
                await self._record(domain, result.get("type", "universal"), result.get("success", False))
                return result
            
            logger.info(f"Identified site type: {site_type}")
            
            # Route to appropriate bypasser
            if site_type == 'gdtot':
                result = await self._bypass_gdtot(url)
            elif site_type == 'sharerw':
                result = await self._bypass_sharerw(url)
            elif site_type == 'uptobox':
                result = await self._bypass_uptobox(url)
            elif site_type == 'terabox':
                result = await self._bypass_terabox(url)
            elif site_type == 'gplinks':
                result = await self._bypass_gplinks(url)
            elif site_type in ['linkvertise', 'adfly', 'ouo', 'shortingly', 'droplink']:
                result = await self._bypass_shortener(url, site_type)
            else:
                # Try universal bypasser
                result = await self._bypass_universal(url)

            await self._record(domain, result.get("type", site_type), result.get("success", False))
            return result
            
        except Exception as e:
            logger.error(f"Error bypassing {url}: {str(e)}")
            return {
                "success": False,
                "error": f"Bypass failed: {str(e)}"
            }

    async def _get_learned_method(self, domain: str) -> Optional[str]:
        try:
            from database.db import db
            return await db.get_best_bypass_method(domain)
        except Exception as e:
            logger.debug(f"Learned-method lookup skipped ({e})")
            return None

    async def _record(self, domain: str, method: str, success: bool):
        try:
            from database.db import db
            await db.record_bypass_result(domain, method or "unknown", success)
        except Exception as e:
            logger.debug(f"Bypass stats recording skipped ({e})")

    async def _dispatch(self, method: str, url: str) -> Dict:
        """Re-run a specific method by name (used for the 'try the
        learned-best method first' shortcut above)."""
        dispatch_map = {
            'gdtot': self._bypass_gdtot,
            'sharerw': self._bypass_sharerw,
            'uptobox': self._bypass_uptobox,
            'terabox': self._bypass_terabox,
            'gplinks_api_token': self._bypass_gplinks,
            'gplinks_html_extraction': self._bypass_gplinks,
            'cloudflare': self.cf_bypasser.bypass,
            'ai_powered': ai_bypasser.bypass,
            'bypass_city_redirect': thirdparty_apis.bypass_city_get,
            'bypass_city_get': thirdparty_apis.bypass_city_get,
            'bypass_city_api': thirdparty_apis.bypass_city_api,
            'adbypass_mirror_redirect': thirdparty_apis.adbypass_mirror,
            'adbypass_mirror': thirdparty_apis.adbypass_mirror,
            'bypass_vip': thirdparty_apis.bypass_vip,
            'domain_specific': self.domain_specific.resolve,
            'adlinkfly_redirect': adlinkfly.bypass,
            'adlinkfly_html_extraction': adlinkfly.bypass,
            'adlinkfly_api_token': adlinkfly.bypass,
            'adlinkfly_api_token_html': adlinkfly.bypass,
            'nicktrick': self.nicktrick.resolve,
            'smart_resolver': self.smart_resolver.resolve,
            'rotating_api': self.rotating_api.bypass,
            'bypass_vip_dedicated': self.bypass_vip_api.bypass,
            'online_fallback': self.online_fallback.bypass,
        }
        fn = dispatch_map.get(method)
        if fn:
            result = await fn(url)
            # domain_specific / nicktrick / rotating_api / bypass_vip_dedicated
            # return a plain string-or-None, not our {success, ...} dict shape.
            if isinstance(result, str):
                return {"success": True, "bypassed_url": result, "type": method} if result else {"success": False, "error": "no result"}
            return result
        if method and method.startswith('shortener_'):
            return await universal.bypass_shortener(url, method.split('_', 1)[1])
        return {"success": False, "error": "no dispatcher for learned method"}
    
    def _identify_site(self, url: str) -> Optional[str]:
        """Identify the type of site from URL"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove www. prefix
            domain = domain.replace('www.', '')
            
            # Check against supported sites
            for site_type, domains in self.supported_sites.items():
                for supported_domain in domains:
                    if supported_domain in domain:
                        return site_type
            
            return None
            
        except Exception as e:
            logger.error(f"Error identifying site: {str(e)}")
            return None
    
    async def _bypass_gdtot(self, url: str) -> Dict:
        """Bypass GDToT links"""
        try:
            if not Config.GDTOT_CRYPT:
                return await universal.bypass_gdtot_alternative(url)
            
            result = await gdtot.bypass(url, Config.GDTOT_CRYPT)
            return result
            
        except Exception as e:
            logger.error(f"GDToT bypass error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _bypass_sharerw(self, url: str) -> Dict:
        """Bypass Sharer.pw links"""
        try:
            if not Config.XSRF_TOKEN or not Config.LARAVEL_SESSION:
                return await universal.bypass_sharerw_alternative(url)
            
            result = await sharerw.bypass(url, Config.XSRF_TOKEN, Config.LARAVEL_SESSION)
            return result
            
        except Exception as e:
            logger.error(f"Sharer.pw bypass error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _bypass_uptobox(self, url: str) -> Dict:
        """Bypass Uptobox links"""
        try:
            return await universal.bypass_uptobox(url, Config.UPTOBOX_TOKEN)
        except Exception as e:
            logger.error(f"Uptobox bypass error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _bypass_terabox(self, url: str) -> Dict:
        """Bypass Terabox links"""
        try:
            return await universal.bypass_terabox(url, Config.TERA_COOKIE)
        except Exception as e:
            logger.error(f"Terabox bypass error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _bypass_shortener(self, url: str, site_type: str) -> Dict:
        """Bypass various URL shorteners"""
        try:
            return await universal.bypass_shortener(url, site_type)
        except Exception as e:
            logger.error(f"Shortener bypass error: {str(e)}")
            return {"success": False, "error": str(e)}

    async def _bypass_gplinks(self, url: str) -> Dict:
        """Dedicated GPLinks bypass (token+API technique); falls back to
        the generic shortener path if the dedicated method comes up empty."""
        try:
            result = await gplinks.bypass(url)
            if result.get("success"):
                return result
            logger.info("Dedicated GPLinks bypass came up empty, falling back to generic shortener path")
            return await universal.bypass_shortener(url, "gplinks")
        except Exception as e:
            logger.error(f"GPLinks bypass error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _bypass_universal(self, url: str) -> Dict:
        """Universal bypass method for unknown sites"""
        try:
            # Try multiple strategies in order
            
            # 1. Try direct extraction with all methods (HTML, CSS, JS, etc.)
            logger.info("Trying direct extraction with multiple methods...")
            result = await universal.extract_direct_link(url)
            if result["success"]:
                return result

            # 1b. Shrinkme-clone cluster resolver (vplink, arolinks,
            # babylinks, earnlinks, shrinkme.io/.click, hypershort — ported
            # from SS_Bypass_bot's nicktrick.py). Only tried for domains in
            # that cluster, cheap no-op otherwise.
            domain_only = urlparse(url).netloc.lower().replace('www.', '')
            if any(d in domain_only for d in ("vplink.in", "arolinks.com", "babylinks.in",
                    "earnlinks.in", "get2short.com", "nowshort.com", "shrinkme.io",
                    "shrinkme.click", "hypershort.com")):
                logger.info("Trying nicktrick cluster resolver...")
                nt_result = await self.nicktrick.resolve(url)
                if nt_result:
                    return {"success": True, "bypassed_url": nt_result, "type": "nicktrick"}

            # 1c. Smart resolver (ported from SS_Bypass_bot) — handles
            # verification-gate detection, countdown/AJAX-endpoint probing,
            # multi-hop JS redirect chains, and GDrive file-ID extraction.
            # More thorough than the plain extraction above for sites with
            # multi-step "wait N seconds then click" gates.
            logger.info("Trying smart resolver...")
            smart_result = await self.smart_resolver.resolve(url)
            if smart_result:
                return {"success": True, "bypassed_url": smart_result, "type": "smart_resolver"}

            # 2. Try Cloudflare bypass
            if Config.CLOUDFLARE_COOKIE:
                logger.info("Trying Cloudflare bypass...")
                result = await self.cf_bypasser.bypass(url)
                if result["success"]:
                    return result
            
            # 3. Try generic bypass methods
            logger.info("Trying generic bypass...")
            result = await universal.generic_bypass(url)
            if result["success"]:
                return result

            # 3b. Try external bypass-as-a-service APIs — 4 from
            # thirdparty_apis.py (Bypass.city, adbypass.org, Bypass.vip)
            # plus SS_Bypass_bot's own rotating pool of 5 free APIs +
            # dedicated Bypass.vip client (config.bypass_vip_api_key).
            # Cheap network calls, worth trying before the heavier
            # browser/AI tiers below.
            logger.info("Trying third-party bypass APIs...")
            result = await thirdparty_apis.bypass_via_third_party_apis(url)
            if result["success"]:
                return result

            logger.info("Trying rotating free bypass API pool...")
            rotating_result = await self.rotating_api.bypass(url)
            if rotating_result:
                return {"success": True, "bypassed_url": rotating_result, "type": "rotating_api"}

            if config.bypass_vip_api_key:
                logger.info("Trying dedicated Bypass.vip API...")
                vip_result = await self.bypass_vip_api.bypass(url)
                if vip_result:
                    return {"success": True, "bypassed_url": vip_result, "type": "bypass_vip_dedicated"}

            logger.info("Trying online unfurl fallback (openunfurl, unfurler)...")
            online_result = await self.online_fallback.bypass(url)
            if online_result:
                return {"success": True, "bypassed_url": online_result, "type": "online_fallback"}

            # 4. Try advanced browser automation (for complex JS sites)
            logger.info("Trying advanced browser automation...")
            result = await advanced_bypasser.bypass_with_browser(url)
            if result["success"]:
                return result

            # 5. Last resort: hand the raw HTML to an LLM and ask it to
            # find the real link (Akbots/nova_bypasser/ai_fallback.py).
            # No-ops cleanly if OPENAI_API_KEY isn't set.
            if ai_bypasser.is_available():
                logger.info("Trying AI-assisted bypass...")
                result = await ai_bypasser.bypass(url)
                if result["success"]:
                    return result

            return {
                "success": False,
                "error": "All bypass methods failed. Site may not be supported yet."
            }
            
        except Exception as e:
            logger.error(f"Universal bypass error: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def get_supported_sites(self) -> list:
        """Get list of all supported sites"""
        sites = []
        for site_type, domains in self.supported_sites.items():
            sites.extend(domains)
        return sorted(set(sites))
    
    def is_supported(self, url: str) -> bool:
        """Check if URL is from a supported site"""
        return self._identify_site(url) is not None
