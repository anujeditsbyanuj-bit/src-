# Akbots - Don't Remove Credit - @AkBots_Official
#
# Auto-Detection Matrix — filename parser used by the auto-rename engine.
# Ported from File-Rename-4GB-Bot-Auto-Rename-Bot (helper/tmdb_detect.py's
# analyze_filename + regex helpers), trimmed to the pure filename-parsing
# part (no TMDb network lookup here — Akbots/movieinfo.py already covers
# on-demand TMDb search separately).
#
# Extracts: title, year, season, episode, quality, codec, audio, source,
# hdr, language, release_group from a media filename. Handles dots,
# underscores, brackets, and common scene/anime naming patterns.

import re
import os


def analyze_filename(filename: str) -> dict:
    """Returns a dict of detected metadata fields from a media filename, e.g.:
        Avengers.Endgame.2019.1080p.BluRay.x265-RELEASE.mkv
        The.Office.S03E14.720p.WEB-DL.mp4
        [SubGroup] One Piece - 1050 [1080p].mkv
    """
    if not filename:
        return _empty_result()

    name_part = os.path.splitext(filename)[0]
    normalized = _normalize_name(name_part)

    result = {
        "original_filename": filename,
        "title": "", "year": "", "season": "", "episode": "",
        "quality": "", "codec": "", "audio": "", "source": "",
        "hdr": "", "language": "", "release_group": "",
        "type": "movie",
        "is_subtitle": _is_subtitle(filename),
    }

    result["year"] = _extract_year(normalized)
    result["season"] = _extract_season(normalized)
    result["episode"] = _extract_episode(normalized)
    result["quality"] = _extract_quality(normalized)
    result["codec"] = _extract_codec(normalized)
    result["audio"] = _extract_audio(normalized)
    result["source"] = _extract_source(normalized)
    result["hdr"] = _extract_hdr(normalized)
    result["language"] = _extract_language(normalized)
    result["release_group"] = _extract_release_group(name_part)

    if result["season"] or result["episode"]:
        result["type"] = "series"

    result["title"] = _extract_title(normalized, result)
    return result


def _empty_result():
    return {
        "original_filename": "", "title": "", "year": "", "season": "",
        "episode": "", "quality": "", "codec": "", "audio": "", "source": "",
        "hdr": "", "language": "", "release_group": "", "type": "movie",
        "is_subtitle": False,
    }


def _normalize_name(name):
    protected = re.sub(r'([Ss]\d{1,2})[.]?([Ee]\d{1,3})', r'\1\2', name)
    result = protected.replace('.', ' ').replace('_', ' ')
    result = re.sub(r'^\[.*?\]\s*', '', result)
    return ' '.join(result.split())


def _extract_year(text):
    match = re.search(r'\((\d{4})\)', text)
    if match and 1950 <= int(match.group(1)) <= 2030:
        return match.group(1)
    match = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', text)
    if match and 1950 <= int(match.group(1)) <= 2030:
        return match.group(1)
    return ""


def _extract_season(text):
    match = re.search(r'[Ss](\d{1,2})(?:[Ee]\d)', text)
    if match:
        return match.group(1).zfill(2)
    match = re.search(r'[Ss]eason\s*(\d{1,2})', text, re.IGNORECASE)
    if match:
        return match.group(1).zfill(2)
    match = re.search(r'[Ss](\d{1,2})(?:\s|$)', text)
    if match:
        return match.group(1).zfill(2)
    return ""


def _extract_episode(text):
    match = re.search(r'[Ee][Pp]?(\d{1,4})', text)
    if match:
        return match.group(1).zfill(2)
    match = re.search(r'[Ee]pisode\s*(\d{1,4})', text, re.IGNORECASE)
    if match:
        return match.group(1).zfill(2)
    # Anime style: " - 1050 " or " - 05 "
    match = re.search(r'\s-\s(\d{1,4})(?:\s|$|\[)', text)
    if match:
        return match.group(1).zfill(2)
    # Chapter style: C05, Ch05, Chapter 05
    match = re.search(r'[Cc](?:h|hapter)?\s*(\d{1,4})', text)
    if match:
        return match.group(1).zfill(2)
    return ""


