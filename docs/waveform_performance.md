# Waveform Moodbar — Performance Notes

Captured 5 April 2026, during planning.

## Three performance phases

### 1. Waveform generation (one-time per track, background QThread)

| Concern | Impact | Mitigation |
|---|---|---|
| VLC null-output decode | Must play the entire file through VLC at max speed to capture PCM. A 5-min track at 8 kHz mono ≈ 2.4 M samples ≈ 9.6 MB raw floats through the callback. | Runs in `QThread` — never blocks the UI. VLC internal decode is native C, fast. |
| 3-band energy analysis | Pure-Python loop over ~2.4 M samples doing simple arithmetic (running averages, squared differences). | ~0.5–2 s per track on a modern CPU. Acceptable in background. Uses `array` module and chunked processing. |
| First play of a track | User sees the bar without waveform for ~1–3 s, then it fills in. | Subtle "analysing…" animation in the bar. DB cache means this only happens once per track, ever. |
| Bulk library load | 10 000 tracks in the library — do NOT pre-analyse. | Lazy: only generate when a track is actually loaded for playback or preview. |

### 2. DB cache read (every subsequent play)

| Concern | Impact | Mitigation |
|---|---|---|
| Compressed blob read | ~2–4 KB zlib decompress per track. | Sub-millisecond. Negligible. |
| Cache miss | Falls back to background generation. | Graceful — plain bar shown until waveform arrives. |

### 3. Paint (every poll tick)

| Concern | Impact | Mitigation |
|---|---|---|
| `paintEvent` cost | ~800 bars × 2 `drawLine` calls (mirrored) = ~1 600 draw calls per repaint. At 60 px height this is trivial for `QPainter`. | `QPainter` line drawing is hardware-accelerated on most platforms. |
| Repaint frequency | Poll timer is 500 ms → only **2 repaints/sec**. Could go to 100 ms (10 fps) for smoother playhead movement without concern. | Very cheap even at 10 fps. |
| Resize handling | Must re-bin the waveform data to match new pixel width. | Pre-compute on resize, cache the binned array. Resizes are rare and fast. |
| Memory | ~800 bins × 4 floats × 4 bytes = ~12.8 KB per track in RAM. | Negligible. |

### Summary

| Phase | Cost | Concern level |
|---|---|---|
| Generation (first time) | ~1–3 s background | Low — threaded + cached |
| Cache read | < 1 ms | None |
| Painting | ~1 600 lines × 2/sec | None — trivial for QPainter |
| Memory | ~13 KB RAM + ~3 KB DB per track | None |

### Key optimisation: cancel on skip

If the user skips tracks rapidly, the in-flight `WaveformWorker` is cancelled
before a new one starts, preventing a pile-up of background threads.

### The only user-visible cost

The 1–3 second delay on the **first-ever play** of a track while the waveform
is generated. After that, playback of that track is instant from the DB cache.
The UI never blocks.
