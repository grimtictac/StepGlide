"""
Performance tracker — always-on timing decorator with Chrome Trace output.

Emits Chrome Trace Format JSON (one file per session) that can be opened
in Perfetto UI (https://ui.perfetto.dev) for interactive timeline analysis.
Also keeps aggregate stats and prints a text summary on dump().

Usage:
    from core.perf import perf

    @perf.track
    def slow_method(self):
        ...

    @perf.track(quiet=True)      # suppress console logging for hot paths
    def poll(self):
        ...

    perf.dump()                   # write trace JSON + print summary
"""

import functools
import json
import os
import threading
import time
from datetime import datetime

_PERF_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'perf_traces')


class PerfTracker:
    """Always-on performance tracker with Chrome Trace JSON output."""

    def __init__(self):
        self.stats = {}           # method_name → {calls, total, min, max, last}
        self._events = []         # Chrome Trace Event list
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self._t0 = time.perf_counter()  # epoch for trace timestamps
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        os.makedirs(_PERF_DIR, exist_ok=True)
        self._trace_path = os.path.join(_PERF_DIR, f'trace_{ts}.json')

    def track(self, method=None, *, quiet=False):
        """Decorator: wraps a method to record its execution time.
        Use @perf.track(quiet=True) to suppress console output for hot paths."""
        if method is None:
            return lambda m: self.track(m, quiet=quiet)
        name = method.__qualname__

        @functools.wraps(method)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            tid = threading.get_ident()
            try:
                return method(*args, **kwargs)
            finally:
                t1 = time.perf_counter()
                elapsed_ms = (t1 - t0) * 1000
                ts_us = (t0 - self._t0) * 1_000_000   # microseconds from start
                dur_us = (t1 - t0) * 1_000_000

                # Chrome Trace Event (Duration event)
                event = {
                    'name': name,
                    'cat': 'perf',
                    'ph': 'X',            # complete duration event
                    'ts': ts_us,
                    'dur': dur_us,
                    'pid': self._pid,
                    'tid': tid,
                }

                with self._lock:
                    self._events.append(event)

                    # Aggregate stats
                    s = self.stats.get(name)
                    if s is None:
                        s = {'calls': 0, 'total': 0.0,
                             'min': float('inf'), 'max': 0.0, 'last': 0.0}
                        self.stats[name] = s
                    s['calls'] += 1
                    s['total'] += elapsed_ms
                    s['last'] = elapsed_ms
                    if elapsed_ms < s['min']:
                        s['min'] = elapsed_ms
                    if elapsed_ms > s['max']:
                        s['max'] = elapsed_ms

                # Console log for noteworthy calls
                if not quiet and elapsed_ms > 1.0:
                    print(f'\033[36m[perf]\033[0m {name}: {elapsed_ms:.1f}ms')

        return wrapper

    def summary(self):
        """Return a formatted text summary of all tracked methods."""
        if not self.stats:
            return 'No performance data collected yet.'
        lines = [
            '',
            '═' * 80,
            '  PERFORMANCE SUMMARY',
            '═' * 80,
            f'  {"Method":<45} {"Calls":>6} {"Total":>9} '
            f'{"Avg":>8} {"Min":>8} {"Max":>8} {"Last":>8}',
            '  ' + '─' * 78,
        ]
        for name in sorted(self.stats, key=lambda n: self.stats[n]['total'],
                           reverse=True):
            s = self.stats[name]
            avg = s['total'] / s['calls'] if s['calls'] else 0
            short = name.split('.')[-1] if '.' in name else name
            lines.append(
                f'  {short:<45} {s["calls"]:>6} {s["total"]:>8.1f}ms '
                f'{avg:>7.1f}ms {s["min"]:>7.1f}ms {s["max"]:>7.1f}ms '
                f'{s["last"]:>7.1f}ms')
        lines.append('═' * 80)
        return '\n'.join(lines)

    def dump(self):
        """Write Chrome Trace JSON, print summary to console."""
        # Write trace file
        with self._lock:
            events = list(self._events)
        if events:
            trace = {'traceEvents': events, 'displayTimeUnit': 'ms'}
            try:
                with open(self._trace_path, 'w', encoding='utf-8') as f:
                    json.dump(trace, f)
                print(f'\033[36m[perf]\033[0m Trace saved → {self._trace_path}')
            except Exception as e:
                print(f'\033[36m[perf]\033[0m Failed to write trace: {e}')

        # Print text summary
        text = self.summary()
        print(text)
        return self._trace_path if events else ''

    def reset(self):
        """Clear all accumulated stats and events."""
        with self._lock:
            self.stats.clear()
            self._events.clear()


# Module-level singleton
perf = PerfTracker()
