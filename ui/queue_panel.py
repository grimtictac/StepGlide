"""
Queue panel — shows the play queue with reorder / remove / clear controls.
"""

from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import qtawesome as qta
from ui.theme import COLORS

TRACK_PATHS_MIME = 'application/x-musicplayer-track-paths'


class _QueueDropTreeWidget(QTreeWidget):
    """QTreeWidget that keeps internal-move reorder AND accepts external
    track-path drops from the track table."""

    external_paths_dropped = Signal(int, list)  # (insert_row, [path, ...])
    internal_move_requested = Signal(int, int)  # (source_row, dest_row)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAcceptDrops(True)
        self._drop_indicator_row = -1   # row to draw indicator before
        self._is_external = False       # is the current drag external?

    # ── Helpers ──────────────────────────────────────────

    def _insert_row_for_pos(self, pos):
        """Compute the insert-row index for a given pixel position."""
        item = self.itemAt(pos)
        if item is None:
            return self.topLevelItemCount()
        rect = self.visualItemRect(item)
        row = self.indexOfTopLevelItem(item)
        # Top half → insert before, bottom half → insert after
        if pos.y() < rect.center().y():
            return row
        return row + 1

    def _indicator_y(self, row):
        """Return the viewport Y coordinate for the indicator line."""
        if row < self.topLevelItemCount():
            rect = self.visualItemRect(self.topLevelItem(row))
            return rect.top()
        elif self.topLevelItemCount() > 0:
            rect = self.visualItemRect(
                self.topLevelItem(self.topLevelItemCount() - 1))
            return rect.bottom() + 1
        return 0

    # ── Drag events ──────────────────────────────────────

    def dragEnterEvent(self, event):
        self._is_external = event.mimeData().hasFormat(TRACK_PATHS_MIME)
        self._drop_indicator_row = -1
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self._drop_indicator_row = self._insert_row_for_pos(
            event.position().toPoint())
        self.viewport().update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop_indicator_row = -1
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        row = self._drop_indicator_row
        if row < 0:
            row = self.topLevelItemCount()
        self._drop_indicator_row = -1
        self.viewport().update()

        if self._is_external:
            # External drop from track table
            raw = bytes(event.mimeData().data(TRACK_PATHS_MIME)).decode('utf-8')
            paths = [p for p in raw.split('\n') if p]
            if paths:
                self.external_paths_dropped.emit(row, paths)
        else:
            # Internal reorder — find which row is being dragged
            selected = self.selectedItems()
            if selected:
                src_row = self.indexOfTopLevelItem(selected[0])
                if src_row >= 0 and src_row != row:
                    self.internal_move_requested.emit(src_row, row)
        event.acceptProposedAction()

    # ── Paint the drop indicator line ────────────────────

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_indicator_row < 0:
            return
        y = self._indicator_y(self._drop_indicator_row)
        painter = QPainter(self.viewport())
        pen = QPen(QColor(COLORS.get('cyan_bright', '#80f0ff')), 2)
        painter.setPen(pen)
        painter.drawLine(0, y, self.viewport().width(), y)
        painter.end()


