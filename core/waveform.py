"""
Waveform / moodbar generation via VLC CLI subprocess.

Decode audio to a temp WAV, analyse 3-band energy, return
(r, g, b, amplitude) tuples for the UI.

Cross-platform: cvlc on Linux, vlc.exe -I dummy on Windows.
No ffmpeg, no numpy -- pure Python + stdlib.

Heavy lifting in WaveformWorker (QThread) so the UI never blocks.
"""

import array
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
import zlib

from PySide6.QtCore import QThread, Signal

SAMPLE_RATE = 8000
NUM_BINS = 800
CHUNK_SAMPLES = 512


def _find_vlc_cli():
    if sys.platform == "win32":
        # Standard install locations
        for prog in [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")]:
            candidate = os.path.join(prog, "VideoLAN", "VLC", "vlc.exe")
            if os.path.isfile(candidate):
                return candidate
        # Try Windows registry
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\VideoLAN\VLC")
            install_dir = winreg.QueryValueEx(key, "InstallDir")[0]
            winreg.CloseKey(key)
            candidate = os.path.join(install_dir, "vlc.exe")
            if os.path.isfile(candidate):
                return candidate
        except Exception:
            pass
        # Fallback: PATH
        return shutil.which("vlc")
    else:
        return shutil.which("cvlc") or shutil.which("vlc")


_VLC_BIN = _find_vlc_cli()


def _analyse_chunk(samples):
    n = len(samples)
    if n < 4:
        return (0.0, 0.0, 0.0, 0.0)

    a_lo = 0.22
    a_hi = 0.84

    bass_e = 0.0
    mid_e = 0.0
    treb_e = 0.0
    peak = 0.0

    lp = samples[0]
    hp_prev = samples[0]

    for i in range(1, n):
        s = samples[i]
        lp += a_lo * (s - lp)
        bass = lp
        hp = a_hi * (hp_prev + s - samples[i - 1])
        hp_prev = hp
        treble = hp
        mid = s - bass - treble
        bass_e += bass * bass
        mid_e += mid * mid
        treb_e += treble * treble
        a = abs(s)
        if a > peak:
            peak = a

    inv_n = 1.0 / n
    bass_e *= inv_n
    mid_e *= inv_n
    treb_e *= inv_n
    return (bass_e, mid_e, treb_e, peak)


def _bin_results(chunk_results, num_bins):
    n = len(chunk_results)
    if n == 0:
        return [(0.0, 0.0, 0.0, 0.0)] * num_bins

    binned = []
    for i in range(num_bins):
        lo = int(i * n / num_bins)
        hi = int((i + 1) * n / num_bins)
        if hi <= lo:
            hi = lo + 1
        hi = min(hi, n)

        bass_sum = mid_sum = treb_sum = amp_max = 0.0
        count = hi - lo
        for j in range(lo, hi):
            b, m, t, a = chunk_results[j]
            bass_sum += b
            mid_sum += m
            treb_sum += t
            if a > amp_max:
                amp_max = a
        inv = 1.0 / count if count else 1.0
        binned.append((bass_sum * inv, mid_sum * inv, treb_sum * inv, amp_max))

    amps = sorted(b[3] for b in binned)
    p90_idx = int(len(amps) * 0.90)
    amp_ref = amps[p90_idx] if amps[p90_idx] > 1e-6 else 1.0

    max_bass = max_mid = max_treb = 1e-12
    for b, m, t, a in binned:
        if b > max_bass:
            max_bass = b
        if m > max_mid:
            max_mid = m
        if t > max_treb:
            max_treb = t

    result = []
    for b, m, t, a in binned:
        rb = min(b / max_bass, 1.0)
        gm = min(m / max_mid, 1.0)
        bt = min(t / max_treb, 1.0)

        amp = min(a / amp_ref, 1.0)
        amp = math.sqrt(amp)

        # Power curve to increase colour contrast between bands
        rb = rb ** 1.8
        gm = gm ** 1.8
        bt = bt ** 1.8

        # Scale so dominant channel reaches 1.0
        mx = max(rb, gm, bt, 0.01)
        rb /= mx
        gm /= mx
        bt /= mx

        result.append((rb, gm, bt, amp))

    return result


def serialise_waveform(data):
    buf = array.array("f")
    for r, g, b, a in data:
        buf.append(r)
        buf.append(g)
        buf.append(b)
        buf.append(a)
    return zlib.compress(buf.tobytes(), level=6)


def deserialise_waveform(blob):
    raw = zlib.decompress(blob)
    floats = array.array("f")
    floats.frombytes(raw)
    result = []
    for i in range(0, len(floats), 4):
        result.append((floats[i], floats[i + 1], floats[i + 2], floats[i + 3]))
    return result


def _decode_to_samples(file_path, sample_rate=SAMPLE_RATE):
    if not _VLC_BIN:
        return []

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)

    try:
        # Wrap paths in single-quotes for VLC sout parser
        # (handles spaces in Windows user profile paths)
        sout = (
            "#transcode{acodec=s16l,channels=1,samplerate="
            + str(sample_rate)
            + "}:std{access=file,mux=wav,dst='"
            + tmp_path.replace("'", "'\''")
            + "'}"
        )

        if sys.platform == "win32":
            cmd = [_VLC_BIN, "-I", "dummy", "--no-video", "--no-spu",
                   file_path, "--sout=" + sout, "vlc://quit"]
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        else:
            cmd = [_VLC_BIN, "--no-video", "--no-spu",
                   file_path, "--sout=" + sout, "vlc://quit"]
            startupinfo = None

        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            startupinfo=startupinfo,
        )

        if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) < 44:
            return []

        wf = wave.open(tmp_path, "rb")
        n_frames = wf.getnframes()
        if n_frames == 0:
            wf.close()
            return []

        raw = wf.readframes(n_frames)
        wf.close()

        count = len(raw) // 2
        int_samples = struct.unpack("<" + str(count) + "h", raw)
        inv_max = 1.0 / 32768.0
        return [s * inv_max for s in int_samples]

    except (subprocess.TimeoutExpired, OSError, wave.Error):
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _generate_waveform(file_path, num_bins=NUM_BINS, sample_rate=SAMPLE_RATE):
    samples = _decode_to_samples(file_path, sample_rate)
    if len(samples) < CHUNK_SAMPLES:
        return []

    chunk_results = []
    for i in range(0, len(samples) - CHUNK_SAMPLES + 1, CHUNK_SAMPLES):
        chunk = samples[i:i + CHUNK_SAMPLES]
        chunk_results.append(_analyse_chunk(chunk))

    return _bin_results(chunk_results, num_bins)


class WaveformWorker(QThread):
    finished = Signal(str, list)

    def __init__(self, file_path, num_bins=NUM_BINS, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._num_bins = num_bins
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        try:
            data = _generate_waveform(self._file_path, self._num_bins)
        except Exception:
            data = []
        if not self._cancelled:
            self.finished.emit(self._file_path, data)
