# Akbots - Don't Remove Credit - @AkBots_Official
#
# FilmyZilla category-page link extractor — ported from a standalone sync
# Pyrogram script the user provided (blocking requests + BeautifulSoup,
# its own Client/Flask keep-alive) into this project's plugin conventions:
# async aiohttp instead of blocking requests, the shared direct_utils.py
# download/upload/progress helpers, link_cache reuse, and auto-detection
# via a message regex handler like every other host module here (see
# mediafire.py for the closest sibling pattern this was modelled on).
#
# What a category page looks like: a list of movie tiles, each linking to
# a movie page, which links to a "server" page, which finally has the real
# download link (hosted on a workers.dev Cloudflare Worker in every case
# observed). Three fetches deep per movie — that's inherent to the site's
# structure, not something this module can shortcut.
#
# Two ways to use what gets found, since this bot's whole reason to exist
# is downloading+re-uploading to Telegram, not just listing links:
#   1) The scrape reply always shows the plain link list (like the
#      original script's TXT-extractor output) — chunked into multiple
#      messages instead of silently truncating at 4096 chars like the
#      original did.
#   2) A "Download & send all" button additionally offers to actually
#      pull each file through stream_download/upload_file and deliver it
#      as a normal Telegram video/document, same as mediafire.py's folder
#      download flow.
#
# Domain isn't hardcoded to filmyzilla0.com — sites like this rotate
# mirror domains often (filmyzilla1.*, filmyzilla2.*, ...), so the base
# URL used for resolving relative links is taken from whatever URL the
# person actually sent.

import re
import json
import uuid
import asyncio
from urllib.parse import urljoin, urlparse

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    DEFAULT_HEADERS, E_CHECK, E_CROSS, E_INFO, E_ROCKET
)
from Akbots.link_cache import try_send_cached
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Matches any filmyzilla mirror's category page — e.g. filmyzilla0.com,
# filmyzilla1.co, filmyzilla2.in, whichever domain currently resolves.
PATTERN = re.compile(
    r"(https?://)?(www\.)?filmyzilla\d*\.\w+/(category/\S+|movie/\S+|server/\S+)",
    re.IGNORECASE,
)

# The only mirror currently reachable from this bot's host — filmyzilla0.com
# and other older mirror numbers are dead/blocked here (connection just
# fails, it doesn't even get far enough to send an HTTP redirect for
# aiohttp to follow). Every incoming link — whatever mirror number the
# person actually pasted — gets its domain rewritten to this one before any
# request is made, so /filmyzilla keeps working even if someone pastes an
# old filmyzilla0.com link out of habit or from an old forwarded message.
# Bump this one line if this mirror ever dies too.
CURRENT_MIRROR = "www.filmyzilla52.com"
_MIRROR_DOMAIN_RE = re.compile(r"(https?://)(www\.)?filmyzilla\d*\.\w+", re.IGNORECASE)


def _normalize_mirror(url: str) -> str:
    return _MIRROR_DOMAIN_RE.sub(lambda m: f"{m.group(1)}{CURRENT_MIRROR}", url)


MAX_MOVIES = 30          # hard cap per category page — mirrors mediafire.py's MAX_FOLDER_FILES
CONCURRENCY = 5          # parallel movie->download-link resolutions
REQUEST_TIMEOUT = 20
LINKS_CHUNK = 3800        # stay under Telegram's 4096-char message limit with room for HTML tags

# Full-site scan (/filmyzillascan) defaults — ported over from the
# standalone TS crawler's approach (walk every category, then every movie
# in each), but capped hard by default since this runs inline in a chat
# request/response cycle rather than as a long-lived offline job. A person
# can raise the caps explicitly via command args if they really want a
# bigger sweep.
SCAN_MAX_CATEGORIES = 5
SCAN_MAX_MOVIES_PER_CATEGORY = 10
SCAN_STATUS_EVERY = 5     # edit the progress message every N movies, not every single one

# session_id -> {"files": [(filename, url), ...], "message": Message}
_FILMY_SESSIONS = {}


def extract_url(text: str):
    m = PATTERN.search(text)
    if not m:
        return None
    url = m.group(0)
    url = url if url.startswith("http") else f"https://{url}"
    return _normalize_mirror(url)


def _base_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> tuple:
    """Returns (html, final_url) — final_url is where the response actually
    came from AFTER following redirects (aiohttp follows them by default),
    which is what relative links on the page are really relative to. Using
    the ORIGINAL url the person pasted instead would silently keep pointing
    at a dead/rotated mirror domain (e.g. filmyzilla0.com redirecting to
    filmyzilla52.com) for every subsequent request built from `base`."""
    async with session.get(url, headers=DEFAULT_HEADERS, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status}")
        return await resp.text(), str(resp.url)


