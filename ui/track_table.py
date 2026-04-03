"""
Track table — QTableView + QAbstractTableModel + QSortFilterProxyModel.

This is the centrepiece of the PySide6 migration: a virtual table that
only renders visible rows, with sorting and filtering in C++ land.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    Qt, Signal,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHeaderView, QMenu, QTableView

from core.formatters import format_duration, format_ts
from ui.theme import COLORS

# ── Column definitions ───────────────────────────────────

ALL_COLUMNS = (
    'Title', 'Artist', 'Album', 'Genre', 'Length', 'Rating',
    'Comment', 'Tags', 'Liked By', 'Disliked By',
    'Plays', 'First Played', 'Last Played', 'File Created',
    'Path', 'Relative Path',
)

# Default visible columns (matches the standard layout)
DEFAULT_VISIBLE_COLUMNS = (
    'Genre', 'Title', 'Length', 'Liked By', 'Rating',
    'Plays', 'Last Played', 'Tags', 'Comment',
)

# Map column index → key function for extracting display data from an entry dict
def _col_value(entry, col_idx):
    """Return the display string for a column."""
    if col_idx == 0:    # Title
        return entry.get('title', entry.get('basename', ''))
    elif col_idx == 1:  # Artist
        return entry.get('artist', '')
    elif col_idx == 2:  # Album
        return entry.get('album', '')
    elif col_idx == 3:  # Genre
        return entry.get('genre', '')
    elif col_idx == 4:  # Length
        return format_duration(entry.get('length'))
    elif col_idx == 5:  # Rating
        r = entry.get('rating', 0)
        return f'+{r}' if r > 0 else str(r)
    elif col_idx == 6:  # Comment
        return entry.get('comment', '')
    elif col_idx == 7:  # Tags
        tags = entry.get('tags', [])
        return ', '.join(sorted(t.upper() for t in tags)) if tags else '—'
    elif col_idx == 8:  # Liked By
        lb = entry.get('liked_by', set())
        return ', '.join(sorted(lb)) if lb else '—'
    elif col_idx == 9:  # Disliked By
        db = entry.get('disliked_by', set())
        return ', '.join(sorted(db)) if db else '—'
    elif col_idx == 10: # Plays
        return str(entry.get('play_count', 0))
    elif col_idx == 11: # First Played
        return format_ts(entry.get('first_played'), relative=False)
    elif col_idx == 12: # Last Played
        return format_ts(entry.get('last_played'), relative=True)
    elif col_idx == 13: # File Created
        return format_ts(entry.get('file_created'), relative=False)
    elif col_idx == 14: # Path
        return entry.get('_abs_path', entry.get('path', ''))
    elif col_idx == 15: # Relative Path
        return entry.get('path', '')
    return ''


def _sort_value(entry, col_idx):
    """Return a sortable value for a column."""
    if col_idx == 0:
        return (entry.get('title') or entry.get('basename', '')).lower()
    elif col_idx == 1:
        return (entry.get('artist') or '').lower()
    elif col_idx == 2:
        return (entry.get('album') or '').lower()
    elif col_idx == 3:
        return (entry.get('genre') or '').lower()
    elif col_idx == 4:
        return entry.get('length') or 0
    elif col_idx == 5:
        return entry.get('rating', 0)
    elif col_idx == 6:
        return (entry.get('comment') or '').lower()
    elif col_idx == 7:
        return ', '.join(sorted(entry.get('tags', []))).lower()
    elif col_idx == 8:
        return ', '.join(sorted(entry.get('liked_by', set()))).lower()
    elif col_idx == 9:
        return ', '.join(sorted(entry.get('disliked_by', set()))).lower()
    elif col_idx == 10:
        return entry.get('play_count', 0)
    elif col_idx in (11, 12, 13):
        keys = {11: 'first_played', 12: 'last_played', 13: 'file_created'}
        return entry.get(keys[col_idx]) or ''
    elif col_idx in (14, 15):
        return (entry.get('path') or '').lower()
    return ''


# ── Table Model ──────────────────────────────────────────

class TrackTableModel(QAbstractTableModel):
    """Wraps the playlist list of dicts directly. No data copying."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks = []        # reference to the backend's playlist list
        self._now_playing_idx = None  # playlist index of currently playing track

    def set_tracks(self, tracks):
        """Replace the backing data. tracks is a list of entry dicts."""
        self.beginResetModel()
        self._tracks = tracks
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._tracks)

    def columnCount(self, parent=QModelIndex()):
        return len(ALL_COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._tracks):
            return None
        entry = self._tracks[row]

        if role == Qt.DisplayRole:
            return _col_value(entry, col)

        elif role == Qt.ForegroundRole:
            # Now-playing row gets green text
            if self._now_playing_idx is not None:
                pl_idx = entry.get('_playlist_idx')
                if pl_idx == self._now_playing_idx:
                    return QColor(COLORS['now_playing_fg'])
            # Rating column colouring
            if col == 5:
                r = entry.get('rating', 0)
                if r > 0:
                    return QColor(COLORS['green_text'])
                elif r < 0:
                    return QColor(COLORS['red_text'])
                return QColor(COLORS['fg_muted'])
            return None

        elif role == Qt.BackgroundRole:
            # Now-playing row highlight
            if self._now_playing_idx is not None:
                pl_idx = entry.get('_playlist_idx')
                if pl_idx == self._now_playing_idx:
                    return QColor(COLORS['now_playing_bg'])
            return None

        elif role == Qt.TextAlignmentRole:
            if col in (4, 5, 10):  # Length, Rating, Plays — center
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        elif role == Qt.UserRole:
            # Return the playlist index for selection tracking
            return entry.get('_playlist_idx')

        elif role == Qt.UserRole + 1:
            # Return raw sort value
            return _sort_value(entry, col)

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section < len(ALL_COLUMNS):
                return ALL_COLUMNS[section]
        return None

    def set_now_playing(self, playlist_idx):
        """Update the now-playing index and refresh affected rows."""
        old = self._now_playing_idx
        self._now_playing_idx = playlist_idx
        # Refresh old and new rows
        for idx in (old, playlist_idx):
            if idx is not None:
                for row, entry in enumerate(self._tracks):
                    if entry.get('_playlist_idx') == idx:
                        self.dataChanged.emit(
                            self.index(row, 0),
                            self.index(row, self.columnCount() - 1))
                        break

    def update_row(self, playlist_idx):
        """Refresh a single row's display after data change (vote, tag, etc.)."""
        for row, entry in enumerate(self._tracks):
            if entry.get('_playlist_idx') == playlist_idx:
                self.dataChanged.emit(
                    self.index(row, 0),
                    self.index(row, self.columnCount() - 1))
                return


