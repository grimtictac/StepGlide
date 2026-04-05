"""
Waveform / moodbar generation via VLC CLI subprocess.

Decode audio to a temp WAV, analyse 3-band energy with adaptive
per-file frequency cutoffs, return (r, g, b, amplitude) tuples
for the UI.

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

# ── Defaults (overridable via WaveformSettings) ─────────

SAMPLE_RATE = 8000
NUM_BINS = 1600
CHUNK_SAMPLES = 512

# Default IIR filter coefficients for 8 kHz sample rate
# Low-pass cutoff ~300 Hz:  alpha = 2*pi*fc / (2*pi*fc + sr)
DEFAULT_A_LO = 0.22
# High-pass cutoff ~2000 Hz
DEFAULT_A_HI = 0.84

# Amplitude normalisation
DEFAULT_AMP_PERCENTILE = 0.95
DEFAULT_AMP_GAMMA = 0.85        # higher = less compression
DEFAULT_COLOR_GAMMA = 1.8


class WaveformSettings:
    """Runtime-adjustable waveform analysis parameters."""

    def __init__(self):
        self.adaptive_bands = True       # scan file to set cutoffs
        self.a_lo = DEFAULT_A_LO         # bass LP coefficient
        self.a_hi = DEFAULT_A_HI         # treble HP coefficient
        self.amp_percentile = DEFAULT_AMP_PERCENTILE
        self.amp_gamma = DEFAULT_AMP_GAMMA
        self.color_gamma = DEFAULT_COLOR_GAMMA
        self.bar_width = 2               # pixels
        self.bar_gap = 1                 # pixels
        self.bar_height = 60             # pixels
        self.played_alpha = 255
        self.unplayed_alpha = 80

    def cutoff_hz(self, alpha, sample_rate=SAMPLE_RATE):
        """Convert IIR alpha to approximate cutoff frequency in Hz."""
        if alpha <= 0 or alpha >= 1:
            return 0
        return alpha * sample_rate / (2 * math.pi * (1 - alpha))

    def alpha_from_hz(self, fc, sample_rate=SAMPLE_RATE):
        """Convert cutoff frequency in Hz to IIR alpha coefficient."""
        if fc <= 0:
            return 0.01
        omega = 2 * math.pi * fc / sample_rate
        return omega / (omega + 1)


# Global singleton — UI panels modify this in-place
waveform_settings = WaveformSettings()


# ── Locate VLC CLI binary ────────────────────────────────

def _find_vlc_cli():
    """Return the VLC command-line executable path, or None."""
    if sys.platform == 'win32':
        for prog in [os.environ.get('PROGRAMFILES', r'C:\Program Files'),
                     os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')]:
            candidate = os.path.join(prog, 'VideoLAN', 'VLC', 'vlc.exe')
            if os.path.isfile(candidate):
                return candidate
        # Try Windows Registry
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r'SOFTWARE\VideoLAN\VLC')
            install_dir, _ = winreg.QueryValueEx(key, 'InstallDir')
            winreg.CloseKey(key)
            candidate = os.path.join(install_dir, 'vlc.exe')
            if os.path.isfile(candidate):
                return candidate
        except Exception:
            pass
        return shutil.which('vlc')
    else:
        return shutil.which('cvlc') or shutil.which('vlc')


_VLC_BIN = _find_vlc_cli()


# ── Adaptive frequency band detection ───────────────────

def _estimate_spectral_bands(samples, sample_rate=SAMPLE_RATE):
    """Scan decoded samples and return (a_lo, a_hi) IIR coefficients
    tuned to the file's actual frequency content.

    Strategy: compute the average zero-crossing rate over windows to
    estimate spectral centroid.  Then place bass/treble cutoffs at
    the 25th and 75th percentile of local zero-crossing rates.

    Returns (a_lo, a_hi) alpha values for the IIR filters.
    """
    n = len(samples)
    if n < 4000:
        return (DEFAULT_A_LO, DEFAULT_A_HI)

    # Measure zero-crossing rate in windows
    win_size = min(2048, n // 8)
    zcr_list = []
    for start in range(0, n - win_size, win_size):
        crossings = 0
        for i in range(start + 1, start + win_size):
            if (samples[i] >= 0) != (samples[i - 1] >= 0):
                crossings += 1
        # Zero-crossing rate → approximate frequency
        freq_est = (crossings / win_size) * sample_rate * 0.5
        if freq_est > 10:  # skip silence
            zcr_list.append(freq_est)

    if len(zcr_list) < 4:
        return (DEFAULT_A_LO, DEFAULT_A_HI)

    zcr_list.sort()
    p25 = zcr_list[len(zcr_list) // 4]
    p75 = zcr_list[3 * len(zcr_list) // 4]

    # Clamp to reasonable ranges
    bass_cutoff = max(80, min(600, p25))
    treble_cutoff = max(1000, min(3500, p75))

    # Ensure separation
    if treble_cutoff < bass_cutoff * 2.5:
        treble_cutoff = bass_cutoff * 2.5

    settings = waveform_settings
    a_lo = settings.alpha_from_hz(bass_cutoff, sample_rate)
    a_hi = settings.alpha_from_hz(treble_cutoff, sample_rate)

    return (a_lo, a_hi)


# ── 3-band energy extraction ────────────────────────────

def _analyse_chunk(samples, a_lo, a_hi):
    """Return (bass_energy, mid_energy, treble_energy, peak) for a chunk.

    Uses cascaded single-pole IIR filters:
    - bass:   low-pass at a_lo
    - treble: high-pass at a_hi
    - mid:    original minus bass minus treble
    """
    n = len(samples)
    if n < 4:
        return (0.0, 0.0, 0.0, 0.0)

    bass_e = 0.0
    mid_e = 0.0
    treb_e = 0.0
    peak = 0.0

    lp = samples[0]
    hp_prev = samples[0]

    for i in range(1, n):
        s = samples[i]

        # Low-pass (bass)
        lp += a_lo * (s - lp)
        bass = lp

        # High-pass (treble)
        hp = a_hi * (hp_prev + s - samples[i - 1])
        hp_prev = hp
        treble = hp

        # Mid = original minus bass minus treble
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


# ── Bin chunk results into NUM_BINS ──────────────────────

def _bin_results(chunk_results, num_bins):
    """Downsample chunk_results to num_bins normalised (r, g, b, amp) entries."""
    n = len(chunk_results)
    if n == 0:
        return [(0.0, 0.0, 0.0, 0.0)] * num_bins

    settings = waveform_settings

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

    # -- Normalise amplitude with compression --
    amps = sorted(b[3] for b in binned)
    p_idx = int(len(amps) * settings.amp_percentile)
    p_idx = min(p_idx, len(amps) - 1)
    amp_ref = amps[p_idx] if amps[p_idx] > 1e-6 else 1.0

    max_bass = max_mid = max_treb = 1e-12
    for b, m, t, a in binned:
        if b > max_bass:
            max_bass = b
        if m > max_mid:
            max_mid = m
        if t > max_treb:
            max_treb = t

    result = []
    color_gamma = settings.color_gamma
    amp_gamma = settings.amp_gamma

    for b, m, t, a in binned:
        rb = min(b / max_bass, 1.0)
        gm = min(m / max_mid, 1.0)
        bt = min(t / max_treb, 1.0)

        amp = min(a / amp_ref, 1.0)
        amp = amp ** amp_gamma

        # Power curve to increase colour contrast between bands
        rb = rb ** color_gamma
        gm = gm ** color_gamma
        bt = bt ** color_gamma

        # Scale so dominant channel reaches 1.0
        mx = max(rb, gm, bt, 0.01)
        rb /= mx
        gm /= mx
        bt /= mx

        result.append((rb, gm, bt, amp))

    return result


# ── Serialise / deserialise for DB cache ─────────────────

def serialise_waveform(data):
    """Pack list of (r,g,b,a) tuples into a zlib-compressed blob."""
    buf = array.array('f')
    for r, g, b, a in data:
        buf.append(r)
        buf.append(g)
        buf.append(b)
        buf.append(a)
    return zlib.compress(buf.tobytes(), level=6)


def deserialise_waveform(blob):
    """Unpack a zlib blob back to list of (r,g,b,a) tuples."""
    raw = zlib.decompress(blob)
    floats = array.array('f')
    floats.frombytes(raw)
    result = []
    for i in range(0, len(floats), 4):
        result.append((floats[i], floats[i + 1], floats[i + 2], floats[i + 3]))
    return result


# ── Decode audio file to float samples via VLC CLI ───────

def _decode_to_samples(file_path, sample_rate=SAMPLE_RATE):
    """Transcode file_path to mono 16-bit WAV via VLC, return float list."""
    if not _VLC_BIN:
        return []

    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.wav')
    os.close(tmp_fd)

    try:
        sout = (
            '#transcode{acodec=s16l,channels=1,samplerate='
            + str(sample_rate)
            + "}:std{access=file,mux=wav,dst='"
            + tmp_path.replace("'", "'\\''")
            + "'}"
        )

        if sys.platform == 'win32':
            cmd = [_VLC_BIN, '-I', 'dummy', '--no-video', '--no-spu',
                   file_path, '--sout=' + sout, 'vlc://quit']
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        else:
            cmd = [_VLC_BIN, '--no-video', '--no-spu',
                   file_path, '--sout=' + sout, 'vlc://quit']
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

        wf = wave.open(tmp_path, 'rb')
        n_frames = wf.getnframes()
        if n_frames == 0:
            wf.close()
            return []

        raw = wf.readframes(n_frames)
        wf.close()

        count = len(raw) // 2
        int_samples = struct.unpack('<' + str(count) + 'h', raw)
        inv_max = 1.0 / 32768.0
        return [s * inv_max for s in int_samples]

    except (subprocess.TimeoutExpired, OSError, wave.Error):
        return []
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Main generation pipeline ────────────────────────────

def _generate_waveform(file_path, num_bins=NUM_BINS, sample_rate=SAMPLE_RATE):
    """Full pipeline: decode → adaptive bands → analyse → bin → normalise."""
    samples = _decode_to_samples(file_path, sample_rate)
    if len(samples) < CHUNK_SAMPLES:
        return []

    settings = waveform_settings

    # Adaptive frequency bands: scan the file to find cutoffs
    if settings.adaptive_bands:
        a_lo, a_hi = _estimate_spectral_bands(samples, sample_rate)
    else:
        a_lo = settings.a_lo
        a_hi = settings.a_hi

    chunk_results = []
    for i in range(0, len(samples) - CHUNK_SAMPLES + 1, CHUNK_SAMPLES):
        chunk = samples[i:i + CHUNK_SAMPLES]
        chunk_results.append(_analyse_chunk(chunk, a_lo, a_hi))

    return _bin_results(chunk_results, num_bins)


# ── QThread worker ───────────────────────────────────────

class WaveformWorker(QThread):
    """Background thread that generates waveform data for a single file."""
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
