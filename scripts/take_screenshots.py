#!/usr/bin/env python
"""
Capture one screenshot per sidebar page and save to docs/images/.
Run from the repository root:
    python scripts/take_screenshots.py
"""
import os
import sys
import time
import logging

# Make sure src/ is in the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from arctis_sound_manager.gui.main_app import QMainApp
from arctis_sound_manager.gui.theme import APP_QSS

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'docs', 'images')

PAGES = [
    (0, 'screenshot_home.png'),
    (1, 'screenshot_equalizer.png'),
    (2, 'screenshot_headset.png'),
    (3, 'screenshot_settings.png'),
    (4, 'screenshot_help.png'),
]


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)

    main_app = QMainApp(app, logging.WARNING)
    window = main_app.main_window
    window.show()

    page_iter = iter(PAGES)

    def capture_next():
        try:
            idx, filename = next(page_iter)
        except StopIteration:
            app.quit()
            return

        main_app._switch_page(idx)
        # Let the page render before capturing
        QTimer.singleShot(300, lambda: do_capture(idx, filename))

    def do_capture(idx, filename):
        pixmap = window.grab()
        out_path = os.path.join(OUTPUT_DIR, filename)
        pixmap.save(out_path, 'PNG')
        print(f'Saved: {out_path}')
        QTimer.singleShot(100, capture_next)

    # Wait for the window to be fully rendered before starting
    QTimer.singleShot(800, capture_next)

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
