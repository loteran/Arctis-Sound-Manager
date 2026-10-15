# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Building blocks of the Headset page: live status as small tiles plus the
ChatMix bar, and the card each group of headset settings sits in.
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import arctis_sound_manager.gui.theme as _theme
from arctis_sound_manager.gui.components import (
    HEADPHONE_ICON,
    IMAGES_DIR,
    MIC_ICON,
    SvgIconWidget,
)
from arctis_sound_manager.gui.home_page import (
    _ChatMixSlider,
    _make_chatmix_bar_qss,
    chatmix_bar_track_css,
    chatmix_percentages_to_bar_position,
)
from arctis_sound_manager.i18n import I18n

BATTERY_ICON = os.path.join(IMAGES_DIR, "battery_icon.svg")
POWER_ICON = os.path.join(IMAGES_DIR, "power_icon.svg")
BLUETOOTH_ICON = os.path.join(IMAGES_DIR, "bluetooth_icon.svg")
CHARGING_ICON = os.path.join(IMAGES_DIR, "charging_icon.svg")

# At most this many tiles side by side; more are shared out over rows of
# near-equal length, so six tiles never land as four over two.
_TILE_MAX_COLUMNS = 6
_TILE_ICON_SIZE = 18
_TILE_MIN_WIDTH = 96


def tile_columns(count: int) -> int:
    """Columns for *count* tiles: one row up to the maximum, else balanced."""
    if count <= _TILE_MAX_COLUMNS:
        return max(count, 1)
    rows = -(-count // _TILE_MAX_COLUMNS)
    return -(-count // rows)

# Status keys whose value is a level worth drawing, not just printing.
_BAR_KEYS = frozenset({'headset_battery_charge', 'charge_slot_battery_charge'})

# The dial's two sides. Shown together as the ChatMix bar, not as two tiles.
_CHATMIX_KEYS = ('media_mix', 'chat_mix')


def _tile_icon(key: str) -> str:
    if 'battery' in key:
        return BATTERY_ICON
    if 'charging' in key:
        return CHARGING_ICON
    if key.startswith('bluetooth'):
        return BLUETOOTH_ICON
    if key.startswith('mic'):
        return MIC_ICON
    if key.endswith('power_status'):
        return POWER_ICON
    return HEADPHONE_ICON


def _tile_caption(key: str) -> str:
    """A short caption where one exists ("Battery"), else the status label."""
    short = I18n.translate('ui', f'headset_tile_{key}')
    if short != f'headset_tile_{key}':
        return short
    return I18n.translate('status', key)


def _card_qss(object_name: str, radius: int = 10) -> str:
    return f"""
        QFrame#{object_name} {{
            background-color: {_theme.c('BG_CARD')};
            border: 1px solid {_theme.c('BORDER')};
            border-radius: {radius}px;
        }}
    """


def card_contents_qss() -> str:
    """For a widget placed inside a card: the card's colour, not the page's."""
    return f"""
        QWidget {{ background-color: {_theme.c('BG_CARD')}; color: {_theme.c('TEXT_PRIMARY')}; }}
        QLabel  {{ background-color: transparent; color: {_theme.c('TEXT_PRIMARY')}; font-size: 10pt; }}
        QComboBox {{
            background-color: {_theme.c('BG_BUTTON')};
            color: {_theme.c('TEXT_PRIMARY')};
            border: 1px solid {_theme.c('BG_BUTTON_HOVER')};
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 10pt;
        }}
        QComboBox:hover {{ background-color: {_theme.c('BG_BUTTON_HOVER')}; }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox QAbstractItemView {{
            background-color: {_theme.c('BG_CARD')};
            color: {_theme.c('TEXT_PRIMARY')};
            selection-background-color: {_theme.c('ACCENT')};
            border: 1px solid {_theme.c('BORDER')};
        }}
        QSlider {{ background: transparent; }}
        QSlider::groove:horizontal {{
            height: 4px; background: {_theme.c('BG_BUTTON')}; border-radius: 2px;
        }}
        QSlider::sub-page:horizontal {{ background: {_theme.c('ACCENT')}; border-radius: 2px; }}
        QSlider::handle:horizontal {{
            background: {_theme.c('TEXT_PRIMARY')}; width: 14px; height: 14px;
            margin: -5px 0; border-radius: 7px;
        }}
    """


def _format_value(entry: dict) -> str:
    value = entry.get('value')
    if entry.get('type') == 'percentage':
        return f"{value}%"
    return I18n.translate('status_values', value)


class _StatusTile(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statusTile")
        # Equal columns whatever the text: the grid's stretch decides the
        # width, not "Unmuted" being longer than "On".
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(_TILE_MIN_WIDTH)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)

        self._icon_path = HEADPHONE_ICON
        self.icon = SvgIconWidget(self._icon_path, _theme.c('ACCENT'), size=_TILE_ICON_SIZE)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.caption = QLabel()
        text.addWidget(self.caption)
        self.value = QLabel()
        text.addWidget(self.value)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(3)
        self.bar.setVisible(False)
        text.addWidget(self.bar)
        row.addLayout(text, 1)
        self.apply_theme()

    def set_entry(self, key: str, entry: dict) -> None:
        self.caption.setText(_tile_caption(key))
        self.value.setText(_format_value(entry))
        icon = _tile_icon(key)
        if icon != self._icon_path:
            self._icon_path = icon
            self.icon._svg_path = icon
            self.icon.set_color(_theme.c('ACCENT'))
        show_bar = key in _BAR_KEYS and isinstance(entry.get('value'), int)
        self.bar.setVisible(show_bar)
        if show_bar:
            self.bar.setValue(max(0, min(100, entry['value'])))

    def apply_theme(self) -> None:
        self.setStyleSheet(_card_qss("statusTile"))
        self.icon.set_color(_theme.c('ACCENT'))
        self.caption.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 8pt; background: transparent; border: none;")
        self.value.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 10pt; font-weight: bold;"
            " background: transparent; border: none;")
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background-color: {_theme.c('BG_BUTTON')}; border: none; border-radius: 1px; }}
            QProgressBar::chunk {{ background-color: {_theme.c('ACCENT')}; border-radius: 1px; }}
        """)


class _ChatMixRow(QFrame):
    """The dial's position as the mixer page's ChatMix bar, read-only."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("chatmixRow")
        # Placed inside another card (the device card): no frame of its own.
        self.embedded = False
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 6, 14, 6)
        row.setSpacing(10)

        self.title = QLabel(I18n.translate('ui', 'headset_tile_chatmix'))
        row.addWidget(self.title)
        row.addSpacing(6)
        self.left = QLabel(I18n.translate('ui', 'game'))
        row.addWidget(self.left)
        self.bar = _ChatMixSlider(Qt.Orientation.Horizontal)
        self.bar.setRange(0, 100)
        self.bar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.bar, 1)
        self.right = QLabel(I18n.translate('ui', 'chat'))
        row.addWidget(self.right)
        self.apply_theme()

    def set_levels(self, game_pct: int, chat_pct: int) -> None:
        self.bar.setValue(chatmix_percentages_to_bar_position(game_pct, chat_pct))

    def embed(self, width: int) -> None:
        """Sit inside another card at a fixed *width*, without a frame."""
        self.embedded = True
        self.setFixedWidth(width)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(
            "QFrame#chatmixRow { background: transparent; border: none; }"
            if self.embedded else _card_qss("chatmixRow"))
        label =f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent; border: none;"
        self.left.setStyleSheet(label)
        self.right.setStyleSheet(label)
        self.title.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 9pt; font-weight: bold;"
            " background: transparent; border: none;")
        self.bar.setStyleSheet("QSlider { background: transparent; }" + _make_chatmix_bar_qss(
            chatmix_bar_track_css([_theme.c('COLOR_GAME')], _theme.c('COLOR_CHAT'))))


