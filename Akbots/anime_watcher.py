# Akbots/anime_watcher.py
#
# Generic multi-site anime episode watcher — ported in from a standalone
# telebot+Selenium bot (bot.py) that this project doesn't otherwise cover.
#
# NOTE ON SCOPE: Akbots/anime.py already handles SubsPlease auto-posting
# and Akbots/set_thumb already handles per-user thumbnails, Akbots/users +
# Akbots/broadcast already handle the user list/broadcast, and /reset
# already exists for other purposes — none of that is duplicated here.
# What's genuinely new is:
#   - watching arbitrary anime-site URLs for a "{NEW}" episode badge
#     (Selenium in the source bot -> Playwright here, to match this
#     project's existing browser-automation stack and avoid adding a new
#     heavy dependency for one feature)
#   - per-quality "mega"/redirect link discovery + bypass on that episode
#     page
#   - auto download -> upload -> batch-forward-to-all-users pipeline for
#     the resulting direct links
#   - dynamic (runtime, DB-stored) admins for *this* feature specifically
#     (/aadmin, /radmin, /wadmins) — separate from config.ADMINS, which
#     stays the hard-coded "can always do anything" list
#   - /cleanup for this feature's own download folder
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import re
import time
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import ADMINS
from database.db import db
from Akbots.direct_utils import (
    E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_BOLT,
    make_output_folder, fmt_bytes, draw_bar,
    extract_thumbnail, get_video_metadata, VIDEO_EXTS,
    _looks_like_html_error, _extract_html_reason,
)

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Optional: an external "resolve this redirect link" API, same shape as the
# source bot's BYPASS_URL (GET ?url=<redirect> -> page containing the real
# link). Leave BYPASS_URL unset in env to disable this specific tier — the
# rest of /watch (episode detection + link posting) still works without it.
BYPASS_URL = os.environ.get("ANIME_WATCH_BYPASS_URL", "")

DEFAULT_CHECK_INTERVAL_MIN = int(os.environ.get("ANIME_WATCH_INTERVAL_MIN", "10"))
DOWNLOAD_DIR = Path(make_output_folder("anime_watch"))

QUALITIES = [
    {"name": "480p", "search": "480p"},
    {"name": "720p", "search": "720p"},
    {"name": "1080p", "search": "1080p"},
]

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SETTINGS_ID = "anime_watch"  # doc _id inside db.settings_col

_scheduler = None
_processing_episodes = set()
_owner_batch = []  # [{message_id, chat_id, anime, episode}] — flushed every 3

# ================== SETTINGS (Mongo, via db.settings_col) ==================

async def _get_settings() -> dict:
    doc = await db.settings_col.find_one({"_id": SETTINGS_ID})
    doc = doc or {}
    doc.setdefault("sites", {})       # url -> {title, last_episode, added_by, added_at}
    doc.setdefault("admins", [])      # runtime admin ids, on top of config.ADMINS
    doc.setdefault("interval_min", DEFAULT_CHECK_INTERVAL_MIN)
    doc.setdefault("thumbs", {})      # anime_key(lower) -> file_id
    return doc

async def _save_settings(**fields):
    await db.settings_col.update_one(
        {"_id": SETTINGS_ID}, {"$set": fields}, upsert=True
    )

async def is_watch_admin(user_id: int) -> bool:
    if user_id in ADMINS:
        return True
    doc = await _get_settings()
    return user_id in doc.get("admins", [])

# ================== TITLE / EPISODE / LINK HELPERS (Playwright) ==================

def extract_clean_title(url: str) -> str:
    try:
        path_part = url.rstrip("/").split("/")[-1] if "/" in url else url
        title = path_part.replace("-", " ").title()

        unwanted = [
            r'Bluray', r'Hindi.*?Jap', r'Multi\s*Audio', r'DD\d+\.\d+',
            r'\d+p', r'HEVC', r'10bit', r'ESub', r'Dubbed?', r'Subbed?',
            r'WEB.*?DL', r'x264', r'x265', r'Complete', r'Batch',
            r'\[.*?\]', r'\(.*?Dub.*?\)', r'Dual\s*Audio'
        ]
        for term in unwanted:
            title = re.sub(term, '', title, flags=re.IGNORECASE)

        season_match = re.search(r'(.*?Season\s+\d+)', title, re.IGNORECASE)
        if season_match:
            title = season_match.group(1)

        return re.sub(r'\s+', ' ', title).strip() or "Unknown Anime"
    except Exception:
        return "Unknown Anime"

async def get_page_title_from_website(url: str) -> str:
    if not PLAYWRIGHT_AVAILABLE:
        return extract_clean_title(url)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            raw_title = None
            for sel in ("h1.entry-title", "h1.title", ".post-title h1"):
                try:
                    el = await page.query_selector(sel)
                    if el:
                        raw_title = (await el.inner_text()).strip()
                        break
                except Exception:
                    continue
            await browser.close()
            if not raw_title:
                return extract_clean_title(url)
            clean = re.sub(
                r'BluRay.*$|\[.*?\]|Hindi.*?Jap.*?Audio|\d+p.*$|DD\d+\.\d+.*$',
                '', raw_title, flags=re.IGNORECASE
            )
            season_match = re.search(r'(.*?Season\s+\d+)', clean, re.IGNORECASE)
            if season_match:
                clean = season_match.group(1)
            return re.sub(r'\s+', ' ', clean).strip() or extract_clean_title(url)
    except Exception as e:
        logger.warning(f"anime_watcher: title fetch failed for {url}: {e}")
        return extract_clean_title(url)

