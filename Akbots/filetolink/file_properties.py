import logging
from typing import Any, Optional

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.file_id import FileId

from .exceptions import FileNotFound

MEDIA_TYPES = (
    "audio",
    "document",
    "photo",
    "sticker",
    "animation",
    "video",
    "voice",
    "video_note",
)


def get_media_from_message(message: "Message") -> Optional[Any]:
    for attr in MEDIA_TYPES:
        media = getattr(message, attr, None)
        if media:
            return media
    return None


async def parse_file_id(message: "Message") -> Optional[FileId]:
    media = get_media_from_message(message)
    if media and getattr(media, "file_id", None):
        return FileId.decode(media.file_id)
    return None


async def parse_file_unique_id(message: "Message") -> Optional[str]:
    media = get_media_from_message(message)
    if media:
        return getattr(media, "file_unique_id", None)
    return None


async def get_file_ids(client: Client, chat_id: int, id: int) -> FileId:
    try:
        message = await client.get_messages(chat_id, id)
    except Exception as e:
        logging.error(f"[filetolink] Error getting message: {e}")
        raise FileNotFound("Message could not be fetched from Telegram")

    if not message or message.empty:
        raise FileNotFound("Message is empty or invalid")

    media = get_media_from_message(message)
    if not media:
        raise FileNotFound("No media found in message")

    file_id = await parse_file_id(message)
    file_unique_id = await parse_file_unique_id(message)

    if not file_id:
        raise FileNotFound("File ID could not be parsed")

    file_id.file_size = getattr(media, "file_size", 0)
    file_id.mime_type = getattr(media, "mime_type", None) or "application/octet-stream"
    file_id.file_name = getattr(media, "file_name", None) or f"file_{id}"
    file_id.unique_id = file_unique_id or "XXXXXX"

    return file_id


def get_hash(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    if media:
        return (getattr(media, "file_unique_id", "") or "")[:6]
    return "000000"
