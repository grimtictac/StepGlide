# PySide6 Music Player — Manual Testing Checklist

Launch with: `.venv-1/bin/python main.py`

---

## Startup & Data Loading
- [ ] **1.** App launches without errors
- [ ] **2.** Track count shown in status bar matches your library
- [ ] **3.** Now-playing bar says "X tracks loaded" on startup

## Playback (Phase 2)
- [ ] **4.** Click a track → double-click plays it
- [ ] **5.** Play/Pause button toggles correctly (icon + state)
- [ ] **6.** Stop button stops playback, resets display
- [ ] **7.** Next/Previous track buttons work
- [ ] **8.** Volume slider changes audio level
- [ ] **9.** Mute button mutes/unmutes
- [ ] **10.** Scrub/seek bar moves during playback; dragging repositions
- [ ] **11.** Time labels update (elapsed / total)
- [ ] **12.** Speed up / speed down / reset buttons work
- [ ] **13.** Auto-reset speed checkbox resets speed on track change

## Keyboard Shortcuts (Phase 3)
- [ ] **14.** Space — play/pause
- [ ] **15.** Right arrow — next track
- [ ] **16.** Left arrow — previous track
- [ ] **17.** Escape — stop
- [ ] **18.** Ctrl+F — focuses search box

## Add Files (Phase 4)
- [ ] **19.** File > Add Files… opens file picker, adds tracks
- [ ] **20.** File > Add Folder… scans recursively, shows progress bar
- [ ] **21.** Duplicates are skipped (add same file twice)

## Search & Filters (Phase 5)
- [ ] **22.** Typing in search box filters tracks in real time
- [ ] **23.** Rating dropdown filters by minimum rating
- [ ] **24.** Liked By dropdown filters by voter
- [ ] **25.** First Played / Last Played / File Created dropdowns filter by date range
- [ ] **26.** Length dropdown filters by duration range
- [ ] **27.** Filters combine (search + rating + length together)

## Sidebar — Genres (Phase 6)
- [ ] **28.** Genre list appears in left sidebar
- [ ] **29.** Clicking a genre filters the table to that genre
- [ ] **30.** Clicking "All" shows all tracks
- [ ] **31.** Genre groups (if configured) appear as expandable parents

## Sidebar — Playlists (Phase 6)
- [ ] **32.** "♫ All Tracks" is the default selection
- [ ] **33.** Right-click playlist list → New Playlist
- [ ] **34.** Right-click a playlist → Rename / Delete
- [ ] **35.** Double-click a playlist filters the table to its tracks

## Tag Bar (Phase 7)
- [ ] **36.** Tag toggle buttons appear below the search bar
- [ ] **37.** Clicking a tag filters to tracks with that tag
- [ ] **38.** Multiple tags = AND filter (only tracks with all selected tags)
- [ ] **39.** Clicking again deselects the tag

## Queue Panel (Phase 8)
- [ ] **40.** Right panel shows Queue tab
- [ ] **41.** Context menu "Add to Queue" adds selected tracks
- [ ] **42.** Double-click a queue item plays it immediately
- [ ] **43.** Move Up / Move Down / Remove buttons work
- [ ] **44.** Queue persists across app restart

## Play Log (Phase 9)
- [ ] **45.** Play Log tab shows date-grouped play history
- [ ] **46.** Entries appear after playing a track
- [ ] **47.** Double-click a log entry plays that track
- [ ] **48.** Right-click → Add to Queue / Jump to Track

## Context Menu (Phase 10)
- [ ] **49.** Right-click a track row → context menu appears
- [ ] **50.** Play Now — plays the track
- [ ] **51.** Add to Queue — adds to queue panel
- [ ] **52.** Edit Title — inline rename via dialog
- [ ] **53.** Genre submenu — change genre
- [ ] **54.** Tags submenu — toggle tags on/off
- [ ] **55.** Add to Playlist submenu — add to existing or new playlist
- [ ] **56.** Remove from Playlist — only visible when a playlist is active
- [ ] **57.** Remove Track(s) — removes from library with confirmation
- [ ] **58.** Multi-select works (Ctrl+click / Shift+click), actions apply to all

