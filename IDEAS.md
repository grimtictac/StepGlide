# MusicPlayer — Ideas & Roadmap

Saved ideas from the recent session. Grouped and prioritized so we can pick them off in separate commits/branches.

## Visual Polish
- Animated handle glow: pulse the pull-fader handle while actively fading (QPropertyAnimation or CSS).
- Volume slider "fill" overlay: paint the groove below the handle solid green→yellow→red.
- Fade progress arc: circular progress indicator showing how far through the fade you are (current vol / starting vol).
- Dark/Light theme toggle: add a second palette and a toggle in settings (theme switcher).
- Waveform scrub bar: render a waveform image in the scrub area.

## Fade & Volume Features
- Fade-out-then-pause: button or shortcut to fade to 0 then pause the track.
- Fade-in on unpause: ramp volume up smoothly when resuming from pause/mute.
- Pull-fader UP mode: allow pull-fader to also fade UP (design: mirror or toggle?).
- Cross-fade between tracks: fade out current while fading in next using unified fade model.
- Saved fade presets: "slow fade" / "quick fade" / "DJ cut" presets.
- Fade curve shape: support linear, exponential, logarithmic, S-curve.

## Keyboard & Accessibility
- Global hotkeys: system-wide media key support (platform APIs or pynput).
- Keyboard fade trigger: hold `F` (or configurable) to fade down while held.
- Scroll-wheel focus gating: require volume strip focus for scroll-fade.
- Tooltips with live values: show "Fading at 25 v/s — 3.2s remaining" live in pull-fader tooltip.

## Playlist & Library
- Drag-and-drop reorder: `QListView` with `InternalMove` for playlist reordering.
- Smart playlists: auto-populate based on genre/artist/rating from the SQLite DB.
- Search/filter bar: type-to-filter in the library panel.
- Album art display: extract embedded art with `mutagen` and show in transport area.
- Play count / last played: track in DB and surface in library columns.

## Technical / Infrastructure
- Unit tests for the fade model: tests for `inject_fade_speed`, `set_fade_speed`, `_pending_interval` logic.
- Config migration versioning: version the XML config to avoid future breakage.
- Undo in settings: snapshot config before opening the dialog and restore on Cancel.
- Logging panel toggle: show/hide debug log from the menu.
- Push + tag: create a release tag (e.g. `v0.19-fade-fix`) and push to origin.

---

### Suggested next steps
1. Triage and pick 1 small item to implement as a dedicated branch (e.g. `feat/fade-tests`).
2. For multi-day items (waveform, cross-fade, global hotkeys) create separate epics and issue tickets.
3. I can implement the smaller items and create separate commits for each as you requested.

(Stored in repo as `IDEAS.md`.)
