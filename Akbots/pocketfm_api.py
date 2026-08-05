# Akbots
# PocketFM API wrapper — ported from a reverse-engineered CLI script
# (pocketfm_app.py) the user provided, converted from sync `requests` calls
# to async aiohttp so it fits this bot's plugin style (same pattern as
# jiosaavn_api.py). This talks to PocketFM's REAL app API — no headless
# browser / guessing needed, unlike the first attempt at this plugin.
#
# Endpoints used (all observed from the app's own traffic, not documented
# publicly anywhere):
#   POST https://iam.pocketfm.com/v1/auth/devices
#       -> issues a free/anonymous "guest" access token, no login needed.
#   GET  https://api.pocketfm.com/v2/content_api/show.get_details
#       -> given show_id (+ optionally curr_story_id), returns show info
#          plus the full "stories" (episode) list, each with its own
#          media_url (audio) / video_url (DASH manifest, for shows that
#          also have an animated video version).
#
# Audio/video quality: PocketFM doesn't return a clean list of quality
# variants in the API response — get_details gives ONE base media_url /
# video_url per episode. The actual bitrate-specific files live at
# predictable sibling paths (e.g. swapping ".../master.m3u8" for
# ".../audio_hls_128000.mp4"), discovered by the original script's author
# via traffic inspection. get_audio_options()/get_video_options() below
# HEAD-probe those candidate paths and only offer the ones that actually
# respond 200 — some shows won't have all tiers.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import re
import uuid
import base64
import json
import aiohttp

_DEVICE_URL = "https://iam.pocketfm.com/v1/auth/devices"
_SHOW_DETAILS_URL = "https://api.pocketfm.com/v2/content_api/show.get_details"

_BASE_HEADERS = {
    "User-Agent": "com.radio.pocketfm",
    "App-Name": "pocket_fm",
    "Platform": "android",
}

_VIDEO_CANDIDATES = (
    ("1080p (DASH)", "video_dash_1080p.mp4"),
    ("720p (DASH)", "video_dash_720p.mp4"),
    ("720p (VP9)", "vp9_720p.mp4"),
    ("480p (DASH)", "video_dash_480p.mp4"),
    ("480p (VP9)", "vp9_480p.mp4"),
    ("360p (DASH)", "video_dash_360p.mp4"),
    ("360p (VP9)", "vp9_360p.mp4"),
    ("240p (DASH)", "video_dash_240p.mp4"),
    ("240p (VP9)", "vp9_240p.mp4"),
    ("1080p (HLS)", "video_hls_1080p.mp4"),
    ("144p (VP9)", "vp9_144p.mp4"),
)


class PocketFMError(Exception):
    pass


