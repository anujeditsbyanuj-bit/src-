# Akbots
# Don't Remove Credit
# Telegram Channel @AkBots_Official
#
# Multi-account GitHub token switching, ported from the "GitHub Auto
# Uploader Pro" CLI tool's config.json account list (save_account,
# switch_github_account, get_active_account). There it was one desktop
# user's local file; here every Telegram ADMIN gets their own saved
# GitHub username/token pairs in the database (see the gh_accounts
# methods appended to database/db.py), and Akbots/repo_upload.py resolves
# whichever account is "active" for that admin (falling back to the
# global GIT_TOKEN from config.py if they haven't added a personal one).
#
# Commands (all ADMINS-only):
#   /addaccount <token>       - validate a GitHub token, save it, make it active (personal, per-admin)
#   /accounts                 - list your saved accounts and which is active
#   /useaccount <username>    - switch your active account
#   /removeaccount <username> - forget a saved account
#   /setgittoken <token>      - set the bot-wide shared GitHub token from inside
#                               Telegram (stored in the database), instead of
#                               editing config.py / the GIT_TOKEN env var

import asyncio
import time
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import ADMINS, GIT_TOKEN
from database.db import db
from Akbots.direct_utils import safe_edit

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN = '<emoji id=5447644880824181073>⚠️</emoji>'

GITHUB_API = "https://api.github.com"
MAX_ACCOUNTS = 10


async def resolve_github_token(user_id: int):
    """Active per-user account first, then the bot-wide token set via /setgittoken,
    then the config.py/env GIT_TOKEN. Returns (username, token, is_personal)."""
    username, token = await db.get_gh_active_account(user_id)
    if token:
        return username, token, True
    stored_token = await db.get_git_token()
    if stored_token:
        return None, stored_token, False
    if GIT_TOKEN:
        return None, GIT_TOKEN, False
    return None, None, False


def _check_rate_limit(token: str):
    """Returns (remaining, limit, reset_epoch, is_valid) for a token, using
    GitHub's own /rate_limit endpoint (checking this never itself counts
    against the limit). is_valid=False means the token is invalid/revoked
    (401), not just rate-limited."""
    try:
        resp = requests.get(
            f"{GITHUB_API}/rate_limit",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=10,
        )
    except Exception:
        return 0, 0, 0, False
    if resp.status_code == 401:
        return 0, 0, 0, False
    if resp.status_code != 200:
        return 0, 0, 0, True  # can't tell for sure, assume token itself is fine
    core = resp.json().get("resources", {}).get("core", {})
    return core.get("remaining", 0), core.get("limit", 0), core.get("reset", 0), True


async def resolve_github_token_with_rotation(user_id: int, min_remaining: int = 10):
    """Like resolve_github_token(), but auto-rotates across ALL of this
    user's saved accounts to avoid one that's close to/at its rate limit —
    the "Auto Token Rotation" feature. Checks the currently-active account
    first; if it has fewer than `min_remaining` requests left (or the token
    turns out to be invalid/revoked), tries the other saved accounts in
    turn and switches active to the first one that's usable. If every
    saved account is exhausted/invalid, falls back to whichever had the
    most remaining requests rather than failing outright.

    Falls back to plain resolve_github_token() when there's 0 or 1
    accounts saved (nothing to rotate to) or none saved at all.
    """
    accounts, active_username = await db.gh_list_accounts(user_id)
    if len(accounts) <= 1:
        return await resolve_github_token(user_id)

    ordered_usernames = ([active_username] if active_username in accounts else []) + \
        [u for u in accounts if u != active_username]

    best_username, best_token, best_remaining = None, None, -1
    for uname in ordered_usernames:
        tok = accounts[uname].get("token")
        if not tok:
            continue
        remaining, _limit, _reset, is_valid = await asyncio.to_thread(_check_rate_limit, tok)
        if not is_valid:
            continue
        if remaining >= min_remaining:
            if uname != active_username:
                await db.set_gh_active_account(user_id, uname)
            return uname, tok, True
        if remaining > best_remaining:
            best_username, best_token, best_remaining = uname, tok, remaining

    if best_token:
        # Every account is below min_remaining, but this one has the most
        # headroom left — better than refusing to work at all.
        if best_username != active_username:
            await db.set_gh_active_account(user_id, best_username)
        return best_username, best_token, True

    # Every saved account came back invalid (401) — fall back to whatever
    # resolve_github_token() would have returned (bot-wide/env token).
    return await resolve_github_token(user_id)


