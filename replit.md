# Save Restricted Content Bot

A Telegram bot built with Python + Pyrogram that lets users download text, media, and files from restricted Telegram channels. Supports batch downloads, premium user management, and a wide range of optional media integrations (YouTube, Instagram, Google Drive, Mega.nz, etc.).

## How to run

The bot is configured as the **Start application** workflow:

```
python bot.py
```

Start it from the Replit workflow panel or run `python bot.py` directly in the shell.

## Required secrets

All credentials are stored as Replit Secrets:

| Secret | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token (from @BotFather) |
| `API_ID` | Telegram API ID (from my.telegram.org) |
| `API_HASH` | Telegram API hash (from my.telegram.org) |
| `ADMINS` | Comma-separated Telegram user IDs with admin access |
| `DB_URI` | MongoDB connection URI |
| `LOG_CHANNEL` | Telegram channel ID for bot logs (e.g. `-1001234567890`) |

## Optional secrets / env vars

| Key | Description |
|---|---|
| `DB_NAME` | MongoDB database name (default: `SaveRestricted2`) |
| `SESSION_SECRET` | Session string for user-account login feature |
| `KUKU_JWT_TOKEN` | KukuFM account JWT bearer token, for `/kuku` downloads |
| `KUKU_REFRESH_TOKEN` | Paired refresh token, used to silently renew the JWT |

## System dependencies installed

- `ffmpeg` — video/audio processing, thumbnails
- `aria2` — torrent/magnet/FTP downloads
- `megatools` — Mega.nz downloads

## Stack

- Python 3.12
- [Kurigram](https://github.com/KurimuzonAkuma/pyrogram) (Pyrogram fork)
- MongoDB via Motor (async)
- Flask + Gunicorn (keep-alive server)
- yt-dlp, gallery-dl, playwright (media downloaders)

## User preferences

- Keep existing project structure — do not restructure or migrate.
