"""
Queue panel — shows the play queue with reorder / remove / clear controls.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ui.theme import COLORS


class QueuePanel(QWidget):
    """Play queue panel with a tree-list and control buttons."""

    # Emitted when user double-clicks a queue item — sends playlist index
    play_from_queue = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = []       # list of playlist indices
        self._playlist = []    # reference to main playlist (set via set_playlist)

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
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(['Title', 'Genre'])
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setDragDropMode(QAbstractItemView.InternalMove)
        self._tree.setDefaultDropAction(Qt.MoveAction)
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_right_click)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        # After internal drag-drop, sync data model
        self._tree.model().rowsMoved.connect(self._sync_from_tree)
        layout.addWidget(self._tree, stretch=1)

        # Button row: ▲ ▼ ⤒ ✕
        btn_row = QHBoxLayout()
        btn_row.setSpacing(2)

        btn_up = QPushButton('▲')
        btn_up.setFixedSize(28, 24)
        btn_up.setToolTip('Move up')
        btn_up.clicked.connect(self._move_up)
        btn_row.addWidget(btn_up)

        btn_down = QPushButton('▼')
        btn_down.setFixedSize(28, 24)
        btn_down.setToolTip('Move down')
        btn_down.clicked.connect(self._move_down)
        btn_row.addWidget(btn_down)

        btn_top = QPushButton('⤒')
        btn_top.setFixedSize(28, 24)
        btn_top.setToolTip('Move to top')
        btn_top.clicked.connect(self._move_to_top)
        btn_row.addWidget(btn_top)

        btn_row.addStretch()

        btn_remove = QPushButton('✕')
        btn_remove.setFixedSize(28, 24)
        btn_remove.setToolTip('Remove selected')
        btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(btn_remove)

        layout.addLayout(btn_row)

    # ── Public API ───────────────────────────────────────

    def set_playlist(self, playlist):
        """Store a reference to the main playlist list (for title lookups)."""
        self._playlist = playlist

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

    def _sync_from_tree(self):
        """After an internal drag-drop, rebuild self._queue from tree order."""
        new_queue = []
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            pl_idx = item.data(0, Qt.UserRole)
            if pl_idx is not None:
                new_queue.append(pl_idx)
        self._queue = new_queue
        self._title_lbl.setText(f'Queue ({len(self._queue)})')

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
