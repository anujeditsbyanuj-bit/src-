# Akbots — direct (no API key) MX Player resolver.
#
# Fallback for Akbots/mxplayer.py's key-based resolver (ott.dkbotzpro.in).
# Talks to MX Player's own web API directly instead of going through a
# third-party proxy — no MXPLAYER_API_KEY needed, but more likely to break
# if MX Player changes their internal API shape.
#
# Ported from the user-supplied helpers.py (mx_player_api + constants only;
# mxplayer.py already has its own yt-dlp format extraction / download code,
# so that part of helpers.py wasn't ported here).

import re
import aiohttp

MX_TITLE_RE = r"^(?:https?://(?:www\.)?mxplayer\.in/(?P<type>movie|show)/.*?-)?(?P<id>[a-f0-9]+)(?:\?.*)?$"
MX_API_BASE = "https://api.mxplayer.in/v1/web"
MX_CDN_BASE = "https://d3sgzbosmwirao.cloudfront.net/"
MX_DRM_LICENCE_BASE = "https://playlicense.mxplay.com/widevine/proxy?content_id="

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://www.mxplayer.in",
    "Referer": "https://www.mxplayer.in/",
}
PARAMS = "&platform=com.mxplay.desktop&device-density=2&kids-mode-enabled=false&content-languages=hi,en,ta,te,bn,ml,kn,mr,pa,gu,bho"


def is_mxplayer_url(url: str) -> bool:
    """Check if the URL matches the direct-API's stricter format
    (trailing hex content id required — mxplayer.py's own PATTERN is looser
    and is what actually gates whether this module gets called at all)."""
    return bool(re.match(MX_TITLE_RE, url))


async def mx_player_api(url: str) -> dict:
    """Fetch video metadata and stream links straight from MX Player's API.

    Returns the same {"status", "m3u8_url", "mpd_url", "show_title",
    "seo_title", "season", "thumbnail", ...} shape as the key-based
    resolver's response, so callers can treat both interchangeably.
    """
    match = re.match(MX_TITLE_RE, url)
    if not match:
        return {"status": False, "message": "Invalid URL format"}

    content_type = match.group("type") or "video"
    content_id = match.group("id")
    api_type = "episode" if content_type == "show" else "movie"

    api_url = f"{MX_API_BASE}/detail/video?type={api_type}&id={content_id}{PARAMS}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(api_url, headers=HEADERS) as response:
                if response.status != 200:
                    return {"status": False, "message": f"API Error: HTTP {response.status}"}

                data = await response.json()
                if "title" not in data:
                    return {"status": False, "message": "API response does not contain valid title"}

                title = data.get("title", "Unknown Title")
                description = data.get("description", "")

                image_info = data.get("imageInfo", [])
                thumbnail = ""
                if image_info:
                    thumb_path = image_info[0].get("url")
                    if thumb_path:
                        thumbnail = f"https://qqcdnpictest.mxplay.com/{thumb_path}"

                stream = data.get("stream", {})
                if not stream:
                    return {"status": False, "message": "No stream info available for this title."}

                provider = stream.get("provider", "")
                hls_url = ""
                dash_url = ""

                if provider == "thirdParty":
                    hls_url = stream.get("thirdParty", {}).get("webHlsUrl") or stream.get("hlsUrl", "")
                else:
                    dash_provider = stream.get(provider, {}).get("dash", stream.get("dash", {}))
                    dash_url = dash_provider.get("high") or dash_provider.get("base") or dash_provider.get("main") or ""

                    hls_provider = stream.get(provider, {}).get("hls", stream.get("hls", {}))
                    hls_url = hls_provider.get("high") or hls_provider.get("base") or hls_provider.get("main") or ""

                if dash_url and not dash_url.startswith("http"):
                    dash_url = f"{MX_CDN_BASE}{dash_url}"
                if hls_url and not hls_url.startswith("http"):
                    hls_url = f"{MX_CDN_BASE}{hls_url}"

                download_url = dash_url or hls_url
                if not download_url:
                    return {"status": False, "message": "No valid DASH/HLS stream found."}

                is_drm = stream.get("drmProtect", False)
                license_url = ""
                if is_drm:
                    video_hash = stream.get("videoHash", "")
                    license_url = f"{MX_DRM_LICENCE_BASE}{video_hash}"

                return {
                    "status": True,
                    "show_title": title,
                    "seo_title": data.get("seoTitle", title),
                    "season": data.get("season", {}).get("seasonNo", ""),
                    "description": description,
                    "thumbnail": thumbnail,
                    "m3u8_url": hls_url,
                    "mpd_url": dash_url,
                    "is_drm": is_drm,
                    "license_url": license_url,
                }
        except Exception as e:
            return {"status": False, "message": str(e)}
