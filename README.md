# 🚀 AkBots — Save Restricted Content Bot (Advanced)

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-blue?logo=python&style=for-the-badge">
  <img src="https://img.shields.io/badge/Library-Kurigram%20(Pyrogram)-yellow?logo=telegram&style=for-the-badge">
  <img src="https://img.shields.io/badge/Database-MongoDB-green?logo=mongodb&style=for-the-badge">
  <img src="https://img.shields.io/badge/Status-Stable-success?style=for-the-badge">
</p>

<p align="center">
<b>An all-in-one Telegram content bot — restricted-content saver, 25+ site downloader, auto-forwarder with clone-bot management, and a monetization system, built on Kurigram + MongoDB.</b>
</p>

---

## 🔗 Quick Links

<p align="center">
  <a href="#-features"><img src="https://img.shields.io/badge/Features-View-blue?style=for-the-badge"></a>
  <a href="#-deployment"><img src="https://img.shields.io/badge/Deployment-Setup-green?style=for-the-badge"></a>
  <a href="#-commands"><img src="https://img.shields.io/badge/Commands-List-orange?style=for-the-badge"></a>
  <a href="#-support"><img src="https://img.shields.io/badge/Support-Telegram-blue?style=for-the-badge&logo=telegram"></a>
</p>

---

# 🚀 Features

<details open>
<summary><b>📦 Core — Restricted Content Saver</b></summary>

- **Save Restricted Content** — pull text, media, and files out of private/restricted channels via `/login` (user session).
- **Batch Mode** — bulk-download a full range of messages, with **infinite pagination** for very large batches.
- **Smart Auto-Detection** — send a plain song/video name in DM (no `/search` needed) and the bot auto-detects it as a search query, filtering out greetings, emojis-only text, and short noise.
- **Multi-Bot / Titanium Clone Mode** — spin up additional cloned bots under the same backend using Telegram Bot API 9.6 "Managed Bots," each with its own token, buttons, and channel bindings (`/setbot`, `/addbot`, `/delbot`, `/rembot`, `/mode`).

</details>

<details>
<summary><b>⬇️ 25+ Site & Host Downloaders</b></summary>

| Category | Sources |
|---|---|
| **Cloud / File Hosts** | Terabox, MediaFire, Mega.nz, Google Drive (OAuth, incl. private files/folders), GoFile, Pixeldrain, Catbox, GDFlix, HubCloud, Filepress |
| **Video / Streaming** | YouTube (`/yt`, `/yta` audio, `/ytraw`), Instagram, Facebook, VK, Dailymotion, MX Player, Zee5/Voot, Streamtape, Fembed-family mirrors |
| **Music** | Spotify, JioSaavn (with ID3/cover-art tagging via mutagen), KukuTV/KukuFM audio dramas (`/kuku`) |
| **Torrents & Direct Links** | Magnet/.torrent (`/torrent`), FTP, resumable direct-HTTP URLs (`/url`), m3u8/HLS playlists (`/m3u8`) |
| **Premium Hosters** | JDownloader integration (`/jd`) — covers hundreds of one-click hosters, click'n'load (.dlc) containers, and generic crawled links yt-dlp can't handle |
| **Fallback Engine** | Headless-browser (Playwright) discovery for JS-rendered players, plus `gallery-dl` for gallery-style sites |
| **Quality Selection** | Before downloading, choose resolution/bitrate (1080p/720p/480p, MP3 kbps, etc.) across YouTube, Instagram, Facebook, TikTok, Pinterest, and all yt-dlp-supported sites |
| **Cookie Management** | Upload/manage per-platform cookies for YouTube, Instagram, Facebook, VK; `/cookiecheck` diagnoses YouTube "sign in to confirm you're not a bot" / PO-token issues |

</details>

<details>
<summary><b>🔁 Auto-Forwarding & FTM Manager</b></summary>

