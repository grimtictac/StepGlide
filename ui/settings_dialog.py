"""
Settings dialog — tabbed dialog for genre groups, tags, length filters,
tooltips, and interface options.
"""

import os
import shutil
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from core.config import DEFAULT_TOOLTIPS
from ui.theme import COLORS
from ui.transport_bar import TickSlider

import qtawesome as qta


class SettingsDialog(QDialog):
    """Tabbed settings dialog matching the five original tabs."""

    def __init__(self, parent, *, config, db, genres, volume_strip=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.resize(580, 620)
        self.setModal(True)

        self._config = config
        self._db = db
        self._genres = sorted(genres)
        self._volume_strip = volume_strip

        # Working copies so Cancel discards changes
        self._working_groups = {k: list(v) for k, v in config.genre_groups.items()}
        self._working_tags = set(config.all_tags)
        self._working_tag_rows = dict(config.tag_rows)
        self._working_durations = [list(d) for d in config.length_filter_durations]
        self._working_tooltips = dict(config.tooltip_texts)
        self._working_queue_throb = config.queue_btn_throb_enabled

        # Volume fade working copies
        self._wk_fade_step = config.fade_step
        self._wk_fade_min_interval = config.fade_min_interval
        self._wk_fade_max_interval = config.fade_max_interval
        self._wk_fade_vel_window = config.fade_vel_window
        self._wk_fade_vel_low = config.fade_vel_low
        self._wk_fade_vel_high = config.fade_vel_high
        self._wk_fade_tick_threshold = config.fade_tick_threshold

        # Pull-fader working copies
        self._wk_pull_step = config.pull_fade_step
        self._wk_pull_min_interval = config.pull_min_interval
        self._wk_pull_max_interval = config.pull_max_interval
        self._wk_pull_dead_zone = config.pull_dead_zone

        self._build_ui()

    # ── Layout ───────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(4)

        # Tab buttons row
        tab_row = QHBoxLayout()
        tab_row.setSpacing(4)
        self._tab_buttons = {}
        self._stack = QStackedWidget()

        for label in ('Genres', 'Tags', 'Length', 'Tooltips', 'Interface', 'Volume'):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda checked, l=label: self._show_tab(l))
            tab_row.addWidget(btn)
            self._tab_buttons[label] = btn

        root.addLayout(tab_row)

        # Build each tab page
        self._stack.addWidget(self._build_genres_tab())     # 0
        self._stack.addWidget(self._build_tags_tab())       # 1
        self._stack.addWidget(self._build_length_tab())     # 2
        self._stack.addWidget(self._build_tooltips_tab())   # 3
        self._stack.addWidget(self._build_interface_tab())  # 4
        self._stack.addWidget(self._build_volume_tab())     # 5
        root.addWidget(self._stack, 1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_snapshot = QPushButton('\U0001f4be Snapshot')
        btn_snapshot.setStyleSheet('padding: 6px 12px;')
        btn_snapshot.clicked.connect(self._snapshot)
        btn_row.addWidget(btn_snapshot)

        btn_genres = QPushButton('\U0001f3b5 Show All Genres')
        btn_genres.setStyleSheet('padding: 6px 12px;')
        btn_genres.clicked.connect(self._show_all_genres)
        btn_row.addWidget(btn_genres)

        btn_row.addStretch()

        btn_cancel = QPushButton('Cancel')
        btn_cancel.setStyleSheet('padding: 6px 14px;')
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton('Save')
        btn_save.setStyleSheet(
            f'background-color: {COLORS["accent"]}; color: white; padding: 6px 14px;')
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        root.addLayout(btn_row)

        self._show_tab('Genres')

    def show_tab(self, name):
        """Public API — jump to a named tab (e.g. 'Volume')."""
        self._show_tab(name)

    def _show_tab(self, name):
        idx_map = {
            'Genres': 0, 'Tags': 1, 'Length': 2,
            'Tooltips': 3, 'Interface': 4, 'Volume': 5,
        }
        self._stack.setCurrentIndex(idx_map[name])
        for lbl, btn in self._tab_buttons.items():
            btn.setChecked(lbl == name)
            if lbl == name:
                btn.setStyleSheet(
                    f'background-color: {COLORS["accent"]}; color: white; '
                    f'font-weight: bold; border-radius: 4px;')
            else:
                btn.setStyleSheet(
                    'background-color: transparent; border: 1px solid #555; border-radius: 4px;')

    # ── Genres tab ───────────────────────────────────────

    def _build_genres_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(4)

        layout.addWidget(self._heading('Genre Groups'))
        layout.addWidget(self._subtext('Create groups and assign genres to them.'))

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._genre_content = QWidget()
        self._genre_layout = QVBoxLayout(self._genre_content)
        self._genre_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._genre_content)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton('+ New Group')
        btn_add.clicked.connect(self._add_genre_group)
        btn_row.addWidget(btn_add)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._rebuild_genres()
        return page

    def _rebuild_genres(self):
        # Clear
        while self._genre_layout.count():
            item = self._genre_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for gname, members in self._working_groups.items():
            group_widget = QWidget()
            group_widget.setStyleSheet(f'background-color: {COLORS["bg_mid"]};'
                                       f'border-radius: 4px; padding: 4px;')
            gl = QVBoxLayout(group_widget)
            gl.setContentsMargins(8, 6, 8, 6)
            gl.setSpacing(2)

            # Header row
            hdr = QHBoxLayout()
            lbl = QLabel(gname)
            lbl.setStyleSheet('font-size: 13px; font-weight: bold;')
            hdr.addWidget(lbl)
            hdr.addStretch()
            btn_rename = QPushButton()
            btn_rename.setIcon(qta.icon('mdi6.pencil', color=COLORS['fg']))
            btn_rename.setFixedSize(30, 26)
            btn_rename.setIconSize(btn_rename.size() * 0.55)
            btn_rename.clicked.connect(lambda _, g=gname: self._rename_genre_group(g))
            hdr.addWidget(btn_rename)
            btn_del = QPushButton()
            btn_del.setIcon(qta.icon('mdi6.delete', color=COLORS['red_text']))
            btn_del.setFixedSize(30, 26)
            btn_del.setIconSize(btn_del.size() * 0.55)
            btn_del.clicked.connect(lambda _, g=gname: self._delete_genre_group(g))
            hdr.addWidget(btn_del)
            gl.addLayout(hdr)

            # Checkboxes for genres
            for genre in self._genres:
                cb = QCheckBox(genre)
                cb.setChecked(genre in members)
                cb.toggled.connect(
                    lambda checked, g=gname, gr=genre: self._toggle_genre(g, gr, checked))
                gl.addWidget(cb)

            self._genre_layout.addWidget(group_widget)

        # Ungrouped genres
        assigned = set()
        for m in self._working_groups.values():
            assigned.update(m)
        ungrouped = [g for g in self._genres if g not in assigned]
        if ungrouped:
            ug_widget = QWidget()
            ug_layout = QVBoxLayout(ug_widget)
            ug_layout.setContentsMargins(8, 6, 8, 6)
            lbl = QLabel('Ungrouped')
            lbl.setStyleSheet('font-size: 13px; font-weight: bold; color: #888;')
            ug_layout.addWidget(lbl)
            for g in ungrouped:
                ug_layout.addWidget(QLabel(f'  {g}'))
            self._genre_layout.addWidget(ug_widget)

    def _add_genre_group(self):
        name, ok = QInputDialog.getText(self, 'New Group', 'Group name:')
        if ok and name.strip() and name.strip() not in self._working_groups:
            self._working_groups[name.strip()] = []
            self._rebuild_genres()

    def _rename_genre_group(self, old):
        name, ok = QInputDialog.getText(self, 'Rename Group', 'New name:', text=old)
        if ok and name.strip() and name.strip() != old:
            self._working_groups[name.strip()] = self._working_groups.pop(old)
            self._rebuild_genres()

    def _delete_genre_group(self, gname):
        del self._working_groups[gname]
        self._rebuild_genres()

    def _toggle_genre(self, group, genre, checked):
        if checked:
            # Remove from any other group first
            for g, members in self._working_groups.items():
                if genre in members and g != group:
                    members.remove(genre)
            if genre not in self._working_groups[group]:
                self._working_groups[group].append(genre)
        else:
            if genre in self._working_groups[group]:
                self._working_groups[group].remove(genre)

    # ── Tags tab ─────────────────────────────────────────

    def _build_tags_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(4)

        layout.addWidget(self._heading('Manage Tags'))
        layout.addWidget(self._subtext('Create, rename, or delete tags.'))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._tags_content = QWidget()
        self._tags_layout = QVBoxLayout(self._tags_content)
        self._tags_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._tags_content)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton('+ New Tag')
        btn_add.clicked.connect(self._add_tag)
        btn_row.addWidget(btn_add)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._rebuild_tags()
        return page

    def _rebuild_tags(self):
        while self._tags_layout.count():
            item = self._tags_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for tag in sorted(self._working_tags):
            row = QWidget()
            row.setStyleSheet(f'background-color: {COLORS["bg_mid"]}; border-radius: 4px;')
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 6, 6, 6)
            rl.addWidget(QLabel(tag.upper()))
            rl.addStretch()
            btn_rename = QPushButton()
            btn_rename.setIcon(qta.icon('mdi6.pencil', color=COLORS['fg']))
            btn_rename.setFixedSize(30, 26)
            btn_rename.setIconSize(btn_rename.size() * 0.55)
            btn_rename.clicked.connect(lambda _, t=tag: self._rename_tag(t))
            rl.addWidget(btn_rename)
            btn_del = QPushButton()
            btn_del.setIcon(qta.icon('mdi6.delete', color=COLORS['red_text']))
            btn_del.setFixedSize(30, 26)
            btn_del.setIconSize(btn_del.size() * 0.55)
            btn_del.clicked.connect(lambda _, t=tag: self._delete_tag(t))
            rl.addWidget(btn_del)
            self._tags_layout.addWidget(row)

        if not self._working_tags:
            self._tags_layout.addWidget(QLabel('No tags yet.'))

    def _add_tag(self):
        name, ok = QInputDialog.getText(self, 'New Tag', 'Tag name:')
        if ok and name.strip():
            self._working_tags.add(name.strip().lower())
            self._rebuild_tags()

    def _rename_tag(self, old):
        name, ok = QInputDialog.getText(self, 'Rename Tag', 'New name:', text=old)
        if ok and name.strip() and name.strip().lower() != old:
            new = name.strip().lower()
            self._working_tags.discard(old)
            self._working_tags.add(new)
            if old in self._working_tag_rows:
                self._working_tag_rows[new] = self._working_tag_rows.pop(old)
            self._rebuild_tags()

    def _delete_tag(self, tag):
        ans = QMessageBox.question(
            self, 'Delete Tag', f'Delete tag "{tag}" from all tracks?')
        if ans == QMessageBox.Yes:
            self._working_tags.discard(tag)
            self._working_tag_rows.pop(tag, None)
            self._rebuild_tags()

    # ── Length tab ───────────────────────────────────────

    def _build_length_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(4)

        layout.addWidget(self._heading('Length Filter Durations'))
        layout.addWidget(self._subtext('Configure duration ranges for the Length filter dropdown.'))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._length_content = QWidget()
        self._length_layout = QVBoxLayout(self._length_content)
        self._length_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._length_content)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton('+ Add Range')
        btn_add.clicked.connect(self._add_duration)
        btn_row.addWidget(btn_add)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addWidget(self._subtext(
            'Enter times as minutes (e.g. "2") or M:SS (e.g. "4:30").\n'
            'Leave From or To empty for open-ended ranges.'))

        self._rebuild_length()
        return page

    def _rebuild_length(self):
        while self._length_layout.count():
            item = self._length_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self._dur_editors = []  # keep references

        for i, dur in enumerate(self._working_durations):
            row = QWidget()
            row.setStyleSheet(f'background-color: {COLORS["bg_mid"]}; border-radius: 4px;')
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 4, 4)

            rl.addWidget(QLabel('Label'))
            lbl_edit = QLineEdit(dur[0])
            lbl_edit.setFixedWidth(120)
            rl.addWidget(lbl_edit)

            rl.addWidget(QLabel('From'))
            lo_edit = QLineEdit(self._secs_to_min(dur[1]))
            lo_edit.setFixedWidth(55)
            lo_edit.setPlaceholderText('min')
            rl.addWidget(lo_edit)

            rl.addWidget(QLabel('To'))
            hi_edit = QLineEdit(self._secs_to_min(dur[2]))
            hi_edit.setFixedWidth(55)
            hi_edit.setPlaceholderText('min')
            rl.addWidget(hi_edit)

            btn_del = QPushButton()
            btn_del.setIcon(qta.icon('mdi6.delete', color=COLORS['red_text']))
            btn_del.setFixedSize(30, 26)
            btn_del.setIconSize(btn_del.size() * 0.55)
            btn_del.clicked.connect(lambda _, idx=i: self._delete_duration(idx))
            rl.addWidget(btn_del)

            self._dur_editors.append((lbl_edit, lo_edit, hi_edit))
            self._length_layout.addWidget(row)

    def _add_duration(self):
        self._commit_durations()
        self._working_durations.append(['New Range', 0, 300])
        self._rebuild_length()

    def _delete_duration(self, idx):
        self._commit_durations()
        self._working_durations.pop(idx)
        self._rebuild_length()

    def _commit_durations(self):
        """Read current editor values back into working list."""
        for i, (lbl_e, lo_e, hi_e) in enumerate(self._dur_editors):
            if i < len(self._working_durations):
                self._working_durations[i] = [
                    lbl_e.text(),
                    self._parse_min(lo_e.text()),
                    self._parse_min(hi_e.text()),
                ]

    @staticmethod
    def _secs_to_min(secs):
        if secs is None:
            return ''
        m, s = divmod(int(secs), 60)
        return f'{m}:{s:02d}' if s else str(m)

    @staticmethod
    def _parse_min(text):
        text = text.strip()
        if not text:
            return None
        parts = text.split(':')
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(parts[0]) * 60
        except ValueError:
            return None

    # ── Tooltips tab ─────────────────────────────────────

    def _build_tooltips_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(4)

        layout.addWidget(self._heading('Customize Tooltips'))
        layout.addWidget(self._subtext('Edit the hover text for each button. Leave blank to hide.'))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._tooltips_content = QWidget()
        self._tooltips_layout = QVBoxLayout(self._tooltips_content)
        self._tooltips_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._tooltips_content)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_reset = QPushButton('Reset All to Defaults')
        btn_reset.setStyleSheet(
            f'background-color: {COLORS["red"]}; color: white; padding: 4px 10px;')
        btn_reset.clicked.connect(self._reset_tooltips)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._rebuild_tooltips()
        return page

    def _rebuild_tooltips(self):
        while self._tooltips_layout.count():
            item = self._tooltips_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self._tooltip_editors = {}

        for key in sorted(DEFAULT_TOOLTIPS.keys()):
            row = QWidget()
            row.setStyleSheet(f'background-color: {COLORS["bg_mid"]}; border-radius: 4px;')
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 4, 4)

            label_text = key.replace('_', ' ').title()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(140)
            rl.addWidget(lbl)

            edit = QLineEdit(self._working_tooltips.get(key, DEFAULT_TOOLTIPS.get(key, '')))
            rl.addWidget(edit, 1)

            btn_reset = QPushButton('↺')
            btn_reset.setFixedSize(28, 24)
            default_text = DEFAULT_TOOLTIPS.get(key, '')
            btn_reset.clicked.connect(lambda _, e=edit, dt=default_text: e.setText(dt))
            rl.addWidget(btn_reset)

            self._tooltip_editors[key] = edit
            self._tooltips_layout.addWidget(row)

    def _reset_tooltips(self):
        self._working_tooltips = dict(DEFAULT_TOOLTIPS)
        self._rebuild_tooltips()

    def _commit_tooltips(self):
        for key, edit in self._tooltip_editors.items():
            self._working_tooltips[key] = edit.text()

    # ── Interface tab ────────────────────────────────────

    def _build_interface_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(4)

        layout.addWidget(self._heading('Interface Behaviour'))
        layout.addWidget(self._subtext('Toggle visual cues and animation effects.'))

        row = QWidget()
        row.setStyleSheet(f'background-color: {COLORS["bg_mid"]}; border-radius: 4px;')
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 10, 10, 10)
        rl.addWidget(QLabel('Queue button glow/throb on track selection'))
        rl.addStretch()
        self._chk_queue_throb = QCheckBox()
        self._chk_queue_throb.setChecked(self._working_queue_throb)
        rl.addWidget(self._chk_queue_throb)
        layout.addWidget(row)

        layout.addStretch()
        return page

    # ── Volume tab ───────────────────────────────────────

    def _build_volume_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(6)

        layout.addWidget(self._heading('Volume & Fade'))
        layout.addWidget(self._subtext(
            'Control how the momentum fade behaves when you scroll.'))

        # ── User-friendly section ────────────────────────
        friendly = QGroupBox('Fade Controls')
        friendly.setStyleSheet(
            f'QGroupBox {{ font-weight: bold; color: {COLORS["fg"]}; '
            f'border: 1px solid {COLORS["border"]}; border-radius: 4px; '
            f'margin-top: 8px; padding-top: 14px; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left: 10px; }}')
        fl = QVBoxLayout(friendly)
        fl.setSpacing(6)

        # Fade speed: high-level slider that controls max_interval (inverted)
        # Low max_interval = faster initial fade, high = slower
        self._sl_fade_speed, _ = self._volume_slider_row(
            fl, 'Fade speed',
            'How fast the fade starts. Higher = faster initial fade.',
            1, 100, self._friendly_fade_speed(), '%')
        self._sl_fade_speed.valueChanged.connect(self._on_friendly_fade_speed)

        # Fade max speed: controls min_interval (inverted)
        self._sl_fade_max_speed, _ = self._volume_slider_row(
            fl, 'Maximum speed',
            'Upper speed limit the fade can reach. Higher = faster cap.',
            1, 100, self._friendly_fade_max_speed(), '%')
        self._sl_fade_max_speed.valueChanged.connect(self._on_friendly_max_speed)

        # Scroll sensitivity: controls tick_threshold (inverted)
        self._sl_scroll_sens, _ = self._volume_slider_row(
            fl, 'Scroll sensitivity',
            'How little scroll is needed to register. Higher = more sensitive.',
            1, 100, self._friendly_scroll_sens(), '%')
        self._sl_scroll_sens.valueChanged.connect(self._on_friendly_scroll_sens)

        layout.addWidget(friendly)

        # ── Advanced section (collapsible) ───────────────
        self._adv_toggle = QPushButton('▶ Advanced')
        self._adv_toggle.setStyleSheet(
            f'color: {COLORS["fg_dim"]}; border: none; text-align: left; '
            f'font-size: 11px; padding: 4px 0;')
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.clicked.connect(self._toggle_advanced)
        layout.addWidget(self._adv_toggle)

        self._adv_group = QWidget()
        adv_layout = QVBoxLayout(self._adv_group)
        adv_layout.setContentsMargins(0, 0, 0, 0)
        adv_layout.setSpacing(4)

        self._sl_step, _ = self._volume_slider_row(
            adv_layout, 'Step (vol/tick)', '', 1, 10,
            self._wk_fade_step, '')
        self._sl_step.valueChanged.connect(self._on_adv_step)

        self._sl_min_iv, _ = self._volume_slider_row(
            adv_layout, 'Min interval (cap)', '', 5, 100,
            self._wk_fade_min_interval, 'ms')
        self._sl_min_iv.valueChanged.connect(self._on_adv_min_interval)

        self._sl_max_iv, _ = self._volume_slider_row(
            adv_layout, 'Max interval', '', 50, 500,
            self._wk_fade_max_interval, 'ms')
        self._sl_max_iv.valueChanged.connect(self._on_adv_max_interval)

        self._sl_vel_win, _ = self._volume_slider_row(
            adv_layout, 'Velocity window', '', 100, 2000,
            self._wk_fade_vel_window, 'ms')
        self._sl_vel_win.valueChanged.connect(self._on_adv_vel_window)

        self._sl_vel_lo, _ = self._volume_slider_row(
            adv_layout, 'Velocity low', '', 1, 30,
            int(self._wk_fade_vel_low), 'e/s')
        self._sl_vel_lo.valueChanged.connect(self._on_adv_vel_low)

        self._sl_vel_hi, _ = self._volume_slider_row(
            adv_layout, 'Velocity high', '', 5, 80,
            int(self._wk_fade_vel_high), 'e/s')
        self._sl_vel_hi.valueChanged.connect(self._on_adv_vel_high)

        self._sl_tick_thr, _ = self._volume_slider_row(
            adv_layout, 'Tick threshold', '', 10, 360,
            self._wk_fade_tick_threshold, '°')
        self._sl_tick_thr.valueChanged.connect(self._on_adv_tick_threshold)

        self._adv_group.setVisible(False)
        layout.addWidget(self._adv_group)

        # ── Pull Fader section ───────────────────────────
        pull_group = QGroupBox('Pull Fader')
        pull_group.setStyleSheet(
            f'QGroupBox {{ font-weight: bold; color: {COLORS["fg"]}; '
            f'border: 1px solid {COLORS["border"]}; border-radius: 4px; '
            f'margin-top: 8px; padding-top: 14px; }}'
            f'QGroupBox::title {{ subcontrol-origin: margin; left: 10px; }}')
        pl = QVBoxLayout(pull_group)
        pl.setSpacing(6)

        # ── Simple: min / max fade duration in seconds ──
        # Min fade duration = time at full pull = 100 * min_interval / (step * 1000)
        # Max fade duration = time at tiny pull = 100 * max_interval / (step * 1000)
        self._sl_pull_min_dur, self._lbl_pull_min_dur = self._volume_slider_row(
            pl, 'Fastest fade',
            'Shortest fade duration at full pull (seconds).',
            1, 100, self._pull_min_duration_tenths(), '')
        self._sl_pull_min_dur.valueChanged.connect(self._on_pull_min_duration)
        self._lbl_pull_min_dur.setText(self._format_duration_tenths(
            self._pull_min_duration_tenths()))

        self._sl_pull_max_dur, self._lbl_pull_max_dur = self._volume_slider_row(
            pl, 'Slowest fade',
            'Longest fade duration at smallest pull (seconds).',
            5, 400, self._pull_max_duration_tenths(), '')
        self._sl_pull_max_dur.valueChanged.connect(self._on_pull_max_duration)
        self._lbl_pull_max_dur.setText(self._format_duration_tenths(
            self._pull_max_duration_tenths()))

        # ── Advanced pull-fader toggle ──
        self._pull_adv_toggle = QPushButton('▶ Advanced')
        self._pull_adv_toggle.setStyleSheet(
            f'color: {COLORS["fg_dim"]}; border: none; text-align: left; '
            f'font-size: 11px; padding: 4px 0;')
        self._pull_adv_toggle.setCheckable(True)
        self._pull_adv_toggle.clicked.connect(self._toggle_pull_advanced)
        pl.addWidget(self._pull_adv_toggle)

        self._pull_adv_group = QWidget()
        pull_adv_layout = QVBoxLayout(self._pull_adv_group)
        pull_adv_layout.setContentsMargins(0, 0, 0, 0)
        pull_adv_layout.setSpacing(4)

        self._sl_pull_step, _ = self._volume_slider_row(
            pull_adv_layout, 'Step size',
            'Volume units per fade tick. Higher = coarser steps.',
            1, 10, self._wk_pull_step, '')
        self._sl_pull_step.valueChanged.connect(self._on_pull_step)

        self._sl_pull_min_iv, _ = self._volume_slider_row(
            pull_adv_layout, 'Min interval (cap)',
            'Fastest timer interval at full pull.',
            5, 100, self._wk_pull_min_interval, 'ms')
        self._sl_pull_min_iv.valueChanged.connect(self._on_pull_adv_min_interval)

        self._sl_pull_max_iv, _ = self._volume_slider_row(
            pull_adv_layout, 'Max interval',
            'Slowest timer interval at tiny pull.',
            50, 500, self._wk_pull_max_interval, 'ms')
        self._sl_pull_max_iv.valueChanged.connect(self._on_pull_adv_max_interval)

        self._sl_pull_dz, _ = self._volume_slider_row(
            pull_adv_layout, 'Dead zone',
            'Minimum pull distance (%) before a fade starts.',
            0, 30, self._wk_pull_dead_zone, '%')
        self._sl_pull_dz.valueChanged.connect(self._on_pull_dead_zone)

        self._pull_adv_group.setVisible(False)
        pl.addWidget(self._pull_adv_group)

        layout.addWidget(pull_group)

        layout.addStretch()
        return page

    # ── Volume tab helpers ───────────────────────────────

    def _volume_slider_row(self, layout, label_text, tooltip, min_val, max_val,
                           default, suffix):
        """Add a label + horizontal slider + value readout row.
        Returns (slider, value_label)."""
        row_w = QWidget()
        row_w.setStyleSheet(
            f'background-color: {COLORS["bg_mid"]}; border-radius: 4px;')
        rl = QVBoxLayout(row_w)
        rl.setContentsMargins(10, 6, 10, 6)
        rl.setSpacing(2)

        lbl = QLabel(label_text)
        lbl.setStyleSheet('font-size: 11px; font-weight: bold;')
        if tooltip:
            lbl.setToolTip(tooltip)
        rl.addWidget(lbl)

        sl_row = QHBoxLayout()
        sl_row.setSpacing(6)

        sl = TickSlider(Qt.Horizontal)
        sl.setRange(min_val, max_val)
        sl.setValue(default)
        sl.setFixedHeight(24)
        sl.setTickPosition(TickSlider.TicksBelow)
        sl.setTickInterval(max(1, (max_val - min_val) // 10))
        sl_row.addWidget(sl, stretch=1)

        val_lbl = QLabel()
        val_lbl.setFixedWidth(50)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_lbl.setStyleSheet(
            f'color: {COLORS["accent"]}; font-size: 11px; font-weight: bold;')

        def _update(v):
            val_lbl.setText(f'{v}{suffix}')
        sl.valueChanged.connect(_update)
        _update(default)

        sl_row.addWidget(val_lbl)
        rl.addLayout(sl_row)
        layout.addWidget(row_w)
        return sl, val_lbl

    def _toggle_advanced(self, checked):
        self._adv_group.setVisible(checked)
        self._adv_toggle.setText('▼ Advanced' if checked else '▶ Advanced')

    # ── Friendly ↔ advanced conversions ──────────────────
    # Friendly sliders are 1–100 percentages that map to dev values.

    def _friendly_fade_speed(self):
        """max_interval → friendly %.  500=1%, 50=100%."""
        v = self._wk_fade_max_interval
        return max(1, min(100, int(100 * (500 - v) / (500 - 50))))

    def _friendly_fade_max_speed(self):
        """min_interval → friendly %.  100=1%, 5=100%."""
        v = self._wk_fade_min_interval
        return max(1, min(100, int(100 * (100 - v) / (100 - 5))))

    def _friendly_scroll_sens(self):
        """tick_threshold → friendly %.  360=1%, 10=100%."""
        v = self._wk_fade_tick_threshold
        return max(1, min(100, int(100 * (360 - v) / (360 - 10))))

    def _on_friendly_fade_speed(self, pct):
        """Friendly fade speed → max_interval."""
        val = int(500 - pct * (500 - 50) / 100)
        val = max(50, min(500, val))
        self._wk_fade_max_interval = val
        self._sl_max_iv.blockSignals(True)
        self._sl_max_iv.setValue(val)
        self._sl_max_iv.blockSignals(False)
        self._apply_fade_live()

    def _on_friendly_max_speed(self, pct):
        """Friendly max speed → min_interval."""
        val = int(100 - pct * (100 - 5) / 100)
        val = max(5, min(100, val))
        self._wk_fade_min_interval = val
        self._sl_min_iv.blockSignals(True)
        self._sl_min_iv.setValue(val)
        self._sl_min_iv.blockSignals(False)
        self._apply_fade_live()

    def _on_friendly_scroll_sens(self, pct):
        """Friendly scroll sensitivity → tick_threshold."""
        val = int(360 - pct * (360 - 10) / 100)
        val = max(10, min(360, val))
        self._wk_fade_tick_threshold = val
        self._sl_tick_thr.blockSignals(True)
        self._sl_tick_thr.setValue(val)
        self._sl_tick_thr.blockSignals(False)
        self._apply_fade_live()

    # ── Advanced → friendly sync ─────────────────────────

    def _on_adv_step(self, v):
        self._wk_fade_step = v
        self._apply_fade_live()

    def _on_adv_min_interval(self, v):
        self._wk_fade_min_interval = v
        self._sl_fade_max_speed.blockSignals(True)
        self._sl_fade_max_speed.setValue(self._friendly_fade_max_speed())
        self._sl_fade_max_speed.blockSignals(False)
        self._apply_fade_live()

    def _on_adv_max_interval(self, v):
        self._wk_fade_max_interval = v
        self._sl_fade_speed.blockSignals(True)
        self._sl_fade_speed.setValue(self._friendly_fade_speed())
        self._sl_fade_speed.blockSignals(False)
        self._apply_fade_live()

    def _on_adv_vel_window(self, v):
        self._wk_fade_vel_window = v
        self._apply_fade_live()

    def _on_adv_vel_low(self, v):
        self._wk_fade_vel_low = float(v)
        self._apply_fade_live()

    def _on_adv_vel_high(self, v):
        self._wk_fade_vel_high = float(v)
        self._apply_fade_live()

    def _on_adv_tick_threshold(self, v):
        self._wk_fade_tick_threshold = v
        self._sl_scroll_sens.blockSignals(True)
        self._sl_scroll_sens.setValue(self._friendly_scroll_sens())
        self._sl_scroll_sens.blockSignals(False)
        self._apply_fade_live()

    def _apply_fade_live(self):
        """Push current working fade values to the VolumeStrip in real-time."""
        vs = self._volume_strip
        if vs is None:
            return
        vs.set_fade_step(self._wk_fade_step)
        vs.set_min_interval(self._wk_fade_min_interval)
        vs.set_max_interval(self._wk_fade_max_interval)
        vs.set_velocity_window(self._wk_fade_vel_window)
        vs.set_vel_low(self._wk_fade_vel_low)
        vs.set_vel_high(self._wk_fade_vel_high)
        vs.set_tick_threshold(self._wk_fade_tick_threshold)

    # ── Pull-fader duration conversions ─────────────────
    # Simple sliders work in tenths-of-a-second.
    # Duration = 100 * interval_ms / (step * 1000)  →  interval = duration * step * 10

    @staticmethod
    def _format_duration_tenths(tenths):
        """Format tenths-of-a-second value as e.g. '2.0s' or '15.0s'."""
        return f'{tenths / 10:.1f}s'

    def _pull_min_duration_tenths(self):
        """Fastest fade duration in tenths-of-a-second (full pull → min_interval)."""
        dur = 100.0 * self._wk_pull_min_interval / (self._wk_pull_step * 1000.0)
        return max(1, min(100, int(dur * 10)))

    def _pull_max_duration_tenths(self):
        """Slowest fade duration in tenths-of-a-second (tiny pull → max_interval)."""
        dur = 100.0 * self._wk_pull_max_interval / (self._wk_pull_step * 1000.0)
        return max(5, min(400, int(dur * 10)))

    def _on_pull_min_duration(self, tenths):
        """Simple fastest-fade slider → min_interval."""
        dur_s = tenths / 10.0
        self._lbl_pull_min_dur.setText(f'{dur_s:.1f}s')
        # interval = duration * step * 1000 / 100 = duration * step * 10
        val = max(5, min(100, int(dur_s * self._wk_pull_step * 10)))
        self._wk_pull_min_interval = val
        # Sync advanced slider
        self._sl_pull_min_iv.blockSignals(True)
        self._sl_pull_min_iv.setValue(val)
        self._sl_pull_min_iv.blockSignals(False)
        self._apply_pull_live()

    def _on_pull_max_duration(self, tenths):
        """Simple slowest-fade slider → max_interval."""
        dur_s = tenths / 10.0
        self._lbl_pull_max_dur.setText(f'{dur_s:.1f}s')
        val = max(50, min(500, int(dur_s * self._wk_pull_step * 10)))
        self._wk_pull_max_interval = val
        # Sync advanced slider
        self._sl_pull_max_iv.blockSignals(True)
        self._sl_pull_max_iv.setValue(val)
        self._sl_pull_max_iv.blockSignals(False)
        self._apply_pull_live()

    def _on_pull_adv_min_interval(self, v):
        """Advanced min_interval changed → sync simple slider."""
        self._wk_pull_min_interval = v
        self._sl_pull_min_dur.blockSignals(True)
        self._sl_pull_min_dur.setValue(self._pull_min_duration_tenths())
        self._sl_pull_min_dur.blockSignals(False)
        self._lbl_pull_min_dur.setText(self._format_duration_tenths(
            self._pull_min_duration_tenths()))
        self._apply_pull_live()

    def _on_pull_adv_max_interval(self, v):
        """Advanced max_interval changed → sync simple slider."""
        self._wk_pull_max_interval = v
        self._sl_pull_max_dur.blockSignals(True)
        self._sl_pull_max_dur.setValue(self._pull_max_duration_tenths())
        self._sl_pull_max_dur.blockSignals(False)
        self._lbl_pull_max_dur.setText(self._format_duration_tenths(
            self._pull_max_duration_tenths()))
        self._apply_pull_live()

    def _toggle_pull_advanced(self, checked):
        self._pull_adv_group.setVisible(checked)
        self._pull_adv_toggle.setText(
            '▼ Advanced' if checked else '▶ Advanced')

    def _on_friendly_pull_speed(self, pct):
        """Friendly pull speed → min_interval."""
        val = int(100 - pct * (100 - 5) / 100)
        val = max(5, min(100, val))
        self._wk_pull_min_interval = val
        self._apply_pull_live()

    def _on_friendly_pull_range(self, pct):
        """Friendly pull range → max_interval."""
        val = int(500 - pct * (500 - 50) / 100)
        val = max(50, min(500, val))
        self._wk_pull_max_interval = val
        self._apply_pull_live()

    def _on_pull_step(self, v):
        self._wk_pull_step = v
        self._apply_pull_live()

    def _on_pull_dead_zone(self, v):
        self._wk_pull_dead_zone = v
        self._apply_pull_live()

    def _apply_pull_live(self):
        """Push current pull-fader values to the PullFader in real-time."""
        vs = self._volume_strip
        if vs is None:
            return
        # Access PullFader via the VolumePanel parent
        panel = vs.parent()
        if panel is None:
            return
        pf = getattr(panel, '_pull_fader', None)
        if pf is None:
            return
        pf.set_fade_step(self._wk_pull_step)
        pf.set_min_interval(self._wk_pull_min_interval)
        pf.set_max_interval(self._wk_pull_max_interval)
        pf.set_dead_zone(self._wk_pull_dead_zone)

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _heading(text):
        lbl = QLabel(text)
        lbl.setStyleSheet('font-size: 14px; font-weight: bold; padding: 4px 0;')
        return lbl

    @staticmethod
    def _subtext(text):
        lbl = QLabel(text)
        lbl.setStyleSheet('font-size: 11px; color: #888888; padding: 0 0 4px 0;')
        return lbl

    def _snapshot(self):
        path = self._config.config_path
        if not os.path.exists(path):
            QMessageBox.information(self, 'Snapshot', 'No config file found yet.')
            return
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base, ext = os.path.splitext(path)
        dest = f'{base}_{stamp}{ext}'
        shutil.copy2(path, dest)
        QMessageBox.information(self, 'Snapshot',
                                f'Settings snapshot saved:\n{os.path.basename(dest)}')

    def _show_all_genres(self):
        """Simple list of all detected genres with counts."""
        # Gather counts from parent's playlist
        parent = self.parent()
        genre_counts = {}
        if hasattr(parent, 'playlist'):
            for e in parent.playlist:
                g = e.get('genre', 'Unknown')
                genre_counts[g] = genre_counts.get(g, 0) + 1

        dlg = QDialog(self)
        dlg.setWindowTitle('All Detected Genres')
        dlg.resize(360, 420)
        dlg.setModal(True)
        ly = QVBoxLayout(dlg)
        ly.addWidget(self._heading(f'{len(self._genres)} genres found'))

        lw = QListWidget()
        for g in self._genres:
            count = genre_counts.get(g, 0)
            item = QListWidgetItem(f'{g}  ({count} tracks)')
            lw.addItem(item)
        ly.addWidget(lw, 1)

        btn_close = QPushButton('Close')
        btn_close.clicked.connect(dlg.accept)
        ly.addWidget(btn_close)
        dlg.exec()

    # ── Save ─────────────────────────────────────────────

    def _on_save(self):
        """Commit all working copies back to config and accept."""
        c = self._config

        # Genres
        c.genre_groups = self._working_groups

        # Tags
        c.all_tags = self._working_tags
        c.tag_rows = self._working_tag_rows

        # Length
        self._commit_durations()
        c.length_filter_durations = [
            tuple(d) for d in self._working_durations if d[0].strip()
        ]

        # Tooltips
        self._commit_tooltips()
        c.tooltip_texts = self._working_tooltips

        # Interface
        c.queue_btn_throb_enabled = self._chk_queue_throb.isChecked()

        # Volume fade
        c.fade_step = self._wk_fade_step
        c.fade_min_interval = self._wk_fade_min_interval
        c.fade_max_interval = self._wk_fade_max_interval
        c.fade_vel_window = self._wk_fade_vel_window
        c.fade_vel_low = self._wk_fade_vel_low
        c.fade_vel_high = self._wk_fade_vel_high
        c.fade_tick_threshold = self._wk_fade_tick_threshold

        # Pull-fader
        c.pull_fade_step = self._wk_pull_step
        c.pull_min_interval = self._wk_pull_min_interval
        c.pull_max_interval = self._wk_pull_max_interval
        c.pull_dead_zone = self._wk_pull_dead_zone

        c.save()
        self.accept()
