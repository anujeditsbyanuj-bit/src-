# Akbots - Don't Remove Credit - @AkBots_Official
#
# /makepost — Premium Emoji Post Maker, ported from the standalone
# "Flexy_premium_test_bot" (pyTelegramBotAPI) into this bot's own
# pyrogram/kurigram plugin system. Converts any Unicode emoji the user
# types into Telegram Premium animated custom emoji, lets them attach
# media + up to 4 coloured inline buttons, preview/reroll, and (admins
# only) broadcast the finished post to every user in the DB.
#
# Wired in as a normal Akbots plugin — auto-loaded by bot.py's
# plugins=dict(root="Akbots"), same as every other command here.

import html
import random
import asyncio

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from config import ADMINS
from database.db import db
from Akbots.direct_utils import wait_for_reply
from Akbots.settings import make_button, BUTTON_STYLE_SUPPORTED

try:
    from pyrogram.enums import ButtonStyle
except ImportError:
    ButtonStyle = None

E_INFO  = '<tg-emoji emoji-id="5334544901428229844">ℹ️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_SPARK = '<tg-emoji emoji-id="5422439311196834318">✨</tg-emoji>'

_P = ButtonStyle.PRIMARY if BUTTON_STYLE_SUPPORTED else None
_D = ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None
_S = ButtonStyle.SECONDARY if BUTTON_STYLE_SUPPORTED else None

# =====================================================================
# Emoji -> Premium (custom) emoji ID mapping — ported as-is from Flexy
# =====================================================================
EMOJI_MAPPING = {
    "✅": ["6246537187614005254", "6246782404476803545", "6010060634803148161", "6010498532488778300"],
    "✔️": ["6246871001062185760", "6010264538375525668", "6010487760710800947"],
    "☑️": ["6246537187614005254", "6010097953773983121"],
    "👁️": ["6035338338406242050", "6035051267087143217", "6034945975963881533", "6034845323405299835"],
    "👁": ["6035338338406242050", "6035051267087143217"],
    "👀": ["6035225389356290238", "6035081585261287115", "6035243995154616907", "6035173858338672933"],
    "🔥": ["4956222745814762495", "4956606007221421405", "4956429969396859866", "6086954744268460848"],
    "💥": ["6032673796530377389", "4958479549265347295"],
    "⚡": ["5791970059597386804", "6087079590377820415", "6095843123252957701"],
    "❤️": ["5783157259152397008", "5801084710343938087", "6010280773351904888"],
    "💙": ["5780496071645991525", "6104780447684757396"],
    "💚": ["5888789252493283486"],
    "💛": ["5840261097719148872"],
    "🧡": ["5840263144212529797"],
    "💜": ["5840265018655703965"],
    "🖤": ["5840266939932994956"],
    "⭐": ["6244496562752331516", "5904618938578243567", "6010193314932855525"],
    "🌟": ["6010156854955480259", "6086924086791902713"],
    "✨": ["6010338729640596556", "6010086134023985536", "5801044672658805468"],
    "🧛": ["6034871295072539452", "6035251193519805118", "6032673796530377389"],
    "🧛‍♂️": ["6034871295072539452", "6035251193519805118"],
    "👹": ["6034962795055812935"],
    "👺": ["6034962795055812935"],
    "👻": ["6035070298087231243"],
    "👿": ["6035242444671421879", "6032985916098750553"],
    "😈": ["6035136809950778133", "6032695825417638128", "6032739101508113500"],
    "👑": ["5794422335599546668", "6089003761496232797", "6247039939305808563"],
    "💰": ["6089104607328342288", "6086730718774300509", "6086664791026307819"],
    "💵": ["6089140105233044310"],
    "💎": ["6086778246882399112", "5791697221799907788"],
    "👍": ["6089313931149448495", "4958626617535497157", "4956582500865410174"],
    "👎": ["6088789257285988672"],
    "👏": ["6093744967304352336", "4956582500865410174"],
    "😀": ["6093864814071780526", "6093922327978840798"],
    "😁": ["6035060329468137931"],
    "😂": ["5782741660936966676", "5782746664573867142"],
    "😃": ["6035337951859184840"],
    "😄": ["5782942227319756256"],
    "😅": ["5782670102486848559"],
    "😆": ["5782670102486848559"],
    "😉": ["6089024570612781324"],
    "😊": ["5780690182692935276"],
    "😍": ["6010179687001625256"],
    "🥰": ["6044369013952222465", "6044359320211034681"],
    "😘": ["6044373012566774137"],
    "😎": ["6032853480782172520", "6044373012566774137"],
    "😢": ["5780793884678296697"],
    "😭": ["5783024321324651865"],
    "😤": ["6034865170449175739", "6034855438053282213"],
    "😠": ["6035355642829475999", "6034843326245508065"],
    "😡": ["6035355642829475999"],
    "🤔": ["5782756916660802905", "5783034045130610245", "6093666528316625608"],
}

