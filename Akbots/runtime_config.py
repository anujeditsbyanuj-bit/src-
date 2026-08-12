# Akbots - Don't Remove Credit - @AkBots_Official
#
# Runtime-settable API keys / tokens.
#
# Every optional key in config.py (OPENAI_API_KEY, GROQ_API_KEY,
# GEMINI_API_KEY, GOFILE_TOKEN, TMDB_API_KEY, GIT_TOKEN, ...) normally only
# comes from an env var — if it's missing you have to add it on
# Render/Replit's secrets panel and redeploy. This module lets an admin set
# (or replace) ANY of them straight from the bot with /setkey, including
# tokens that aren't wired into any plugin yet (e.g. a Grok key you're
# saving for later).
#
# How it works:
#   - Values are stored in MongoDB via db.get_fs_config/set_fs_config (the
#     same generic settings store Akbots already uses elsewhere), so they
#     survive restarts/redeploys.
#   - On every /setkey, the new value is (a) written to config.<ATTR> in
#     place, and (b) pushed into every already-imported plugin module that
#     did `from config import ATTR` at load time — so it applies instantly,
#     no bot restart needed.
#   - On bot startup, apply_saved_keys_to_config() (called from bot.py
#     before plugins load) pre-loads every saved override into config.*,
#     so even the destructured imports pick up the right value from the
#     very first import.
#
# Add a new service later by adding one line to KEY_MAP (and, only if that
# module does `from config import THE_NAME`, one entry to MODULE_USAGE).
# Anything not in KEY_MAP still works — /setkey <any-name> <value> is saved
# and can be read back with get_key("<any-name>") for a future plugin.

import sys

import config
from database.db import db

# /setkey name -> config.py attribute it overrides.
KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "grok": "GROK_API_KEY",
    "gofile": "GOFILE_TOKEN",
    "imgbb": "IMGBB_API_KEY",
    "tmdb": "TMDB_API_KEY",
    "mxplayer": "MXPLAYER_API_KEY",
    "git": "GIT_TOKEN",
    "kartoon": "MEOWTOON_KARTOON_TOKEN",
    "shortener": "FILESTORE_SHORTENER_API_TOKEN",
    "spotify_id": "SPOTIFY_CLIENT_ID",
    "spotify_secret": "SPOTIFY_CLIENT_SECRET",
    "terabox_ndus": "TERABOX_NDUS",
    "heroku": "HEROKU_API",
    "jd_email": "JD_EMAIL",
    "jd_pass": "JD_PASS",
    "mega_email": "MEGA_EMAIL",
    "mega_password": "MEGA_PASSWORD",
    "aria2_secret": "ARIA2_RPC_SECRET",
    "moctale_cookie": "MOCTALE_COOKIE",
    "meowverse_secret": "MEOWVERSE_SECRET_KEY_ENCRYPTED",
    "meowverse_des_key": "MEOWVERSE_DES_KEY",
    "meowverse_des_iv": "MEOWVERSE_DES_IV",
    "meowverse_aes_key": "MEOWVERSE_AES_KEY",
    "meowverse_aes_iv": "MEOWVERSE_AES_IV",
    "meowverse_ws_secret": "MEOWVERSE_WS_SECRET",
    "meowverse_p2p_salt": "MEOWVERSE_P2P_SALT",
}

# Names whose live-patch alone isn't enough — an external process also
# needs to be restarted/reconnected before the new value actually takes
# effect. Shown as a note in /setkey's reply.
NEEDS_RESTART_NOTE = {
    "aria2_secret": "aria2c itself was launched with the old secret — restart the bot (which restarts aria2c) for this to fully take effect.",
}

