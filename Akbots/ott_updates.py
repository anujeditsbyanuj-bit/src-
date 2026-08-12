# Akbots
# OTT Updates — ported from OTT-Updater-mnbots (TMDB + JustWatch powered).
# Notifies subscribed users/channels when new titles land on Netflix, Prime,
# Hotstar, Disney+, and other streaming platforms in a configured region.
# Pure metadata (posters, ratings, "streaming on X") via TMDB's official API
# and JustWatch's public endpoints — no login, no stream extraction, no
# downloading of any platform's video content.
#
# Commands:
#   /ott [DD-MM-YYYY]   — browse OTT releases for a date (default: today)
#   /ottsearch <query>  — search TMDB, tap a result for OTT availability
#   /subscribe          — get new-release pings in PM
#   /unsubscribe        — stop them
#   /autosend on|off    — toggle this chat's scheduled push (PM/group)
#   /setchannel <id>    — register a channel/group for auto-updates (bot must be admin there)
#   /removechannel <id> — unregister it
#   /mychannels         — list registered auto-update targets
#   /platforms          — list supported platforms for the configured region
#
# Runs its own small Mongo collection set (via DB_URI, separate "ott_updates"
# database) so it doesn't touch the main bot's user collection.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import (
    TMDB_API_KEY, DB_URI, ADMINS,
    JUSTWATCH_COUNTRY, OTT_UPDATE_INTERVAL_HOURS, OTT_DB_NAME,
)

logger = logging.getLogger("Akbots.ott_updates")

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_W = "https://image.tmdb.org/t/p/w500"

OTT_PROVIDERS = {
    8: "Netflix", 9: "Amazon Prime", 337: "Disney+",
    122: "Hotstar", 2: "Apple TV+", 384: "Max",
    386: "Peacock", 531: "Paramount+", 283: "Crunchyroll",
    11: "MUBI", 15: "Hulu", 103: "MX Player",
    220: "ZEE5", 232: "SonyLIV", 31: "HBO",
    350: "Apple TV", 43: "Starz",
}

GENRE_MAP = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
    80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
    14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
    9648: "Mystery", 10749: "Romance", 878: "Sci-Fi", 53: "Thriller",
    10752: "War", 37: "Western",
    10759: "Action & Adventure", 10762: "Kids", 10763: "News",
    10764: "Reality", 10765: "Sci-Fi & Fantasy", 10766: "Soap",
    10767: "Talk", 10768: "War & Politics",
}

RESULTS_PER_PAGE = 5
DAY_LIST_LIMIT = 20
CACHE_TTL = 300

_sessions: dict[tuple[int, int], dict] = {}      # (bot identity, user_id) -> search session
# Keyed by (id(client), user_id), not bare user_id: the main bot and every
# Titanium clone (Akbots/titanium.py) run as separate Client instances in
# the SAME PYTHON PROCESS, so a bare user_id key would let one bot's
# search session clobber another's for the same owner. See
# Akbots/cookies_manager.py's _pk() for the original fix.


def _pk(client, user_id: int) -> tuple[int, int]:
    return (id(client), user_id)
_cache: dict[str, tuple] = {}        # simple TMDB response cache


# ═══════════════════════════════════════════════════════════════════════════
#  Own Mongo collections (separate DB so this plugin never touches the
#  main bot's `users` collection or schema)
# ═══════════════════════════════════════════════════════════════════════════

_client = None
_db = None


def _get_db():
    global _client, _db
    if _db is None:
        import motor.motor_asyncio
        _client = motor.motor_asyncio.AsyncIOMotorClient(DB_URI)
        _db = _client[OTT_DB_NAME]
    return _db


async def _ensure_indexes():
    db = _get_db()
    try:
        await db.subscribers.create_index("chat_id", unique=True)
        await db.sent_items.create_index("item_id", unique=True)
        await db.sent_items.create_index("sent_at", expireAfterSeconds=30 * 24 * 3600)
    except Exception as exc:
        logger.warning(f"OTT Updates: index setup failed (non-fatal): {exc}")