FLAG_MAPPING = {
    "🇺🇸": "5433865586356531140", "🇬🇧": "5433827537241258614", "🇫🇷": "5433636707549331311",
    "🇩🇪": "5433845881046578644", "🇮🇳": "5433601609076586221", "🇯🇵": "5434147542369579483",
    "🇨🇳": "5435996255207567113", "🇷🇺": "5433674924168328689", "🇧🇷": "5433825269498525925",
    "🇮🇹": "5433627189901801019", "🇨🇦": "5433979415874779870", "🇦🇺": "5434067655977874913",
    "🇰🇷": "5434142701941437163", "🇪🇸": "5434026158003862063", "🇲🇽": "5434131139889478358",
    "🇮🇩": "5431739800883312139", "🇳🇱": "5431656358258685474", "🇹🇷": "5433792911214917126",
    "🇸🇦": "5433991338703991663", "🇦🇪": "5434013938821902926", "🇿🇦": "5431489619038320862",
    "🇵🇰": "5434064563601421981", "🇧🇩": "5433854239052935880", "🇱🇰": "5433609855413794108",
    "🇳🇵": "5433852744404317916", "🇲🇾": "5431620340662940910", "🇸🇬": "5433884376838454074",
    "🇵🇭": "5434119663736862995", "🇻🇳": "5431676201007592926", "🇹🇭": "5433814347396692144",
    "🇪🇬": "5433643519367461444", "🇳🇬": "5433982207603520017",
}

PRIMARY_EMOJIS = [
    "6035051267087143217", "6034945975963881533", "6034845323405299835", "6035169816774446606",
    "6035085583875837709", "6032965553658794901", "6035158121578501544", "6035208832257364215",
    "6035067476293718178", "6033130342964007608", "6035179291472302298", "6034986056598688136",
    "6032765485492214347", "6032660275973330342", "6034916516783198293", "6034904439335162652",
    "6034928023000585140", "6035372904303038740", "6035137110598492010", "6035338338406242050",
    "6035225389356290238", "6035081585261287115", "6035243995154616907", "6034865170449175739",
    "6035173858338672933", "6035210301136182368", "6035265083444042235", "6034871295072539452",
    "6035251193519805118", "6035136809950778133", "6032695825417638128", "6032739101508113500",
    "6032985916098750553", "6035374291577475270", "6035355642829475999", "6035337951859184840",
    "6035072209347678547", "6035060329468137931", "6033077437556855182", "6032823763903452409",
    "6034853694296560978", "6035015146412183834", "6035372401791864953", "6034955549445984368",
    "6032673796530377389", "6032916496542339992", "6034855438053282213", "6034962795055812935",
    "6034832094906028632", "6035087164423802534", "6035343380697846690", "6032737138708059114",
    "6035194237958493530", "6035317340311129897", "6035070298087231243", "6035242444671421879",
    "5791970059597386804", "5794422335599546668",
]

