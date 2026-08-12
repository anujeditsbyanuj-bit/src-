# Akbots - Don't Remove Credit - @AkBots_Official
#
# CloudflareBypasser — ported from the well-known standalone
# "Cloudflare-Bypasser-DrissionPage" technique (sarperavci's project is the
# common reference implementation this pattern is known by): given an
# already-navigated DrissionPage ChromiumPage sitting on a Cloudflare
# "Just a moment..." interstitial, repeatedly locate the Turnstile
# checkbox — which Cloudflare buries inside nested shadow DOM, sometimes
# inside an iframe inside another shadow root — and click it until the
# interstitial clears or max_retries is hit.
#
# Only this one class was ported in (see Akbots/cf_bypass.py's header
# comment) — the source project's own FastAPI server/tests/Dockerfile
# aren't needed here since Akbots/cf_bypass.py drives this in-process.
#
# NOTE: DrissionPage's DOM/shadow-root API used below hasn't been
# exercised against a live Cloudflare challenge in this environment (no
# network here to install DrissionPage + a real browser) — the traversal
# logic matches the widely-referenced technique, but if Cloudflare has
# changed its widget markup since, the shadow-root walk may need
# adjusting. Report back the exact failure (bypass() just times out vs.
# an exception) and it can be tightened.

import time
import logging

logger = logging.getLogger(__name__)


class CloudflareBypasser:
    def __init__(self, driver, max_retries: int = 5, log: bool = False):
        self.driver = driver
        self.max_retries = max_retries
        self.log = log

    def log_message(self, message: str):
        if self.log:
            print(message)
        else:
            logger.debug(message)

    # --- shadow-DOM traversal -------------------------------------------
    # Cloudflare mounts the Turnstile widget inside a chain of shadow
    # roots (sometimes with an iframe partway down) rather than plain DOM,
    # specifically to make it awkward to script against.

    def _search_recursively_shadow_root_with_iframe(self, ele):
        if ele.shadow_root:
            if ele.shadow_root.child().tag == "iframe":
                return ele.shadow_root.child()
            return None
        for child in ele.children():
            result = self._search_recursively_shadow_root_with_iframe(child)
            if result:
                return result
        return None

    def _search_recursively_shadow_root_with_cf_input(self, ele):
        if ele.shadow_root:
            found = ele.shadow_root.ele("tag:input", timeout=0.5)
            if found:
                return found
            return None
        for child in ele.children():
            result = self._search_recursively_shadow_root_with_cf_input(child)
            if result:
                return result
        return None

    def _locate_cf_button(self):
        """Finds the Turnstile checkbox `<input>` however deep Cloudflare
        has currently nested it. Returns None if not found on this
        render — bypass() just retries the whole detection on the next
        loop iteration rather than treating that as fatal."""
        try:
            eles = self.driver.eles("tag:input")
        except Exception:
            eles = []

        for ele in eles:
            attrs = ele.attrs or {}
            if "name" in attrs and "cf-turnstile" in attrs.get("name", ""):
                parent = ele.parent()
                for _ in range(4):
                    if parent is None:
                        break
                    if getattr(parent, "shadow_root", None):
                        found = self._search_recursively_shadow_root_with_cf_input(parent)
                        if found:
                            return found
                        break
                    parent = parent.parent()
                break

        # Fallback: walk every <div> looking for one whose shadow root
        # contains the iframe Cloudflare sometimes wraps the checkbox in.
        try:
            div_elements = self.driver.eles("tag:div")
        except Exception:
            div_elements = []
        for div in div_elements:
            if getattr(div, "shadow_root", None):
                try:
                    if div.shadow_root.child().tag == "iframe":
                        iframe = self._search_recursively_shadow_root_with_iframe(div)
                        if iframe:
                            body = iframe.ele("tag:body", timeout=1)
                            if body:
                                button = body.sr("tag:input")
                                if button:
                                    return button
                except Exception:
                    continue
        return None

    def is_bypassed(self) -> bool:
        try:
            title = (self.driver.title or "").lower()
        except Exception:
            return False
        return "just a moment" not in title and "attention required" not in title

    def bypass(self):
        """Blocking — call via asyncio.to_thread, never directly from
        async code (matches Akbots/cf_bypass.py's _run_bypass())."""
        try_count = 0
        while not self.is_bypassed():
            if try_count >= self.max_retries:
                self.log_message("CloudflareBypasser: exceeded max_retries, giving up.")
                break
            self.log_message(f"CloudflareBypasser: attempt {try_count + 1} — locating Turnstile checkbox...")
            try:
                button = self._locate_cf_button()
                if button:
                    button.click()
                else:
                    self.log_message("CloudflareBypasser: checkbox not found on this render.")
            except Exception as e:
                self.log_message(f"CloudflareBypasser: click attempt failed: {e}")
            try_count += 1
            time.sleep(2)

        if self.is_bypassed():
            self.log_message("CloudflareBypasser: bypass successful.")
        else:
            self.log_message("CloudflareBypasser: bypass failed.")
        return self.is_bypassed()
