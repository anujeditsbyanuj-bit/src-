# filmyfly-resolver (Cloudflare Worker)

Resolves a filmyfly.luxe movie page into direct download links (bypasses the
linkmake.in → new1.filesdl.in redirect chain), grouped by quality/size.

## Deploy

```
cd workers/filmyfly-resolver
npx wrangler login      # first time only
npx wrangler deploy
```

Deploy prints your worker URL, e.g. `https://filmyfly-resolver.<account>.workers.dev`.
Set that as `FILMYFLY_WORKER_URL` in the bot's `.env` / Render-Railway config
(see `config.py`) — the `Akbots/filmyfly.py` plugin calls it.

## API

`GET /?url=<filmyfly movie page URL>` →

```json
{
  "movies": [
    {
      "title": "...",
      "releaseYear": "...",
      "quality": "1080p",
      "downloadLinks": [
        { "groupTitle": "720p", "links": [{ "size": "1.2GB", "url": "https://..." }] }
      ]
    }
  ]
}
```

`GET /debug?url=<any URL>` — returns raw HTML for a target URL (useful when
filmyfly/linkmake change their page layout and the regex parsers need
updating).
