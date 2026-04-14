"""
Formatting utilities — timestamps, durations.
Pure Python, no UI dependencies.
"""

import functools
import time
from datetime import datetime


@functools.lru_cache(maxsize=16384)
def format_ts_absolute(iso_str):
    """Format an ISO timestamp as 'Mon DD, YYYY'. Cached (deterministic)."""
    if not iso_str:
        return 'Never'
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None)
    except Exception:
        return str(iso_str)[:16]
    return dt.strftime('%b %d, %Y')


@functools.lru_cache(maxsize=16384)
def format_ts_relative(iso_str, _now_minute):
    """Format an ISO timestamp as relative text ('3d ago' etc.).
    *_now_minute* is ``int(time.time()) // 60`` — the cache stays valid
    for one minute so repeated calls within the same filter pass are free."""
    if not iso_str:
        return 'Never'
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None)
    except Exception:
        return str(iso_str)[:16]
    now = datetime.now()
    diff = now - dt.replace(tzinfo=None)
    secs = int(diff.total_seconds())
    if secs < 60:
        return 'Just now'
    if secs < 3600:
        return f'{secs // 60} min ago'
    if secs < 86400:
        return f'{secs // 3600}h ago'
    days = secs // 86400
    if days == 1:
        return 'Yesterday'
    if days < 7:
        return f'{days}d ago'
    return dt.strftime('%b %d, %Y')


def format_ts(iso_str, relative=False):
    if relative:
        return format_ts_relative(iso_str, int(time.time()) // 60)
    return format_ts_absolute(iso_str)


def format_duration(seconds):
    """Format seconds as M:SS or H:MM:SS."""
    if seconds is None:
        return '—'
    seconds = int(seconds)
    if seconds < 0:
        return '—'
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f'{h}:{m:02d}:{s:02d}'
    m = seconds // 60
    s = seconds % 60
    return f'{m}:{s:02d}'


def format_time_ms(ms):
    """Format milliseconds as M:SS."""
    if ms is None or ms < 0:
        return '0:00'
    s = ms // 1000
    return f'{s // 60}:{s % 60:02d}'


def build_track_tooltip(entry):
    """Build a rich-text tooltip string with all track info."""
    if not entry:
        return ''
    title = entry.get('title', entry.get('basename', '?'))
    artist = entry.get('artist', '')
    album = entry.get('album', '')
    genre = entry.get('genre', '')
    length = format_duration(entry.get('length'))
    rating = entry.get('rating', 0)
    comment = entry.get('comment', '')
    tags = entry.get('tags', [])
    liked_by = entry.get('liked_by', set())
    disliked_by = entry.get('disliked_by', set())
    plays = entry.get('play_count', 0)
    first_played = format_ts(entry.get('first_played'), relative=False)
    last_played = format_ts(entry.get('last_played'), relative=True)
    path = entry.get('path', '')

    lines = [f'<b>{title}</b>']
    if artist:
        lines.append(f'Artist: {artist}')
    if album:
        lines.append(f'Album: {album}')
    if genre:
        lines.append(f'Genre: {genre}')
    lines.append(f'Length: {length}')
    lines.append(f'Rating: {"+"+str(rating) if rating > 0 else str(rating)}')
    if tags:
        lines.append(f'Tags: {", ".join(sorted(t.upper() for t in tags))}')
    if liked_by:
        lines.append(f'Liked by: {", ".join(sorted(liked_by))}')
    if disliked_by:
        lines.append(f'Disliked by: {", ".join(sorted(disliked_by))}')
    lines.append(f'Plays: {plays}')
    lines.append(f'First played: {first_played}')
    lines.append(f'Last played: {last_played}')
    if comment:
        lines.append(f'Comment: {comment}')
    lines.append(f'<span style="color:#888;">{path}</span>')
    return '<br>'.join(lines)
