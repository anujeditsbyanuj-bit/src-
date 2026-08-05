# hls-proxy (Cloudflare Worker)

Ported from the **meowtv** project (`workers/hls-worker.js`). Proxies HLS
playlists/segments and generic HTTP requests so playback links work around
CORS, missing headers, and broken `in` tokens on freecdn/netmirror-style
CDNs. Also converts SRT subtitles to WebVTT on the fly.

## Deploy

```
cd workers/hls-proxy
npx wrangler login      # first time only
npx wrangler deploy
```

Deploy prints your worker URL, e.g. `https://hls-proxy.<account>.workers.dev`.
Set that as `HLS_WORKER_URL` in the bot's `.env` / Render-Railway config
(see `config.py`) — `Akbots/hls_proxy.py` builds proxied links from it, the
same way `Akbots/filmyfly.py` uses `FILMYFLY_WORKER_URL`.

## API

`GET /api/hls?url=<m3u8 or segment URL>&referer=...&cookie=...&ua=...&kind=playlist|seg&decrypt=...&proxy_segments=false`
— fetches the upstream URL. If it looks like an M3U8 playlist, rewrites every
line/URI attribute to route back through this worker; otherwise streams the
segment/binary through, fixing content-type quirks and range requests.

`GET /api/proxy?url=<any URL>&referer=...&cookie=...&ua=...` — plain
pass-through proxy that forwards auth-relevant headers and exposes
`X-Proxied-Set-Cookie`.

Both endpoints support `OPTIONS` (CORS preflight) and set
`Access-Control-Allow-Origin: *`.
