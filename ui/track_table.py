"""
Track table — QTableView + QAbstractTableModel.

The model receives a pre-filtered, pre-sorted list of track dicts from
MainWindow._apply_filters().  No QSortFilterProxyModel — all filtering
and sorting happens in pure Python to avoid C++→Python per-row overhead.
"""

from PySide6.QtCore import (
    QAbstractTableModel, QByteArray, QMimeData, QModelIndex,
    QPoint, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QMenu, QTableView, QToolTip,
)

from core.formatters import build_track_tooltip, format_duration, format_ts
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

    MIME_TYPE = 'application/x-musicplayer-track-paths'

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks = []        # reference to the backend's playlist list
        self._now_playing_idx = None  # playlist index of currently playing track

    def set_tracks(self, tracks):
        """Replace the backing data. tracks is a list of entry dicts."""
        self.beginResetModel()
        self._tracks = tracks
        self.endResetModel()

    # ── Drag support (drag OUT only — no drop / reorder) ─

    def flags(self, index):
        default = super().flags(index)
        if index.isValid():
            return default | Qt.ItemIsDragEnabled
        return default

    def mimeTypes(self):
        return [self.MIME_TYPE]

    def mimeData(self, indexes):
        """Encode the relative file paths of dragged rows."""
        rows = sorted({idx.row() for idx in indexes if idx.isValid()})
        paths = []
        for r in rows:
            if r < len(self._tracks):
                paths.append(self._tracks[r].get('path', ''))
        mime = QMimeData()
        mime.setData(self.MIME_TYPE,
                     QByteArray('\n'.join(paths).encode('utf-8')))
        return mime

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

        elif role == Qt.ToolTipRole:
            return build_track_tooltip(entry)

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



# ── Table View widget ────────────────────────────────────

