"""
WaveformSettingsPanel -- collapsible tuning panel for waveform display.

Two tabs:
  - **Simple**: intuitive sliders (draw style, bar size, brightness, height)
  - **Advanced**: raw analysis parameters (gamma, percentile, crossover Hz)

Changes are applied live via the global ``waveform_settings`` singleton.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QSlider, QSizePolicy, QTabWidget,
    QVBoxLayout, QWidget,
)

from ui.theme import COLORS
from core.waveform import (
    waveform_settings,
    DEFAULT_BASS_FC, DEFAULT_TREBLE_FC,
    DEFAULT_AMP_PERCENTILE, DEFAULT_AMP_GAMMA, DEFAULT_COLOR_GAMMA,
)


_LABEL_CSS = f'color:{COLORS["fg_dim"]};font-size:10px;'
_VALUE_CSS = f'color:{COLORS["accent"]};font-size:10px;font-weight:bold;'
_TITLE_CSS = f'color:{COLORS["fg_muted"]};font-size:10px;font-weight:bold;'


def _make_slider_row(layout, label_text, min_val, max_val, default,
                     suffix, callback, *, float_scale=0, tooltip=''):
    """Add a labelled horizontal slider row.  Returns (slider, value_label)."""
    row = QWidget()
    rl = QVBoxLayout(row)
    rl.setContentsMargins(0, 2, 0, 2)
    rl.setSpacing(1)

    top = QHBoxLayout()
    top.setSpacing(4)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(_LABEL_CSS)
    if tooltip:
        lbl.setToolTip(tooltip)
    top.addWidget(lbl)
    top.addStretch()
    val_lbl = QLabel()
    val_lbl.setStyleSheet(_VALUE_CSS)
    val_lbl.setFixedWidth(60)
    val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    top.addWidget(val_lbl)
    rl.addLayout(top)

    sl = QSlider(Qt.Horizontal)
    sl.setRange(min_val, max_val)
    sl.setValue(default)
    sl.setFixedHeight(18)
    if tooltip:
        sl.setToolTip(tooltip)
    rl.addWidget(sl)

    def _on_change(v):
        if float_scale:
            real = v / float_scale
            decimals = max(1, len(str(float_scale)) - 1)
            val_lbl.setText(f'{real:.{decimals}f}{suffix}')
            callback(real)
        else:
            val_lbl.setText(f'{v}{suffix}')
            callback(v)

    sl.valueChanged.connect(_on_change)
    _on_change(default)

    layout.addWidget(row)
    return sl, val_lbl


class WaveformSettingsPanel(QWidget):
    """Collapsible waveform tuning panel with Simple / Advanced tabs."""

    visual_changed = Signal()       # rebin + repaint only
    analysis_changed = Signal()     # requires waveform regeneration
    height_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = waveform_settings

        # Debounce: only fire analysis_changed after 400 ms idle
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setSingleShot(True)
        self._analysis_timer.setInterval(400)
        self._analysis_timer.timeout.connect(self.analysis_changed.emit)

        self._build_ui()

    # ---- debounce helper ----------------------------------------

    def _debounce_analysis(self):
        """Restart the debounce timer."""
        self._analysis_timer.start()

    # ---- UI construction ----------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(4)

        # Title bar with collapse toggle
        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        title_lbl = QLabel('\U0001f4ca Waveform Settings')
        title_lbl.setStyleSheet(_TITLE_CSS)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        self._btn_collapse = QPushButton('\u25b2')
        self._btn_collapse.setFixedSize(24, 20)
        self._btn_collapse.setStyleSheet('border: none; font-size: 10px;')
        self._btn_collapse.clicked.connect(self._toggle_collapse)
        title_row.addWidget(self._btn_collapse)
        outer.addLayout(title_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f'color:{COLORS["border"]};')
        outer.addWidget(sep)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f'QTabWidget::pane {{ border: 1px solid {COLORS["border"]}; '
            f'background: {COLORS["bg"]}; }}'
            f'QTabBar::tab {{ padding: 4px 12px; font-size: 10px; }}'
            f'QTabBar::tab:selected {{ background: {COLORS["accent"]}; color: white; }}'
        )
        self._tabs.addTab(self._build_simple_tab(), 'Simple')
        self._tabs.addTab(self._build_advanced_tab(), 'Advanced')
        outer.addWidget(self._tabs)
        self._content_visible = True

    # ---- Simple tab ---------------------------------------------

    def _build_simple_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        s = self._settings

        # Draw mode selector
        mode_row = QHBoxLayout()
        lbl = QLabel('Draw Style')
        lbl.setStyleSheet(_LABEL_CSS)
        mode_row.addWidget(lbl)
        mode_row.addStretch()
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(['Bars', 'Envelope'])
        self._mode_combo.setCurrentIndex(0 if s.draw_mode == 'bars' else 1)
        self._mode_combo.setFixedWidth(100)
        self._mode_combo.setFixedHeight(22)
        self._mode_combo.setStyleSheet('font-size:10px;')
        self._mode_combo.setToolTip(
            'Bars: discrete vertical bars (Serato style)\n'
            'Envelope: smooth filled waveform (SoundCloud style)')
        self._mode_combo.currentIndexChanged.connect(self._set_draw_mode)
        mode_row.addWidget(self._mode_combo)
        layout.addLayout(mode_row)

        _make_slider_row(layout, 'Bar Thickness', 1, 6, s.bar_width, ' px',
                         self._set_bar_width,
                         tooltip='Width of each waveform bar in pixels')
        _make_slider_row(layout, 'Bar Spacing', 0, 4, s.bar_gap, ' px',
                         self._set_bar_gap,
                         tooltip='Gap between bars in pixels')
        _make_slider_row(layout, 'Waveform Height', 30, 120, s.bar_height, ' px',
                         self._set_height,
                         tooltip='Total height of the waveform display')
        _make_slider_row(layout, 'Dynamic Range', 30, 100,
                         int(s.amp_gamma * 100), '',
                         self._set_amp_gamma_simple, float_scale=100,
                         tooltip='Lower = more compressed (quiet parts visible)\n'
                                 'Higher = more contrast between loud and quiet')
        _make_slider_row(layout, 'Colour Intensity', 100, 400,
                         int(s.color_gamma * 100), '',
                         self._set_color_gamma_simple, float_scale=100,
                         tooltip='Higher = more vivid, saturated colours\n'
                                 'Lower = more blended/pastel')
        _make_slider_row(layout, 'Unplayed Brightness', 20, 255,
                         s.unplayed_alpha, '',
                         self._set_unplayed_alpha,
                         tooltip='Opacity of the unplayed portion')
        layout.addStretch()
        return page

    # ---- Advanced tab -------------------------------------------

    def _build_advanced_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        s = self._settings

        _make_slider_row(layout, 'Bass Crossover', 100, 600,
                         s.bass_fc, ' Hz',
                         self._set_bass_fc,
                         tooltip='Low-pass crossover frequency for the bass (red) band')
        _make_slider_row(layout, 'Treble Crossover', 800, 3500,
                         s.treble_fc, ' Hz',
                         self._set_treble_fc,
                         tooltip='High-pass crossover frequency for the treble (blue) band')
        _make_slider_row(layout, 'Amp Percentile', 50, 99,
                         int(s.amp_percentile * 100), '%',
                         self._set_amp_percentile, float_scale=100,
                         tooltip='Amplitude reference: Nth percentile of RMS.\n'
                                 'Lower = more dynamic range, taller peaks.')
        _make_slider_row(layout, 'Amp Gamma', 30, 150,
                         int(s.amp_gamma * 100), '',
                         self._set_amp_gamma, float_scale=100,
                         tooltip='Amplitude power curve.\n'
                                 '0.5 = sqrt (compressed), 1.0 = linear')
        _make_slider_row(layout, 'Colour Gamma', 100, 500,
                         int(s.color_gamma * 100), '',
                         self._set_color_gamma, float_scale=100,
                         tooltip='Power curve for R/G/B band separation.\n'
                                 'Higher = more saturated dominant colour.')
        _make_slider_row(layout, 'Played Alpha', 100, 255,
                         s.played_alpha, '',
                         self._set_played_alpha,
                         tooltip='Opacity of the played portion')
        _make_slider_row(layout, 'Unplayed Alpha', 20, 255,
                         s.unplayed_alpha, '',
                         self._set_unplayed_alpha_adv,
                         tooltip='Opacity of the unplayed portion')

        btn_reset = QPushButton('Reset to Defaults')
        btn_reset.setFixedHeight(26)
        btn_reset.setStyleSheet('font-size:10px; padding: 2px 8px;')
        btn_reset.clicked.connect(self._reset_defaults)
        layout.addWidget(btn_reset)

        layout.addStretch()
        return page

    # ---- Collapse toggle ----------------------------------------

    def _toggle_collapse(self):
        self._content_visible = not self._content_visible
        self._tabs.setVisible(self._content_visible)
        self._btn_collapse.setText('\u25b2' if self._content_visible else '\u25bc')

    # ---- Simple tab callbacks -----------------------------------

    def _set_draw_mode(self, index):
        self._settings.draw_mode = 'bars' if index == 0 else 'envelope'
        self.visual_changed.emit()

    def _set_bar_width(self, v):
        self._settings.bar_width = int(v)
        self.visual_changed.emit()

    def _set_bar_gap(self, v):
        self._settings.bar_gap = int(v)
        self.visual_changed.emit()

    def _set_height(self, v):
        self._settings.bar_height = int(v)
        self.height_changed.emit(int(v))
        self.visual_changed.emit()

    def _set_amp_gamma_simple(self, v):
        self._settings.amp_gamma = v
        self._debounce_analysis()

    def _set_color_gamma_simple(self, v):
        self._settings.color_gamma = v
        self._debounce_analysis()

    def _set_unplayed_alpha(self, v):
        self._settings.unplayed_alpha = int(v)
        self.visual_changed.emit()

    # ---- Advanced tab callbacks ---------------------------------

    def _set_bass_fc(self, hz):
        self._settings.bass_fc = int(hz)
        self._debounce_analysis()

    def _set_treble_fc(self, hz):
        self._settings.treble_fc = int(hz)
        self._debounce_analysis()

    def _set_amp_percentile(self, v):
        self._settings.amp_percentile = v
        self._debounce_analysis()

    def _set_amp_gamma(self, v):
        self._settings.amp_gamma = v
        self._debounce_analysis()

    def _set_color_gamma(self, v):
        self._settings.color_gamma = v
        self._debounce_analysis()

    def _set_played_alpha(self, v):
        self._settings.played_alpha = int(v)
        self.visual_changed.emit()

    def _set_unplayed_alpha_adv(self, v):
        self._settings.unplayed_alpha = int(v)
        self.visual_changed.emit()

    # ---- Reset --------------------------------------------------

    def _reset_defaults(self):
        """Reset all settings to factory defaults."""
        s = self._settings
        s.bass_fc = DEFAULT_BASS_FC
        s.treble_fc = DEFAULT_TREBLE_FC
        s.amp_percentile = DEFAULT_AMP_PERCENTILE
        s.amp_gamma = DEFAULT_AMP_GAMMA
        s.color_gamma = DEFAULT_COLOR_GAMMA
        s.draw_mode = 'bars'
        s.bar_width = 2
        s.bar_gap = 1
        s.bar_height = 60
        s.played_alpha = 255
        s.unplayed_alpha = 80
        self.analysis_changed.emit()
        self.visual_changed.emit()
        self.height_changed.emit(60)
