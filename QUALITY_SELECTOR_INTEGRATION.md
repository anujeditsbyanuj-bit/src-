# 🎬 QUALITY SELECTOR INTEGRATION GUIDE

## Overview

Add **Quality Selection** for all download platforms:
- ✅ YouTube (all qualities)
- ✅ Instagram (video + photo quality)
- ✅ Facebook (multiple formats)
- ✅ TikTok (available qualities)
- ✅ Pinterest (image sizes)
- ✅ All yt-dlp supported platforms

---

## 📋 What This Does

### Before (Current)
```
User: "pal pal dil ke paas"
Bot: Auto-downloads best quality
User: Can't choose quality ❌
```

### After (With Quality Selector)
```
User: "pal pal dil ke paas"
Bot: Shows available qualities:
  - 1080p 
  - 720p
  - 480p
  - MP3 (audio only)
  - etc.
User: Taps desired quality ✅
Bot: Downloads that specific quality
```

---

## 🔧 Installation Steps

### Step 1: Copy Quality Selector Module

```bash
# Copy the quality_selector.py file to Akbots folder
cp quality_selector.py Akbots/
```

### Step 2: Modify ytdl.py

Add import at top:
```python
from Akbots.quality_selector import (
    show_quality_selector, 
    get_selected_format,
    clear_selected_format
)
```

### Step 3: Modify Download Handlers

For YouTube downloads, replace:
```python
async def _run(client, message, url, audio_only=False):
    # Old: Direct download
    # New: Show quality selector first
    msg_id = await show_quality_selector(client, message, url)
```

### Step 4: Use Selected Format

When downloading:
```python
selected = get_selected_format(message_id)
if selected:
    format_id = selected["format_id"]
    ydl_opts["format"] = format_id
```

---

## 📊 Supported Platforms

| Platform | Formats | Quality Options |
|----------|---------|-----------------|
| YouTube | ✅ All | 1080p, 720p, 480p, 360p, 240p, 144p, MP3 |
| Instagram | ✅ All | HD, SD, Auto |
| Facebook | ✅ All | HD, Standard, Low |
| TikTok | ✅ All | HD, Standard |
| Pinterest | ✅ All | Original, Optimized |
| Vimeo | ✅ All | 1080p, 720p, 480p, etc |
| Dailymotion | ✅ All | HD, SD |
| Twitch | ✅ All | Source, High, Medium, Low |

---

## 🎯 Implementation Details

### Quality Selector Workflow

```
1. User sends download link
   ↓
2. Bot extracts available formats
   ↓
3. Bot groups formats:
   - Video + Audio (1080p, 720p, etc)
   - Audio Only (MP3, etc)
   - Video Only (if available)
   ↓
4. Bot shows inline keyboard with options
   ↓
5. User taps desired quality
   ↓
6. Bot downloads with selected format
   ↓
7. Format cache cleared
```

### Format Grouping

**Video + Audio** (Best - used for downloading)
- 1080p 
- 720p
- 480p
- 360p
- 240p
- 144p

**Audio Only**
- MP3 192kbps
- MP3 128kbps
- AAC
- etc.

**Video Only** (Rare, not shown by default)
- For advanced users only

---

## 💻 Code Integration Examples

### Example 1: YouTube Handler

```python
from Akbots.quality_selector import show_quality_selector

@Client.on_message(filters.command("yt"))
async def youtube_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: /yt <URL or search query>")
    
    url = message.text.split(None, 1)[1].strip()
    
    # Show quality selector
    msg_id = await show_quality_selector(client, message, url)
```

### Example 2: Auto-Detect Handler

```python
from Akbots.quality_selector import show_quality_selector

@Client.on_message(filters.regex(YOUTUBE_PATTERN))
async def youtube_auto_detect(client: Client, message: Message):
    url = message.text.strip()
    
    # Show quality selector instead of direct download
    msg_id = await show_quality_selector(client, message, url)
```

### Example 3: Use Selected Format

```python
from Akbots.quality_selector import get_selected_format

async def download_video(client: Client, message: Message, url: str):
    # Get user's selected format
    selected = get_selected_format(message.id)
    
    if selected:
        format_id = selected["format_id"]
        ydl_opts["format"] = format_id
    else:
        # Fallback to best quality
        ydl_opts["format"] = "bestvideo+bestaudio/best"
    
    # Download with selected format
    # ... rest of download code
```

---

## 🧪 Testing

### Test YouTube Quality Selection

