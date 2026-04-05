"""
Waveform / moodbar generation via VLC CLI subprocess.

Decode audio to a temp WAV, split into 3 frequency bands using
2nd-order biquad filters, return (r, g, b, amplitude) tuples.

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

# ── Defaults ─────────────────────────────────────────────

SAMPLE_RATE = 8000
NUM_BINS = 1600
CHUNK_SAMPLES = 512

# Biquad crossover frequencies (Hz)
DEFAULT_BASS_FC = 300       # LP cutoff for bass band
DEFAULT_TREBLE_FC = 2000    # HP cutoff for treble band

# Amplitude normalisation
DEFAULT_AMP_PERCENTILE = 0.85   # lower = more headroom, more dynamic range
DEFAULT_AMP_GAMMA = 0.65        # lower = more compressed (quiet parts louder)
DEFAULT_COLOR_GAMMA = 2.5       # higher = more vivid/saturated colours


class WaveformSettings:
    """Runtime-adjustable waveform analysis parameters."""

    def __init__(self):
        self.bass_fc = DEFAULT_BASS_FC
        self.treble_fc = DEFAULT_TREBLE_FC
        self.amp_percentile = DEFAULT_AMP_PERCENTILE
        self.amp_gamma = DEFAULT_AMP_GAMMA
        self.color_gamma = DEFAULT_COLOR_GAMMA
        self.draw_mode = 'bars'          # 'bars' or 'envelope'
        self.bar_width = 2               # pixels (bars mode only)
        self.bar_gap = 1                 # pixels (bars mode only)
        self.bar_height = 60             # pixels
        self.played_alpha = 255
        self.unplayed_alpha = 80


# Global singleton -- UI panels modify this in-place
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


# ── Biquad filter design ────────────────────────────────

def _biquad_lp(fc, sr, Q=0.707):
    """2nd-order Butterworth low-pass biquad coefficients."""
    w0 = 2.0 * math.pi * fc / sr
    alpha = math.sin(w0) / (2.0 * Q)
    cos_w0 = math.cos(w0)
    b0 = (1.0 - cos_w0) / 2.0
    b1 = 1.0 - cos_w0
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _biquad_hp(fc, sr, Q=0.707):
    """2nd-order Butterworth high-pass biquad coefficients."""
    w0 = 2.0 * math.pi * fc / sr
    alpha = math.sin(w0) / (2.0 * Q)
    cos_w0 = math.cos(w0)
    b0 = (1.0 + cos_w0) / 2.0
    b1 = -(1.0 + cos_w0)
    b2 = b0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


# ── 3-band energy extraction (biquad) ───────────────────

def _analyse_chunk(chunk, lp_coeffs, hp_coeffs):
    """Return (bass_rms, mid_rms, treble_rms, rms_amplitude) for a chunk.

    Uses 2nd-order biquad LP for bass, HP for treble, and
    mid = signal - bass - treble.  Returns RMS values.
    """
    n = len(chunk)
    if n < 4:
        return (0.0, 0.0, 0.0, 0.0)

    lb0, lb1, lb2, la1, la2 = lp_coeffs
    hb0, hb1, hb2, ha1, ha2 = hp_coeffs

    bass_e = 0.0
    mid_e = 0.0
    treb_e = 0.0
    total_e = 0.0

    # LP filter state
    lx1 = lx2 = ly1 = ly2 = 0.0
    # HP filter state
    hx1 = hx2 = hy1 = hy2 = 0.0

    for i in range(n):
        s = chunk[i]

        # Low-pass (bass)
        lo = lb0 * s + lb1 * lx1 + lb2 * lx2 - la1 * ly1 - la2 * ly2
        lx2 = lx1
        lx1 = s
        ly2 = ly1
        ly1 = lo

        # High-pass (treble)
        hi = hb0 * s + hb1 * hx1 + hb2 * hx2 - ha1 * hy1 - ha2 * hy2
        hx2 = hx1
        hx1 = s
        hy2 = hy1
        hy1 = hi

        # Mid = original minus bass minus treble
        mid = s - lo - hi

        bass_e += lo * lo
        mid_e += mid * mid
        treb_e += hi * hi
        total_e += s * s

    inv_n = 1.0 / n
    return (math.sqrt(bass_e * inv_n),
            math.sqrt(mid_e * inv_n),
            math.sqrt(treb_e * inv_n),
            math.sqrt(total_e * inv_n))


# ── Bin chunk results into NUM_BINS ──────────────────────

def _bin_results(chunk_results, num_bins):
    """Downsample chunk_results to num_bins normalised (r, g, b, amp) entries.

    - Each band is normalised independently to its own track-wide max
    - Amplitude uses percentile-based normalisation with gamma compression
    - color_gamma sharpens the dominant band to produce vivid colours
    """
    n = len(chunk_results)
    if n == 0:
        return [(0.0, 0.0, 0.0, 0.0)] * num_bins

    settings = waveform_settings

    # Average chunks into bins
    binned = []
    for i in range(num_bins):
        lo = int(i * n / num_bins)
        hi = int((i + 1) * n / num_bins)
        if hi <= lo:
            hi = lo + 1
        hi = min(hi, n)

        bass_sum = mid_sum = treb_sum = amp_sum = 0.0
        count = hi - lo
        for j in range(lo, hi):
            b, m, t, a = chunk_results[j]
            bass_sum += b
            mid_sum += m
            treb_sum += t
            amp_sum += a
        inv = 1.0 / count if count else 1.0
        binned.append((bass_sum * inv, mid_sum * inv, treb_sum * inv, amp_sum * inv))

    # -- Amplitude normalisation (RMS-based percentile) --
    amps = sorted(b[3] for b in binned)
    p_idx = min(int(len(amps) * settings.amp_percentile), len(amps) - 1)
    amp_ref = amps[p_idx] if amps[p_idx] > 1e-8 else 1.0

    # -- Per-band normalisation --
    max_bass = max((b[0] for b in binned), default=1e-12) or 1e-12
    max_mid = max((b[1] for b in binned), default=1e-12) or 1e-12
    max_treb = max((b[2] for b in binned), default=1e-12) or 1e-12

    result = []
    color_gamma = settings.color_gamma
    amp_gamma = settings.amp_gamma

    for b, m, t, a in binned:
        # Normalise each band to 0-1 relative to its own max
        rb = min(b / max_bass, 1.0)
        gm = min(m / max_mid, 1.0)
        bt = min(t / max_treb, 1.0)

        # Amplitude: percentile + gamma compression
        amp = min(a / amp_ref, 1.0)
        amp = amp ** amp_gamma

        # Colour gamma: sharpen the dominant band
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
            + tmp_path.replace("\'", "\'\\\'\'")
            + "\'}"
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
    """Full pipeline: decode -> biquad filter -> bin -> normalise."""
    samples = _decode_to_samples(file_path, sample_rate)
    if len(samples) < CHUNK_SAMPLES:
        return []

    settings = waveform_settings

    # Compute biquad coefficients from current settings
    lp_coeffs = _biquad_lp(settings.bass_fc, sample_rate)
    hp_coeffs = _biquad_hp(settings.treble_fc, sample_rate)

    chunk_results = []
    for i in range(0, len(samples) - CHUNK_SAMPLES + 1, CHUNK_SAMPLES):
        chunk = samples[i:i + CHUNK_SAMPLES]
        chunk_results.append(_analyse_chunk(chunk, lp_coeffs, hp_coeffs))

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
