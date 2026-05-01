"""
AudioDeviceDebugDialog — live status view of OutputManager devices with
manual override controls (force absent, force scan, restore overrides).

This is intentionally a developer/power-user tool: it exposes the
internal state of OutputManager and lets you simulate hot-plug events
without physically touching the hardware.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class AudioDeviceDebugDialog(QDialog):
    """Modeless dialog showing the current OutputManager device state."""

    def __init__(self, output_manager, parent=None):
        super().__init__(parent)
        self._mgr = output_manager
        self.setWindowTitle('Audio Device Debug')
        self.setMinimumSize(720, 360)

        layout = QVBoxLayout(self)

        intro = QLabel(
            'Live view of OutputManager device list.  Refreshes every 1s; '
            'use "Force Scan Now" to re-enumerate from libvlc immediately.\n\n'
            'Toggling "Forced Absent" simulates an unplug WITHOUT touching '
            'the hardware — handy for testing routing fall-back logic.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Tree of devices
        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels(
            ['Description', 'Device ID', 'Present', 'Forced Absent', 'Test'])
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        layout.addWidget(self._tree, stretch=1)

        # Action buttons row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        self._btn_scan = QPushButton('Force Scan Now')
        self._btn_scan.clicked.connect(self._on_force_scan)
        btn_row.addWidget(self._btn_scan)

        self._btn_clear = QPushButton('Clear All Overrides')
        self._btn_clear.clicked.connect(self._on_clear_overrides)
        btn_row.addWidget(self._btn_clear)

        self._btn_beep_all = QPushButton('🔊 Beep All Present')
        self._btn_beep_all.clicked.connect(self._on_beep_all)
        btn_row.addWidget(self._btn_beep_all)

        btn_row.addStretch()
        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        # Refresh on changes from the manager itself
        self._mgr.devices_changed.connect(self._refresh)

        # Soft refresh timer (catches description-only updates)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start()

        self._refresh()

    # ── Refresh ──────────────────────────────────────────

    def _refresh(self):
        # Preserve which row was selected so the user's toggle context
        # doesn't jump around.
        selected_id = None
        items = self._tree.selectedItems()
        if items:
            selected_id = items[0].data(0, Qt.UserRole)

        self._tree.clear()
        for dev in self._mgr.devices():
            item = QTreeWidgetItem([
                dev.description,
                dev.device_id or '(system default)',
                '✔' if dev.present else '✘',
                '',  # filled by checkbox below
                '',  # filled by beep button below
            ])
            item.setData(0, Qt.UserRole, dev.device_id)
            self._tree.addTopLevelItem(item)

            # System default is uncheckable — it cannot be force-absented
            if dev.device_id == '':
                item.setText(3, '—')
            else:
                cb = QCheckBox()
                cb.setChecked(dev.overridden_absent)
                cb.toggled.connect(
                    lambda checked, did=dev.device_id:
                        self._on_toggle_absent(did, checked))
                self._tree.setItemWidget(item, 3, cb)

            # Beep button — disabled if device is unusable
            beep_btn = QPushButton('🔊 Beep')
            beep_btn.setEnabled(dev.is_usable())
            beep_btn.clicked.connect(
                lambda _checked=False, did=dev.device_id:
                    self._on_test_beep(did))
            self._tree.setItemWidget(item, 4, beep_btn)

            if selected_id == dev.device_id:
                item.setSelected(True)

    # ── Actions ──────────────────────────────────────────

    def _on_force_scan(self):
        self._mgr.scan(reason='debug-menu-force-scan')
        self._refresh()

    def _on_clear_overrides(self):
        self._mgr.debug_clear_overrides()
        self._refresh()

    def _on_toggle_absent(self, device_id: str, checked: bool):
        self._mgr.debug_force_absent(device_id, absent=checked)
        # _refresh is also called via the devices_changed signal but doing
        # it here keeps the UI snappy on click.
        self._refresh()

    def _on_test_beep(self, device_id: str):
        dev = self._mgr.find_by_id(device_id)
        if dev is None:
            return
        self._mgr.play_test_beep(dev)

    def _on_beep_all(self):
        """Beep through every present device, staggered ~600 ms apart so
        you can identify which physical output is which."""
        present = [d for d in self._mgr.devices() if d.is_usable()]
        for i, dev in enumerate(present):
            QTimer.singleShot(
                i * 600,
                lambda d=dev: self._mgr.play_test_beep(d))

    # ── Cleanup ──────────────────────────────────────────

    def closeEvent(self, event):
        self._refresh_timer.stop()
        try:
            self._mgr.devices_changed.disconnect(self._refresh)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)
