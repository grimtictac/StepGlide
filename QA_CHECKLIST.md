# Manual QA Checklist

## 1. Drag from Track Table to Sidebar Playlists
- 1.1. Select a single track → drag onto a static playlist → track appears in that playlist
- 1.2. Select multiple tracks → drag onto a static playlist → all tracks added
- 1.3. Drag onto "All Tracks" → drop is rejected (no highlight, nothing added)
- 1.4. Drag onto a smart playlist → drop is rejected
- 1.5. Drag the same track onto a playlist that already contains it → no duplicate added
- 1.6. After dropping, playlist item count updates in the sidebar
- 1.7. Select the playlist after dropping → track table filters to show the playlist contents including new tracks

## 2. Drag from Track Table to Queue Panel
- 2.1. Select a single track → drag onto empty queue → track appears in queue
- 2.2. Select multiple tracks → drag onto queue → all tracks added in order
- 2.3. Drag onto top half of an existing queue item → tracks insert above that item
- 2.4. Drag onto bottom half of an existing queue item → tracks insert below that item
- 2.5. Drag below the last queue item → tracks append to end
- 2.6. Cyan drop indicator line appears while dragging, tracking cursor position
- 2.7. Drop indicator disappears after drop completes
- 2.8. Drop indicator disappears if you drag away without dropping (drag leave)

## 3. Queue Internal Reorder
- 3.1. Drag a queue item to a different position → item moves, queue order updates
- 3.2. Drop on top half of another item → moved item lands above target
- 3.3. Drop on bottom half of another item → moved item lands below target
- 3.4. Drop indicator line shows during internal drag
- 3.5. Item does not vanish or duplicate after drop
- 3.6. After reorder, double-click an item → correct track plays
- 3.7. After reorder, "next track" plays the correct queue order

## 4. Queue Playback Integration
- 4.1. Add tracks to queue → let current song finish → next song is first queue item
- 4.2. Queue items are consumed (removed) when played
- 4.3. Queue empties → playback falls back to sequential playlist order
- 4.4. Reorder queue → let song finish → plays in new queue order
- 4.5. Double-click a queue item → that track plays immediately, item removed from queue

## 5. Track Table Not Droppable
- 5.1. Cannot drag tracks from queue into the track table
- 5.2. Cannot drag files from the OS file manager into the track table
- 5.3. No drop indicators appear over the track table
- 5.4. Track table rows cannot be reordered by drag-drop

## 6. Column Sort — Wait Cursor
- 6.1. Click a column header → cursor changes to wait/busy indicator
- 6.2. After sort completes → cursor returns to normal
- 6.3. Click a column header during an active sort → second click is ignored
- 6.4. Rapidly click different column headers → only one sort executes, no crash

## 7. Column Sort — Correctness
- 7.1. Sort by Title → alphabetical order, click again → reversed
- 7.2. Sort by Artist → alphabetical order
- 7.3. Sort by Rating → numeric order
- 7.4. Sort by Plays → numeric order
- 7.5. Sort by Length → numeric order
- 7.6. Sort by Last Played → date order
- 7.7. Sort by Tags → alphabetical (comma-joined)
- 7.8. Play a track (updating last_played) → re-sort by Last Played → new value is in correct position
- 7.9. Vote on a track → re-sort by Rating → new value is in correct position
- 7.10. Sort with a genre/playlist/search filter active → only visible rows are sorted, filter still applied

## 8. Existing Functionality (Regression Check)
- 8.1. Double-click a track in the table → plays correctly
- 8.2. Right-click a track → context menu works (add to queue, add to playlist, etc.)
- 8.3. Multi-select tracks → right-click → batch operations work
- 8.4. Genre filter in sidebar → track table filters correctly
- 8.5. Playlist selection in sidebar → track table filters correctly
- 8.6. Smart playlist selection → evaluates and displays correctly
- 8.7. Search/filter bar → filters track table correctly
- 8.8. Queue panel Clear button → empties queue
- 8.9. Queue panel ▲/▼/⤒ buttons → move items correctly
- 8.10. Queue panel right-click → Remove / Move to Top / Clear work
- 8.11. Playback controls (play/pause/stop/next/prev) all function
- 8.12. Column visibility toggle (right-click header) still works
- 8.13. Column drag-reorder (drag header sections) still works