def fix_episode_link(base_link, episode_num):
    if not base_link:
        return None
    return re.sub(r'(\d+x)(\d+)$', rf'\g<1>{episode_num}', base_link)

async def _get_new_episode_elements(page):
    """Elements on the page whose text contains 'NEW' + an episode number."""
    handles = await page.query_selector_all("xpath=//*[contains(text(), 'NEW')]")
    found = []
    for el in handles:
        try:
            text = (await el.inner_text()).strip()
            match = re.search(r'Episode\s+(\d+)\s*\{?\s*NEW\s*\}?', text, re.IGNORECASE)
            if match:
                found.append({"num": int(match.group(1)), "el": el})
        except Exception:
            continue
    return found

async def check_new_episodes_count(url: str) -> list:
    if not PLAYWRIGHT_AVAILABLE:
        return []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)
            eps = await _get_new_episode_elements(page)
            await browser.close()
            return sorted({e["num"] for e in eps})
    except Exception as e:
        logger.warning(f"anime_watcher: episode check failed for {url}: {e}")
        return []

async def get_episode_page_url(url: str, episode_num: int) -> str | None:
    """Click into the earliest 'NEW' episode block, grab its watch/download
    link, then rewrite the trailing episode number so it points at
    `episode_num` specifically."""
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)

            eps = await _get_new_episode_elements(page)
            if not eps:
                await browser.close()
                return None
            eps.sort(key=lambda e: e["num"])

            try:
                await eps[0]["el"].scroll_into_view_if_needed()
                await eps[0]["el"].click(timeout=5000)
            except Exception:
                try:
                    anchor = await eps[0]["el"].query_selector("xpath=./ancestor::a[1]")
                    if anchor:
                        await anchor.click(timeout=5000)
                except Exception:
                    await browser.close()
                    return None
            await page.wait_for_timeout(5000)

            base_url = None
            for txt in ("watch", "download"):
                try:
                    link = page.get_by_text(re.compile(txt, re.IGNORECASE)).first
                    base_url = await link.get_attribute("href")
                    if base_url:
                        break
                except Exception:
                    continue

            await browser.close()
            return fix_episode_link(base_url, episode_num) if base_url else None
    except Exception as e:
        logger.warning(f"anime_watcher: episode page fetch failed for {url}: {e}")
        return None

async def get_mega_links_for_all_qualities(episode_url: str) -> dict:
    """Per-quality redirect/host links found on an episode page."""
    quality_links = {q["name"]: None for q in QUALITIES}
    if not PLAYWRIGHT_AVAILABLE or not episode_url:
        return quality_links

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            for quality in QUALITIES:
                page = await browser.new_page()
                try:
                    await page.goto(episode_url, timeout=30_000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(4000)

                    try:
                        btn = page.get_by_text(quality["search"], exact=False).first
                        await btn.click(timeout=5000)
                        await page.wait_for_timeout(4000)
                    except Exception:
                        await page.close()
                        continue

                    redirect = None
                    try:
                        candidates = await page.query_selector_all(
                            "xpath=//a[contains(@href,'redirect') or contains(@href,'crypt') "
                            "or contains(@href,'hubcloud')]"
                        )
                        for c in candidates:
                            if await c.is_visible():
                                redirect = await c.get_attribute("href")
                                if redirect:
                                    break
                    except Exception:
                        pass

                    quality_links[quality["name"]] = redirect
                except Exception:
                    pass
                finally:
                    await page.close()
            await browser.close()
    except Exception as e:
        logger.warning(f"anime_watcher: mega link fetch failed for {episode_url}: {e}")

    return quality_links

# ================== BYPASS ==================

async def bypass_redirect_link(redirect_url: str) -> str | None:
    if not redirect_url or not BYPASS_URL:
        return None
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            async with session.get(BYPASS_URL, params={"url": redirect_url}, timeout=30) as r:
                if r.status != 200:
                    return None
                text = await r.text()
        urls = URL_RE.findall(text or "")
        for u in urls:
            if "mega.nz" in u.lower():
                return u
        for u in urls:
            if "drive.google" in u.lower():
                return u
        for u in urls:
            if len(u) > 30:
                return u
        return None
    except Exception as e:
        logger.warning(f"anime_watcher: bypass failed for {redirect_url}: {e}")
        return None

# ================== CAPTION ==================

def format_caption(filename: str, anime_title: str, episode_num: int) -> str:
    anime_name = re.sub(r'\s*Season\s+\d+', '', anime_title, flags=re.IGNORECASE).strip()
    season_match = re.search(r'Season\s+(\d+)', anime_title, re.IGNORECASE)
    season_num = season_match.group(1) if season_match else "1"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"📺 {anime_name}",
        f"🏝️ Season {season_num}",
        f"🔢 Episode {episode_num}",
    ]
    fl = filename.lower()
    if "1080p" in fl:
        lines.append("🎬 Quality: 1080p" + (" HEVC 10bit" if "hevc" in fl or "10bit" in fl else ""))
    elif "720p" in fl:
        lines.append("🎬 Quality: 720p" + (" x264" if "x264" in fl else ""))
    elif "480p" in fl:
        lines.append("🎬 Quality: 480p" + (" x264" if "x264" in fl else ""))
    if "multi audio" in fl or "dual audio" in fl:
        lines.append("🔊 Multi Audio")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"

