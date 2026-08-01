"""
JDownloader booter (adapted from mirror-leech-telegram-bot's
bot/core/jdownloader_booter.py to fit this bot's plain-async style).

How it works:
- Talks to a local JDownloader.jar (Java) process over its LOCAL API on
  http://127.0.0.1:3128 (the myjd/ package handles that HTTP layer) - no
  MyJDownloader cloud round-trip needed for actual downloading.
- JD_EMAIL/JD_PASS just let JDownloader itself sign in to my.jdownloader.org
  in the background (needed for some premium-hoster/captcha features and
  remote monitoring via the JDownloader app/website).
- If JD_EMAIL/JD_PASS aren't set in config.py, boot() just no-ops and
  jdownloader.is_connected stays False - /jd then tells the user it's
  disabled instead of erroring.
- First boot ever: JDownloader.jar is only the small self-updating
  installer (see Dockerfile) - it downloads the rest of itself on first
  run, which can take a few minutes. Subsequent restarts are fast.
"""

import os
import json
import asyncio
from random import randint

from config import JD_EMAIL, JD_PASS
from logger import LOGGER
from myjd import MyJdApi

logger = LOGGER(__name__)

JD_DIR = "/JDownloader"


class JDownloader(MyJdApi):
    def __init__(self):
        super().__init__()
        self.is_connected = False
        self.error = "JDownloader credentials not set (JD_EMAIL / JD_PASS in config)."
        self._device_name = ""
        self._boot_task = None

    def boot(self):
        """Fire-and-forget: starts the boot/watch loop as a background task."""
        if self._boot_task is None or self._boot_task.done():
            self._boot_task = asyncio.create_task(self._boot_loop())
        return self._boot_task

    async def _boot_loop(self):
        if not JD_EMAIL or not JD_PASS:
            self.is_connected = False
            self.error = "JDownloader credentials not set (JD_EMAIL / JD_PASS in config)."
            logger.info("JDownloader (/jd) disabled — JD_EMAIL/JD_PASS not set.")
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-9", "-f", "JDownloader.jar",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        except Exception:
            pass

        if not os.path.exists(f"{JD_DIR}/JDownloader.jar"):
            self.is_connected = False
            self.error = (
                "JDownloader.jar is missing — the Docker image wasn't built with it "
                "(check the build logs for the JDownloader download step)."
            )
            logger.error(self.error)
            return

        self.error = "Connecting... first boot can take a few minutes (self-update)."
        self._device_name = f"{randint(0, 9999)}@AkbotsJD"

        jdata = {
            "autoconnectenabledv2": True,
            "password": JD_PASS,
            "devicename": self._device_name,
            "email": JD_EMAIL,
        }
        remote_data = {
            "localapiserverheaderaccesscontrollalloworigin": "",
            "deprecatedapiport": 3128,
            "localapiserverheaderxcontenttypeoptions": "nosniff",
            "localapiserverheaderxframeoptions": "DENY",
            "externinterfaceenabled": True,
            "deprecatedapilocalhostonly": True,
            "localapiserverheaderreferrerpolicy": "no-referrer",
            "deprecatedapienabled": True,
            "localapiserverheadercontentsecuritypolicy": "default-src 'self'",
            "jdanywhereapienabled": True,
            "externinterfacelocalhostonly": False,
            "localapiserverheaderxxssprotection": "1; mode=block",
        }
        os.makedirs(f"{JD_DIR}/cfg", exist_ok=True)
        with open(f"{JD_DIR}/cfg/org.jdownloader.api.myjdownloader.MyJDownloaderSettings.json", "w") as f:
            json.dump(jdata, f)
        with open(f"{JD_DIR}/cfg/org.jdownloader.api.RemoteAPIConfig.json", "w") as f:
            json.dump(remote_data, f)

        cmd = (
            "java -Dsun.jnu.encoding=UTF-8 -Dfile.encoding=UTF-8 "
            "-Djava.awt.headless=true -jar JDownloader.jar"
        )
        logger.info("Starting JDownloader (/jd)...")
        self.is_connected = True
        self.error = ""
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=JD_DIR,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        code = await proc.wait()
        self.is_connected = False
        if code != -9:  # not an intentional kill (e.g. /restart) - crashed, restart it
            logger.warning(f"JDownloader exited (code {code}) — restarting...")
            await self._boot_loop()


jdownloader = JDownloader()
