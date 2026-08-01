# Akbots - Don't Remove Credit - @AkBots_Official
# Ported from Mediainfo-Bot-master/core/formatter.py, unchanged.

def format_output(data):
    """
    Parses metadata (from MediaInfo or ffprobe) and returns a formatted string.
    """
    if "media" in data:
        return format_mediainfo(data)
    elif "streams" in data:
        return format_ffprobe(data)
    else:
        return "❌ Could not parse media information."
def align(key, value, width=40):
    if not value or value == "N/A":
        return ""
    return f"{key:<{width}} : {value}\n"

def format_mediainfo(data):
    text = ""
    tracks = data["media"].get("track", [])
    
    for track in tracks:
        ttype = track.get("@type")
        
        if ttype == "General":
            text += "🗒 **General**\n"
            text += align("Unique ID", track.get("UniqueID"))
            text += align("Format", track.get("Format"))
            text += align("Format version", track.get("Format_Version"))
            text += align("File size", track.get("FileSize_String", track.get("FileSize")))
            text += align("Duration", track.get("Duration_String3", track.get("Duration")))
            text += align("Overall bit rate", track.get("OverallBitRate_String"))
            text += align("Frame rate", track.get("FrameRate"))
            text += align("Movie name", track.get("Title"))
            text += align("Encoded date", track.get("Encoded_Date"))
            text += align("Writing application", track.get("Encoded_Application"))
            text += align("Writing library", track.get("Encoded_Library"))
            text += "\n"

        elif ttype == "Video":
            text += "🎞 **Video**\n"
            text += align("ID", track.get("ID"))
            text += align("Format", track.get("Format"))
            text += align("Format/Info", track.get("Format_Info"))
            text += align("Format profile", track.get("Format_Profile"))
            text += align("Codec ID", track.get("CodecID"))
            text += align("Duration", track.get("Duration_String3"))
            text += align("Width", track.get("Width"))
            text += align("Height", track.get("Height"))
            text += align("Display aspect ratio", track.get("DisplayAspectRatio_String"))
            text += align("Frame rate mode", track.get("FrameRate_Mode_String"))
            text += align("Frame rate", f"{track.get('FrameRate')} FPS")
            text += align("Color space", track.get("ColorSpace"))
            text += align("Chroma subsampling", track.get("ChromaSubsampling"))
            text += align("Bit depth", f"{track.get('BitDepth')} bits")
            text += align("Title", track.get("Title"))
            text += align("Writing library", track.get("Encoded_Library"))
            text += align("Default", track.get("Default"))
            text += align("Forced", track.get("Forced"))
            text += "\n"

        elif ttype == "Audio":
            idx = track.get("StreamOrder", "1")
            text += f"🔊 **Audio #{int(idx)+1}**\n"
            text += align("ID", track.get("ID"))
            text += align("Format", track.get("Format"))
            text += align("Codec ID", track.get("CodecID"))
            text += align("Duration", track.get("Duration_String3"))
            text += align("Channel(s)", f"{track.get('Channels')} channels")
            text += align("Channel layout", track.get("ChannelLayout"))
            text += align("Sampling rate", track.get("SamplingRate_String"))
            text += align("Language", track.get("Language_String"))
            text += align("Title", track.get("Title"))
            text += align("Default", track.get("Default"))
            text += align("Forced", track.get("Forced"))
            text += "\n"

        elif ttype == "Text":
            idx = track.get("StreamOrder", "1")
            text += f"🔠 **Subtitle #{int(idx)+1}**\n"
            text += align("ID", track.get("ID"))
            text += align("Format", track.get("Format"))
            text += align("Codec ID", track.get("CodecID"))
            text += align("Language", track.get("Language_String"))
            text += align("Title", track.get("Title"))
            text += align("Default", track.get("Default"))
            text += align("Forced", track.get("Forced"))
            text += "\n"

    return f"```\n{text.strip()}\n```"

def format_ffprobe(data):
    # FFprobe fallback - simplified but still detailed
    text = ""
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    
    text += "🗒 **General**\n"
    text += align("Format", fmt.get("format_long_name"))
    size = int(fmt.get("size", 0))
    text += align("File size", f"{size / (1024*1024):.2f} MB")
    dur = float(fmt.get("duration", 0))
    text += align("Duration", f"{dur/60:.2f} min")
    text += align("Bit rate", f"{int(fmt.get('bit_rate', 0))/1000:.1f} kbps")
    text += "\n"
    
    for i, s in enumerate(streams):
        stype = s.get("codec_type")
        tags = s.get("tags", {})
        
        if stype == "video":
            text += "🎞 **Video**\n"
            text += align("Codec", s.get("codec_name"))
            text += align("Resolution", f"{s.get('width')}x{s.get('height')}")
            text += align("Frame rate", s.get("avg_frame_rate"))
            text += align("Bit depth", f"{s.get('bits_per_raw_sample')} bits")
            text += "\n"
        elif stype == "audio":
            text += f"🔊 **Audio #{i}**\n"
            text += align("Codec", s.get("codec_name"))
            text += align("Channels", s.get("channels"))
            text += align("Language", tags.get("language"))
            text += align("Title", tags.get("title"))
            text += "\n"
        elif stype == "subtitle":
            text += f"🔠 **Subtitle #{i}**\n"
            text += align("Codec", s.get("codec_name"))
            text += align("Language", tags.get("language"))
            text += align("Title", tags.get("title"))
            text += "\n"
            
    return f"```\n{text.strip()}\n```"