def _filename_from_cd(header: str):
    if not header:
        return None
    m = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", header, flags=re.IGNORECASE)
    return unquote(m.group(1)) if m else None

# ================== DOWNLOAD -> UPLOAD -> BROADCAST ==================

async def _download_direct(url: str, dest: Path, status: Message, label: str) -> Path:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    start = time.time()
    last_edit = 0
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=1800)) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                sample = await resp.content.read(4096)
                raise ValueError(f"Download failed — {_extract_html_reason(sample, resp.status)}.")
            real_name = _filename_from_cd(resp.headers.get("Content-Disposition"))
            if real_name:
                dest = dest.with_name(real_name)
            total = int(resp.headers.get("Content-Length", 0) or 0)
            tmp = dest.with_suffix(dest.suffix + ".part")
            downloaded = 0
            first_chunk = None
            with tmp.open("wb") as f:
                async for chunk in resp.content.iter_chunked(8 * 1024 * 1024):
                    if not chunk:
                        continue
                    if first_chunk is None:
                        first_chunk = chunk
                        if _looks_like_html_error(first_chunk):
                            try:
                                tmp.unlink(missing_ok=True)
                            except Exception:
                                pass
                            raise ValueError(f"Download failed — {_extract_html_reason(first_chunk, resp.status)}.")
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_edit >= 3:
                        last_edit = now
                        percent = int(downloaded * 100 / total) if total else 0
                        speed = downloaded / max(now - start, 1)
                        try:
                            await status.edit_text(
                                f"{E_BOLT} <b>{label}</b>\n<code>{dest.name}</code>\n\n"
                                f"[{draw_bar(percent)}] {percent}%\n"
                                f"{fmt_bytes(downloaded)} / {fmt_bytes(total)} @ {fmt_bytes(int(speed))}/s"
                            )
                        except Exception:
                            pass
            tmp.rename(dest)
    return dest

