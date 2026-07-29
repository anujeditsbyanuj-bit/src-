# ========================================================
# Akbots
# Don't Remove Credit 🥺
# Telegram Channel @AkBots_Official
#
# Maintained & Updated by:
# ANUJ
# GitHub: https://github.com/anujeditinganuj-dotcom
# ========================================================

FROM python:3.12.9-slim-bullseye

# Prevent Python from creating .pyc files
ENV PYTHONDONTWRITEBYTECODE=1
# Ensure logs are shown instantly
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    mediainfo \
    aria2 \
    megatools \
    p7zip-full \
    unrar-free \
    default-jre-headless \
    wget \
    curl \
    git \
    gcc \
    python3-dev \
    build-essential \
    xvfb \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# RAR CLI (Akbots/archive.py's /zipformat rar) — unlike every other archive
# format here, .rar can only ever be *created* by RARLAB's own proprietary
# `rar` binary (p7zip/unrar-free above can only extract it, never create
# it), so it isn't available as a normal apt package. Fetched straight from
# rarlab.com's official Linux x64 build and dropped into /usr/local/bin
# instead. Pinned to a known-working version — if rarlab.com ever retires
# this specific file, bump RAR_VERSION to whatever's current on
# https://www.rarlab.com/download.htm (Linux x64 .tar.gz link) and rebuild;
# nothing else in the bot depends on the exact version. Never fails the
# build if the download doesn't go through (offline build environments,
# rarlab.com being briefly unreachable, etc) — /zipformat rar just reports
# "not installed" at runtime instead of the build breaking, same
# fail-soft approach as the rest of this Dockerfile's optional tooling.
ENV RAR_VERSION=720
RUN (wget -q -O /tmp/rar.tar.gz "https://www.rarlab.com/rar/rarlinux-x64-${RAR_VERSION}.tar.gz" \
    && tar -xzf /tmp/rar.tar.gz -C /tmp \
    && cp -v /tmp/rar/rar /tmp/rar/unrar /usr/local/bin/ \
    && rm -rf /tmp/rar.tar.gz /tmp/rar) || echo "[build] RAR CLI download failed -- /zipformat rar will be unavailable"

# yt-dlp PO-token provider (Akbots/ytdl.py's YouTube "web" client is the
# only one with the full 1080p/720p/480p/360p quality ladder, but it needs
# a valid PO token or YouTube silently caps/drops most of those formats).
# requirements.txt only pip-installs the *plugin* side (the Python glue
# that talks to a provider) — the actual token generator is a separate
# Node.js HTTP server that has to be built and running alongside the bot,
# which is what this block sets up. Pinned to a known-working release tag
# instead of the moving default branch.
ENV BGUTIL_POT_VERSION=1.3.1
RUN git clone --single-branch --branch ${BGUTIL_POT_VERSION} --depth 1 \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil-pot \
    && cd /opt/bgutil-pot/server \
    && npm ci \
    && npx tsc

# JDownloader (/jd) — baked in at build time so the bot doesn't need to
# fetch it over the network on every restart. This is just the small
# self-updating installer; it downloads the rest of itself once on first
# boot (Akbots/jdownloader_core.py handles that — can take a few minutes
# the very first time, cached after). No-ops harmlessly if JD_EMAIL/JD_PASS
# aren't set in config — /jd just stays disabled.
RUN mkdir -p /JDownloader && \
    wget -q -O /JDownloader/JDownloader.jar http://installer.jdownloader.org/JDownloader.jar || true

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright's pip package doesn't ship the actual browser - fetch chromium
# (+ its OS-level libraries) at build time so Akbots/headless.py's
# JS-rendering fallback and Akbots/playwright_bypass.py's headless
# shortlink-bypass tier both work out of the box, no manual step needed.
RUN playwright install --with-deps chromium

# Same idea for camoufox (Akbots/cf_lib/turnstile_solver.py's stealth
# Firefox backend) — fetch its browser binary at build time instead of on
# first use.
RUN python3 -m camoufox fetch

# Copy project files
COPY . .

# Starts the bgutil PO-token HTTP server in the background (default port
# 4416 — same default the yt-dlp plugin looks for, no extra config on the
# Python side needed), waits briefly, and logs whether it's actually
# reachable — check the container logs after boot for an explicit
# "[bgutil-pot] OK" or "[bgutil-pot] WARNING" line instead of guessing.
# If the token server fails to start for any reason, the bot still runs —
# it just falls back to tv_embedded/android's lower-res formats for
# YouTube, same as before this was added.
RUN chmod +x /app/entrypoint.sh

# Start the bot (+ its PO-token server sidecar)
# Flask keep_alive server handles port binding
CMD ["/app/entrypoint.sh"]

# ========================================================
# Akbots
# Don't Remove Credit
# Telegram Channel @AkBots_Official
#
# Updated & Managed by:
# ANUJ | https://github.com/anujeditinganuj-dotcom
# ========================================================
