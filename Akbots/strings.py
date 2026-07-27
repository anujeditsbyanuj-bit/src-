# Akbots - Don't Remove Credit - @AkBots_Official

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_BOLT   = '<emoji id=5456140674028019486>⚡️</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'
E_PENCIL = '<emoji id=5395444784611480792>✏️</emoji>'
E_IMAGE  = '<emoji id=5395444784611480792>🖼</emoji>'
E_TIP    = '<emoji id=5422439311196834318>💡</emoji>'
E_LOCK   = '<emoji id=5296369303661067030>🔒</emoji>'
E_BATCH  = '<emoji id=5341498088408234504>💯</emoji>'
E_DIAMOND= '<emoji id=5217822164362739968>💎</emoji>'
E_ARROW  = '<emoji id=5416117059207572332>➡️</emoji>'
E_CROWN  = '<emoji id=5217822164362739968>👑</emoji>'
E_STAR   = '<emoji id=5438496463044752972>⭐️</emoji>'

HELP_TXT = (
    f"<b>{E_ROCKET} 𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 𝐀𝐍𝐔𝐉 𝐊𝐔𝐌𝐀𝐑 𝐒𝐚𝐯𝐞 𝐑𝐞𝐬𝐭𝐫𝐢𝐜𝐭𝐞𝐝 𝐁𝐨𝐭 - Complete Guide</b>\n\n"
    f"<b>{E_INFO} How to Use Me</b>\n"
    "<blockquote expandable>\n"
    f"<b>1. {E_LOCK} Login (Required for Restricted Content)</b>\n"
    f"{E_ARROW} Use <code>/login</code> to connect your account.\n"
    f"{E_CHECK} This allows saving from private/restricted channels.\n\n"
    f"<b>2. {E_ROCKET} Saving Content</b>\n"
    f"{E_ARROW} Simply <b>send any Telegram post link</b> (public or private).\n"
    f"{E_BATCH} For <b>batch saving</b>: Send a link like <code>https://t.me/channel/100-110</code>\n"
    f"{E_CHECK} The bot will save all files/media in the range.\n\n"
    f"<b>3. {E_STAR} Features</b>\n"
    f"{E_PENCIL} Custom captions with {{filename}} & {{size}} placeholders\n"
    f"{E_IMAGE} Custom thumbnails\n"
    f"{E_BOLT} Auto-forward to your dump chat\n"
    f"{E_TIP} Daily token system (10 saves/day for free users)\n"
    f"{E_DIAMOND} Premium = unlimited everything ♾️\n"
    "</blockquote>\n\n"
    f"<b>{E_GEAR} User Commands</b>\n\n"
    f"<blockquote>{E_ARROW} <b>/start</b> — Restart the bot & view your quota</blockquote>\n"
    f"<blockquote>{E_ARROW} <b>/help</b> — Show this detailed guide</blockquote>\n"
    f"<blockquote>{E_GEAR} <b>/settings</b> — Open settings menu</blockquote>\n"
    f"<blockquote>{E_INFO} <b>/commands</b> — Quick command list</blockquote>\n\n"
    f"<blockquote>{E_LOCK} <b>/login</b> — Login with session string</blockquote>\n"
    f"<blockquote>{E_CROSS} <b>/logout</b> — Logout current session</blockquote>\n"
    f"<blockquote>{E_WARN} <b>/cancel</b> — Cancel ongoing batch save</blockquote>\n\n"
    f"<blockquote>{E_DIAMOND} <b>/myplan</b> — View your plan status & quota</blockquote>\n"
    f"<blockquote>{E_CROWN} <b>/premium</b> — Premium plans & benefits</blockquote>\n\n"
    f"<blockquote>{E_BOLT} <b>/setchat &lt;chat_id&gt;</b> — Set dump chat</blockquote>\n"
    f"<blockquote>{E_CROSS} <b>/setchat clear</b> — Remove dump chat</blockquote>\n\n"
    f"<blockquote>{E_BOLT} <b>/set_channel_id &lt;channel_id&gt;</b> — Auto-send files to your channel/group</blockquote>\n"
    f"<blockquote>{E_CROSS} <b>/del_channel_id</b> — Remove custom channel</blockquote>\n\n"
    f"<blockquote>{E_PENCIL} <b>/set_caption &lt;text&gt;</b> — Set custom caption</blockquote>\n"
    f"<blockquote>{E_INFO} <b>/see_caption</b> — Preview current caption</blockquote>\n"
    f"<blockquote>{E_CROSS} <b>/del_caption</b> — Remove custom caption</blockquote>\n\n"
    f"<blockquote>{E_IMAGE} <b>/set_thumb</b> — Reply to photo to set custom thumbnail</blockquote>\n"
    f"<blockquote>{E_INFO} <b>/view_thumb</b> — Preview current thumbnail</blockquote>\n"
    f"<blockquote>{E_CROSS} <b>/del_thumb</b> — Remove custom thumbnail</blockquote>\n\n"
    f"<blockquote>📤 <b>/upload_mode</b> — Toggle Auto vs Always-Document uploads</blockquote>\n\n"
    f"<b>{E_TIP} Tips</b>\n"
    f"{E_ARROW} Free users: 10 saves/day + 5 files per batch\n"
    f"{E_DIAMOND} Premium users: Unlimited saves & batch size\n"
    f"{E_ROCKET} Contact @anujedits76 for support or premium purchase\n\n"
    f"<b>{E_CROWN} 𝐓𝐡𝐚𝐧𝐤 𝐲𝐨𝐮 𝐟𝐨𝐫 𝐮𝐬𝐢𝐧𝐠 𝐀𝐧𝐮𝐣 𝐊𝐮𝐦𝐚𝐫! ❤️</b>"
)

