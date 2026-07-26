# TeraBox Account-Transfer Bridge Setup

`Akbots/terabox.py` already works with **zero setup** via 3 cookie-less
tiers (direct scrape → guest-mode → teradownloader.com). This adds an
**optional 4th tier**, ported from TeraBridge-api: it logs into a real
TeraBox account, transfers the shared file into that account's own
storage, then resolves a download link from there. Different failure
mode than the 3 existing tiers — more reliable when configured, but
needs an account.

If you skip this whole setup, nothing breaks — the existing 3-tier chain
keeps working exactly as before.

---

## 1. Get an NDUS cookie (required)

1. Log into [terabox.com](https://www.terabox.com) in a normal browser
   — **a premium/VIP account is strongly recommended**. Free accounts
   are bandwidth-throttled and hit small daily transfer/storage caps, so
   this tier would fail almost as often as it helps on one.
2. Open DevTools (F12) → **Application** (Chrome) or **Storage**
   (Firefox) tab → **Cookies** → `https://www.terabox.com`.
3. Copy every cookie as one string in `name=value; name2=value2; ...`
   format (must include `ndus=...`). Easiest way: open Network tab,
   reload the page, click any request to terabox.com, and copy the full
   `Cookie:` request header value.
4. Set it as an environment variable:
   ```
   TERABOX_NDUS_COOKIE=ndus=YQ7...; PANWEB=1; ...rest of your cookies...
   ```
   That's the only credential needed — `bdstoken`, `jsToken`, and
   `logid` are all auto-resolved from this cookie at runtime.

Restart the bot. `/terabox <link>` will now try this account first,
falling back to the existing 3 tiers if it fails.

---

## 2. Multiple accounts + rotation (optional)

Only useful if you have more than one account and want to spread load
or survive one account getting rate-limited/banned.

1. Create a free [Upstash](https://upstash.com) account → **Create
   Database** (Redis, any region close to your server) → copy the
   **REST URL** and **REST Token** from the dashboard.
2. Set:
   ```
   UPSTASH_REDIS_REST_URL=https://xxxx.upstash.io
   UPSTASH_REDIS_REST_TOKEN=xxxxxxxxxxxx
   TERABOX_ACCOUNTS=[{"id":"acc1","cookie":"ndus=...; PANWEB=1"},{"id":"acc2","cookie":"ndus=...; PANWEB=1"}]
   ```
   (or the comma-separated form: `TERABOX_ACCOUNTS=acc1:ndus=...,acc2:ndus=...`
   — avoid this form if any cookie itself contains a comma).
3. `TERABOX_NDUS_COOKIE` can be left set too — it's auto-added as an
   `"default"` account if `TERABOX_ACCOUNTS` doesn't already define one.

Without Redis configured, only a single account is ever used per
process (no rotation, no persisted health state across restarts) — fine
for one account, not for real load-spreading across several.

Accounts that fail (expired/invalid cookie) are automatically marked
`unhealthy` in the pool and skipped on future requests. To bring one
back, fix its cookie value and restart the bot (or clear its Redis
entry).

---

## 2b. Ban-avoidance (automatic once you have 2+ accounts)

Just rotating accounts round-robin doesn't stop a single account from
getting flagged if it's the one that happens to be picked repeatedly in
a short burst. On top of round-robin, the pool also enforces:

- **Per-account hourly cap** (`TERABOX_MAX_REQUESTS_PER_HOUR`, default
  `20`) — an account stops getting picked once it hits this many
  requests in a rolling hour, until the window rolls forward.
- **Minimum gap between uses of the same account**
  (`TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS`, default `8`) — even the
  least-recently-used account won't be reused faster than this.
- **Automatic cooldown + retry on a different account** — if Terabox
  responds with anything that looks like a ban/rate-limit signal
  (forbidden, frequency-limited, session kicked) instead of a normal
  error, that account is benched for `TERABOX_ACCOUNT_COOLDOWN_SECONDS`
  (default `1800` = 30 min) and the *same request* automatically retries
  with the next healthy account — up to `TERABOX_MAX_ACCOUNT_RETRIES`
  (default `3`) accounts before giving up and falling through to
  terabox.py's other tiers. Cooldown self-expires; you don't need to do
  anything to bring the account back.
- A genuinely dead/expired cookie still gets marked permanently
  `unhealthy` (not just cooled down) — that one does need you to fix
  the cookie and restart.

Tune these via env vars if you have a lot of accounts and want to push
throughput, or very few and want to be extra conservative:

```
TERABOX_MAX_REQUESTS_PER_HOUR=20
TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS=8
TERABOX_ACCOUNT_COOLDOWN_SECONDS=1800
TERABOX_MAX_ACCOUNT_RETRIES=3
```

More accounts in the pool directly means more effective throughput
before any individual one gets close to its hourly cap — this is the
main reason to add more than one account beyond just redundancy.

---

## 3. Storage cleanup (already handled automatically)

Every resolve transfers the shared file into `/cloudvids` inside the
account's own storage. To stop that folder from filling up over time,
the bridge automatically deletes the **oldest** files once the folder
passes a configurable limit:

```
TERABOX_MAX_STORED_FILES=50   # default; lower this for free/small-storage accounts
```

This runs on every request, before the new transfer — no separate cron
job needed.

---

## Environment variables reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `TERABOX_NDUS_COOKIE` | For this tier at all | *(empty = tier skipped)* | Single account's full cookie string |
| `TERABOX_ACCOUNTS` | Only for multi-account | *(empty)* | JSON array or `id:cookie,...` list of extra accounts |
| `UPSTASH_REDIS_REST_URL` | Only for real rotation | *(empty = in-memory pool)* | Upstash Redis REST URL |
| `UPSTASH_REDIS_REST_TOKEN` | Only for real rotation | *(empty)* | Upstash Redis REST token |
| `TERABOX_MAX_STORED_FILES` | No | `50` | Files kept in `/cloudvids` before oldest are pruned |
| `TERABOX_MAX_REQUESTS_PER_HOUR` | No | `20` | Per-account cap before it's skipped for the rest of the rolling hour |
| `TERABOX_MIN_ACCOUNT_INTERVAL_SECONDS` | No | `8` | Minimum gap between two uses of the same account |
| `TERABOX_ACCOUNT_COOLDOWN_SECONDS` | No | `1800` | How long an account benches itself after a ban-like signal |
| `TERABOX_MAX_ACCOUNT_RETRIES` | No | `3` | Max different accounts tried per request before giving up |

---

## Files added/changed

- **New:** `Akbots/terabridge_account.py` — the ported account-transfer
  logic (session/token handling, transfer, dlink resolve, pruning,
  Redis-or-memory account pool).
- **Changed:** `Akbots/terabox.py` — imports the new module and tries it
  as tier 0 in `_extract_terabox_files()` before the existing 3 tiers,
  only when `TERABOX_NDUS_COOKIE`/`TERABOX_ACCOUNTS` is set.
- **Changed:** `config.py` — new env-var-backed settings listed above.
- **Changed:** `requirements.txt` — added `upstash-redis` (optional at
  runtime; the module falls back to in-memory mode if Redis env vars
  aren't set, but the package must be installed either way).