class StatusTiles(QWidget):
    """Every live status value the headset reports, one tile each, with the
    ChatMix dial drawn as a bar underneath.

    The keys are whatever the device profile maps, so nothing here names a
    particular headset: a Nova 7 gets battery, ChatMix and Bluetooth, a wired
    Nova 3 reports nothing and the whole block stays hidden.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        outer.addLayout(self._grid)
        self._laid_out_count = -1

        self.chatmix = _ChatMixRow(self)
        self.chatmix.setVisible(False)
        outer.addWidget(self.chatmix)

        self._tiles: list[_StatusTile] = []
        self._status: dict | None = None

    def update_status(self, status: dict) -> None:
        if status == self._status:
            return
        self._status = status

        flat = {
            key: entry
            for category in (status or {}).values() if isinstance(category, dict)
            for key, entry in category.items() if isinstance(entry, dict)
        }
        mix = [flat.get(k, {}).get('value') for k in _CHATMIX_KEYS]
        has_mix = all(isinstance(v, int) for v in mix)
        if has_mix:
            self.chatmix.set_levels(*mix)
        self.chatmix.setVisible(has_mix)

        entries = [(k, e) for k, e in flat.items() if not (has_mix and k in _CHATMIX_KEYS)]
        # Reuse tiles rather than rebuild them: status arrives every second.
        while len(self._tiles) < len(entries):
            self._tiles.append(_StatusTile(self))
        if len(entries) != self._laid_out_count:
            self._lay_out(len(entries))
        for tile, (key, entry) in zip(self._tiles, entries):
            tile.set_entry(key, entry)
            tile.setVisible(True)
        for tile in self._tiles[len(entries):]:
            tile.setVisible(False)
        self.setVisible(bool(entries) or has_mix)

    def _lay_out(self, count: int) -> None:
        """Place the first *count* tiles on the grid, in equal columns."""
        for tile in self._tiles:
            self._grid.removeWidget(tile)
        columns = tile_columns(count)
        for column in range(max(self._grid.columnCount(), columns)):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        for index, tile in enumerate(self._tiles[:count]):
            self._grid.addWidget(tile, index // columns, index % columns)
        self._laid_out_count = count

    def apply_theme(self) -> None:
        for tile in self._tiles:
            tile.apply_theme()
        self.chatmix.apply_theme()


class SettingsCard(QFrame):
    """A titled card holding one group of settings."""

    def __init__(self, title: str, body: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(4)

        self.title = QLabel(title)
        layout.addWidget(self.title)

        self.body = body
        layout.addWidget(body)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(_card_qss("settingsCard"))
        self.title.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; font-weight: bold;"
            " background: transparent; border: none;")
        self.body.setStyleSheet(card_contents_qss())
        if hasattr(self.body, "apply_theme"):
            self.body.apply_theme()


def section_title(section: str | None) -> str:
    """Card title for a settings section of the device profile."""
    if section:
        title = I18n.translate('ui', f'headset_section_{section}')
        if title != f'headset_section_{section}':
            return title
    return I18n.translate('ui', 'device_settings')