def _movie_links(html: str, base: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.select("a.d-flex.flex-row"):
        href = a.get("href")
        if href and "/movie/" in href:
            links.append(urljoin(base, href))
    return links


def _find_server_link(html: str, base: str):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # filmyzilla-family sites have used both "server_XXX" and
        # "/server/XXX/" href formats at different times (site layout
        # changes) — match either instead of assuming one is permanent.
        if "server_" in href or "/server/" in href:
            return urljoin(base, href)
    return None


def _find_download_link(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Must be an actual file (workers.dev direct link ending in a real
        # media extension) — NOT another "/server/.../something.mkv.html"
        # page. That ".mkv" in the path is just part of the filename baked
        # into an intermediate page's URL, not proof it's the real file;
        # requiring the URL to actually END in the extension (rather than
        # just contain it anywhere) is what stops a page like that from
        # being mistaken for a downloadable file and handed to a
        # downloader, which fails with a cryptic HTTP-response error
        # instead of a clear "couldn't find a download link" message.
        if "workers.dev" in href and re.search(r"\.(mkv|mp4)(\?|$)", href, re.IGNORECASE):
            return href

    # Not found as a plain anchor — the "Start Download Now" button on some
    # server pages is now JS-driven (the workers.dev URL lives inside a
    # <script> block or a button's data-/onclick attribute instead of a
    # static <a href>), so BeautifulSoup's anchor-only scan above finds
    # nothing even though the link is still present as plain text
    # somewhere in the page source. Fall back to scanning the raw HTML
    # directly for the same URL shape, regardless of what tag it's in.
    m = re.search(r"https://[A-Za-z0-9\.\-]+\.workers\.dev/\S*?\.(?:mkv|mp4)(?:\?[^\"'\s<)]*)?", html, re.IGNORECASE)
    if m:
        return m.group(0)
    return None


async def _resolve_one_movie(session: aiohttp.ClientSession, base: str, movie_url: str, sem: asyncio.Semaphore):
    async with sem:
        try:
            movie_html, final_url = await _fetch_html(session, movie_url)
            base = _base_url(final_url)  # follow any mirror redirect for this page too
            server_link = _find_server_link(movie_html, base)
            if not server_link:
                return None

            server_html, _ = await _fetch_html(session, server_link)
            download_link = _find_download_link(server_html)
            if not download_link:
                return None

            filename = safe_filename(download_link.split("/")[-1].split("?")[0], "movie_file")
            return filename, download_link
        except Exception:
            # One dead/changed movie page shouldn't sink the whole batch —
            # same "skip and keep going" approach mediafire.py's folder
            # loop uses for individual file failures.
            return None


async def _scrape_category(url: str):
    """Returns a list of (filename, download_url), capped at MAX_MOVIES.
    Raises ValueError with a human-readable message on hard failures
    (page unreachable, bs4 missing, no movies found)."""
    if BeautifulSoup is None:
        raise ValueError("BeautifulSoup (bs4) isn't installed on this bot.")

    base = _base_url(url)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession() as session:
        category_html, final_url = await _fetch_html(session, url)
        base = _base_url(final_url)
        movie_urls = _movie_links(category_html, base)[:MAX_MOVIES]
        if not movie_urls:
            raise ValueError("No movies found on that category page — the page structure may have changed.")

        results = await asyncio.gather(*(
            _resolve_one_movie(session, base, m_url, sem) for m_url in movie_urls
        ))

    return [r for r in results if r]


def _links_text_chunks(files: list) -> list:
    """Splits the filename/link list into <=LINKS_CHUNK-char pieces instead
    of the original script's silent [:4096] truncation, so nothing gets
    dropped off a long category page."""
    chunks = []
    current = ""
    for filename, url in files:
        entry = f"<b>{filename}</b>\n<code>{url}</code>\n\n"
        if len(current) + len(entry) > LINKS_CHUNK and current:
            chunks.append(current)
            current = ""
        current += entry
    if current:
        chunks.append(current)
    return chunks


def _result_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        make_button("⬇️ ᴅᴏᴡɴʟᴏᴀᴅ & sᴇɴᴅ ᴀʟʟ", callback_data=f"fzdl#{session_id}", style=_BS.PRIMARY if _BS else None),
        make_button("❌ ᴅɪsᴍɪss", callback_data=f"fzcancel#{session_id}", style=_BS.DANGER if _BS else None),
    ]])


