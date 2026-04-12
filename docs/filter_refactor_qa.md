# Filter Refactor QA — What to Break

## Background

Removing `QSortFilterProxyModel` (`TrackFilterProxy`) and doing all
filtering + sorting in pure Python.  The model receives a pre-filtered,
pre-sorted list directly.

Expected improvement: filter/sort from ~500-2000ms → ~5-50ms.

---

## 1. Now-Playing Highlight

**Risk:** Highlight gets lost or sticks to the wrong row after filter change.

**Tests:**
- Play a Rock track → filter to Jazz → the playing track disappears from the list (expected) → clear filter → **the playing track should be highlighted again**
- Play track #3 → sort by Artist → **highlight should follow the track, not stay on row 3**
- Play a track → search for something that excludes it → clear search → **highlight should return**

**What to look for:** Yellow/highlighted row is on the WRONG track, or no row is highlighted when something is playing.

---

## 2. Selection Persistence

**Risk:** Selections vanish or point to wrong tracks after filter/sort.

**Tests:**
- Select 5 tracks → change genre filter → **selections may clear (acceptable) but should NOT point to different tracks**
- Select tracks 1, 3, 5 → sort by a different column → **check that the same tracks are still selected, not the tracks that moved into positions 1, 3, 5**
- Select tracks → search for a term that keeps some selected tracks → **remaining selected tracks should stay selected (nice to have, not critical)**

**What to look for:** Right-click after re-sort shows the wrong track name in the context menu.

---

## 3. Double-Click Plays Correct Track

**Risk:** View row maps to wrong playlist entry — plays wrong song. **This is the scariest bug because it's silent.**

**Tests:**
- Filter to a genre → double-click the 3rd visible track → **check that the NOW PLAYING label matches what you clicked**
- Sort by Artist → double-click a track → **verify it's the right song**
- Search for "love" → double-click a result → **verify correct song plays**
- Clear all filters → double-click first track → **verify it's correct**
- With a sort active, filter to a genre, double-click → **still correct?**

**What to look for:** The track title in the transport bar doesn't match the row you double-clicked. Listen to make sure it's the right audio file.

---

## 4. Context Menu Actions

**Risk:** Edit/tag/vote/remove operates on the wrong track.

**Tests:**
- Sort by Plays descending → right-click the top track → Edit Title → **does the dialog show the correct current title?**
- Filter to a genre → right-click a track → Set Genre → **does the correct track's genre change?**
- Filter + sort active → right-click → Toggle Tag → **check the right track gets the tag**
- Multi-select 3 tracks → right-click → Remove Tracks → **are the correct 3 removed?**
- Right-click → Add to Playlist → **do the correct tracks appear in the playlist?**

**What to look for:** After the action, the wrong track has the changed title/genre/tag, or the wrong tracks got removed.

---

## 5. Sort Column Indicator

**Risk:** The header arrow shows on the wrong column or doesn't appear.

**Tests:**
- Click Artist header → **arrow appears on Artist column, tracks are sorted**
- Click Artist again → **arrow flips direction, tracks reverse-sort**
- Click Title header → **arrow moves from Artist to Title**
- Change a filter → **arrow should stay on the same column with same direction**
- Close and reopen app → **sort state may or may not persist (check what's expected)**

**What to look for:** Arrow on wrong column, or no arrow at all, or sort direction wrong.

---

## 6. Row Update After Vote/Tag Change

**Risk:** After voting or toggling a tag, the row doesn't refresh — or the wrong row refreshes.

**Tests:**
- Sort by Rating → vote thumbs-up on a track → **the rating column should update immediately for that track**
- With a search active → vote on a visible track → **correct row updates**
- Vote from the Play Log panel (not the track table) → **the track table row should still update if visible**
- Toggle a tag on a track while sorted by Tags → **row updates, and if sort order changes, the row should move**

**What to look for:** Rating/tag column still shows old value after the action. Or a DIFFERENT row's value changed.

---

## 7. Drag-and-Drop to Playlists

**Risk:** Dragging tracks from the table to a sidebar playlist adds the wrong tracks.

**Tests:**
- Sort by Artist → select 3 tracks → drag to a playlist → **check the playlist contains the 3 tracks you selected, not whatever was in those row positions before sorting**
- Filter to a genre → drag 2 tracks to a playlist → **correct tracks added**
- Search + sort active → drag tracks → **correct tracks added**

**What to look for:** Open the playlist and see tracks you didn't drag.

---

## 8. Jump-to-Playing

**Risk:** The "jump to currently playing track" action scrolls to the wrong row.

**Tests:**
- Play a track → scroll away → press the jump-to-playing shortcut → **table scrolls to and highlights the playing track**
- Play a track → apply a filter that hides it → jump-to-playing → **should either do nothing or clear filters and jump (decide which behaviour is correct)**
- Play a track → sort by a different column → jump-to-playing → **scrolls to the correct row in the new sort order**

**What to look for:** Table scrolls to a different track, or doesn't scroll at all when it should.

---

## 9. Track Count Label

**Risk:** "X of Y tracks" shows wrong numbers.

**Tests:**
- No filters → should show "Y tracks" (total count)
- Filter to a genre → should show "X of Y tracks" where X < Y
- Clear filter → should go back to "Y tracks"
- Search for something with no matches → should show "0 of Y tracks"

---

## 10. Edge Cases

- **Empty library:** No tracks loaded. Everything should still render without crashes.
- **Single track:** One track in library. Filter/sort/play should all work.
- **All tracks filtered out:** Every filter combination results in 0 matches. Table should be empty, no crashes.
- **Rapid filter changes:** Click through genres quickly. No stale data, no crashes, no visual glitches.
- **Filter while playing:** Change filters rapidly while a track is playing. Playback should not be interrupted.

---

## Quick Smoke Test (do this first)

1. Launch app
2. Double-click a track — does it play?
3. Click Genre "Rock" in sidebar — does the list filter?
4. Click a column header — does it sort?
5. Double-click a track in the filtered+sorted view — does the RIGHT track play?
6. Clear filters — does the full list return with now-playing highlighted?

If all 6 pass, the core mapping is correct. Then work through the rest.
