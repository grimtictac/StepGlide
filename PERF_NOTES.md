# Performance Notes — MusicPlayer

## `_apply_filter_inner` — the main bottleneck

This method rebuilds the entire treeview listing. With ~7,000 tracks it was
taking **300–380 ms** (up to 1,000 ms in some logs).

### Root cause

The chunked insert loop called `update_idletasks()` every 400 rows, which
forced CustomTkinter geometry processing across the full widget tree (~50 CTk
widgets). Isolated benchmarks showed treeview insert alone is ~55 ms for 7,000
rows; the `update_idletasks()` calls added ~230 ms on top.

### Optimisations applied (April 2026)

| Commit | Change | Savings |
|--------|--------|---------|
| `8db0a7f` | Remove `update_idletasks` from filter insert loop; fix double-filter on sort | ~230 ms / filter |
| `a12f3e2` | LRU-cache `_format_ts` (absolute cached forever, relative cached per minute) | ~20 ms / filter |
| `7356261` | Reuse play dialog (build once, show/hide) instead of recreating CTkToplevel | ~65–115 ms / double-click |
| `3986d6c` | Share CTkFont instances in tag bar rebuild | ~25 ms on tag-set change |
| `f21a3c8` | Replace all 80 CTkFrame with tk.Frame | ~50 ms startup + layout |

### After optimisation

`_apply_filter_inner` dropped from **~300 ms → ~60 ms** on 7,000 tracks.

### What triggers `_apply_filter` (22 call sites)

| Trigger | Frequency |
|---------|-----------|
| Startup (load tracks) | 1× on launch |
| Genre click | Each click |
| Tag button click | Each click |
| Column sort | Each click |
| Rating / liked-by / date / length filter | Each change |
| Reset all filters | Each click |
| Search typing | Each keystroke (debounced) |
| Playlist select / delete | Each action |
| Right-click: set genre, toggle tag, remove track | Each action |
| Add files / Add folder / Library scan | Rare (manual) |
| Settings save | On save |

### What was tested but NOT worth doing

- **Detach/reattach instead of delete/insert** — benchmarked at 60 ms vs 54 ms
  (no improvement) because `tree.item(iid, values=...)` costs as much as
  `tree.insert()`. Only pure `move()` without value updates is fast (5 ms).

- **Full CustomTkinter removal** — attempted on branch `remove-customtkinter`
  with regex-based migration script. Failed: regex too fragile for 5,500-line
  file with multi-line widget constructors. Would save ~712 ms startup but
  not practical without a proper AST-based approach.

### Remaining CTk widget counts (potential future targets)

| Widget | Count |
|--------|-------|
| CTkLabel | 89 |
| CTkButton | 72 |
| CTkEntry | 13 |
| CTkToplevel | 11 |
| CTkOptionMenu | 11 |
| CTkScrollableFrame | 9 |
| CTkSlider | 6 |
| CTkProgressBar | 2 |
| CTkCheckBox | 2 |
| CTkSwitch | 1 |
| CTkTextbox | 1 |
| CTkFont | 147 |
