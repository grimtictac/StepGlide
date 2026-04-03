"""
Settings dialog — tabbed dialog for genre groups, tags, length filters,
tooltips, and interface options.
"""

import os
import shutil
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from core.config import DEFAULT_TOOLTIPS
from ui.theme import COLORS

import qtawesome as qta


class SettingsDialog(QDialog):
    """Tabbed settings dialog matching the five original tabs."""

    def __init__(self, parent, *, config, db, genres):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.resize(580, 620)
        self.setModal(True)

        self._config = config
        self._db = db
        self._genres = sorted(genres)

        # Working copies so Cancel discards changes
        self._working_groups = {k: list(v) for k, v in config.genre_groups.items()}
        self._working_tags = set(config.all_tags)
        self._working_tag_rows = dict(config.tag_rows)
        self._working_durations = [list(d) for d in config.length_filter_durations]
        self._working_tooltips = dict(config.tooltip_texts)
        self._working_queue_throb = config.queue_btn_throb_enabled

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

        for label in ('Genres', 'Tags', 'Length', 'Tooltips', 'Interface'):
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

    def _show_tab(self, name):
        idx_map = {'Genres': 0, 'Tags': 1, 'Length': 2, 'Tooltips': 3, 'Interface': 4}
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

        c.save()
        self.accept()