COMMANDS_TXT = (
    f"<b>{E_INFO} All Available Commands</b>\n\n"
    f"<b>{E_ARROW} Main Commands</b>\n"
    "<blockquote>\n"
    f"{E_ROCKET} /start  — Home & quota\n"
    f"{E_INFO} /help  — Detailed guide\n"
    f"{E_GEAR} /settings — Customize bot\n"
    f"{E_INFO} /commands — This list\n\n"
    f"{E_LOCK} /login  — Connect account\n"
    f"{E_CROSS} /logout — Disconnect account\n"
    f"{E_WARN} /cancel — Stop current task\n"
    "</blockquote>\n\n"
    f"<b>{E_DIAMOND} Plan & Quota</b>\n"
    "<blockquote>\n"
    f"{E_DIAMOND} /myplan — Your plan status\n"
    f"{E_CROWN} /premium — Upgrade options\n"
    f"{E_STAR} /transfer &lt;user_id&gt; — Gift your Premium to someone\n"
    "</blockquote>\n\n"
    f"<b>{E_BOLT} Dump Chat</b>\n"
    "<blockquote>\n"
    f"{E_BOLT} /setchat &lt;chat_id&gt; — Set forward destination\n"
    f"{E_CROSS} /setchat clear — Remove dump chat\n"
    f"{E_BOLT} /set_channel_id &lt;channel_id&gt; — Auto-send files to channel/group\n"
    f"{E_CROSS} /del_channel_id — Remove custom channel\n"
    "</blockquote>\n\n"
    f"<b>{E_PENCIL} Caption</b>\n"
    "<blockquote>\n"
    f"{E_PENCIL} /set_caption &lt;text&gt; — Custom caption\n"
    f"{E_INFO} /see_caption — Preview caption\n"
    f"{E_CROSS} /del_caption — Remove caption\n"
    "</blockquote>\n\n"
    f"<b>{E_IMAGE} Thumbnail</b>\n"
    "<blockquote>\n"
    f"{E_IMAGE} /set_thumb — Reply to photo\n"
    f"{E_INFO} /view_thumb — Preview thumbnail\n"
    f"{E_CROSS} /del_thumb — Remove thumbnail\n"
    f"📤 /upload_mode — Toggle Auto/Document\n"
    f"{E_GEAR} /thumb_mode — Status\n"
    "</blockquote>\n\n"
    f"<b>{E_ROCKET} Tools & Extras</b>\n"
    "<blockquote>\n"
    f"{E_ROCKET} /ping — Check bot response speed\n"
    f"{E_INFO} /status — Your account overview\n"
    f"{E_ROCKET} /yt &lt;url&gt; — Download video, pick quality (💀 30+ sites: YouTube, Insta, etc.)\n"
    f"{E_ROCKET} /search &lt;name&gt; — Search YouTube by song/video name\n"
    f"{E_ROCKET} /yta or /adl &lt;url&gt; — Extract audio directly (👻 30+ sites)\n"
    f"{E_ROCKET} /fb &lt;url&gt; — Download Facebook video (or just paste the link)\n"
    f"{E_ROCKET} /insta &lt;url&gt; — Download Instagram video (or just paste the link)\n"
    f"{E_ROCKET} /filepress &lt;url&gt; — Bypass FilePress/HubDrive link (or just paste the link)\n"
    f"{E_ROCKET} /gdflix &lt;url&gt; — Bypass GDFlix link (or just paste the link)\n"
    f"{E_ROCKET} /hubcloud &lt;url&gt; — Bypass HubCloud link (or just paste the link)\n"
    f"{E_BOLT} /speedtest — Server network speed\n"
    f"{E_INFO} /token — Free temporary access (if enabled)\n"
    f"{E_STAR} /referral — Invite friends, earn bucks 🎁\n"
    "</blockquote>\n\n"
    f"<b>{E_DIAMOND} Premium = Unlimited Everything</b>\n"
    f"<i>{E_ROCKET} Contact @anujedits76 to upgrade!</i>"
)
