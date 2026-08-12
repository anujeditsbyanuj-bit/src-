# Akbots
# Don't Remove Credit
# Telegram Channel @AkBots_Official
#
# Heroku app-management commands, ported from the "GITHUB-HEROKU" repo's
# daxxop bot (daxxop/modules/heroku.py) into Akbotz's own plugin system.
# The original module was gated on a single hardcoded OWNER_ID and imported
# its own pyrogram Client (`from daxxop import daxxop as app`) — here
# that's replaced with Akbotz's generic `@Client.on_message` pattern and
# the multi-user ADMINS list (config.py), matching Akbots/github_tools.py.
#
# Commands (all ADMINS-only):
#   /createapp <name>                      - create a new Heroku app
#   /addapp <app_name> <email>             - add a collaborator to an app
#   /removeapp <app_name> <email>          - remove a collaborator from an app
#   /herokulogs <app_name>                 - fetch and send an app's logs
#   /herokuinfo                            - account info + all apps/dynos
#   /delheroku <app_name>                  - delete a Heroku app
#   /veriable <app_name>                   - fetch an app's config vars/env
#   /apps                                  - list all apps on the account
#   /restartdynos <app_name>               - restart (recycle) an app's dynos
#   /rename <old_app_name> <new_app_name>  - rename an app
#   /setherokutoken <api_key>              - set the Heroku API key from inside
#                                             Telegram (stored in the database),
#                                             instead of editing config.py /
#                                             the HEROKU_API env var
#
# Requires a Heroku API key (Account Settings -> API Key on
# dashboard.heroku.com), resolved per-request: the value set via
# /setherokutoken (stored in the database) if present, else config.py's
# HEROKU_API env var. Without either, every command below replies with a
# short "not configured" message instead of failing with a raw exception.

import os

import requests
from pyrogram import Client, filters
from pyrogram.types import Message

from config import ADMINS, HEROKU_API
from database.db import db

try:
    from heroku3 import from_key as heroku_from_key
except ImportError:
    heroku_from_key = None

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'

HEROKU_API_BASE = "https://api.heroku.com"


async def _resolve_heroku_key():
    """DB-stored key (set via /setherokutoken) first, else config.py/env HEROKU_API."""
    stored = await db.get_heroku_token()
    return stored or HEROKU_API or None


def heroku3_available() -> bool:
    return heroku_from_key is not None


NOT_CONFIGURED = (
    f"{E_WARN} <b>ʜᴇʀᴏᴋᴜ ᴛᴏᴏʟs ᴀʀᴇɴ'ᴛ ᴄᴏɴғɪɢᴜʀᴇᴅ.</b>\n"
    "Use <code>/setherokutoken &lt;api_key&gt;</code> (from Account Settings on "
    "dashboard.heroku.com) to enable these commands."
)

NOT_INSTALLED = (
    f"{E_WARN} The <code>heroku3</code> package isn't installed. Run "
    "<code>pip install heroku3</code>."
)


def _headers(key: str) -> dict:
    return {
        "Accept": "application/vnd.heroku+json; version=3",
        "Authorization": f"Bearer {key}",
    }


# ---------------------------------------------------------------------------
# /setherokutoken <api_key>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("setherokutoken") & filters.user(ADMINS))
async def set_heroku_token_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/setherokutoken &lt;heroku_api_key&gt;</code>\n"
            "Tip: delete your message after sending this so the key doesn't sit in chat history."
        )

    key = message.command[1]
    resp = requests.get(f"{HEROKU_API_BASE}/account", headers=_headers(key))
    if resp.status_code != 200:
        return await message.reply_text(f"{E_CROSS} That key doesn't look valid: {resp.text}")

    await db.set_heroku_token(key)
    email = resp.json().get("email", "unknown")
    await message.reply_text(f"{E_CHECK} Heroku API key set (account: <b>{email}</b>).")
    try:
        await message.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /createapp <name>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("createapp") & filters.user(ADMINS))
async def create_app_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/createapp &lt;app_name&gt;</code>"
        )

    app_name = message.command[1]
    resp = requests.post(
        f"{HEROKU_API_BASE}/apps", json={"name": app_name}, headers=_headers(key)
    )
    if resp.status_code == 201:
        await message.reply_text(f"{E_CHECK} Heroku app '{app_name}' created successfully!")
    else:
        await message.reply_text(f"{E_CROSS} Failed to create Heroku app: {resp.text}")