def _category_links(html: str, base: str) -> list:
    """Every distinct /category/ link reachable from the homepage — ported
    from the standalone TS crawler's getCategories(), which walks the
    homepage the same way rather than assuming a fixed list of category
    IDs (categories get added/renumbered on this kind of site)."""
    soup = BeautifulSoup(html, "html.parser")
    seen, links = set(), []
    for a in soup.select('a[href*="/category/"]'):
        href = a.get("href")
        if not href:
            continue
        full = urljoin(base, href)
        if full not in seen:
            seen.add(full)
            links.append(full)
    return links


def _movie_metadata(html: str, movie_url: str) -> dict:
    """Best-effort title/year/thumbnail/description for a movie page —
    the same fields the TS crawler records per movie. Kept best-effort
    (falls back to empty string) since /filmyzillascan's JSON export is a
    bonus data-collection feature, not something the resolve/download
    flow above depends on."""
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.select_one('a[href*="/movie/"]')
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title or title.lower() == "filmyzilla.com":
        server_tag = soup.select_one('a[href*="/server/"]')
        if server_tag:
            m = re.match(r"^(.+?)\s+\d+p", server_tag.get_text(strip=True), re.IGNORECASE)
            title = m.group(1).strip() if m else title

    id_match = re.search(r"/movie/(\d+)/", movie_url)
    year_match = re.search(r"(\d{4})", title)
    thumb_tag = soup.select_one('img[src*="poster"]')
    desc_tag = soup.select_one('meta[name="description"]')

    return {
        "id": id_match.group(1) if id_match else "",
        "title": title,
        "url": movie_url,
        "year": year_match.group(1) if year_match else "",
        "thumbnail": thumb_tag.get("src", "") if thumb_tag else "",
        "description": desc_tag.get("content", "") if desc_tag else "",
    }


def _quality_info(link_tag_text: str, size_text: str) -> tuple:
    """Parses quality/format/size out of a server-link's own visible text
    (e.g. '720p WEB-DL x264') plus its sibling size text — same three
    regexes the TS crawler used, since that site's link labels haven't
    changed shape."""
    quality = re.search(r"(\d+p|HEVC|HD|HDTC)", link_tag_text, re.IGNORECASE)
    fmt = re.search(r"\.(mkv|mp4|avi)", link_tag_text, re.IGNORECASE)
    size = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB)", size_text, re.IGNORECASE)
    return (
        quality.group(1) if quality else "Unknown",
        fmt.group(1).lower() if fmt else "mkv",
        size.group(0) if size else "Unknown",
    )


async def _scrape_movie_full(session: aiohttp.ClientSession, base: str, movie_url: str, sem: asyncio.Semaphore) -> dict:
    """Like _resolve_one_movie, but collects every server link on the
    movie page (with quality/format/size) instead of stopping at the
    first one, plus the movie metadata — the richer per-movie record
    /filmyzillascan writes to JSON, mirroring the TS crawler's Movie
    shape. Returns None if the movie page itself can't be read; a movie
    with zero resolvable server links still returns a record (links=[])
    rather than being dropped, so the JSON output reflects what's
    actually on the site."""
    async with sem:
        try:
            movie_html, final_url = await _fetch_html(session, movie_url)
            page_base = _base_url(final_url)
            record = _movie_metadata(movie_html, movie_url)

            soup = BeautifulSoup(movie_html, "html.parser")
            links = []
            for a in soup.select('a[href*="/server/"]'):
                href = a.get("href")
                if not href:
                    continue
                server_url = urljoin(page_base, href)
                quality, fmt, size = _quality_info(
                    a.get_text(" ", strip=True),
                    a.parent.get_text(" ", strip=True) if a.parent else "",
                )
                try:
                    server_html, _ = await _fetch_html(session, server_url)
                    download_url = _find_download_link(server_html) or "NOT_FOUND"
                except Exception:
                    download_url = "ERROR"

                links.append({
                    "quality": quality, "format": fmt, "size": size,
                    "serverUrl": server_url, "downloadUrl": download_url,
                })

            record["links"] = links
            return record
        except Exception:
            return None


