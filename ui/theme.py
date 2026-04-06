"""
Dark theme QSS stylesheet for the music player.
"""

DARK_THEME = """
/* ── Global ─────────────────────────────────── */
QMainWindow, QWidget {
    background-color: #242424;
    color: #dce4ee;
    font-family: "Segoe UI", "Ubuntu", sans-serif;
    font-size: 11px;
}

/* ── Menu bar ────────────────────────────────── */
QMenuBar {
    background-color: #1e1e2e;
    color: #dce4ee;
    border-bottom: 1px solid #333333;
}
QMenuBar::item:selected {
    background-color: #1f6aa5;
}
QMenu {
    background-color: #2b2b2b;
    color: #dce4ee;
    border: 1px solid #444444;
}
QMenu::item:selected {
    background-color: #1f6aa5;
}
QMenu::separator {
    height: 1px;
    background-color: #444444;
    margin: 4px 8px;
}

/* ── Push buttons ────────────────────────────── */
QPushButton {
    background-color: #3b3b3b;
    color: #dce4ee;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 4px 12px;
    min-height: 24px;
}
QPushButton:hover {
    background-color: #4a4a4a;
    border-color: #1f6aa5;
}
QPushButton:pressed {
    background-color: #1f6aa5;
}
QPushButton:disabled {
    background-color: #2b2b2b;
    color: #666666;
}

/* ── Tool buttons (icon buttons) ─────────────── */
QToolButton {
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 4px;
    color: #dce4ee;
}
QToolButton:hover {
    background-color: #3b3b3b;
}
QToolButton:pressed {
    background-color: #1f6aa5;
}

/* ── Table view (track listing) ──────────────── */
QTableView {
    background-color: #2b2b2b;
    alternate-background-color: #303030;
    color: #dce4ee;
    gridline-color: #3b3b3b;
    border: none;
    selection-background-color: #1f6aa5;
    selection-color: #ffffff;
}
QTableView::item {
    padding: 4px 8px;
    border: none;
}
QTableView::item:hover {
    background-color: #353535;
}

QHeaderView::section {
    background-color: #3b3b3b;
    color: #dce4ee;
    border: none;
    border-right: 1px solid #4a4a4a;
    border-bottom: 1px solid #4a4a4a;
    padding: 6px 8px;
    font-weight: bold;
}
QHeaderView::section:hover {
    background-color: #4a4a4a;
}
QHeaderView::down-arrow {
    image: none;
    width: 0; height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #80f0ff;
    margin-right: 6px;
    subcontrol-position: center right;
}
QHeaderView::up-arrow {
    image: none;
    width: 0; height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-bottom: 6px solid #80f0ff;
    margin-right: 6px;
    subcontrol-position: center right;
}

/* ── Tree view (genre list, play log) ────────── */
QTreeView, QTreeWidget {
    background-color: #2b2b2b;
    color: #dce4ee;
    border: none;
    selection-background-color: #1f6aa5;
    selection-color: #ffffff;
}
QTreeView::item {
    padding: 3px;
}
QTreeView::item:hover {
    background-color: #353535;
}

/* ── List view / widget ──────────────────────── */
QListView, QListWidget {
    background-color: #2b2b2b;
    color: #dce4ee;
    border: none;
    selection-background-color: #1f6aa5;
    selection-color: #ffffff;
}
QListWidget::item {
    padding: 4px 8px;
}
QListWidget::item:hover {
    background-color: #353535;
}

/* ── Scrollbars ──────────────────────────────── */
QScrollBar:vertical {
    background-color: #2b2b2b;
    width: 12px;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: #555555;
    min-height: 30px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #777777;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background-color: #2b2b2b;
    height: 12px;
    border: none;
}
QScrollBar::handle:horizontal {
    background-color: #555555;
    min-width: 30px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #777777;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Sliders ─────────────────────────────────── */
QSlider::groove:horizontal {
    height: 6px;
    background-color: #3b3b3b;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -5px 0;
    background-color: #00bcd4;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover {
    background-color: #80f0ff;
}
QSlider::sub-page:horizontal {
    background-color: #00bcd4;
    border-radius: 3px;
}

QSlider::groove:vertical {
    width: 6px;
    background-color: #3b3b3b;
    border-radius: 3px;
}
QSlider::handle:vertical {
    height: 16px;
    width: 16px;
    margin: 0 -5px;
    background-color: #00bcd4;
    border-radius: 8px;
}
QSlider::handle:vertical:hover {
    background-color: #80f0ff;
}
QSlider::sub-page:vertical {
    background-color: #00bcd4;
    border-radius: 3px;
}

/* ── Line edits / search ─────────────────────── */
QLineEdit {
    background-color: #1e1e2e;
    color: #dce4ee;
    border: 1px solid #444444;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #1f6aa5;
}
QLineEdit:focus {
    border-color: #1f6aa5;
}

/* ── Combo boxes ─────────────────────────────── */
QComboBox {
    background-color: #3b3b3b;
    color: #dce4ee;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 24px;
}
QComboBox:hover {
    border-color: #1f6aa5;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #2b2b2b;
    color: #dce4ee;
    selection-background-color: #1f6aa5;
    border: 1px solid #444444;
}

/* ── Labels ──────────────────────────────────── */
QLabel {
    color: #dce4ee;
    background-color: transparent;
}

/* ── Splitters ───────────────────────────────── */
QSplitter::handle {
    background-color: #3b3b3b;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}
QSplitter::handle:hover {
    background-color: #1f6aa5;
}

/* ── Progress bar ────────────────────────────── */
QProgressBar {
    background-color: #3b3b3b;
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #1f6aa5;
    border-radius: 3px;
}

/* ── Tab widget ──────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #3b3b3b;
}
QTabBar::tab {
    background-color: #2b2b2b;
    color: #aaaaaa;
    padding: 6px 16px;
    border: 1px solid #3b3b3b;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background-color: #3b3b3b;
    color: #dce4ee;
}
QTabBar::tab:hover {
    background-color: #4a4a4a;
}

/* ── Tooltips ────────────────────────────────── */
QToolTip {
    background-color: #333333;
    color: #eeeeee;
    border: 1px solid #555555;
    padding: 4px 8px;
    font-size: 10px;
}

/* ── Status bar ──────────────────────────────── */
QStatusBar {
    background-color: #1e1e2e;
    color: #aaaaaa;
    border-top: 1px solid #333333;
}

/* ── Dialog buttons ──────────────────────────── */
QDialogButtonBox QPushButton {
    min-width: 80px;
}
"""

# Named colours for programmatic use
COLORS = {
    'bg': '#242424',
    'bg_dark': '#1e1e2e',
    'bg_mid': '#2b2b2b',
    'bg_light': '#3b3b3b',
    'fg': '#dce4ee',
    'fg_dim': '#aaaaaa',
    'fg_muted': '#888888',
    'fg_very_dim': '#666666',
    'accent': '#1f6aa5',
    'accent_hover': '#1a5a8a',
    'cyan': '#00bcd4',
    'cyan_bright': '#80f0ff',
    'green': '#27ae60',
    'green_hover': '#2ecc71',
    'green_text': '#5dff5d',
    'red': '#c0392b',
    'red_hover': '#e74c3c',
    'red_text': '#ff5d5d',
    'yellow': '#f1c40f',
    'yellow_hover': '#f39c12',
    'orange': '#ff9800',
    'border': '#444444',
    'now_playing_bg': '#1a3a1a',
    'now_playing_fg': '#5dff5d',
}
