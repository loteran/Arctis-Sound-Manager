import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider,
                                QVBoxLayout, QWidget)

from linux_arctis_manager.gui.dbus_wrapper import DbusWrapper


STATE_FILE = Path.home() / '.config' / 'arctis_manager' / '.eq_mode'
YAML_PATH = Path.home() / '.config' / 'arctis_manager' / 'devices' / 'nova_pro_wireless.yaml'

EQ_BANDS = ['31', '62', '125', '250', '500', '1K', '2K', '4K', '8K', '16K']

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


class _EqSlider(QWidget):
    """Single vertical EQ band slider with label and value display."""

    value_changed = Signal(int, int)  # band_index, raw_value (0-40)

    def __init__(self, index: int, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._index = index

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setLayout(layout)

        freq_label = QLabel(label)
        freq_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        freq_label.setStyleSheet('font-size: 11px; color: #aaa;')
        layout.addWidget(freq_label)

        self._slider = QSlider(Qt.Orientation.Vertical)
        self._slider.setMinimum(-20)   # -10 dB
        self._slider.setMaximum(20)    # +10 dB
        self._slider.setValue(0)       # 0 dB
        self._slider.setTickInterval(2)
        self._slider.setFixedHeight(140)
        self._slider.valueChanged.connect(self._on_value_changed)
        layout.addWidget(self._slider, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._val_label = QLabel('0')
        self._val_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._val_label.setStyleSheet('font-size: 11px;')
        layout.addWidget(self._val_label)

    def _on_value_changed(self, v: int):
        db = v * 0.5
        self._val_label.setText(f'{db:+.1f}' if db != 0 else '0')
        raw = v + 20  # convert slider (-20..+20) to byte (0..40)
        self.value_changed.emit(self._index, raw)

    def set_raw_value(self, raw: int):
        """Set slider from a raw byte value (0-40)."""
        self._slider.blockSignals(True)
        self._slider.setValue(raw - 20)
        db = (raw - 20) * 0.5
        self._val_label.setText(f'{db:+.1f}' if db != 0 else '0')
        self._slider.blockSignals(False)


class QSonarToggleWidget(QWidget):
    _sig_eq_bands = Signal(list)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._worker = None
        self._band_values = [20] * 10  # raw byte values

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

        # EQ sliders (visible only in custom mode)
        self._eq_widget = QWidget()
        eq_layout = QHBoxLayout()
        eq_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        eq_layout.setSpacing(0)
        self._eq_widget.setLayout(eq_layout)

        self._sliders: list[_EqSlider] = []
        for i, label in enumerate(EQ_BANDS):
            s = _EqSlider(i, label)
            s.value_changed.connect(self._on_slider_changed)
            eq_layout.addWidget(s)
            self._sliders.append(s)

        layout.addSpacing(12)
        layout.addWidget(self._eq_widget)

        self._sig_eq_bands.connect(self._on_eq_bands_received)

        self._refresh()
        DbusWrapper.get_eq_bands(self._sig_eq_bands)

    def _refresh(self):
        mode = _current_mode()
        if mode == 'sonar':
            self._mode_label.setText('Mode actuel : <b>Sonar</b>')
            self._button.setText('Passer en Custom EQ')
            self._eq_widget.setVisible(False)
        else:
            self._mode_label.setText('Mode actuel : <b>Custom EQ</b>')
            self._button.setText('Passer en mode Sonar')
            self._eq_widget.setVisible(True)

    def _on_eq_bands_received(self, bands: list):
        self._band_values = bands
        for i, slider in enumerate(self._sliders):
            slider.set_raw_value(bands[i])

    def _on_slider_changed(self, index: int, raw: int):
        self._band_values[index] = raw
        DbusWrapper.send_eq_command(list(self._band_values))

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
        if success and mode == 'custom':
            DbusWrapper.get_eq_bands(self._sig_eq_bands)