def _fetch_github_username(token: str):
    """Returns (username, error_detail). username is None on failure, with
    error_detail explaining why (bad token, rate-limited, network issue, etc.)
    instead of the caller only ever seeing a generic 'invalid' message."""
    try:
        resp = requests.get(
            f"{GITHUB_API}/user",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=10,
        )
    except Exception as e:
        return None, f"couldn't reach GitHub ({e})"

    if resp.status_code == 200:
        return resp.json().get("login"), None
    if resp.status_code == 401:
        return None, "GitHub rejected it as invalid/expired (401 Bad credentials)"
    if resp.status_code == 403:
        return None, "forbidden — likely rate-limited, or an SSO-restricted/org token (403)"
    return None, f"GitHub API error {resp.status_code}: {resp.text[:200]}"


# ---------------------------------------------------------------------------
# /addaccount <token>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("addaccount") & filters.user(ADMINS))
async def add_account_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/addaccount &lt;github_personal_access_token&gt;</code>\n"
            "Tip: delete your message after sending this so the token doesn't sit in chat history."
        )

    token = message.command[1]
    username, error_detail = _fetch_github_username(token)
    if not username:
        return await message.reply_text(
            f"{E_CROSS} Couldn't use that token — {error_detail}."
        )

    existing, _ = await db.gh_list_accounts(message.from_user.id)
    if username not in existing and len(existing) >= MAX_ACCOUNTS:
        return await message.reply_text(
            f"{E_CROSS} You already have {MAX_ACCOUNTS} accounts saved (the max). "
            f"Remove one with <code>/removeaccount &lt;username&gt;</code> first."
        )

    await db.save_gh_account(message.from_user.id, username, token)
    await message.reply_text(
        f"{E_CHECK} Saved and switched to GitHub account <b>{username}</b>."
    )
    try:
        await message.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /accounts
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("accounts") & filters.user(ADMINS))
async def list_accounts_command(client: Client, message: Message):
    accounts, active_username = await db.gh_list_accounts(message.from_user.id)
    if not accounts:
        return await message.reply_text(
            f"{E_WARN} No personal GitHub accounts saved yet. Use "
            "<code>/addaccount &lt;token&gt;</code> to add one — until then, "
            "GitHub commands fall back to the bot-wide token (set via "
            "<code>/setgittoken</code>, or the GIT_TOKEN env var)."
        )

    lines = []
    for username in sorted(accounts):
        marker = f" {E_CHECK} (active)" if username == active_username else ""
        lines.append(f"• {username}{marker}")

    await message.reply_text("<b>ʏᴏᴜʀ sᴀᴠᴇᴅ ɢɪᴛʜᴜʙ ᴀᴄᴄᴏᴜɴᴛs:</b>\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# /useaccount <username>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("useaccount") & filters.user(ADMINS))
async def use_account_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(f"{E_WARN} Usage: <code>/useaccount &lt;username&gt;</code>")

    username = message.command[1]
    ok = await db.set_gh_active_account(message.from_user.id, username)
    if ok:
        await message.reply_text(f"{E_CHECK} Switched to GitHub account <b>{username}</b>.")
    else:
        await message.reply_text(
            f"{E_CROSS} No saved account named '{username}'. Check <code>/accounts</code>."
        )


# ---------------------------------------------------------------------------
# /removeaccount <username>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("removeaccount") & filters.user(ADMINS))
async def remove_account_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(f"{E_WARN} Usage: <code>/removeaccount &lt;username&gt;</code>")

    username = message.command[1]
    ok = await db.remove_gh_account(message.from_user.id, username)
    if ok:
        await message.reply_text(f"{E_CHECK} Removed saved account '{username}'.")
    else:
        await message.reply_text(
            f"{E_CROSS} No saved account named '{username}'. Check <code>/accounts</code>."
        )


# ---------------------------------------------------------------------------
# /setgittoken <token>  - bot-wide shared token (used when no admin has a
# personal account active via /addaccount), stored in the database instead
# of config.py / the GIT_TOKEN environment variable.
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("setgittoken") & filters.user(ADMINS))
async def set_git_token_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/setgittoken &lt;github_personal_access_token&gt;</code>\n"
            "Tip: delete your message after sending this so the token doesn't sit in chat history."
        )

    token = message.command[1]
    username, error_detail = _fetch_github_username(token)
    if not username:
        return await message.reply_text(
            f"{E_CROSS} Couldn't use that token — {error_detail}."
        )

    await db.set_git_token(token)
    await message.reply_text(
        f"{E_CHECK} Bot-wide GitHub token set (account: <b>{username}</b>). "
        "Admins without a personal /addaccount will use this by default."
    )
    try:
        await message.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /tokens — Token Manager: view, check, and refresh the status of every
