# Akbots - Don't Remove Credit - @AkBots_Official
#
# Filename-based quality/episode parsing used by the File Store auto-batch
# system (Akbots/auto_batch.py) to decide which uploads belong together.

import re
from typing import Dict, Optional

QUALITY_PATTERNS = {
    '144p': (r'144p', 0),
    '240p': (r'240p', 1),
    '360p': (r'360p', 2),
    '480p': (r'480p', 3),
    '720p': (r'720p', 4),
    '1080p': (r'1080p', 5),
    'HDRip': (r'HDRip|HD-Rip|HD Rip', 6),
    '4K': (r'4K|2160p', 7),
}


def extract_quality(filename: str) -> Optional[str]:
    """Extract a quality tag from a filename, e.g. '...S01E01.720p.mkv' -> '720p'."""
    for quality, (pattern, _) in QUALITY_PATTERNS.items():
        if re.search(pattern, filename, re.IGNORECASE):
            return quality
    return None


def get_base_name(filename: str) -> str:
    """Strip extension, quality tags, episode markers etc. to get a
    grouping key. 'Movie.Name.S01E01.1080p.mkv' -> 'Movie Name S01E01'."""
    name = filename.rsplit('.', 1)[0] if '.' in filename else filename
    name = re.sub(r'[.\-_/;:,\\]+', ' ', name)

    for quality, (pattern, _) in QUALITY_PATTERNS.items():
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    patterns_to_remove = [
        r'S\d+E\d+',
        r'\bS\d+\b',
        r'Season\s*\d+',
        r'Episode\s*\d+',
        r'\d{4}',
        r'BluRay|BRRip|WEBRip|WEB-DL',
        r'x264|x265|HEVC',
        r'\bDual\b',
        r'\bAudio\b',
        r'\bMulti\b',
        r'\bmkv\b',
        r'\bmp4\b',
        r'\bavi\b',
        r'\[.*?\]',
        r'\(.*?\)',
    ]
    for pattern in patterns_to_remove:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    name = re.sub(r'[.\-_]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def get_series_name(filename: str) -> str:
    """Like get_base_name but also strips standalone episode numbers, so
    quality-mode batches (which don't need episode granularity) group by
    show only, e.g. 'E07 Ancient Magus' -> 'Ancient Magus'."""
    name = get_base_name(filename)
    patterns = [
        r'\bE\d+\b',
        r'\bEp\d+\b',
        r'\bEpisode\s*\d+\b',
        r'^\d+\s+',
        r'\s+\d+$',
    ]
    for pattern in patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', name).strip()


def get_quality_priority(quality: str) -> int:
    return QUALITY_PATTERNS.get(quality, (None, 999))[1]


def parse_episode_info(filename: str) -> Dict:
    info = {'season': None, 'episode': None}
    match = re.search(r'S(\d+)[._-]*E(\d+)', filename, re.IGNORECASE)
    if match:
        info['season'] = int(match.group(1))
        info['episode'] = int(match.group(2))
    return info