def _extract_quality(text):
    patterns = [
        (r'2160[pP]|4[kK]|UHD', '2160p'),
        (r'1080[pP]|FHD', '1080p'),
        (r'720[pP]|HD(?!TV|Rip)', '720p'),
        (r'480[pP]|SD', '480p'),
        (r'360[pP]', '360p'),
    ]
    for pattern, quality in patterns:
        if re.search(pattern, text):
            return quality
    return ""


def _extract_codec(text):
    patterns = [
        (r'[xX]265|[Hh]\.?265|HEVC', 'x265'),
        (r'[xX]264|[Hh]\.?264|AVC', 'x264'),
        (r'AV1', 'AV1'),
        (r'VP9', 'VP9'),
        (r'MPEG-?4', 'MPEG4'),
    ]
    for pattern, codec in patterns:
        if re.search(pattern, text):
            return codec
    return ""


def _extract_audio(text):
    patterns = [
        (r'DTS-?HD(?:\s*MA)?', 'DTS-HD MA'),
        (r'DTS', 'DTS'),
        (r'DD[P+]?\s*5\.?1|Dolby\s*Digital\s*Plus', 'DD+ 5.1'),
        (r'DD\s*5\.?1|AC-?3\s*5\.?1', 'DD 5.1'),
        (r'AC-?3|Dolby\s*Digital', 'AC3'),
        (r'AAC\s*5\.?1', 'AAC 5.1'),
        (r'AAC\s*2\.?0|AAC', 'AAC'),
        (r'FLAC', 'FLAC'),
        (r'Atmos', 'Atmos'),
        (r'TrueHD', 'TrueHD'),
    ]
    for pattern, audio in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return audio
    return ""


def _extract_source(text):
    patterns = [
        (r'BluRay|Blu-Ray|BDRip|BRRip', 'BluRay'),
        (r'WEB-?DL|WEBRip|AMZN|NF|DSNP|ATVP|HMAX', 'WEB-DL'),
        (r'HDTV', 'HDTV'),
        (r'DVDRip|DVD', 'DVDRip'),
        (r'HDRip', 'HDRip'),
        (r'CAMRip|CAM|HDCAM|HDTS|TS', 'CAM'),
        (r'REMUX', 'REMUX'),
    ]
    for pattern, source in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return source
    return ""


def _extract_hdr(text):
    patterns = [
        (r'Dolby\s*Vision|DV|DoVi', 'Dolby Vision'),
        (r'HDR10\+|HDR10Plus', 'HDR10+'),
        (r'HDR10|HDR', 'HDR10'),
        (r'HLG', 'HLG'),
    ]
    for pattern, hdr in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return hdr
    return ""


def _extract_language(text):
    patterns = [
        (r'\b(?:Dual\s*Audio|Dual)\b', 'Dual Audio'),
        (r'\bMulti\s*Audio\b|\bMulti\b', 'Multi'),
        (r'\bHindi\b|\bHINDI\b|\bHin\b', 'Hindi'),
        (r'\bEnglish\b|\bENG\b|\bEn\b', 'English'),
        (r'\bJapanese\b|\bJAP\b|\bJPN\b', 'Japanese'),
        (r'\bKorean\b|\bKOR\b', 'Korean'),
        (r'\bTamil\b|\bTAM\b', 'Tamil'),
        (r'\bTelugu\b|\bTEL\b', 'Telugu'),
        (r'\bBengali\b|\bBEN\b', 'Bengali'),
        (r'\bArabic\b|\bARA\b', 'Arabic'),
        (r'\bFrench\b|\bFR\b', 'French'),
        (r'\bSpanish\b|\bESP\b', 'Spanish'),
        (r'\bGerman\b|\bDEU\b', 'German'),
    ]
    for pattern, lang in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return lang
    return ""


def _extract_release_group(text):
    match = re.search(r'-([A-Za-z0-9]+)$', text)
    if match:
        group = match.group(1)
        false_positives = {'mkv', 'mp4', 'avi', 'srt', 'ass', 'sub', 'vtt'}
        if group.lower() not in false_positives:
            return group
    return ""