class PocketFM:
    async def generate_guest_token(self, device_id: str = None):
        """Free, anonymous, no-login-required access token. Returns
        (bearer_token, device_id)."""
        device_id = device_id or uuid.uuid4().hex[:16]
        headers = {**_BASE_HEADERS, "Device-Id": device_id}
        async with aiohttp.ClientSession() as session:
            async with session.post(_DEVICE_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 201:
                    raise PocketFMError(f"Guest token request failed (HTTP {r.status})")
                data = await r.json()
        token = data.get("access_token")
        if not token:
            raise PocketFMError("Guest token response had no access_token.")
        return f"Bearer {token}", device_id

    async def get_show_details(self, show_id: str, token: str, story_id: str = None) -> dict:
        """Returns the show_details dict (includes a 'stories' list, one
        entry per episode, each with story_id/story_title/media_url/
        video_url). story_id is optional — only used by the app to mark a
        "current position"; the full stories list comes back either way."""
        headers = {"User-Agent": "com.radio.pocketfm", "Authorization": token}
        params = {"show_id": show_id, "info_level": "max"}
        if story_id:
            params["curr_story_id"] = story_id
        async with aiohttp.ClientSession() as session:
            async with session.get(_SHOW_DETAILS_URL, params=params, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    raise PocketFMError(f"show.get_details failed (HTTP {r.status})")
                data = await r.json()
        result = data.get("result") or []
        if not result:
            raise PocketFMError("No show details returned — show_id may be wrong, or the guest token expired.")
        return result[0]

    @staticmethod
    def find_story(show_details: dict, story_id: str) -> dict:
        for story in show_details.get("stories", []):
            if story.get("story_id") == story_id:
                return story
        return None

    async def _head_size(self, session: aiohttp.ClientSession, url: str, headers: dict):
        try:
            async with session.head(url, headers=headers, allow_redirects=True,
                                     timeout=aiohttp.ClientTimeout(total=6)) as r:
                if r.status == 200:
                    return int(r.headers.get("content-length", 0))
        except Exception:
            pass
        return None

    async def get_audio_options(self, media_url: str, video_url: str, token: str) -> list:
        """Returns [{"label": ..., "url": ...}, ...] — HEAD-probes the
        known sibling-file naming scheme for HLS audio tiers, falls back to
        a single "Default Stream" entry using the raw media_url/video_url
        if none of the probes hit (some episodes just don't use that HLS
        layout, or every probe is genuinely unavailable)."""
        headers = {"User-Agent": "com.radio.pocketfm", "Authorization": token}
        audio_base = None
        is_hls = False
        if video_url:
            audio_base = re.sub(r"/[^/]+\.mpd$", "", video_url)
        elif media_url:
            audio_base = media_url
            is_hls = "master.m3u8" in media_url

        options = []
        if audio_base:
            async with aiohttp.ClientSession() as session:
                if is_hls:
                    for label, bitrate_file in (
                        ("128 kbps", "audio_hls_128000.mp4"),
                        ("64 kbps", "audio_hls_64000.mp4"),
                        ("32 kbps", "audio_hls_32000.mp4"),
                    ):
                        url = audio_base.replace("master.m3u8", bitrate_file)
                        size = await self._head_size(session, url, headers)
                        if size is not None:
                            options.append({"label": f"🎧 {label} (~{size / 1024 / 1024:.1f} MB)", "url": url})
                if not options:
                    dash_url = audio_base if audio_base.endswith(".mp4") else f"{audio_base}/audio.mp4"
                    size = await self._head_size(session, dash_url, headers)
                    if size is not None:
                        options.append({"label": f"🎧 Standard Quality (~{size / 1024 / 1024:.1f} MB)", "url": dash_url})

        if not options and audio_base:
            options.append({"label": "🎧 Default Stream", "url": audio_base})
        return options

    async def get_video_options(self, video_url: str, token: str) -> list:
        """Same idea as get_audio_options but for shows that also have an
        animated video version — returns [] if there's no video_url at all
        or none of the known sibling paths respond."""
        if not video_url:
            return []
        headers = {"User-Agent": "com.radio.pocketfm", "Authorization": token}
        base_url = re.sub(r"/[^/]+\.(mpd|m3u8)$", "", video_url)
        options = []
        async with aiohttp.ClientSession() as session:
            for label, suffix in _VIDEO_CANDIDATES:
                url = f"{base_url}/{suffix}"
                size = await self._head_size(session, url, headers)
                if size is not None:
                    options.append({"label": f"🎬 {label} (~{size / 1024 / 1024:.1f} MB)", "url": url})
        return options

    @staticmethod
    def uid_from_token(token: str) -> str:
        """Decodes the JWT payload to pull out the "uid" claim, without
        verifying the signature (we don't have PocketFM's key — this is
        just for display, not auth)."""
        try:
            raw = token.replace("Bearer ", "")
            payload_part = raw.split(".")[1]
            payload_part += "=" * (-len(payload_part) % 4)
            payload = json.loads(base64.b64decode(payload_part).decode("utf-8"))
            return payload.get("uid")
        except Exception:
            return None
