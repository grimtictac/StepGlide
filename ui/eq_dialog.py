"""
Equalizer dialog — 10-band graphic EQ with presets, per-track persistence.
"""

import vlc

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from ui.theme import COLORS


# VLC 10-band frequencies
EQ_BANDS = [
    '60 Hz', '170 Hz', '310 Hz', '600 Hz', '1 kHz',
    '3 kHz', '6 kHz', '12 kHz', '14 kHz', '16 kHz',
]

EQ_PRESETS = {
    'Flat':         (0, [0]*10),
    'Bass Boost':   (2, [6, 5, 3, 1, 0, 0, 0, 0, 0, 0]),
    'Treble Boost': (2, [0, 0, 0, 0, 0, 1, 3, 5, 6, 6]),
    'Rock':         (1, [5, 3, 0, -2, -3, -2, 0, 3, 4, 5]),
    'Pop':          (0, [-1, 2, 4, 4, 2, 0, -1, -1, -1, -1]),
    'Jazz':         (0, [3, 2, 0, 1, -1, -1, 0, 1, 2, 3]),
    'Classical':    (0, [4, 3, 2, 1, -1, -1, 0, 2, 3, 4]),
    'Dance':        (1, [5, 4, 2, 0, 0, -2, -3, -2, 0, 0]),
    'Latin':        (1, [3, 1, 0, 0, -2, -2, -2, 0, 3, 4]),
    'Vocal':        (0, [-2, -1, 0, 3, 5, 5, 3, 0, -1, -2]),
    'Loudness':     (3, [5, 3, 0, 0, -1, 0, 0, -3, 5, 3]),
    'Headphones':   (1, [3, 4, 2, -1, -2, -1, 1, 3, 5, 5]),
}


