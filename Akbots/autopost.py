# Akbots
# AutoPost — ported from Url-uploader-Bot-V4 (Plugin/autopost.py).
# Daily job that checks TMDB's upcoming-movies list and posts a card to
# AUTOPOST_CHANNEL for movies releasing today / in 1 week / in 1 month /
# released 1 week or 1 month ago. Fully disabled unless TMDB_API_KEY and
# AUTOPOST_CHANNEL are both set.
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import random
import logging
from datetime import datetime, timedelta

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import TMDB_API_KEY, AUTOPOST_CHANNEL, AUTOPOST_HOUR_UTC, ADMINS
from Akbots.movieinfo import get_poster_url, LANG_MAP, BASE_URL

logger = logging.getLogger("Akbots.autopost")

_scheduler = None  # set by schedule_autopost() if apscheduler is available


def is_configured():
    return bool(TMDB_API_KEY and AUTOPOST_CHANNEL)


def _format_caption(details, directors, top_actors, languages, tag):
    title = details.get("title")
    release_date = details.get("release_date", "N/A")
    year = release_date.split("-")[0] if release_date else "N/A"
    overview = details.get("overview", "No description available.")
    genres = ", ".join(g["name"] for g in details.get("genres", [])) or "N/A"
    runtime = details.get("runtime", "N/A")

    return (
        f"🎬 <b>{title}</b> ({year})\n\n"
        f"<b>🏷 Status:</b> <code>{tag}</code>\n"
        f"<b>🗓 Release Date:</b> <code>{release_date}</code>\n"
        f"<b>⏱ Runtime:</b> <code>{runtime} min</code>\n"
        f"<b>🌐 Languages:</b> <code>{languages}</code>\n"
        f"<b>🎭 Genres:</b> <code>{genres}</code>\n"
        f"<b>🎬 Director:</b> <code>{directors}</code>\n"
        f"<b>⭐ Cast:</b> <code>{top_actors}</code>\n\n"
        f"📝 <code>{overview}</code>"
    )


async def send_movie_post(app: Client, session: aiohttp.ClientSession, movie: dict, tag: str):
    movie_id = movie["id"]
    async with session.get(f"{BASE_URL}/movie/{movie_id}?api_key={TMDB_API_KEY}&language=en-US") as r:
        details = await r.json()
    async with session.get(f"{BASE_URL}/movie/{movie_id}/credits?api_key={TMDB_API_KEY}&language=en-US") as r:
        credits = await r.json()

    cast = credits.get("cast", [])
    crew = credits.get("crew", [])
    top_actors = ", ".join(a["name"] for a in cast[:10]) or "N/A"
    directors = [m["name"] for m in crew if m.get("job") == "Director"]
    director_names = ", ".join(directors) if directors else "N/A"

    spoken_langs = details.get("spoken_languages", [])
    langs = [LANG_MAP.get(l["iso_639_1"], l.get("english_name", "?")) for l in spoken_langs]
    languages = ", ".join(langs) if langs else "N/A"

    poster_url = await get_poster_url(session, movie_id)
    caption = _format_caption(details, director_names, top_actors, languages, tag)

    try:
        if poster_url:
            await app.send_photo(AUTOPOST_CHANNEL, poster_url, caption=caption, parse_mode=enums.ParseMode.HTML)
        else:
            await app.send_message(AUTOPOST_CHANNEL, caption, parse_mode=enums.ParseMode.HTML)
        logger.info(f"Posted: {details.get('title')} ({tag})")
    except Exception as e:
        logger.error(f"Failed to post {details.get('title')}: {e}")


async def check_movies(app: Client):
    if not is_configured():
        return
    today = datetime.utcnow().date()
    url = f"{BASE_URL}/movie/upcoming?api_key={TMDB_API_KEY}&language=en-US&page=1&region=IN"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                resp = await r.json()
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            return

        movies = resp.get("results", [])
        logger.info(f"Checking {len(movies)} upcoming movies...")

        for movie in movies:
            release_date = movie.get("release_date")
            if not release_date:
                continue
            try:
                release = datetime.strptime(release_date, "%Y-%m-%d").date()
            except ValueError:
                continue

            if release == today:
                await send_movie_post(app, session, movie, "🎉 Releasing Today")
            elif release == today + timedelta(days=7):
                await send_movie_post(app, session, movie, "⏳ Releasing in 1 Week")
            elif release == today + timedelta(days=30):
                await send_movie_post(app, session, movie, "🗓 Releasing in 1 Month")
            elif release == today - timedelta(days=7):
                await send_movie_post(app, session, movie, "✅ Released 1 Week Ago")
            elif release == today - timedelta(days=30):
                await send_movie_post(app, session, movie, "📀 Released 1 Month Ago")


def schedule_autopost(app: Client):
    """Call once from Bot.start() after the client is running. No-op if
    apscheduler isn't installed or the feature isn't configured."""
    global _scheduler
    if not is_configured():
        logger.info("AutoPost disabled (TMDB_API_KEY / AUTOPOST_CHANNEL not set).")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("AutoPost enabled but apscheduler isn't installed — add it to requirements.txt.")
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(check_movies, "cron", hour=AUTOPOST_HOUR_UTC, args=[app])
    _scheduler.start()
    logger.info(f"AutoPost scheduler started ({AUTOPOST_HOUR_UTC}:00 UTC daily).")


@Client.on_message(filters.command("autotest") & filters.user(ADMINS))
async def autotest_command(client: Client, message: Message):
    if not is_configured():
        return await message.reply_text(
            "❌ AutoPost isn't configured — set TMDB_API_KEY and AUTOPOST_CHANNEL.",
        )

    status = await message.reply_text("🎲 Picking a random upcoming movie...")
    url = f"{BASE_URL}/movie/upcoming?api_key={TMDB_API_KEY}&language=en-US&page=1&region=IN"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                resp = await r.json()
            movies = resp.get("results", [])
            if not movies:
                return await status.edit_text("❌ No upcoming movies found.")
            movie = random.choice(movies)
            await send_movie_post(client, session, movie, "📢 Test AutoPost")
        await status.edit_text(f"✅ Random test movie posted: <code>{movie.get('title')}</code>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status.edit_text(f"❌ Error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
