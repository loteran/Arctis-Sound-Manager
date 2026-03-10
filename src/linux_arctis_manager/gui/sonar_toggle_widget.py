import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


STATE_FILE = Path.home() / '.config' / 'arctis_manager' / '.eq_mode'
YAML_PATH = Path.home() / '.config' / 'arctis_manager' / 'devices' / 'nova_pro_wireless.yaml'

_SONAR_ON = {
    '[0x06, 0x3b, 0x01]': '[0x06, 0x3b, 0x00]',
    '[0x06, 0x8d, 0x00]': '[0x06, 0x8d, 0x01]',
    '[0x06, 0x49, 0x00]': '[0x06, 0x49, 0x01]',
}
_SONAR_OFF = {v: k for k, v in _SONAR_ON.items()}


def _current_mode() -> str:
    return STATE_FILE.read_text().strip() if STATE_FILE.exists() else 'custom'


def _apply_yaml(mode: str) -> bool:
    try:
        content = YAML_PATH.read_text()
        for old, new in (_SONAR_ON if mode == 'sonar' else _SONAR_OFF).items():
            content = content.replace(old, new)
        YAML_PATH.write_text(content)
        return True
    except Exception:
        return False


class _ToggleWorker(QThread):
    countdown_tick = Signal(int)
    done = Signal(bool, str)  # success, new_mode

    def __init__(self, new_mode: str, old_mode: str):
        super().__init__()
        self._new_mode = new_mode
        self._old_mode = old_mode

    def run(self):
        if not _apply_yaml(self._new_mode):
            self.done.emit(False, self._old_mode)
            return

        result = subprocess.run(
            ['systemctl', '--user', 'restart',
             'pipewire', 'wireplumber', 'pipewire-pulse', 'filter-chain', 'arctis-manager'],
            check=False,
        )

        if result.returncode != 0:
            _apply_yaml(self._old_mode)  # rollback
            self.done.emit(False, self._old_mode)
            return

        for remaining in range(5, 0, -1):
            self.countdown_tick.emit(remaining)
            self.msleep(1000)

        STATE_FILE.write_text(self._new_mode)
        subprocess.run(
            ['notify-send', '-a', 'Arctis EQ', 'Arctis EQ',
             f'Mode {"Sonar" if self._new_mode == "sonar" else "Custom EQ"} activé'],
            check=False,
        )
        self.done.emit(True, self._new_mode)


class QSonarToggleWidget(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._worker = None

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(layout)

        title = QLabel('Equalizer')
        font = title.font()
        font.setBold(True)
        font.setPointSize(16)
        title.setFont(font)
        layout.addWidget(title)

        self._mode_label = QLabel()
        self._mode_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._mode_label)

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
        old_mode = _current_mode()
        new_mode = 'sonar' if old_mode == 'custom' else 'custom'

        self._button.setEnabled(False)
        self._button.setText('Redémarrage du son...')

        self._worker = _ToggleWorker(new_mode, old_mode)
        self._worker.countdown_tick.connect(self._on_countdown)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_countdown(self, remaining: int):
        self._button.setText(f'Veuillez patienter... {remaining}s')

    def _on_done(self, success: bool, mode: str):
        if not success:
            self._mode_label.setText('<b style="color:red;">Erreur lors du changement de mode</b>')
        self._refresh()
        self._button.setEnabled(True)
