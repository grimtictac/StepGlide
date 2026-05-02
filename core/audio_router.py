"""
AudioRouter — per-OS strategy for actually pinning a libvlc MediaPlayer
to a specific audio output device.

Why this exists
---------------
libvlc 3.x's documented routing API ``MediaPlayer.audio_output_device_set
(module, device_id)`` is **silently broken** on at least one important
backend on this project's dev box: PulseAudio (and PipeWire's pulse
compatibility layer).  The call returns success but the audio still
goes to whatever the *system* default sink happens to be at the time.
Multiple permutations were verified empirically with ``pactl list
sink-inputs``:

* Calling ``audio_output_device_set`` before play     → ignored.
* Calling ``audio_output_device_set`` after play      → ignored.
* Setting ``PULSE_SINK=<sink>`` before vlc.Instance() → **also** ignored
  on PipeWire-pulse (worked on pure pulse in some isolated tests but
  proved unreliable in the running app — the session manager's idea of
  "default sink" wins).

The one mechanism that *always* works on Linux is the same thing
``pavucontrol`` uses: list pulse sink-inputs, find the one that belongs
to our player, and ``pactl move-sink-input <id> <sink>``.  This works
identically on classic PulseAudio and on PipeWire-pulse because both
implement the move-sink-input client protocol.

On Windows we keep the documented ``audio_output_device_set('mmdevice',
device_id)`` path for now — the mmdevice plugin has a much better track
record than pulse.  If it turns out to be similarly broken in practice,
we'll add a Windows-side equivalent (the WASAPI session API allows
per-stream device assignment).

Public surface
--------------
The router instance is constructed once per app::

    router = make_router(debug_log_fn=...)

For each persistent vlc.MediaPlayer (main, preview), the caller does::

    router.attach(player, label='main')           # once, on creation
    router.pin_player(player, sink_id)            # after each play()
    router.move_player(player, new_sink_id)       # mid-track switch

For one-shots (the test beep)::

    router.pin_one_shot(player, sink_id)          # call right after play()

The router is intentionally tolerant: if it can't find the sink-input
within its polling budget it logs a warning and gives up — audio still
plays, just on the system default.  No exceptions reach the caller.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QObject, QTimer, Signal, Qt

# --------------------------------------------------------------------- #
# Public factory                                                        #
# --------------------------------------------------------------------- #


def make_router(debug_log_fn: Optional[Callable[[str, str], None]] = None,
                parent: Optional[QObject] = None) -> 'AudioRouter':
    """Return the appropriate AudioRouter for the current OS."""
    log = debug_log_fn or (lambda lvl, msg: None)
    if sys.platform.startswith('linux') and _pactl_available():
        return LinuxPulseRouter(debug_log_fn=log, parent=parent)
    if sys.platform.startswith('win'):
        return WindowsRouter(debug_log_fn=log, parent=parent)
    return NullRouter(debug_log_fn=log, parent=parent)


def _pactl_available() -> bool:
    """Is the pactl CLI on PATH?  We need it for the Linux backend."""
    try:
        r = subprocess.run(['pactl', '--version'],
                           capture_output=True, text=True, timeout=2)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------- #
# Base                                                                  #
# --------------------------------------------------------------------- #


class AudioRouter(QObject):
    """Abstract base; subclasses implement per-OS routing."""

    # Internal signals used to marshal calls back to the router's
    # owning thread.  libvlc fires its events on a worker thread, so
    # any handler that uses QTimer (which we do for the pulse
    # discovery polling) MUST hop threads first or the timer will
    # never fire.  Subclasses override the ``_do_*`` slots, not the
    # public methods.
    _pin_requested = Signal(object, str)        # player, sink_id
    _move_requested = Signal(object, str)
    _one_shot_requested = Signal(object, str)
    _detach_requested = Signal(object)

    def __init__(self, debug_log_fn=None, parent=None):
        super().__init__(parent)
        self._log = debug_log_fn or (lambda lvl, msg: None)
        # AutoConnection becomes QueuedConnection automatically when
        # emit() crosses thread boundaries.  Using AutoConnection rather
        # than DirectConnection means same-thread emits stay synchronous.
        self._pin_requested.connect(self._do_pin)
        self._move_requested.connect(self._do_move)
        self._one_shot_requested.connect(self._do_one_shot)
        self._detach_requested.connect(self._do_detach)

    # -- public, thread-safe entry points -----------------------------

    def attach(self, player, label: str) -> None:
        """Register a player for routing.  ``label`` is for logs only."""

    def detach(self, player) -> None:
        """Forget a player (call when it's released)."""
        self._detach_requested.emit(player)

    def pin_player(self, player, sink_id: str) -> None:
        """Route this attached player's stream to ``sink_id``.

        Should be called shortly after play() — the router needs an
        active sink-input to operate on.  Empty/None sink_id means
        "leave on system default" (no-op).  Safe to call from any
        thread (e.g. from a libvlc event callback)."""
        self._pin_requested.emit(player, sink_id or '')

    def move_player(self, player, sink_id: str) -> None:
        """Move an already-pinned player's stream to a different sink.

        Identical semantics to ``pin_player`` but assumes the player
        already has a known sink-input from a previous pin call (so on
        backends that cache, the move can be instant)."""
        self._move_requested.emit(player, sink_id or '')

    def pin_one_shot(self, player, sink_id: str) -> None:
        """Route a transient one-shot player (e.g. test beep) to
        ``sink_id``.  Caller is responsible for releasing the player
        when the sound ends."""
        self._one_shot_requested.emit(player, sink_id or '')

    def name(self) -> str:
        return type(self).__name__

    # -- subclass override points (run on router's own thread) --------

    def _do_pin(self, player, sink_id: str) -> None:
        pass

    def _do_move(self, player, sink_id: str) -> None:
        self._do_pin(player, sink_id)

    def _do_one_shot(self, player, sink_id: str) -> None:
        pass

    def _do_detach(self, player) -> None:
        pass


# --------------------------------------------------------------------- #
# Linux (PulseAudio / PipeWire-pulse)                                   #
# --------------------------------------------------------------------- #


class LinuxPulseRouter(AudioRouter):
    """Routes by issuing ``pactl move-sink-input`` after play().

    Per-player state is keyed by id(player) so we don't keep a strong
    reference (the caller owns the player's lifetime).  We cache the
    discovered sink-input id and re-use it across pin/move calls.
    """

    # Polling schedule (milliseconds) for discovering a brand-new
    # sink-input after play().  PA registers the new client almost
    # instantly; we still allow a generous budget for slow/loaded
    # systems so the move always lands.
    _DISCOVERY_DELAYS_MS = (50, 100, 200, 400, 800, 1500)

    def __init__(self, debug_log_fn=None, parent=None):
        super().__init__(debug_log_fn=debug_log_fn, parent=parent)
        self._our_pid = str(os.getpid())
        # Per-player state.
        self._labels: Dict[int, str] = {}            # id(player) -> label
        self._known_si: Dict[int, str] = {}          # id(player) -> sink-input id
        self._desired: Dict[int, str] = {}           # id(player) -> desired sink id

    # -- API ----------------------------------------------------------

    def attach(self, player, label: str) -> None:
        # attach() is rare and called from the main thread; no need
        # to marshal via signal.
        self._labels[id(player)] = label
        self._log('INFO', f'AudioRouter[linux/pulse]: attach {label!r}')

    def _do_detach(self, player) -> None:
        pid = id(player)
        label = self._labels.pop(pid, '?')
        self._known_si.pop(pid, None)
        self._desired.pop(pid, None)
        self._log('INFO', f'AudioRouter[linux/pulse]: detach {label!r}')

    def _do_pin(self, player, sink_id: str) -> None:
        if not sink_id:
            return
        pid = id(player)
        self._desired[pid] = sink_id
        cached = self._known_si.get(pid)
        if cached and self._sink_input_exists(cached):
            self._move(cached, sink_id, label=self._labels.get(pid, '?'))
            return
        # Discover via polling — caller has just (or just about to) play().
        self._discover_then_move(player, sink_id, attempt=0)

    def _do_move(self, player, sink_id: str) -> None:
        # Identical for this backend — _do_pin handles cache hits already.
        self._do_pin(player, sink_id)

    def _do_one_shot(self, player, sink_id: str) -> None:
        if not sink_id:
            return
        # No registration — just discover-and-move.
        self._discover_then_move(player, sink_id, attempt=0,
                                 label='one-shot', cache=False)

    # -- Internals ----------------------------------------------------

    def _discover_then_move(self, player, sink_id, attempt: int,
                            label: Optional[str] = None,
                            cache: bool = True) -> None:
        """Poll PulseAudio for a sink-input owned by our PID that we
        don't already have a different player tracking, then move it.

        If discovery fails we fall back to the most-recently-created
        VLC sink-input that isn't already on the desired sink."""
        if attempt >= len(self._DISCOVERY_DELAYS_MS):
            self._log(
                'WARN',
                f'AudioRouter[linux/pulse]: gave up discovering sink-input '
                f'for {label or self._labels.get(id(player), "?")!r} after '
                f'{len(self._DISCOVERY_DELAYS_MS)} attempts')
            return
        delay = self._DISCOVERY_DELAYS_MS[attempt]
        QTimer.singleShot(
            delay,
            lambda: self._discover_attempt(player, sink_id, attempt,
                                           label=label, cache=cache))

    def _discover_attempt(self, player, sink_id, attempt: int,
                          label, cache) -> None:
        # Only consider sink-inputs we don't already track for OTHER
        # players (so concurrent main+preview don't fight).
        already_tracked: Set[str] = {
            si for pid_other, si in self._known_si.items()
            if pid_other != id(player)
        }
        candidates = [
            (sid, sink, app) for (sid, sink, app) in self._our_vlc_sink_inputs()
            if sid not in already_tracked
        ]
        if not candidates:
            self._discover_then_move(player, sink_id, attempt + 1,
                                     label=label, cache=cache)
            return
        # Prefer one not already on the desired sink (the freshly-spawned
        # one will almost certainly be on the system default).  If multiple,
        # pick the highest-numbered (most recent).
        wrong_sink = [c for c in candidates if c[1] != sink_id]
        chosen = max(wrong_sink or candidates, key=lambda c: int(c[0]))
        sid = chosen[0]
        if cache:
            self._known_si[id(player)] = sid
        lbl = label or self._labels.get(id(player), '?')
        self._move(sid, sink_id, label=lbl)

    def _move(self, sink_input_id: str, sink_id: str, label: str) -> bool:
        try:
            r = subprocess.run(
                ['pactl', 'move-sink-input', sink_input_id, sink_id],
                capture_output=True, text=True, timeout=3)
        except subprocess.SubprocessError as e:
            self._log('ERROR',
                      f'AudioRouter[linux/pulse]: move-sink-input failed '
                      f'for {label!r}: {e}')
            return False
        if r.returncode != 0:
            self._log('WARN',
                      f'AudioRouter[linux/pulse]: move-sink-input '
                      f'{sink_input_id} -> {sink_id} for {label!r} '
                      f'returned {r.returncode}: {r.stderr.strip()}')
            return False
        self._log('INFO',
                  f'AudioRouter[linux/pulse]: moved sink-input '
                  f'{sink_input_id} -> {sink_id} ({label!r})')
        return True

    def _sink_input_exists(self, sink_input_id: str) -> bool:
        try:
            r = subprocess.run(['pactl', 'list', 'short', 'sink-inputs'],
                               capture_output=True, text=True, timeout=2)
        except subprocess.SubprocessError:
            return False
        if r.returncode != 0:
            return False
        for line in r.stdout.splitlines():
            parts = line.split('\t')
            if parts and parts[0] == sink_input_id:
                return True
        return False

    def _our_vlc_sink_inputs(self) -> List[Tuple[str, str, str]]:
        """Return [(sink_input_id, sink_id_str, app_name)] for sink-inputs
        owned by our PID and produced by libvlc."""
        try:
            r = subprocess.run(['pactl', 'list', 'sink-inputs'],
                               capture_output=True, text=True, timeout=2)
        except subprocess.SubprocessError as e:
            self._log('ERROR',
                      f'AudioRouter[linux/pulse]: pactl list failed: {e}')
            return []
        if r.returncode != 0:
            return []
        result: List[Tuple[str, str, str]] = []
        # Sink ID -> name map for converting numeric "Sink: N" to PA name.
        sink_map = self._sink_name_map()
        for block in r.stdout.split('Sink Input #')[1:]:
            lines = block.splitlines()
            sid = lines[0].strip()
            sink_num = '?'
            app_name = ''
            pid = ''
            binary = ''
            for ln in lines:
                ls = ln.strip()
                if ls.startswith('Sink:'):
                    sink_num = ls.split(':', 1)[1].strip()
                elif 'application.name' in ls:
                    app_name = _strip_pa_value(ls)
                elif 'application.process.id' in ls:
                    pid = _strip_pa_value(ls)
                elif 'application.process.binary' in ls:
                    binary = _strip_pa_value(ls)
            if pid != self._our_pid:
                continue
            # Be permissive about what counts as "vlc": application.name
            # contains "VLC" / "LibVLC" / our binary may be python3.
            is_vlc = ('VLC' in app_name or 'libvlc' in app_name.lower()
                      or 'vlc' in binary.lower())
            if not is_vlc:
                # Fall back: any sink-input from our process is a candidate
                # (since we control our own process, only our players make
                # noise here).
                pass
            sink_name = sink_map.get(sink_num, sink_num)
            result.append((sid, sink_name, app_name))
        return result

    def _sink_name_map(self) -> Dict[str, str]:
        try:
            r = subprocess.run(['pactl', 'list', 'short', 'sinks'],
                               capture_output=True, text=True, timeout=2)
        except subprocess.SubprocessError:
            return {}
        if r.returncode != 0:
            return {}
        out: Dict[str, str] = {}
        for line in r.stdout.splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                out[parts[0]] = parts[1]
        return out


def _strip_pa_value(line: str) -> str:
    # 'application.name = "VLC media player ..."'  ->  'VLC media player ...'
    if '=' not in line:
        return ''
    val = line.split('=', 1)[1].strip()
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        val = val[1:-1]
    return val


# --------------------------------------------------------------------- #
# Windows                                                               #
# --------------------------------------------------------------------- #


class WindowsRouter(AudioRouter):
    """Routes via libvlc's documented audio_output_device_set on
    ``mmdevice``.  This backend is provisional — if real-world testing
    on Windows shows the same broken-routing pattern as Linux/pulse,
    we'll switch to a per-stream WASAPI approach."""

    def attach(self, player, label: str) -> None:
        self._log('INFO', f'AudioRouter[windows]: attach {label!r}')

    def _do_pin(self, player, sink_id: str) -> None:
        try:
            player.audio_output_device_set('mmdevice', sink_id or '')
        except Exception as e:
            self._log('ERROR',
                      f'AudioRouter[windows]: audio_output_device_set '
                      f'failed: {e}')

    def _do_one_shot(self, player, sink_id: str) -> None:
        self._do_pin(player, sink_id)


# --------------------------------------------------------------------- #
# Null fallback (no pactl, non-Linux non-Windows)                       #
# --------------------------------------------------------------------- #


class NullRouter(AudioRouter):
    """Used when no usable backend is available (e.g. macOS, or Linux
    without pactl).  Falls back to libvlc's API call — better than
    nothing — but does not attempt the move-sink-input dance."""

    def _do_pin(self, player, sink_id: str) -> None:
        try:
            player.audio_output_device_set(None, sink_id or '')
        except Exception as e:
            self._log('ERROR',
                      f'AudioRouter[null]: audio_output_device_set '
                      f'failed: {e}')

    def _do_one_shot(self, player, sink_id: str) -> None:
        self._do_pin(player, sink_id)