async def process_video_from_url(app: Client, url: str, anime_title: str, episode_num: int):
    """Download a resolved direct link, upload it to the staging chat
    (first configured admin), and queue it for batch-forwarding once 3
    videos are ready — mirrors the source bot's owner-batches-of-3 flow."""
    global _owner_batch
    if not ADMINS:
        return
    staging_chat = ADMINS[0]

    try:
        status = await app.send_message(staging_chat, f"{E_INFO} <b>Processing video...</b>")
    except Exception:
        return

    filename_hint = f"{anime_title}_E{episode_num}.mp4"
    dest = DOWNLOAD_DIR / filename_hint

    try:
        final_path = await _download_direct(url, dest, status, "Downloading")
    except Exception as e:
        try:
            await status.edit_text(f"{E_CROSS} <b>Download failed:</b> {e}")
        except Exception:
            pass
        return

    caption_text = format_caption(final_path.name, anime_title, episode_num)

    thumb_path = None
    settings = await _get_settings()
    thumbs = settings.get("thumbs", {})
    anime_lower = anime_title.lower()
    matched_key = next((k for k in thumbs if k == anime_lower), None) or \
        next((k for k in thumbs if k in anime_lower or anime_lower in k), None)
    if matched_key:
        try:
            thumb_path = await app.download_media(thumbs[matched_key], file_name=f"{matched_key}_thumb.jpg")
        except Exception:
            thumb_path = None

    duration = width = height = 0
    if final_path.suffix.lower() in VIDEO_EXTS:
        if not thumb_path:
            gen_thumb = str(final_path.with_suffix(".jpg"))
            if await asyncio.to_thread(extract_thumbnail, str(final_path), gen_thumb):
                thumb_path = gen_thumb
        try:
            duration, width, height = await asyncio.to_thread(get_video_metadata, str(final_path))
        except Exception:
            pass

    try:
        await status.edit_text(f"{E_BOLT} <b>Uploading to Telegram...</b>")
        if final_path.suffix.lower() in VIDEO_EXTS:
            sent = await app.send_video(
                chat_id=staging_chat, video=str(final_path), caption=caption_text,
                duration=duration, width=width, height=height, thumb=thumb_path,
                supports_streaming=True, parse_mode=enums.ParseMode.HTML,
            )
        else:
            sent = await app.send_document(
                chat_id=staging_chat, document=str(final_path), caption=caption_text,
                thumb=thumb_path, parse_mode=enums.ParseMode.HTML,
            )
        await status.delete()
        _owner_batch.append({
            "message_id": sent.id, "chat_id": staging_chat,
            "anime": anime_title, "episode": episode_num,
        })
        logger.info(f"anime_watcher: staged {anime_title} Ep{episode_num} ({len(_owner_batch)}/3)")
        if len(_owner_batch) >= 3:
            await _forward_batch_to_all_users(app)
    except Exception as e:
        try:
            await status.edit_text(f"{E_CROSS} <b>Upload failed:</b> {e}")
        except Exception:
            pass
    finally:
        for p in (final_path, thumb_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

async def _forward_batch_to_all_users(app: Client):
    global _owner_batch
    recipients = set()
    users_cursor = await db.get_all_users()
    async for user in users_cursor:
        recipients.add(user["id"])
    settings = await _get_settings()
    recipients.update(settings.get("admins", []))
    recipients.update(ADMINS)

    for item in _owner_batch:
        for uid in recipients:
            try:
                await app.copy_message(chat_id=uid, from_chat_id=item["chat_id"], message_id=item["message_id"])
                await asyncio.sleep(0.3)
            except Exception:
                pass
    _owner_batch = []

async def send_bypass_links_to_users(app: Client, anime_title, episode_num, quality_results, page_url):
    lines = [
        "🆕 <b>NEW EPISODE ALERT!</b>", "━━━━━━━━━━━━━━━━━━━━",
        f"📺 <b>{anime_title}</b>", f"🔢 Episode {episode_num}",
        "━━━━━━━━━━━━━━━━━━━━\n",
    ]
    has_link = False
    for q in QUALITIES:
        link = quality_results.get(q["name"])
        if link:
            lines.append(f"🔥 <b>{q['name']}:</b>\n<code>{link}</code>\n")
            has_link = True
        else:
            lines.append(f"❌ <b>{q['name']}:</b> Not Available\n")
    if not has_link:
        return
    lines.append(f"━━━━━━━━━━━━━━━━━━━━\n🔗 Source: {page_url}")
    text = "\n".join(lines)

    recipients = set()
    users_cursor = await db.get_all_users()
    async for user in users_cursor:
        recipients.add(user["id"])
    settings = await _get_settings()
    recipients.update(settings.get("admins", []))
    recipients.update(ADMINS)

    for uid in recipients:
        try:
            await app.send_message(uid, text, disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
            await asyncio.sleep(0.3)
        except Exception:
            pass

# ================== MONITOR JOB ==================

async def _process_episode_once(app: Client, url: str, anime_title: str, episode_num: int):
    key = f"{url}:{episode_num}"
    if key in _processing_episodes:
        return
    _processing_episodes.add(key)
    try:
        ep_page = await get_episode_page_url(url, episode_num)
        if not ep_page:
            return
        mega_redirects = await get_mega_links_for_all_qualities(ep_page)

        quality_results = {}
        for q in QUALITIES:
            name = q["name"]
            redirect = mega_redirects.get(name)
            final = await bypass_redirect_link(redirect) if redirect else None
            quality_results[name] = final
            if final and ("hubcloud" in final or "pixeldrain" in final):
                asyncio.create_task(process_video_from_url(app, final, anime_title, episode_num))

        await send_bypass_links_to_users(app, anime_title, episode_num, quality_results, ep_page)
    except Exception as e:
        logger.warning(f"anime_watcher: episode processing failed ({anime_title} Ep{episode_num}): {e}")
    finally:
        _processing_episodes.discard(key)

async def _watch_tick(app: Client):
    settings = await _get_settings()
    sites = settings.get("sites", {})
    if not sites:
        return
    for url, info in list(sites.items()):
        try:
            new_eps = await check_new_episodes_count(url)
            if not new_eps:
                continue
            highest = max(new_eps)
            if highest > info.get("last_episode", 0):
                sites[url]["last_episode"] = highest
                await _save_settings(sites=sites)
                if ADMINS:
                    try:
                        await app.send_message(
                            ADMINS[0],
                            f"🚨 <b>New episode!</b>\n📺 {info.get('title', 'Unknown')}\n"
                            f"🔢 Episode {highest}\n\n⏳ Processing...",
                        )
                    except Exception:
                        pass
                await _process_episode_once(app, url, info.get("title", "Unknown"), highest)
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"anime_watcher: tick failed for {url}: {e}")

def schedule_anime_watch(app: Client):
    """Call once from Bot.start() (see bot.py), same pattern as
    Akbots/hdhub.py's schedule_hdhub_autopost(). No-op if apscheduler or
    playwright aren't available — /watch commands still work for managing
    the site list, they just won't auto-check until this is available."""
    global _scheduler
    if not PLAYWRIGHT_AVAILABLE:
        logger.info("anime_watcher: Playwright unavailable, auto-check not scheduled.")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("anime_watcher: apscheduler not installed, auto-check not scheduled.")
        return

    async def _boot():
        settings = await _get_settings()
        interval = int(settings.get("interval_min", DEFAULT_CHECK_INTERVAL_MIN))
        global _scheduler
        _scheduler = AsyncIOScheduler(timezone="UTC")
        _scheduler.add_job(_watch_tick, "interval", minutes=interval, args=[app], id="anime_watch_tick")
        _scheduler.start()
        logger.info(f"anime_watcher: auto-check scheduled every {interval} min.")

    asyncio.create_task(_boot())

def _reschedule(minutes: int):
    global _scheduler
    if _scheduler:
        try:
            _scheduler.reschedule_job("anime_watch_tick", trigger="interval", minutes=minutes)
        except Exception:
            pass

# ================== COMMANDS ==================

@Client.on_message(filters.command("watch") & filters.private)
async def cmd_watch(client: Client, message: Message):
    if not await is_watch_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} Admin only!")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text(f"{E_INFO} Usage: <code>/watch [URL]</code>")
    url = parts[1].strip()

    settings = await _get_settings()
    sites = settings.get("sites", {})
    if url in sites:
        return await message.reply_text(f"{E_INFO} Already watching this URL!")

    wait_msg = await message.reply_text(f"{E_BOLT} Fetching title...")
    title = await get_page_title_from_website(url)
    sites[url] = {
        "title": title, "last_episode": 0,
        "added_by": message.from_user.id,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    await _save_settings(sites=sites)
    await wait_msg.edit_text(f"{E_CHECK} <b>Now watching:</b>\n📺 {title}\n🔗 {url}", disable_web_page_preview=True)

@Client.on_message(filters.command("unwatch") & filters.private)
async def cmd_unwatch(client: Client, message: Message):
    if not await is_watch_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} Admin only!")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text(f"{E_INFO} Usage: <code>/unwatch [URL]</code>")
    url = parts[1].strip()

    settings = await _get_settings()
    sites = settings.get("sites", {})
    if url not in sites:
        return await message.reply_text(f"{E_CROSS} Not being watched.")
    removed = sites.pop(url)
    await _save_settings(sites=sites)
    await message.reply_text(f"{E_CHECK} Stopped watching: {removed.get('title')}")

