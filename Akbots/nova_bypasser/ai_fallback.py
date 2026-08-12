"""
AI-powered bypass — ported from the Nova-Link-Bypasser-Bot repo's
bypass/ai_bypass.py. This is the *last-resort* tier: when every
rule-based method (universal HTML/CSS/JS extraction, Cloudflare bypass,
generic shortener redirect-follow, optional browser automation) has
already failed, hand the raw page HTML to an LLM and ask it to find the
real destination URL — catches new/changed ad-lock schemes the rule-based
methods don't recognise yet, at the cost of an API call.

Reuses this bot's existing OPENAI_API_KEY (see config.py) — no separate
key needed. If OPENAI_API_KEY isn't set, this tier just reports itself
unavailable and core.py's chain stops one step earlier, exactly as if
this file didn't exist.
"""

import json
import logging
import requests
from typing import Dict, Optional
from config import Config, OPENAI_API_KEY

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

_SYSTEM_PROMPT = """You are an expert web scraping and bypass specialist. Your task is to analyze HTML content from link shortener / ad-lock pages and extract the final destination URL.

Analyze the provided HTML and:
1. Identify what type of protection is being used (countdown, CAPTCHA, hidden elements, JavaScript obfuscation, etc.)
2. Look for any hidden URLs in the HTML, JavaScript, or meta tags
3. Identify patterns that might reveal the destination URL
4. Provide the final destination URL if found

Respond in JSON format only, with these fields:
- success: boolean indicating if you found the destination URL
- url: the destination URL (if success is true)
- confidence: number from 0-1 indicating confidence level
- reasoning: brief explanation of how you found the URL
- error: error message (if success is false)

Look for: hidden form inputs with URLs, JavaScript variables containing URLs, base64 encoded strings, data attributes on elements, comments containing URLs, obfuscated JavaScript redirects."""


class AIBypass:
    METHOD_NAME = "ai_powered"

    def __init__(self):
        self.client: Optional["AsyncOpenAI"] = None
        if AsyncOpenAI is not None and OPENAI_API_KEY:
            self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    def is_available(self) -> bool:
        return self.client is not None

    async def bypass(self, url: str) -> Dict:
        if not self.client:
            return {"success": False, "error": "AI fallback not configured (OPENAI_API_KEY unset)"}

        try:
            logger.info(f"[nova_bypasser/ai] Attempting AI-assisted bypass for: {url}")
            page_content = self._fetch_page(url)
            if not page_content:
                return {"success": False, "error": "Failed to fetch page for AI analysis"}

            result = await self._analyze_with_ai(url, page_content)
            if result and result.get("success"):
                bypass_url = result.get("url")
                if bypass_url and bypass_url.startswith("http"):
                    logger.info(f"[nova_bypasser/ai] Bypass successful: {bypass_url} (confidence={result.get('confidence')})")
                    return {"success": True, "bypassed_url": bypass_url, "type": "ai_powered"}

            return {"success": False, "error": (result or {}).get("error", "AI analysis found nothing usable")}

        except Exception as e:
            logger.error(f"[nova_bypasser/ai] Error: {e}")
            return {"success": False, "error": str(e)}

    def _fetch_page(self, url: str) -> Optional[str]:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30, allow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error(f"[nova_bypasser/ai] Failed to fetch page: {e}")
            return None

    async def _analyze_with_ai(self, url: str, page_content: str) -> Optional[Dict]:
        try:
            max_len = 8000
            if len(page_content) > max_len:
                page_content = page_content[:max_len] + "..."

            user_prompt = (
                f"URL: {url}\n\nHTML Content:\n```html\n{page_content}\n```\n\n"
                f"Analyze this page and extract the destination URL. Respond in JSON format only."
            )

            response = await self.client.chat.completions.create(
                model=Config.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=Config.AI_TEMPERATURE,
                max_tokens=Config.AI_MAX_TOKENS,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"[nova_bypasser/ai] Analysis failed: {e}")
            return None


# Global instance — mirrors the rest of this package's module-level singletons.
ai_bypasser = AIBypass()
