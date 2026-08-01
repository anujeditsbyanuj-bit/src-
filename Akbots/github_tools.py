# Akbots
# Don't Remove Credit
# Telegram Channel @AkBots_Official
#
# GitHub repo-management commands, wired in from the "GITHUB-HEROKU" repo's
# daxxop bot into Akbotz's own plugin system. Original modules there each
# imported their own pyrogram Client instance (`from daxxop import daxxop
# as app`) — here they're merged into one file using the generic
# `@Client.on_message` pattern that every other Akbots/*.py plugin uses, so
# pyrogram's plugins=dict(root="Akbots") loader in bot.py picks it up
# automatically. No changes needed anywhere else to enable this file.
#
# Commands (all ADMINS-only unless noted):
#   /create_repo <name>                         - create a new GitHub repo
#   /delrepo <github_repo_url>                   - delete a GitHub repo
#   /downloadrepo <github_repo_url>              - clone + zip + send a repo
#   /fork <github_repo_url>                      - fork a single repo
#   /forkall <github_username>                   - fork all repos of a user
#   /add_collaborator <repo_url> <username>      - add a collaborator
#   /remove_collaborator <repo_url> <username>   - remove a collaborator
#   /gitprivate <repo_url>                       - set a repo to private
#   /gitpublic <repo_url>                        - set a repo to public
#   /github <username>  or  /git <username>      - GitHub user profile lookup (public)
#   /allrepo <username>                          - list all public repos of a user (public)
#
# Requires a GitHub Personal Access Token with `repo` + `delete_repo`
# scopes, resolved per-request via Akbots/github_accounts.py.resolve_github_token
# (checks: your personal /addaccount, then the bot-wide /setgittoken value,
# then config.py's GIT_TOKEN env var, in that order). Without any of those,
# every command below replies with a short "not configured" message instead
# of failing with a raw exception.

import os
import shutil

import requests
from pyrogram import Client, filters
from pyrogram.types import Message

from config import ADMINS
from Akbots.github_accounts import resolve_github_token

try:
    import git  # GitPython, used by /downloadrepo
except ImportError:
    git = None

try:
    from github import Github  # PyGithub, used by /fork, /forkall, collaborators
except ImportError:
    Github = None

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN = '<emoji id=5447644880824181073>⚠️</emoji>'

GITHUB_API = "https://api.github.com"


def _split_owner_repo(url: str):
    """'https://github.com/owner/repo' (or 'owner/repo') -> (owner, repo)."""
    parts = url.strip().rstrip("/").split("/")
    return parts[-2], parts[-1].removesuffix(".git")


NOT_CONFIGURED = (
    f"{E_WARN} <b>GitHub tools aren't configured.</b>\n"
    "Use <code>/addaccount &lt;token&gt;</code> (personal) or "
    "<code>/setgittoken &lt;token&gt;</code> (bot-wide) to set a GitHub "
    "Personal Access Token with <code>repo</code> + <code>delete_repo</code> scopes."
)


# ---------------------------------------------------------------------------
# /create_repo <name>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("create_repo") & filters.user(ADMINS))
async def create_repo_command(client: Client, message: Message):
    _, token, _ = await resolve_github_token(message.from_user.id)
    if not token:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/create_repo &lt;repository_name&gt;</code>"
        )

    repo_name = message.command[1]
    resp = requests.post(
        f"{GITHUB_API}/user/repos",
        headers={"Authorization": f"token {token}"},
        json={"name": repo_name, "auto_init": True},
    )

    if resp.status_code == 201:
        repo_link = resp.json().get("html_url")
        await message.reply_text(
            f"{E_CHECK} <b>Repository created.</b>\n{repo_link}"
        )
    else:
        await message.reply_text(f"{E_CROSS} Failed to create repo: {resp.text}")


# ---------------------------------------------------------------------------
# /delrepo <github_repo_url>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("delrepo") & filters.user(ADMINS))
async def delete_repo_command(client: Client, message: Message):
    _, token, _ = await resolve_github_token(message.from_user.id)
    if not token:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/delrepo &lt;github_repo_url&gt;</code>"
        )

    try:
        owner, repo_name = _split_owner_repo(message.command[1])
        resp = requests.delete(
            f"{GITHUB_API}/repos/{owner}/{repo_name}", headers={"Authorization": f"token {token}"}
        )
        if resp.status_code == 204:
            await message.reply_text(f"{E_CHECK} Deleted {owner}/{repo_name}.")
        else:
            await message.reply_text(
                f"{E_CROSS} Failed to delete repo. Status {resp.status_code}: {resp.text}"
            )
    except Exception as e:
        await message.reply_text(f"{E_CROSS} An error occurred: {e}")