@Client.on_message(filters.command("watchlist") & filters.private)
async def cmd_watchlist(client: Client, message: Message):
    settings = await _get_settings()
    sites = settings.get("sites", {})
    if not sites:
        return await message.reply_text(f"{E_INFO} No anime being watched.")
    lines = [f"<b>📺 Watched sites ({len(sites)})</b>\n"]
    for i, (url, info) in enumerate(sites.items(), 1):
        lines.append(f"{i}. <b>{info.get('title')}</b>\n   📌 Ep {info.get('last_episode', 0)}\n   🔗 {url}\n")
    await message.reply_text("\n".join(lines), disable_web_page_preview=True)

@Client.on_message(filters.command("watchtime") & filters.private)
async def cmd_watchtime(client: Client, message: Message):
    if not await is_watch_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} Admin only!")
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 180):
        settings = await _get_settings()
        return await message.reply_text(
            f"{E_INFO} Usage: <code>/watchtime [1-180]</code> (minutes)\n"
            f"Current: {settings.get('interval_min', DEFAULT_CHECK_INTERVAL_MIN)} min"
        )
    minutes = int(parts[1])
    await _save_settings(interval_min=minutes)
    _reschedule(minutes)
    await message.reply_text(f"{E_CHECK} Check interval set to {minutes} min.")

@Client.on_message(filters.command("cleanup") & filters.private)
async def cmd_cleanup(client: Client, message: Message):
    if not await is_watch_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} Admin only!")
    count = 0
    for f in DOWNLOAD_DIR.glob("*"):
        if f.is_file():
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
    await message.reply_text(f"{E_CHECK} Cleaned {count} file(s) from the anime-watch download folder.")

@Client.on_message(filters.command("wthumb") & filters.private)
async def cmd_wthumb(client: Client, message: Message):
    """Per-anime-title thumbnail used when auto-uploading watched
    episodes (distinct from the per-user /set_thumb elsewhere in this
    bot — this one is looked up by anime name during processing)."""
    if not await is_watch_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} Admin only!")
    if not message.reply_to_message or not message.reply_to_message.photo:
        return await message.reply_text(f"{E_INFO} Reply to a photo with:\n<code>/wthumb Anime Name</code>")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text(f"{E_INFO} Anime name missing!")
    anime_key = parts[1].strip().lower()
    file_id = message.reply_to_message.photo.file_id

    settings = await _get_settings()
    thumbs = settings.get("thumbs", {})
    thumbs[anime_key] = file_id
    await _save_settings(thumbs=thumbs)
    await message.reply_text(f"{E_CHECK} Thumbnail saved for: {parts[1].strip()}")

@Client.on_message(filters.command("wthumblist") & filters.private)
async def cmd_wthumblist(client: Client, message: Message):
    settings = await _get_settings()
    thumbs = settings.get("thumbs", {})
    if not thumbs:
        return await message.reply_text(f"{E_INFO} No anime-watch thumbnails saved.")
    lines = [f"<b>📸 Saved thumbnails ({len(thumbs)})</b>\n"]
    lines.extend(f"{i}. {name.title()}" for i, name in enumerate(thumbs.keys(), 1))
    await message.reply_text("\n".join(lines))

@Client.on_message(filters.command("wthumbdel") & filters.private)
async def cmd_wthumbdel(client: Client, message: Message):
    if not await is_watch_admin(message.from_user.id):
        return await message.reply_text(f"{E_CROSS} Admin only!")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text(f"{E_INFO} Usage: <code>/wthumbdel Anime Name</code>")
    anime_key = parts[1].strip().lower()
    settings = await _get_settings()
    thumbs = settings.get("thumbs", {})
    if anime_key not in thumbs:
        return await message.reply_text(f"{E_CROSS} Not found: {parts[1]}")
    del thumbs[anime_key]
    await _save_settings(thumbs=thumbs)
    await message.reply_text(f"{E_CHECK} Deleted: {parts[1]}")