- **Forward Engine** — forward from source(s) to target(s) with live status/progress (`/fwd`, `/fwdstatus`, `/fwdcancel`, `/fwdresume`).
- **Multiple Forward Modes** — Delta, Alpha, Gamma, Theta, Watermark, Pi, Blast, Replacer, Remover modes, paginated across an in-chat FTM Manager menu (`/ftmmanager`).
- **Filters & Cleanup** — remove usernames/links, word replace/delete, custom caption/button overlay on forwarded posts (`/fwd_caption`, `/fwd_button`, `/fwd_filter`).
- **Source/Target/Route management** — multi-source and multi-target routing (`/addsource`, `/addtarget`, `/addroute`, etc.), with MongoDB-backed concurrency locking so parallel forward jobs don't collide.
- **RSS Auto-Poster** — watch RSS feeds and auto-post new items to a channel (`/rss_add`, `/rss_list`, `/rss_remove`).
- **TMDB Auto-Post** — scheduled daily movie-release posts to a channel, powered by TMDB (`/autotest`, `AUTOPOST_CHANNEL`).

</details>

<details>
<summary><b>⚙️ File Customization & Processing</b></summary>

- Custom captions, thumbnails, and thumbnail modes (`/set_caption`, `/set_thumb`, `/thumb_mode`)
- Auto-rename with prefix/suffix and sequential numbering (`/autorename`, `/set_prefix`, `/set_suffix`, `/numbering`)
- Metadata editing (title/author/etc.) and watermark overlay with position control (`/set_metadata`, `/set_watermark`, `/watermark_position`)
- Format conversion — to MP4/video/document, audio extraction (`/tomp4`, `/tovideo`, `/todocument`, `/extract_audio`)
- Auto/manual screenshots and sample-video generation (`/screenshots`, `/autoscreenshots`, `/sample`, `/autosample`)
- Archive tools — password-protected zip/7z creation (AES-256, AES-128, or legacy ZipCrypto for zip; AES-256 for 7z, via `/zipencryption`), folder structure inside the archive via `/zipfolder` or per-file captions, `/zipfiles` to review the queue, and multi-format extraction (rar/7z/tar/gz via system `7z`) (`/zip`, `/unzip`, `/zippass`)
- Duplicate-file remover across a channel (`/unequify`)
- Spoiler tagging for sensitive media (`/spoiler`)

</details>

<details>
<summary><b>🔍 Search & Media Info</b></summary>

- IMDb / TMDB-powered movie & series info with posters (`/imdb`, `/movieinfo`, `/poster`, `/series`)
- Anime info and per-channel anime release tracking (`/anime`, `/set_anime_channel`)
- YouTube search with paginated results (`/search`)

</details>

<details>
<summary><b>💎 Monetization & User System</b></summary>

- **Freemium/Paid modes** — configurable globally (`DEFAULT_BOT_MODE`)
- **Telegram Stars payments** — buy premium in 1 day / 1 week / 1 month tiers (`/pay`)
- **Referral program** — earn bonus "bucks" per successful referral, redeemable for premium days (`/referral`, `/referral_list`)
- **Token gate** — optional URL-shortener based free-access token system (`/token`)
- **Usage tracking & batch limits** — separate free/premium batch-size caps and per-user usage stats (`/myuses`, `/totaluses`, `/useruses`)
- **Force-Subscribe** — require users to join a channel before using the bot

</details>

<details>
<summary><b>👑 Admin & Ops Tools</b></summary>

- Broadcast, ban/unban, premium grant/revoke, user & premium-user lists
- `/eval`, `/shell`, `/restart`, `/freez`, `/logs` — developer diagnostics (owner-gated, can be disabled via `DEV_TOOLS_ENABLED`)
- Automated daily MongoDB backup to a channel + on-demand `/backupdb`, `/dblink`
- Auto-copy every finished download to a backup/log channel (`AUTO_BACKUP_FILES`)
- `/speedtest`, `/ping`, `/status`, `/queue` — live server/task diagnostics
- `/stats` — bot-wide usage statistics

</details>

<details>
<summary><b>🧠 Persistent Storage & Infra</b></summary>

- MongoDB (via Motor, async) for all user data, settings, forward configs, and clone-bot registry
- Flask-based keep-alive server for uptime-ping style hosts
- Docker support with Playwright/Chromium, ffmpeg, aria2, megatools, and `7z` preinstalled for the full downloader stack