class TrackTableView(QTableView):
    """Pre-configured QTableView for the track listing."""

    # Signals for main window to connect to
    play_requested = Signal(int)          # playlist_idx — double-click
    context_menu_requested = Signal(int, object)  # playlist_idx, QPoint
    selection_changed = Signal(list)       # list of playlist indices
    sort_requested = Signal(int, object)   # column_index, Qt.SortOrder

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.ExtendedSelection)
        self.setShowGrid(False)
        self.setSortingEnabled(False)  # we handle sorting ourselves
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(34)
        self.horizontalHeader().setStretchLastSection(True)

        # Hover tooltip with 500ms delay
        self.setMouseTracking(True)
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.setInterval(500)
        self._tooltip_timer.timeout.connect(self._show_hover_tooltip)
        self._hover_index = None
        self._hover_global_pos = QPoint()

        # Sort state tracked here for the header indicator
        self._sort_column = -1
        self._sort_order = Qt.AscendingOrder

        # Connect header clicks to our own sort handler
        self.horizontalHeader().sectionClicked.connect(self._on_header_clicked)

        # Drag-and-drop: drag OUT of table only (into sidebar playlists)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.horizontalHeader().setSectionsMovable(True)
        self.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.horizontalHeader().customContextMenuRequested.connect(
            self._on_header_context_menu)
        self.horizontalHeader().sectionHandleDoubleClicked.connect(
            lambda _: self._rebalance_columns())

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

        # Minimum column widths for default columns (pixels)
        self._min_col_widths = {
            0: 120,   # Title — needs room for text
            3: 60,    # Genre
            4: 45,    # Length
            5: 40,    # Rating
            6: 80,    # Comment
            7: 80,    # Tags
            8: 55,    # Liked By
            10: 40,   # Plays
            12: 65,   # Last Played
        }
        self._user_resizing_column = False
        self._last_viewport_width = 0

    def setModel(self, model):
        super().setModel(model)
        # Apply default column widths
        for col, width in self._default_widths.items():
            self.setColumnWidth(col, width)
        # Apply default column visibility
        self.set_visible_columns(list(DEFAULT_VISIBLE_COLUMNS))
        # Fit default columns into the viewport width
        self._rebalance_columns()
        # Connect selection
        sel_model = self.selectionModel()
        if sel_model:
            sel_model.selectionChanged.connect(self._on_selection_changed)

    # ── Sort handling ─────────────────────────────────────

    def _on_header_clicked(self, logical_index):
        """User clicked a column header — toggle sort and emit signal."""
        if logical_index == self._sort_column:
            # Same column — flip direction
            self._sort_order = (Qt.DescendingOrder
                                if self._sort_order == Qt.AscendingOrder
                                else Qt.AscendingOrder)
        else:
            self._sort_column = logical_index
            self._sort_order = Qt.AscendingOrder
        # Update the visual indicator
        header = self.horizontalHeader()
        header.setSortIndicatorShown(True)
        header.setSortIndicator(self._sort_column, self._sort_order)
        # Let MainWindow do the actual sorting
        self.sort_requested.emit(self._sort_column, self._sort_order)

    # ── Column rebalancing ────────────────────────────────

    def _rebalance_columns(self):
        """Proportionally shrink/grow visible default columns to fill the viewport.

        Non-default columns (Artist, Album, Path, etc.) are left at their
        original widths — they are intentionally wide and the user accepts
        horizontal scrolling when they enable them.
        """
        header = self.horizontalHeader()
        available = self.viewport().width()
        if available <= 0:
            return

        default_set = set()
        for i, name in enumerate(ALL_COLUMNS):
            if name in DEFAULT_VISIBLE_COLUMNS:
                default_set.add(i)

        # Partition visible columns into default vs non-default
        vis_default = []   # (col_index, current_width)
        non_default_total = 0
        for i in range(len(ALL_COLUMNS)):
            if header.isSectionHidden(i):
                continue
            if i in default_set:
                vis_default.append((i, self.columnWidth(i)))
            else:
                non_default_total += self.columnWidth(i)

        if not vis_default:
            return

        # Space remaining for default columns
        budget = available - non_default_total
        if budget < 50:
            return   # viewport too small to bother

        current_total = sum(w for _, w in vis_default)
        if current_total <= 0:
            return

        # Scale proportionally, clamping to minimums
        scale = budget / current_total
        new_widths = {}
        used = 0
        for col, w in vis_default:
            min_w = self._min_col_widths.get(col, 40)
            new_w = max(min_w, int(w * scale))
            new_widths[col] = new_w
            used += new_w

        # Give any leftover pixels to Title (col 0) if visible
        leftover = budget - used
        if leftover > 0 and 0 in new_widths:
            new_widths[0] += leftover
        elif leftover > 0 and vis_default:
            new_widths[vis_default[0][0]] += leftover

        for col, w in new_widths.items():
            self.setColumnWidth(col, w)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        vp_width = self.viewport().width()
        if vp_width != self._last_viewport_width:
            self._last_viewport_width = vp_width
            self._rebalance_columns()

    # ── Hover tooltip (500 ms delay) ─────────────────────

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        index = self.indexAt(event.pos())
        if index.isValid() and index.row() != (self._hover_index.row() if self._hover_index and self._hover_index.isValid() else -1):
            self._hover_index = index
            self._hover_global_pos = event.globalPosition().toPoint()
            self._tooltip_timer.start()
        elif not index.isValid():
            self._tooltip_timer.stop()
            self._hover_index = None

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._tooltip_timer.stop()
        self._hover_index = None

    def _show_hover_tooltip(self):
        if self._hover_index and self._hover_index.isValid():
            tip = self._hover_index.data(Qt.ToolTipRole)
            if tip:
                QToolTip.showText(self._hover_global_pos, tip, self)

    def mouseDoubleClickEvent(self, event):
        self._tooltip_timer.stop()
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
        self._rebalance_columns()

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
        """Select and scroll to a specific playlist index.
        Returns True if the track was found in the visible rows."""
        model = self.model()
        if model is None:
            return False
        for row in range(model.rowCount()):
            idx = model.index(row, 0)
            if idx.data(Qt.UserRole) == playlist_idx:
                self.selectRow(row)
                self.scrollTo(idx)
                return True
        return False
