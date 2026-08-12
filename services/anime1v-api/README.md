# Anime1v API

> **Wired into Ak_Devil_Bot** as `Akbots/akashi_dl.py` (`/anime1v` command).
> Vendored as-is from AKASHI-VERSE's `anime1v-api/` (Node/Express, Puppeteer
> for the sites that need a real browser). Deploy this folder as its own
> service (Railway auto-detects the Dockerfile — New Project → Deploy from
> GitHub → set root directory to `services/anime1v-api`), then set
> `ANIME1V_API_URL` (and `ANIME1V_API_KEY` if you set `API_KEYS` below) in
> the main bot's env to the deployed URL.
>
> This is a **sidecar HTTP service**, not something imported into the
> Python bot process — it's ~7.5k lines of Puppeteer-driven Node scraping
> across 7 sites (AnimeYT, AnimeFLV, Hentaila, JKAnime, Monoschinos,
> TioAnime, AnimeAV1); porting that to Python line-by-line would be a much
> larger, much riskier undertaking than running it as-is and calling its
> REST API, the same pattern this repo already uses for `services/hotstar-api`.

Anime search/info/episode-link/stream-resolve API. Copy `.env.example` to
`.env` and fill in as needed before running locally:

```bash
cp .env.example .env
npm install
npm start          # http://localhost:3000
```

## Deploy to Railway

1. Push this repo (or just this folder) to GitHub.
2. Railway → New Project → Deploy from GitHub → set **Root Directory** to
   `services/anime1v-api`.
3. Set env vars from `.env.example` (at minimum `API_KEYS` if you don't
   want `DISABLE_AUTH=true`).
4. Once deployed, set `ANIME1V_API_URL=https://your-app.up.railway.app`
   and `ANIME1V_API_KEY=<one of the API_KEYS you configured>` in the
   **main bot's** environment.

## Endpoints used by Akbots/akashi_dl.py

- `GET /api/v1/anime/search?q=<query>` — search across the enabled sites
- `GET /api/v1/anime/info?url=<result_url>` — episode list for a title
- `GET /api/v1/anime/episode?url=<episode_url>` — playback server list
- `GET /api/v1/anime/resolve?url=<embed_url>` — embed URL → direct stream

See `src/routes/anime.routes.js` for the full route list (catalog,
download/batch-download job tracking, etc.) — the bot only drives the four
above; the rest exists upstream but isn't wired into Telegram.