class QueuePanel(QWidget):
    """Play queue panel with a tree-list and control buttons."""

    # Emitted when user double-clicks a queue item — sends playlist index
    play_from_queue = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = []       # list of playlist indices
        self._playlist = []    # reference to main playlist (set via set_playlist)
        self._path_to_idx = {} # path → playlist index (rebuilt by set_playlist)

        self._init_ui()

    # ── UI ───────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Header row
        header = QHBoxLayout()
        self._title_lbl = QLabel('Queue (0)')
        self._title_lbl.setStyleSheet(
            f'color: {COLORS["fg_dim"]}; font-weight: bold; font-size: 12px;')
        header.addWidget(self._title_lbl)
        header.addStretch()

        btn_clear = QPushButton('Clear')
        btn_clear.setFixedHeight(22)
        btn_clear.setToolTip('Clear the entire queue')
        btn_clear.clicked.connect(self.clear)
        header.addWidget(btn_clear)
        layout.addLayout(header)

        # Tree widget (Title, Genre columns)
        self._tree = _QueueDropTreeWidget()
        self._tree.setHeaderLabels(['Title', 'Genre'])
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_right_click)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.external_paths_dropped.connect(self._on_external_drop)
        self._tree.internal_move_requested.connect(self._on_internal_move)
        layout.addWidget(self._tree, stretch=1)

        # Button row: ▲ ▼ ⤒ ✕
        btn_row = QHBoxLayout()
        btn_row.setSpacing(2)

        btn_up = QPushButton()
        btn_up.setIcon(qta.icon('mdi6.arrow-up', color=COLORS['fg']))
        btn_up.setFixedSize(32, 28)
        btn_up.setIconSize(btn_up.size() * 0.55)
        btn_up.setToolTip('Move up')
        btn_up.clicked.connect(self._move_up)
        btn_row.addWidget(btn_up)

        btn_down = QPushButton()
        btn_down.setIcon(qta.icon('mdi6.arrow-down', color=COLORS['fg']))
        btn_down.setFixedSize(32, 28)
        btn_down.setIconSize(btn_down.size() * 0.55)
        btn_down.setToolTip('Move down')
        btn_down.clicked.connect(self._move_down)
        btn_row.addWidget(btn_down)

        btn_top = QPushButton()
        btn_top.setIcon(qta.icon('mdi6.arrow-collapse-up', color=COLORS['fg']))
        btn_top.setFixedSize(32, 28)
        btn_top.setIconSize(btn_top.size() * 0.55)
        btn_top.setToolTip('Move to top')
        btn_top.clicked.connect(self._move_to_top)
        btn_row.addWidget(btn_top)

        btn_row.addStretch()

        btn_remove = QPushButton()
        btn_remove.setIcon(qta.icon('mdi6.close', color=COLORS['red_text']))
        btn_remove.setFixedSize(32, 28)
        btn_remove.setIconSize(btn_remove.size() * 0.55)
        btn_remove.setToolTip('Remove selected')
        btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(btn_remove)

        layout.addLayout(btn_row)

    # ── Public API ───────────────────────────────────────

    def set_playlist(self, playlist):
        """Store a reference to the main playlist list (for title lookups)."""
        self._playlist = playlist
        # Rebuild path→index map for external drops
        self._path_to_idx = {e['path']: i for i, e in enumerate(playlist)}

    def get_queue(self):
        """Return a copy of the current queue (list of playlist indices)."""
        return list(self._queue)

    def add(self, playlist_idx):
        """Append a single track to the queue."""
        self._queue.append(playlist_idx)
        self._rebuild()

    def add_multiple(self, indices):
        """Append several tracks to the queue."""
        self._queue.extend(indices)
        self._rebuild()

    # ── External track drop ──────────────────────────────

    def _on_external_drop(self, row, paths):
        """Resolve dropped file paths to playlist indices and insert at row."""
        indices = [self._path_to_idx[p] for p in paths
                   if p in self._path_to_idx]
        if indices:
            for i, idx in enumerate(indices):
                self._queue.insert(row + i, idx)
            self._rebuild()

    def pop_next(self):
        """Remove and return the first queue item, or None."""
        if self._queue:
            idx = self._queue.pop(0)
            self._rebuild()
            return idx
        return None

    def clear(self):
        """Remove all items from the queue."""
        self._queue.clear()
        self._rebuild()

    def set_queue(self, indices):
        """Replace the entire queue with the given list."""
        self._queue = list(indices)
        self._rebuild()

    def queue_paths(self):
        """Return file paths for all queued tracks (for DB persistence)."""
        return [self._playlist[i]['path'] for i in self._queue
                if i < len(self._playlist)]

    # ── Rebuild display ──────────────────────────────────

    def _rebuild(self):
        self._tree.clear()
        for pl_idx in self._queue:
            if pl_idx < len(self._playlist):
                entry = self._playlist[pl_idx]
                title = entry.get('title', entry.get('basename', '?'))
                genre = entry.get('genre', '')
                item = QTreeWidgetItem([title[:50], genre])
                item.setData(0, Qt.UserRole, pl_idx)
                self._tree.addTopLevelItem(item)
        self._title_lbl.setText(f'Queue ({len(self._queue)})')

    # ── Selection helpers ────────────────────────────────

    def _selected_row(self):
        items = self._tree.selectedItems()
        if not items:
            return None
        return self._tree.indexOfTopLevelItem(items[0])

    def _select_row(self, row):
        if 0 <= row < self._tree.topLevelItemCount():
            self._tree.setCurrentItem(self._tree.topLevelItem(row))

    # ── Reorder / remove ─────────────────────────────────

    def _move_up(self):
        i = self._selected_row()
        if i is None or i == 0:
            return
        self._queue[i - 1], self._queue[i] = self._queue[i], self._queue[i - 1]
        self._rebuild()
        self._select_row(i - 1)

    def _move_down(self):
        i = self._selected_row()
        if i is None or i >= len(self._queue) - 1:
            return
        self._queue[i + 1], self._queue[i] = self._queue[i], self._queue[i + 1]
        self._rebuild()
        self._select_row(i + 1)

    def _move_to_top(self):
        i = self._selected_row()
        if i is None or i == 0:
            return
        item = self._queue.pop(i)
        self._queue.insert(0, item)
        self._rebuild()
        self._select_row(0)

    def _remove_selected(self):
        i = self._selected_row()
        if i is None:
            return
        self._queue.pop(i)
        self._rebuild()

    # ── Drag-drop sync ───────────────────────────────────

    def _on_internal_move(self, src_row, dest_row):
        """Move a queue entry from src_row to dest_row."""
        if src_row < 0 or src_row >= len(self._queue):
            return
        item = self._queue.pop(src_row)
        # After popping, adjust dest if it was below the source
        if dest_row > src_row:
            dest_row -= 1
        dest_row = max(0, min(dest_row, len(self._queue)))
        self._queue.insert(dest_row, item)
        self._rebuild()
        self._select_row(dest_row)

    # ── Context menu ─────────────────────────────────────

    def _on_right_click(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            menu = QMenu(self)
            menu.addAction('Clear Queue', self.clear)
            menu.exec(self._tree.mapToGlobal(pos))
            return
        row = self._tree.indexOfTopLevelItem(item)
        self._tree.setCurrentItem(item)

        menu = QMenu(self)
        menu.addAction('Remove', lambda: self._remove_at(row))
        menu.addAction('Move to Top', lambda: self._move_to_top_at(row))
        menu.addSeparator()
        menu.addAction('Clear Queue', self.clear)
        menu.exec(self._tree.mapToGlobal(pos))

    def _remove_at(self, row):
        if 0 <= row < len(self._queue):
            self._queue.pop(row)
            self._rebuild()

    def _move_to_top_at(self, row):
        if 0 < row < len(self._queue):
            item = self._queue.pop(row)
            self._queue.insert(0, item)
            self._rebuild()
            self._select_row(0)

    # ── Double-click to play ─────────────────────────────

    def _on_double_click(self, item, _col):
        row = self._tree.indexOfTopLevelItem(item)
        if row < 0 or row >= len(self._queue):
            return
        pl_idx = self._queue.pop(row)
        self._rebuild()
        self.play_from_queue.emit(pl_idx)