# ---------------------------------------------------------------------------
# /addapp <app_name> <email>  /  /removeapp <app_name> <email>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("addapp") & filters.user(ADMINS))
async def add_collaboration_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)
    if not heroku3_available():
        return await message.reply_text(NOT_INSTALLED)

    if len(message.command) != 3:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/addapp &lt;app_name&gt; &lt;email&gt;</code>"
        )

    app_name, email = message.command[1], message.command[2]
    try:
        heroku_conn = heroku_from_key(key)
        if not heroku_conn.apps().get(app_name):
            return await message.reply_text(f"{E_CROSS} App '{app_name}' not found on Heroku.")
        heroku_conn.apps()[app_name].add_collaborator(email)
        await message.reply_text(f"{E_CHECK} '{email}' added as a collaborator on '{app_name}'.")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error adding collaborator: {e}")


@Client.on_message(filters.command("removeapp") & filters.user(ADMINS))
async def remove_collaboration_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)
    if not heroku3_available():
        return await message.reply_text(NOT_INSTALLED)

    if len(message.command) != 3:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/removeapp &lt;app_name&gt; &lt;email&gt;</code>"
        )

    app_name, email = message.command[1], message.command[2]
    try:
        heroku_conn = heroku_from_key(key)
        if not heroku_conn.apps().get(app_name):
            return await message.reply_text(f"{E_CROSS} App '{app_name}' not found on Heroku.")
        heroku_conn.apps()[app_name].remove_collaborator(email)
        await message.reply_text(f"{E_CHECK} '{email}' removed as a collaborator on '{app_name}'.")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error removing collaborator: {e}")


# ---------------------------------------------------------------------------
# /herokulogs <app_name>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("herokulogs") & filters.user(ADMINS))
async def heroku_logs_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)
    if not heroku3_available():
        return await message.reply_text(NOT_INSTALLED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/herokulogs &lt;app_name&gt;</code>"
        )

    app_name = message.command[1]
    log_path = f"/tmp/{app_name}_heroku_log.txt"
    try:
        heroku_conn = heroku_from_key(key)
        if not heroku_conn.apps().get(app_name):
            return await message.reply_text(f"{E_CROSS} App '{app_name}' not found on Heroku.")

        logs = heroku_conn.apps()[app_name].get_log()
        with open(log_path, "w") as f:
            f.write(logs)

        await message.reply_document(log_path, caption=f"<blockquote>Heroku logs for '{app_name}'.</blockquote>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error fetching Heroku logs: {e}")
    finally:
        if os.path.exists(log_path):
            os.remove(log_path)


# ---------------------------------------------------------------------------
# /herokuinfo
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("herokuinfo") & filters.user(ADMINS))
async def heroku_info_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)
    if not heroku3_available():
        return await message.reply_text(NOT_INSTALLED)

    try:
        account_resp = requests.get(f"{HEROKU_API_BASE}/account", headers=_headers(key))
        if account_resp.status_code != 200:
            return await message.reply_text(
                f"{E_CROSS} Error retrieving Heroku account info: {account_resp.text}"
            )
        account = account_resp.json()

        heroku_conn = heroku_from_key(key)
        apps = heroku_conn.apps()

        app_rows = []
        total_dynos = 0
        for heroku_app in apps:
            dynos = list(heroku_app.dynos())
            dynos_on = any(d.state == "up" for d in dynos)
            total_dynos += len(dynos)
            app_rows.append((heroku_app.name, dynos_on, len(dynos)))

        info_text = (
            f"<b>ᴍᴜʟᴛɪ-ғᴀᴄᴛᴏʀ ᴀᴜᴛʜᴇɴᴛɪᴄᴀᴛɪᴏɴ:</b> {account.get('two_factor_authentication')}\n"
            f"<b>ᴇᴍᴀɪʟ:</b> {account.get('email')}\n"
            f"<b>ɴᴀᴍᴇ:</b> {account.get('name')}\n"
            f"<b>ᴛᴏᴛᴀʟ ᴀᴘᴘs:</b> {len(app_rows)}\n"
            f"<b>ᴛᴏᴛᴀʟ ᴅʏɴᴏs:</b> {total_dynos}\n"
        )
        for name, dynos_on, dyno_count in app_rows:
            info_text += (
                f"\n<b>ᴀᴘᴘ:</b> {name}\n"
                f"<b>ᴅʏɴᴏs:</b> {'On' if dynos_on else 'Off'}, Total: {dyno_count}\n"
            )

        await message.reply_text(info_text)
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error: {e}")