# ---------------------------------------------------------------------------
# /downloadrepo <github_repo_url>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("downloadrepo") & filters.user(ADMINS))
async def download_repo_command(client: Client, message: Message):
    if git is None:
        return await message.reply_text(
            f"{E_WARN} GitPython isn't installed. Run "
            "<code>pip install GitPython</code>."
        )

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/downloadrepo &lt;github_repo_url&gt;</code>"
        )

    repo_url = message.command[1]
    repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    repo_path = os.path.join("/tmp", repo_name)
    status = await message.reply_text(f"{E_CHECK} Cloning {repo_name}...")

    try:
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)
        git.Repo.clone_from(repo_url, repo_path)
        zip_base = os.path.join("/tmp", repo_name)
        zip_path = shutil.make_archive(zip_base, "zip", repo_path)

        await message.reply_document(zip_path, caption=f"<blockquote>{repo_name}.zip</blockquote>")
        os.remove(zip_path)
    except Exception as e:
        await status.edit_text(f"{E_CROSS} Unable to download that repo: {e}")
    else:
        await status.delete()
    finally:
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# /fork <github_repo_url>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("fork") & filters.user(ADMINS))
async def fork_command(client: Client, message: Message):
    _, token, _ = await resolve_github_token(message.from_user.id)
    if not token:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/fork &lt;github_repo_url&gt;</code>"
        )

    try:
        owner, repo_name = _split_owner_repo(message.command[1])
        resp = requests.post(
            f"{GITHUB_API}/repos/{owner}/{repo_name}/forks", headers={"Authorization": f"token {token}"}
        )
        if resp.status_code == 202:
            await message.reply_text(f"{E_CHECK} Forked. Check your GitHub!")
        else:
            await message.reply_text(
                f"{E_CROSS} Failed to fork. Status {resp.status_code}: {resp.text}"
            )
    except Exception as e:
        await message.reply_text(f"{E_CROSS} An error occurred: {e}")


# ---------------------------------------------------------------------------
# /forkall <github_username>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("forkall") & filters.user(ADMINS))
async def fork_all_command(client: Client, message: Message):
    _, token, _ = await resolve_github_token(message.from_user.id)
    if not token or Github is None:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/forkall &lt;github_username&gt;</code>"
        )

    target_username = message.command[1]
    try:
        gh = Github(token)
        user = gh.get_user(target_username)
        count = 0
        for repo in user.get_repos():
            repo.create_fork()
            count += 1
        await message.reply_text(
            f"{E_CHECK} Forked {count} repositories from {target_username}."
        )
    except Exception as e:
        await message.reply_text(f"{E_CROSS} An error occurred: {e}")


# ---------------------------------------------------------------------------
# /add_collaborator <repo_url> <username>  /  /remove_collaborator ...
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("add_collaborator") & filters.user(ADMINS))
async def add_collaborator_command(client: Client, message: Message):
    _, token, _ = await resolve_github_token(message.from_user.id)
    if not token or Github is None:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 3:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/add_collaborator &lt;repo_url&gt; &lt;github_username&gt;</code>"
        )

    repo_url, username = message.command[1], message.command[2]
    try:
        owner, repo_name = _split_owner_repo(repo_url)
        gh = Github(token)
        repo = gh.get_repo(f"{owner}/{repo_name}")
        repo.add_to_collaborators(username, "push")
        await message.reply_text(f"{E_CHECK} {username} added as a collaborator.")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} An error occurred: {e}")


@Client.on_message(filters.command("remove_collaborator") & filters.user(ADMINS))
async def remove_collaborator_command(client: Client, message: Message):
    _, token, _ = await resolve_github_token(message.from_user.id)
    if not token or Github is None:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 3:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/remove_collaborator &lt;repo_url&gt; &lt;github_username&gt;</code>"
        )

    repo_url, username = message.command[1], message.command[2]
    try:
        owner, repo_name = _split_owner_repo(repo_url)
        gh = Github(token)
        repo = gh.get_repo(f"{owner}/{repo_name}")
        repo.remove_from_collaborators(username)
        await message.reply_text(f"{E_CHECK} {username} removed as a collaborator.")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} An error occurred: {e}")


