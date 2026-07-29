# JDownloader Integration Setup (/jd)

`/jd <link>` downloads through JDownloader — it covers hundreds of file
hosts that yt-dlp and gallery-dl don't (premium one-click hosters,
click'n'load `.dlc` containers, and generic crawled links).

**Needs persistent hosting (VPS/Docker) — will not run reliably on
Replit/free serverless platforms.** JDownloader is a Java process that
must keep running in the background continuously.

## 1. Create a free MyJDownloader account
Go to https://my.jdownloader.org and sign up (just an email + password).
This is NOT your bot's Telegram info — it's a separate free account used
only so JDownloader itself can identify to its own network.

## 2. Set the credentials
Add these two environment variables wherever you configure the bot (`.env`,
Railway/host dashboard, etc.):

```
JD_EMAIL=your_myjdownloader_email
JD_PASS=your_myjdownloader_password
```

Optional (defaults shown):
```
JD_DOWNLOAD_DIR=/JDownloader/downloads
```

Leave `JD_EMAIL`/`JD_PASS` blank to keep `/jd` disabled entirely — nothing
else in the bot is affected either way.

## 3. Deploy
The Dockerfile already installs Java + downloads the JDownloader installer
at build time. On the bot's first-ever startup after this, JDownloader
self-updates (downloads the rest of itself) — **this can take a few
minutes the very first time only**. Every restart after that is fast.

Check it's ready with:
```
/jdstatus
```

## 4. Use it
```
/jd https://example-host.com/some/file/link
```
It adds the link to JDownloader, waits for it to resolve and download, then
uploads the finished file(s) to Telegram — same as every other downloader
in this bot.

## Notes
- `/jd` is a **manual command only** — it's deliberately not wired into
  the automatic link-detection that `/yt`, `/gallery`, etc. use, since
  JDownloader is one shared background process and every pasted link
  auto-triggering it could collide with other in-progress tasks.
- If a link needs a premium account on some hoster, add that hoster's
  login inside JDownloader's own settings (via the JDownloader app/website,
  logged into the same MyJDownloader account) — the bot doesn't manage
  hoster credentials itself.
- If `/jdstatus` shows disconnected, check the bot's logs — the error
  message there (missing jar, bad credentials, etc.) tells you what to fix.
