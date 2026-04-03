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