# ---------------------------------------------------------------------------
# /delheroku <app_name>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("delheroku") & filters.user(ADMINS))
async def delete_heroku_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)
    if not heroku3_available():
        return await message.reply_text(NOT_INSTALLED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/delheroku &lt;app_name&gt;</code>"
        )

    app_name = message.command[1]
    try:
        heroku_conn = heroku_from_key(key)
        if not heroku_conn.apps().get(app_name):
            return await message.reply_text(f"{E_CROSS} App '{app_name}' not found on Heroku.")
        heroku_conn.apps()[app_name].delete()
        await message.reply_text(f"{E_CHECK} Heroku app '{app_name}' has been deleted.")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error deleting Heroku app: {e}")


# ---------------------------------------------------------------------------
# /veriable <app_name>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("veriable") & filters.user(ADMINS))
async def heroku_variables_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)
    if not heroku3_available():
        return await message.reply_text(NOT_INSTALLED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/veriable &lt;app_name&gt;</code>"
        )

    app_name = message.command[1]
    vars_path = f"/tmp/{app_name}_heroku_vars.txt"
    try:
        heroku_conn = heroku_from_key(key)
        if not heroku_conn.apps().get(app_name):
            return await message.reply_text(f"{E_CROSS} App '{app_name}' not found on Heroku.")

        config_vars = heroku_conn.apps()[app_name].config().to_dict()
        with open(vars_path, "w") as f:
            for cfg_key, value in config_vars.items():
                f.write(f"{cfg_key}={value}\n")

        await message.reply_document(
            vars_path, caption=f"<blockquote>Environment variables for '{app_name}'.</blockquote>", parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error fetching env variables: {e}")
    finally:
        if os.path.exists(vars_path):
            os.remove(vars_path)


# ---------------------------------------------------------------------------
# /apps
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("apps") & filters.user(ADMINS))
async def heroku_apps_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)
    if not heroku3_available():
        return await message.reply_text(NOT_INSTALLED)

    try:
        heroku_conn = heroku_from_key(key)
        app_names = [a.name for a in heroku_conn.apps()]
        if app_names:
            await message.reply_text("\n".join(app_names))
        else:
            await message.reply_text(f"{E_WARN} No Heroku apps found for this account.")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error fetching Heroku apps: {e}")


# ---------------------------------------------------------------------------
# /restartdynos <app_name>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("restartdynos") & filters.user(ADMINS))
async def restart_dynos_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/restartdynos &lt;app_name&gt;</code>"
        )

    app_name = message.command[1]
    resp = requests.delete(f"{HEROKU_API_BASE}/apps/{app_name}/dynos", headers=_headers(key))
    if resp.status_code == 200:
        await message.reply_text(f"{E_CHECK} Dynos for '{app_name}' restarted successfully.")
    else:
        await message.reply_text(
            f"{E_CROSS} Failed to restart dynos. Status {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# /rename <old_app_name> <new_app_name>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("rename") & filters.user(ADMINS))
async def rename_app_command(client: Client, message: Message):
    key = await _resolve_heroku_key()
    if not key:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 3:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/rename &lt;old_app_name&gt; &lt;new_app_name&gt;</code>"
        )

    old_name, new_name = message.command[1], message.command[2]
    try:
        info_resp = requests.get(f"{HEROKU_API_BASE}/apps/{old_name}", headers=_headers(key))
        info_resp.raise_for_status()
        app_id = info_resp.json()["id"]

        rename_resp = requests.patch(
            f"{HEROKU_API_BASE}/apps/{app_id}", json={"name": new_name}, headers=_headers(key)
        )
        rename_resp.raise_for_status()

        await message.reply_text(f"{E_CHECK} Heroku app '{old_name}' renamed to '{new_name}'.")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error renaming app: {e}")