# ---------------------------------------------------------------------------
# /gitprivate <repo_url>  /  /gitpublic <repo_url>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command(["gitprivate", "gitpublic"]) & filters.user(ADMINS))
async def change_repo_visibility_command(client: Client, message: Message):
    _, token, _ = await resolve_github_token(message.from_user.id)
    if not token:
        return await message.reply_text(NOT_CONFIGURED)

    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/{message.command[0]} &lt;github_repo_url&gt;</code>"
        )

    try:
        owner, repo_name = _split_owner_repo(message.command[1])
        is_private = message.command[0] == "gitprivate"
        resp = requests.patch(
            f"{GITHUB_API}/repos/{owner}/{repo_name}",
            headers={"Authorization": f"token {token}"},
            json={"private": is_private},
        )
        if resp.status_code == 200:
            visibility = "private" if is_private else "public"
            await message.reply_text(
                f"{E_CHECK} {owner}/{repo_name} set to {visibility}."
            )
        else:
            await message.reply_text(
                f"{E_CROSS} Failed to change visibility. Status {resp.status_code}: {resp.text}"
            )
    except Exception as e:
        await message.reply_text(f"{E_CROSS} An error occurred: {e}")


# ---------------------------------------------------------------------------
# /github <username>  /  /git <username>
# (public - read-only lookup, ported from the GITHUB-HEROKU repo's misc.py)
# ---------------------------------------------------------------------------
@Client.on_message(filters.command(["github", "git"]))
async def github_user_lookup_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/git &lt;github_username&gt;</code>"
        )

    username = message.command[1]
    resp = requests.get(f"{GITHUB_API}/users/{username}")

    if resp.status_code == 404:
        return await message.reply_text(f"{E_CROSS} No GitHub user named \"{username}\".")
    if resp.status_code != 200:
        return await message.reply_text(
            f"{E_CROSS} GitHub API error. Status {resp.status_code}: {resp.text}"
        )

    data = resp.json()
    caption = (
        "<blockquote>"
        f"<b>GitHub info for {data.get('name') or username}</b>\n\n"
        f"<b>Username:</b> {username}\n"
        f"<b>Bio:</b> {data.get('bio') or '-'}\n"
        f"<b>Link:</b> {data.get('html_url')}\n"
        f"<b>Company:</b> {data.get('company') or '-'}\n"
        f"<b>Created on:</b> {data.get('created_at')}\n"
        f"<b>Repositories:</b> {data.get('public_repos')}\n"
        f"<b>Blog:</b> {data.get('blog') or '-'}\n"
        f"<b>Location:</b> {data.get('location') or '-'}\n"
        f"<b>Followers:</b> {data.get('followers')}\n"
        f"<b>Following:</b> {data.get('following')}"
        "</blockquote>"
    )

    avatar_url = data.get("avatar_url")
    if avatar_url:
        await message.reply_photo(photo=avatar_url, caption=caption)
    else:
        await message.reply_text(caption)


# ---------------------------------------------------------------------------
# /allrepo <github_username>
# (public - read-only lookup, ported from the GITHUB-HEROKU repo's misc.py)
# ---------------------------------------------------------------------------
def _chunk_string(text: str, chunk_size: int):
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


@Client.on_message(filters.command("allrepo"))
async def all_repos_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/allrepo &lt;github_username&gt;</code>"
        )

    username = message.command[1]
    try:
        resp = requests.get(f"{GITHUB_API}/users/{username}/repos")
        if resp.status_code != 200:
            return await message.reply_text(
                f"{E_CROSS} GitHub API error. Status {resp.status_code}: {resp.text}"
            )

        repos = resp.json()
        if not repos:
            return await message.reply_text(f"{E_WARN} No public repos found for {username}.")

        repo_info = "\n\n".join(
            f"<b>Repository:</b> {repo['full_name']}\n"
            f"<b>Description:</b> {repo.get('description') or '-'}\n"
            f"<b>Stars:</b> {repo['stargazers_count']}\n"
            f"<b>Forks:</b> {repo['forks_count']}\n"
            f"<b>URL:</b> {repo['html_url']}"
            for repo in repos
        )

        for chunk in _chunk_string(repo_info, 4000):
            await message.reply_text(chunk)
    except Exception as e:
        await message.reply_text(f"{E_CROSS} An error occurred: {e}")
