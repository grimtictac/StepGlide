"""
Main window — QMainWindow shell with layout, splitters, and panel wiring.
"""

import os
import time

import vlc

import qtawesome as qta
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
    QMenu, QMessageBox, QProgressBar, QPushButton, QSplitter, QStatusBar,
    QVBoxLayout, QWidget,
)

from ui.theme import COLORS, DARK_THEME
from ui.search_bar import SearchFilterBar
from ui.debug_panel import DebugPanel
from ui.eq_dialog import EqualizerDialog, apply_eq_for_track
from ui.misc_dialogs import AuditLogDialog, RandomQueueDialog
from ui.play_log_panel import PlayLogPanel
from ui.preview_dialog import PreviewDialog
from ui.queue_panel import QueuePanel
from ui.settings_dialog import SettingsDialog
from ui.sidebar import SidebarWidget
from ui.tag_bar import TagBar
from ui.track_table import ALL_COLUMNS, DEFAULT_VISIBLE_COLUMNS, TrackFilterProxy, TrackTableModel, TrackTableView
from ui.transport_bar import TransportBar, VolumePanel, VolumeStrip

from core.audio_devices import list_audio_devices
from core.waveform import WaveformWorker, deserialise_waveform, serialise_waveform


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
        self._last_action = 'stopped'
        self._play_started_at = 0.0
        self._consecutive_skips = 0

        # Play queue: list of playlist indices
        self._play_queue = []
        self._selected_indices = []
        self._lite_mode = False

        # Playback speed
        self._speed = 1.0
        self._auto_reset_speed = True

        # Volume / mute
        self._muted = False
        self._pre_mute_vol = 80

        # Preview
        self._preview_dialog = None

        # Waveform worker
        self._waveform_worker = None

        # ── VLC ──────────────────────────────────────────
        self.vlc_instance = vlc.Instance()
        self.vlc_player = self.vlc_instance.media_list_player_new()
        self.vlc_media_list = self.vlc_instance.media_list_new()

        # ── Build the UI ─────────────────────────────────
        self._build_ui()
        self._build_menu_bar()
        self._build_status_bar()

        # ── Poll timer (replaces Tk's self.after(500, ...)) ──
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        # ── Load data ────────────────────────────────────
        self._load_tracks()

        # ── Keyboard shortcuts ───────────────────────────
        self._bind_shortcuts()

        # ── Apply configured main audio output device ────
        self._apply_main_audio_device()

        # ── Accept drag-and-drop from file manager ───────
        self.setAcceptDrops(True)

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

        # Left sidebar: genre list + playlists
        self._sidebar = SidebarWidget()
        self._connect_sidebar()

        # Center: now-playing + transport + track table
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
        self._lbl_genre = QLabel('')
        self._lbl_genre.setStyleSheet(
            f'color: {COLORS["cyan"]}; font-size: 11px; padding: 2px 6px;')
        np_layout.addWidget(self._lbl_genre)
        np_layout.addStretch()

        # Rating display + vote buttons + voter selector
        self._lbl_rating = QLabel('')
        self._lbl_rating.setStyleSheet(
            'font-size: 13px; font-weight: bold; padding: 0 6px;')
        np_layout.addWidget(self._lbl_rating)

        btn_like = QPushButton()
        btn_like.setIcon(qta.icon('mdi6.thumb-up', color=COLORS['green_text']))
        btn_like.setFixedSize(40, 32)
        btn_like.setIconSize(btn_like.size() * 0.55)
        btn_like.setToolTip('Like this track')
        btn_like.setStyleSheet(
            'QPushButton { background-color: #1a3a1a;'
            '  border: 1px solid #27ae60; border-radius: 4px; }'
            'QPushButton:hover { background-color: #27ae60; }')
        btn_like.clicked.connect(lambda: self._vote(+1))
        np_layout.addWidget(btn_like)

        btn_dislike = QPushButton()
        btn_dislike.setIcon(qta.icon('mdi6.thumb-down', color=COLORS['red_text']))
        btn_dislike.setFixedSize(40, 32)
        btn_dislike.setIconSize(btn_dislike.size() * 0.55)
        btn_dislike.setToolTip('Dislike this track')
        btn_dislike.setStyleSheet(
            'QPushButton { background-color: #3a1a1a;'
            '  border: 1px solid #c0392b; border-radius: 4px; }'
            'QPushButton:hover { background-color: #c0392b; }')
        btn_dislike.clicked.connect(lambda: self._vote(-1))
        np_layout.addWidget(btn_dislike)

        self._voter_combo = QComboBox()
        self._voter_combo.setEditable(True)
        self._voter_combo.setFixedWidth(110)
        self._voter_combo.setToolTip('Voter name')
        self._voter_combo.lineEdit().setPlaceholderText('anonymous')
        np_layout.addWidget(self._voter_combo)

        # EQ button
        self._btn_eq = QPushButton()
        self._icon_eq_off = qta.icon('mdi6.equalizer', color=COLORS['fg'])
        self._icon_eq_on = qta.icon('mdi6.equalizer', color=COLORS['green_text'])
        self._btn_eq.setIcon(self._icon_eq_off)
        self._btn_eq.setFixedSize(40, 32)
        self._btn_eq.setIconSize(self._btn_eq.size() * 0.55)
        self._btn_eq.setToolTip('Equalizer')
        self._btn_eq.clicked.connect(self._show_eq_dialog)
        np_layout.addWidget(self._btn_eq)

        # Settings button
        btn_settings = QPushButton()
        btn_settings.setIcon(qta.icon('mdi6.cog', color=COLORS['fg']))
        btn_settings.setFixedSize(40, 32)
        btn_settings.setIconSize(btn_settings.size() * 0.55)
        btn_settings.setToolTip('Settings')
        btn_settings.clicked.connect(self._open_settings)
        np_layout.addWidget(btn_settings)

        # Jump-to-playing button
        self.btn_jump = QPushButton()
        self.btn_jump.setIcon(qta.icon('mdi6.crosshairs-gps', color=COLORS['fg']))
        self.btn_jump.setFixedSize(40, 32)
        self.btn_jump.setIconSize(self.btn_jump.size() * 0.55)
        self.btn_jump.setToolTip('Jump to now playing track')
        self.btn_jump.clicked.connect(self._jump_to_playing)
        np_layout.addWidget(self.btn_jump)

        center_layout.addWidget(now_playing_bar)

        # Transport bar
        self._transport = TransportBar(self)
        self._transport.setStyleSheet(f'background-color: {COLORS["bg_dark"]};')
        self._connect_transport()
        center_layout.addWidget(self._transport)

        # Search / filter bar
        self._search_bar = SearchFilterBar(self)
        self._connect_search_bar()
        center_layout.addWidget(self._search_bar)

        # Tag filter bar
        self._tag_bar = TagBar(self)
        self._tag_bar.tags_changed.connect(self._on_tags_changed)
        center_layout.addWidget(self._tag_bar)

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

        # Debug panel (hidden by default, toggled with F10)
        self._debug_panel = DebugPanel(self)
        center_layout.addWidget(self._debug_panel)

        # Right panel: queue + play log in vertical splitter
        self._right_splitter = QSplitter(Qt.Vertical)

        self._queue_panel = QueuePanel()
        self._queue_panel.play_from_queue.connect(self._on_play_from_queue)
        self._right_splitter.addWidget(self._queue_panel)

        self._play_log = PlayLogPanel()
        self._play_log.play_requested.connect(self._on_play_from_queue)
        self._play_log.add_to_queue_requested.connect(
            lambda idx: self._queue_panel.add(idx))
        self._play_log.jump_to_track.connect(self._jump_to_track_index)
        self._right_splitter.addWidget(self._play_log)

        self._right_splitter.setSizes([350, 250])

        # Volume strip — far right edge
        self._volume_strip = VolumeStrip(self)
        self._volume_strip.volume_changed.connect(self._on_volume_changed)
        self._volume_strip.mute_toggled.connect(self._toggle_mute)
        self._volume_strip.debug_log.connect(self._debug_log)
        self._volume_strip.settings_requested.connect(
            lambda: self._open_settings(tab='Volume'))
        self._volume_strip.apply_config(self.config)

        self._volume_panel = VolumePanel(self._volume_strip, self)
        self._volume_panel.pull_fader.debug_log.connect(self._debug_log)
        self._volume_panel.pull_fader.apply_config(self.config)

        vol_container = QWidget()
        vol_container.setStyleSheet(
            f'background-color: {COLORS["bg_dark"]}; '
            f'border-left: 1px solid {COLORS["border"]};')
        vcl = QVBoxLayout(vol_container)
        vcl.setContentsMargins(0, 0, 0, 0)
        vcl.setSpacing(0)
        vcl.addWidget(self._volume_panel, stretch=1)

        # Add to main splitter
        self._main_splitter.addWidget(self._sidebar)
        self._main_splitter.addWidget(self._center)
        self._main_splitter.addWidget(self._right_splitter)
        self._main_splitter.addWidget(vol_container)
        self._main_splitter.setSizes([120, 850, 280, 260])
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setStretchFactor(2, 0)
        self._main_splitter.setStretchFactor(3, 0)

    def _build_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu('&File')

        add_files_action = QAction('Add &Files...', self)
        add_files_action.triggered.connect(self._add_files)
        file_menu.addAction(add_files_action)

        add_folder_action = QAction('Add F&older...', self)
        add_folder_action.triggered.connect(self._add_folder)
        file_menu.addAction(add_folder_action)

        file_menu.addSeparator()
        quit_action = QAction('&Quit', self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menu_bar.addMenu('&View')
        sidebar_action = QAction('Toggle &Sidebar', self)
        sidebar_action.setShortcut('F1')
        sidebar_action.triggered.connect(self._toggle_sidebar)
        view_menu.addAction(sidebar_action)

        right_action = QAction('Toggle &Right Panel', self)
        right_action.setShortcut('F2')
        right_action.triggered.connect(self._toggle_right_panel)
        view_menu.addAction(right_action)

        fullscreen_action = QAction('Toggle &Fullscreen', self)
        fullscreen_action.setShortcut('F11')
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen_action)

        view_menu.addSeparator()

        tagbar_action = QAction('Toggle &Tag Bar', self)
        tagbar_action.setShortcut('F3')
        tagbar_action.triggered.connect(self._toggle_tag_bar)
        view_menu.addAction(tagbar_action)

        searchbar_action = QAction('Toggle S&earch Bar', self)
        searchbar_action.setShortcut('F4')
        searchbar_action.triggered.connect(self._toggle_search_bar)
        view_menu.addAction(searchbar_action)

        view_menu.addSeparator()

        lite_action = QAction('&Lite Mode', self)
        lite_action.setShortcut('Ctrl+L')
        lite_action.triggered.connect(self._toggle_lite_mode)
        view_menu.addAction(lite_action)

        view_menu.addSeparator()

        debug_action = QAction('&Debug Log', self)
        debug_action.setShortcut('F10')
        debug_action.triggered.connect(self._toggle_debug_panel)
        view_menu.addAction(debug_action)

        view_menu.addSeparator()

        # Track List submenu
        track_list_menu = view_menu.addMenu('Track &List')

        default_layout_action = QAction('&Default Layout', self)
        default_layout_action.triggered.connect(self._reset_track_list_default)
        track_list_menu.addAction(default_layout_action)

        show_all_cols_action = QAction('Show &All Columns', self)
        show_all_cols_action.triggered.connect(self._show_all_columns)
        track_list_menu.addAction(show_all_cols_action)

        # ── Audio menu ──────────────────────────────────
        audio_menu = menu_bar.addMenu('&Audio')
        self._audio_main_sub = audio_menu.addMenu('Main &Output')
        self._audio_preview_sub = audio_menu.addMenu('&Preview Output')
        self._refresh_audio_device_menus()

        tools_menu = menu_bar.addMenu('&Tools')

        rq_action = QAction('Random &Queue Generator...', self)
        rq_action.triggered.connect(self._random_queue_dialog)
        tools_menu.addAction(rq_action)

        tools_menu.addSeparator()

        audit_action = QAction('&Audit Log...', self)
        audit_action.triggered.connect(self._show_audit_log)
        tools_menu.addAction(audit_action)

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
        self._debug_log('INFO', f'Loaded {len(tracks)} tracks from database')
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

        # Populate search bar dropdowns
        self._search_bar.set_voters(self.all_voters)
        self._refresh_voter_combo()
        if hasattr(self.config, 'length_filter_durations') and self.config.length_filter_durations:
            opts = [label for label, lo, hi in self.config.length_filter_durations]
            self._search_bar.set_length_options(opts)

        # Populate sidebar
        self._sidebar.set_genre_data(self.genres, self.config.genre_groups)
        self._sidebar.set_playlist_data(self.config.playlists)

        # Populate tag bar
        if self.config.all_tags:
            self._tag_bar.set_tags(self.config.all_tags, self.config.tag_rows)

        # Set up queue panel and restore persisted queue
        self._queue_panel.set_playlist(self.playlist)
        saved_paths = self.db.load_queue()
        if saved_paths:
            restored = [self._path_to_idx[p] for p in saved_paths
                        if p in self._path_to_idx]
            if restored:
                self._queue_panel.set_queue(restored)

        # Load play log
        self._play_log.set_path_map(self._path_to_idx)
        self._play_log.set_db(self.db)
        self._play_log.load(self.db)

    def _update_track_count(self):
        total = len(self.playlist)
        shown = self._filter_proxy.rowCount()
        if shown == total:
            self._track_count_lbl.setText(f'{total} tracks')
        else:
            self._track_count_lbl.setText(f'{shown} of {total} tracks')

    # ── Add files / folders ──────────────────────────────

    def _rel_path(self, abs_path):
        """Convert an absolute path to relative (using library_root)."""
        if not self.config.library_root:
            return abs_path
        return os.path.relpath(abs_path, self.config.library_root)

    def _abs_path(self, rel_path):
        """Convert a relative path back to absolute."""
        if not self.config.library_root:
            return rel_path
        if os.path.isabs(rel_path):
            return rel_path
        return os.path.join(self.config.library_root, rel_path)

    def _add_path(self, abs_path):
        """Add a single track by absolute path. Returns True if new."""
        rel = self._rel_path(abs_path)
        if rel in self._path_set:
            return False

        title = os.path.basename(abs_path)
        genre, artist, album, comment, length = 'Unknown', '', '', '', None

        try:
            from mutagen import File as MutagenFile
            tags = MutagenFile(abs_path, easy=True)
            if tags is not None:
                title = tags.get('title', [title])[0]
                genre = tags.get('genre', [genre])[0]
                artist = tags.get('artist', [''])[0] or ''
                album = tags.get('album', [''])[0] or ''
                comment = str(tags.get('comment', [''])[0] or '')
            audio = MutagenFile(abs_path)
            if audio is not None and audio.info is not None:
                length = audio.info.length
        except Exception:
            pass

        entry = {
            'path': rel, 'title': title, 'basename': os.path.basename(abs_path),
            'artist': artist, 'album': album, 'genre': genre, 'comment': comment,
            'length': length, 'tags': [], 'rating': 0,
            'liked_by': set(), 'disliked_by': set(),
        }

        idx = len(self.playlist)
        entry['_playlist_idx'] = idx
        entry['_abs_path'] = abs_path
        self.playlist.append(entry)
        self._path_set.add(rel)
        self._path_to_idx[rel] = idx
        self.genres.add(genre)

        stats = self.db.ensure_track(rel, title, genre, comment, length, artist, album)
        entry['play_count'] = stats[0]
        entry['first_played'] = stats[1]
        entry['last_played'] = stats[2]
        entry['file_created'] = stats[3]
        if stats[4] is not None:
            entry['length'] = stats[4]
        return True

    def _add_files(self):
        """File > Add Files... dialog."""
        files, _ = QFileDialog.getOpenFileNames(
            self, 'Select audio files', '',
            'Audio Files (*.mp3 *.wav *.ogg *.flac);;All Files (*)')
        if not files:
            return
        for f in files:
            self._add_path(f)
        self._track_model.set_tracks(self.playlist)
        self._update_track_count()
        self._lbl_now_playing.setText(f'{len(files)} file(s) added')

    def _add_folder(self):
        """File > Add Folder... dialog."""
        folder = QFileDialog.getExistingDirectory(self, 'Select folder')
        if not folder:
            return
        exts = ('.mp3', '.wav', '.ogg', '.flac')
        self._lbl_now_playing.setText('Scanning folder\u2026')

        audio_files = []
        for root, _, filenames in os.walk(folder):
            for name in filenames:
                if name.lower().endswith(exts):
                    audio_files.append(os.path.join(root, name))

        total = len(audio_files)
        if total == 0:
            QMessageBox.information(self, 'No files',
                                    'No supported audio files found in folder.')
            self._lbl_now_playing.setText('Not Playing')
            return

        # Show progress in the status bar
        progress = QProgressBar()
        progress.setRange(0, total)
        progress.setFixedWidth(200)
        self._status_bar.addWidget(progress)

        added = 0
        from PySide6.QtWidgets import QApplication
        for i, path in enumerate(audio_files, 1):
            if self._add_path(path):
                added += 1
            progress.setValue(i)
            if i % 25 == 0 or i == total:
                QApplication.processEvents()

        self._status_bar.removeWidget(progress)
        progress.deleteLater()

        self._track_model.set_tracks(self.playlist)
        self._update_track_count()
        self._lbl_now_playing.setText(f'Added {added} tracks')

    # ── Connect transport bar signals ───────────────────

    def _connect_transport(self):
        t = self._transport
        t.play_pause_clicked.connect(self._play_pause)
        t.stop_clicked.connect(self._stop)
        t.next_clicked.connect(self._next_track)
        t.prev_clicked.connect(self._prev_track)
        t.scrub_released.connect(self._on_scrub_released)
        t.speed_up_clicked.connect(self._speed_up)
        t.speed_down_clicked.connect(self._speed_down)
        t.speed_reset_clicked.connect(self._speed_reset)
        t.auto_reset_speed_changed.connect(self._on_auto_reset_speed)

    # ── Connect search / filter bar ─────────────────────

    def _connect_search_bar(self):
        sb = self._search_bar
        sb.search_changed.connect(self._on_search_changed)
        sb.rating_changed.connect(self._on_rating_filter)
        sb.liked_by_changed.connect(self._on_liked_by_filter)
        sb.first_played_changed.connect(
            lambda v: self._on_date_filter('first_played', v))
        sb.last_played_changed.connect(
            lambda v: self._on_date_filter('last_played', v))
        sb.file_created_changed.connect(
            lambda v: self._on_date_filter('file_created', v))
        sb.length_changed.connect(self._on_length_filter)
        sb.filters_reset.connect(self._on_filters_reset)

    # ── Connect sidebar ─────────────────────────────────

    def _connect_sidebar(self):
        sb = self._sidebar
        sb.genre_selected.connect(self._on_genre_selected)
        sb.playlist_selected.connect(self._on_playlist_selected)
        sb.playlist_changed.connect(self._on_playlist_changed)

    def _on_search_changed(self, tokens):
        self._filter_proxy.set_search_tokens(tokens)
        self._update_track_count()

    def _on_rating_filter(self, threshold):
        self._filter_proxy.set_rating_filter(threshold)
        self._update_track_count()

    def _on_liked_by_filter(self, voter):
        self._filter_proxy.set_liked_by_filter(voter)
        self._update_track_count()

    def _on_date_filter(self, which, value):
        self._filter_proxy.set_date_filter(which, value)
        self._update_track_count()

    def _on_length_filter(self, label, lo, hi):
        if label == 'All':
            self._filter_proxy.set_length_filter('All', None, None)
        else:
            # Look up (lo, hi) from config by label
            for cfg_label, cfg_lo, cfg_hi in self.config.length_filter_durations:
                if cfg_label == label:
                    self._filter_proxy.set_length_filter(label, cfg_lo, cfg_hi)
                    break
        self._update_track_count()

    def _on_filters_reset(self):
        self._filter_proxy.clear_all_filters()
        self._update_track_count()

    # ── Sidebar handlers ─────────────────────────────────

    def _on_genre_selected(self, genres):
        """genres is a set of genre strings, or None for All."""
        self._filter_proxy.set_genre_filter(genres)
        self._update_track_count()

    def _on_playlist_selected(self, paths):
        """paths is a set of file paths, or None for All Tracks."""
        self._filter_proxy.set_playlist_filter(paths)
        self._update_track_count()

    def _on_playlist_changed(self):
        """A playlist was created/renamed/deleted — persist to config."""
        self.config.playlists = self._sidebar._playlists
        self.config.save()

    # ── Tag bar handler ──────────────────────────────────

    def _on_tags_changed(self, active_tags):
        """Handle tag toggle — active_tags is a set (empty = show all)."""
        self._filter_proxy.set_tag_filter(active_tags)
        self._update_track_count()

    # ── VLC helpers ──────────────────────────────────────

    def _vlc_mp(self):
        """Shortcut to the underlying media player."""
        return self.vlc_player.get_media_player()

    def _load(self, index):
        """Prepare VLC to play playlist[index]. Returns True on success."""
        if index is None or index < 0 or index >= len(self.playlist):
            return False
        path = self.playlist[index]['_abs_path']
        if not os.path.isfile(path):
            title = self.playlist[index].get('title', os.path.basename(path))
            QMessageBox.warning(self, 'File not found',
                                f'Cannot play \u201c{title}\u201d\n\n'
                                f'The file no longer exists:\n{path}')
            return False
        try:
            media = self.vlc_instance.media_new(path)
            self.vlc_media_list = self.vlc_instance.media_list_new()
            self.vlc_media_list.add_media(media)
            self.vlc_player.set_media_list(self.vlc_media_list)
            self.current_index = index
            self._track_model.set_now_playing(index)
            self._track_table.jump_to_playlist_index(index)
            self._start_waveform(index)
            return True
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Could not load {path}: {e}')
            return False

    def _update_now_playing(self, text=None):
        """Update the now-playing label and genre badge."""
        if text:
            self._lbl_now_playing.setText(text)
            self._lbl_genre.setText('')
        elif self.current_index is not None:
            entry = self.playlist[self.current_index]
            title = entry.get('title', entry.get('basename', ''))
            genre = entry.get('genre', '')
            self._lbl_now_playing.setText(title)
            if genre and genre != 'Unknown':
                self._lbl_genre.setText(f'  {genre}  ')
            else:
                self._lbl_genre.setText('')
        else:
            self._lbl_now_playing.setText('Not Playing')
            self._lbl_genre.setText('')
        self._update_rating_display()

    def _record_play_immediate(self):
        """Record the play for the current track and update the table row."""
        if self.current_index is None:
            return
        path = self.playlist[self.current_index]['path']
        stats = self.db.record_play(path)
        if stats:
            entry = self.playlist[self.current_index]
            entry['play_count'] = stats[0]
            entry['first_played'] = stats[1]
            entry['last_played'] = stats[2]
        self._track_model.update_row(self.current_index)
        self._play_log.refresh()

    # ── Playback controls ────────────────────────────────

    def _play_pause(self):
        """Toggle play / pause / start."""
        # Currently playing → pause
        if self.is_playing and not self.is_paused:
            self.vlc_player.pause()
            self.is_paused = True
            self.is_playing = False
            self._last_action = 'paused'
            self._transport.set_playing_state(False)
            self._update_now_playing('Paused')
            return

        # Currently paused → resume
        if self.is_paused:
            self.vlc_player.play()
            self.is_paused = False
            self.is_playing = True
            self._last_action = 'playing'
            self._play_started_at = time.time()
            self._transport.set_playing_state(True)
            self._update_now_playing()
            return

        # Nothing playing → start
        if not self.playlist:
            QMessageBox.information(self, 'No tracks', 'Add some audio files first.')
            return
        if self.current_index is None:
            self.current_index = 0

        self._play_index(self.current_index)

    def _play_index(self, index):
        """Load and start playing a specific playlist index."""
        # Auto-close preview when main player starts a new track
        self._close_preview()
        if not self._load(index):
            return
        try:
            self.vlc_player.play()
            self._debug_log('INFO', f'Playing #{index}: {self.playlist[index].get("title", "?")}')
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self._play_started_at = time.time()
            self._consecutive_skips = 0
            self._record_play_immediate()
            self._transport.set_playing_state(True)
            self._update_now_playing()
            self._apply_eq_for_current()
        except Exception as e:
            QMessageBox.critical(self, 'Playback error', str(e))

    def _stop(self):
        """Stop playback."""
        self._cancel_waveform()
        self.vlc_player.stop()
        self.is_playing = False
        self.is_paused = False
        self._last_action = 'stopped'
        self._transport.reset_display()
        self._update_now_playing('Stopped')

    def _next_track(self):
        """Advance to the next track (queue-aware)."""
        if not self.playlist:
            return
        # Auto-reset speed if enabled
        if self._auto_reset_speed and abs(self._speed - 1.0) > 0.05:
            self._speed_reset()

        # Check play queue first
        nxt = self._queue_panel.pop_next()
        if nxt is None:
            if self.current_index is not None:
                nxt = (self.current_index + 1) % len(self.playlist)
            else:
                nxt = 0

        if not self._load(nxt):
            self._last_action = 'stopped'
            self.is_playing = False
            self.is_paused = False
            return
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self._play_started_at = time.time()
        self._record_play_immediate()
        self._transport.set_playing_state(True)
        self._update_now_playing()

    def _prev_track(self):
        """Go back to the previous track."""
        if not self.playlist:
            return
        if self.current_index is not None:
            prev = (self.current_index - 1) % len(self.playlist)
        else:
            prev = 0

        if not self._load(prev):
            self._last_action = 'stopped'
            self.is_playing = False
            self.is_paused = False
            return
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self._play_started_at = time.time()
        self._record_play_immediate()
        self._transport.set_playing_state(True)
        self._update_now_playing()

    # ── Scrub / Volume / Speed ───────────────────────────

    def _on_scrub_released(self, pos):
        """Seek to position (0.0–1.0) in the current track."""
        mp = self._vlc_mp()
        length = mp.get_length()
        if length > 0 and (self.is_playing or self.is_paused):
            mp.set_position(pos)

    def _on_volume_changed(self, volume):
        """Apply volume (0–100) to VLC."""
        self._vlc_mp().audio_set_volume(volume)
        if volume > 0:
            self._muted = False
            self._volume_strip.set_mute_icon(False)

    def _toggle_mute(self):
        if self._muted:
            self._volume_strip.set_volume(self._pre_mute_vol)
            self._vlc_mp().audio_set_volume(self._pre_mute_vol)
            self._muted = False
            self._volume_strip.set_mute_icon(False)
        else:
            self._pre_mute_vol = self._volume_strip.volume_slider.value()
            self._volume_strip.set_volume(0)
            self._vlc_mp().audio_set_volume(0)
            self._muted = True
            self._volume_strip.set_mute_icon(True)

    def _speed_up(self):
        self._speed = min(round(self._speed + 0.1, 1), 3.0)
        self._apply_speed()

    def _speed_down(self):
        self._speed = max(round(self._speed - 0.1, 1), 0.3)
        self._apply_speed()

    def _speed_reset(self):
        self._speed = 1.0
        self._apply_speed()

    def _apply_speed(self):
        self._vlc_mp().set_rate(self._speed)
        self._transport.set_speed_label(self._speed)

    def _on_auto_reset_speed(self, checked):
        self._auto_reset_speed = checked

    # ── Jump to playing ──────────────────────────────────

    def _jump_to_playing(self):
        """Scroll the track table to the currently playing track."""
        if self.current_index is not None:
            self._track_table.jump_to_playlist_index(self.current_index)

    # ── Slot handlers ────────────────────────────────────

    def _on_play_requested(self, playlist_idx):
        """Handle double-click on a track — load and play it."""
        # Auto-reset speed if enabled
        if self._auto_reset_speed and abs(self._speed - 1.0) > 0.05:
            self._speed_reset()
        self._play_index(playlist_idx)

    def _on_selection_changed(self, indices):
        """Handle track selection change."""
        self._selected_indices = indices

    def _get_selected_indices(self):
        """Return list of currently selected playlist indices."""
        indices = []
        for index in self._track_table.selectionModel().selectedRows():
            from PySide6.QtCore import Qt
            pl_idx = index.data(Qt.UserRole)
            if pl_idx is not None:
                indices.append(pl_idx)
        return indices

    def _on_context_menu(self, playlist_idx, pos):
        """Show context menu on right-click in track table."""
        selected = self._get_selected_indices()
        if not selected:
            selected = [playlist_idx]
        entry = self.playlist[playlist_idx]
        multi = len(selected) > 1
        menu = QMenu(self)

        # Play
        if not multi:
            menu.addAction('\u25b6  Play', lambda: self._play_index(playlist_idx))
            menu.addAction('\U0001f3a7  Preview', lambda: self._preview_track(playlist_idx))

        # Add to queue
        q_label = f'\U0001f4cb  Add {len(selected)} to Queue' if multi else '\U0001f4cb  Add to Queue'
        menu.addAction(q_label,
                       lambda idxs=selected: self._queue_panel.add_multiple(idxs))

        menu.addSeparator()

        # Edit actions (single selection only)
        if not multi:
            menu.addAction('\u270f  Edit Title\u2026',
                           lambda: self._ctx_edit_title(playlist_idx))

            # Genre submenu
            genre_sub = menu.addMenu('\U0001f3b5  Genre')
            current_genre = entry.get('genre', 'Unknown')
            for genre in sorted(self.genres):
                lbl = f'\u2713  {genre}' if genre == current_genre else f'     {genre}'
                genre_sub.addAction(
                    lbl, lambda g=genre: self._ctx_set_genre(playlist_idx, g))
            genre_sub.addSeparator()
            genre_sub.addAction('Other\u2026',
                                lambda: self._ctx_edit_genre(playlist_idx))

            menu.addAction('\u270f  Edit Comment\u2026',
                           lambda: self._ctx_edit_comment(playlist_idx))

            # Tags submenu
            if self.config.all_tags:
                tags_sub = menu.addMenu('\U0001f3f7  Tags')
                track_tags = set(entry.get('tags', []))
                for tag in sorted(self.config.all_tags):
                    has = tag in track_tags
                    lbl = f'\u2713  {tag.upper()}' if has else f'     {tag.upper()}'
                    tags_sub.addAction(
                        lbl, lambda t=tag, h=has: self._ctx_toggle_tag(
                            playlist_idx, t, h))

        # Playlist submenu
        pl_names = self._sidebar.get_playlist_names()
        if pl_names:
            menu.addSeparator()
            pl_sub = menu.addMenu('\U0001f4c1  Add to Playlist')
            for pl_name in pl_names:
                pl_sub.addAction(
                    pl_name,
                    lambda n=pl_name, idxs=selected: self._ctx_add_to_playlist(
                        n, idxs))

        # Remove from active playlist
        active_pl = self._sidebar.get_active_playlist_name()
        if active_pl:
            n = len(selected)
            lbl = (f'\U0001f5d1  Remove {n} from "{active_pl}"' if multi
                   else f'\U0001f5d1  Remove from "{active_pl}"')
            menu.addAction(
                lbl, lambda idxs=selected: self._ctx_remove_from_playlist(idxs))

        menu.addSeparator()
        n = len(selected)
        r_lbl = f'\U0001f5d1  Remove {n} from Library' if multi else '\U0001f5d1  Remove from Library'
        menu.addAction(r_lbl,
                       lambda idxs=selected: self._ctx_remove_tracks(idxs))

        menu.exec(pos)

    # ── Context menu actions ─────────────────────────────

    def _ctx_edit_title(self, idx):
        entry = self.playlist[idx]
        current = entry.get('title', entry.get('basename', ''))
        new_val, ok = QInputDialog.getText(
            self, 'Edit Title', 'Title:', text=current)
        if ok and new_val.strip():
            entry['title'] = new_val.strip()
            self.db.update_track_field(entry['path'], 'title', new_val.strip())
            self._track_model.update_row(idx)

    def _ctx_set_genre(self, idx, genre):
        entry = self.playlist[idx]
        entry['genre'] = genre
        self.genres.add(genre)
        self.db.update_track_field(entry['path'], 'genre', genre)
        self._track_model.update_row(idx)
        self._sidebar.set_genre_data(self.genres, self.config.genre_groups)

    def _ctx_edit_genre(self, idx):
        entry = self.playlist[idx]
        current = entry.get('genre', 'Unknown')
        new_val, ok = QInputDialog.getText(
            self, 'Change Genre', 'Genre:', text=current)
        if ok and new_val.strip():
            self._ctx_set_genre(idx, new_val.strip())

    def _ctx_edit_comment(self, idx):
        entry = self.playlist[idx]
        current = entry.get('comment', '')
        new_val, ok = QInputDialog.getText(
            self, 'Edit Comment', 'Comment:', text=current)
        if ok:
            entry['comment'] = new_val.strip()
            self.db.update_track_field(entry['path'], 'comment', new_val.strip())
            self._track_model.update_row(idx)

    def _ctx_toggle_tag(self, idx, tag, currently_has):
        entry = self.playlist[idx]
        if currently_has:
            self.db.remove_tag(entry['path'], tag)
            if tag in entry.get('tags', []):
                entry['tags'].remove(tag)
        else:
            self.db.add_tag(entry['path'], tag)
            entry.setdefault('tags', []).append(tag)
        self._track_model.update_row(idx)

    def _ctx_add_to_playlist(self, pl_name, indices):
        paths = [self.playlist[i]['path'] for i in indices]
        self._sidebar.add_tracks_to_playlist(pl_name, paths)

    def _ctx_remove_from_playlist(self, indices):
        paths = {self.playlist[i]['path'] for i in indices}
        self._sidebar.remove_tracks_from_active_playlist(paths)
        self._update_track_count()

    def _ctx_remove_tracks(self, indices):
        n = len(indices)
        msg = (f'Remove {n} tracks from the library?\n\n(Files will not be deleted.)'
               if n > 1 else
               f'Remove "{self.playlist[indices[0]].get("title", "?")}" from the library?\n\n'
               '(File will not be deleted.)')
        reply = QMessageBox.question(
            self, 'Remove Track', msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        # Remove in reverse order to keep indices valid
        for idx in sorted(indices, reverse=True):
            entry = self.playlist[idx]
            path = entry['path']
            if self.current_index == idx:
                self._stop()
                self.current_index = None
            elif self.current_index is not None and self.current_index > idx:
                self.current_index -= 1
            self.db.delete_track(path)
            self.playlist.pop(idx)
            self._path_set.discard(path)
            self._path_to_idx.pop(path, None)
        # Rebuild path→idx mapping
        self._path_to_idx = {e['path']: i for i, e in enumerate(self.playlist)}
        for i, e in enumerate(self.playlist):
            e['_playlist_idx'] = i
        self._track_model.set_tracks(self.playlist)
        self._update_track_count()

    # ── Voting ───────────────────────────────────────────

    def _vote(self, vote):
        """Record a +1 (like) or -1 (dislike) vote for the current track."""
        if self.current_index is None:
            QMessageBox.information(self, 'No Track', 'No track is currently playing.')
            return
        entry = self.playlist[self.current_index]
        voter = self._voter_combo.currentText().strip()

        success, msg = self.db.record_vote(entry['path'], vote, voter)
        if not success:
            QMessageBox.information(self, 'Already Voted', msg)
            return

        # Update in-memory state
        entry['rating'] = entry.get('rating', 0) + vote
        if voter:
            self.all_voters.add(voter)
            if vote > 0:
                entry.setdefault('liked_by', set()).add(voter)
            else:
                entry.setdefault('disliked_by', set()).add(voter)

        self._track_model.update_row(self.current_index)
        self._update_rating_display()
        self._refresh_voter_combo()
        self._search_bar.set_voters(self.all_voters)

    def _update_rating_display(self):
        """Update the rating label in the now-playing bar."""
        if self.current_index is None:
            self._lbl_rating.setText('')
            return
        rating = self.playlist[self.current_index].get('rating', 0)
        if rating > 0:
            self._lbl_rating.setText(f'+{rating}')
            self._lbl_rating.setStyleSheet(
                'font-size: 13px; font-weight: bold; color: #4caf50; padding: 0 6px;')
        elif rating < 0:
            self._lbl_rating.setText(str(rating))
            self._lbl_rating.setStyleSheet(
                'font-size: 13px; font-weight: bold; color: #f44336; padding: 0 6px;')
        else:
            self._lbl_rating.setText('0')
            self._lbl_rating.setStyleSheet(
                'font-size: 13px; font-weight: bold; color: #888888; padding: 0 6px;')

    def _refresh_voter_combo(self):
        """Rebuild the voter dropdown with current voter names."""
        current = self._voter_combo.currentText()
        self._voter_combo.clear()
        self._voter_combo.addItem('')  # anonymous
        for name in sorted(self.all_voters):
            self._voter_combo.addItem(name)
        # Restore selection
        idx = self._voter_combo.findText(current)
        if idx >= 0:
            self._voter_combo.setCurrentIndex(idx)

    # ── Equalizer ────────────────────────────────────────

    def _show_eq_dialog(self):
        """Open the per-track equalizer dialog."""
        path = None
        title = None
        if self.current_index is not None:
            entry = self.playlist[self.current_index]
            path = entry.get('path')
            title = entry.get('title', entry.get('basename', ''))
        dlg = EqualizerDialog(
            self, db=self.db, vlc_player=self.vlc_player,
            track_path=path, track_title=title)
        dlg.exec()
        self._update_eq_button()

    def _apply_eq_for_current(self):
        """Load and apply saved EQ for the current track."""
        path = None
        if self.current_index is not None:
            path = self.playlist[self.current_index].get('path')
        has_eq = apply_eq_for_track(self.db, self.vlc_player, path)
        self._update_eq_button(has_eq)

    def _update_eq_button(self, has_eq=None):
        """Style the EQ button green when a custom EQ is active."""
        if has_eq is None:
            # Check DB
            if self.current_index is not None:
                path = self.playlist[self.current_index].get('path')
                tid = self.db.get_track_id(path) if path else None
                row = self.db.load_track_eq(tid) if tid else None
                has_eq = row is not None
            else:
                has_eq = False
        if has_eq:
            self._btn_eq.setIcon(self._icon_eq_on)
            self._btn_eq.setStyleSheet(
                'QPushButton { background-color: #1a3d1a;'
                '  border: 1px solid #4caf50; border-radius: 4px; }'
                'QPushButton:hover { background-color: #2a5a2a; }')
        else:
            self._btn_eq.setIcon(self._icon_eq_off)
            self._btn_eq.setStyleSheet('')

    # ── Settings ─────────────────────────────────────────

    def _open_settings(self, tab=None):
        """Open the settings dialog and apply changes on save."""
        dlg = SettingsDialog(
            self, config=self.config, db=self.db, genres=self.genres,
            volume_strip=self._volume_strip)
        if tab:
            dlg.show_tab(tab)
        if dlg.exec():
            # Refresh UI elements that depend on config
            self._sidebar.set_genre_data(
                sorted(self.genres), self.config.genre_groups)
            self._tag_bar.set_tags(self.config.all_tags, self.config.tag_rows)
            if self.config.length_filter_durations:
                opts = [label for label, lo, hi in self.config.length_filter_durations]
                self._search_bar.set_length_options(opts)
            # Apply saved fade settings
            self._volume_strip.apply_config(self.config)
            self._volume_panel.pull_fader.apply_config(self.config)

    # ── Misc dialogs ────────────────────────────────────

    def _random_queue_dialog(self):
        """Open the random queue generator and apply result to queue panel."""
        dlg = RandomQueueDialog(
            self, playlist=self.playlist, genres=self.genres,
            all_tags=self.config.all_tags)
        if dlg.exec() and dlg.result_indices:
            self._queue_panel.set_queue(dlg.result_indices)

    def _show_audit_log(self):
        """Show the audit log viewer."""
        AuditLogDialog(self, db=self.db).exec()

    def _on_play_from_queue(self, playlist_idx):
        """Handle double-click on a queue item — play immediately."""
        if self._auto_reset_speed and abs(self._speed - 1.0) > 0.05:
            self._speed_reset()
        self._play_index(playlist_idx)

    def _jump_to_track_index(self, playlist_idx):
        """Scroll and select a track in the main table by playlist index."""
        self._track_table.jump_to_playlist_index(playlist_idx)

    # ── Poll timer ───────────────────────────────────────

    def _poll(self):
        """Periodic update: scrub position, time labels, auto-advance."""
        try:
            self._poll_inner()
        except Exception:
            pass  # swallow errors in the poll loop

    def _poll_inner(self):
        mp = self._vlc_mp()
        is_playing = mp.is_playing()

        # Reset consecutive skip counter when a track is genuinely playing
        if is_playing and self._consecutive_skips > 0:
            self._consecutive_skips = 0

        # Update scrub slider and time labels
        if not self._transport.is_user_scrubbing:
            length = mp.get_length()
            pos = mp.get_position()
            if length > 0 and pos >= 0:
                self._transport.set_scrub_position(pos)
                self._transport.set_time_labels(int(pos * length), length)
            elif not is_playing and not self.is_paused:
                self._transport.set_scrub_position(0)
                self._transport.set_time_labels(0, 0)

        # Auto-advance: VLC finished playing and we were in 'playing' state
        if not is_playing and self._last_action == 'playing' and not self.is_paused:
            # Guard: don't auto-advance within 1.5s of play (VLC async startup)
            if time.time() - self._play_started_at < 1.5:
                return
            if self._consecutive_skips >= 3:
                self._consecutive_skips = 0
                self._stop()
            elif len(self.playlist) > 1:
                self._consecutive_skips += 1
                self._next_track()
            elif self.playlist:
                self._stop()

    # ── Keyboard shortcuts ─────────────────────────────

    def _bind_shortcuts(self):
        """Set up global keyboard shortcuts."""
        def _sc(key, slot):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(slot)
            return s

        _sc('Space',      self._play_pause)
        _sc('Right',      self._next_track)
        _sc('Left',       self._prev_track)
        _sc('Escape',     self._stop)
        _sc('Ctrl+F',     self._focus_search)
        _sc('F1',         self._toggle_sidebar)
        _sc('F2',         self._toggle_right_panel)
        _sc('F3',         self._toggle_tag_bar)
        _sc('F4',         self._toggle_search_bar)
        _sc('Ctrl+L',     self._toggle_lite_mode)
        _sc('F10',        self._toggle_debug_panel)
        _sc('F11',        self._toggle_fullscreen)
        _sc('P',          self._preview_selected)

    def _focus_search(self):
        """Focus the search box."""
        self._search_bar.focus_search()

    def _toggle_sidebar(self):
        """Show/hide the left sidebar panel."""
        if self._sidebar.isVisible():
            self._sidebar.hide()
        else:
            self._sidebar.show()

    def _toggle_right_panel(self):
        """Show/hide the right queue/play-log panel."""
        if self._right_splitter.isVisible():
            self._right_splitter.hide()
        else:
            self._right_splitter.show()

    def _toggle_tag_bar(self):
        """Show/hide the tag filter bar."""
        if self._tag_bar.isVisible():
            self._tag_bar.hide()
        else:
            self._tag_bar.show()

    def _toggle_search_bar(self):
        """Show/hide the search/filter bar."""
        if self._search_bar.isVisible():
            self._search_bar.hide()
        else:
            self._search_bar.show()

    def _toggle_lite_mode(self):
        """Toggle lite mode — hides sidebar, search bar, and tag bar."""
        self._lite_mode = not self._lite_mode
        if self._lite_mode:
            self._sidebar.hide()
            self._search_bar.hide()
            self._tag_bar.hide()
        else:
            self._sidebar.show()
            self._search_bar.show()
            self._tag_bar.show()

    def _toggle_fullscreen(self):
        """Toggle between fullscreen and normal window."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_debug_panel(self):
        """Show/hide the debug log panel."""
        if self._debug_panel.isVisible():
            self._debug_panel.hide()
        else:
            self._debug_panel.show()

    def _reset_track_list_default(self):
        """Reset columns to the default visible set and widths."""
        self._track_table.set_visible_columns(list(DEFAULT_VISIBLE_COLUMNS))
        for col, width in self._track_table._default_widths.items():
            self._track_table.setColumnWidth(col, width)
        self.statusBar().showMessage('Track list reset to default layout', 3000)

    def _show_all_columns(self):
        """Make all columns visible."""
        self._track_table.set_visible_columns(list(ALL_COLUMNS))
        self.statusBar().showMessage('All columns visible', 3000)

    def _debug_log(self, level, msg):
        """Write a message to the debug panel."""
        self._debug_panel.log(level, msg)

    # ── Audio device routing ─────────────────────────────

    def _refresh_audio_device_menus(self):
        """(Re)populate the Audio → Main Output / Preview Output sub-menus."""
        devices = list_audio_devices(self.vlc_instance)

        for sub, current_id, setter in [
            (self._audio_main_sub, self.config.main_audio_device,
             self._set_main_audio_device),
            (self._audio_preview_sub, self.config.preview_audio_device,
             self._set_preview_audio_device),
        ]:
            sub.clear()
            for dev_id, label in devices:
                prefix = '\u2713  ' if dev_id == current_id else '     '
                action = sub.addAction(
                    f'{prefix}{label}',
                    lambda d=dev_id, s=setter: s(d),
                )
                action.setData(dev_id)

    def _set_main_audio_device(self, device_id):
        self.config.main_audio_device = device_id
        self._apply_main_audio_device()
        self._refresh_audio_device_menus()
        self.config.save()
        label = 'System Default' if not device_id else device_id
        self._debug_log('INFO', f'Main audio output → {label}')

    def _set_preview_audio_device(self, device_id):
        self.config.preview_audio_device = device_id
        self._refresh_audio_device_menus()
        self.config.save()
        label = 'System Default' if not device_id else device_id
        self._debug_log('INFO', f'Preview audio output → {label}')

    def _apply_main_audio_device(self):
        """Route the main VLC media player to the configured device."""
        dev = self.config.main_audio_device
        if dev:
            self._vlc_mp().audio_output_device_set(None, dev)

    # ── Preview ──────────────────────────────────────────

    def _preview_track(self, playlist_idx):
        """Open the modeless preview dialog for the given track."""
        self._close_preview()
        entry = self.playlist[playlist_idx]

        # Try to pass cached waveform data to the preview dialog
        wf_data = None
        rel_path = entry.get('path', '')
        cached = self.db.get_waveform(rel_path)
        if cached:
            try:
                wf_data = deserialise_waveform(cached)
            except Exception:
                pass

        self._preview_dialog = PreviewDialog(
            track_entry=entry,
            device_id=self.config.preview_audio_device,
            waveform_data=wf_data,
            parent=self,
        )
        self._preview_dialog.closed.connect(self._on_preview_closed)
        self._preview_dialog.show()
        self._debug_log('INFO',
                        f'Preview: {entry.get("title", entry.get("basename", "?"))}')

    def _preview_selected(self):
        """Preview the first selected track (P shortcut)."""
        selected = self._get_selected_indices()
        if selected:
            self._preview_track(selected[0])

    def _close_preview(self):
        """Stop and close the preview dialog if it is open."""
        if self._preview_dialog is not None:
            try:
                self._preview_dialog.stop_and_release()
                self._preview_dialog.close()
            except RuntimeError:
                pass  # already deleted
            self._preview_dialog = None

    def _on_preview_closed(self):
        """Slot for PreviewDialog.closed signal."""
        self._preview_dialog = None

    # ── Waveform ─────────────────────────────────────────

    def _start_waveform(self, playlist_idx):
        """Kick off waveform generation for the given track.

        Checks the DB cache first; spawns a background worker on miss.
        Cancels any in-flight worker.
        """
        # Cancel previous worker
        if self._waveform_worker is not None:
            self._waveform_worker.cancel()
            self._waveform_worker = None

        entry = self.playlist[playlist_idx]
        abs_path = entry.get('_abs_path', '')
        rel_path = entry.get('path', '')

        # Try cache
        cached = self.db.get_waveform(rel_path)
        if cached:
            try:
                data = deserialise_waveform(cached)
                self._transport.scrub_slider.set_waveform(data)
                return
            except Exception:
                pass  # corrupt cache — regenerate

        # Show loading state
        self._transport.scrub_slider.set_loading(True)
        self._debug_log('INFO', f'Waveform: generating for {os.path.basename(abs_path)}')

        # Spawn worker
        worker = WaveformWorker(abs_path, parent=self)
        worker.finished.connect(
            lambda fp, data, rp=rel_path: self._on_waveform_ready(rp, fp, data))
        self._waveform_worker = worker
        worker.start()

    def _on_waveform_ready(self, rel_path, file_path, data):
        """Slot: background waveform generation finished."""
        self._waveform_worker = None
        if not data:
            self._transport.scrub_slider.set_loading(False)
            self._debug_log('WARN', f'Waveform generation failed for {os.path.basename(file_path)}')
            return

        self._debug_log('INFO', f'Waveform: ready ({len(data)} bins) for {os.path.basename(file_path)}')

        # Cache to DB
        try:
            blob = serialise_waveform(data)
            self.db.save_waveform(rel_path, blob)
        except Exception as e:
            self._debug_log('WARN', f'Waveform cache save failed: {e}')

        # Apply to scrub bar (only if we're still on the same track)
        if self.current_index is not None:
            cur_path = self.playlist[self.current_index].get('_abs_path', '')
            if cur_path == file_path:
                self._transport.scrub_slider.set_waveform(data)

    def _cancel_waveform(self):
        """Cancel any in-flight waveform worker."""
        if self._waveform_worker is not None:
            self._waveform_worker.cancel()
            self._waveform_worker = None

    # ── Drag-and-drop ─────────────────────────────────

    _AUDIO_EXTS = ('.mp3', '.wav', '.ogg', '.flac')

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        """Handle files/folders dropped from a file manager."""
        urls = event.mimeData().urls()
        if not urls:
            return
        added = 0
        for url in urls:
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isdir(path):
                for root_dir, _, filenames in os.walk(path):
                    for name in filenames:
                        if name.lower().endswith(self._AUDIO_EXTS):
                            if self._add_path(os.path.join(root_dir, name)):
                                added += 1
            elif os.path.isfile(path) and path.lower().endswith(self._AUDIO_EXTS):
                if self._add_path(path):
                    added += 1
        if added:
            self._track_model.set_tracks(self.playlist)
            self._update_track_count()
            self._lbl_now_playing.setText(f'Dropped {added} track(s)')
        event.acceptProposedAction()

    # ── Cleanup ──────────────────────────────────────────

    def closeEvent(self, event):
        self._poll_timer.stop()
        self._cancel_waveform()
        self._close_preview()
        self.vlc_player.stop()
        self.config.visible_columns = self._track_table.get_visible_columns()
        self.config.save()
        # Persist queue
        self.db.save_queue(self._queue_panel.queue_paths())
        super().closeEvent(event)