def _extract_title(text, metadata):
    title = text
    cut_patterns = [
        r'(?:19|20)\d{2}',
        r'[Ss]\d{1,2}[Ee]\d{1,4}',
        r'[Ss]eason\s*\d{1,2}',
        r'[Ee]pisode\s*\d{1,4}',
        r'(?:480|720|1080|2160)[pP]',
        r'4[kK]|UHD|FHD',
        r'[xXhH]\.?26[45]|HEVC|AVC|AV1',
        r'BluRay|Blu-Ray|BDRip|WEB-?DL|WEBRip',
        r'HDTV|DVDRip|HDRip|REMUX|CAM',
        r'DTS|DD[P+]?\s*5|AC-?3|AAC|FLAC|Atmos',
        r'HDR\d*\+?|Dolby\s*Vision|DV|DoVi|HLG',
        r'\b(?:Hindi|English|Japanese|Korean|Tamil|Telugu|Dual\s*Audio|Multi)\b',
    ]
    earliest_pos = len(title)
    for pattern in cut_patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match and match.start() < earliest_pos:
            earliest_pos = match.start()

    anime_match = re.search(r'\s-\s\d{1,4}(?:\s|$)', title)
    if anime_match and anime_match.start() < earliest_pos:
        earliest_pos = anime_match.start()

    title = title[:earliest_pos].strip()
    title = re.sub(r'[\s\-_.]+$', '', title)
    title = re.sub(r'^\[.*?\]\s*', '', title)
    title = re.sub(r'\s*\[.*?\]$', '', title)
    title = re.sub(r'\(\s*\)', '', title)

    return title.strip() if title.strip() else os.path.splitext(metadata.get("original_filename", ""))[0]


def _is_subtitle(filename):
    sub_exts = ['.srt', '.ass', '.ssa', '.vtt', '.sub', '.idx']
    return any(filename.lower().endswith(ext) for ext in sub_exts)


async def apply_autorename_template(original_filename: str, template: str) -> str:
    """Extract metadata from original_filename and substitute it into
    template. Supports: {episode}, {season}, {chapter}, {quality}, {audio}
    (simple regex, checked against the raw filename first) plus the richer
    {title}, {year}, {source}, {codec}, {language}, {hdr}, {release}
    placeholders from analyze_filename() as a fallback/extra source."""
    name_part, ext = os.path.splitext(original_filename)
    detected = analyze_filename(original_filename)

    basic_patterns = {
        'episode': r'(?:E|EP|Episode)[.\s-]*(\d+)',
        'season': r'(?:S|Season)[.\s-]*(\d+)',
        'chapter': r'(?:C|Ch|Chapter)[.\s-]*(\d+)',
        'quality': r'(480p|720p|1080p|2160p|4K|HDRip|WEBRip|BluRay|HDTV|DVDRip)',
        'audio': r'(Hindi|English|Dual|Multi|Japanese|Korean|Tamil|Telugu|HINDI|ENG|JAP)',
    }

    result = template
    for key, pattern in basic_patterns.items():
        match = re.search(pattern, original_filename, re.IGNORECASE)
        value = match.group(1) if match else detected.get(key, '')
        result = result.replace('{' + key + '}', value)

    advanced_keys = {
        'title': detected.get('title', ''),
        'year': detected.get('year', ''),
        'source': detected.get('source', ''),
        'codec': detected.get('codec', ''),
        'language': detected.get('language', ''),
        'hdr': detected.get('hdr', ''),
        'release': detected.get('release_group', ''),
    }
    for key, value in advanced_keys.items():
        result = result.replace('{' + key + '}', value)

    result = ' '.join(result.split())  # collapse double spaces left by empty placeholders

    if ext and not result.lower().endswith(ext.lower()):
        result += ext

    return result


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    """Strips filesystem-unsafe characters and truncates to max_length
    bytes (UTF-8 aware), preserving the extension."""
    if not filename:
        return "unnamed_file"

    invalid_chars = [':', '<', '>', '|', '?', '*', '"', '─', '‣', '/', '\\', '\n', '\r', '\t']
    for char in invalid_chars:
        filename = filename.replace(char, '_')

    filename = ' '.join(filename.split())
    while '__' in filename:
        filename = filename.replace('__', '_')

    if '.' in filename:
        base, ext = filename.rsplit('.', 1)
        ext = '.' + ext
    else:
        base, ext = filename, ''

    if len(filename.encode('utf-8')) > max_length:
        max_base_bytes = max_length - len(ext.encode('utf-8')) - 10
        encoded_base = base.encode('utf-8')
        if len(encoded_base) > max_base_bytes:
            base = encoded_base[:max_base_bytes].decode('utf-8', errors='ignore').rstrip('_').rstrip()
        filename = base + ext

    return filename.strip() if filename.strip() else "unnamed_file"
