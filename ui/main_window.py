"""
Main window — QMainWindow shell with layout, splitters, and panel wiring.
"""

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QSplitter, QStatusBar,
    QVBoxLayout, QWidget,
)

from ui.theme import COLORS, DARK_THEME
from ui.track_table import ALL_COLUMNS, TrackFilterProxy, TrackTableModel, TrackTableView


class MainWindow(QMainWindow):
    """Top-level window for the music player."""

    def __init__(self, db, config, parent=None):
        super().__init__(parent)
        self.db = db
        self.config = config

        self.setWindowTitle('Python Music Player')
        self.resize(1920, 1080)
        self.setMinimumSize(900, 500)
        self.setStyleSheet(DARK_THEME)

        # ── Data ─────────────────────────────────────────
        self.playlist = []
        self.genres = set()
        self.all_voters = set()
        self._path_set = set()
        self._path_to_idx = {}
        self.current_index = None
        self.is_playing = False
        self.is_paused = False

        # ── Build the UI ─────────────────────────────────
        self._build_ui()
        self._build_menu_bar()
        self._build_status_bar()

        # ── Load data ────────────────────────────────────
        self._load_tracks()

    def _build_ui(self):
        """Construct the main layout with splitters."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Main horizontal splitter: [sidebar | center | right panel] ──
        self._main_splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(self._main_splitter)

        # Left sidebar placeholder
        self._sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_label = QLabel('Genres / Playlists')
        sidebar_label.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-weight: bold;')
        sidebar_layout.addWidget(sidebar_label)
        sidebar_layout.addStretch()

        # Center: track table
        self._center = QWidget()
        center_layout = QVBoxLayout(self._center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        # Now-playing bar
        now_playing_bar = QWidget()
        now_playing_bar.setStyleSheet(f'background-color: {COLORS["bg_dark"]};')
        np_layout = QHBoxLayout(now_playing_bar)
        np_layout.setContentsMargins(12, 6, 12, 6)
        self._lbl_now_playing = QLabel('Not Playing')
        self._lbl_now_playing.setStyleSheet('font-size: 18px; font-weight: bold;')
        np_layout.addWidget(self._lbl_now_playing)
        np_layout.addStretch()
        center_layout.addWidget(now_playing_bar)

        # Track table
        self._track_model = TrackTableModel(self)
        self._filter_proxy = TrackFilterProxy(self)
        self._filter_proxy.setSourceModel(self._track_model)
        self._track_table = TrackTableView(self)
        self._track_table.setModel(self._filter_proxy)

        # Connect signals
        self._track_table.play_requested.connect(self._on_play_requested)
        self._track_table.selection_changed.connect(self._on_selection_changed)
        self._track_table.context_menu_requested.connect(self._on_context_menu)

        center_layout.addWidget(self._track_table, stretch=1)

        # Right panel: queue + play log (vertical splitter)
        self._right_panel = QWidget()
        right_layout = QVBoxLayout(self._right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_label = QLabel('Queue / Play Log')
        right_label.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-weight: bold;')
        right_layout.addWidget(right_label)
        right_layout.addStretch()

        # Add to main splitter
        self._main_splitter.addWidget(self._sidebar)
        self._main_splitter.addWidget(self._center)
        self._main_splitter.addWidget(self._right_panel)
        self._main_splitter.setSizes([170, 900, 240])
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setStretchFactor(2, 0)

    def _build_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu('&File')
        file_menu.addAction(QAction('Add &Files...', self))
        file_menu.addAction(QAction('Add F&older...', self))
        file_menu.addSeparator()
        quit_action = QAction('&Quit', self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menu_bar.addMenu('&View')
        view_menu.addAction(QAction('Toggle &Sidebar', self))
        view_menu.addAction(QAction('Toggle &Right Panel', self))

    def _build_status_bar(self):
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._track_count_lbl = QLabel('0 tracks')
        self._status_bar.addWidget(self._track_count_lbl)
        self._perf_lbl = QLabel('')
        self._perf_lbl.setStyleSheet(f'color: {COLORS["fg_very_dim"]};')
        self._status_bar.addPermanentWidget(self._perf_lbl)

    # ── Data loading ─────────────────────────────────────

    def _load_tracks(self):
        """Load all tracks from DB and populate the table model."""
        tracks, voters, genres = self.db.load_all_tracks()
        self.all_voters = voters
        self.genres = genres

        # Annotate each entry with its playlist index and absolute path
        abs_fn = lambda p: os.path.join(self.config.library_root, p) if self.config.library_root and not os.path.isabs(p) else p
        self.playlist = []
        self._path_set = set()
        self._path_to_idx = {}
        for i, entry in enumerate(tracks):
            entry['_playlist_idx'] = i
            entry['_abs_path'] = abs_fn(entry['path'])
            self.playlist.append(entry)
            self._path_set.add(entry['path'])
            self._path_to_idx[entry['path']] = i

        self._track_model.set_tracks(self.playlist)

        # Apply visible columns from config
        if self.config.visible_columns:
            self._track_table.set_visible_columns(self.config.visible_columns)

        self._update_track_count()
        self._lbl_now_playing.setText(f'{len(self.playlist)} tracks loaded')

    def _update_track_count(self):
        total = len(self.playlist)
        shown = self._filter_proxy.rowCount()
        if shown == total:
            self._track_count_lbl.setText(f'{total} tracks')
        else:
            self._track_count_lbl.setText(f'{shown} of {total} tracks')

    # ── Slot handlers ────────────────────────────────────

    def _on_play_requested(self, playlist_idx):
        """Handle double-click on a track."""
        # TODO: wire to VLC playback
        entry = self.playlist[playlist_idx]
        self._lbl_now_playing.setText(entry.get('title', entry.get('basename', '')))
        self.current_index = playlist_idx
        self._track_model.set_now_playing(playlist_idx)

    def _on_selection_changed(self, indices):
        """Handle track selection change."""
        pass  # TODO: update preview/details

    def _on_context_menu(self, playlist_idx, pos):
        """Handle right-click on a track."""
        # TODO: full context menu
        pass

    # ── Cleanup ──────────────────────────────────────────

    def closeEvent(self, event):
        # Save config
        self.config.visible_columns = self._track_table.get_visible_columns()
        self.config.save()
        super().closeEvent(event)
