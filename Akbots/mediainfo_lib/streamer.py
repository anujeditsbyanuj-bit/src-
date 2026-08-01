# Akbots - Don't Remove Credit - @AkBots_Official
#
# MediaStreamer — ported from Mediainfo-Bot-master/core/streamer.py, wired
# into Akbots. Runs a tiny local-only aiohttp server that proxies a
# Telegram message's media as an HTTP resource with Range support, so
# ffprobe/mediainfo can seek (header at the start, moov atom often at the
# end) and read only the bytes it actually needs — the file is never
# downloaded to disk. Only reachable on 127.0.0.1; not exposed publicly.
#
# The upstream version had two bugs that made Range requests unsafe: (1)
# stream_media's offset is in whole 1MB chunks, so the first chunk it
# returns can start earlier than the requested byte and needs trimming;
# (2) it never stopped reading once past the requested end, so it kept
# streaming to EOF regardless of the Range asked for, breaking the
# Content-Length it had already sent. Both are fixed below.

import math
import logging

from aiohttp import web

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # pyrogram's stream_media chunk size (1MB)

# Set by MediaStreamer.start() once the server is actually listening, so
# Akbots/mediainfo.py can build the right URL without hardcoding the port.
_RUNNING_PORT = None


class MediaStreamer:
    def __init__(self, client):
        self.client = client
        self.app = web.Application()
        self.app.router.add_get("/stream/{chat_id}/{message_id}", self.stream_handler)
        self.runner = None

    async def start(self, host="127.0.0.1", port=8099):
        global _RUNNING_PORT
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host, port)
        await site.start()
        _RUNNING_PORT = port
        logger.info(f"MediaInfo local streamer started at http://{host}:{port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()

    async def stream_handler(self, request):
        chat_id = int(request.match_info["chat_id"])
        message_id = int(request.match_info["message_id"])

        try:
            message = await self.client.get_messages(chat_id, message_id)
            if not message or not (message.video or message.audio or message.document):
                return web.Response(status=404, text="Media not found")

            media = message.video or message.audio or message.document
            file_size = media.file_size
            file_name = media.file_name or "file"

            start = 0
            end = file_size - 1
            range_header = request.headers.get("Range")
            if range_header:
                h_range = range_header.replace("bytes=", "").split("-")
                start = int(h_range[0]) if h_range[0] else 0
                end = int(h_range[1]) if len(h_range) > 1 and h_range[1] else file_size - 1
            end = min(end, file_size - 1)

            response = web.StreamResponse(
                status=206 if range_header else 200,
                reason="Partial Content" if range_header else "OK",
                headers={
                    "Content-Type": media.mime_type or "application/octet-stream",
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(end - start + 1),
                    "Content-Disposition": f'inline; filename="{file_name}"',
                    "Accept-Ranges": "bytes",
                }
            )
            await response.prepare(request)

            chunk_offset = start // CHUNK_SIZE          # which 1MB chunk to start pyrogram at
            skip_in_first_chunk = start % CHUNK_SIZE     # bytes to trim off that first chunk
            bytes_remaining = end - start + 1
            first = True

            async for chunk in self.client.stream_media(message, offset=math.floor(chunk_offset)):
                if not chunk:
                    continue
                if first:
                    chunk = chunk[skip_in_first_chunk:]
                    first = False
                if len(chunk) > bytes_remaining:
                    chunk = chunk[:bytes_remaining]
                if chunk:
                    await response.write(chunk)
                    bytes_remaining -= len(chunk)
                if bytes_remaining <= 0:
                    break

            return response

        except ConnectionResetError:
            # Probe closed the connection early after reading the header it
            # needed — expected for partial-probe reads, not an error.
            return web.Response(status=200)
        except Exception as e:
            logger.error(f"MediaInfo streaming error: {e}")
            return web.Response(status=500, text=str(e))
