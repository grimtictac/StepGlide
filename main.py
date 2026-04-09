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
    splash = SplashScreen()
    splash.show()
    app.processEvents()

    # ── Core init ────────────────────────────────────────
    splash.set_status('Loading configuration')
    config = AppConfig()
    config.load()

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

    window = MainWindow(db=db, config=config)

    splash.set_status('Ready')
    splash.finish_splash(window)
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