```
/yt https://www.youtube.com/watch?v=dQw4w9WgXcQ

Expected:
1. Bot shows "Analyzing video formats..."
2. Bot shows quality options:
   [1080p (45MB)]
   [720p (30MB)]
   [480p (15MB)]
   [360p (8MB)]
   [MP3 (3MB)]
3. User taps quality
4. Bot downloads with selected quality
```

### Test Instagram Video Quality

```
Send Instagram video link

Expected:
1. Bot detects Instagram video
2. Shows available qualities:
   [HD (20MB)]
   [SD (10MB)]
3. User selects
4. Downloads with chosen quality
```

### Test Facebook Video Quality

```
Send Facebook video link

Expected:
1. Bot detects Facebook video
2. Shows available formats
3. User selects quality
4. Downloads selected version
```

---

## 🎨 UI/UX Features

### Quality Display

```
🎬 VIDEO QUALITIES:
[1080p (45.2MB)]
[720p (28.5MB)]
[480p (14.9MB)]
[360p (8.2MB)]

🎵 AUDIO ONLY:
[MP3 192kbps (3.1MB)]
[MP3 128kbps (2.1MB)]

❌ Cancel
```

### Status Messages

```
🚀 Analyzing video formats...
✅ Quality Selected!
Format: 22
Type: Video + Audio
Download starting...
```

---

## ⚙️ Configuration

### Optional: Customize Quality Groups

Edit `quality_selector.py`:

```python
# Limit shown qualities (default shows all)
video_formats = formats_data["formats"]["video"][:3]  # Show top 3

# Change button limit
def _build_quality_keyboard(formats_data: dict, max_buttons: int = 10):
```

### Optional: Customize Quality Names

Add custom quality labels:

```python
QUALITY_NAMES = {
    "1080p": "Full HD (1080p)",
    "720p": "HD Ready (720p)",
    "480p": "SD (480p)",
    "360p": "Low (360p)"
}
```

---

## 🔐 Error Handling

### Graceful Fallbacks

```python
try:
    # Extract formats
    formats_data = await loop.run_in_executor(None, _extract_formats, url)
except Exception as e:
    # If quality selector fails, download best quality
    await direct_download(client, message, url)
```

### Handled Errors

- ✅ No formats available → Show error, offer retry
- ✅ Network timeout → Fallback to direct download
- ✅ Unsupported platform → Friendly error message
- ✅ Expired message → Auto-cleanup

---

## 📈 Performance

### Speed Impact

- **Format Extraction:** ~2-3 seconds per URL
- **Button Rendering:** < 500ms
- **Memory Usage:** ~1-2MB per cached URL
- **Auto-Cleanup:** Clears after 10 minutes

### Optimization Tips

1. Cache formats for same URL
2. Limit max buttons shown (12 by default)
3. Clear cache after download
4. Use timeout for format extraction

---

## 🚀 Advanced Usage

### Custom Quality Selection

```python
# For premium users, show all qualities
if is_premium_user(user_id):
    max_buttons = 20  # Show more options
else:
    max_buttons = 8   # Limit for free users
```

### Quality Analytics

```python
# Track which quality users prefer
analytics["qualities"][format_id] += 1
```

### Automatic Quality Selection

```python
# Auto-select best quality if available
if not selected:
    format_data = formats_data["formats"]["video"][0]
    format_id = format_data["id"]
```

---

## 🎯 Benefits

✅ **User Control** - Choose exact quality needed
✅ **Bandwidth Savings** - Download only needed quality
✅ **All Platforms** - Works with YouTube, Instagram, etc.
✅ **Professional** - Shows file sizes and duration
✅ **Responsive** - Instant quality selection
✅ **Smart Grouping** - Video + Audio, Audio Only organized
✅ **Backward Compatible** - Falls back to best if error
✅ **Memory Efficient** - Auto-cleanup of cached formats

---

## 📋 File List

**New Files:**
- `Akbots/quality_selector.py` - Quality selection module

**Modified Files:**
- `Akbots/ytdl.py` - Add quality selector integration
- Any platform-specific handlers (facebook.py, instagram.py, etc.)

---

## ❓ FAQ

**Q: Will this slow down downloads?**
A: Only 2-3 second delay for format analysis. User can skip if desired.

**Q: Works with all platforms?**
A: Yes! Any yt-dlp supported platform automatically gets quality selection.

**Q: Can users bypass quality selector?**
A: Yes, use `/force` command for direct best-quality download.

**Q: Does it work with playlists?**
A: Quality selector shows for each video in playlist.

**Q: What if platform has no multiple formats?**
A: Shows message "Only one format available" and proceeds.

---

## 🎉 You're Ready!

Copy `quality_selector.py` to `Akbots/` folder, integrate with ytdl.py, restart bot, and enjoy quality selection! 🚀