ALL_PREMIUM_EMOJIS = list(set(PRIMARY_EMOJIS + [
    "6246537187614005254", "6246610665914505571", "6244496562752331516", "6246782404476803545",
    "6247039939305808563", "6246774261218810895", "6246871001062185760",
    "5780840497958360623", "5780413823022273797", "5782940582347281850", "5783091623462180025",
    "5783151611270403662", "5783124312458270318", "5782741660936966676", "5782753386197685582",
    "6084695058894819673", "6086730718774300509", "6086664791026307819", "6089003761496232797",
    "4956222745814762495", "4958617898751886363", "4958479549265347295", "4958624886663678191",
]))

# =====================================================================
# Emoji -> premium HTML conversion (regex-free, unicode-range based —
# no extra "emoji" pip dependency needed, this repo doesn't have it)
# =====================================================================
_REGIONAL_LO, _REGIONAL_HI = 0x1F1E6, 0x1F1FF
_SKIN_TONE_LO, _SKIN_TONE_HI = 0x1F3FB, 0x1F3FF
_VARIATION_SELECTORS = ("\ufe0f", "\ufe0e")
_ZWJ = "\u200d"


def _is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x2190 <= cp <= 0x21FF
        or 0x2B00 <= cp <= 0x2BFF
        or 0x1F000 <= cp <= 0x1F0FF
        or _REGIONAL_LO <= cp <= _REGIONAL_HI
        or cp == 0x2764
    )


def _split_emoji_runs(text: str):
    """Yield (is_emoji, fragment) pairs that reconstruct `text` in order."""
    chars = list(text)
    i, n = 0, len(chars)
    buf = ""
    while i < n:
        ch = chars[i]
        if i + 1 < n and _REGIONAL_LO <= ord(ch) <= _REGIONAL_HI and _REGIONAL_LO <= ord(chars[i + 1]) <= _REGIONAL_HI:
            if buf:
                yield False, buf
                buf = ""
            yield True, ch + chars[i + 1]
            i += 2
            continue
        if _is_emoji_char(ch):
            if buf:
                yield False, buf
                buf = ""
            seq = ch
            j = i + 1
            while j < n:
                c2 = chars[j]
                if c2 in _VARIATION_SELECTORS or _SKIN_TONE_LO <= ord(c2) <= _SKIN_TONE_HI:
                    seq += c2
                    j += 1
                elif c2 == _ZWJ and j + 1 < n and _is_emoji_char(chars[j + 1]):
                    seq += c2 + chars[j + 1]
                    j += 2
                else:
                    break
            yield True, seq
            i = j
            continue
        buf += ch
        i += 1
    if buf:
        yield False, buf


def _get_premium_id(fragment: str) -> str:
    if fragment in FLAG_MAPPING:
        return FLAG_MAPPING[fragment]
    if fragment in EMOJI_MAPPING:
        return random.choice(EMOJI_MAPPING[fragment])
    stripped = fragment.replace("\ufe0f", "").replace("\ufe0e", "").replace("\u200d", "")
    if stripped in FLAG_MAPPING:
        return FLAG_MAPPING[stripped]
    if stripped in EMOJI_MAPPING:
        return random.choice(EMOJI_MAPPING[stripped])
    return random.choice(ALL_PREMIUM_EMOJIS)


def to_premium_html(text: str) -> str:
    """Turn plain text with normal emoji into HTML with Telegram Premium
    custom-emoji tags, ready for parse_mode=HTML."""
    out = []
    for is_emoji, frag in _split_emoji_runs(text):
        out.append(f'<tg-emoji emoji-id="{_get_premium_id(frag)}">{frag}</tg-emoji>' if is_emoji else html.escape(frag))
    return "".join(out)


# =====================================================================
# Conversation state + flow
# =====================================================================
SESSIONS: dict = {}


def _cancelled(reply: Message) -> bool:
    return bool(reply.text) and reply.text.strip() == "/cancel"


