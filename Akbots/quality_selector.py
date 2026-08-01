"""
Quality Selection Module - For all platforms (YouTube, Instagram, Facebook, TikTok, etc)
Shows available formats/qualities to user before downloading
"""

import asyncio
import json
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# Cache for available formats: url -> {"formats": [...], "info": {...}}
_FORMAT_CACHE = {}

# Cache for selected formats: message_id -> {"url": str, "format_id": str}
_SELECTED_FORMAT = {}

E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'


def _extract_formats(url: str) -> dict:
    """
    Extract available formats/qualities from URL using yt-dlp.
    Works with: YouTube, Instagram, Facebook, TikTok, Pinterest, etc.
    """
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
        if not info:
            return {"error": "Could not extract video info"}
        
        formats = info.get("formats", [])
        if not formats:
            return {"error": "No formats available"}
        
        # Group formats by quality
        grouped = {
            "video": [],      # Video + Audio combined
            "audio_only": [],  # Audio only
            "video_only": [],  # Video only
        }
        
        for fmt in formats:
            fmt_id = fmt.get("format_id", "unknown")
            ext = fmt.get("ext", "unknown")
            
            # Video with audio (best for downloading)
            if fmt.get("vcodec") != "none" and fmt.get("acodec") != "none":
                height = fmt.get("height", 0)
                tbr = fmt.get("tbr", 0)
                filesize = fmt.get("filesize", 0)
                
                if height:
                    label = f"{height}p"
                elif tbr:
                    label = f"{int(tbr)}kbps"
                else:
                    label = ext.upper()
                
                grouped["video"].append({
                    "id": fmt_id,
                    "label": label,
                    "height": height,
                    "ext": ext,
                    "filesize": filesize,
                    "format": fmt.get("format", "")
                })
            
            # Audio only
            elif fmt.get("vcodec") == "none" and fmt.get("acodec") != "none":
                abr = fmt.get("abr", 0)
                label = f"MP3 {int(abr)}kbps" if abr else "MP3"
                grouped["audio_only"].append({
                    "id": fmt_id,
                    "label": label,
                    "ext": ext,
                    "filesize": fmt.get("filesize", 0),
                    "format": fmt.get("format", "")
                })
            
            # Video only
            elif fmt.get("vcodec") != "none" and fmt.get("acodec") == "none":
                height = fmt.get("height", 0)
                label = f"{height}p (video only)" if height else "Video only"
                grouped["video_only"].append({
                    "id": fmt_id,
                    "label": label,
                    "height": height,
                    "ext": ext,
                    "filesize": fmt.get("filesize", 0),
                })
        
        # Sort by quality (highest first)
        grouped["video"].sort(key=lambda x: x.get("height", 0), reverse=True)
        grouped["audio_only"].sort(key=lambda x: x.get("filesize", 0), reverse=True)
        
        return {
            "title": info.get("title", "Video"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", "Unknown"),
            "formats": grouped,
            "total_formats": len(formats)
        }
    
    except Exception as e:
        return {"error": f"Failed to extract formats: {str(e)}"}


def _format_size(size_bytes):
    """Convert bytes to human readable format"""
    if not size_bytes:
        return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}TB"


def _build_quality_keyboard(formats_data: dict, max_buttons: int = 12) -> InlineKeyboardMarkup:
    """
    Build inline keyboard with quality options
    Priority: Video+Audio > Audio Only > Video Only
    """
    buttons = []
    
    video_formats = formats_data["formats"]["video"][:5]  # Top 5 video qualities
    audio_formats = formats_data["formats"]["audio_only"][:2]  # Top 2 audio qualities
    
    # Video + Audio options
    if video_formats:
        buttons.append([make_button("🎬 VIDEO QUALITIES:", callback_data="qual:noop", style=_BS.PRIMARY if _BS else None)])
        for fmt in video_formats:
            size_str = f" ({_format_size(fmt['filesize'])})" if fmt['filesize'] else ""
            label = f"{fmt['label']}{size_str}"
            buttons.append([
                make_button(label, callback_data=f"qual:vid:{fmt['id']}", style=_BS.PRIMARY if _BS else None)
            ])
    
    # Audio only options
    if audio_formats:
        buttons.append([make_button("🎵 AUDIO ONLY:", callback_data="qual:noop", style=_BS.PRIMARY if _BS else None)])
        for fmt in audio_formats:
            size_str = f" ({_format_size(fmt['filesize'])})" if fmt['filesize'] else ""
            label = f"{fmt['label']}{size_str}"
            buttons.append([
                make_button(label, callback_data=f"qual:aud:{fmt['id']}", style=_BS.PRIMARY if _BS else None)
            ])
    
    # Cancel button
    buttons.append([make_button("❌ Cancel", callback_data="qual:cancel", style=_BS.DANGER if _BS else None)])
    
    return InlineKeyboardMarkup(buttons)


