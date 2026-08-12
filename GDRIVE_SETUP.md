# Google Drive Folder Support Setup

By default, `/gdrive` only works with **public** ("Anyone with the link")
single files — zero setup needed, works out of the box.

To also support **folders** and **private files**, an admin needs to do a
one-time OAuth setup on their own computer:

1. On your PC (not the server), download `gdrive_oauth_setup.py` from this
   repo.
2. Follow the steps in the comment at the top of that file (create a Google
   Cloud project, enable the Drive API, download `credentials.json`).
3. Run it: `python gdrive_oauth_setup.py` — a browser opens, log in and
   approve access.
4. You'll get a `token.pickle` file. Send it to the bot in a private chat
   with the command `/setgdrivetoken` (attach the file, or send the command
   first and then the file when asked).
5. Done — `/gdrive <folder link>` now works, and private files are tried
   automatically as a fallback if the public method fails.

Notes:
- The bot only needs `token.pickle` — never `credentials.json`. Keep that
  one on your own PC.
- Token is tied to whichever Google account you logged in with in step 3;
  the bot can only access Drive files/folders that account has access to.
- If access ever breaks (token revoked, expired without a refresh token,
  etc.), just re-run `gdrive_oauth_setup.py` and re-upload with
  `/setgdrivetoken`.
