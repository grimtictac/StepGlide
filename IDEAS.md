# MusicPlayer — Ideas & Roadmap

Saved ideas from sessions. Grouped and prioritized so we can pick them off in separate commits/branches.

## Already Done ✅
- ~~Waveform scrub bar~~ — full moodbar with genre-coloured segments
- ~~Smart playlists~~ — auto-populate based on genre/artist/rating
- ~~Search/filter bar~~ — type-to-filter with genre, rating, voter, length dropdowns
- ~~Play count / play log~~ — date-grouped history panel with voting
- ~~Voting system~~ — thumbs up/down per voter in the play log
- ~~Debug/logging panel~~ — toggle from menu
- ~~Splash/loading screen~~ — dark themed with animated status
- ~~Windows build~~ — GitHub Actions workflow (onedir)
- ~~Deferred track loading~~ — window paints instantly, tracks load after

## Visual Polish
- Animated handle glow: pulse the pull-fader handle while actively fading (QPropertyAnimation or CSS).
- Volume slider "fill" overlay: paint the groove below the handle solid green→yellow→red.
- Fade progress arc: circular progress indicator showing how far through the fade you are (current vol / starting vol).
- Dark/Light theme toggle: add a second palette and a toggle in settings (theme switcher).
- Album art display: extract embedded cover art with `mutagen` and show in now-playing bar or a panel.
- "Now Playing" highlight: visually highlight the currently playing row in the track table (green tint or bold).
- Album art grid view: browse library as a grid of album covers instead of a table.
- Mini mode: collapse to a small floating widget with just transport controls + track name.

## Fade & Volume Features
- Fade-out-then-pause: button or shortcut to fade to 0 then pause the track.
- Fade-in on unpause: ramp volume up smoothly when resuming from pause/mute.
- Pull-fader UP mode: allow pull-fader to also fade UP (design: mirror or toggle?).
- Cross-fade between tracks: fade out current while fading in next using unified fade model.
- Saved fade presets: "slow fade" / "quick fade" / "DJ cut" presets.
- Fade curve shape: support linear, exponential, logarithmic, S-curve.
- Keyboard-driven fade: hold a key to fade down, release to fade back up (DJ-style).

## Keyboard & Accessibility
- System media key support: play/pause/next/prev from keyboard media keys (`Qt.Key_MediaPlay` etc.).
- Keyboard fade trigger: hold `F` (or configurable) to fade down while held.
- Scroll-wheel focus gating: require volume strip focus for scroll-fade.
- Tooltips with live values: show "Fading at 25 v/s — 3.2s remaining" live in pull-fader tooltip.

## Playlist & Library
- Drag-and-drop reorder: `QListView` with `InternalMove` for playlist reordering.
- Playlist export/import: M3U/PLS export so users can share playlists.
- Duplicate finder: scan library for same-title or same-duration tracks and offer to remove.
- Remember window state: save/restore window size, splitter positions, and column widths to config on exit.

## Technical / Infrastructure
- Unit tests for the fade model: tests for `inject_fade_speed`, `set_fade_speed`, `_pending_interval` logic.
- Config migration versioning: version the XML config to avoid future breakage.
- Undo in settings: snapshot config before opening the dialog and restore on Cancel.
- Audio fingerprinting: use `chromaprint`/AcoustID to auto-tag unknown files.
- Scrobbling: Last.fm / ListenBrainz integration.
- Plugin system: let users drop Python scripts into a `plugins/` folder for custom behaviour.
- Combo box dropdown arrow: fix the global theme `QComboBox::drop-down` so arrows render properly (WIP).

---

### Suggested next steps
1. **Quick wins**: album art display, media key support, remember window state, now-playing highlight.
2. **Medium effort**: cross-fade, mini mode, playlist export, duplicate finder, keyboard fade.
3. **Bigger projects**: album art grid view, audio fingerprinting, scrobbling, plugin system.
4. **Bug**: combo box dropdown arrow still renders wrong — needs fixing in theme.py.
