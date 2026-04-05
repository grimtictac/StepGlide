"""
WaveformSettingsPanel — collapsible tuning panel for waveform display.

Two modes:
  - **Simple**: intuitive sliders (bar size, brightness, height)
  - **Advanced**: raw analysis parameters (gamma, percentile, filter coeffs)

Changes are applied live via the global ``waveform_settings`` singleton.
"""

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QSlider, QSizePolicy, QTabWidget,
    QVBoxLayout, QWidget,
)

from ui.theme import COLORS
from core.waveform import waveform_settings, DEFAULT_A_LO, DEFAULT_A_HI


_LABEL_CSS = f'color:{COLORS["fg_dim"]};font-size:10px;'
_VALUE_CSS = f'color:{COLORS["accent"]};font-size:10px;font-weight:bold;'
_TITLE_CSS = f'color:{COLORS["fg_muted"]};font-size:10px;font-weight:bold;'


def _make_slider_row(layout, label_text, min_val, max_val, default,
                     suffix, callback, *, float_scale=0, tooltip=''):
    """Add a labelled horizontal slider row.  Returns (slider, value_label).

    *float_scale*: if > 0, slider integer value is divided by this to
    produce a float (e.g. float_scale=100 → 0.01 steps).
    """
    row_widget = QWidget()
    row_layout = QVBoxLayout(row_widget)
    row_layout.setContentsMargins(0, 2, 0, 2)
    row_layout.setSpacing(1)

    # Label + value on one line
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
    row_layout.addLayout(top)

    # Slider
    sl = QSlider(Qt.Horizontal)
    sl.setRange(min_val, max_val)
    sl.setValue(default)
    sl.setFixedHeight(18)
    if tooltip:
        sl.setToolTip(tooltip)
    row_layout.addWidget(sl)

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

    layout.addWidget(row_widget)
    return sl, val_lbl


