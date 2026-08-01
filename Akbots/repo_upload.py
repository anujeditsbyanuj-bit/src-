# Akbots
# Don't Remove Credit
# Telegram Channel @AkBots_Official
#
# /uploadrepo and /release, adapted from the "GitHub Auto Uploader Pro"
# CLI tool's folder-upload flow (github_auto_uploader/app.py:
# setup_git_and_push, handle_gitignore, create_archive_release). The
# original tool walked a local folder on the operator's own desktop with
# an interactive arrow-key/Prompt.ask flow; a Telegram bot has no local
# folder to point at and no interactive terminal, so here the "folder" is
# a .zip the admin uploads, and every confirmation the CLI asked
# interactively is instead a hard stop with an explanatory reply — if a
# secret is found, the upload is refused outright rather than offering to
# silently rewrite Git history, since that kind of destructive rewrite
# needs a human watching, not a bot alone in a chat.
#
# Commands (all ADMINS-only):
#   /uploadrepo <repo_name>  - reply to (or caption on) an uploaded .zip;
#                              extracts it, scans for secrets, then
#                              git init + commit + force-push to that repo
#                              under your active GitHub account
#                              (see Akbots/github_accounts.py)
#   /release <owner/repo> | <tag> | <title> | <new functions> | <bug fixes>
#                            - create a GitHub release for a repo, in the
#                              same "New Functions" / "Bug Fixes" format
#                              the source tool used for its archive releases

import os
import shutil
import subprocess
import tempfile
import zipfile

from github import Github, GithubException
from pyrogram import Client, filters
from pyrogram.types import Message

from config import ADMINS
from database.db import db
from Akbots.github_accounts import resolve_github_token_with_rotation
from Akbots.gh_security import scan_for_secrets, redact_sensitive_text

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN = '<emoji id=5447644880824181073>⚠️</emoji>'

GITHUB_API = "https://api.github.com"
LARGE_FILE_MB = 50


def _run(command, cwd):
    return subprocess.run(command, capture_output=True, text=True, cwd=cwd)


def _safe_extract(zip_path: str, dest: str):
    """Extract a zip while refusing entries that would escape `dest` ("zip slip")."""
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = os.path.normpath(os.path.join(dest, member.filename))
            if not target.startswith(os.path.normpath(dest) + os.sep) and target != os.path.normpath(dest):
                raise ValueError(f"Unsafe path in zip: {member.filename}")
        zf.extractall(dest)


def _flatten_single_root(dest: str):
    """If the zip contained one top-level folder, treat its contents as the repo root."""
    entries = os.listdir(dest)
    if len(entries) == 1 and os.path.isdir(os.path.join(dest, entries[0])):
        inner = os.path.join(dest, entries[0])
        for name in os.listdir(inner):
            shutil.move(os.path.join(inner, name), os.path.join(dest, name))
        os.rmdir(inner)


