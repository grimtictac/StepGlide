#!/usr/bin/env python3
"""
Music Player — PySide6 edition.

Entry point: creates the QApplication, initialises the core modules,
and launches the main window.
"""

import os
import sys

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication

from ui.splash import SplashScreen
from core.config import AppConfig
from core.database import Database


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Python Music Player')

    # ── Show splash screen ───────────────────────────────
    splash = SplashScreen(total_steps=5)
    splash.show()
    app.processEvents()

    # ── Core init ────────────────────────────────────────
    splash.set_status('Loading configuration')
    config = AppConfig()
    config.load()
    if config.ensure_builtin_smart_playlists():
        config.save()

    splash.set_status('Initialising database')
    db = Database(
        abs_path_fn=lambda p: (
            os.path.join(config.library_root, p)
            if config.library_root and not os.path.isabs(p)
            else p
        ),
    )
    db.init_schema()

    # ── Import UI after core is ready ────────────────────
    splash.set_status('Building interface')
    from ui.main_window import MainWindow

    splash.set_status('Loading tracks')
    window = MainWindow(db=db, config=config, splash=splash)

    splash.set_status('Ready')
    splash.finish_splash(window)
    window.show()

    # ── Kick off deferred mutagen backfills off the UI thread ──
    # These used to run inside Database.init_schema(), which on a
    # large library re-walked thousands of files via mutagen on every
    # launch and held the splash hostage for tens of seconds.  They're
    # now one-shot per row (see tracks.backfill_done) and run in the
    # background after the UI is up.
    from PySide6.QtCore import QThread, QObject, Signal

    class _BackfillWorker(QObject):
        finished = Signal()

        def __init__(self, db, stop_flag):
            super().__init__()
            self._db = db
            self._stop_flag = stop_flag

        def run(self):
            try:
                self._db.run_deferred_backfills(
                    should_stop=lambda: self._stop_flag[0])
            finally:
                self.finished.emit()

    stop_flag = [False]
    thread = QThread()
    worker = _BackfillWorker(db, stop_flag)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    # Track whether the thread has been deleted on the C++ side so the
    # quit handler doesn't poke at a freed QThread.  The backfill
    # typically completes long before the user quits, at which point
    # thread.deleteLater() has already run — calling .quit() on the
    # bound shiboken wrapper then raises 'Internal C++ object already
    # deleted'.
    thread_alive = [True]
    thread.destroyed.connect(lambda *_: thread_alive.__setitem__(0, False))

    def _on_about_to_quit():
        stop_flag[0] = True
        if not thread_alive[0]:
            return
        try:
            thread.quit()
            thread.wait(2000)
        except RuntimeError:
            # Thread torn down between the alive check and the call —
            # benign, nothing left to clean up.
            pass
    app.aboutToQuit.connect(_on_about_to_quit)
    thread.start()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
