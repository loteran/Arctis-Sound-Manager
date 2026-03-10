import subprocess
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


STATE_FILE = Path.home() / '.config' / 'arctis_manager' / '.eq_mode'
TOGGLE_SCRIPT = Path.home() / '.config' / 'arctis_manager' / 'toggle_sonar.py'


def _current_mode() -> str:
    return STATE_FILE.read_text().strip() if STATE_FILE.exists() else 'custom'


class QSonarToggleWidget(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(layout)

        title = QLabel('Equalizer')
        font = title.font()
        font.setBold(True)
        font.setPointSize(16)
        title.setFont(font)
        layout.addWidget(title)

        # Mode label
        self._mode_label = QLabel()
        self._mode_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._mode_label)

        # Toggle button row
        row = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        row.setLayout(row_layout)

        self._button = QPushButton()
        self._button.setFixedWidth(200)
        self._button.clicked.connect(self._on_toggle)
        row_layout.addWidget(self._button)

        layout.addWidget(row)

        self._refresh()

    def _refresh(self):
        mode = _current_mode()
        if mode == 'sonar':
            self._mode_label.setText('Mode actuel : <b>Sonar</b>')
            self._button.setText('Passer en Custom EQ')
        else:
            self._mode_label.setText('Mode actuel : <b>Custom EQ</b>')
            self._button.setText('Passer en mode Sonar')

    def _on_toggle(self):
        self._button.setEnabled(False)
        self._button.setText('Changement en cours...')
        subprocess.Popen(
            ['python3', str(TOGGLE_SCRIPT)],
        ).wait()
        # Restart audio services to restore virtual sinks after mode switch
        self._button.setText('Redémarrage du son...')
        subprocess.run(
            ['systemctl', '--user', 'restart',
             'pipewire', 'wireplumber', 'pipewire-pulse', 'filter-chain', 'arctis-manager'],
            check=False,
        )
        time.sleep(4)
        self._refresh()
        self._button.setEnabled(True)