## Voting (Phase 11)
- [ ] **59.** 👍 / 👎 buttons in now-playing bar
- [ ] **60.** Rating label updates (+N green, -N red, 0 grey)
- [ ] **61.** Voter combo is editable — type a new name
- [ ] **62.** Same voter can't vote twice on same track in one day (shows message)
- [ ] **63.** Rating updates in the track table row too

## Equalizer (Phase 12)
- [ ] **64.** EQ button in now-playing bar opens dialog
- [ ] **65.** 10 vertical band sliders + preamp slider
- [ ] **66.** Preset dropdown (Flat, Bass Boost, Rock, etc.) sets sliders
- [ ] **67.** Moving sliders updates audio in real time (live preview)
- [ ] **68.** Save persists to DB; next play of that track auto-applies EQ
- [ ] **69.** Reset clears EQ and removes from DB
- [ ] **70.** EQ button turns green when track has a custom EQ

## Settings (Phase 13)
- [ ] **71.** ⚙ button opens Settings dialog
- [ ] **72.** Genres tab — create/rename/delete genre groups, assign genres via checkboxes
- [ ] **73.** Tags tab — add/rename/delete tags
- [ ] **74.** Length tab — edit duration filter ranges
- [ ] **75.** Tooltips tab — edit button hover texts, reset to defaults
- [ ] **76.** Interface tab — queue throb toggle
- [ ] **77.** Save persists to XML and refreshes UI
- [ ] **78.** Cancel discards changes
- [ ] **79.** Snapshot button creates a timestamped config backup

## Random Queue Generator (Phase 14)
- [ ] **80.** Tools > Random Queue Generator… opens dialog
- [ ] **81.** Set genre weight sliders, min rating, recency filter, tags
- [ ] **82.** Generate populates the queue panel
- [ ] **83.** "No tracks match" shown if criteria too strict

## Audit Log (Phase 14)
- [ ] **84.** Tools > Audit Log… opens a table of recent actions
- [ ] **85.** Timestamps, actions, and details are readable

## Panel Toggles & Lite Mode (Phase 15)
- [ ] **86.** F1 — toggle sidebar visibility
- [ ] **87.** F2 — toggle right panel (queue/play log)
- [ ] **88.** F3 — toggle tag bar
- [ ] **89.** F4 — toggle search/filter bar
- [ ] **90.** Ctrl+L — Lite mode (hides sidebar + search + tag bar together)
- [ ] **91.** Ctrl+L again restores all panels
- [ ] **92.** F11 — toggle fullscreen
- [ ] **93.** All available in View menu with shortcuts shown

## Drag-and-Drop (Phase 16)
- [ ] **94.** Drag .mp3/.flac/.ogg/.wav files from file manager onto window → tracks added
- [ ] **95.** Drag a folder → all audio files inside added recursively
- [ ] **96.** Status bar shows "Dropped X track(s)"

## Debug Panel (Phase 17)
- [ ] **97.** F10 — toggles debug panel at bottom of center area
- [ ] **98.** Shows color-coded log entries (green=INFO, orange=WARN, red=ERROR)
- [ ] **99.** "Loaded X tracks from database" appears on startup
- [ ] **100.** Playing a track logs its title
- [ ] **101.** Clear button empties the log
- [ ] **102.** ✕ Hide button collapses the panel
- [ ] **103.** Re-opening replays buffered entries

## General UI
- [ ] **104.** Dark theme applied consistently across all panels
- [ ] **105.** Column headers in track table are clickable for sorting
- [ ] **106.** Column visibility persists across restart
- [ ] **107.** Window size/state is usable on resize
- [ ] **108.** File > Quit (or window close) saves config and queue before exit
