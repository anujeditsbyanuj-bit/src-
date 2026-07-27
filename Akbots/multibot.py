import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db
from config import API_ID, API_HASH

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'

# Cache of live Client instances spun up for users' own bot tokens.
# key: user_id -> pyrogram.Client (already .start()-ed)
_active_bots: dict[int, Client] = {}


async def get_user_bot(user_id: int) -> Client | None:
    """Return a running Client for this user's custom bot, starting it on
    first use. Returns None if the user has no custom bot configured, or
    if it fails to start (in which case the caller should fall back to the
    main bot)."""
    if user_id in _active_bots:
        return _active_bots[user_id]

    token = await db.get_custom_bot(user_id)
    if not token:
        return None

    try:
        session_name = f"custom_bot_{user_id}"
        cli = Client(session_name, api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
        await cli.start()
        _active_bots[user_id] = cli
        return cli
    except Exception:
        return None


async def stop_user_bot(user_id: int):
    cli = _active_bots.pop(user_id, None)
    if cli:
        try:
            await cli.stop()
        except Exception:
            pass


@Client.on_message(filters.command("setbot") & filters.private)
async def set_custom_bot(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/setbot &lt;bot_token&gt;</code>\n\n"
            f"<i>{E_INFO} Get a token from @BotFather. Files will then be delivered "
            f"through your own bot instead of this one.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    token = message.command[1].strip()
    user_id = message.from_user.id
    status = await message.reply_text(f"<b>{E_INFO} Verifying token...</b>", parse_mode=enums.ParseMode.HTML)

    await stop_user_bot(user_id)  # drop any previously cached instance for this user
    try:
        test_cli = Client(f"custom_bot_{user_id}", api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
        await test_cli.start()
        me = await test_cli.get_me()
        _active_bots[user_id] = test_cli
    except Exception as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Invalid bot token:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    await db.set_custom_bot(user_id, token)
    await status.edit_text(
        f"<b>{E_CHECK} Custom bot connected: @{me.username}</b>\n"
        f"<i>Your files will now be delivered through this bot.</i>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("rembot") & filters.private)
async def remove_custom_bot(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.get_custom_bot(user_id):
        return await message.reply_text(
            f"<b>{E_INFO} You don't have a custom bot set.</b>", parse_mode=enums.ParseMode.HTML
        )
    await stop_user_bot(user_id)
    await db.remove_custom_bot(user_id)
    await message.reply_text(
        f"<b>{E_CHECK} Custom bot removed.</b> Files will now be delivered through the main bot again.",
        parse_mode=enums.ParseMode.HTML
    )