class WaveformSettingsPanel(QWidget):
    """Collapsible waveform tuning panel with Simple / Advanced tabs."""

    # Emitted when visual settings change (bar size, height, alpha)
    # so the scrub bar can refresh immediately
    visual_changed = Signal()
    # Emitted when analysis settings change — requires regeneration
    analysis_changed = Signal()
    # Emitted when height changes specifically
    height_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = waveform_settings
        self._build_ui()

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

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f'color:{COLORS["border"]};')
        outer.addWidget(sep)

        # Tabs: Simple | Advanced
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

    # ── Simple tab ───────────────────────────────────────

    def _build_simple_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        s = self._settings

        _make_slider_row(layout, 'Bar Thickness', 1, 6, s.bar_width, ' px',
                         self._set_bar_width,
                         tooltip='Width of each waveform bar in pixels')

        _make_slider_row(layout, 'Bar Spacing', 0, 4, s.bar_gap, ' px',
                         self._set_bar_gap,
                         tooltip='Gap between bars in pixels')

        _make_slider_row(layout, 'Waveform Height', 30, 120, s.bar_height, ' px',
                         self._set_height,
                         tooltip='Total height of the waveform display')

        _make_slider_row(layout, 'Dynamic Range', 50, 100, int(s.amp_gamma * 100),
                         '', self._set_amp_gamma_simple,
                         float_scale=100,
                         tooltip='Higher = more contrast between loud and quiet.\n'
                                 'Lower = more uniform height.')

        _make_slider_row(layout, 'Colour Intensity', 100, 300, int(s.color_gamma * 100),
                         '', self._set_color_gamma_simple,
                         float_scale=100,
                         tooltip='Higher = more vivid, saturated colours.\n'
                                 'Lower = more blended/pastel.')

        _make_slider_row(layout, 'Unplayed Brightness', 20, 255, s.unplayed_alpha, '',
                         self._set_unplayed_alpha,
                         tooltip='Opacity of the unplayed portion (0=invisible, 255=full)')

        # Adaptive checkbox
        cb_row = QHBoxLayout()
        self._cb_adaptive = QCheckBox('Adaptive frequency bands')
        self._cb_adaptive.setChecked(s.adaptive_bands)
        self._cb_adaptive.setToolTip(
            'Scan each file to find its frequency content\n'
            'and adjust bass/mid/treble colour mapping.\n'
            'When off, uses fixed cutoff frequencies.')
        self._cb_adaptive.toggled.connect(self._set_adaptive)
        self._cb_adaptive.setStyleSheet(f'color:{COLORS["fg_dim"]};font-size:10px;')
        cb_row.addWidget(self._cb_adaptive)
        cb_row.addStretch()
        layout.addLayout(cb_row)

        layout.addStretch()
        return page

    # ── Advanced tab ─────────────────────────────────────

    def _build_advanced_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        s = self._settings

        _make_slider_row(layout, 'Amp Percentile', 50, 99,
                         int(s.amp_percentile * 100), '%',
                         self._set_amp_percentile,
                         float_scale=100,
                         tooltip='Amplitude reference: Nth percentile.\n'
                                 'Higher = more headroom, lower bars.')

        _make_slider_row(layout, 'Amp Gamma', 30, 150,
                         int(s.amp_gamma * 100), '',
                         self._set_amp_gamma,
                         float_scale=100,
                         tooltip='Amplitude power curve.\n'
                                 '0.5=sqrt (compressed), 1.0=linear, >1.0=expanded')

        _make_slider_row(layout, 'Colour Gamma', 50, 400,
                         int(s.color_gamma * 100), '',
                         self._set_color_gamma,
                         float_scale=100,
                         tooltip='Power curve for R/G/B band separation.\n'
                                 'Higher = more saturated dominant colour.')

        # Bass cutoff Hz (derived from a_lo)
        bass_hz = int(s.cutoff_hz(s.a_lo))
        _make_slider_row(layout, 'Bass Cutoff', 50, 800, bass_hz, ' Hz',
                         self._set_bass_cutoff,
                         tooltip='Low-pass filter cutoff for bass band.')

        # Treble cutoff Hz (derived from a_hi)
        treb_hz = int(s.cutoff_hz(s.a_hi))
        _make_slider_row(layout, 'Treble Cutoff', 500, 4000, treb_hz, ' Hz',
                         self._set_treble_cutoff,
                         tooltip='High-pass filter cutoff for treble band.')

        _make_slider_row(layout, 'Played Alpha', 100, 255, s.played_alpha, '',
                         self._set_played_alpha,
                         tooltip='Opacity of the played portion')

        _make_slider_row(layout, 'Unplayed Alpha', 20, 255, s.unplayed_alpha, '',
                         self._set_unplayed_alpha_adv,
                         tooltip='Opacity of the unplayed portion')

        # Reset button
        btn_reset = QPushButton('Reset to Defaults')
        btn_reset.setFixedHeight(26)
        btn_reset.setStyleSheet(f'font-size:10px; padding: 2px 8px;')
        btn_reset.clicked.connect(self._reset_defaults)
        layout.addWidget(btn_reset)

        layout.addStretch()
        return page

    # ── Collapse toggle ──────────────────────────────────

    def _toggle_collapse(self):
        self._content_visible = not self._content_visible
        self._tabs.setVisible(self._content_visible)
        self._btn_collapse.setText('\u25b2' if self._content_visible else '\u25bc')

    # ── Simple tab callbacks ─────────────────────────────

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
        self.analysis_changed.emit()

    def _set_color_gamma_simple(self, v):
        self._settings.color_gamma = v
        self.analysis_changed.emit()

    def _set_unplayed_alpha(self, v):
        self._settings.unplayed_alpha = int(v)
        self.visual_changed.emit()

    def _set_adaptive(self, checked):
        self._settings.adaptive_bands = checked
        self.analysis_changed.emit()

    # ── Advanced tab callbacks ───────────────────────────

    def _set_amp_percentile(self, v):
        self._settings.amp_percentile = v
        self.analysis_changed.emit()

    def _set_amp_gamma(self, v):
        self._settings.amp_gamma = v
        self.analysis_changed.emit()

    def _set_color_gamma(self, v):
        self._settings.color_gamma = v
        self.analysis_changed.emit()

    def _set_bass_cutoff(self, hz):
        self._settings.a_lo = self._settings.alpha_from_hz(hz)
        self._settings.adaptive_bands = False
        self._cb_adaptive.setChecked(False)
        self.analysis_changed.emit()

    def _set_treble_cutoff(self, hz):
        self._settings.a_hi = self._settings.alpha_from_hz(hz)
        self._settings.adaptive_bands = False
        self._cb_adaptive.setChecked(False)
        self.analysis_changed.emit()

    def _set_played_alpha(self, v):
        self._settings.played_alpha = int(v)
        self.visual_changed.emit()

    def _set_unplayed_alpha_adv(self, v):
        self._settings.unplayed_alpha = int(v)
        self.visual_changed.emit()

    def _reset_defaults(self):
        """Reset all settings to defaults."""
        from core.waveform import (DEFAULT_A_LO, DEFAULT_A_HI,
                                   DEFAULT_AMP_PERCENTILE, DEFAULT_AMP_GAMMA,
                                   DEFAULT_COLOR_GAMMA)
        s = self._settings
        s.adaptive_bands = True
        s.a_lo = DEFAULT_A_LO
        s.a_hi = DEFAULT_A_HI
        s.amp_percentile = DEFAULT_AMP_PERCENTILE
        s.amp_gamma = DEFAULT_AMP_GAMMA
        s.color_gamma = DEFAULT_COLOR_GAMMA
        s.bar_width = 2
        s.bar_gap = 1
        s.bar_height = 60
        s.played_alpha = 255
        s.unplayed_alpha = 80
        self.analysis_changed.emit()
        self.visual_changed.emit()
        self.height_changed.emit(60)