@Client.on_message(filters.command(["makepost", "emojipost"]) & filters.private)
async def makepost_cmd(client: Client, message: Message):
    uid = message.from_user.id
    SESSIONS[uid] = {"text": "", "media_type": None, "media_id": None, "buttons": []}
    await message.reply_text(
        f"<blockquote>{E_SPARK} <b>ᴄʀᴇᴀᴛᴇ ᴘʀᴇᴍɪᴜᴍ ᴇᴍᴏᴊɪ ᴘᴏsᴛ</b>\n\n"
        f"{E_INFO} Send the text for your post now (any emoji you type gets "
        f"converted to Telegram Premium animated emoji). Send /cancel to stop.</blockquote>",
        parse_mode=enums.ParseMode.HTML,
        quote=True,
    )
    try:
        reply = await wait_for_reply(client, message.chat.id, uid, timeout=180)
    except asyncio.TimeoutError:
        SESSIONS.pop(uid, None)
        return await message.reply_text(f"{E_CROSS} Timed out. Run /makepost again.")
    if _cancelled(reply):
        SESSIONS.pop(uid, None)
        return await reply.reply_text(f"{E_CROSS} Cancelled.")
    SESSIONS[uid]["text"] = reply.text or reply.caption or ""
    await _ask_media(client, message.chat.id, uid)