def _format_quality_text(formats_data: dict) -> str:
    """Create message text showing available qualities"""
    if "error" in formats_data:
        return f"{E_CROSS} <b>Error:</b> {formats_data['error']}"
    
    title = formats_data.get("title", "Video")
    duration = formats_data.get("duration", 0)
    uploader = formats_data.get("uploader", "Unknown")
    
    # Format duration
    mins, secs = divmod(int(duration), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        duration_str = f"{hours}:{mins:02d}:{secs:02d}"
    else:
        duration_str = f"{mins}:{secs:02d}"
    
    text = f"{E_ROCKET} <b>Select Quality</b>\n\n"
    text += f"<b>Title:</b> <i>{title[:50]}</i>\n"
    text += f"<b>Duration:</b> {duration_str}\n"
    text += f"<b>Uploader:</b> {uploader}\n"
    text += f"<b>Available Formats:</b> {formats_data.get('total_formats', 0)}\n\n"
    text += "<b>Tap a quality below to download:</b>"
    
    return text


async def show_quality_selector(client: Client, message: Message, url: str):
    """
    Main function: Show quality selection menu to user
    """
    status = await message.reply_text(
        f"{E_ROCKET} <b>Analyzing video formats...</b>",
        parse_mode=enums.ParseMode.HTML
    )
    
    loop = asyncio.get_event_loop()
    try:
        # Extract formats in executor (blocking operation)
        formats_data = await loop.run_in_executor(None, _extract_formats, url)
    except Exception as e:
        return await safe_edit(status.edit_text, 
            f"{E_CROSS} <b>Error:</b> {str(e)}",
            parse_mode=enums.ParseMode.HTML
        )
    
    # Show quality options
    await safe_edit(status.edit_text, 
        _format_quality_text(formats_data),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_build_quality_keyboard(formats_data)
    )
    
    # Cache the URL and formats for this message
    _FORMAT_CACHE[status.id] = formats_data
    
    # Return the message ID for tracking
    return status.id


@Client.on_callback_query(filters.regex(r"^qual:"))
async def quality_selector_callback(client: Client, callback_query: CallbackQuery):
    """Handle quality selection callbacks"""
    
    try:
        action = callback_query.matches[0].group(0)
        
        # Cancel request
        if action == "qual:cancel":
            await callback_query.message.delete()
            return await callback_query.answer("Download cancelled", show_alert=False)
        
        # Noop (info button)
        if action == "qual:noop":
            return await callback_query.answer()
        
        # Parse format type and ID: qual:vid:format_id or qual:aud:format_id
        parts = action.split(":")
        if len(parts) < 3:
            return await callback_query.answer("Invalid format selection", show_alert=True)
        
        fmt_type = parts[1]  # 'vid' or 'aud'
        fmt_id = ":".join(parts[2:])  # format_id (may contain colons)
        
        # Store selected format
        _SELECTED_FORMAT[callback_query.message.id] = {
            "format_id": fmt_id,
            "type": fmt_type,
            "url": callback_query.message.reply_to_message.text if callback_query.message.reply_to_message else ""
        }
        
        await callback_query.answer(f"Selected format: {fmt_id}", show_alert=False)
        
        # Update message to show selection
        await safe_edit(callback_query.message.edit_text, 
            f"{E_CHECK} <b>Quality Selected!</b>\n\n"
            f"Format: <code>{fmt_id}</code>\n"
            f"Type: {'Video + Audio' if fmt_type == 'vid' else 'Audio Only'}\n\n"
            f"<i>Download starting...</i>",
            parse_mode=enums.ParseMode.HTML
        )
        
    except Exception as e:
        await callback_query.answer(f"Error: {str(e)}", show_alert=True)


def get_selected_format(message_id: int) -> dict:
    """Get selected format for a message"""
    return _SELECTED_FORMAT.get(message_id, {})


def clear_selected_format(message_id: int):
    """Clear selected format after download"""
    _SELECTED_FORMAT.pop(message_id, None)
    _FORMAT_CACHE.pop(message_id, None)