</details>

---

# 🛠 Deployment

## ✅ Prerequisites

- Python **3.12+**
- MongoDB database
- Telegram API ID & Hash ([my.telegram.org](https://my.telegram.org))
- Bot Token (from [@BotFather](https://t.me/BotFather))
- System binaries: `ffmpeg`, `aria2`, `megatools`, `7z` (all installed automatically in Docker)

---

## ⚙️ Environment Variables

<details>
<summary><b>🔴 Required</b></summary>

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token from BotFather |
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API Hash |
| `ADMINS` | Comma-separated admin Telegram user IDs (required — gates `/eval`, `/shell`) |
| `DB_URI` | MongoDB connection string |
| `LOG_CHANNEL` | Channel ID the bot logs users/errors to |

> ⚠️ **Security note:** `config.py` currently ships with hardcoded fallback values for `BOT_TOKEN`, `API_HASH`, and `DB_URI` (used only if the env var is unset). Make sure real environment variables are always set in production so these defaults are never actually used — and rotate the bot token / DB credentials before making this repo public.

</details>

<details>
<summary><b>⚪ Optional — General</b></summary>

| Variable | Description | Default |
|---|---|---|
| `DB_NAME` | MongoDB database name | `SaveRestricted2` |
| `ERROR_MESSAGE` | Send error messages to users | `True` |
| `DEV_TOOLS_ENABLED` | Enable `/eval` and `/shell` for admins | `True` |
| `FORCE_SUB_CHANNEL` | Channel users must join before using the bot | disabled |
| `MAX_BATCH_IDS_FREE` / `MAX_BATCH_IDS_PREMIUM` | Max messages per batch link | `50` / `200` |

</details>

<details>
<summary><b>⚪ Optional — Downloaders & Cookies</b></summary>

| Variable | Description | Default |
|---|---|---|
| `YTDL_MAX_FILESIZE` | Max direct-download size in bytes (auto-split above 1.9GB) | `4GB` |
| `YTDL_SEARCH_PAGE_SIZE` | Results per page for `/search` | `10` |
| `YT_COOKIES` / `INSTA_COOKIES` / `FB_COOKIES` / `VK_COOKIES` | Paths to Netscape-format cookies files | per-platform default paths |
| `GOFILE_TOKEN` | GoFile account token (optional, raises rate limits) | guest session |
| `GDRIVE_TOKEN_PATH` | Path to OAuth `token.pickle` for `/gdrive` private files/folders | `gdrive/token.pickle` |
| `JD_EMAIL` / `JD_PASS` / `JD_DOWNLOAD_DIR` | MyJDownloader account credentials for `/jd` | disabled if blank |

</details>

<details>
<summary><b>⚪ Optional — Monetization, TMDB, Backups</b></summary>

| Variable | Description | Default |
|---|---|---|
| `DEFAULT_BOT_MODE` | `paid` or `freemium` | `paid` |
| `STAR_PRICE_DAY` / `STAR_PRICE_WEEK` / `STAR_PRICE_MONTH` | Telegram Stars pricing for `/pay` | `15` / `75` / `250` |
| `REFERRAL_REWARD_BUCKS` / `REFERRAL_TRIAL_DAYS` / `BUCKS_PER_PREMIUM_DAY` | Referral program tuning | `50` / `1` / `100` |
| `WEBSITE_URL` / `AD_API` | URL-shortener token-gate config | disabled if blank |
| `TOKEN_VALID_HOURS` / `TOKEN_BATCH_BONUS` | Token-gate validity & batch bonus | `3` / `20` |
| `TMDB_API_KEY` | Enables `/movieinfo`, `/poster`, autopost | disabled if blank |
| `AUTOPOST_CHANNEL` / `AUTOPOST_HOUR_UTC` | Daily TMDB autopost target & schedule (UTC hour) | disabled / `6` |
| `DB_CHANNEL` / `AUTO_BACKUP_FILES` / `DB_BACKUP_HOUR_UTC` | Backup-copy channel, file auto-backup toggle, DB dump schedule (UTC hour) | `LOG_CHANNEL` / `True` / `3` |

</details>

---

## 💻 Local Setup

<details open>
<summary><b>Installation Steps</b></summary>

```bash
git clone <your-repo-url>
cd akbots
pip install -r requirements.txt
```

**System binaries (install via apt, not pip):**
```bash
apt-get install ffmpeg aria2 megatools p7zip-full
playwright install --with-deps chromium
```

**Run the bot:**
```bash
python bot.py
```

</details>

---

## 🐳 Docker

```bash
docker build -t akbots .
docker run -d --env-file .env akbots
```

The provided `Dockerfile` already installs `ffmpeg`, `aria2`, `megatools`, `7z`, and Playwright's Chromium build, and runs `playwright install --with-deps chromium` at build time.

---

## ☁️ Render / Procfile Deployment

This repo ships a `Procfile` (`worker: python3 bot.py`) and `runtime.txt` (`python-3.12.9`) for buildpack-style hosts like Render/Railway. On non-Docker deploys, `headless.py` self-installs the Playwright Chromium binary on first use (one-time delay, cached after).

---

# 📝 Commands

## 👤 User — Core & Account

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/start` | Start the bot |
| `/help` | Get help information |
| `/commands` | Full command list |
| `/about` | Bot info |
| `/login` / `/logout` | Log in/out for restricted-content saving |
| `/cancel` / `/cancel_all` / `/allcancel` | Cancel ongoing batch/task(s) |
| `/settings` | Open settings menu |
| `/setchat` | Set default target chat |
| `/status` / `/queue` / `/check` / `/ping` | Task/queue diagnostics |

</details>

## ⚙️ User — Customization

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/set_caption` `/see_caption` `/del_caption` `/clearcaption` | Custom caption |
| `/set_thumb` `/view_thumb` `/del_thumb` `/thumb_mode` | Custom thumbnail |
| `/set_prefix` `/see_prefix` `/del_prefix` | Filename prefix |
| `/set_suffix` `/see_suffix` `/del_suffix` | Filename suffix |
| `/autorename` `/see_autorename` `/del_autorename` `/numbering` | Auto-rename & sequencing |
| `/set_metadata` `/see_metadata` `/del_metadata` `/apply_metadata` | File metadata |
| `/set_watermark` `/see_watermark` `/del_watermark` `/apply_watermark` `/watermark_position` | Watermark overlay |
| `/set_del_word` `/rem_del_word` `/set_repl_word` `/rem_repl_word` | Caption word filters |
| `/upload_mode` `/textonlymode` | Output preferences |

</details>

## ⬇️ User — Downloaders

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/terabox` `/mf` `/mega` `/gdrive` `/gofile` `/pixeldrain` `/catbox` `/gdflix` `/hubcloud` `/filepress` | Cloud/file host downloads |
| `/yt` `/yta` `/ytraw` `/ytstatus` `/search` | YouTube video/audio/search |
| `/insta` `/fb` `/vk` `/dailymotion` `/mxplayer` `/zee5` `/stape` `/fembed` | Social/streaming site downloads |
| `/spotify` `/saavn` | Music downloads |
| `/torrent` `/ftp` `/url` `/m3u8` | Torrent, FTP, direct-link, HLS downloads |
| `/jd` `/jdstatus` | JDownloader (premium hosters) |
| `/gallery` | Gallery-style site downloads |
| `/transfer` | File transfer between chats |

</details>

## 🍪 User — Cookies & Diagnostics

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/cookie` `/setcookies` `/delcookies` `/listcookies` | Manage per-platform cookies |
| `/cookiecheck` | Diagnose YouTube/login-wall cookie issues |
| `/speedtest` | Server speed test |

</details>

## 🎬 User — File Tools

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/screenshots` `/autoscreenshots` | Generate screenshots from video |
| `/sample` `/autosample` | Generate sample clip |
| `/tomp4` `/tovideo` `/todocument` `/extract_audio` | Format conversion |
| `/zip` `/unzip` `/zipname` `/zippass` `/zipcancel` `/donezip` | Archive create/extract |
| `/unequify` `/unequifycancel` | Duplicate remover |
| `/spoiler` | Mark media as spoiler |

</details>

## 🔍 User — Info & Search

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/imdb` `/movieinfo` `/poster` `/series` | Movie/series info |
| `/anime` | Anime info |

</details>

## 💎 User — Premium & Referral

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/myplan` `/plan` `/premium` | Plan info |
| `/pay` | Buy premium via Telegram Stars |
| `/referral` `/referral_list` | Referral program |
| `/token` | Free-access token gate |
| `/myuses` `/totaluses` | Personal usage stats |
| `/terms` | Terms of service |

</details>

## 🔁 User — Forwarding

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/fwd` `/fwdstatus` `/fwdcancel` `/fwdresume` | Run/manage a forward job |
| `/fwd_caption` `/fwd_button` `/fwd_filter` `/fwd_settings` | Forward customization |
| `/ftmmanager` `/forwardmode` `/forwardstatus` | Forward mode selection & status |
| `/deltamode` `/thetamode` `/blastmode` `/pimode` | Individual FTM modes |
| `/addremover` `/clearremover` `/addreplacer` `/clearreplacer` | Word remove/replace filters |
| `/linkremover` `/usernameremover` | Strip links/usernames from forwards |
| `/addsource` `/rmsource` `/sources` `/setsource` | Source channel management |
| `/addtarget` `/rmtarget` `/targets` `/settarget` | Target channel management |
| `/addroute` `/delroute` | Source→target routing |
| `/rss_add` `/rss_list` `/rss_remove` `/rss_check` | RSS auto-poster |

</details>

## 🤖 Multi-Bot / Clone Management

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/setbot` `/addbot` `/delbot` `/rembot` `/mode` | Manage cloned/managed bots (Titanium mode) |
| `/setbutton` `/delbutton` | Custom inline buttons |
| `/channel_id` `/set_channel_id` `/del_channel_id` `/channels` | Bound channel management |
| `/set_anime_channel` `/del_anime_channel` `/anime_channel_status` | Anime-post channel binding |
| `/setfilters` | Clone-bot content filters |

</details>

## 👑 Admin Commands

<details>
<summary><b>Click to Expand</b></summary>

| Command | Action |
|---|---|
| `/broadcast` | Broadcast a message to all users |
| `/ban` / `/unban` | Ban/unban a user |
| `/add_premium` / `/remove_premium` | Grant/revoke premium |
| `/users` / `/premium_users` / `/stats` | User & bot stats |
| `/set_dump` / `/dblink` | Dump/DB link config |
| `/mydb` `/set_mydb` `/del_mydb` `/backupdb` | Database management & backups |
| `/setgdrivetoken` | Upload Google Drive OAuth token |
| `/add_unsubscribe` | Force-sub channel management |
| `/warning` `/freez` | Moderation |
| `/logs` `/eval` `/shell` `/restart` | Developer diagnostics |
| `/admin_commands_list` | List all admin commands |

</details>

---

# 🤝 Contributors

<p align="center">
  <a href="https://t.me/Anujedits76">
    <img src="https://img.shields.io/badge/Anuj-Telegram-blue?style=for-the-badge&logo=telegram">
  </a>
  &nbsp;
  <a href="https://github.com/anujeditinganuj-dotcom">
    <img src="https://img.shields.io/badge/Anuj-GitHub-black?style=for-the-badge&logo=github">
  </a>
</p>

---

# 📞 Support

<p align="center">
  <a href="https://t.me/AkBots_Official">
    <img src="https://img.shields.io/badge/AkBots-Official%20Channel-blue?style=for-the-badge&logo=telegram">
  </a>
  <br><br>
  <a href="https://t.me/THEUPDATEDGUYS">
    <img src="https://img.shields.io/badge/Updates-Channel-blue?style=for-the-badge&logo=telegram">
  </a>
</p>

---

<p align="center">
⭐ If this project helped you, consider starring the repository!
</p>
