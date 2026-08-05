"""
File-to-Link streaming/download engine.

Ported in from the standalone FILE-TO-LINK-BOT project and adapted to run
inside this bot's own Pyrogram Client (no second bot/token needed) and its
own MongoDB connection. See config.py's "File-to-Link Streamer" block for
the knobs (STREAM_BIN_CHANNEL, STREAM_PORT, STREAM_URL, STREAM_LINK_EXPIRY).
"""

import time

StartTime = time.time()
__version__ = "1.0.0"

# Work-load table (kept as a dict, same shape the upstream multi-client
# version used). Grows to one entry per extra client if STREAM_EXTRA_TOKENS
# is configured (see config.py / web_server.py).
work_loads = {0: 0}

# index -> Pyrogram Client. Index 0 is always the bot's own client
# (STREAM_EXTRA_TOKENS adds 1, 2, 3, ...). Populated in web_server.py.
multi_clients = {}

# Cache of ByteStreamer instances keyed by Client — populated lazily by
# stream_routes.py the first time a request comes in.
class_cache = {}

# The running bot Client, set once at startup by bot.py via set_client().
BOT = None


def set_client(client):
    global BOT
    BOT = client
