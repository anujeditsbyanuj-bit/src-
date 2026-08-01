import os
import logging
import urllib.parse

import jinja2
import aiofiles

from config import STREAM_BIN_CHANNEL, STREAM_URL
from .template import page_template
from .file_properties import get_file_ids
from .exceptions import InvalidHash

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "template")


def get_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024.0:
            return f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} PB"


async def render_page(bot, id: int, secure_hash: str) -> str:
    try:
        file_data = await get_file_ids(bot, STREAM_BIN_CHANNEL, id)
    except Exception as e:
        logging.error(f"[filetolink] Error fetching file info: {e}")
        raise

    if file_data.unique_id[:6] != secure_hash:
        raise InvalidHash

    url_base = STREAM_URL if STREAM_URL.endswith("/") else STREAM_URL + "/"
    src = urllib.parse.urljoin(url_base, f"{id}?hash={secure_hash}")

    tag = (file_data.mime_type or "application/octet-stream").split("/")[0].strip()
    file_size = get_size(file_data.file_size)

    if tag in ("video", "audio"):
        template_file = os.path.join(TEMPLATE_DIR, "watch.html")
    else:
        template_file = os.path.join(TEMPLATE_DIR, "dl.html")

    async with aiofiles.open(template_file, mode="r") as f:
        content = await f.read()
    template = jinja2.Template(content)

    file_name = (file_data.file_name or f"file_{id}").replace("_", " ")

    me = getattr(bot, "_ftl_me_username", None)
    tg_link = f"https://t.me/{me}?start=file_{id}" if me else "#"

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
        template_ne=page_template.NAME,
        disclaimer=page_template.DISCLAIMER,
        report_link=page_template.REPORT_LINK,
        colours=page_template.COLOURS,
        tg_button=tg_link,
    )
