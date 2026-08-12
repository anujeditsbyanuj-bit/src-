# PeliApi

> **Wired into Ak_Devil_Bot** as `Akbots/akashi_dl.py` (`/peliapi`
> command). Vendored as-is from AKASHI-VERSE's `peliapi/` (Node/Express +
> Puppeteer). Same sidecar-service reasoning as `services/anime1v-api`'s
> README — deploy separately, point the bot at it with `PELIAPI_URL`.

Movie/TV/anime search + episode-server + stream-resolve API aggregating
PelisPlus, Cuevana, RepelisHD, PoseidonHD, SeriesFlixHD, AnimeYT and
Unlimplay (TMDB-id based).

```bash
cp .env.example .env    # if present — otherwise see src/server.js / auth.js
npm install
npm start                # http://localhost:3000
```

## Deploy to Railway

Same steps as `services/anime1v-api/README.md`, with **Root Directory**
set to `services/peliapi`. Then set `PELIAPI_URL=https://your-app.up.railway.app`
(and `PELIAPI_API_KEY` if you configured `API_KEYS`) in the main bot's env.

## Endpoints used by Akbots/akashi_dl.py

- `GET /api/v1/content/search?s=<query>` — search across PelisPlus/RepelisHD/PoseidonHD/SeriesFlixHD
- `GET /api/v1/content/info/<slug>?type=movie|series|anime&provider=...` — details + play servers
- `GET /api/v1/content/servers?slug=<slug>&season=&episode=&provider=` — episode server list (series)
- `GET /api/v1/content/resolve?url=<embed_url>` — embed URL → direct stream

See `src/routes/content.routes.js` / `tv.routes.js` for the full route
list (catalog, genres, download/batch-download job tracking) — only the
four above are wired into Telegram.
