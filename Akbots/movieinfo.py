# Akbots
# Movie Info / Poster (TMDB) — ported from Url-uploader-Bot-V4
# (Plugin/movieinfo.py + Plugin/poster.py), merged and rewritten with aiohttp
# to match the rest of Akbots' async style.
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import TMDB_API_KEY

E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'

BASE_URL = "https://api.themoviedb.org/3"

LANG_MAP = {
    "hi": "Hindi", "te": "Telugu", "ta": "Tamil", "ml": "Malayalam", "kn": "Kannada",
    "en": "English", "bn": "Bengali", "mr": "Marathi", "gu": "Gujarati",
    "pa": "Punjabi", "or": "Odia", "as": "Assamese", "ur": "Urdu",
}

NO_ARGS_HELP = (
    "🎦 Please provide a movie or series name with the command.\n\n"
    "📌 Example: <code>/imdb Interstellar</code>\n"
    "✏️ Tip: If the movie is not found, try adding the release year along with the name.\n\n"
    "<b>Available Commands:</b>\n"
    "📝 <code>/movieinfo &lt;name&gt;</code> – Full info (poster, cast, rating, etc.)\n"
    "🎬 <code>/imdb &lt;name&gt;</code> – Quick info with poster\n"
    "🖼️ <code>/poster &lt;name&gt;</code> – Just the movie/series poster\n"
    "📺 <code>/series &lt;name&gt;</code> – For TV shows only"
)


def _is_configured():
    return bool(TMDB_API_KEY)


