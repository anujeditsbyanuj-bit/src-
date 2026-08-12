# Hotstar Stream API

> **Wired into tera_api** as `Akbots/hotstar.py` (`/hotstar` command). Deploy
> this folder as its own service (Railway auto-detects the Dockerfile —
> New Project → Deploy from GitHub → set root directory to
> `services/hotstar-api`), then set `HOTSTAR_API_URL` in the main bot's env
> to the deployed URL (e.g. `https://your-app.up.railway.app`). Optionally
> set `HOTSTAR_USER_TOKEN` too so users don't have to paste a fresh
> `x-hs-usertoken` every time (it still expires ~24h, see below).

FastAPI service that resolves Hotstar content IDs to stream URLs and downloads HLS streams.

## Deploy to Railway

1. Push this folder to a GitHub repo
2. New project on railway.app → Deploy from GitHub
3. It auto-detects the Dockerfile

## API Endpoints

### POST /api/resolve
Calls Hotstar's internal widget 244 API to get the raw M3U8 stream URL.

**Body (cookies-based, no manual token):**
```json
{
  "content_id": "1271635183",
  "cookies": {"userUP": "<paste cookies from browser DevTools>"}
}
```
The service auto-detects the JWT token from the cookie jar (checks common
cookie names first, then scans every value for a JWT-shaped string), so you
don't need to hunt for `x-hs-usertoken` separately anymore.

**Body (explicit token, still supported):**
```json
{
  "content_id": "1271635183",
  "user_token": "<x-hs-usertoken from browser DevTools>",
  "cookies": {}
}
```

**How to get the token:**
1. Open hotstar.com → DevTools → Network tab
2. Filter by `XHR` or search for `widgets/244`
3. Click any request → Headers → copy `x-hs-usertoken` value

### POST /api/download
Queue an HLS download job.

```json
{
  "m3u8_url": "https://hssportsprepack.akamaized.net/.../index.m3u8",
  "output_name": "ipl_match",
  "workers": 6
}
```

### GET /api/status/{job_id}
Poll job progress.

### GET /api/file/{job_id}
Download finished `.ts` file when status is `done`.

### GET /api/jobs
List all jobs.

### DELETE /api/jobs/{job_id}
Delete a job + its file.

## Notes

- Token expires after ~24h — grab a fresh one from DevTools each session
- Content IDs are in Hotstar URLs: `/in/sports/cricket/live/**1271635183**`
- Output is `.ts` — convert with: `ffmpeg -i output.ts -c copy output.mp4`
- Railway ephemeral disk — download files before restarting the service