async def _scrape_full_site(status, base: str, max_categories: int, max_movies: int) -> dict:
    """Walks homepage -> categories -> movies, same shape as the TS
    crawler's run(), but reporting progress via Telegram message edits
    instead of console.log and bounded by max_categories/max_movies so a
    single command can't run away indefinitely."""
    if BeautifulSoup is None:
        raise ValueError("BeautifulSoup (bs4) isn't installed on this bot.")

    sem = asyncio.Semaphore(CONCURRENCY)
    movies = []

    async with aiohttp.ClientSession() as session:
        home_html, final_url = await _fetch_html(session, base)
        base = _base_url(final_url)
        categories = _category_links(home_html, base)[:max_categories]
        if not categories:
            raise ValueError("No categories found on the homepage — the site layout may have changed.")

        for c_idx, category_url in enumerate(categories, start=1):
            try:
                category_html, cat_final_url = await _fetch_html(session, category_url)
            except Exception:
                continue  # one dead category shouldn't sink the whole scan
            cat_base = _base_url(cat_final_url)
            movie_urls = _movie_links(category_html, cat_base)[:max_movies]

            results = await asyncio.gather(*(
                _scrape_movie_full(session, cat_base, m_url, sem) for m_url in movie_urls
            ))
            for r in results:
                if r:
                    r["category"] = category_url.rstrip("/").split("/")[-1]
                    movies.append(r)

            if c_idx % 1 == 0:
                try:
                    await safe_edit(
                        status.edit_text,
                        f"<b>{E_INFO} Scanning...</b>\n"
                        f"Category {c_idx}/{len(categories)} — {len(movies)} movie(s) collected so far.",
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass

    total_links = sum(len(m["links"]) for m in movies)
    return {
        "totalMovies": len(movies),
        "totalCategories": len({m["category"] for m in movies}),
        "totalLinks": total_links,
        "categories": sorted({m["category"] for m in movies}),
        "movies": movies,
    }


async def _resolve_single(session: aiohttp.ClientSession, base: str, url: str):
    """Handles a URL that's already a single movie page or server page,
    instead of a category listing — returns (filename, download_url) or
    raises ValueError with a clear reason. Used when someone pastes/sends
    a specific movie's link directly rather than a /category/ page."""
    if "/server/" in url:
        server_html, _ = await _fetch_html(session, url)
        download_link = _find_download_link(server_html)
        if not download_link:
            raise ValueError(
                "Reached the server page but couldn't find a real download "
                "link on it (site layout may have changed, or this server "
                "option doesn't have a direct workers.dev link)."
            )
        filename = safe_filename(download_link.split("/")[-1].split("?")[0], "movie_file")
        return filename, download_link

    # A /movie/ page — one step further back than a server page.
    movie_html, final_url = await _fetch_html(session, url)
    base = _base_url(final_url)
    server_link = _find_server_link(movie_html, base)
    if not server_link:
        raise ValueError("Couldn't find a server/download link on that movie page.")
    return await _resolve_single(session, base, server_link)


async def _handle(client: Client, message: Message, url: str):
    base = _base_url(url)

    # A single movie/server link resolves to exactly one file — skip the
    # category-listing flow entirely instead of failing with "no movies
    # found" (that message is for actual /category/ pages, not this).
    if "/server/" in url or ("/movie/" in url and "/category/" not in url):
        status = await message.reply_text(f"<b>{E_INFO} Resolving link...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            async with aiohttp.ClientSession() as session:
                filename, download_link = await _resolve_single(session, base, url)
        except Exception as e:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

        await status.delete()
        session_id = uuid.uuid4().hex[:10]
        _FILMY_SESSIONS[session_id] = {"files": [(filename, download_link)], "message": message}
        await message.reply_text(
            f"<b>{E_ROCKET} Resolved:</b>\n<b>{filename}</b>\n<code>{download_link}</code>",
            reply_markup=_result_keyboard(session_id),
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
        )
        return

    status = await message.reply_text(f"<b>{E_INFO} Reading category page...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        files = await _scrape_category(url)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not files:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} No working links found.</b>\n"
            f"<i>Movie pages were reachable but none had a resolvable server/download link.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    await status.delete()
    chunks = _links_text_chunks(files)
    for chunk in chunks:
        await message.reply_text(chunk, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)

    session_id = uuid.uuid4().hex[:10]
    _FILMY_SESSIONS[session_id] = {"files": files, "message": message}
    await message.reply_text(
        f"<b>{E_ROCKET} Found {len(files)} file(s).</b>\n"
        f"<i>Links are above. Want them downloaded and sent as Telegram files instead?</i>",
        reply_markup=_result_keyboard(session_id),
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.text & filters.private & filters.regex(PATTERN), group=1)
async def filmyzilla_auto_detect(client: Client, message: Message):
    url = extract_url(message.text)
    if url:
        await _handle(client, message, url)


@Client.on_message(filters.command("filmyzilla") & filters.private)
async def filmyzilla_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/filmyzilla &lt;category page URL&gt;</code>\n"
            f"<i>Example: /filmyzilla https://www.filmyzilla52.com/category/398/2025-latest-bollywood-movies/default/1.html</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or _normalize_mirror(message.command[1])
    await _handle(client, message, url)


@Client.on_message(filters.command("filmyzillascan") & filters.private)
async def filmyzilla_scan_command(client: Client, message: Message):
    """/filmyzillascan [max_categories] [max_movies_per_category]
    Full-site sweep — homepage -> every category -> every movie in each,
    with quality/format/size per download link — exported as a single
    JSON file, the same data shape the standalone TS crawler produces.
    Unlike /filmyzilla (one category at a time, links posted as text),
    this is for someone who wants the whole catalog as structured data.
    """
    max_categories = SCAN_MAX_CATEGORIES
    max_movies = SCAN_MAX_MOVIES_PER_CATEGORY
    args = message.command[1:]
    try:
        if len(args) >= 1:
            max_categories = max(1, min(int(args[0]), 30))
        if len(args) >= 2:
            max_movies = max(1, min(int(args[1]), 30))
    except ValueError:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/filmyzillascan [max_categories] [max_movies_per_category]</code>\n"
            f"<i>Both optional integers. Default: {SCAN_MAX_CATEGORIES} categories x {SCAN_MAX_MOVIES_PER_CATEGORY} movies each.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(f"<b>{E_INFO} Starting full-site scan...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        data = await _scrape_full_site(status, f"https://{CURRENT_MIRROR}", max_categories, max_movies)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Scan failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not data["movies"]:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No movies found — nothing to export.</b>", parse_mode=enums.ParseMode.HTML)

    folder = make_output_folder("filmyzilla")
    out_path = f"{folder}/filmyzilla_data_{message.id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    await safe_edit(status.edit_text,
        f"<b>{E_CHECK} Scan complete</b>\n"
        f"{data['totalMovies']} movie(s), {data['totalCategories']} categor{'y' if data['totalCategories'] == 1 else 'ies'}, "
        f"{data['totalLinks']} link(s).\n<i>Uploading JSON...</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    await client.send_document(
        message.chat.id, out_path,
        file_name="filmyzilla_data.json",
        caption=f"<b>{E_ROCKET} FilmyZilla full-site scan</b>\n"
                f"{data['totalMovies']} movies • {data['totalCategories']} categories • {data['totalLinks']} links",
        parse_mode=enums.ParseMode.HTML,
    )
    await status.delete()


async def _download_one(client: Client, message: Message, status: Message, filename: str, url: str):
    if await try_send_cached(client, message, url, status, delete_status=False):
        return True
    try:
        folder = make_output_folder("filmyzilla")
        dest = f"{folder}/{message.id}_{filename}"
        await stream_download(url, dest, status, f"Downloading {filename}", user_id=message.from_user.id, file_name=filename)
        await upload_file(client, message, dest, status, f"<b>{E_CHECK} FilmyZilla File</b>\n<code>{filename}</code>", file_name=filename, cache_url=url, delete_status=False)
        return True
    except Exception as e:
        await message.reply_text(f"<b>{E_CROSS} Failed:</b> {filename}\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        return False


@Client.on_callback_query(filters.regex(r"^fzdl#"))
async def filmyzilla_download_all_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    session = _FILMY_SESSIONS.pop(session_id, None)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the category link again.</b>", parse_mode=enums.ParseMode.HTML)

    status = callback_query.message
    files = session["files"]
    total = len(files)
    done = failed = 0
    for i, (filename, url) in enumerate(files, start=1):
        try:
            await safe_edit(status.edit_text, 
                f"<b>{E_INFO} File {i}/{total}:</b> {filename}\n✅ {done}   ❌ {failed}",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass  # reused status message can't always be edited — don't abort the batch over it
        ok = await _download_one(client, session["message"], status, filename, url)
        done += 1 if ok else 0
        failed += 0 if ok else 1

    summary = f"<b>{E_CHECK} Done — {done}/{total} file(s) sent</b>" + (f", {failed} failed." if failed else ".")
    try:
        await safe_edit(status.edit_text, summary, parse_mode=enums.ParseMode.HTML)
    except Exception:
        await session["message"].reply_text(summary, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^fzcancel#"))
async def filmyzilla_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _FILMY_SESSIONS.pop(session_id, None)
    await callback_query.answer("Dismissed")
    await callback_query.message.delete()
