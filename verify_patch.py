# Akbots - Don't Remove Credit - @AkBots_Official
#
# Minimal verification-gate shim. Akbots/terabox.py (ported from the
# MN-BOTS project) imports these names to optionally require users to
# "verify" (e.g. via a shortener) before using a command. This project
# doesn't ship that shortener-verification flow, so the gate is disabled
# by default (IS_VERIFY = False) — every plugin that imports from here
# will simply skip the check. Flip IS_VERIFY to True and fill in
# is_verified()/build_verification_link() with real logic (token storage
# in database/db.py + a shortener call, mirroring Akbots/shortener_bypass.py)
# if you want to actually gate a command behind verification.

IS_VERIFY = False
HOW_TO_VERIFY = "https://t.me/AkBots_Official"


async def is_verified(user_id: int) -> bool:
    """Always True while IS_VERIFY is False (gate disabled)."""
    return True


async def build_verification_link(bot_username: str, user_id: int) -> str:
    """Stub link; only ever shown if IS_VERIFY is turned on above."""
    return f"https://t.me/{bot_username}?start=verify_{user_id}"