async def _ask_media(client: Client, chat_id: int, uid: int):
    kb = InlineKeyboardMarkup([
        [make_button("🖼 Photo", callback_data=f"pep_media:photo:{uid}", style=_P),
         make_button("🎬 Video", callback_data=f"pep_media:video:{uid}", style=_P)],
        [make_button("📄 Document", callback_data=f"pep_media:doc:{uid}", style=_P),
         make_button("⏭ Skip", callback_data=f"pep_media:skip:{uid}", style=_D)],
    ])
    await client.send_message(chat_id, f"{E_INFO} Add media to this post?", reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^pep_media:(photo|video|doc|skip):(\d+)$"))
async def pep_media_cb(client: Client, cq: CallbackQuery):
    action, uid = cq.data.split(":")[1], int(cq.data.split(":")[2])
    if cq.from_user.id != uid or uid not in SESSIONS:
        return await cq.answer("Session expired — run /makepost again.", show_alert=True)
    await cq.answer()
    try:
        await cq.message.delete()
    except Exception:
        pass

    if action == "skip":
        SESSIONS[uid]["media_type"] = None
        return await _ask_button_count(client, cq.message.chat.id, uid)

    await client.send_message(cq.message.chat.id, f"{E_INFO} Send the {action} now (or /cancel).", parse_mode=enums.ParseMode.HTML)
    try:
        reply = await wait_for_reply(client, cq.message.chat.id, uid, timeout=180)
    except asyncio.TimeoutError:
        SESSIONS.pop(uid, None)
        return await client.send_message(cq.message.chat.id, f"{E_CROSS} Timed out.")
    if _cancelled(reply):
        SESSIONS.pop(uid, None)
        return await reply.reply_text(f"{E_CROSS} Cancelled.")

    media_id = None
    if action == "photo" and reply.photo:
        media_id = reply.photo.file_id
    elif action == "video" and reply.video:
        media_id = reply.video.file_id
    elif action == "doc" and reply.document:
        media_id = reply.document.file_id

    if not media_id:
        return await client.send_message(cq.message.chat.id, f"{E_CROSS} That wasn't a valid {action}. Run /makepost again.")

    SESSIONS[uid]["media_type"] = action
    SESSIONS[uid]["media_id"] = media_id
    await _ask_button_count(client, cq.message.chat.id, uid)


async def _ask_button_count(client: Client, chat_id: int, uid: int):
    rows = [
        [make_button(str(n), callback_data=f"pep_btncount:{n}:{uid}", style=_P) for n in (1, 2)],
        [make_button(str(n), callback_data=f"pep_btncount:{n}:{uid}", style=_P) for n in (3, 4)],
        [make_button("🚫 No buttons", callback_data=f"pep_btncount:0:{uid}", style=_D)],
    ]
    await client.send_message(
        chat_id, f"{E_INFO} Add inline buttons? Choose how many (up to 4).",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex(r"^pep_btncount:(\d+):(\d+)$"))
async def pep_btncount_cb(client: Client, cq: CallbackQuery):
    count, uid = int(cq.data.split(":")[1]), int(cq.data.split(":")[2])
    if cq.from_user.id != uid or uid not in SESSIONS:
        return await cq.answer("Session expired — run /makepost again.", show_alert=True)
    await cq.answer()
    try:
        await cq.message.delete()
    except Exception:
        pass

    SESSIONS[uid]["buttons"] = []
    if count == 0:
        return await _create_preview(client, cq.message.chat.id, uid)
    await _ask_button_details(client, cq.message.chat.id, uid, target=count)


async def _ask_button_details(client: Client, chat_id: int, uid: int, target: int):
    session = SESSIONS.get(uid)
    if not session:
        return
    current = len(session["buttons"]) + 1

    await client.send_message(chat_id, f"{E_INFO} Button {current}/{target} — send its label text.", parse_mode=enums.ParseMode.HTML)
    try:
        reply = await wait_for_reply(client, chat_id, uid, timeout=180)
    except asyncio.TimeoutError:
        SESSIONS.pop(uid, None)
        return await client.send_message(chat_id, f"{E_CROSS} Timed out.")
    if _cancelled(reply):
        SESSIONS.pop(uid, None)
        return await reply.reply_text(f"{E_CROSS} Cancelled.")

    label = (reply.text or "").strip()[:30]
    if not label:
        return await _ask_button_details(client, chat_id, uid, target)

    await client.send_message(
        chat_id, f"{E_INFO} Now send the URL for \"{html.escape(label)}\" (must start with http://, https:// or tg://).",
        parse_mode=enums.ParseMode.HTML
    )
    try:
        reply2 = await wait_for_reply(client, chat_id, uid, timeout=180)
    except asyncio.TimeoutError:
        SESSIONS.pop(uid, None)
        return await client.send_message(chat_id, f"{E_CROSS} Timed out.")
    if _cancelled(reply2):
        SESSIONS.pop(uid, None)
        return await reply2.reply_text(f"{E_CROSS} Cancelled.")

    url = (reply2.text or "").strip()
    if not url.startswith(("http://", "https://", "tg://")):
        await client.send_message(chat_id, f"{E_CROSS} Invalid URL, try again.")
        return await _ask_button_details(client, chat_id, uid, target)

    session["buttons"].append({"label": label, "url": url})
    if len(session["buttons"]) < target:
        return await _ask_button_details(client, chat_id, uid, target)
    await _create_preview(client, chat_id, uid)


def _build_buttons(session: dict):
    if not session["buttons"]:
        return None
    colors = [_P, _D, _S] if BUTTON_STYLE_SUPPORTED else [None]
    rows = []
    for idx, btn in enumerate(session["buttons"]):
        style = colors[idx % len(colors)]
        icon = random.choice(PRIMARY_EMOJIS)
        rows.append([make_button(btn["label"], url=btn["url"], icon_custom_emoji_id=icon, style=style)])
    return InlineKeyboardMarkup(rows)


async def _create_preview(client: Client, chat_id: int, uid: int):
    session = SESSIONS.get(uid)
    if not session:
        return

    body_html = to_premium_html(session["text"]) if session["text"] else f"{E_SPARK} Your post"
    reply_markup = _build_buttons(session)

    try:
        if session["media_type"] == "photo" and session.get("media_id"):
            sent = await client.send_photo(chat_id, session["media_id"], caption=body_html, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)
        elif session["media_type"] == "video" and session.get("media_id"):
            sent = await client.send_video(chat_id, session["media_id"], caption=body_html, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)
        elif session["media_type"] == "doc" and session.get("media_id"):
            sent = await client.send_document(chat_id, session["media_id"], caption=body_html, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)
        else:
            sent = await client.send_message(chat_id, body_html, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)
    except Exception as e:
        SESSIONS.pop(uid, None)
        return await client.send_message(chat_id, f"{E_CROSS} Couldn't build preview: {e}")

    session["preview_msg_id"] = sent.id

    action_rows = [
        [make_button("🔄 Refresh", callback_data=f"pep_act:refresh:{uid}", style=_P),
         make_button("🗑 Delete", callback_data=f"pep_act:delete:{uid}", style=_D)],
        [make_button("✅ Done", callback_data=f"pep_act:done:{uid}", style=_S)],
    ]
    if uid in ADMINS:
        action_rows.append([make_button("📢 Broadcast to all users", callback_data=f"pep_act:broadcast:{uid}", style=_P)])

    action_msg = await client.send_message(
        chat_id,
        f"{E_INFO} Preview ready — <b>Refresh</b> to reroll emoji, <b>Done</b> to finish"
        + (", or <b>Broadcast</b> to send to everyone." if uid in ADMINS else "."),
        reply_markup=InlineKeyboardMarkup(action_rows),
        parse_mode=enums.ParseMode.HTML,
    )
    session["action_msg_id"] = action_msg.id


@Client.on_callback_query(filters.regex(r"^pep_act:(refresh|delete|done|broadcast):(\d+)$"))
async def pep_action_cb(client: Client, cq: CallbackQuery):
    action, uid = cq.data.split(":")[1], int(cq.data.split(":")[2])
    if cq.from_user.id != uid or uid not in SESSIONS:
        return await cq.answer("Session expired — run /makepost again.", show_alert=True)

    session = SESSIONS[uid]
    chat_id = cq.message.chat.id

    if action == "refresh":
        await cq.answer("Rerolling emoji...")
        for mid in (session.get("preview_msg_id"), session.get("action_msg_id")):
            if mid:
                try:
                    await client.delete_messages(chat_id, mid)
                except Exception:
                    pass
        return await _create_preview(client, chat_id, uid)

    if action == "delete":
        await cq.answer("Deleted.")
        for mid in (session.get("preview_msg_id"), session.get("action_msg_id")):
            if mid:
                try:
                    await client.delete_messages(chat_id, mid)
                except Exception:
                    pass
        SESSIONS.pop(uid, None)
        return

    if action == "done":
        await cq.answer("Saved.")
        if session.get("action_msg_id"):
            try:
                await client.delete_messages(chat_id, session["action_msg_id"])
            except Exception:
                pass
        SESSIONS.pop(uid, None)
        return await client.send_message(chat_id, f"{E_CHECK} Post finalised above. Run /makepost to create another.", parse_mode=enums.ParseMode.HTML)

    if action == "broadcast":
        if uid not in ADMINS:
            return await cq.answer("Admins only.", show_alert=True)
        await cq.answer("Broadcasting...")

        body_html = to_premium_html(session["text"]) if session["text"] else f"{E_SPARK} Post"
        reply_markup = _build_buttons(session)
        total = await db.total_users_count()
        users = await db.get_all_users()
        status = await client.send_message(chat_id, f"{E_INFO} Broadcasting to {total} users...", parse_mode=enums.ParseMode.HTML)

        success = failed = 0
        async for user in users:
            target = user.get("id")
            if not target:
                failed += 1
                continue
            try:
                if session["media_type"] == "photo" and session.get("media_id"):
                    await client.send_photo(target, session["media_id"], caption=body_html, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)
                elif session["media_type"] == "video" and session.get("media_id"):
                    await client.send_video(target, session["media_id"], caption=body_html, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)
                elif session["media_type"] == "doc" and session.get("media_id"):
                    await client.send_document(target, session["media_id"], caption=body_html, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)
                else:
                    await client.send_message(target, body_html, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)
                success += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

        try:
            await status.delete()
        except Exception:
            pass
        await client.send_message(chat_id, f"{E_CHECK} Broadcast done. Success: {success} | Failed: {failed}", parse_mode=enums.ParseMode.HTML)