class EqualizerDialog(QDialog):
    """10-band graphic EQ dialog with presets and per-track save."""

    def __init__(self, parent, *, db, vlc_player, track_path, track_title):
        super().__init__(parent)
        self.setWindowTitle('Equalizer')
        self.resize(560, 440)
        self.setModal(True)

        self._db = db
        self._vlc_player = vlc_player
        self._track_path = track_path
        self._track_id = db.get_track_id(track_path) if track_path else None

        self._band_sliders = []
        self._band_labels = []

        self._build_ui(track_title)
        self._load_current()

    # ── UI ───────────────────────────────────────────────

    def _build_ui(self, track_title):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # Header
        lbl_title = QLabel(f'EQ: {track_title[:60]}' if track_title else 'No track playing')
        lbl_title.setStyleSheet('font-size: 13px; font-weight: bold; padding: 4px;')
        lbl_title.setAlignment(Qt.AlignCenter)
        root.addWidget(lbl_title)

        # Preset row
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel('Preset:'))
        self._preset_combo = QComboBox()
        self._preset_combo.addItems(list(EQ_PRESETS.keys()) + ['Custom'])
        self._preset_combo.currentTextChanged.connect(self._on_preset_selected)
        preset_row.addWidget(self._preset_combo)
        preset_row.addStretch()
        root.addLayout(preset_row)

        # Preamp
        pa_row = QHBoxLayout()
        pa_row.addWidget(QLabel('Preamp'))
        self._preamp_slider = QSlider(Qt.Horizontal)
        self._preamp_slider.setRange(-200, 200)  # ×10 for 0.1 resolution
        self._preamp_slider.setValue(0)
        self._preamp_slider.valueChanged.connect(self._on_slider_change)
        pa_row.addWidget(self._preamp_slider, 1)
        self._preamp_label = QLabel('0 dB')
        self._preamp_label.setFixedWidth(50)
        pa_row.addWidget(self._preamp_label)
        root.addLayout(pa_row)

        # Band sliders (vertical)
        bands_widget = QWidget()
        bands_layout = QHBoxLayout(bands_widget)
        bands_layout.setContentsMargins(4, 4, 4, 4)
        bands_layout.setSpacing(2)

        for i, freq in enumerate(EQ_BANDS):
            col = QVBoxLayout()
            col.setSpacing(2)

            val_lbl = QLabel('0')
            val_lbl.setAlignment(Qt.AlignCenter)
            val_lbl.setFixedWidth(36)
            val_lbl.setStyleSheet('font-size: 10px;')
            self._band_labels.append(val_lbl)
            col.addWidget(val_lbl, 0, Qt.AlignCenter)

            slider = QSlider(Qt.Vertical)
            slider.setRange(-200, 200)
            slider.setValue(0)
            slider.setFixedWidth(28)
            slider.setMinimumHeight(180)
            slider.valueChanged.connect(self._on_slider_change)
            self._band_sliders.append(slider)
            col.addWidget(slider, 1, Qt.AlignCenter)

            freq_lbl = QLabel(freq)
            freq_lbl.setAlignment(Qt.AlignCenter)
            freq_lbl.setStyleSheet('font-size: 9px; color: #888888;')
            col.addWidget(freq_lbl, 0, Qt.AlignCenter)

            bands_layout.addLayout(col)

        root.addWidget(bands_widget, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_reset = QPushButton('Reset')
        btn_reset.setStyleSheet(
            f'background-color: {COLORS["red"]}; color: white; padding: 6px 14px;')
        btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(btn_reset)

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

    # ── Data helpers ─────────────────────────────────────

    def _get_preamp(self):
        return self._preamp_slider.value() / 10.0

    def _get_bands(self):
        return [s.value() / 10.0 for s in self._band_sliders]

    def _set_preamp(self, val):
        self._preamp_slider.blockSignals(True)
        self._preamp_slider.setValue(int(val * 10))
        self._preamp_slider.blockSignals(False)

    def _set_bands(self, bands):
        for i, val in enumerate(bands):
            if i < len(self._band_sliders):
                self._band_sliders[i].blockSignals(True)
                self._band_sliders[i].setValue(int(val * 10))
                self._band_sliders[i].blockSignals(False)

    def _update_labels(self):
        pa = self._get_preamp()
        self._preamp_label.setText(f'{pa:.0f} dB')
        for i, s in enumerate(self._band_sliders):
            self._band_labels[i].setText(f'{s.value() / 10.0:.0f}')

    def _detect_preset(self):
        pa = self._get_preamp()
        bands = self._get_bands()
        for name, (p, b) in EQ_PRESETS.items():
            if abs(pa - p) < 0.5 and all(abs(a - bv) < 0.5 for a, bv in zip(bands, b)):
                self._preset_combo.blockSignals(True)
                self._preset_combo.setCurrentText(name)
                self._preset_combo.blockSignals(False)
                return
        self._preset_combo.blockSignals(True)
        self._preset_combo.setCurrentText('Custom')
        self._preset_combo.blockSignals(False)

    # ── Load / apply ─────────────────────────────────────

    def _load_current(self):
        """Load saved EQ for this track, or default to Flat."""
        if self._track_id is not None:
            row = self._db.load_track_eq(self._track_id)
            if row:
                preamp = float(row[0])
                bands_str = row[1] or ''
                bands = [float(x) for x in bands_str.split(',') if x.strip()]
                if len(bands) != 10:
                    bands = [0] * 10
                self._set_preamp(preamp)
                self._set_bands(bands)
                self._update_labels()
                self._detect_preset()
                return
        # Default flat
        self._preset_combo.setCurrentText('Flat')
        self._update_labels()

    def _apply_live(self):
        """Apply current slider values to VLC in real time."""
        try:
            mp = self._vlc_player.get_media_player()
            pa = self._get_preamp()
            bands = self._get_bands()
            if pa == 0 and all(b == 0 for b in bands):
                mp.set_equalizer(None)
            else:
                eq = vlc.AudioEqualizer()
                eq.set_preamp(pa)
                for i, val in enumerate(bands):
                    eq.set_amp_at_index(val, i)
                mp.set_equalizer(eq)
        except Exception:
            pass

    # ── Slots ────────────────────────────────────────────

    def _on_slider_change(self, _=None):
        self._update_labels()
        self._detect_preset()
        self._apply_live()

    def _on_preset_selected(self, name):
        if name == 'Custom':
            return
        preamp, bands = EQ_PRESETS.get(name, (0, [0] * 10))
        self._set_preamp(preamp)
        self._set_bands(bands)
        self._update_labels()
        self._apply_live()

    def _on_reset(self):
        """Reset to flat and remove from DB."""
        self._set_preamp(0)
        self._set_bands([0] * 10)
        self._update_labels()
        self._preset_combo.setCurrentText('Flat')
        self._apply_live()
        if self._track_id is not None:
            self._db.delete_track_eq(self._track_id)

    def _on_save(self):
        """Persist EQ to DB and close."""
        if self._track_id is not None:
            pa = round(self._get_preamp(), 1)
            bands = [round(b, 1) for b in self._get_bands()]
            if pa == 0 and all(b == 0 for b in bands):
                self._db.delete_track_eq(self._track_id)
            else:
                bands_str = ','.join(f'{b:.1f}' for b in bands)
                self._db.save_track_eq(self._track_id, pa, bands_str)
        self.accept()


# ── Standalone helper for applying EQ without dialog ─────

def apply_eq_for_track(db, vlc_player, track_path):
    """Load EQ settings from DB for *track_path* and apply to VLC.
    Returns True if a custom EQ was applied, False if reset to flat."""
    if not track_path:
        return False
    track_id = db.get_track_id(track_path)
    if track_id is None:
        return False
    row = db.load_track_eq(track_id)
    try:
        mp = vlc_player.get_media_player()
        if row:
            preamp = float(row[0])
            bands_str = row[1] or ''
            bands = [float(x) for x in bands_str.split(',') if x.strip()]
            if len(bands) != 10:
                bands = [0] * 10
            eq = vlc.AudioEqualizer()
            eq.set_preamp(preamp)
            for i, val in enumerate(bands):
                eq.set_amp_at_index(val, i)
            mp.set_equalizer(eq)
            return True
        else:
            mp.set_equalizer(None)
            return False
    except Exception:
        return False
