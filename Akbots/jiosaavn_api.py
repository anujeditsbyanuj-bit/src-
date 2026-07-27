# Akbots
# JioSaavn API wrapper — ported from the standalone jiosaavn-main bot's
# api/jiosaavn.py. Kept as a thin, self-contained class (only depends on
# aiohttp) so it can be reused from Akbots/jiosaavn.py the same way every
# other download plugin in this bot has its own small API/scrape helper.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import json
from typing import Any, Dict, List, Literal, Optional, Union

import aiohttp

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    "Referer": "https://www.jiosaavn.com/",
    "Origin": "https://www.jiosaavn.com",
}


class Jiosaavn:
    """Interact with JioSaavn's unofficial web API for searching and
    resolving download URLs for songs, albums, and playlists."""

    BASE_URL = "https://www.jiosaavn.com"
    API_URL = f"{BASE_URL}/api.php"

    async def _request_data(
        self, url: str, params: Dict[str, Any] = None
    ) -> Union[Dict[str, Any], List[Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url=url, params=params, headers=HEADERS) as response:
                    response.raise_for_status()
                    response_text = await response.text()
                    return json.loads(response_text)
        except aiohttp.ClientError as e:
            raise RuntimeError(f"Request to {url} failed: {e}")
        except ValueError as e:
            raise RuntimeError(f"Failed to decode JSON response from {url}: {e}")

    async def search(
        self,
        query: str,
        search_type: Literal["songs", "albums", "artists", "playlists"],
        page_no: Optional[int] = 1,
        page_size: Optional[int] = 10,
    ) -> Dict[str, Any]:
        search_type_call_map = {
            "songs": "search.getResults",
            "albums": "search.getAlbumResults",
            "artists": "search.getArtistResults",
            "playlists": "search.getPlaylistResults",
        }
        call = search_type_call_map.get(search_type)
        if not call:
            raise ValueError(f"Invalid search_type: {search_type}")

        params = {
            "p": page_no, "q": query, "__call": call, "api_version": 4,
            "n": page_size, "_format": "json", "_marker": 0, "ctx": "web6dot0",
        }
        return await self._request_data(self.API_URL, params=params)

    async def get_playlist_or_album(
        self,
        album_id: Optional[str] = None,
        playlist_id: Optional[str] = None,
        page_no: Optional[int] = 1,
        page_size: Optional[int] = 50,
    ) -> Optional[Dict[str, Any]]:
        if not album_id and not playlist_id:
            raise ValueError("Either `album_id` or `playlist_id` must be provided.")

        search_type = "album" if album_id else "playlist"
        token = album_id or playlist_id
        params = {
            "__call": "webapi.get", "token": token, "type": search_type,
            "p": page_no, "n": page_size, "includeMetaTags": 0,
            "ctx": "web6dot0", "api_version": 4, "_format": "json", "_marker": 0,
        }
        response = await self._request_data(self.API_URL, params=params)
        if not response:
            return None
        if search_type == "playlist":
            return response
        if not response.get("list"):
            return None
        return response

    async def get_song(self, song_id: str) -> Dict[str, Any]:
        params = {
            "__call": "webapi.get", "token": song_id, "type": "song",
            "includeMetaTags": 0, "ctx": "web6dot0", "api_version": 4,
            "_format": "json", "_marker": 0,
        }
        return await self._request_data(self.API_URL, params=params)

    async def get_download_url(self, song_id: str, bitrate: Literal[160, 320]) -> Optional[Dict[str, Any]]:
        """Resolves the short-lived authenticated download URL for a song.
        bitrate=320 silently falls back to whatever the account tier allows
        if 320kbps isn't actually available for that track."""
        song_response = await self.get_song(song_id=song_id)
        if not song_response or not song_response.get("songs"):
            return None
        encrypted_media_url = song_response["songs"][0].get("more_info", {}).get("encrypted_media_url")
        if not encrypted_media_url:
            return None
        params = {
            "__call": "song.generateAuthToken", "url": encrypted_media_url,
            "bitrate": bitrate, "api_version": 4, "_format": "json",
            "ctx": "wap6dot0", "_marker": 0,
        }
        return await self._request_data(url=self.API_URL, params=params)
