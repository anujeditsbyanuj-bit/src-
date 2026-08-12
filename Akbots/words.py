# Akbots - Don't Remove Credit - @AkBots_Official

from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN   = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_PENCIL = '<tg-emoji emoji-id="5395444784611480792">✏️</tg-emoji>'
E_TRASH  = '<tg-emoji emoji-id="5260293700088511294">🗑</tg-emoji>'
E_TIP    = '<tg-emoji emoji-id="5422439311196834318">💡</tg-emoji>'
E_BOLT   = '<tg-emoji emoji-id="5456140674028019486">⚡️</tg-emoji>'

@Client.on_message(filters.command("set_del_word") & filters.private)
async def set_del_word(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>ᴜsᴀɢᴇ:</b> <code>/set_del_word word1 word2 ...</code>\n\n"
            f"{E_TIP} These words will be automatically removed from captions and filenames.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    words = message.command[1:]
    await db.set_delete_words(message.from_user.id, words)
    await message.reply_text(
        f"<b>{E_CHECK} Added <code>{len(words)}</code> words to delete list.</b>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("rem_del_word") & filters.private)
async def rem_del_word(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/rem_del_word word1 word2 ...</code>",
            parse_mode=enums.ParseMode.HTML
        )
    words = message.command[1:]
    await db.remove_delete_words(message.from_user.id, words)
    await message.reply_text(
        f"<b>{E_TRASH} Removed <code>{len(words)}</code> words from delete list.</b>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("set_repl_word") & filters.private)
async def set_repl_word(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>ᴜsᴀɢᴇ:</b> <code>/set_repl_word target replacement</code>\n\n"
            f"{E_TIP} Example: <code>/set_repl_word @OldChannel @NewChannel</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    target = message.command[1]
    replacement = message.command[2]
    await db.set_replace_words(message.from_user.id, {target: replacement})
    await message.reply_text(
        f"<b>{E_PENCIL} Replacement set:</b> <code>{target}</code> {E_BOLT} <code>{replacement}</code>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("rem_repl_word") & filters.private)
async def rem_repl_word(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/rem_repl_word target</code>",
            parse_mode=enums.ParseMode.HTML
        )
    target = message.command[1]
    await db.remove_replace_words(message.from_user.id, [target])
    await message.reply_text(
        f"<b>{E_TRASH} Removed replacement for:</b> <code>{target}</code>",
        parse_mode=enums.ParseMode.HTML
    )