# config.py attribute -> plugin modules that imported it by name
# (`from config import ATTR`) and therefore need a live patch on change.
# Modules that instead do `import config` + `config.ATTR` pick up changes
# automatically and don't need to be listed here.
MODULE_USAGE = {
    "OPENAI_API_KEY": ["Akbots.coach", "Akbots.openai_chat", "Akbots.aiulta"],
    "OPENROUTER_API_KEY": [
        "Akbots.enhance", "Akbots.arena", "Akbots.coach",
        "Akbots.summarizer", "Akbots.openai_chat", "Akbots.aiulta",
    ],
    "GROQ_API_KEY": ["Akbots.chatbot", "Akbots.groq_chat"],
    "GEMINI_API_KEY": ["Akbots.gemini_chat"],
    "GOFILE_TOKEN": ["Akbots.gofile"],
    "IMGBB_API_KEY": ["Akbots.imgtolink"],
    "TMDB_API_KEY": ["Akbots.movieinfo", "Akbots.autopost"],
    "MXPLAYER_API_KEY": ["Akbots.mxplayer"],
    "GIT_TOKEN": ["Akbots.github_accounts"],
    "FILESTORE_SHORTENER_API_TOKEN": ["Akbots.urlshortener", "Akbots.filestore"],
    "SPOTIFY_CLIENT_ID": ["Akbots.spotify"],
    "SPOTIFY_CLIENT_SECRET": ["Akbots.spotify"],
    "TERABOX_NDUS": ["Akbots.terabox_lib"],
    "HEROKU_API": ["Akbots.heroku_tools"],
    "JD_EMAIL": ["Akbots.jdownloader_core"],
    "JD_PASS": ["Akbots.jdownloader_core"],
    "MEGA_EMAIL": ["Akbots.mega"],
    "MEGA_PASSWORD": ["Akbots.mega"],
    "ARIA2_RPC_SECRET": ["Akbots.aria2_rpc"],
    # MOCTALE_COOKIE is re-imported fresh from config inside the function
    # that uses it (not snapshotted at module load), so no live-patch entry
    # is needed — setattr(config, ...) alone is enough.
    "MEOWVERSE_SECRET_KEY_ENCRYPTED": ["Akbots.meowverse_provider"],
    "MEOWVERSE_DES_KEY": ["Akbots.meowverse_provider"],
    "MEOWVERSE_DES_IV": ["Akbots.meowverse_provider"],
    "MEOWVERSE_AES_KEY": ["Akbots.meowverse_provider"],
    "MEOWVERSE_AES_IV": ["Akbots.meowverse_provider"],
    "MEOWVERSE_WS_SECRET": ["Akbots.meowverse_provider"],
    "MEOWVERSE_P2P_SALT": ["Akbots.meowverse_provider"],
}

_CACHE: dict[str, str] = {}

# Snapshot of each attribute's original value (env var / built-in default)
# taken the moment this module is first imported — i.e. before any /setkey
# override has been applied. /delkey restores this, not an empty string.
_ORIGINAL_DEFAULTS: dict[str, str] = {
    attr: getattr(config, attr, "") for attr in set(KEY_MAP.values())
}


def _db_key(name: str) -> str:
    return f"apikey_{name.lower()}"


def _live_patch(name: str, value: str):
    """Push a new value into config.py and every already-loaded plugin
    module that snapshotted it at import time, so it applies without a
    restart."""
    attr = KEY_MAP.get(name)
    if not attr:
        return
    setattr(config, attr, value)
    for mod_name in MODULE_USAGE.get(attr, []):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            try:
                setattr(mod, attr, value)
            except Exception:
                pass


async def set_key(name: str, value: str) -> None:
    name = name.strip().lower()
    value = value.strip()
    await db.set_fs_config(_db_key(name), value)
    _CACHE[name] = value
    _live_patch(name, value)


def restart_note(name: str) -> str:
    return NEEDS_RESTART_NOTE.get(name.strip().lower(), "")


async def del_key(name: str) -> None:
    """Remove the DB override for `name`, reverting it to whatever the
    env var / config.py default originally was (not to blank)."""
    name = name.strip().lower()
    await db.set_fs_config(_db_key(name), "")
    _CACHE.pop(name, None)
    attr = KEY_MAP.get(name)
    if attr:
        setattr(config, attr, _ORIGINAL_DEFAULTS.get(attr, ""))
        for mod_name in MODULE_USAGE.get(attr, []):
            mod = sys.modules.get(mod_name)
            if mod is not None:
                try:
                    setattr(mod, attr, _ORIGINAL_DEFAULTS.get(attr, ""))
                except Exception:
                    pass


async def get_key(name: str) -> str:
    """Effective value for `name`: DB override if one is set, else
    whatever config.py currently holds (env var or built-in default)."""
    name = name.strip().lower()
    if name in _CACHE:
        return _CACHE[name]
    val = await db.get_fs_config(_db_key(name))
    if val:
        _CACHE[name] = val
        return val
    attr = KEY_MAP.get(name)
    if attr:
        return getattr(config, attr, "") or ""
    return ""


async def list_keys() -> dict:
    """{name: (value_or_empty, source)} for every known name, source is
    'db' (set via /setkey), 'env/default' (from config.py), or 'unset'."""
    out = {}
    for name in KEY_MAP:
        db_val = _CACHE.get(name)
        if db_val is None:
            db_val = await db.get_fs_config(_db_key(name))
            if db_val:
                _CACHE[name] = db_val
        if db_val:
            out[name] = (db_val, "db")
            continue
        attr = KEY_MAP[name]
        env_val = getattr(config, attr, "") or ""
        out[name] = (env_val, "env/default") if env_val else ("", "unset")
    return out


async def apply_saved_keys_to_config() -> int:
    """Call once at bot startup, BEFORE plugins are loaded (i.e. before
    Client.start()), so every DB-saved override is already sitting on
    config.* by the time plugin modules do `from config import ATTR`.
    Returns how many overrides were applied."""
    applied = 0
    for name, attr in KEY_MAP.items():
        val = await db.get_fs_config(_db_key(name))
        if val:
            setattr(config, attr, val)
            _CACHE[name] = val
            applied += 1
    return applied


def known_names():
    return list(KEY_MAP.keys())


def mask(value: str) -> str:
    if not value:
        return "—"
    if len(value) <= 10:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"
