import logging

from aiohttp import web
from pyrogram import Client

from config import API_ID, API_HASH, STREAM_EXTRA_TOKENS, STREAM_SPEEDTEST_MAX_MB
from . import set_client, multi_clients, work_loads
from .stream_routes import routes
from .speedtest_routes import routes as speedtest_routes

logger = logging.getLogger(__name__)


def build_app() -> web.Application:
    # client_max_size must cover the biggest possible request body, which
    # is now the /speedtest/upload probe (STREAM_SPEEDTEST_MAX_MB) rather
    # than the flat 30MB this used to be hardcoded to — regular stream/
    # download requests have no body of their own, so this only affects
    # upload-style POSTs.
    max_size = max(30_000_000, STREAM_SPEEDTEST_MAX_MB * 1024 * 1024)
    app = web.Application(client_max_size=max_size)
    # speedtest_routes MUST be added before `routes` — stream_routes.py ends
    # with a catch-all `/{path:\S+}` handler that would otherwise swallow
    # `/speedtest/*` requests before they ever reach speedtest_routes.py.
    app.add_routes(speedtest_routes)
    app.add_routes(routes)
    return app


async def _start_extra_clients():
    """Starts one lightweight Pyrogram Client per STREAM_EXTRA_TOKENS
    entry, purely to spread streaming/download load across more Telegram
    API sessions. These clients don't run any plugins or handle updates —
    they're only ever used inside stream_routes.py to fetch file bytes."""
    for i, token in enumerate(STREAM_EXTRA_TOKENS, start=1):
        try:
            client = Client(
                name=f"ftl_extra_{i}",
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=token,
                in_memory=True,
                no_updates=True,
            )
            await client.start()
            multi_clients[i] = client
            work_loads[i] = 0
            logger.info(f"File-to-Link extra streaming client {i} started.")
        except Exception as e:
            logger.warning(f"File-to-Link extra streaming client {i} failed to start: {e}")


async def start_stream_server(bot, port: int):
    """
    Starts the file-to-link aiohttp server bound to 0.0.0.0:<port>, using
    `bot` (the running Pyrogram Client) - plus any STREAM_EXTRA_TOKENS
    clients - to fetch/stream files from STREAM_BIN_CHANNEL. Returns the
    aiohttp.web.AppRunner so the caller can keep a reference (mirrors how
    Akbots/mediainfo_lib/streamer.py is wired into bot.py).
    """
    set_client(bot)
    multi_clients[0] = bot
    work_loads[0] = 0

    try:
        me = await bot.get_me()
        bot._ftl_me_username = me.username
    except Exception:
        pass

    if STREAM_EXTRA_TOKENS:
        await _start_extra_clients()
        if len(multi_clients) > 1:
            logger.info(f"File-to-Link multi-client streaming enabled ({len(multi_clients)} clients).")

    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
