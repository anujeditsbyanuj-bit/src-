# Akbots - Don't Remove Credit - @AkBots_Official
#
# Akbots/cf_lib/ — the two in-process Cloudflare/Turnstile-bypass
# implementations that Akbots/cf_bypass.py (generic fetch/bypass helper)
# and Akbots/bypassers/lksfy.py (site-specific) import as optional
# fallbacks. Both callers already wrap their imports from here in
# try/except ImportError and degrade to returning None if this package
# — or its own DrissionPage/camoufox dependencies — isn't available, so
# nothing else in the bot breaks if this stays unused on a given host.