async def _get_json(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        return await resp.json()


def _parse_name_year(command: list):
    if len(command) > 2 and command[-1].isdigit() and len(command[-1]) == 4:
        return " ".join(command[1:-1]), command[-1]
    return " ".join(command[1:]), None


async def _search(session, name, year, media_type="movie"):
    year_param = "year" if media_type == "movie" else "first_air_date_year"
    url = f"{BASE_URL}/search/{media_type}?api_key={TMDB_API_KEY}&query={name}"
    if year:
        url += f"&{year_param}={year}"
    resp = await _get_json(session, url)
    results = resp.get("results", [])
    return results[0] if results else None


async def _search_movie(session, name, year):
    return await _search(session, name, year, "movie")


async def _search_movie_or_tv(session, name, year):
    """Try a movie search first, then fall back to TV — lets /imdb and
    /poster work for either a film or a show without the caller (or the
    user typing the command) needing to specify which up front."""
    movie = await _search(session, name, year, "movie")
    if movie:
        return movie, "movie"
    tv = await _search(session, name, year, "tv")
    if tv:
        return tv, "tv"
    return None, None


async def get_poster_url(session, media_id, media_type="movie"):
    url = f"{BASE_URL}/{media_type}/{media_id}/images?api_key={TMDB_API_KEY}&include_image_language=hi,en,null"
    try:
        resp = await _get_json(session, url)
    except Exception:
        return None
    backdrops = resp.get("backdrops", [])
    posters = resp.get("posters", [])

    for b in backdrops:
        if b.get("iso_639_1") == "hi":
            return f"https://media.themoviedb.org/t/p/w1000_and_h563_face{b['file_path']}"
    for b in backdrops:
        if b.get("iso_639_1") == "en":
            return f"https://media.themoviedb.org/t/p/w1000_and_h563_face{b['file_path']}"
    if posters:
        return f"https://image.tmdb.org/t/p/original{posters[0]['file_path']}"
    if backdrops:
        return f"https://media.themoviedb.org/t/p/w1000_and_h563_face{backdrops[0]['file_path']}"
    return None


def format_caption(details, directors, top_actors, languages):
    title = details.get("title")
    release_date = details.get("release_date", "N/A")
    year = release_date.split("-")[0] if release_date else "N/A"
    overview = details.get("overview", "No description available.")
    genres = ", ".join(g["name"] for g in details.get("genres", [])) or "N/A"
    runtime = details.get("runtime", "N/A")

    return (
        f"🎬 <b>{title}</b> ({year})\n\n"
        f"<b>🗓 Release Date:</b> <code>{release_date}</code>\n"
        f"<b>⏱ Runtime:</b> <code>{runtime} min</code>\n"
        f"<b>🌐 Languages:</b> <code>{languages}</code>\n"
        f"<b>🎭 Genres:</b> <code>{genres}</code>\n"
        f"<b>🎬 Director:</b> <code>{directors}</code>\n"
        f"<b>⭐ Cast:</b> <code>{top_actors}</code>\n\n"
        f"📝 <code>{overview}</code>"
    )


def format_series_caption(details, creators, top_actors, languages):
    title = details.get("name")
    first_air = details.get("first_air_date", "N/A")
    year = first_air.split("-")[0] if first_air else "N/A"
    overview = details.get("overview", "No description available.")
    genres = ", ".join(g["name"] for g in details.get("genres", [])) or "N/A"
    seasons = details.get("number_of_seasons", "N/A")
    episodes = details.get("number_of_episodes", "N/A")
    status = details.get("status", "N/A")
    networks = ", ".join(n["name"] for n in details.get("networks", [])) or "N/A"

    return (
        f"📺 <b>{title}</b> ({year})\n\n"
        f"<b>🗓 First Aired:</b> <code>{first_air}</code>\n"
        f"<b>📡 Status:</b> <code>{status}</code>\n"
        f"<b>🎞 Seasons/Episodes:</b> <code>{seasons} / {episodes}</code>\n"
        f"<b>🌐 Languages:</b> <code>{languages}</code>\n"
        f"<b>🎭 Genres:</b> <code>{genres}</code>\n"
        f"<b>📡 Network:</b> <code>{networks}</code>\n"
        f"<b>👨‍💼 Creator:</b> <code>{creators}</code>\n"
        f"<b>⭐ Cast:</b> <code>{top_actors}</code>\n\n"
        f"📝 <code>{overview}</code>"
    )


def format_quick_caption(details, media_type="movie"):
    is_tv = media_type == "tv"
    title = details.get("name") if is_tv else details.get("title")
    date = details.get("first_air_date" if is_tv else "release_date", "N/A")
    year = date.split("-")[0] if date else "N/A"
    rating = details.get("vote_average") or 0
    votes = details.get("vote_count") or 0
    genres = ", ".join(g["name"] for g in details.get("genres", [])) or "N/A"
    overview = details.get("overview", "No description available.")
    if len(overview) > 400:
        overview = overview[:400].rsplit(" ", 1)[0] + "…"
    kind_emoji = "📺" if is_tv else "🎬"
    kind_label = "TV Show" if is_tv else "Movie"

    return (
        f"{kind_emoji} <b>{title}</b> ({year}) <i>[{kind_label}]</i>\n\n"
        f"<b>⭐ Rating:</b> <code>{rating:.1f}/10</code> <i>({votes} votes)</i>\n"
        f"<b>🎭 Genres:</b> <code>{genres}</code>\n\n"
        f"📝 <code>{overview}</code>"
    )


@Client.on_message(filters.command("movieinfo"))
async def movieinfo_command(client: Client, message: Message):
    if not _is_configured():
        return await message.reply_text(
            f"<b>{E_CROSS} TMDB_API_KEY is not set.</b> Add it in Secrets to enable this command.",
            parse_mode=enums.ParseMode.HTML
        )
    if len(message.command) < 2:
        return await message.reply_text(NO_ARGS_HELP, parse_mode=enums.ParseMode.HTML)

    name, year = _parse_name_year(message.command)
    status = await message.reply_text(f"🔎 Searching for <b>{name}</b>...", parse_mode=enums.ParseMode.HTML)

    try:
        async with aiohttp.ClientSession() as session:
            movie = await _search_movie(session, name, year)
            if not movie:
                return await status.edit_text(f"❌ No results found for {name} ({year or ''}).")

            movie_id = movie["id"]
            details = await _get_json(session, f"{BASE_URL}/movie/{movie_id}?api_key={TMDB_API_KEY}&language=en-US")
            credits = await _get_json(session, f"{BASE_URL}/movie/{movie_id}/credits?api_key={TMDB_API_KEY}&language=en-US")

            cast = credits.get("cast", [])
            crew = credits.get("crew", [])
            top_actors = ", ".join(a["name"] for a in cast[:10]) or "N/A"
            directors = [m["name"] for m in crew if m.get("job") == "Director"]
            director_names = ", ".join(directors) if directors else "N/A"

            spoken_langs = details.get("spoken_languages", [])
            langs = [LANG_MAP.get(l["iso_639_1"], l.get("english_name", "?")) for l in spoken_langs]
            languages = ", ".join(langs) if langs else "N/A"

            poster_url = await get_poster_url(session, movie_id, "movie")
            caption = format_caption(details, director_names, top_actors, languages)

        await status.delete()
        if poster_url:
            await message.reply_photo(poster_url, caption=caption, parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(caption, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status.edit_text(f"❌ Error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("series"))
async def series_command(client: Client, message: Message):
    if not _is_configured():
        return await message.reply_text(
            f"<b>{E_CROSS} TMDB_API_KEY is not set.</b> Add it in Secrets to enable this command.",
            parse_mode=enums.ParseMode.HTML
        )
    if len(message.command) < 2:
        return await message.reply_text(NO_ARGS_HELP, parse_mode=enums.ParseMode.HTML)

    name, year = _parse_name_year(message.command)
    status = await message.reply_text(f"🔎 Searching for <b>{name}</b>...", parse_mode=enums.ParseMode.HTML)

    try:
        async with aiohttp.ClientSession() as session:
            show = await _search(session, name, year, "tv")
            if not show:
                return await status.edit_text(f"❌ No results found for {name} ({year or ''}).")

            tv_id = show["id"]
            details = await _get_json(session, f"{BASE_URL}/tv/{tv_id}?api_key={TMDB_API_KEY}&language=en-US")
            credits = await _get_json(session, f"{BASE_URL}/tv/{tv_id}/credits?api_key={TMDB_API_KEY}&language=en-US")

            cast = credits.get("cast", [])
            top_actors = ", ".join(a["name"] for a in cast[:10]) or "N/A"
            creators = [c["name"] for c in details.get("created_by", [])]
            creator_names = ", ".join(creators) if creators else "N/A"

            spoken_langs = details.get("spoken_languages", [])
            langs = [LANG_MAP.get(l["iso_639_1"], l.get("english_name", "?")) for l in spoken_langs]
            languages = ", ".join(langs) if langs else "N/A"

            poster_url = await get_poster_url(session, tv_id, "tv")
            caption = format_series_caption(details, creator_names, top_actors, languages)

        await status.delete()
        if poster_url:
            await message.reply_photo(poster_url, caption=caption, parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(caption, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status.edit_text(f"❌ Error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("imdb"))
async def imdb_command(client: Client, message: Message):
    """Quick info + poster, movie or TV — tries a movie match first, then
    falls back to TV, so the caller doesn't need to know which one it is."""
    if not _is_configured():
        return await message.reply_text(
            f"<b>{E_CROSS} TMDB_API_KEY is not set.</b> Add it in Secrets to enable this command.",
            parse_mode=enums.ParseMode.HTML
        )
    if len(message.command) < 2:
        return await message.reply_text(NO_ARGS_HELP, parse_mode=enums.ParseMode.HTML)

    name, year = _parse_name_year(message.command)
    status = await message.reply_text(f"🔎 Searching for <b>{name}</b>...", parse_mode=enums.ParseMode.HTML)

    try:
        async with aiohttp.ClientSession() as session:
            result, media_type = await _search_movie_or_tv(session, name, year)
            if not result:
                return await status.edit_text(f"❌ No results found for {name} ({year or ''}).")

            media_id = result["id"]
            details = await _get_json(
                session, f"{BASE_URL}/{media_type}/{media_id}?api_key={TMDB_API_KEY}&language=en-US"
            )
            poster_url = await get_poster_url(session, media_id, media_type)
            caption = format_quick_caption(details, media_type)

        await status.delete()
        if poster_url:
            await message.reply_photo(poster_url, caption=caption, parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(caption, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status.edit_text(f"❌ Error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("poster"))
async def poster_command(client: Client, message: Message):
    if not _is_configured():
        return await message.reply_text(
            f"<b>{E_CROSS} TMDB_API_KEY is not set.</b> Add it in Secrets to enable this command.",
            parse_mode=enums.ParseMode.HTML
        )
    if len(message.command) < 2:
        return await message.reply_text(NO_ARGS_HELP, parse_mode=enums.ParseMode.HTML)

    name, year = _parse_name_year(message.command)
    status = await message.reply_text(f"🔎 Searching posters for <b>{name}</b>...", parse_mode=enums.ParseMode.HTML)

    try:
        async with aiohttp.ClientSession() as session:
            # /poster is documented as "movie/series poster" — fall back to
            # a TV search too, same as /imdb, instead of only ever matching
            # movies.
            result, media_type = await _search_movie_or_tv(session, name, year)
            if not result:
                return await status.edit_text(f"❌ '{name}' not found.")
            poster_url = await get_poster_url(session, result["id"], media_type)

        await status.delete()
        title = result.get("title") or result.get("name") or name
        if poster_url:
            await message.reply_photo(poster_url, caption=f"🎬 <b>{title}</b>", parse_mode=enums.ParseMode.HTML)
        else:
            await message.reply_text(f"❌ No poster found for {title}.")
    except Exception as e:
        await status.edit_text(f"❌ Error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
