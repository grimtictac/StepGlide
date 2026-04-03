"""
Performance tracker — lightweight timing decorator + stats accumulator.
Extracted from the original monolithic player.py.
"""

import functools
import logging
import os
import time
from datetime import datetime

_PERF_LOG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PerfTracker:
    """Lightweight performance tracker: timing decorator + stats accumulator."""

    def __init__(self):
        self.stats = {}          # method_name → {calls, total, min, max, last}
        self._ui_callback = None  # set to a callable(method_name, ms) to update UI
        self.last_action = ''    # last user action context for perf logging
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._log_path = os.path.join(_PERF_LOG_DIR, f'perf_{ts}.log')
        self._logger = logging.getLogger('perf')
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        # File handler — timestamped log
        fh = logging.FileHandler(self._log_path, encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s  %(message)s', datefmt='%H:%M:%S'))
        self._logger.addHandler(fh)
        # Console handler
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('\033[36m[perf]\033[0m %(message)s'))
        self._logger.addHandler(ch)
        self._logger.info(f'Performance log started → {self._log_path}')

    def track(self, method=None, *, quiet=False):
        """Decorator: wraps a method to record its execution time.
        Use @perf.track(quiet=True) to suppress per-call logging."""
        if method is None:
            # Called with arguments: @perf.track(quiet=True)
            return lambda m: self.track(m, quiet=quiet)
        name = method.__qualname__

        @functools.wraps(method)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return method(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - t0) * 1000  # ms
                s = self.stats.get(name)
                if s is None:
                    s = {'calls': 0, 'total': 0.0, 'min': float('inf'), 'max': 0.0, 'last': 0.0}
                    self.stats[name] = s
                s['calls'] += 1
                s['total'] += elapsed
                s['last'] = elapsed
                if elapsed < s['min']:
                    s['min'] = elapsed
                if elapsed > s['max']:
                    s['max'] = elapsed
                # Only log noteworthy calls (> 1ms) to reduce noise
                if not quiet and elapsed > 1.0:
                    ctx = f' [{self.last_action}]' if self.last_action else ''
                    self._logger.info(f'{name}: {elapsed:.1f}ms{ctx}')
                if self._ui_callback:
                    try:
                        self._ui_callback(name, elapsed)
                    except Exception:
                        pass
        return wrapper

    def summary(self):
        """Return a formatted summary string of all tracked methods."""
        if not self.stats:
            return 'No performance data collected yet.'
        lines = ['', '═' * 80, '  PERFORMANCE SUMMARY', '═' * 80,
                 f'  {"Method":<45} {"Calls":>6} {"Total":>9} {"Avg":>8} {"Min":>8} {"Max":>8} {"Last":>8}',
                 '  ' + '─' * 78]
        for name in sorted(self.stats, key=lambda n: self.stats[n]['total'], reverse=True):
            s = self.stats[name]
            avg = s['total'] / s['calls'] if s['calls'] else 0
            short = name.split('.')[-1] if '.' in name else name
            lines.append(f'  {short:<45} {s["calls"]:>6} {s["total"]:>8.1f}ms {avg:>7.1f}ms '
                         f'{s["min"]:>7.1f}ms {s["max"]:>7.1f}ms {s["last"]:>7.1f}ms')
        lines.append('═' * 80)
        return '\n'.join(lines)

    def dump(self):
        """Print summary to console and write to log file."""
        text = self.summary()
        self._logger.info(text)
        return text

    def reset(self):
        """Clear all accumulated stats."""
        self.stats.clear()
        self._logger.info('Stats reset')


# Module-level singleton
perf = PerfTracker()
