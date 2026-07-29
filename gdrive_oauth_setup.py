"""
Google Drive OAuth setup — run this ONCE on your own PC (needs a browser).

It produces a `token.pickle` file. Upload that file to the bot via
/setgdrivetoken to enable folder downloads and private-file access in
/gdrive. The bot server itself never needs a browser or these credentials
directly — only this one-time local step does.

Steps:
1. Go to https://console.cloud.google.com/ -> create a project (or pick one).
2. Enable the "Google Drive API" for that project (APIs & Services -> Library).
3. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID.
   - Application type: "Desktop app"
   - Download the JSON, save it next to this script as `credentials.json`.
4. Install deps locally:  pip install google-auth google-auth-oauthlib google-api-python-client
5. Run:  python gdrive_oauth_setup.py
   A browser window opens — log in with the Google account whose Drive you
   want the bot to access, and approve access.
6. A `token.pickle` file appears in this folder. Send that file to the bot
   with /setgdrivetoken.

Re-run this script any time you need to switch accounts or the token stops
working (e.g. access revoked).
"""

import pickle
import os

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.pickle"


def main():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"ERROR: {CREDENTIALS_FILE} not found.")
                print("Download it from Google Cloud Console (see the guide at the top of this file) first.")
                return
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    print(f"Done! '{TOKEN_FILE}' created.")
    print("Send this file to your bot with /setgdrivetoken to enable folder + private-file support.")


if __name__ == "__main__":
    main()