async def add_subscriber(chat_id: int, username: str = "") -> bool:
    db = _get_db()
    try:
        result = await db.subscribers.update_one(
            {"chat_id": chat_id},
            {"$setOnInsert": {
                "chat_id": chat_id,
                "username": username or "",
                "subscribed_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        return result.upserted_id is not None
    except Exception as exc:
        logger.error(f"add_subscriber error: {exc}")
        return False


async def remove_subscriber(chat_id: int) -> bool:
    db = _get_db()
    result = await db.subscribers.delete_one({"chat_id": chat_id})
    return result.deleted_count > 0


async def is_subscriber(chat_id: int) -> bool:
    db = _get_db()
    doc = await db.subscribers.find_one({"chat_id": chat_id}, {"_id": 1})
    return doc is not None


async def set_auto_send(chat_id: int, enabled: bool, username: str = "") -> None:
    db = _get_db()
    await db.subscribers.update_one(
        {"chat_id": chat_id},
        {"$set": {"auto_send": enabled},
         "$setOnInsert": {"username": username or "", "subscribed_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def get_auto_send_subscribers() -> List[int]:
    db = _get_db()
    cursor = db.subscribers.find(
        {"$or": [{"auto_send": {"$exists": False}}, {"auto_send": True}]},
        {"chat_id": 1, "_id": 0},
    )
    return [doc["chat_id"] async for doc in cursor]


async def is_item_sent(item_id: str) -> bool:
    db = _get_db()
    doc = await db.sent_items.find_one({"item_id": item_id}, {"_id": 1})
    return doc is not None


async def mark_item_sent(item_id: str, title: str = "") -> None:
    db = _get_db()
    await db.sent_items.update_one(
        {"item_id": item_id},
        {"$setOnInsert": {"item_id": item_id, "title": title, "sent_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  TMDB helpers (metadata + watch-provider info only)
# ═══════════════════════════════════════════════════════════════════════════

async def _tmdb(path: str, params: dict | None = None) -> Optional[dict]:
    if not TMDB_API_KEY:
        return None
    p = dict(params or {})
    p["api_key"] = TMDB_API_KEY

    cache_key = path + "|" + "&".join(f"{k}={v}" for k, v in sorted(p.items()) if k != "api_key")
    now = asyncio.get_event_loop().time()
    if cache_key in _cache:
        data, exp = _cache[cache_key]
        if now < exp:
            return data

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"{TMDB_BASE}{path}", params=p,
                                 timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status == 200:
                    data = await r.json()
                    _cache[cache_key] = (data, now + CACHE_TTL)
                    return data
                logger.warning(f"TMDB {path} → HTTP {r.status}")
    except asyncio.TimeoutError:
        logger.warning(f"TMDB {path} timed out")
    except Exception as exc:
        logger.error(f"TMDB {path} error: {exc}")
    return None


def _poster(path: str | None) -> str:
    return f"{TMDB_IMG_W}{path}" if path else ""


async def fetch_day_releases(for_date: date) -> list[dict]:
    iso = for_date.isoformat()
    base = {
        "sort_by": "popularity.desc",
        "watch_region": JUSTWATCH_COUNTRY,
        "with_watch_monetization_types": "flatrate",
        "page": 1,
    }
    movies_raw, tv_raw = await asyncio.gather(
        _tmdb("/discover/movie", {**base, "primary_release_date.gte": iso, "primary_release_date.lte": iso}),
        _tmdb("/discover/tv", {**base, "first_air_date.gte": iso, "first_air_date.lte": iso}),
    )
    items: list[dict] = []
    for m in (movies_raw or {}).get("results", []):
        items.append({
            "id": m["id"], "type": "movie", "title": m.get("title", "Unknown"),
            "year": (m.get("primary_release_date") or "")[:4],
            "rating": round(m.get("vote_average", 0), 1), "poster": _poster(m.get("poster_path")),
        })
    for t in (tv_raw or {}).get("results", []):
        items.append({
            "id": t["id"], "type": "tv", "title": t.get("name", "Unknown"),
            "year": (t.get("first_air_date") or "")[:4],
            "rating": round(t.get("vote_average", 0), 1), "poster": _poster(t.get("poster_path")),
        })
    items.sort(key=lambda x: -x["rating"])
    return items


async def fetch_detail(item_type: str, item_id: int) -> Optional[dict]:
    path = f"/movie/{item_id}" if item_type == "movie" else f"/tv/{item_id}"
    raw = await _tmdb(path, {"append_to_response": "watch/providers,credits"})
    if not raw:
        return None

    prov_data = raw.get("watch/providers", {}).get("results", {})
    country_prov = prov_data.get(JUSTWATCH_COUNTRY, {})
    providers = [p["provider_name"] for p in country_prov.get("flatrate", [])]
    rent = [p["provider_name"] for p in country_prov.get("rent", [])]
    buy = [p["provider_name"] for p in country_prov.get("buy", [])]
    cast = [c["name"] for c in raw.get("credits", {}).get("cast", [])[:5]]

    title = raw.get("title") or raw.get("name", "Unknown")
    release = raw.get("release_date") or raw.get("first_air_date", "")

    return {
        "id": item_id, "type": item_type, "title": title,
        "year": release[:4] if release else "", "release_date": release,
        "status": raw.get("status", ""), "rating": round(raw.get("vote_average", 0), 1),
        "vote_count": raw.get("vote_count", 0), "poster": _poster(raw.get("poster_path")),
        "overview": raw.get("overview", ""), "genres": [g["name"] for g in raw.get("genres", [])],
        "cast": cast, "runtime": raw.get("runtime") or (raw.get("episode_run_time") or [None])[0],
        "seasons": raw.get("number_of_seasons"), "episodes": raw.get("number_of_episodes"),
        "providers": providers, "rent": rent, "buy": buy,
        "tmdb_url": f"https://www.themoviedb.org/{'movie' if item_type=='movie' else 'tv'}/{item_id}",
    }


async def search_tmdb(query: str, page: int = 1) -> tuple[list[dict], int]:
    raw = await _tmdb("/search/multi", {"query": query, "page": page, "include_adult": "false"})
    if not raw:
        return [], 0
    results = []
    for r in raw.get("results", []):
        mt = r.get("media_type")
        if mt not in ("movie", "tv"):
            continue
        title = r.get("title") or r.get("name", "Unknown")
        year_raw = r.get("release_date") or r.get("first_air_date", "")
        results.append({
            "id": r["id"], "type": mt, "title": title,
            "year": year_raw[:4] if year_raw else "N/A",
            "rating": round(r.get("vote_average", 0), 1), "poster": _poster(r.get("poster_path")),
        })
    return results, raw.get("total_pages", 1)


async def _tmdb_providers_for(media_type: str, tmdb_id: int) -> List[str]:
    data = await _tmdb(f"/{media_type}/{tmdb_id}/watch/providers", {})
    if not data:
        return []
    region = data.get("results", {}).get(JUSTWATCH_COUNTRY, {})
    seen: list[str] = []
    for cat in ("flatrate", "free", "ads"):
        for p in region.get(cat, []):
            name = OT_PROVIDERS_NAME(p)
            if name and name not in seen:
                seen.append(name)
    return seen


def OT_PROVIDERS_NAME(p: dict) -> str:
    return OTT_PROVIDERS.get(p.get("provider_id", 0)) or p.get("provider_name", "")


async def get_new_releases(days_back: int = 7, dedup: bool = True) -> List[Dict[str, Any]]:
    """New titles (last `days_back` days) with confirmed streaming availability."""
    if not TMDB_API_KEY:
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results: List[Dict[str, Any]] = []

    for mt in ("movie", "tv"):
        date_field = "primary_release_date" if mt == "movie" else "first_air_date"
        data = await _tmdb(f"/discover/{mt}", {
            "sort_by": "popularity.desc",
            "with_watch_monetization_types": "flatrate|free|ads",
            "watch_region": JUSTWATCH_COUNTRY,
            f"{date_field}.gte": since,
            f"{date_field}.lte": today,
            "page": 1,
        })
        if not data:
            continue
        items = data.get("results", [])
        provider_results = await asyncio.gather(
            *[_tmdb_providers_for(mt, it["id"]) for it in items],
            return_exceptions=True,
        )
        for item, providers in zip(items, provider_results):
            if isinstance(providers, Exception) or not providers:
                continue
            item_id = f"tmdb_{mt}_{item['id']}"
            if dedup and await is_item_sent(item_id):
                continue
            title = item.get("title") or item.get("name") or "Unknown"
            rel_date = item.get("release_date") or item.get("first_air_date") or ""
            if dedup:
                await mark_item_sent(item_id, title)
            results.append({
                "id": item["id"], "item_id": item_id, "type": mt, "title": title,
                "release_date": rel_date, "overview": (item.get("overview") or "No description.")[:600],
                "providers": providers, "poster": _poster(item.get("poster_path")),
                "rating": round(item.get("vote_average", 0), 1), "genre_ids": item.get("genre_ids", []),
            })
    return results


def format_item_message(item: Dict[str, Any]) -> str:
    emoji = "🎬" if item["type"] == "movie" else "📺"
    kind = "Movie" if item["type"] == "movie" else "TV Series"
    title = item["title"]
    rel = item.get("release_date") or "N/A"
    overview = item.get("overview", "")
    rating = item.get("rating", 0)
    provs = " • ".join(item.get("providers", [])) or "Check JustWatch"
    genres = [GENRE_MAP[g] for g in item.get("genre_ids", []) if g in GENRE_MAP]
    genre_s = " | ".join(genres[:3]) if genres else ""

    tmdb_id = item.get("id", "")
    mpath = "movie" if item["type"] == "movie" else "tv"
    country = JUSTWATCH_COUNTRY.lower()
    tmdb_url = f"https://www.themoviedb.org/{mpath}/{tmdb_id}" if tmdb_id else ""
    jw_url = f"https://www.justwatch.com/{country}/{mpath}"

    lines = [f"{emoji} <b>{title}</b>  <code>[{kind}]</code>", f"📅 <b>Released:</b> {rel}"]
    if rating:
        lines.append(f"⭐ <b>Rating:</b> {rating}/10")
    if genre_s:
        lines.append(f"🎭 <b>Genre:</b> {genre_s}")
    lines.append(f"📡 <b>Available on:</b> {provs}")
    lines.append("")
    lines.append(overview)
    if tmdb_url:
        lines.append("")
        lines.append(f'<a href="{tmdb_url}">📖 TMDB</a>  |  <a href="{jw_url}">🍿 JustWatch</a>')
    return "\n".join(lines)


def format_item_keyboard(item: Dict[str, Any]) -> InlineKeyboardMarkup:
    mpath = "movie" if item["type"] == "movie" else "tv"
    country = JUSTWATCH_COUNTRY.lower()
    tmdb_id = item.get("id", "")
    tmdb_url = f"https://www.themoviedb.org/{mpath}/{tmdb_id}" if tmdb_id else None
    jw_url = f"https://www.justwatch.com/{country}/{mpath}"
    buttons = [[InlineKeyboardButton("▶️ Open JustWatch", url=jw_url)]]
    if tmdb_url:
        buttons.append([InlineKeyboardButton("🎬 Open TMDB", url=tmdb_url)])
    return InlineKeyboardMarkup(buttons)


# ═══════════════════════════════════════════════════════════════════════════
#  Formatting helpers
# ═══════════════════════════════════════════════════════════════════════════

def _fmt_date_label(d: date) -> str:
    today = date.today()
    if d == today:
        return f"📅 Today  —  {d.strftime('%d %b %Y')}"
    if d == today - timedelta(days=1):
        return f"📅 Yesterday  —  {d.strftime('%d %b %Y')}"
    if d == today + timedelta(days=1):
        return f"📅 Tomorrow  —  {d.strftime('%d %b %Y')}"
    return f"📅 {d.strftime('%d %b %Y')}"


def _parse_user_date(s: str) -> Optional[date]:
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _icon(item_type: str) -> str:
    return "🎬" if item_type == "movie" else "📺"


def _format_detail_text(d: dict) -> str:
    lines = [f"{_icon(d['type'])} <b>{d['title']}</b>"]
    if d.get("release_date"):
        lines.append(f"📅 <b>{'Release' if d['type']=='movie' else 'First Air'}:</b> {d['release_date']}")
    if d.get("rating"):
        bar = "★" * round(d["rating"] / 2) + "☆" * (5 - round(d["rating"] / 2))
        lines.append(f"⭐ <b>Rating:</b> {d['rating']}/10  <code>{bar}</code>  ({d['vote_count']:,} votes)")
    if d.get("genres"):
        lines.append(f"🎭 <b>Genres:</b> {' · '.join(d['genres'])}")
    if d.get("runtime"):
        h, m = divmod(d["runtime"], 60)
        lines.append(f"⏱ <b>Runtime:</b> {f'{h}h {m}m' if h else f'{m}m'}")
    if d.get("seasons"):
        lines.append(f"📺 <b>Seasons:</b> {d['seasons']}  |  <b>Episodes:</b> {d.get('episodes','?')}")
    if d.get("status"):
        lines.append(f"🔖 <b>Status:</b> {d['status']}")
    if d.get("cast"):
        lines.append(f"🎭 <b>Cast:</b> {', '.join(d['cast'])}")
    lines.append("")

    if d.get("providers"):
        lines.append(f"✅ <b>Streaming on:</b>  {'  ·  '.join(d['providers'])}")
    elif d.get("rent") or d.get("buy"):
        lines.append(f"🛒 <b>Rent/Buy on:</b>  {'  ·  '.join((d.get('rent') or []) + (d.get('buy') or []))}")
        lines.append("❌ Not on subscription streaming in your region.")
    else:
        year = int(d.get("year") or 0)
        if year and year < 2024:
            lines.append("❌ <b>OTT:</b> Not currently available for streaming in your region.")
        else:
            lines.append("❌ <b>OTT:</b> Not yet released on any streaming platform.")

    lines.append("")
    if d.get("overview"):
        ov = d["overview"]
        lines.append(f"📝 <i>{ov[:597] + '…' if len(ov) > 600 else ov}</i>")
    return "\n".join(lines)


def _day_keyboard(items: list[dict], for_date: date) -> InlineKeyboardMarkup:
    rows = []
    iso = for_date.isoformat()
    for item in items[:DAY_LIST_LIMIT]:
        t = "m" if item["type"] == "movie" else "t"
        cb = f"ott_it|{t}|{item['id']}|{iso}"
        lbl = f"{_icon(item['type'])} {item['title']}"
        if item.get("year"):
            lbl += f" ({item['year']})"
        if item.get("rating"):
            lbl += f" ⭐{item['rating']}"
        rows.append([InlineKeyboardButton(lbl[:60], callback_data=cb)])
    prev_iso = (for_date - timedelta(days=1)).isoformat()
    next_iso = (for_date + timedelta(days=1)).isoformat()
    rows.append([
        InlineKeyboardButton("⬅️ Prev Day", callback_data=f"ott_nav|{prev_iso}"),
        InlineKeyboardButton("Next Day ➡️", callback_data=f"ott_nav|{next_iso}"),
    ])
    return InlineKeyboardMarkup(rows)


def _detail_keyboard(item_type: str, item_id: int, back_date: str = "",
                      from_search: bool = False, uid: int = 0) -> InlineKeyboardMarkup:
    t = "m" if item_type == "movie" else "t"
    rows = []
    if back_date:
        rows.append([InlineKeyboardButton("⬅️ Back to List", callback_data=f"ott_nav|{back_date}")])
    if from_search and uid:
        rows.append([InlineKeyboardButton("🔙 Back to Search", callback_data=f"ott_sr|{uid}")])
    rows.append([InlineKeyboardButton(
        "🔄 Refresh", callback_data=f"ott_pk|{t}|{item_id}|{'s' if from_search else 'd'}|{uid}"
    )])
    return InlineKeyboardMarkup(rows)


def _search_keyboard(uid: int, session: dict) -> InlineKeyboardMarkup:
    results, page = session["results"], session["page"]
    start = page * RESULTS_PER_PAGE
    end = min(start + RESULTS_PER_PAGE, len(results))
    total_pg = max(1, -(-len(results) // RESULTS_PER_PAGE))

    rows = []
    for item in results[start:end]:
        t = "m" if item["type"] == "movie" else "t"
        lbl = f"{_icon(item['type'])} {item['title']} ({item['year']})"
        if item.get("rating"):
            lbl += f" ⭐{item['rating']}"
        rows.append([InlineKeyboardButton(lbl[:60], callback_data=f"ott_pk|{t}|{item['id']}|s|{uid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ott_sp|{uid}|prev"))
    nav.append(InlineKeyboardButton(f"📄 {page+1}/{total_pg}", callback_data="ott_noop"))
    if end < len(results) or session.get("tmdb_page", 1) < session.get("tmdb_total", 1):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"ott_sp|{uid}|next"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _build_search_msg(client, uid: int) -> tuple[str, InlineKeyboardMarkup]:
    session = _sessions[_pk(client, uid)]
    total = len(session["results"])
    page = session["page"]
    start = page * RESULTS_PER_PAGE
    end = min(start + RESULTS_PER_PAGE, total)
    text = (
        f"🔍 <b>Results for:</b>  <code>{session['query']}</code>\n"
        f"Showing <b>{start+1}–{end}</b> of {total}+ results\n\n"
        f"Tap a title to see OTT info:"
    )
    return text, _search_keyboard(uid, session)


# ═══════════════════════════════════════════════════════════════════════════
#  Send helpers
# ═══════════════════════════════════════════════════════════════════════════

async def _send_day(client: Client, target, for_date: date, edit: bool):
    items = await fetch_day_releases(for_date)
    label = _fmt_date_label(for_date)
    mc = sum(1 for i in items if i["type"] == "movie")
    tc = sum(1 for i in items if i["type"] == "tv")

    text = (f"{label}\n\n😔 No OTT releases found for this date." if not items else
            f"{label}\n🎬 {mc} Movies  ·  📺 {tc} Series\n\nTap a title for full details:")
    kb = _day_keyboard(items, for_date)

    if edit:
        try:
            await target.edit_message_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except Exception:
            await target.message.reply(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
    else:
        await target.reply(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)


async def _send_detail(client: Client, target, detail: dict, kb: InlineKeyboardMarkup, edit: bool):
    text = _format_detail_text(detail)
    if detail.get("poster"):
        try:
            if edit:
                try:
                    await target.message.delete()
                except Exception:
                    pass
                await client.send_photo(target.message.chat.id, photo=detail["poster"],
                                         caption=text[:1024], parse_mode=enums.ParseMode.HTML, reply_markup=kb)
            else:
                await target.reply_photo(photo=detail["poster"], caption=text[:1024],
                                          parse_mode=enums.ParseMode.HTML, reply_markup=kb)
            return
        except Exception as exc:
            logger.warning(f"Photo send failed, falling back to text: {exc}")

    if edit:
        try:
            await target.edit_message_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            return
        except Exception:
            pass
    await (target.message if hasattr(target, "message") else target).reply(
        text, reply_markup=kb, parse_mode=enums.ParseMode.HTML
    )


# ═══════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command("ott"))
async def cmd_ott(client: Client, message: Message):
    """/ott [DD-MM-YYYY] — OTT releases for a date (default: today)."""
    if not TMDB_API_KEY:
        await message.reply("❌ TMDB_API_KEY is not configured. Ask the admin to add it.")
        return

    args = message.command[1:]
    if args:
        for_date = _parse_user_date(args[0])
        if not for_date:
            await message.reply(
                "❌ <b>Invalid date format.</b>\nUse: <code>/ott DD-MM-YYYY</code>\n"
                "Example: <code>/ott 13-05-2025</code>", parse_mode=enums.ParseMode.HTML)
            return
    else:
        for_date = date.today()

    loading = await message.reply("⏳ Fetching releases…")
    try:
        await _send_day(client, loading, for_date, edit=True)
    except Exception as exc:
        logger.error(f"cmd_ott error: {exc}")
        await loading.edit_text("❌ Something went wrong. Please try again.")


@Client.on_message(filters.command("ottsearch"))
async def cmd_ott_search(client: Client, message: Message):
    """/ottsearch <query> — search TMDB, tap a result for OTT availability."""
    if not TMDB_API_KEY:
        await message.reply("❌ TMDB_API_KEY is not configured. Ask the admin to add it.")
        return

    query_str = " ".join(message.command[1:]).strip()
    if len(query_str) < 2:
        await message.reply("Usage: <code>/ottsearch Squid Game</code>", parse_mode=enums.ParseMode.HTML)
        return

    loading = await message.reply(f"🔍 Searching for <b>{query_str}</b>…", parse_mode=enums.ParseMode.HTML)
    results, total_pages = await search_tmdb(query_str, page=1)
    if not results:
        await loading.edit_text(
            f"❌ No results found for <b>{query_str}</b>.\nTry a different spelling or include the release year.",
            parse_mode=enums.ParseMode.HTML)
        return

    uid = message.from_user.id
    _sessions[_pk(client, uid)] = {"query": query_str, "results": results, "page": 0,
                       "tmdb_page": 1, "tmdb_total": total_pages}
    text, kb = _build_search_msg(client, uid)
    await loading.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("subscribe"))
async def subscribe_cmd(client: Client, message: Message):
    user = message.from_user
    chat_id, username = message.chat.id, (getattr(user, "username", "") or "")
    if await is_subscriber(chat_id):
        await message.reply(
            "✅ You're <b>already subscribed!</b>\nYou'll receive updates automatically.\n\nUse /unsubscribe to stop.",
            parse_mode=enums.ParseMode.HTML)
        return
    await add_subscriber(chat_id, username)
    await message.reply(
        "✅ <b>Subscribed!</b>\n\nYou'll be notified whenever new content lands on OTT platforms.\n\n"
        "Use /unsubscribe to stop at any time.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("unsubscribe"))
async def unsubscribe_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not await is_subscriber(chat_id):
        await message.reply("ℹ️ You are <b>not subscribed</b>.\nUse /subscribe to start.",
                             parse_mode=enums.ParseMode.HTML)
        return
    await remove_subscriber(chat_id)
    await message.reply(
        "❌ <b>Unsubscribed.</b>\n\nYou won't receive automatic updates anymore.\n"
        "Use /subscribe to re-enable at any time.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("autosend"))
async def autosend_cmd(client: Client, message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or parts[1].lower() not in {"on", "off"}:
        await message.reply("Usage: /autosend on|off")
        return
    enabled = parts[1].lower() == "on"
    await set_auto_send(message.chat.id, enabled, getattr(message.from_user, "username", "") or "")
    await message.reply(f"✅ Auto-send {'enabled' if enabled else 'disabled'} for this chat.")


@Client.on_message(filters.command("platforms"))
async def platforms_cmd(client: Client, message: Message):
    await message.reply(
        f"📡 <b>Supported OTT Platforms</b>\n<i>Region: <code>{JUSTWATCH_COUNTRY}</code></i>\n\n"
        "Netflix, Prime Video, Disney+, Hotstar, Apple TV+, Max, Hulu, Peacock, Paramount+, "
        "SonyLIV, ZEE5, MX Player, Crunchyroll, MUBI, Starz",
        parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("setchannel") & filters.user(ADMINS))
async def cmd_set_channel(client: Client, message: Message):
    """/setchannel @username | -100xxxxxxxxxx — bot must already be admin there."""
    args = message.command[1:]
    if not args:
        await message.reply(
            "📢 <b>Add Auto-Update Channel / Group</b>\n\nUsage:\n"
            "  <code>/setchannel @yourchannel</code>\n  <code>/setchannel -1001234567890</code>\n\n"
            "⚠️ The bot must be an <b>admin</b> of the channel/group first.", parse_mode=enums.ParseMode.HTML)
        return

    target_id = args[0].strip()
    try:
        chat = await client.get_chat(target_id)
    except Exception as exc:
        await message.reply(f"❌ Cannot find that chat:\n<code>{exc}</code>", parse_mode=enums.ParseMode.HTML)
        return

    try:
        me = await client.get_me()
        member = await client.get_chat_member(chat.id, me.id)
        if member.status not in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER):
            await message.reply(f"❌ I'm not an admin in <b>{chat.title}</b>.\nPlease promote me and try again.",
                                 parse_mode=enums.ParseMode.HTML)
            return
    except Exception as exc:
        await message.reply(f"❌ Could not verify admin status:\n<code>{exc}</code>", parse_mode=enums.ParseMode.HTML)
        return

    added = await add_subscriber(chat.id, username=chat.username or "")
    await set_auto_send(chat.id, True)
    verb = "added" if added else "already registered"
    await message.reply(f"✅ <b>{chat.title}</b> has been {verb} for auto-updates.\n🆔 Chat ID: <code>{chat.id}</code>",
                         parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("removechannel") & filters.user(ADMINS))
async def cmd_remove_channel(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply("Usage: <code>/removechannel @yourchannel</code>", parse_mode=enums.ParseMode.HTML)
        return
    target_id = args[0].strip()
    try:
        chat = await client.get_chat(target_id)
    except Exception as exc:
        await message.reply(f"❌ Cannot find that chat:\n<code>{exc}</code>", parse_mode=enums.ParseMode.HTML)
        return
    removed = await remove_subscriber(chat.id)
    if removed:
        await message.reply(f"✅ <b>{chat.title}</b> removed from auto-update targets.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply(f"ℹ️ <b>{chat.title}</b> was not in the list.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("mychannels") & filters.user(ADMINS))
async def cmd_my_channels(client: Client, message: Message):
    ids = await get_auto_send_subscribers()
    if not ids:
        await message.reply("📭 No channels or groups registered for auto-updates yet.")
        return
    lines = ["📢 <b>Registered Auto-Update Targets:</b>\n"]
    for cid in ids:
        try:
            chat = await client.get_chat(cid)
            handle = f"@{chat.username}" if chat.username else f"ID: <code>{cid}</code>"
            lines.append(f"• {chat.title}  ({handle})")
        except Exception:
            lines.append(f"• Unknown  (<code>{cid}</code>)")
    lines.append(f"\nTotal: {len(ids)}")
    await message.reply("\n".join(lines), parse_mode=enums.ParseMode.HTML)


# ═══════════════════════════════════════════════════════════════════════════
#  CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════

@Client.on_callback_query(filters.regex(r"^ott_nav\|"))
async def cb_nav(client: Client, query: CallbackQuery):
    iso = query.data.split("|", 1)[1]
    try:
        for_date = date.fromisoformat(iso)
    except ValueError:
        await query.answer("❌ Invalid date.", show_alert=True)
        return
    await query.answer()
    await _send_day(client, query, for_date, edit=True)


@Client.on_callback_query(filters.regex(r"^ott_it\|"))
async def cb_item(client: Client, query: CallbackQuery):
    parts = query.data.split("|")
    t, item_id_s, back_iso = parts[1], parts[2], parts[3]
    item_type = "movie" if t == "m" else "tv"
    await query.answer("⏳ Loading…")
    detail = await fetch_detail(item_type, int(item_id_s))
    if not detail:
        await query.answer("❌ Failed to fetch details.", show_alert=True)
        return
    kb = _detail_keyboard(item_type, detail["id"], back_date=back_iso)
    await _send_detail(client, query, detail, kb, edit=True)


@Client.on_callback_query(filters.regex(r"^ott_pk\|"))
async def cb_pick(client: Client, query: CallbackQuery):
    parts = query.data.split("|")
    t, item_id_s = parts[1], parts[2]
    source = parts[3] if len(parts) > 3 else "d"
    uid = int(parts[4]) if len(parts) > 4 else query.from_user.id
    item_type = "movie" if t == "m" else "tv"
    await query.answer("⏳ Loading…")
    detail = await fetch_detail(item_type, int(item_id_s))
    if not detail:
        await query.answer("❌ Failed to fetch details.", show_alert=True)
        return
    kb = _detail_keyboard(item_type, detail["id"], from_search=(source == "s"), uid=uid)
    await _send_detail(client, query, detail, kb, edit=True)


@Client.on_callback_query(filters.regex(r"^ott_sr\|"))
async def cb_search_restore(client: Client, query: CallbackQuery):
    uid = int(query.data.split("|", 1)[1])
    session = _sessions.get(_pk(client, uid))
    if not session:
        await query.answer("⏳ Session expired. Please search again.", show_alert=True)
        return
    await query.answer()
    text, kb = _build_search_msg(client, uid)
    await query.edit_message_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^ott_sp\|"))
async def cb_search_page(client: Client, query: CallbackQuery):
    parts = query.data.split("|")
    uid, direction = int(parts[1]), parts[2]
    session = _sessions.get(_pk(client, uid))
    if not session:
        await query.answer("⏳ Session expired. Please search again.", show_alert=True)
        return

    results, cur_page = session["results"], session["page"]
    if direction == "next":
        new_page = cur_page + 1
        if new_page * RESULTS_PER_PAGE >= len(results):
            next_tmdb = session["tmdb_page"] + 1
            if next_tmdb <= session["tmdb_total"]:
                await query.answer("⏳ Loading more…")
                more, _ = await search_tmdb(session["query"], page=next_tmdb)
                if more:
                    session["results"].extend(more)
                    session["tmdb_page"] = next_tmdb
            else:
                await query.answer("No more results.", show_alert=False)
                return
        session["page"] = min(new_page, -(-len(session["results"]) // RESULTS_PER_PAGE) - 1)
    elif direction == "prev":
        if cur_page == 0:
            await query.answer()
            return
        session["page"] = cur_page - 1

    await query.answer()
    text, kb = _build_search_msg(client, uid)
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^ott_noop$"))
async def cb_noop(client: Client, query: CallbackQuery):
    await query.answer()


# ═══════════════════════════════════════════════════════════════════════════
#  Scheduler — periodic new-release push to subscribers
# ═══════════════════════════════════════════════════════════════════════════

_scheduler = None


async def _push_new_releases(app: Client):
    if not TMDB_API_KEY:
        return
    try:
        items = await get_new_releases(days_back=1, dedup=True)
    except Exception as exc:
        logger.error(f"OTT Updates: fetch failed: {exc}")
        return
    if not items:
        return

    targets = await get_auto_send_subscribers()
    if not targets:
        return

    for item in items:
        text = format_item_message(item)
        kb = format_item_keyboard(item)
        for chat_id in targets:
            try:
                if item.get("poster"):
                    await app.send_photo(chat_id, photo=item["poster"], caption=text[:1024],
                                          parse_mode=enums.ParseMode.HTML, reply_markup=kb)
                else:
                    await app.send_message(chat_id, text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)
            except Exception as exc:
                logger.warning(f"OTT Updates: push to {chat_id} failed: {exc}")
            await asyncio.sleep(0.3)  # gentle flood-wait avoidance


def schedule_ott_updates(app: Client):
    """Call once from Bot.start(). No-op if TMDB_API_KEY isn't set or apscheduler is missing."""
    global _scheduler
    if not TMDB_API_KEY:
        logger.info("OTT Updates disabled (TMDB_API_KEY not set).")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning("OTT Updates enabled but apscheduler isn't installed.")
        return

    asyncio.create_task(_ensure_indexes())

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(_push_new_releases, IntervalTrigger(hours=OTT_UPDATE_INTERVAL_HOURS), args=[app])
    _scheduler.start()
    logger.info(f"OTT Updates scheduler started (every {OTT_UPDATE_INTERVAL_HOURS}h, region={JUSTWATCH_COUNTRY}).")