# ---------------------------------------------------------------------------
# /uploadrepo <repo_name>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("uploadrepo") & filters.user(ADMINS))
async def upload_repo_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: reply to (or caption) an uploaded <code>.zip</code> with "
            "<code>/uploadrepo &lt;repo_name&gt;</code>"
        )
    repo_name = message.command[1]

    doc_message = message if message.document else message.reply_to_message
    if not doc_message or not doc_message.document or not doc_message.document.file_name.endswith(".zip"):
        return await message.reply_text(
            f"{E_WARN} Attach or reply to a <code>.zip</code> file with this command."
        )

    username, token, _ = await resolve_github_token_with_rotation(message.from_user.id)
    if not token:
        return await message.reply_text(
            f"{E_WARN} No GitHub token available. Use <code>/addaccount &lt;token&gt;</code> "
            "or set <code>GIT_TOKEN</code> in config."
        )
    if not username:
        # Global GIT_TOKEN path — fetch whose account it belongs to.
        import requests
        resp = requests.get(
            f"{GITHUB_API}/user", headers={"Authorization": f"token {token}"}, timeout=10
        )
        if resp.status_code != 200:
            return await message.reply_text(f"{E_CROSS} GitHub rejected the configured token.")
        username = resp.json()["login"]

    status = await message.reply_text(f"{E_WARN} Downloading and scanning archive…")
    work_dir = tempfile.mkdtemp(prefix="uploadrepo_")
    zip_path = os.path.join(work_dir, "upload.zip")
    extract_dir = os.path.join(work_dir, "repo")

    try:
        await doc_message.download(file_name=zip_path)
        os.makedirs(extract_dir, exist_ok=True)
        try:
            _safe_extract(zip_path, extract_dir)
        except Exception as e:
            return await status.edit_text(f"{E_CROSS} Couldn't extract that zip: {e}")
        _flatten_single_root(extract_dir)

        findings = scan_for_secrets(extract_dir)
        if findings:
            rows = "\n".join(f"• {f.kind} — {f.path}:{f.line}" for f in findings[:15])
            return await status.edit_text(
                f"{E_CROSS} <b>Upload blocked — possible secrets found:</b>\n{rows}\n\n"
                "Remove or redact these and re-upload. (Values themselves are never shown or stored.)"
            )

        large = []
        for root, _dirs, files in os.walk(extract_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    size_mb = os.path.getsize(fpath) / (1024 * 1024)
                except OSError:
                    continue
                if size_mb > LARGE_FILE_MB:
                    large.append((os.path.relpath(fpath, extract_dir), size_mb))

        await status.edit_text(f"{E_WARN} Preparing repository…")

        import requests
        create_resp = requests.post(
            f"{GITHUB_API}/user/repos",
            json={"name": repo_name},
            headers={"Authorization": f"token {token}"},
        )
        if create_resp.status_code not in (201, 422):  # 422 == already exists
            return await status.edit_text(
                f"{E_CROSS} Couldn't create/verify the repo: {create_resp.text}"
            )

        clean_url = f"https://github.com/{username}/{repo_name}.git"
        auth_url = f"https://{token}@github.com/{username}/{repo_name}.git"

        steps = [
            ["git", "init"],
            ["git", "checkout", "-B", "main"],
            ["git", "add", "."],
            ["git", "config", "user.email", "akbotz@upload.bot"],
            ["git", "config", "user.name", "AkbotzUploader"],
        ]
        for cmd in steps:
            _run(cmd, cwd=extract_dir)

        commit = _run(["git", "commit", "-m", f"Upload via /uploadrepo ({repo_name})"], cwd=extract_dir)
        push = _run(["git", "push", auth_url, "main:main", "--force"], cwd=extract_dir)

        if push.returncode == 0:
            note = ""
            if large:
                note = "\n\n" + f"{E_WARN} Large files pushed (may be slow to clone):\n" + "\n".join(
                    f"• {p} ({s:.1f} MB)" for p, s in large[:5]
                )
            await status.edit_text(
                f"{E_CHECK} Pushed to <b>{username}/{repo_name}</b>:\n{clean_url}{note}"
            )
            try:
                await db.log_gh_upload(message.from_user.id, username, repo_name)
            except Exception:
                pass
        else:
            safe_err = redact_sensitive_text(push.stderr or commit.stderr)
            await status.edit_text(f"{E_CROSS} Push failed:\n<code>{safe_err[:1500]}</code>")
    except Exception as e:
        await status.edit_text(f"{E_CROSS} Error: {redact_sensitive_text(e)}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# /release <owner/repo> | <tag> | <title> | <new_functions> | <bug_fixes>
# ---------------------------------------------------------------------------
def _format_release_message(new_functions: str, bug_fixes: str) -> str:
    return (
        "## New Functions\n"
        f"- {new_functions.strip()}\n\n"
        "## Bug Fixes\n"
        f"- {bug_fixes.strip()}"
    )


@Client.on_message(filters.command("release") & filters.user(ADMINS))
async def create_release_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"{E_WARN} Usage:\n"
            "<code>/release owner/repo | tag | title | new functions | bug fixes</code>\n\n"
            "Example:\n"
            "<code>/release me/my-repo | v1.2.0 | Better uploads | zip upload support | fixed crash on empty folder</code>"
        )

    raw = message.text.split(None, 1)[1]
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2:
        return await message.reply_text(
            f"{E_WARN} Separate fields with <code>|</code> — at least <code>owner/repo | tag</code> is needed."
        )

    full_name = parts[0]
    tag = parts[1]
    title = parts[2] if len(parts) > 2 else tag
    new_functions = parts[3] if len(parts) > 3 else "No new functions listed"
    bug_fixes = parts[4] if len(parts) > 4 else "No bug fixes listed"

    _, token, _ = await resolve_github_token(message.from_user.id)
    if not token:
        return await message.reply_text(
            f"{E_WARN} No GitHub token available. Use <code>/addaccount &lt;token&gt;</code> "
            "or set <code>GIT_TOKEN</code> in config."
        )

    try:
        gh = Github(token)
        repo = gh.get_repo(full_name)
        body = _format_release_message(new_functions, bug_fixes)
        release = repo.create_git_release(tag=tag, name=title, message=body, draft=False, prerelease=False)
        await message.reply_text(
            f"{E_CHECK} Release <b>{tag}</b> created for {full_name}:\n{release.html_url}"
        )
    except GithubException as e:
        await message.reply_text(f"{E_CROSS} GitHub error: {e.data.get('message', str(e))}")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Error creating release: {e}")
