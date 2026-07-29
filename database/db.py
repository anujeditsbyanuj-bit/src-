import motor.motor_asyncio
import datetime
import random
import string
import time
from config import DB_NAME, DB_URI
from logger import LOGGER
logger = LOGGER(__name__)
class Database:
   
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col = self.db.users
        self.cache_col = self.db.link_cache
        self.settings_col = self.db.bot_settings
        # File Store feature (Akbots/filestore.py, Akbots/auto_batch.py)
        self.filestore_tokens = self.db.filestore_tokens        # hybrid token -> {channel_id, msg_id, end_msg_id}
        self.filestore_access = self.db.filestore_access        # shortener gate one-time access tokens
        self.filestore_pending = self.db.filestore_pending      # files waiting to be auto-batched
        self.filestore_batches = self.db.filestore_batches      # completed auto/manual batches
        self.filestore_ratelimit = self.db.filestore_ratelimit  # invalid-token rate limiting
        self.fwd_dupe_col = self.db.fwd_dupes  # Akbots/forward.py "Skip Duplicate" — per-user forwarded file_unique_ids
    def new_user(self, id, name):
        return dict(
            id = id,
            name = name,
            session = None,
            daily_usage = 0, # Added: Track saves
            limit_reset_time = None # Added: Track 24h reset time
        )
   
    async def add_user(self, id, name):
        user = self.new_user(id, name)
        await self.col.insert_one(user)
        logger.info(f"New user added to DB: {id} - {name}")
   
    async def is_user_exist(self, id):
        user = await self.col.find_one({'id':int(id)})
        return bool(user)
   
    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count
    async def get_all_users(self):
        return self.col.find({})
    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})
        logger.info(f"User deleted from DB: {user_id}")
    async def set_session(self, id, session):
        await self.col.update_one({'id': int(id)}, {'$set': {'session': session}})
    async def get_session(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('session')
    # Caption Support
    async def set_caption(self, id, caption):
        await self.col.update_one({'id': int(id)}, {'$set': {'caption': caption}})
    async def get_caption(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('caption', None)
    async def del_caption(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'caption': ""}})
    # Thumbnail Support
    async def set_thumbnail(self, id, thumbnail):
        await self.col.update_one({'id': int(id)}, {'$set': {'thumbnail': thumbnail}})
    async def get_thumbnail(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('thumbnail', None)
    async def del_thumbnail(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'thumbnail': ""}})
    # Upload Mode Toggle — "auto" (default) picks video/audio/photo/document
    # per file extension like today; "document" forces every upload to go
    # out as a plain document (no re-encoding/compression risk, keeps
    # original quality — some users prefer this for large/rare video codecs).
    async def set_upload_mode(self, id, mode):
        await self.col.update_one({'id': int(id)}, {'$set': {'upload_mode': mode}})
    async def get_upload_mode(self, id):
        user = await self.col.find_one({'id': int(id)})
        return (user or {}).get('upload_mode', 'auto')
    # Spoiler Mode — when on, every video/photo this bot uploads for the
    # user is sent with Telegram's spoiler blur (has_spoiler=True).
    async def set_spoiler_mode(self, id, enabled: bool):
        await self.col.update_one({'id': int(id)}, {'$set': {'spoiler_mode': bool(enabled)}}, upsert=True)
    async def get_spoiler_mode(self, id):
        user = await self.col.find_one({'id': int(id)})
        return bool((user or {}).get('spoiler_mode', False))
    # Auto Screenshots — when on, every video upload is automatically
    # followed by a handful of preview screenshots sent as a reply.
    async def set_auto_screenshots(self, id, enabled: bool):
        await self.col.update_one({'id': int(id)}, {'$set': {'auto_screenshots': bool(enabled)}}, upsert=True)
    async def get_auto_screenshots(self, id):
        user = await self.col.find_one({'id': int(id)})
        return bool((user or {}).get('auto_screenshots', False))
    # Auto Sample — when on, a short preview clip is generated and sent
    # BEFORE the full video upload begins.
    async def set_auto_sample(self, id, enabled: bool):
        await self.col.update_one({'id': int(id)}, {'$set': {'auto_sample': bool(enabled)}}, upsert=True)
    async def get_auto_sample(self, id):
        user = await self.col.find_one({'id': int(id)})
        return bool((user or {}).get('auto_sample', False))
    # Auto-Rename Template Support
    async def set_autorename(self, id, template):
        if template:
            await self.col.update_one({'id': int(id)}, {'$set': {'autorename': template}}, upsert=True)
        else:
            await self.col.update_one({'id': int(id)}, {'$unset': {'autorename': ""}}, upsert=True)
    async def get_autorename(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('autorename') if user else None
    # Filename Prefix / Suffix Support
    async def set_prefix(self, id, prefix):
        if prefix:
            await self.col.update_one({'id': int(id)}, {'$set': {'prefix': prefix}}, upsert=True)
        else:
            await self.col.update_one({'id': int(id)}, {'$unset': {'prefix': ""}}, upsert=True)
    async def get_prefix(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('prefix') if user else None
    async def set_suffix(self, id, suffix):
        if suffix:
            await self.col.update_one({'id': int(id)}, {'$set': {'suffix': suffix}}, upsert=True)
        else:
            await self.col.update_one({'id': int(id)}, {'$unset': {'suffix': ""}}, upsert=True)
    async def get_suffix(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('suffix') if user else None
    # Metadata Title Support
    async def set_metadata(self, id, text):
        if text:
            await self.col.update_one({'id': int(id)}, {'$set': {'metadata_text': text}}, upsert=True)
        else:
            await self.col.update_one({'id': int(id)}, {'$unset': {'metadata_text': ""}}, upsert=True)
    async def get_metadata(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('metadata_text') if user else None
    # Watermark Support
    async def set_watermark(self, id, text):
        if text:
            await self.col.update_one({'id': int(id)}, {'$set': {'watermark_text': text}}, upsert=True)
        else:
            await self.col.update_one({'id': int(id)}, {'$unset': {'watermark_text': ""}}, upsert=True)
    async def get_watermark(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('watermark_text') if user else None
    async def set_watermark_position(self, id, position):
        await self.col.update_one({'id': int(id)}, {'$set': {'watermark_position': position}}, upsert=True)
    async def get_watermark_position(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('watermark_position') if user else 'bottom_right'

    # =====================================================================
    # /encode Settings System — ported from ENCODING-BOT-master's
    # utils/database/database.py persistent per-user settings (all ~20
    # ffmpeg knobs: resolution/codec/CRF/preset/tune/CABAC/10-bit/aspect/
    # reframe/fps/hardsub/softsub/watermark-burn/metadata-tag/audio codec+
    # bitrate+samplerate+channels/container). Every field is prefixed
    # `enc_` so it can never collide with the unrelated fields already
    # above (e.g. `watermark_text` is the drawtext overlay used by
    # /apply_watermark; `enc_watermark` below just toggles whether /encode
    # should also burn that same saved text in).
    # =====================================================================
    _ENC_DEFAULTS = {
        'enc_resolution': 'OG',        # OG/2160/1440/1080/720/576/480/360/240
        'enc_codec': 'h264',           # h264/h265
        'enc_crf': 23,
        'enc_preset': 'medium',        # ultrafast..veryslow (real ffmpeg preset names)
        'enc_tune': 'film',            # film/animation
        'enc_cabac': False,
        'enc_bits10': False,           # 10-bit (yuv420p10le)
        'enc_aspect169': False,        # force -aspect 16:9
        'enc_reframe': 'pass',         # pass/4/8/16 (-refs)
        'enc_fps': 'source',           # source/ntsc/pal/film/23.976/30/60
        'enc_extension': 'MKV',        # MP4/MKV/AVI
        'enc_hardsub': False,          # burn embedded subtitle stream into video
        'enc_softsub': True,           # copy/keep subtitle streams (MP4/MKV only)
        'enc_watermark': False,        # burn the saved /set_watermark text in
        'enc_metadata_tag': False,     # tag output streams with a Akbots title
        'enc_audio_codec': 'source',   # source/aac/ac3/opus/vorbis/alac
        'enc_audio_bitrate': 'source', # source/128/160/192/224/256/320/400 (Kbps)
        'enc_audio_samplerate': 'source',  # source/44.1K/48K
        'enc_audio_channels': 'source',    # source/1.0/2.0/2.1/5.1/7.1
        'enc_notify': True,            # False = send the finished encode silently (no push notification)
        'enc_autodel': False,          # delete the original replied-to message after a successful encode
    }

    async def get_encode_settings(self, id):
        """Returns the full encode-settings dict for a user, filled in with
        defaults for any field never touched."""
        user = await self.col.find_one({'id': int(id)}) or {}
        return {key: user.get(key, default) for key, default in self._ENC_DEFAULTS.items()}

    async def set_encode_setting(self, id, key, value):
        if key not in self._ENC_DEFAULTS:
            raise ValueError(f"Unknown encode setting: {key}")
        await self.col.update_one({'id': int(id)}, {'$set': {key: value}}, upsert=True)

    async def reset_encode_settings(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': dict(self._ENC_DEFAULTS)}, upsert=True)

    # Akbots / Modified by You
    # Don't Remove Credit
    # Telegram Channel @AkBots_Official
    # Premium Support
    async def add_premium(self, id, expiry_date):
        # When user buys premium, we also reset their limits just in case
        await self.col.update_one({'id': int(id)}, {
            '$set': {
                'is_premium': True,
                'premium_expiry': expiry_date,
                'daily_usage': 0,
                'limit_reset_time': None
            }
        })
        logger.info(f"User {id} granted premium until {expiry_date}")
    async def remove_premium(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': {'is_premium': False, 'premium_expiry': None}})
        logger.info(f"User {id} removed from premium")
    async def check_premium(self, id):
        user = await self.col.find_one({'id': int(id)})
        if user and user.get('is_premium'):
            return user.get('premium_expiry')
        return None
    async def get_premium_users(self):
        return self.col.find({'is_premium': True})
    # Ban Support
    async def ban_user(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': {'is_banned': True}})
        logger.warning(f"User banned: {id}")
    async def unban_user(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': {'is_banned': False}})
        logger.info(f"User unbanned: {id}")
    async def is_banned(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('is_banned', False)
    # Dump Chat / Custom Channel Support
    async def set_dump_chat(self, id, chat_id):
        if chat_id:
            await self.col.update_one({'id': int(id)}, {'$set': {'dump_chat': int(chat_id)}}, upsert=True)
        else:
            await self.col.update_one({'id': int(id)}, {'$unset': {'dump_chat': ""}}, upsert=True)
    async def get_dump_chat(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('dump_chat', None) if user else None
    # Multi-Channel Support — a user can link several channels/groups at
    # once (e.g. /add_channel_id run more than once). Stored separately
    # from the legacy single 'dump_chat' field above so existing
    # single-channel users (set via /set_channel_id, /setchat, or admin.py)
    # keep working unchanged; get_dump_chats() merges both so every reader
    # (forwarding, /channel_id list, etc.) sees the full set either way.
    async def add_dump_chat(self, id, chat_id):
        await self.col.update_one({'id': int(id)}, {'$addToSet': {'dump_chats': int(chat_id)}}, upsert=True)
    async def remove_dump_chat(self, id, chat_id):
        await self.col.update_one({'id': int(id)}, {'$pull': {'dump_chats': int(chat_id)}}, upsert=True)
    async def get_dump_chats(self, id):
        user = await self.col.find_one({'id': int(id)})
        if not user:
            return []
        chats = list(user.get('dump_chats', []) or [])
        legacy = user.get('dump_chat')
        if legacy and legacy not in chats:
            chats.append(legacy)
        return chats
    # Batch Limit Support (user-configurable, capped server-side by plan)
    async def set_batch_limit(self, id, limit_val):
        await self.col.update_one({'id': int(id)}, {'$set': {'batch_limit': int(limit_val)}})
    async def get_batch_limit(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('batch_limit') if user else None
    # Lifetime Saved-Files Counter (for /status)
    async def increment_total_saved(self, id):
        await self.col.update_one({'id': int(id)}, {'$inc': {'total_saved': 1}})
    async def get_total_saved(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('total_saved', 0) if user else 0
    # Optional Free-Access Token Gate (URL-shortener based, disabled unless configured)
    async def set_free_token(self, id, expires_at):
        await self.col.update_one({'id': int(id)}, {'$set': {'free_token_expiry': expires_at}})
    async def get_free_token_expiry(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('free_token_expiry') if user else None
    async def has_valid_free_token(self, id):
        expiry = await self.get_free_token_expiry(id)
        if not expiry:
            return False
        return datetime.datetime.now() < expiry
    # Bot Mode (Freemium / Paid) — stored on a fixed settings doc
    async def get_bot_mode(self):
        doc = await self.db.settings.find_one({'_id': 'bot_mode'})
        return doc.get('mode', 'paid') if doc else 'paid'
    async def set_bot_mode(self, mode):
        await self.db.settings.update_one({'_id': 'bot_mode'}, {'$set': {'mode': mode}}, upsert=True)
    # Custom Bot Token Support (/setbot, /rembot)
    async def set_custom_bot(self, id, bot_token):
        await self.col.update_one({'id': int(id)}, {'$set': {'custom_bot_token': bot_token}})
    async def get_custom_bot(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('custom_bot_token') if user else None
    async def remove_custom_bot(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'custom_bot_token': ""}})
    # Referral System
    async def ensure_referral_data(self, id):
        user = await self.col.find_one({'id': int(id)})
        if user and user.get('referral', {}).get('code'):
            return
        code = None
        for _ in range(5):
            candidate = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not await self.col.find_one({'referral.code': candidate}):
                code = candidate
                break
        code = code or ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        await self.col.update_one(
            {'id': int(id)},
            {'$set': {
                'referral.code': code,
                'referral.ak_bucks': 0,
                'referral.total_referrals': 0,
                'referral.referred_users': [],
                'referral.referred_by': None,
            }},
            upsert=True
        )
    async def get_referral_info(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('referral') if user else None
    async def get_user_by_referral_code(self, code):
        return await self.col.find_one({'referral.code': code})
    async def add_referral(self, referrer_id, new_user_id, new_user_name, reward_bucks):
        await self.col.update_one(
            {'id': int(referrer_id)},
            {
                '$inc': {'referral.ak_bucks': reward_bucks, 'referral.total_referrals': 1},
                '$push': {'referral.referred_users': {
                    'id': int(new_user_id), 'name': new_user_name,
                    'referred_at': datetime.datetime.now().isoformat()
                }}
            }
        )
        await self.col.update_one({'id': int(new_user_id)}, {'$set': {'referral.referred_by': int(referrer_id)}})
    async def deduct_referral_bucks(self, id, amount):
        """Atomically checks-and-deducts in a single update_one call, using a
        $gte filter on the current balance as the guard. Without this, a
        separate "read balance, then deduct" sequence leaves a window where
        two concurrent redeems (e.g. a double-tap) can both pass the balance
        check before either deduction lands, letting a user redeem more bucks
        than they actually have (or go negative). Returns True if the
        balance was sufficient and the deduction was applied, False
        otherwise (e.g. balance changed/was insufficient in between)."""
        result = await self.col.update_one(
            {'id': int(id), 'referral.ak_bucks': {'$gte': amount}},
            {'$inc': {'referral.ak_bucks': -amount}}
        )
        return result.modified_count > 0
    async def get_referral_leaderboard(self, skip=0, limit=10):
        cursor = self.col.find({'referral.total_referrals': {'$gt': 0}}) \
            .sort('referral.total_referrals', -1).skip(skip).limit(limit)
        return [u async for u in cursor]
    async def count_referral_leaderboard(self):
        return await self.col.count_documents({'referral.total_referrals': {'$gt': 0}})
    # Delete/Replace Words Support
    # RSS Feed Support (per-user subscriptions for auto-download)
    async def add_rss_feed(self, id, name, url, initial_seen=None):
        user = await self.col.find_one({'id': int(id)})
        feeds = (user or {}).get('rss_feeds', []) or []
        feeds.append({'name': name, 'url': url, 'seen': initial_seen or []})
        await self.col.update_one({'id': int(id)}, {'$set': {'rss_feeds': feeds}}, upsert=True)

    async def get_rss_feeds(self, id):
        user = await self.col.find_one({'id': int(id)})
        return (user or {}).get('rss_feeds', []) or []

    async def remove_rss_feed(self, id, index):
        feeds = await self.get_rss_feeds(id)
        if 0 <= index < len(feeds):
            feeds.pop(index)
            await self.col.update_one({'id': int(id)}, {'$set': {'rss_feeds': feeds}})
            return True
        return False

    async def mark_rss_seen(self, id, feed_index, guid, cap=300):
        feeds = await self.get_rss_feeds(id)
        if 0 <= feed_index < len(feeds):
            seen = feeds[feed_index].get('seen') or []
            seen.append(guid)
            feeds[feed_index]['seen'] = seen[-cap:]
            await self.col.update_one({'id': int(id)}, {'$set': {'rss_feeds': feeds}})

    async def get_all_rss_users(self):
        return self.col.find({'rss_feeds': {'$exists': True, '$ne': []}})

    # Anime auto-poster (global, not per-user — one channel the whole bot
    # posts new SubsPlease episodes to). Stored in its own single-document
    # collection since it's bot-wide config, not tied to any one user.
    async def set_anime_channel(self, chat_id):
        await self.settings_col.update_one(
            {'_id': 'global'}, {'$set': {'anime_channel': chat_id}}, upsert=True
        )

    async def get_anime_channel(self):
        doc = await self.settings_col.find_one({'_id': 'global'})
        return (doc or {}).get('anime_channel')

    async def is_anime_uploaded(self, uid):
        doc = await self.settings_col.find_one({'_id': 'global'})
        return uid in ((doc or {}).get('anime_uploaded') or [])

    async def add_anime_uploaded(self, uid, cap=3000):
        doc = await self.settings_col.find_one({'_id': 'global'})
        seen = (doc or {}).get('anime_uploaded') or []
        seen.append(uid)
        seen = seen[-cap:]
        await self.settings_col.update_one(
            {'_id': 'global'}, {'$set': {'anime_uploaded': seen}}, upsert=True
        )

    async def set_delete_words(self, id, words):
        await self.col.update_one({'id': int(id)}, {'$addToSet': {'delete_words': {'$each': words}}})
    async def get_delete_words(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('delete_words', [])
    async def remove_delete_words(self, id, words):
        await self.col.update_one({'id': int(id)}, {'$pull': {'delete_words': {'$in': words}}})
    async def set_replace_words(self, id, repl_dict):
        user = await self.col.find_one({'id': int(id)})
        current_repl = user.get('replace_words', {})
        current_repl.update(repl_dict)
        await self.col.update_one({'id': int(id)}, {'$set': {'replace_words': current_repl}})
    async def get_replace_words(self, id):
        user = await self.col.find_one({'id': int(id)})
        return user.get('replace_words', {})
    async def remove_replace_words(self, id, words):
        user = await self.col.find_one({'id': int(id)})
        current_repl = user.get('replace_words', {})
        for w in words:
            current_repl.pop(w, None)
        await self.col.update_one({'id': int(id)}, {'$set': {'replace_words': current_repl}})
    # --------------------------------------------------------
    # NEW FEATURES: Daily Limits (Free User Restriction)
    # --------------------------------------------------------
    async def check_limit(self, id):
        """
        Checks if a user has hit their daily limit.
        Returns: True if BLOCKED (limit reached), False if ALLOWED.
        """
        user = await self.col.find_one({'id': int(id)})
        if not user:
            return False # Should be added via add_user, but safe fallback
       
        # 1. Premium Check: Always allowed
        if user.get('is_premium'):
            return False
        # 2. Check Time Reset
        now = datetime.datetime.now()
        reset_time = user.get('limit_reset_time')
       
        # If reset time has passed or was never set, reset count to 0
        if reset_time is None or now >= reset_time:
            await self.col.update_one(
                {'id': int(id)},
                {'$set': {'daily_usage': 0, 'limit_reset_time': None}}
            )
            return False # Allowed (count is 0)
        # 3. Check Count
        usage = user.get('daily_usage', 0)
        if usage >= 10:
            return True # Blocked
       
        return False # Allowed
    async def add_traffic(self, id):
        """
        Increments usage count.
        If it's the first save of the cycle, sets the 24h timer.
        """
        user = await self.col.find_one({'id': int(id)})
       
        # If premium, do nothing or track stats if you want (currently strictly for limit logic)
        if user.get('is_premium'):
            return
        now = datetime.datetime.now()
        reset_time = user.get('limit_reset_time')
        # Logic: If timer is not running (None), start it for 24 hours from NOW.
        if reset_time is None:
            new_reset_time = now + datetime.timedelta(hours=24)
            await self.col.update_one(
                {'id': int(id)},
                {'$set': {'daily_usage': 1, 'limit_reset_time': new_reset_time}}
            )
        else:
            # Just increment
            await self.col.update_one(
                {'id': int(id)},
                {'$inc': {'daily_usage': 1}}
            )
    # --------------------------------------------------------
    # Audio Track Selector (Akbots/audio_select.py) — its own daily
    # counter, separate from the generic check_limit/add_traffic above,
    # so this feature's 15-free / 30-premium quota doesn't eat into (or
    # get eaten into by) other features' 10/day limit.
    # --------------------------------------------------------
    async def check_audio_select_limit(self, id):
        """Returns True if BLOCKED (daily limit reached), False if ALLOWED."""
        user = await self.col.find_one({'id': int(id)})
        if not user:
            return False
        limit = 30 if user.get('is_premium') else 15
        now = datetime.datetime.now()
        reset_time = user.get('audio_select_reset_time')
        if reset_time is None or now >= reset_time:
            await self.col.update_one(
                {'id': int(id)},
                {'$set': {'audio_select_usage': 0, 'audio_select_reset_time': None}}
            )
            return False
        return user.get('audio_select_usage', 0) >= limit

    async def add_audio_select_usage(self, id):
        user = await self.col.find_one({'id': int(id)})
        now = datetime.datetime.now()
        reset_time = user.get('audio_select_reset_time') if user else None
        if reset_time is None:
            new_reset_time = now + datetime.timedelta(hours=24)
            await self.col.update_one(
                {'id': int(id)},
                {'$set': {'audio_select_usage': 1, 'audio_select_reset_time': new_reset_time}}
            )
        else:
            await self.col.update_one({'id': int(id)}, {'$inc': {'audio_select_usage': 1}})

    async def get_audio_select_usage(self, id):
        user = await self.col.find_one({'id': int(id)})
        return (user or {}).get('audio_select_usage', 0)

    async def get_audio_select_limit(self, id):
        user = await self.col.find_one({'id': int(id)})
        return 30 if (user and user.get('is_premium')) else 15

    # Default output filename for Akbots/audio_select.py — caption reuses
    # the existing global /set_caption (db.get_caption) so there's only
    # one caption setting across the whole bot instead of a duplicate one.
    async def set_audio_select_name(self, id, filename):
        await self.col.update_one({'id': int(id)}, {'$set': {'audio_select_name': filename}})

    async def get_audio_select_name(self, id):
        user = await self.col.find_one({'id': int(id)})
        return (user or {}).get('audio_select_name', None)

    # --------------------------------------------------------
    # Link Cache (instant re-send for previously downloaded links)
    # --------------------------------------------------------
    async def get_cached_link(self, url_hash):
        return await self.cache_col.find_one({'url_hash': url_hash})

    async def set_cached_link(self, url_hash, data: dict):
        data = dict(data)
        data['url_hash'] = url_hash
        data['updated_at'] = datetime.datetime.utcnow()
        await self.cache_col.update_one(
            {'url_hash': url_hash}, {'$set': data}, upsert=True
        )

    async def delete_cached_link(self, url_hash):
        await self.cache_col.delete_one({'url_hash': url_hash})

    # --------------------------------------------------------
    # Forward tool (/setsource, /settarget, /fwd — Akbots/forward.py)
    # --------------------------------------------------------
    async def set_fwd_source(self, id, chat_id, via="bot"):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_source': chat_id, 'fwd_source_via': via}})

    async def set_fwd_target(self, id, chat_id, via="bot"):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_target': chat_id, 'fwd_target_via': via}})

    async def get_fwd_settings(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return {
            'source': user.get('fwd_source'),
            'source_via': user.get('fwd_source_via', 'bot'),
            'target': user.get('fwd_target'),
            'target_via': user.get('fwd_target_via', 'bot'),
            'last_id': user.get('fwd_last_id'),
            'last_range': user.get('fwd_last_range'),
            'caption': user.get('fwd_caption'),
            'button': user.get('fwd_button'),
            'filters': user.get('fwd_filters', []),
            'has_login': bool(user.get('session')),
            'keywords': user.get('fwd_keywords', []),          # allowlist: only forward if filename matches one
            'ext_block': user.get('fwd_ext_block', []),         # blocklist: skip these file extensions
            'size_limit_mb': user.get('fwd_size_limit_mb', 0),  # 0 = no limit; skip media above this size
            'skip_duplicate': user.get('fwd_skip_duplicate', False),
            'tag': user.get('fwd_tag', False),                  # True = real forward (keeps "Forwarded from"), no caption/button possible
            'poll': user.get('fwd_poll', False),                # ported from FTM Forward Bot's Extra Settings screen
            'secure_message': user.get('fwd_secure_message', False),
        }

    async def set_fwd_progress(self, id, last_id, last_range=None):
        update = {'fwd_last_id': last_id}
        if last_range is not None:
            update['fwd_last_range'] = last_range
        await self.col.update_one({'id': int(id)}, {'$set': update})

    async def clear_fwd_progress(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'fwd_last_id': "", 'fwd_last_range': ""}})

    async def set_fwd_caption(self, id, caption):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_caption': caption}}, upsert=True)

    async def clear_fwd_caption(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'fwd_caption': ""}})

    async def set_fwd_button(self, id, text, url=None):
        """Stores the button config. Pass either:
        - set_fwd_button(id, raw_markup_string)  -> new [text][buttonurl:url] syntax, multi-button
        - set_fwd_button(id, text, url)          -> legacy single button (kept for backward compat)
        """
        if url is None:
            await self.col.update_one({'id': int(id)}, {'$set': {'fwd_button': text}}, upsert=True)
        else:
            await self.col.update_one({'id': int(id)}, {'$set': {'fwd_button': f"[{text}][buttonurl:{url}]"}}, upsert=True)

    async def clear_fwd_button(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'fwd_button': ""}})

    async def set_fwd_filters(self, id, filters_list):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_filters': filters_list}}, upsert=True)

    async def clear_fwd_filters(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'fwd_filters': ""}})

    # -- Keywords allowlist ("File with these keywords in file name will
    # forward" — only files whose name matches ONE of these get through;
    # empty list = no restriction. Distinct from fwd_filters' kw: token,
    # which is an exclusion/skip rule, not an allowlist.) --
    async def add_fwd_keyword(self, id, word):
        await self.col.update_one({'id': int(id)}, {'$addToSet': {'fwd_keywords': word.lower()}}, upsert=True)

    async def clear_fwd_keywords(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_keywords': []}})

    # -- Extension blocklist ("Files with these extensions will not
    # forward") — dot-prefixed, e.g. ".exe". --
    async def add_fwd_ext_block(self, id, ext):
        await self.col.update_one({'id': int(id)}, {'$addToSet': {'fwd_ext_block': ext.lower()}}, upsert=True)

    async def clear_fwd_ext_block(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_ext_block': []}})

    async def set_fwd_size_limit(self, id, mb: int):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_size_limit_mb': max(0, int(mb))}}, upsert=True)

    async def set_fwd_skip_duplicate(self, id, on: bool):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_skip_duplicate': bool(on)}}, upsert=True)

    async def set_fwd_tag(self, id, on: bool):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_tag': bool(on)}}, upsert=True)

    async def set_fwd_poll(self, id, on: bool):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_poll': bool(on)}}, upsert=True)

    async def set_fwd_secure_message(self, id, on: bool):
        await self.col.update_one({'id': int(id)}, {'$set': {'fwd_secure_message': bool(on)}}, upsert=True)

    # -- Skip Duplicate storage: one doc per (user, file_unique_id) ever
    # forwarded. file_unique_id is Telegram's content hash — stable across
    # re-uploads/resends of the same underlying file, unlike file_id. --
    async def is_fwd_duplicate(self, id, file_unique_id: str) -> bool:
        return await self.fwd_dupe_col.find_one({'uid': int(id), 'fuid': file_unique_id}) is not None

    async def record_fwd_duplicate(self, id, file_unique_id: str):
        await self.fwd_dupe_col.update_one(
            {'uid': int(id), 'fuid': file_unique_id},
            {'$set': {'uid': int(id), 'fuid': file_unique_id, 'ts': time.time()}},
            upsert=True
        )

    async def clear_fwd_duplicates(self, id):
        await self.fwd_dupe_col.delete_many({'uid': int(id)})

    async def reset_fwd_settings(self, id):
        """Clears every forward-related field for /reset: source, target,
        progress, caption, button, filters, keywords, extension blocklist,
        size limit, skip-duplicate, and tag. Deliberately leaves the login
        session ('session' field) alone since that's shared with other
        Akbots features, not just forwarding — /logout handles that."""
        await self.col.update_one({'id': int(id)}, {'$unset': {
            'fwd_source': "", 'fwd_source_via': "",
            'fwd_target': "", 'fwd_target_via': "",
            'fwd_last_id': "", 'fwd_last_range': "",
            'fwd_caption': "", 'fwd_button': "", 'fwd_filters': "",
            'fwd_keywords': "", 'fwd_ext_block': "", 'fwd_size_limit_mb': "",
            'fwd_skip_duplicate': "", 'fwd_tag': "",
            'fwd_poll': "", 'fwd_secure_message': "",
        }})
        await self.clear_fwd_duplicates(id)

    # --------------------------------------------------------
    # Channel Routes — saved source+target pairs, list-based
    # (/channels, /addroute, /delroute — Akbots/channels.py)
    #
    # forward.py's job engine is still single-job-per-user (one active
    # source+target at a time via fwd_source/fwd_target, unchanged from
    # before). This is an address book on top of it: save several
    # source→target pairs under a label, then "activate" one to become
    # the current fwd_source/fwd_target with one tap instead of re-typing
    # both chat refs every time. It does NOT run multiple forwards at
    # once — that would mean reworking _RUNNING in forward.py from one
    # task per user to a set of tasks, which is a bigger change than this
    # pass makes.
    # --------------------------------------------------------
    async def add_fwd_route(self, id, label, source, source_via, target, target_via):
        # Re-saving an existing label replaces it instead of duplicating.
        await self.col.update_one({'id': int(id)}, {'$pull': {'fwd_routes': {'label': label}}})
        route = {
            'label': label,
            'source': source, 'source_via': source_via,
            'target': target, 'target_via': target_via,
            'added_at': datetime.datetime.utcnow(),
        }
        await self.col.update_one({'id': int(id)}, {'$push': {'fwd_routes': route}}, upsert=True)

    async def get_fwd_routes(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('fwd_routes', [])

    async def remove_fwd_route(self, id, label):
        result = await self.col.update_one({'id': int(id)}, {'$pull': {'fwd_routes': {'label': label}}})
        return result.modified_count > 0

    # --------------------------------------------------------
    # Titanium Clone Mode (/titanium, /addbot, /delbot — Akbots/titanium.py)
    # --------------------------------------------------------
    async def get_titanium_bots(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('titanium_bots', [])

    async def add_titanium_bot(self, id, token, username, source="manual", bot_id=None):
        now = datetime.datetime.utcnow()
        entry = {
            "token": token, "username": username, "source": source, "bot_id": bot_id,
            "added_at": now, "last_used": 0,
            "status": "active", "last_validated": now,
        }
        await self.col.update_one({'id': int(id)}, {'$push': {'titanium_bots': entry}})

    async def touch_titanium_bot_validated(self, id, token):
        await self.col.update_one(
            {'id': int(id), 'titanium_bots.token': token},
            {'$set': {'titanium_bots.$.last_validated': datetime.datetime.utcnow(), 'titanium_bots.$.status': 'active'}}
        )

    # --------------------------------------------------------
    # Download History & Favourites (/history, /fav, /favourites —
    # Akbots/history_favs.py). History keeps only the most recent 50
    # entries per user via $slice so the array can't grow unbounded.
    # --------------------------------------------------------
    async def add_download_history(self, id, entry):
        entry = dict(entry)
        entry["at"] = datetime.datetime.utcnow()
        await self.col.update_one(
            {'id': int(id)},
            {'$push': {'download_history': {'$each': [entry], '$position': 0, '$slice': 50}}},
            upsert=True,
        )

    async def get_download_history(self, id, limit=15):
        user = await self.col.find_one({'id': int(id)}) or {}
        return (user.get('download_history') or [])[:limit]

    async def add_favourite(self, id, url, title=None):
        user = await self.col.find_one({'id': int(id)}) or {}
        if any(f.get("url") == url for f in user.get("favourites", [])):
            return False
        entry = {"url": url, "title": title or url, "added_at": datetime.datetime.utcnow()}
        await self.col.update_one({'id': int(id)}, {'$push': {'favourites': entry}}, upsert=True)
        return True

    async def get_favourites(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('favourites', [])

    async def remove_favourite(self, id, url):
        result = await self.col.update_one(
            {'id': int(id)}, {'$pull': {'favourites': {'url': url}}}
        )
        return result.modified_count > 0

    async def remove_favourite_by_index(self, id, index):
        favs = await self.get_favourites(id)
        if index < 1 or index > len(favs):
            return None
        target = favs[index - 1]
        await self.remove_favourite(id, target["url"])
        return target

    async def remove_titanium_bot(self, id, username):
        result = await self.col.update_one(
            {'id': int(id)}, {'$pull': {'titanium_bots': {'username': username}}}
        )
        return result.modified_count > 0

    async def touch_titanium_bot(self, id, token):
        await self.col.update_one(
            {'id': int(id), 'titanium_bots.token': token},
            {'$set': {'titanium_bots.$.last_used': time.time()}}
        )

    # --------------------------------------------------------
    # Per-user MongoDB (/set_mydb, /del_mydb, /mydb — Akbots/userdb.py)
    #
    # Akbots itself always keeps running on the shared DB above (DB_URI/
    # DB_NAME) — that's where this very record (fwd settings, session,
    # titanium bots, etc.) lives, and it isn't going anywhere. This is a
    # SEPARATE, opt-in connection a person can point at their own MongoDB
    # cluster, for plugins that want to store per-user data (e.g. dedup
    # caches) there instead of the shared instance. Storing the URI here
    # is just bookkeeping; get_user_db_client() below is what actually
    # opens it.
    # --------------------------------------------------------
    async def set_user_db_uri(self, id, uri):
        await self.col.update_one({'id': int(id)}, {'$set': {'user_db_uri': uri}}, upsert=True)

    async def get_user_db_uri(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('user_db_uri')

    async def clear_user_db_uri(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'user_db_uri': ""}})
        evict_user_db_client(id)


# --------------------------------------------------------
# Per-user MongoDB connection cache
#
# Kept module-level (not a Database method) since each entry is its own
# independent AsyncIOMotorClient pointed at a DIFFERENT cluster per user
# — nothing to do with the shared `db` instance's own connection pool.
# Cache key includes the URI so a stale cached client is never handed
# back after someone changes it; evict_user_db_client() closes the old
# one explicitly on /del_mydb so the socket doesn't sit open for no
# reason.
# --------------------------------------------------------
_user_db_cache = {}  # user_id -> (uri, AsyncIOMotorClient)


def _extract_db_name(uri: str) -> str:
    """Pulls the path-segment database name out of a mongo URI, e.g.
    '...mongodb.net/mydb?retryWrites=true' -> 'mydb'. Falls back to a
    fixed name if the URI has none (bare '/' or nothing after the host),
    which is the common case for URIs copied straight from Atlas."""
    try:
        after_scheme = uri.split("://", 1)[1]
        after_host = after_scheme.split("/", 1)[1] if "/" in after_scheme else ""
        name = after_host.split("?", 1)[0].strip("/")
        return name or "Akbots_userdb"
    except Exception:
        return "Akbots_userdb"


async def get_user_db_client(user_id):
    """Returns a connected (AsyncIOMotorClient, database) tuple for the
    given user's own stored Mongo URI, or (None, None) if they haven't
    set one. Does NOT re-validate reachability on every call — that
    check happens once at /set_mydb time; a cluster that goes down later
    will surface as a normal pymongo error to whatever plugin tries to
    use it, same as it would for the shared DB.
    """
    uri = await db.get_user_db_uri(user_id)
    if not uri:
        return None, None

    cached = _user_db_cache.get(user_id)
    if cached and cached[0] == uri:
        client = cached[1]
        return client, client[_extract_db_name(uri)]

    client = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=8000)
    _user_db_cache[user_id] = (uri, client)
    return client, client[_extract_db_name(uri)]


def evict_user_db_client(user_id):
    cached = _user_db_cache.pop(user_id, None)
    if cached:
        try:
            cached[1].close()
        except Exception:
            pass


db = Database(DB_URI, DB_NAME)


# --------------------------------------------------------
# Forward Engine (Source/Target channels, live forwarding mode, caption
# tools, custom button, message-type filters) — ported from the reference
# forward-bot's "AK Manager" (gamma source/target, replacer/remover/
# prefix/suffix, button, filters). Attached to the Database class so
# every call site can keep using db.<method>(...) like everything else.
# --------------------------------------------------------
async def _fe_add_forward_source(self, id, chat_id, title=""):
    entry = {'chat_id': int(chat_id), 'title': title}
    await self.col.update_one(
        {'id': int(id), 'forward_sources.chat_id': {'$ne': int(chat_id)}},
        {'$push': {'forward_sources': entry}}, upsert=True
    )
async def _fe_remove_forward_source(self, id, chat_id):
    await self.col.update_one({'id': int(id)}, {'$pull': {'forward_sources': {'chat_id': int(chat_id)}}})
async def _fe_get_forward_sources(self, id):
    user = await self.col.find_one({'id': int(id)})
    return (user or {}).get('forward_sources', []) or []

async def _fe_add_forward_target(self, id, chat_id, title=""):
    entry = {'chat_id': int(chat_id), 'title': title}
    await self.col.update_one(
        {'id': int(id), 'forward_targets.chat_id': {'$ne': int(chat_id)}},
        {'$push': {'forward_targets': entry}}, upsert=True
    )
async def _fe_remove_forward_target(self, id, chat_id):
    await self.col.update_one({'id': int(id)}, {'$pull': {'forward_targets': {'chat_id': int(chat_id)}}})
async def _fe_get_forward_targets(self, id):
    user = await self.col.find_one({'id': int(id)})
    return (user or {}).get('forward_targets', []) or []

async def _fe_set_forward_mode(self, id, enabled: bool):
    await self.col.update_one({'id': int(id)}, {'$set': {'forward_mode': bool(enabled)}}, upsert=True)
async def _fe_get_forward_mode(self, id):
    user = await self.col.find_one({'id': int(id)})
    return bool((user or {}).get('forward_mode', False))

async def _fe_get_forward_caption_config(self, id):
    user = await self.col.find_one({'id': int(id)})
    cfg = dict((user or {}).get('forward_caption') or {})
    cfg.setdefault('replacer', [])
    cfg.setdefault('remover', [])
    cfg.setdefault('prefix', '')
    cfg.setdefault('suffix', '')
    return cfg
async def _fe_set_forward_caption_field(self, id, field, value):
    await self.col.update_one({'id': int(id)}, {'$set': {f'forward_caption.{field}': value}}, upsert=True)
async def _fe_add_forward_replacer(self, id, old, new):
    await self.col.update_one({'id': int(id)}, {'$push': {'forward_caption.replacer': {'old': old, 'new': new}}}, upsert=True)
async def _fe_clear_forward_replacer(self, id):
    await self.col.update_one({'id': int(id)}, {'$set': {'forward_caption.replacer': []}}, upsert=True)
async def _fe_add_forward_remover(self, id, word):
    await self.col.update_one({'id': int(id)}, {'$addToSet': {'forward_caption.remover': word}}, upsert=True)
async def _fe_clear_forward_remover(self, id):
    await self.col.update_one({'id': int(id)}, {'$set': {'forward_caption.remover': []}}, upsert=True)

async def _fe_get_forward_button(self, id):
    user = await self.col.find_one({'id': int(id)})
    return (user or {}).get('forward_button') or None
async def _fe_set_forward_button(self, id, text, url):
    await self.col.update_one({'id': int(id)}, {'$set': {'forward_button': {'text': text, 'url': url, 'enabled': True}}}, upsert=True)
async def _fe_clear_forward_button(self, id):
    await self.col.update_one({'id': int(id)}, {'$unset': {'forward_button': ""}}, upsert=True)

async def _fe_get_forward_filters(self, id):
    # Empty list == no filter, forward every message type.
    user = await self.col.find_one({'id': int(id)})
    return (user or {}).get('forward_filters', []) or []
async def _fe_set_forward_filters(self, id, types: list):
    await self.col.update_one({'id': int(id)}, {'$set': {'forward_filters': types}}, upsert=True)

# ------ Extra AK Manager toggles: numbering, bullets, username/link
# remover, delta (source-link), theta (image+caption only), blast
# (parallel sends), course-sellers (remover preset) ------
_FORWARD_EXTRA_DEFAULTS = {
    'numbering_enabled': False, 'numbering_style': 'dot', 'numbering_count': 0,
    'bullets_enabled': False, 'bullet_style': 'style1',
    'username_remover': False, 'link_remover': False,
    'delta_enabled': False, 'delta_version': 'v1',
    'theta_mode': False, 'blast_mode': False, 'course_sellers_mode': False,
}
async def _fe_get_forward_extra(self, id):
    user = await self.col.find_one({'id': int(id)})
    cfg = dict((user or {}).get('forward_extra') or {})
    for k, v in _FORWARD_EXTRA_DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg
async def _fe_set_forward_extra_field(self, id, field, value):
    await self.col.update_one({'id': int(id)}, {'$set': {f'forward_extra.{field}': value}}, upsert=True)
async def _fe_inc_forward_numbering(self, id):
    doc = await self.col.find_one_and_update(
        {'id': int(id)}, {'$inc': {'forward_extra.numbering_count': 1}},
        upsert=True, return_document=True
    )
    return ((doc or {}).get('forward_extra') or {}).get('numbering_count', 1)
async def _fe_reset_forward_numbering(self, id):
    await self.col.update_one({'id': int(id)}, {'$set': {'forward_extra.numbering_count': 0}}, upsert=True)


Database.add_forward_source = _fe_add_forward_source
Database.remove_forward_source = _fe_remove_forward_source
Database.get_forward_sources = _fe_get_forward_sources
Database.add_forward_target = _fe_add_forward_target
Database.remove_forward_target = _fe_remove_forward_target
Database.get_forward_targets = _fe_get_forward_targets
Database.set_forward_mode = _fe_set_forward_mode
Database.get_forward_mode = _fe_get_forward_mode
Database.get_forward_caption_config = _fe_get_forward_caption_config
Database.set_forward_caption_field = _fe_set_forward_caption_field
Database.add_forward_replacer = _fe_add_forward_replacer
Database.clear_forward_replacer = _fe_clear_forward_replacer
Database.add_forward_remover = _fe_add_forward_remover
Database.clear_forward_remover = _fe_clear_forward_remover
Database.get_forward_button = _fe_get_forward_button
Database.set_forward_button = _fe_set_forward_button
Database.clear_forward_button = _fe_clear_forward_button
Database.get_forward_filters = _fe_get_forward_filters
Database.set_forward_filters = _fe_set_forward_filters
Database.get_forward_extra = _fe_get_forward_extra
Database.set_forward_extra_field = _fe_set_forward_extra_field
Database.inc_forward_numbering = _fe_inc_forward_numbering
Database.reset_forward_numbering = _fe_reset_forward_numbering


# =====================================================================
# FILE STORE — hybrid link sharing, batch links, auto-batch, multi-DB
# channel round-robin, and the URL-shortener access gate.
# Ported over from the File-Store project and wired onto the same
# Database class (db.<method>(...)), monkey-patched the same way the
# Forward Engine block above does it.
# =====================================================================

# ---------------------- hybrid token link system ----------------------
async def _fs_create_file_token(self, channel_id: int, msg_id: int, end_msg_id: int = None) -> str:
    """Generate a unique random token and store it. Returns the token."""
    import secrets, string as _string
    alphabet = _string.ascii_letters + _string.digits
    for _ in range(10):  # retry a few times on a random _id collision
        token = ''.join(secrets.choice(alphabet) for _ in range(14))
        try:
            await self.filestore_tokens.insert_one({
                "_id": token,
                "channel_id": channel_id,
                "msg_id": msg_id,
                "end_msg_id": end_msg_id,
                "is_batch": False,
                "created_at": datetime.datetime.utcnow(),
                "clicks": 0,
            })
            return token
        except Exception:
            continue
    raise RuntimeError("Failed to generate a unique file token after 10 attempts")

async def _fs_resolve_file_token(self, token: str):
    """Resolve a hybrid token to its stored {channel_id, msg_id, end_msg_id} doc, or None."""
    await self.filestore_tokens.update_one({"_id": token}, {"$inc": {"clicks": 1}})
    return await self.filestore_tokens.find_one({"_id": token})

async def _fs_record_invalid_token_attempt(self, user_id: int):
    now = datetime.datetime.utcnow()
    window_start = now.replace(second=0, microsecond=0)
    await self.filestore_ratelimit.update_one(
        {"user_id": user_id, "window_start": window_start},
        {"$inc": {"attempts": 1}, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

async def _fs_is_token_rate_limited(self, user_id: int, max_attempts: int = 10) -> bool:
    now = datetime.datetime.utcnow()
    window_start = now - datetime.timedelta(minutes=1)
    cursor = self.filestore_ratelimit.find({"user_id": user_id, "window_start": {"$gte": window_start}})
    total = 0
    async for doc in cursor:
        total += doc.get("attempts", 0)
    return total >= max_attempts


# ---------------------- multi-DB channel round robin ----------------------
async def _fs_get_db_channels(self) -> list:
    """Extra DB channels configured on top of the primary DB_CHANNEL."""
    doc = await self.settings_col.find_one({'_id': 'global'})
    return (doc or {}).get('fs_db_channels', []) or []

async def _fs_add_db_channel(self, channel_id: int):
    await self.settings_col.update_one(
        {'_id': 'global'}, {'$addToSet': {'fs_db_channels': int(channel_id)}}, upsert=True
    )

async def _fs_remove_db_channel(self, channel_id: int):
    await self.settings_col.update_one(
        {'_id': 'global'}, {'$pull': {'fs_db_channels': int(channel_id)}}
    )

async def _fs_is_multi_db_enabled(self) -> bool:
    doc = await self.settings_col.find_one({'_id': 'global'})
    return bool((doc or {}).get('fs_multi_db_enabled', False))

async def _fs_toggle_multi_db(self) -> bool:
    current = await self._fs_is_multi_db_enabled()
    await self.settings_col.update_one(
        {'_id': 'global'}, {'$set': {'fs_multi_db_enabled': not current}}, upsert=True
    )
    return not current

async def _fs_get_next_db_channel(self, main_channel_id: int) -> int:
    """Round-robin across [main_channel_id] + extra fs_db_channels so no
    single storage channel fills up (Telegram channels have no hard file
    cap, but this keeps content spread out and gives redundancy)."""
    if not await self._fs_is_multi_db_enabled():
        return main_channel_id
    extra = await self._fs_get_db_channels()
    if not extra:
        return main_channel_id
    all_channels = [main_channel_id] + extra
    doc = await self.settings_col.find_one({'_id': 'global'})
    current_index = (doc or {}).get('fs_db_channel_index', 0)
    selected = all_channels[current_index % len(all_channels)]
    next_index = (current_index + 1) % len(all_channels)
    await self.settings_col.update_one(
        {'_id': 'global'}, {'$set': {'fs_db_channel_index': next_index}}, upsert=True
    )
    return selected


# ---------------------- auto-batch (quality-variant grouping) ----------------------
async def _fs_get_config(self, key: str, default=None):
    doc = await self.settings_col.find_one({'_id': 'global'})
    return (doc or {}).get(key, default)

async def _fs_set_config(self, key: str, value):
    await self.settings_col.update_one({'_id': 'global'}, {'$set': {key: value}}, upsert=True)

async def _fs_add_pending_file(self, file_id: str, filename: str, base_name: str, quality: str, user_id: int, channel_id: int):
    await self.filestore_pending.insert_one({
        'file_id': file_id, 'filename': filename, 'base_name': base_name,
        'quality': quality, 'user_id': user_id, 'channel_id': channel_id,
        'timestamp': datetime.datetime.utcnow(),
    })

async def _fs_get_pending_files(self, time_window_seconds: int = 30):
    threshold = datetime.datetime.utcnow() - datetime.timedelta(seconds=time_window_seconds)
    cursor = self.filestore_pending.find({'timestamp': {'$gte': threshold}})
    return [doc async for doc in cursor]

async def _fs_create_batch(self, base_name: str, files: list) -> str:
    import secrets
    batch_id = secrets.token_hex(8)
    await self.filestore_batches.insert_one({
        'batch_id': batch_id, 'base_name': base_name, 'files': files,
        'created': datetime.datetime.utcnow(),
    })
    file_ids = [f['file_id'] for f in files]
    await self.filestore_pending.delete_many({'file_id': {'$in': file_ids}})
    return batch_id

async def _fs_get_batch(self, batch_id: str):
    return await self.filestore_batches.find_one({'batch_id': batch_id})

async def _fs_cleanup_old_pending(self, max_age_seconds: int = 120):
    threshold = datetime.datetime.utcnow() - datetime.timedelta(seconds=max_age_seconds)
    await self.filestore_pending.delete_many({'timestamp': {'$lt': threshold}})


# ---------------------- URL shortener access gate ----------------------
async def _fs_create_access_token(self, user_id: int, payload: str, token: str, expiry_minutes: int = 10):
    await self.filestore_access.insert_one({
        'user_id': user_id, 'payload': payload, 'token': token,
        'created': datetime.datetime.utcnow(), 'used': False, 'click_count': 0,
        'expires': datetime.datetime.utcnow() + datetime.timedelta(minutes=expiry_minutes),
    })

async def _fs_verify_access_token(self, user_id: int, token: str, payload: str) -> str:
    """Returns one of: OK, INVALID, EXPIRED, ALREADY_USED."""
    doc = await self.filestore_access.find_one({'user_id': user_id, 'token': token, 'payload': payload})
    if not doc:
        return "INVALID"
    if datetime.datetime.utcnow() > doc.get('expires', datetime.datetime.utcnow()):
        return "EXPIRED"
    if doc.get('used', False):
        return "ALREADY_USED"
    await self.filestore_access.update_one(
        {'_id': doc['_id']}, {'$set': {'used': True, 'used_at': datetime.datetime.utcnow()}}
    )
    return "OK"

async def _fs_increment_access_clicks(self, user_id: int, token: str):
    await self.filestore_access.update_one(
        {'user_id': user_id, 'token': token}, {'$inc': {'click_count': 1}}
    )

async def _fs_ensure_indexes(self):
    """Create MongoDB indexes for fast token lookup / TTL cleanup. Safe to
    call every startup — create_index is a no-op if the index exists."""
    try:
        await self.filestore_tokens.create_index("created_at")
        await self.filestore_access.create_index("expires")
        await self.filestore_ratelimit.create_index("user_id")
        await self.filestore_ratelimit.create_index("window_start")
        await self.filestore_pending.create_index("timestamp")
    except Exception:
        pass


Database.create_file_token = _fs_create_file_token
Database.resolve_file_token = _fs_resolve_file_token
Database.record_invalid_token_attempt = _fs_record_invalid_token_attempt
Database.is_token_rate_limited = _fs_is_token_rate_limited
Database.get_db_channels = _fs_get_db_channels
Database.add_db_channel = _fs_add_db_channel
Database.remove_db_channel = _fs_remove_db_channel
Database.is_multi_db_enabled = _fs_is_multi_db_enabled
Database.toggle_multi_db = _fs_toggle_multi_db
Database.get_next_db_channel = _fs_get_next_db_channel
Database.get_fs_config = _fs_get_config
Database.set_fs_config = _fs_set_config
Database.add_pending_file = _fs_add_pending_file
Database.get_pending_files = _fs_get_pending_files
Database.create_batch = _fs_create_batch
Database.get_batch = _fs_get_batch
Database.cleanup_old_pending = _fs_cleanup_old_pending
Database.create_access_token = _fs_create_access_token
Database.verify_access_token = _fs_verify_access_token
Database.increment_access_clicks = _fs_increment_access_clicks
Database.ensure_filestore_indexes = _fs_ensure_indexes


# ============================================================
# Maintenance Mode (Akbots/maintenance.py) — global on/off switch that,
# when enabled, makes the bot reply "under maintenance" to every
# non-admin message/callback instead of running any other plugin.
# ============================================================
async def _maint_get_mode(self) -> bool:
    doc = await self.db.settings.find_one({'_id': 'maintenance_mode'})
    return bool(doc.get('enabled', False)) if doc else False


async def _maint_set_mode(self, enabled: bool):
    await self.db.settings.update_one(
        {'_id': 'maintenance_mode'}, {'$set': {'enabled': bool(enabled)}}, upsert=True
    )


Database.get_maintenance_mode = _maint_get_mode
Database.set_maintenance_mode = _maint_set_mode


# ============================================================
# Stream/download link expiry (Akbots/set_expiry.py) — lets an admin
# change how long generated Stream/Download links stay valid at runtime,
# via /set_expiry, instead of only through the STREAM_LINK_EXPIRY env var.
# A value stored here (including explicit 0 = never expires) overrides
# the env var/config.py default; if nothing's ever been set, callers fall
# back to config.STREAM_LINK_EXPIRY.
# ============================================================
async def _expiry_get_seconds(self):
    """Returns the stored override in seconds, or None if never set (in
    which case the caller should fall back to config.STREAM_LINK_EXPIRY)."""
    doc = await self.db.settings.find_one({'_id': 'stream_link_expiry'})
    if doc is None or 'seconds' not in doc:
        return None
    return int(doc['seconds'])


async def _expiry_set_seconds(self, seconds: int):
    await self.db.settings.update_one(
        {'_id': 'stream_link_expiry'}, {'$set': {'seconds': int(seconds)}}, upsert=True
    )


async def _expiry_clear_override(self):
    """Removes the override entirely so get_link_expiry_seconds() goes
    back to returning None (i.e. callers fall back to the env default)."""
    await self.db.settings.delete_one({'_id': 'stream_link_expiry'})


Database.get_link_expiry_seconds = _expiry_get_seconds
Database.set_link_expiry_seconds = _expiry_set_seconds
Database.clear_link_expiry_override = _expiry_clear_override


# ============================================================
# File-to-Link persistent link storage (Akbots/filetolink/) — backs
# STREAM_LINK_EXPIRY so expiry survives a bot restart, instead of the
# in-memory-only tracking the first cut of this feature shipped with.
# ============================================================
async def _ftl_save_link(self, msg_id: int, file_name: str, hash_str: str, file_size: int = 0):
    await self.db.stream_links.update_one(
        {'_id': int(msg_id)},
        {'$set': {
            'file_name': file_name,
            'hash': hash_str,
            'file_size': file_size,
            'timestamp': time.time(),
        }},
        upsert=True,
    )


async def _ftl_get_link_timestamp(self, msg_id: int):
    doc = await self.db.stream_links.find_one({'_id': int(msg_id)})
    return doc.get('timestamp') if doc else None


async def _ftl_ensure_indexes(self):
    try:
        await self.db.stream_links.create_index("timestamp")
    except Exception:
        pass


Database.save_stream_link = _ftl_save_link
Database.get_stream_link_timestamp = _ftl_get_link_timestamp
Database.ensure_stream_link_indexes = _ftl_ensure_indexes


# ============================================================
# Storage stats (Akbots/storageinfo.py — /storageinfo) — aggregates the
# stream_links collection (every video the File-to-Link feature has ever
# served a streaming link for) into a total file count + total bytes, so
# /storageinfo doesn't need to page through every document itself.
# ============================================================
async def _storage_get_stream_link_stats(self):
    count = await self.db.stream_links.count_documents({})
    total_size = 0
    pipeline = [{"$group": {"_id": None, "total_size": {"$sum": "$file_size"}}}]
    async for doc in self.db.stream_links.aggregate(pipeline):
        total_size = doc.get("total_size") or 0
    oldest = await self.db.stream_links.find_one(sort=[("timestamp", 1)])
    newest = await self.db.stream_links.find_one(sort=[("timestamp", -1)])
    return {
        "count": count,
        "total_size": total_size,
        "oldest_ts": oldest.get("timestamp") if oldest else None,
        "newest_ts": newest.get("timestamp") if newest else None,
    }


Database.get_stream_link_stats = _storage_get_stream_link_stats
