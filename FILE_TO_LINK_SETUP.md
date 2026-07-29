# File-to-Link (Stream + Download) — Setup

This wires the FILE-TO-LINK-BOT project's streaming/download engine directly
into Akbots — same bot, same token, no second process.

## What you get
- Send any document/video/audio to the bot in DM → get back a **Stream**
  link (plays in-browser with range-request seeking, plus MX Player / VLC /
  PlayIt buttons) and a **Download** link.
- `/link` as a reply to a file works the same way.
- `/linkbatch <first_msg_link> <last_msg_link>` — generate Stream +
  Download links for a whole range of messages in one shot (bot needs to
  already be a member/admin of that source chat). Capped at
  `STREAM_BATCH_MAX_FREE` (default 30) for regular users, or
  `STREAM_BATCH_MAX_ADMIN` (default 200) for admins.
- `/maintenance on` / `/maintenance off` (admin-only) — a global switch
  that makes the *entire bot* reply "under maintenance" to every
  non-admin message/callback across all plugins, instantly, with no
  restart needed.
- Link expiry (`STREAM_LINK_EXPIRY`) is now **persisted in MongoDB**, so
  it survives bot restarts (previously in-memory only).
- `/set_expiry` (admin-only) — change how long newly generated links
  stay valid *without* touching env vars or restarting. Accepts things
  like `/set_expiry 1h`, `/set_expiry 30m`, `/set_expiry 1h30m`,
  `/set_expiry 3600`, `/set_expiry 0` (never expires), or
  `/set_expiry reset` to go back to the `STREAM_LINK_EXPIRY` default.
  Run with no argument to see the current value.
- `/linkbatch ... --protect` — mark the forwarded copies that land in
  `STREAM_BIN_CHANNEL` as protected content, so Telegram blocks anyone
  forwarding/saving them out of that channel. Only affects the internal
  copies, not the HTTP Stream/Download links themselves.
- **Rate limiting / abuse protection** (ported from TGFiletoLinkBot) —
  two independent, in-memory sliding-window limits, admins always
  bypass both:
  - Per-user limit on generating links via `/link`, `/linkbatch`, or the
    auto-handler: `STREAM_RATE_LIMIT_MAX_LINKS` per
    `STREAM_RATE_LIMIT_PERIOD_SECONDS` (default 20 per 60s). Toggle with
    `STREAM_RATE_LIMIT_ENABLED`.
  - Per-IP limit on the actual HTTP stream/download requests:
    `STREAM_HTTP_RATE_LIMIT_MAX` per `STREAM_HTTP_RATE_LIMIT_PERIOD_SECONDS`
    (default 90 per 60s, generous enough for normal seek/buffer range
    requests). Toggle with `STREAM_HTTP_RATE_LIMIT_ENABLED`.
  - Banned users (`/ban`) are now also silently blocked from generating
    new file-to-link links.
- **Reverse proxy support** — if your stream server sits behind nginx /
  Cloudflare / your platform's edge, set `STREAM_TRUST_PROXY=true` so the
  per-IP rate limiter reads the real client IP from `X-Forwarded-For`
  instead of the proxy's own IP. Leave this `false` (default) unless you
  actually control that proxy, since a client could otherwise spoof the
  header to dodge the limit.
- Optional **multi-client load balancing**: add extra bot tokens via
  `STREAM_EXTRA_TOKENS` and streaming/download traffic automatically
  spreads across whichever client currently has the least load.
- Admins keep using `filestore.py`'s existing `/genlink` / `/batch` flow
  untouched — this is purely additive.

## Required setup
1. Create a **private Telegram channel** (or reuse an existing log channel)
   and add this bot as **admin** there. This is where files get silently
   forwarded so the streamer can pull bytes from them.
2. Set `STREAM_BIN_CHANNEL` to that channel's id (env var or in
   `config.py`). Defaults to the same channel as `LOG_CHANNEL` if unset.
3. Set `STREAM_FQDN` to your public domain (e.g. your Render/Railway URL,
   no scheme) so generated links are actually reachable from outside. If
   left blank, links default to `http://localhost:<STREAM_PORT>/` — fine
   to smoke-test locally, not shareable.
4. Make sure your host exposes `STREAM_PORT` (default `8070`) — on
   Render/Railway you'd normally map this to the platform's `$PORT` via
   `STREAM_PORT=$PORT`, or put it behind a reverse proxy with SSL.

## Optional: multi-client load balancing
Set `STREAM_EXTRA_TOKENS` to a comma-separated list of extra bot tokens
(from @BotFather, one per additional bot). **Each of those bots must also
be added as admin/member of `STREAM_BIN_CHANNEL`**, same as the main bot —
otherwise that client can't fetch the forwarded files. With this set,
every stream/download request is routed to whichever client (main bot or
one of the extras) currently has the fewest active transfers.

## Optional: maintenance mode
`/maintenance on` (admin-only) makes every plugin in the bot stop
responding to non-admins immediately — useful during deploys/migrations.
`/maintenance off` restores normal operation. Admins are always exempt, so
you can keep testing/administering while it's on. State is stored in
MongoDB, so it also survives restarts.

## Files added / changed
- `Akbots/filetolink/` — the streaming engine (byte-range fetcher, aiohttp
  routes, watch/download HTML pages, multi-client picker), ported from
  FILE-TO-LINK-BOT.
- `Akbots/filetolink/rate_limit.py` — per-user (link generation) and
  per-IP (HTTP requests) sliding-window rate limiting, ported from
  TGFiletoLinkBot.
- `Akbots/filetolink/link_builder.py` — shared "forward + build links +
  persist to Mongo" helper used by both `/link` and `/linkbatch`.
- `Akbots/filetolink.py` — the bot-facing plugin (`/link` + auto-handler).
- `Akbots/filetolink_batch.py` — `/linkbatch` for whole message ranges.
- `Akbots/maintenance.py` — `/maintenance on|off` + the global gate.
- `Akbots/set_expiry.py` — `/set_expiry` runtime override for link expiry.
- `database/db.py` — new methods: `get_maintenance_mode` /
  `set_maintenance_mode`, `save_stream_link` / `get_stream_link_timestamp`
  / `ensure_stream_link_indexes`, `get_link_expiry_seconds` /
  `set_link_expiry_seconds` / `clear_link_expiry_override`.
- `config.py` — new block at the bottom: `STREAM_BIN_CHANNEL`,
  `STREAM_PORT`, `STREAM_URL`, `STREAM_LINK_EXPIRY`,
  `STREAM_BATCH_MAX_FREE`, `STREAM_BATCH_MAX_ADMIN`, `STREAM_EXTRA_TOKENS`.
- `bot.py` — starts the aiohttp server (and any extra streaming clients)
  on boot, same pattern as the existing MediaInfo streamer; also ensures
  the new `stream_links` Mongo index on startup.
