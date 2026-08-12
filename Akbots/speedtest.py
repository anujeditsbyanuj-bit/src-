import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    import speedtest
except ImportError:
    speedtest = None

E_ROCKET = '<tg-emoji emoji-id="5456140674028019486">🚀</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'


def _speed_fmt(bits_per_sec: float) -> str:
    size = bits_per_sec / 8  # bits -> bytes
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    idx = 0
    while size > 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{round(size, 2)} {units[idx]}"


def _run_speedtest() -> dict:
    st = speedtest.Speedtest()
    st.get_best_server()
    st.download()
    st.upload()
    return st.results.dict()


@Client.on_message(filters.command(["speedtest", "speed"]) & filters.private)
async def speedtest_command(client: Client, message: Message):
    if speedtest is None:
        return await message.reply_text(
            f"<b>{E_CROSS} Speedtest module not installed.</b>\n"
            f"<i>Run <code>pip install speedtest-cli</code> on the host to enable this.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(
        f"<b>{E_ROCKET} Running speed test... please wait.</b>", parse_mode=enums.ParseMode.HTML
    )

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _run_speedtest)
    except Exception as e:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Speed test failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    ping_val = result.get('ping', 0) or 0
    # speedtest-cli occasionally reports a bogus multi-minute "ping" (a
    # leaked socket-timeout value, not a real latency) on hosts where
    # ICMP is firewalled off, e.g. most Google Cloud VMs. A real ping is
    # never in the tens-of-thousands of ms, so cap what we display rather
    # than showing something like "1800000.0 ms" as if it were real.
    ping_display = f"{ping_val:.1f} ms" if ping_val < 5000 else "ɴ/ᴀ (ɪᴄᴍᴘ ʙʟᴏᴄᴋᴇᴅ ᴏɴ ᴛʜɪs ʜᴏsᴛ)"

    text = (
        f"<b>{E_ROCKET} Speedtest Results</b>\n\n"
        f"📥 <b>ᴅᴏᴡɴʟᴏᴀᴅ:</b> <code>{_speed_fmt(result['download'])}</code>\n"
        f"📡 <b>ᴜᴘʟᴏᴀᴅ:</b> <code>{_speed_fmt(result['upload'])}</code>\n"
        f"📍 <b>ᴘɪɴɢ:</b> <code>{ping_display}</code>\n\n"
        f"🌍 <b>sᴇʀᴠᴇʀ:</b> {result['server']['sponsor']} ({result['server']['name']}, {result['server']['country']})\n"
        f"👤 <b>ɪsᴘ:</b> {result['client']['isp']}"
    )
    buttons = InlineKeyboardMarkup([[make_button("🔗 ᴠɪᴇᴡ ғᴜʟʟ ʀᴇᴘᴏʀᴛ", url=result["share"], style=_BS.PRIMARY if _BS else None)]]) \
        if result.get("share") else None

    await safe_edit(status.edit_text, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
