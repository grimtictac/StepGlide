"""
Smart playlist rule evaluator.

Given a list of rules and a match mode ('all' / 'any'), evaluate
whether a track entry dict satisfies the smart playlist criteria.
"""

from datetime import datetime, timedelta


def evaluate_rules(entry, rules, match_mode='all'):
    """Return True if *entry* satisfies the smart playlist *rules*.

    Parameters
    ----------
    entry : dict
        A track entry dict with keys like 'genre', 'rating', 'play_count',
        'tags', 'last_played', 'artist', 'title', etc.
    rules : list[dict]
        Each rule has 'field', 'op', 'value'.
    match_mode : str
        'all' — every rule must match (AND).
        'any' — at least one rule must match (OR).
    """
    results = [_eval_one(entry, r) for r in rules]
    if match_mode == 'any':
        return any(results)
    return all(results)


def _eval_one(entry, rule):
    """Evaluate a single rule against an entry."""
    field = rule.get('field', '')
    op = rule.get('op', '')
    value = rule.get('value', '')

    if field == 'Genre':
        return _match_str(entry.get('genre', ''), op, str(value))

    elif field == 'Rating':
        return _match_num(entry.get('rating', 0), op, int(value))

    elif field == 'Play Count':
        return _match_num(entry.get('play_count', 0), op, int(value))

    elif field == 'Tag':
        tags = entry.get('tags', [])
        tag_set = set(t.lower() for t in tags) if tags else set()
        target = str(value).lower()
        if op == 'has':
            return target in tag_set
        elif op == 'has not':
            return target not in tag_set

    elif field == 'Last Played (days)':
        raw = entry.get('last_played')
        if not raw:
            # Never played — "within" → False, "older than" → True
            return op == 'older than'
        try:
            last = datetime.fromisoformat(raw)
            age_days = (datetime.now() - last).days
        except Exception:
            return op == 'older than'
        days_val = int(value)
        if op == 'within':
            return age_days <= days_val
        elif op == 'older than':
            return age_days > days_val

    elif field == 'Artist':
        return _match_str(entry.get('artist', ''), op, str(value))

    elif field == 'Title':
        title = entry.get('title', entry.get('basename', ''))
        return _match_str(title, op, str(value))

    return False


def _match_str(actual, op, target):
    """String matching: is / is not / contains (case-insensitive)."""
    a = (actual or '').lower()
    t = target.lower()
    if op == 'is':
        return a == t
    elif op == 'is not':
        return a != t
    elif op == 'contains':
        return t in a
    return False


def _match_num(actual, op, target):
    """Numeric comparison."""
    try:
        a = int(actual) if actual is not None else 0
    except (ValueError, TypeError):
        a = 0
    t = int(target)
    if op == '>=':
        return a >= t
    elif op == '<=':
        return a <= t
    elif op == '=':
        return a == t
    elif op == '!=':
        return a != t
    return False


def collect_matching_paths(playlist, rules, match_mode='all'):
    """Return a list of relative paths for tracks matching the rules.

    Parameters
    ----------
    playlist : list[dict]
        The full playlist (list of entry dicts).
    rules : list[dict]
        Smart playlist rules.
    match_mode : str
        'all' or 'any'.

    Returns
    -------
    list[str]
        Relative file paths of matching tracks.
    """
    return [entry['path'] for entry in playlist
            if evaluate_rules(entry, rules, match_mode)]