# saved GitHub token (remaining rate limit, reset time, validity) in one
# place, instead of finding out a token is exhausted/revoked only when an
# upload fails.
# ---------------------------------------------------------------------------
@Client.on_message(filters.command(["tokens", "tokenmanager"]) & filters.user(ADMINS))
async def token_manager_command(client: Client, message: Message):
    accounts, active_username = await db.gh_list_accounts(message.from_user.id)
    if not accounts:
        return await message.reply_text(
            f"{E_WARN} No personal GitHub accounts saved yet. Use "
            "<code>/addaccount &lt;token&gt;</code> to add one."
        )

    status = await message.reply_text(f"{E_WARN} Checking {len(accounts)} token(s)...")
    lines = [f"<b>📊 Token Manager ({len(accounts)}/{MAX_ACCOUNTS}):</b>", ""]
    for username, acc in accounts.items():
        token = acc.get("token")
        remaining, limit, reset, is_valid = await asyncio.to_thread(_check_rate_limit, token)
        marker = f" {E_CHECK} active" if username == active_username else ""
        if not is_valid:
            lines.append(f"• <b>{username}</b>{marker} — {E_CROSS} invalid/revoked token")
            continue
        reset_str = ""
        if reset:
            mins = max(0, int((reset - time.time()) / 60))
            reset_str = f", resets in {mins}m" if mins else ", resets shortly"
        health = "🟢" if remaining > 100 else ("🟡" if remaining > 10 else "🔴")
        lines.append(f"• <b>{username}</b>{marker} — {health} {remaining}/{limit or '?'} left{reset_str}")

    lines.append("")
    lines.append("<i>Auto-rotation picks the healthiest token automatically on upload.</i>")
    await safe_edit(status.edit_text, "\n".join(lines), parse_mode=enums.ParseMode.HTML)


# ---------------------------------------------------------------------------
# /exportgh — Export Summary: a downloadable text file with your saved
# GitHub accounts (usernames only — tokens are never included in the
# export, for safety) and your recent /uploadrepo history.
# ---------------------------------------------------------------------------
@Client.on_message(filters.command(["exportgh", "exportsummary"]) & filters.user(ADMINS))
async def export_summary_command(client: Client, message: Message):
    accounts, active_username = await db.gh_list_accounts(message.from_user.id)
    uploads = await db.get_gh_upload_log(message.from_user.id, limit=50)

    lines = ["Akbotz GitHub Uploader — Summary Export", "=" * 44, ""]
    lines.append(f"Saved accounts ({len(accounts)}/{MAX_ACCOUNTS}):")
    if accounts:
        for username in sorted(accounts):
            marker = " (active)" if username == active_username else ""
            lines.append(f"  - {username}{marker}")
    else:
        lines.append("  (none saved)")

    lines.append("")
    lines.append(f"Recent uploads ({len(uploads)}):")
    if uploads:
        for u in uploads:
            when = u.get("at")
            when_str = when.strftime("%Y-%m-%d %H:%M UTC") if hasattr(when, "strftime") else str(when)
            lines.append(f"  - {when_str} — {u.get('gh_username')} -> {u.get('repo')}")
    else:
        lines.append("  (no uploads logged yet)")

    lines.append("")
    lines.append("Note: tokens are never included in this export for security.")

    import io
    buf = io.BytesIO("\n".join(lines).encode("utf-8"))
    buf.name = "akbotz_github_summary.txt"
    await message.reply_document(
        buf, caption=f"<blockquote>{E_CHECK} Your GitHub token & upload summary.</blockquote>",
    )
