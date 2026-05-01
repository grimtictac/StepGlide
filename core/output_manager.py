"""
OutputManager — single source of truth for audio device availability.

Owns enumeration, presence tracking, and hot-plug detection for libvlc
audio output devices.  Designed to be the bedrock for the upcoming
two-output (speaker / headphones) playback system.

Key contract: libvlc's silent fallback to system default is treated as
the enemy.  Every device interaction goes through resolve(), which
verifies presence first.  If a configured device is absent we refuse to
apply it rather than allowing audio to come out somewhere unexpected.

All operations log via the supplied debug_log_fn (level, msg) so the
debug panel becomes the single place to inspect device behaviour.

This module deliberately depends only on the standard library + vlc, so
it stays unit-testable and reusable from the preview/main pipeline alike.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

import vlc


@dataclass
class AudioDevice:
    """One enumerated audio output device."""
    device_id: str           # opaque id from libvlc (e.g. PulseAudio sink name)
    description: str         # human-readable label
    present: bool = True     # currently enumerated by libvlc?
    overridden_absent: bool = False  # debug: forced absent for testing

    def is_usable(self) -> bool:
        return self.present and not self.overridden_absent

    def label(self) -> str:
        suffix = ''
        if self.overridden_absent:
            suffix = '  [DEBUG: forced absent]'
        elif not self.present:
            suffix = '  (not present)'
        return f'{self.description}{suffix}'


# Sentinel device id used to mean "system default" (libvlc treats empty
# string / NULL the same way; we standardise on '').
SYSTEM_DEFAULT_ID = ''
SYSTEM_DEFAULT_DESC = 'System Default'


class OutputManager(QObject):
    """Tracks audio output devices and provides safe routing primitives.

    Lifecycle:
      mgr = OutputManager(vlc_instance, debug_log_fn=...)
      mgr.scan(reason='startup')           # initial enumeration
      mgr.start_periodic_scan(interval_ms=3000)  # optional hot-plug detection

    Routing:
      device = mgr.resolve(configured_id, configured_description)
      if device is not None:
          mgr.apply_to_player(media_player, device)

    UI hooks:
      mgr.devices_changed       — list membership or presence changed
      mgr.device_appeared(id, desc)
      mgr.device_disappeared(id, desc)
    """

    devices_changed = Signal()
    device_appeared = Signal(str, str)
    device_disappeared = Signal(str, str)

    def __init__(
        self,
        vlc_instance,
        debug_log_fn: Optional[Callable[[str, str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._instance = vlc_instance
        self._debug_log = debug_log_fn or (lambda lvl, msg: None)

        # Stable list of devices.  System default is always first and always
        # considered usable (libvlc will route somewhere even if all devices
        # vanish — that is the only place we tolerate the silent fallback).
        self._devices: List[AudioDevice] = [
            AudioDevice(
                device_id=SYSTEM_DEFAULT_ID,
                description=SYSTEM_DEFAULT_DESC,
                present=True,
            ),
        ]

        self._scan_timer: Optional[QTimer] = None

        # Test-beep state (held references so libvlc isn't GC'd mid-tone).
        self._active_beeps: List = []
        self._beep_cache: dict = {}

    # ── Enumeration ──────────────────────────────────────

    def _enumerate_raw(self) -> List[Tuple[str, str]]:
        """Call libvlc to enumerate devices.  Returns [(id, description), ...].

        Pure libvlc call — no caching, no state changes.  Used by scan().
        """
        out: List[Tuple[str, str]] = []
        try:
            mp = self._instance.media_player_new()
            mods = mp.audio_output_device_enum()
            if mods:
                mod = mods
                while mod:
                    dev_id = mod.contents.device
                    desc = mod.contents.description
                    if isinstance(dev_id, bytes):
                        dev_id = dev_id.decode('utf-8', errors='replace')
                    if isinstance(desc, bytes):
                        desc = desc.decode('utf-8', errors='replace')
                    out.append((dev_id or '', desc or dev_id or ''))
                    mod = mod.contents.next
                vlc.libvlc_audio_output_device_list_release(mods)
            mp.release()
        except Exception as e:
            self._debug_log('ERROR', f'OutputManager enumeration failed: {e}')
        return out

    def scan(self, reason: str = 'manual') -> List[AudioDevice]:
        """Re-enumerate devices, update presence flags, emit signals.

        Called periodically (via start_periodic_scan) and on demand from
        the Debug menu.  Logs at INFO for the trigger and DEBUG for each
        observed device; logs INFO for any add/remove transition.
        """
        raw = self._enumerate_raw()
        # Normalise to dict {id: description}
        seen = {dev_id: desc for dev_id, desc in raw}

        self._debug_log(
            'INFO',
            f'OutputManager scan ({reason}): {len(seen)} device(s) enumerated',
        )
        for dev_id, desc in raw:
            self._debug_log('DEBUG', f'  device: id={dev_id!r}  desc={desc!r}')

        changed = False

        # 1. Mark existing entries present/absent based on enumeration.
        existing_ids = {d.device_id for d in self._devices
                        if d.device_id != SYSTEM_DEFAULT_ID}
        for dev in self._devices:
            if dev.device_id == SYSTEM_DEFAULT_ID:
                continue
            now_present = dev.device_id in seen
            if now_present != dev.present:
                dev.present = now_present
                changed = True
                if now_present:
                    self._debug_log(
                        'INFO',
                        f'OutputManager: device APPEARED → {dev.description!r} '
                        f'({dev.device_id!r})',
                    )
                    self.device_appeared.emit(dev.device_id, dev.description)
                else:
                    self._debug_log(
                        'WARN',
                        f'OutputManager: device DISAPPEARED → '
                        f'{dev.description!r} ({dev.device_id!r})',
                    )
                    self.device_disappeared.emit(
                        dev.device_id, dev.description)

        # 2. Add any newly-seen devices we hadn't tracked before.
        for dev_id, desc in raw:
            if dev_id == SYSTEM_DEFAULT_ID:
                continue
            if dev_id in existing_ids:
                continue
            self._devices.append(AudioDevice(
                device_id=dev_id, description=desc, present=True,
            ))
            changed = True
            self._debug_log(
                'INFO',
                f'OutputManager: NEW device tracked → {desc!r} ({dev_id!r})',
            )
            self.device_appeared.emit(dev_id, desc)

        if changed:
            self.devices_changed.emit()

        return list(self._devices)

    # ── Periodic scanning ────────────────────────────────

    def start_periodic_scan(self, interval_ms: int = 3000) -> None:
        """Begin polling libvlc for device list changes.  Cheap call."""
        if self._scan_timer is not None:
            return
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(interval_ms)
        self._scan_timer.timeout.connect(lambda: self.scan(reason='periodic'))
        self._scan_timer.start()
        self._debug_log(
            'INFO',
            f'OutputManager: periodic scan started ({interval_ms} ms)',
        )

    def stop_periodic_scan(self) -> None:
        if self._scan_timer is not None:
            self._scan_timer.stop()
            self._scan_timer = None
            self._debug_log('INFO', 'OutputManager: periodic scan stopped')

    # ── Inspection ───────────────────────────────────────

    def devices(self) -> List[AudioDevice]:
        """Return a snapshot of the current device list (system default first)."""
        return list(self._devices)

    def find_by_id(self, device_id: str) -> Optional[AudioDevice]:
        for d in self._devices:
            if d.device_id == device_id:
                return d
        return None

    def find_by_description(self, description: str) -> Optional[AudioDevice]:
        if not description:
            return None
        for d in self._devices:
            if d.description == description and d.is_usable():
                return d
        return None

    def is_present(self, device_id: str) -> bool:
        """True if device_id is enumerated and not debug-overridden absent."""
        dev = self.find_by_id(device_id)
        return bool(dev and dev.is_usable())

    # ── Resolution & application ─────────────────────────

    def resolve(
        self,
        configured_id: str,
        configured_description: str = '',
    ) -> Optional[AudioDevice]:
        """Resolve a configured device to a usable AudioDevice.

        Returns the AudioDevice on success, None if the device cannot be
        used.  Logs the outcome.

        Resolution order:
          1. Empty configured_id  → System Default (always usable)
          2. ID match + present   → exact device
          3. Description match    → device whose ID changed across reboots
                                    (logged as a WARN so the caller can
                                    re-save the new ID to config)
          4. Otherwise            → None  (caller MUST NOT route audio)
        """
        if not configured_id:
            return self._devices[0]   # system default

        dev = self.find_by_id(configured_id)
        if dev and dev.is_usable():
            self._debug_log(
                'DEBUG',
                f'OutputManager.resolve: exact match for {configured_id!r}',
            )
            return dev

        # Try description fallback (handles PulseAudio ID churn between reboots)
        if configured_description:
            alt = self.find_by_description(configured_description)
            if alt is not None:
                self._debug_log(
                    'WARN',
                    f'OutputManager.resolve: id {configured_id!r} not found, '
                    f'matched by description {configured_description!r} → '
                    f'{alt.device_id!r}',
                )
                return alt

        self._debug_log(
            'WARN',
            f'OutputManager.resolve: device UNAVAILABLE — '
            f'id={configured_id!r} desc={configured_description!r}. '
            f'Refusing to route (would silently fall back to system default).',
        )
        return None

    def apply_to_player(self, media_player, device: AudioDevice) -> bool:
        """Route media_player to the given device.

        Returns True on success.  Logs every attempt.  Refuses to apply
        a non-usable device.
        """
        if device is None:
            self._debug_log(
                'ERROR',
                'OutputManager.apply_to_player: refusing to apply None device',
            )
            return False
        if not device.is_usable():
            self._debug_log(
                'ERROR',
                f'OutputManager.apply_to_player: refusing to apply unusable '
                f'device {device.description!r}',
            )
            return False
        try:
            media_player.audio_output_device_set(None, device.device_id)
            shown_id = device.device_id or 'default'
            self._debug_log(
                'INFO',
                f'OutputManager.apply_to_player: routed to '
                f'{device.description!r} ({shown_id!r})',
            )
            return True
        except Exception as e:
            self._debug_log(
                'ERROR',
                f'OutputManager.apply_to_player: vlc set_device raised: {e}',
            )
            return False

    # ── Debug overrides ──────────────────────────────────

    def debug_force_absent(self, device_id: str, absent: bool = True) -> None:
        """Mark a device absent without actually unplugging it.

        Used by the Debug menu to test hot-unplug handling.  The next
        scan() will reset present-ness based on real enumeration but the
        overridden_absent flag persists until cleared.
        """
        dev = self.find_by_id(device_id)
        if dev is None:
            self._debug_log(
                'WARN',
                f'debug_force_absent: unknown device {device_id!r}',
            )
            return
        dev.overridden_absent = absent
        self._debug_log(
            'INFO',
            f'debug_force_absent: {dev.description!r} → '
            f'{"FORCED ABSENT" if absent else "restored"}',
        )
        if absent:
            self.device_disappeared.emit(dev.device_id, dev.description)
        else:
            self.device_appeared.emit(dev.device_id, dev.description)
        self.devices_changed.emit()

    def debug_clear_overrides(self) -> None:
        """Clear all force-absent overrides."""
        any_cleared = False
        for d in self._devices:
            if d.overridden_absent:
                d.overridden_absent = False
                any_cleared = True
                self._debug_log(
                    'INFO',
                    f'debug_clear_overrides: restored {d.description!r}',
                )
                self.device_appeared.emit(d.device_id, d.description)
        if any_cleared:
            self.devices_changed.emit()

    # ── Test beep ────────────────────────────────────────

    def play_test_beep(self, device, freq_hz: int = 880,
                       duration_ms: int = 400) -> bool:
        """Play a short sine-wave beep through the given device.

        Synthesises a small WAV in the system temp dir on first call and
        reuses it.  Each invocation creates a fresh dedicated MediaPlayer
        on the shared vlc.Instance so it doesn't interfere with the main
        playback player or any previous beep that's still ringing out.

        The MediaPlayer is parked on self until the beep ends, then
        released; this prevents the GC from killing libvlc mid-playback.
        Returns True if playback was started.
        """
        if device is None or not device.is_usable():
            self._debug_log(
                'WARN',
                'play_test_beep: refusing to beep unusable device',
            )
            return False

        wav_path = self._ensure_beep_wav(freq_hz, duration_ms)
        if wav_path is None:
            return False

        try:
            mp = self._instance.media_player_new()
            mp.audio_output_device_set(None, device.device_id)
            media = self._instance.media_new(wav_path)
            mp.set_media(media)
            mp.play()
        except Exception as e:
            self._debug_log(
                'ERROR', f'play_test_beep: vlc raised: {e}')
            return False

        # Hold a reference until the tone finishes so libvlc isn't GC'd.
        self._active_beeps.append(mp)
        shown_id = device.device_id or 'default'
        self._debug_log(
            'INFO',
            f'play_test_beep: {freq_hz} Hz / {duration_ms} ms → '
            f'{device.description!r} ({shown_id!r})',
        )
        # Schedule release a little after the tone ends so we don't cut
        # the tail off (libvlc may still be flushing the audio buffer).
        QTimer.singleShot(duration_ms + 600,
                          lambda m=mp: self._release_beep(m))
        return True

    def _release_beep(self, mp) -> None:
        try:
            mp.stop()
        except Exception:
            pass
        try:
            self._active_beeps.remove(mp)
        except ValueError:
            pass

    def _ensure_beep_wav(self, freq_hz: int, duration_ms: int):
        """Lazily synthesise a WAV file for the given tone, cached on disk.

        Cached per (freq, duration) combination so repeated beeps don't
        re-encode.  Returns the absolute path or None on failure.
        """
        key = (int(freq_hz), int(duration_ms))
        cached = self._beep_cache.get(key)
        if cached:
            return cached

        import math
        import os
        import struct
        import tempfile
        import wave

        try:
            sample_rate = 44100
            n_frames = int(sample_rate * duration_ms / 1000)
            # Short attack/release envelope to avoid clicks.
            attack = max(1, int(sample_rate * 0.005))   # 5 ms
            release = max(1, int(sample_rate * 0.020))  # 20 ms
            amp = 0.35  # peak amplitude (avoid clipping headroom)
            two_pi_f = 2.0 * math.pi * freq_hz / sample_rate

            frames = bytearray()
            for i in range(n_frames):
                env = 1.0
                if i < attack:
                    env = i / attack
                elif i > n_frames - release:
                    env = max(0.0, (n_frames - i) / release)
                sample = int(amp * env * math.sin(two_pi_f * i) * 32767)
                frames += struct.pack('<h', sample)

            tmpdir = tempfile.gettempdir()
            path = os.path.join(
                tmpdir, f'stepglide_beep_{freq_hz}hz_{duration_ms}ms.wav')
            with wave.open(path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(bytes(frames))
            self._beep_cache[key] = path
            self._debug_log(
                'DEBUG', f'play_test_beep: synthesised {path}')
            return path
        except Exception as e:
            self._debug_log(
                'ERROR', f'play_test_beep: failed to synth WAV: {e}')
            return None