# ── Filter Proxy ─────────────────────────────────────────

class TrackFilterProxy(QSortFilterProxyModel):
    """Handles all filtering (genre, tags, search, rating, dates, length, playlist)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDynamicSortFilter(False)  # we control when to invalidate
        self.setSortRole(Qt.UserRole + 1)

        # Filter state
        self._genre_filter = None       # set of genres, or None for All
        self._active_tags = set()
        self._search_tokens = []        # [(field_fn_or_None, term), ...]
        self._rating_threshold = None   # (op, val) or None
        self._liked_by_filter = None    # voter name or None
        self._first_played_filter = 'All'
        self._last_played_filter = 'All'
        self._file_created_filter = 'All'
        self._length_filter = 'All'
        self._length_range = (None, None)  # (lo, hi) in seconds
        self._playlist_paths = None     # set of paths or None

    def set_genre_filter(self, genres):
        """genres is a set of genre strings, or None for All."""
        self._genre_filter = genres
        self.invalidateFilter()

    def set_tag_filter(self, tags):
        self._active_tags = tags
        self.invalidateFilter()

    def set_search_tokens(self, tokens):
        self._search_tokens = tokens
        self.invalidateFilter()

    def set_rating_filter(self, threshold):
        self._rating_threshold = threshold
        self.invalidateFilter()

    def set_liked_by_filter(self, voter):
        self._liked_by_filter = voter
        self.invalidateFilter()

    def set_date_filter(self, which, value):
        """which is 'first_played', 'last_played', or 'file_created'."""
        setattr(self, f'_{which}_filter', value)
        self.invalidateFilter()

    def set_length_filter(self, label, lo, hi):
        self._length_filter = label
        self._length_range = (lo, hi)
        self.invalidateFilter()

    def set_playlist_filter(self, paths):
        """paths is a set of file paths, or None for All."""
        self._playlist_paths = paths
        self.invalidateFilter()

    def clear_all_filters(self):
        self._genre_filter = None
        self._active_tags = set()
        self._search_tokens = []
        self._rating_threshold = None
        self._liked_by_filter = None
        self._first_played_filter = 'All'
        self._last_played_filter = 'All'
        self._file_created_filter = 'All'
        self._length_filter = 'All'
        self._length_range = (None, None)
        self._playlist_paths = None
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if source_row >= len(model._tracks):
            return False
        entry = model._tracks[source_row]

        # Playlist filter
        if self._playlist_paths is not None:
            if entry['path'] not in self._playlist_paths:
                return False

        # Genre filter
        if self._genre_filter is not None:
            if entry.get('genre') not in self._genre_filter:
                return False

        # Tag filter
        if self._active_tags:
            track_tags = entry.get('tags')
            if not track_tags or not self._active_tags.intersection(track_tags):
                return False

        # Rating filter
        if self._rating_threshold is not None:
            op, val = self._rating_threshold
            rating = entry.get('rating', 0)
            if op == '>=' and rating < val:
                return False
            elif op == '<=' and rating > val:
                return False
            elif op == '=' and rating != val:
                return False

        # Liked-by filter
        if self._liked_by_filter:
            if self._liked_by_filter not in entry.get('liked_by', set()):
                return False

        # Date filters
        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        for field_key, filter_val in [
            ('first_played', self._first_played_filter),
            ('last_played', self._last_played_filter),
            ('file_created', self._file_created_filter),
        ]:
            if filter_val == 'All':
                continue
            raw = entry.get(field_key)
            try:
                d = datetime.fromisoformat(raw).date() if raw else None
            except Exception:
                d = None
            if filter_val == 'Today' and (not d or d != today):
                return False
            if filter_val == 'This Week' and (not d or d < week_ago):
                return False
            if filter_val == 'This Month' and (not d or d < month_ago):
                return False

        # Length filter
        if self._length_filter != 'All':
            lo, hi = self._length_range
            track_len = entry.get('length')
            if track_len is None:
                return False
            if lo is not None and hi is not None and not (lo <= track_len < hi):
                return False
            elif lo is not None and hi is None and track_len < lo:
                return False
            elif hi is not None and lo is None and track_len >= hi:
                return False

        # Search filter
        if self._search_tokens:
            title_lower = (entry.get('title') or entry.get('basename', '')).lower()
            artist_lower = (entry.get('artist') or '').lower()
            album_lower = (entry.get('album') or '').lower()
            genre_lower = (entry.get('genre') or '').lower()
            comment_lower = (entry.get('comment') or '').lower()
            tags_lower = ' '.join(entry.get('tags', [])).lower()
            liked_lower = ' '.join(entry.get('liked_by', set())).lower()
            path_lower = entry.get('path', '').lower()
            all_text = f'{title_lower} {artist_lower} {album_lower} {genre_lower} {comment_lower} {tags_lower} {liked_lower} {path_lower}'

            for field_fn, term in self._search_tokens:
                if field_fn is not None:
                    if term not in field_fn(entry):
                        return False
                else:
                    if term not in all_text:
                        return False

        return True


# ── Table View widget ────────────────────────────────────

class TrackTableView(QTableView):
    """Pre-configured QTableView for the track listing."""

    # Signals for main window to connect to
    play_requested = Signal(int)          # playlist_idx — double-click
    context_menu_requested = Signal(int, object)  # playlist_idx, QPoint
    selection_changed = Signal(list)       # list of playlist indices

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.ExtendedSelection)
        self.setShowGrid(False)
        self.setSortingEnabled(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(34)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionsMovable(True)
        self.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.horizontalHeader().customContextMenuRequested.connect(
            self._on_header_context_menu)

        # Default column widths (tuned for the standard layout)
        self._default_widths = {
            0: 280,   # Title (stretch fills remaining)
            1: 150,   # Artist
            2: 150,   # Album
            3: 120,   # Genre
            4: 55,    # Length
            5: 55,    # Rating
            6: 160,   # Comment
            7: 180,   # Tags
            8: 80,    # Liked By
            9: 80,    # Disliked By
            10: 45,   # Plays
            11: 90,   # First Played
            12: 80,   # Last Played
            13: 90,   # File Created
            14: 250,  # Path
            15: 200,  # Relative Path
        }

    def setModel(self, model):
        super().setModel(model)
        # Apply default column widths
        for col, width in self._default_widths.items():
            self.setColumnWidth(col, width)
        # Apply default column visibility
        self.set_visible_columns(list(DEFAULT_VISIBLE_COLUMNS))
        # Connect selection
        sel_model = self.selectionModel()
        if sel_model:
            sel_model.selectionChanged.connect(self._on_selection_changed)

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid():
            playlist_idx = index.data(Qt.UserRole)
            if playlist_idx is not None:
                self.play_requested.emit(playlist_idx)
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid():
            playlist_idx = index.data(Qt.UserRole)
            if playlist_idx is not None:
                self.context_menu_requested.emit(playlist_idx, event.globalPos())
                return
        super().contextMenuEvent(event)

    def _on_selection_changed(self, selected, deselected):
        indices = []
        for index in self.selectionModel().selectedRows():
            pl_idx = index.data(Qt.UserRole)
            if pl_idx is not None:
                indices.append(pl_idx)
        self.selection_changed.emit(indices)

    def _on_header_context_menu(self, pos):
        """Column visibility toggle menu."""
        menu = QMenu(self)
        header = self.horizontalHeader()
        for col in range(self.model().columnCount()):
            col_name = ALL_COLUMNS[col] if col < len(ALL_COLUMNS) else str(col)
            is_hidden = header.isSectionHidden(col)
            action = menu.addAction(col_name)
            action.setCheckable(True)
            action.setChecked(not is_hidden)
            if col == 0:  # Title always visible
                action.setEnabled(False)
            action.setData(col)
            action.toggled.connect(lambda checked, c=col: self._toggle_column(c, checked))
        menu.exec_(self.horizontalHeader().mapToGlobal(pos))

    def _toggle_column(self, col, visible):
        self.horizontalHeader().setSectionHidden(col, not visible)

    def get_visible_columns(self):
        """Return list of visible column names."""
        header = self.horizontalHeader()
        return [ALL_COLUMNS[i] for i in range(len(ALL_COLUMNS))
                if not header.isSectionHidden(i)]

    def set_visible_columns(self, column_names):
        """Show only the named columns."""
        header = self.horizontalHeader()
        visible_set = set(column_names) if column_names else set(ALL_COLUMNS)
        for i, name in enumerate(ALL_COLUMNS):
            header.setSectionHidden(i, name not in visible_set)

    def jump_to_playlist_index(self, playlist_idx):
        """Select and scroll to a specific playlist index."""
        model = self.model()
        if model is None:
            return
        for row in range(model.rowCount()):
            idx = model.index(row, 0)
            if idx.data(Qt.UserRole) == playlist_idx:
                self.selectRow(row)
                self.scrollTo(idx)
                return
