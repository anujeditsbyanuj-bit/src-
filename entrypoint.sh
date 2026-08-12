#!/bin/sh
# Starts a virtual X display for Akbots/cf_lib/turnstile_solver.py's
# camoufox backend — real Cloudflare deployments fingerprint headless
# browsers and refuse to mount the Turnstile widget, so that solver always
# runs headed, which needs a display even with no physical one attached.
# Harmless no-op cost if that fallback never ends up used.
if [ -z "$DISPLAY" ]; then
    Xvfb :99 -screen 0 1280x900x24 -nolisten tcp >/dev/null 2>&1 &
    export DISPLAY=:99
    sleep 0.5
fi

# Starts the bgutil PO-token HTTP server in the background, waits a moment,
# then prints whether it's actually reachable before starting the bot —
# so "is it working" is answered by the container logs on every boot
# instead of staying a silent guess.
node /opt/bgutil-pot/server/build/main.js > /tmp/bgutil-pot.log 2>&1 &
BGUTIL_PID=$!

# Poll for up to ~15s instead of a single fixed 3s sleep — on slower/cold
# instances (e.g. Render free tier) the Node server can take longer than
# 3s to bind its port, which was causing false "WARNING" logs even though
# the server came up fine a moment later.
BGUTIL_UP=0
for i in $(seq 1 15); do
    if wget -q -O- http://127.0.0.1:4416/ping >/dev/null 2>&1; then
        BGUTIL_UP=1
        break
    fi
    sleep 1
done

if [ "$BGUTIL_UP" = "1" ] && kill -0 "$BGUTIL_PID" 2>/dev/null; then
    echo "[bgutil-pot] OK — PO token server is up on :4416 (YouTube 'web' client should get the full quality ladder)"
else
    echo "[bgutil-pot] WARNING — PO token server did not come up after 15s. Last log lines:"
    tail -n 20 /tmp/bgutil-pot.log 2>/dev/null
    echo "[bgutil-pot] Bot will still run — YouTube just falls back to tv_embedded/android's lower-res formats."
fi

# ------------------------------------------------------------------
# anime1v-api + peliapi (Akbots/akashi_dl.py's /anime1v and /pelisplus
# sidecars) — same self-contained pattern as bgutil-pot above: started
# here on localhost-only ports, no separate Railway deploy needed.
# config.py defaults ANIME1V_API_URL/PELIAPI_URL to these same ports,
# so nothing else has to be configured. Set ANIME1V_API_URL/PELIAPI_URL
# env vars to point at separately-hosted instances instead if you ever
# want that.
# ------------------------------------------------------------------
export DISABLE_AUTH=true
export DISABLE_RATE_LIMIT=true

(cd /app/services/anime1v-api && PORT=3000 exec node src/server.js) > /tmp/anime1v-api.log 2>&1 &
ANIME1V_PID=$!

(cd /app/services/peliapi && PORT=5555 exec node src/server.js) > /tmp/peliapi.log 2>&1 &
PELIAPI_PID=$!

ANIME1V_UP=0
for i in $(seq 1 20); do
    if wget -q -O- http://127.0.0.1:3000/health >/dev/null 2>&1; then
        ANIME1V_UP=1
        break
    fi
    sleep 1
done
if [ "$ANIME1V_UP" = "1" ] && kill -0 "$ANIME1V_PID" 2>/dev/null; then
    echo "[anime1v-api] OK — up on :3000 (/anime1v ready)"
else
    echo "[anime1v-api] WARNING — did not come up after 20s. Last log lines:"
    tail -n 20 /tmp/anime1v-api.log 2>/dev/null
    echo "[anime1v-api] /anime1v will report 'not configured' until this is fixed."
fi

PELIAPI_UP=0
for i in $(seq 1 20); do
    if wget -q -O- http://127.0.0.1:5555/health >/dev/null 2>&1; then
        PELIAPI_UP=1
        break
    fi
    sleep 1
done
if [ "$PELIAPI_UP" = "1" ] && kill -0 "$PELIAPI_PID" 2>/dev/null; then
    echo "[peliapi] OK — up on :5555 (/pelisplus ready)"
else
    echo "[peliapi] WARNING — did not come up after 20s. Last log lines:"
    tail -n 20 /tmp/peliapi.log 2>/dev/null
    echo "[peliapi] /pelisplus will report 'not configured' until this is fixed."
fi

exec python3 bot.py