@Client.on_message(filters.command("aadmin") & filters.private & filters.user(ADMINS))
async def cmd_aadmin(client: Client, message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.reply_text(f"{E_INFO} Usage: <code>/aadmin [user_id]</code>")
    uid = int(parts[1])
    settings = await _get_settings()
    admins = settings.get("admins", [])
    if uid in admins:
        return await message.reply_text(f"{E_INFO} Already an admin.")
    admins.append(uid)
    await _save_settings(admins=admins)
    await message.reply_text(f"{E_CHECK} Added anime-watch admin: <code>{uid}</code>")
    try:
        await client.send_message(uid, f"{E_ROCKET} You're now an anime-watch admin!")
    except Exception:
        pass

@Client.on_message(filters.command("radmin") & filters.private & filters.user(ADMINS))
async def cmd_radmin(client: Client, message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.reply_text(f"{E_INFO} Usage: <code>/radmin [user_id]</code>")
    uid = int(parts[1])
    settings = await _get_settings()
    admins = settings.get("admins", [])
    if uid not in admins:
        return await message.reply_text(f"{E_CROSS} Not an anime-watch admin.")
    admins.remove(uid)
    await _save_settings(admins=admins)
    await message.reply_text(f"{E_CHECK} Removed: <code>{uid}</code>")

@Client.on_message(filters.command("wadmins") & filters.private)
async def cmd_wadmins(client: Client, message: Message):
    settings = await _get_settings()
    admins = settings.get("admins", [])
    lines = [f"<b>👥 Anime-watch admins</b>\n", f"👑 Config admins: {', '.join(str(a) for a in ADMINS)}\n"]
    if admins:
        lines.append(f"🔧 Runtime admins ({len(admins)}):")
        lines.extend(f"  • <code>{a}</code>" for a in admins)
    else:
        lines.append("📭 No extra runtime admins.")
    await message.reply_text("\n".join(lines))

# ================== PER-USER WATCHLIST + MANUAL BATCH ==================
# Ported from a second reference script (auto_uploading.txt) that had a
# genuinely different model from the admin-managed /watch list above:
# **self-service** — each user keeps their own anime list and toggles
# their own monitoring on/off, uploads land in their own DM. Also brings
# a manual /batch downloader (grab episodes 1..N of a URL right now,
# no monitoring involved).
#
# Deliberately NOT ported from that script:
#   - /rti + /unlock (a paid quality-tier unlock system) — this bot
#     already has a premium system (/premium, /pay, /myplan, /transfer);
#     bolting on a second, parallel monetization gate would conflict with
#     it rather than filling a real gap.
#   - /owner (first-user-to-claim-it ownership) — incompatible with this
#     bot's admin model, which is a fixed ADMINS list from config/env,
#     not a claimable runtime role. Use /aadmin (config admin only).
#   - /ban, /unban — this bot already has global /ban, /unban.

USER_WATCH_QUALITIES = ["480p", "720p", "1080p"]
PIXELDRAIN_RE = re.compile(r"https?://[^\s\"'<>]*pixeldrain[^\s\"'<>]*", re.IGNORECASE)

_user_monitor_running = set()  # user_ids with an active monitor_loop task

async def _get_user_watch(user_id: int) -> dict:
    settings = await _get_settings()
    uw = settings.get("user_watch", {})
    entry = uw.get(str(user_id)) or {"anime_list": [], "is_monitoring": False}
    entry.setdefault("anime_list", [])
    entry.setdefault("is_monitoring", False)
    return entry

async def _save_user_watch(user_id: int, entry: dict):
    settings = await _get_settings()
    uw = settings.get("user_watch", {})
    uw[str(user_id)] = entry
    await _save_settings(user_watch=uw)

async def get_audio_languages(path: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
            "-select_streams", "a", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        import json as _json
        streams = _json.loads(out or b"{}").get("streams", [])
        lang_map = {"hin": "Hindi", "hi": "Hindi", "eng": "English", "en": "English",
                    "jpn": "Japanese", "ja": "Japanese", "tam": "Tamil", "tel": "Telugu"}
        langs = []
        for s in streams:
            lang = (s.get("tags", {}).get("language") or "").lower()
            if lang in lang_map and lang_map[lang] not in langs:
                langs.append(lang_map[lang])
        return ", ".join(langs) if langs else "Multi Audio"
    except Exception:
        return "Multi Audio"

async def check_latest_episode_number(url: str) -> int | None:
    """Highest 'Episode N' occurrence anywhere in the page — a looser
    check than check_new_episodes_count()'s 'NEW' badge requirement,
    matching how the source script's per-user monitor worked."""
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            content = await page.content()
            await browser.close()
        nums = re.findall(r'Episode\s+(\d+)', content, re.IGNORECASE)
        return max(int(n) for n in nums) if nums else None
    except Exception as e:
        logger.warning(f"anime_watcher: latest-episode check failed for {url}: {e}")
        return None

async def extract_quality_pixeldrain_link(url: str, episode_num: int, quality: str) -> str | None:
    """On a site's episode-listing page, click the quality link that sits
    near 'Episode {episode_num}', follow through an optional 'neodrive'
    intermediate page, and pull out the resulting pixeldrain link."""
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            clicked = False
            try:
                anchor = page.locator(
                    f"xpath=(//*[contains(text(), 'Episode {episode_num}')]"
                    f"/following::*[contains(text(), '{quality}')])[1]"
                )
                await anchor.click(timeout=5000)
                clicked = True
            except Exception:
                try:
                    anchor = page.get_by_text(quality, exact=False).first
                    await anchor.click(timeout=5000)
                    clicked = True
                except Exception:
                    pass
            if not clicked:
                await browser.close()
                return None
            await page.wait_for_timeout(4000)

            target = page
            pages = context.pages
            if len(pages) > 1:
                target = pages[-1]

            try:
                neo = target.get_by_text(re.compile("neodrive", re.IGNORECASE)).first
                await neo.click(timeout=5000)
                await target.wait_for_timeout(3000)
                if len(context.pages) > 1:
                    target = context.pages[-1]
            except Exception:
                pass

            href = None
            try:
                links = await target.query_selector_all("a[href]")
                for link in links:
                    h = await link.get_attribute("href")
                    if h and "pixeldrain" in h.lower():
                        href = h
                        break
            except Exception:
                pass
            if not href:
                try:
                    content = await target.content()
                    m = PIXELDRAIN_RE.search(content)
                    href = m.group(0) if m else None
                except Exception:
                    pass

            await browser.close()
            return href
    except Exception as e:
        logger.warning(f"anime_watcher: quality-link extract failed ({quality} Ep{episode_num}): {e}")
        return None

async def download_pixeldrain(purl: str, status: Message, label: str) -> Path | None:
    try:
        if "/u/" in purl:
            fid = purl.split("/u/")[-1].split("?")[0].split("#")[0].split("/")[0]
        else:
            fid = purl.rstrip("/").split("/")[-1].split("?")[0]
        api = f"https://pixeldrain.com/api/file/{fid}"

        fname = f"{label}.mkv"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{api}/info", timeout=10) as r:
                    if r.status == 200:
                        info = await r.json()
                        fname = re.sub(r'[<>:"/\\|?*\[\]]', '', info.get("name") or fname)
        except Exception:
            pass

        dest = DOWNLOAD_DIR / fname
        final = await _download_direct(api, dest, status, "Downloading")
        if final.exists() and final.stat().st_size > 1024 * 1024:
            return final
        return None
    except Exception as e:
        logger.warning(f"anime_watcher: pixeldrain download failed for {purl}: {e}")
        return None

async def _upload_to_user(app: Client, user_id: int, fpath: Path, status: Message,
                           anime_name: str, episode_num, quality: str, season: str = "01"):
    thumb_path = None
    try:
        duration, width, height = await asyncio.to_thread(get_video_metadata, str(fpath))
        audio = await get_audio_languages(str(fpath))

        settings = await _get_settings()
        thumbs = settings.get("thumbs", {})
        anime_lower = anime_name.lower()
        matched_key = next((k for k in thumbs if k == anime_lower), None) or \
            next((k for k in thumbs if k in anime_lower or anime_lower in k), None)
        if matched_key:
            try:
                thumb_path = await app.download_media(thumbs[matched_key], file_name=f"{matched_key}_thumb.jpg")
            except Exception:
                thumb_path = None
        if not thumb_path:
            gen_thumb = str(fpath.with_suffix(".jpg"))
            if await asyncio.to_thread(extract_thumbnail, str(fpath), gen_thumb):
                thumb_path = gen_thumb

        q_text = {"1080p": "1080p HEVC", "720p": "720p x264", "480p": "480p x264"}.get(quality, quality)
        ep_line = f"🔢 Episode: {str(episode_num).zfill(2)}\n" if episode_num else ""
        caption = (
            "<blockquote>"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📺 Anime: {get_anime_only_title(anime_name)}\n"
            f"🏝️ Season: {season}\n"
            f"{ep_line}"
            f"🎬 Quality: {q_text}\n"
            f"🔊 Audio: {audio}\n"
            "━━━━━━━━━━━━━━━━━━━━"
            "</blockquote>"
        )

        await status.edit_text(f"{E_BOLT} Uploading...\n📺 {anime_name} | {quality}")
        await app.send_video(
            chat_id=user_id, video=str(fpath), caption=caption,
            duration=duration, width=width, height=height, thumb=thumb_path,
            supports_streaming=True, parse_mode=enums.ParseMode.HTML,
        )
        try:
            await status.delete()
        except Exception:
            pass
    finally:
        for p in (fpath, thumb_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

def get_anime_only_title(name: str) -> str:
    return re.sub(r'\s*Season\s*\d+', '', name, flags=re.IGNORECASE).strip() or name

# --------- per-user monitoring ---------

async def _user_monitor_loop(app: Client, user_id: int):
    if user_id in _user_monitor_running:
        return
    _user_monitor_running.add(user_id)
    try:
        while True:
            entry = await _get_user_watch(user_id)
            if not entry.get("is_monitoring"):
                break

            settings = await _get_settings()
            interval_min = int(settings.get("interval_min", DEFAULT_CHECK_INTERVAL_MIN))

            for anime in list(entry.get("anime_list", [])):
                fresh = await _get_user_watch(user_id)
                if not fresh.get("is_monitoring"):
                    break

                url = anime["url"]
                last_ep = anime.get("last_ep", 0)
                latest = await check_latest_episode_number(url)
                if latest and latest > last_ep:
                    anime["last_ep"] = latest
                    await _save_user_watch(user_id, entry)
                    try:
                        await app.send_message(user_id, f"{E_ROCKET} New episode found: {anime['name']} Ep {latest}")
                    except Exception:
                        pass
                    for q in USER_WATCH_QUALITIES:
                        try:
                            purl = await extract_quality_pixeldrain_link(url, latest, q)
                            if not purl:
                                continue
                            status = await app.send_message(user_id, f"{E_INFO} Fetching {q}...")
                            fpath = await download_pixeldrain(purl, status, f"{anime['name']}_E{latest}_{q}")
                            if fpath:
                                await _upload_to_user(
                                    app, user_id, fpath, status, anime["name"], latest, q,
                                    season=get_season_number(anime["name"]),
                                )
                        except Exception as e:
                            logger.warning(f"anime_watcher: user-monitor upload failed ({q}): {e}")
                        await asyncio.sleep(1)
                await asyncio.sleep(2)

            await asyncio.sleep(interval_min * 60)
    finally:
        _user_monitor_running.discard(user_id)

def get_season_number(name: str) -> str:
    m = re.search(r'Season\s*(\d+)', name, re.IGNORECASE)
    return m.group(1).zfill(2) if m else "01"

# --------- commands ---------

@Client.on_message(filters.command("myanime") & filters.private)
async def cmd_myanime(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text(f"{E_INFO} Usage: <code>/myanime [URL]</code>")
    url = parts[1].strip()
    user_id = message.from_user.id

    entry = await _get_user_watch(user_id)
    if any(a["url"] == url for a in entry["anime_list"]):
        return await message.reply_text(f"{E_INFO} Already in your list!")

    wait_msg = await message.reply_text(f"{E_BOLT} Fetching title...")
    title = await get_page_title_from_website(url)
    entry["anime_list"].append({"url": url, "name": title, "last_ep": 0})
    await _save_user_watch(user_id, entry)
    await wait_msg.edit_text(f"{E_CHECK} Added to your list:\n📺 {title}", disable_web_page_preview=True)

@Client.on_message(filters.command("myanimedel") & filters.private)
async def cmd_myanimedel(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text(f"{E_INFO} Usage: <code>/myanimedel [URL]</code>")
    url = parts[1].strip()
    user_id = message.from_user.id

    entry = await _get_user_watch(user_id)
    before = len(entry["anime_list"])
    entry["anime_list"] = [a for a in entry["anime_list"] if a["url"] != url]
    if len(entry["anime_list"]) == before:
        return await message.reply_text(f"{E_CROSS} Not found in your list.")
    await _save_user_watch(user_id, entry)
    await message.reply_text(f"{E_CHECK} Removed from your list.")

@Client.on_message(filters.command("myanimelist") & filters.private)
async def cmd_myanimelist(client: Client, message: Message):
    entry = await _get_user_watch(message.from_user.id)
    if not entry["anime_list"]:
        return await message.reply_text(f"{E_INFO} Your list is empty. Add one with <code>/myanime [URL]</code>")
    lines = [f"<b>📺 Your anime list ({len(entry['anime_list'])})</b>\n"]
    for i, a in enumerate(entry["anime_list"], 1):
        lines.append(f"{i}. <b>{a['name']}</b>\n   📌 Ep {a.get('last_ep', 0)}\n   🔗 {a['url']}\n")
    status_line = f"👀 Monitoring: {'ON' if entry['is_monitoring'] else 'OFF'}"
    lines.append(status_line)
    await message.reply_text("\n".join(lines), disable_web_page_preview=True)

@Client.on_message(filters.command("monitor") & filters.private)
async def cmd_monitor(client: Client, message: Message):
    user_id = message.from_user.id
    entry = await _get_user_watch(user_id)
    if not entry["anime_list"]:
        return await message.reply_text(f"{E_CROSS} No anime in your list! Use <code>/myanime [URL]</code> first.")
    if entry["is_monitoring"]:
        return await message.reply_text(f"{E_INFO} Already running!")

    entry["is_monitoring"] = True
    await _save_user_watch(user_id, entry)
    settings = await _get_settings()
    await message.reply_text(
        f"{E_ROCKET} <b>Monitoring started!</b>\n\n"
        f"📺 Anime: {len(entry['anime_list'])}\n"
        f"⏱️ Interval: {settings.get('interval_min', DEFAULT_CHECK_INTERVAL_MIN)} min"
    )
    asyncio.create_task(_user_monitor_loop(client, user_id))

@Client.on_message(filters.command("stopmonitor") & filters.private)
async def cmd_stopmonitor(client: Client, message: Message):
    user_id = message.from_user.id
    entry = await _get_user_watch(user_id)
    entry["is_monitoring"] = False
    await _save_user_watch(user_id, entry)
    await message.reply_text(f"{E_CHECK} Monitoring stopped.")

@Client.on_message(filters.command("animebatch") & filters.private)
async def cmd_batch(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(f"{E_INFO} Usage: <code>/animebatch [URL] [count]</code>")
    url = message.command[1]
    try:
        count = int(message.command[2])
        if not (1 <= count <= 9):
            raise ValueError
    except ValueError:
        return await message.reply_text(f"{E_CROSS} Episode count must be 1-9.")

    status = await message.reply_text(f"{E_ROCKET} Starting batch 1-{count}...")
    anime_name = await get_page_title_from_website(url)
    season = get_season_number(anime_name)
    user_id = message.from_user.id

    for ep in range(1, count + 1):
        try:
            await status.edit_text(f"{E_BOLT} {anime_name}\n📥 Episode {ep}/{count}")
        except Exception:
            pass
        for q in USER_WATCH_QUALITIES:
            try:
                purl = await extract_quality_pixeldrain_link(url, ep, q)
                if not purl:
                    continue
                fpath = await download_pixeldrain(purl, status, f"{anime_name}_E{ep}_{q}")
                if fpath:
                    await _upload_to_user(client, user_id, fpath, status, anime_name, ep, q, season=season)
                    status = await message.reply_text(f"{E_BOLT} {anime_name}\n📥 Episode {ep}/{count}")
            except Exception as e:
                logger.warning(f"anime_watcher: /animebatch failed ({anime_name} Ep{ep} {q}): {e}")
            await asyncio.sleep(1)

    try:
        await status.edit_text(f"{E_CHECK} Batch done! Episodes 1-{count}")
    except Exception:
        pass
