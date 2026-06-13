# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Theme Editor page — create or edit a custom user theme.
"""

from PySide6.QtWidgets import (
    QWidget, QScrollArea, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QGridLayout, QMessageBox,
    QColorDialog, QSizePolicy, QFrame,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QColor

from arctis_sound_manager.gui.theme import (
    get_theme, get_theme_label, all_theme_labels, save_user_theme,
    set_preview_colors, THEME_GROUPS, COLOR_LABEL_KEYS, THEME_KEYS,
)
from arctis_sound_manager.i18n import I18n
import arctis_sound_manager.gui.theme as _theme


# ── ColorPickerRow ─────────────────────────────────────────────────────────────

class ColorPickerRow(QWidget):
    """Horizontal row: label + swatch button + hex code label."""

    sig_color_changed = Signal(str, str)  # (color_key, "#RRGGBB")

    def __init__(self, color_key: str, label: str, parent=None):
        super().__init__(parent)
        self._color_key = color_key
        self._color = "#000000"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self._label = QLabel(label)
        self._label.setFixedWidth(220)
        self._label.setStyleSheet("background: transparent;")
        layout.addWidget(self._label)

        self._swatch = QPushButton()
        self._swatch.setFixedSize(48, 28)
        self._swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._swatch.clicked.connect(self._on_swatch_clicked)
        layout.addWidget(self._swatch)

        self._hex_label = QLabel(self._color)
        self._hex_label.setStyleSheet(
            f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 9pt; background: transparent;"
        )
        layout.addWidget(self._hex_label)

        layout.addStretch()

    def _on_swatch_clicked(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, self._label.text())
        if color.isValid():
            self.set_color(color.name())
            self.sig_color_changed.emit(self._color_key, self._color)

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._swatch.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid #444; border-radius: 4px;"
        )
        self._hex_label.setText(hex_color)

    def color(self) -> str:
        return self._color


# ── ThemeEditorPage ────────────────────────────────────────────────────────────

class ThemeEditorPage(QWidget):
    """Full-page editor for creating or modifying a user theme."""

    sig_saved = Signal(str)       # theme_id
    sig_cancelled = Signal()
    sig_preview = Signal(dict)    # colors dict complet

    def __init__(self, parent=None):
        super().__init__(parent)

        self._editing_id: str | None = None
        self._colors: dict[str, str] = dict(get_theme("steelseries"))
        self._rows: dict[str, ColorPickerRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Scroll area ───────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {_theme.c('BG_MAIN')}; border: none; }}"
        )
        self._scroll = scroll

        content = QWidget()
        content.setStyleSheet(f"background-color: {_theme.c('BG_MAIN')};")
        self._content = content

        content_layout = QVBoxLayout(content)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_layout.setContentsMargins(36, 24, 36, 24)
        content_layout.setSpacing(0)

        # ── Title ─────────────────────────────────────────────────────────────
        self._title_label = QLabel(I18n.translate("ui", "theme_editor_title"))
        self._title_label.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; font-size: 22pt; font-weight: bold; background: transparent;"
        )
        content_layout.addWidget(self._title_label)
        content_layout.addSpacing(20)

        # ── Name row ──────────────────────────────────────────────────────────
        name_row = QHBoxLayout()
        name_row.setSpacing(10)

        self._name_label = QLabel(I18n.translate("ui", "theme_name"))
        self._name_label.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; background: transparent;"
        )
        self._name_label.setFixedWidth(160)
        name_row.addWidget(self._name_label)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(I18n.translate("ui", "theme_name_placeholder"))
        self._name_edit.setMaxLength(40)
        self._name_edit.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: {_theme.c('BG_BUTTON')};
                color: {_theme.c('TEXT_PRIMARY')};
                border: 1px solid {_theme.c('BORDER')};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11pt;
            }}
            QLineEdit:focus {{
                border-color: {_theme.c('ACCENT')};
            }}
            """
        )
        name_row.addWidget(self._name_edit)
        content_layout.addLayout(name_row)
        content_layout.addSpacing(10)

        # ── Duplicate-from row ────────────────────────────────────────────────
        self._dup_row_widget = QWidget()
        dup_row = QHBoxLayout(self._dup_row_widget)
        dup_row.setContentsMargins(0, 0, 0, 0)
        dup_row.setSpacing(10)

        self._dup_label = QLabel(I18n.translate("ui", "theme_duplicate_from"))
        self._dup_label.setStyleSheet(
            f"color: {_theme.c('TEXT_PRIMARY')}; background: transparent;"
        )
        self._dup_label.setFixedWidth(160)
        dup_row.addWidget(self._dup_label)

        self._dup_combo = QComboBox()
        self._dup_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: {_theme.c('BG_BUTTON')};
                color: {_theme.c('TEXT_PRIMARY')};
                border: 1px solid {_theme.c('BORDER')};
                border-radius: 6px;
                padding: 4px 10px;
                min-width: 160px;
            }}
            QComboBox:hover {{ border-color: {_theme.c('ACCENT')}; }}
            QComboBox QAbstractItemView {{
                background-color: {_theme.c('BG_CARD')};
                color: {_theme.c('TEXT_PRIMARY')};
                border: 1px solid {_theme.c('BORDER')};
                selection-background-color: {_theme.c('ACCENT')};
                selection-color: #FFFFFF;
            }}
            """
        )
        self._rebuild_duplicate_combo()
        self._dup_combo.currentIndexChanged.connect(self._on_duplicate_changed)
        dup_row.addWidget(self._dup_combo)
        dup_row.addStretch()

        content_layout.addWidget(self._dup_row_widget)
        content_layout.addSpacing(20)

        # ── Color picker groups ───────────────────────────────────────────────
        for group_key, color_keys in THEME_GROUPS.items():
            # Section title
            section_title = QLabel(I18n.translate("ui", group_key))
            section_title.setStyleSheet(
                f"color: {_theme.c('TEXT_SECONDARY')}; font-size: 15pt; font-weight: bold; background: transparent;"
            )
            content_layout.addWidget(section_title)
            content_layout.addSpacing(6)

            # Grid: 2 columns of ColorPickerRow
            grid_widget = QWidget()
            grid_widget.setStyleSheet(
                f"background-color: {_theme.c('BG_CARD')}; border-radius: 8px;"
            )
            grid = QGridLayout(grid_widget)
            grid.setContentsMargins(12, 10, 12, 10)
            grid.setSpacing(4)

            for idx, color_key in enumerate(color_keys):
                label_key = COLOR_LABEL_KEYS.get(color_key, color_key)
                label_text = I18n.translate("ui", label_key)
                row_widget = ColorPickerRow(color_key, label_text)
                row_widget.set_color(self._colors.get(color_key, "#000000"))
                row_widget.sig_color_changed.connect(self._on_color_changed)
                self._rows[color_key] = row_widget

                grid_row = idx // 2
                grid_col = idx % 2
                grid.addWidget(row_widget, grid_row, grid_col)

            content_layout.addWidget(grid_widget)
            content_layout.addSpacing(16)

        # ── Button row ────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self._cancel_btn = QPushButton(I18n.translate("ui", "theme_cancel"))
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setFixedHeight(40)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)

        self._save_btn = QPushButton(I18n.translate("ui", "theme_save"))
        self._save_btn.setObjectName("accentBtn")
        self._save_btn.setFixedHeight(40)
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        content_layout.addSpacing(8)
        content_layout.addLayout(btn_row)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # ── Public API ─────────────────────────────────────────────────────────────

    def open_for_new(self, base_theme_id: str = "steelseries") -> None:
        """Prepare the editor for creating a new theme."""
        self._editing_id = None
        self._name_edit.clear()
        self._dup_row_widget.setVisible(True)

        # Select the base theme in the combo without triggering _on_duplicate_changed
        self._dup_combo.blockSignals(True)
        for i in range(self._dup_combo.count()):
            if self._dup_combo.itemData(i) == base_theme_id:
                self._dup_combo.setCurrentIndex(i)
                break
        self._dup_combo.blockSignals(False)

        self._load_colors(get_theme(base_theme_id))

    def open_for_edit(self, theme_id: str) -> None:
        """Prepare the editor for editing an existing theme."""
        self._editing_id = theme_id
        self._name_edit.setText(get_theme_label(theme_id))
        self._dup_row_widget.setVisible(False)
        self._load_colors(get_theme(theme_id))

    def apply_theme(self, t: dict) -> None:
        """Restyle the page widgets using colors from dict t."""
        bg_main = t.get("BG_MAIN", "#16191E")
        bg_card = t.get("BG_CARD", "#1C2026")
        bg_button = t.get("BG_BUTTON", "#2D363E")
        bg_button_hover = t.get("BG_BUTTON_HOVER", "#3A4550")
        text_primary = t.get("TEXT_PRIMARY", "#C8C8C8")
        text_secondary = t.get("TEXT_SECONDARY", "#8D96AA")
        border = t.get("BORDER", "#2A3038")
        accent = t.get("ACCENT", "#FB4A00")

        self._content.setStyleSheet(f"background-color: {bg_main};")
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {bg_main}; border: none; }}"
        )

        self._title_label.setStyleSheet(
            f"color: {text_primary}; font-size: 22pt; font-weight: bold; background: transparent;"
        )
        self._name_label.setStyleSheet(
            f"color: {text_primary}; background: transparent;"
        )
        self._dup_label.setStyleSheet(
            f"color: {text_primary}; background: transparent;"
        )
        self._name_edit.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: {bg_button};
                color: {text_primary};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11pt;
            }}
            QLineEdit:focus {{ border-color: {accent}; }}
            """
        )
        self._dup_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: {bg_button};
                color: {text_primary};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 4px 10px;
                min-width: 160px;
            }}
            QComboBox:hover {{ border-color: {accent}; }}
            QComboBox QAbstractItemView {{
                background-color: {bg_card};
                color: {text_primary};
                border: 1px solid {border};
                selection-background-color: {accent};
                selection-color: #FFFFFF;
            }}
            """
        )

        # Update section title labels and grid cards
        for group_key in THEME_GROUPS:
            # Rebuild section title style — section labels are direct children
            pass  # styled via QSS propagation from content widget

        # Update ColorPickerRow hex labels style
        for row in self._rows.values():
            row._hex_label.setStyleSheet(
                f"color: {text_secondary}; font-size: 9pt; background: transparent;"
            )
            row._label.setStyleSheet("background: transparent;")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _load_colors(self, colors: dict) -> None:
        """Load a color dict into self._colors and update all picker rows."""
        for key in THEME_KEYS:
            val = colors.get(key, "#000000")
            self._colors[key] = val
            if key in self._rows:
                self._rows[key].set_color(val)

    def _rebuild_duplicate_combo(self) -> None:
        """Repopulate the duplicate-from combo with all current themes."""
        self._dup_combo.blockSignals(True)
        self._dup_combo.clear()
        for tid, label in all_theme_labels().items():
            self._dup_combo.addItem(label, userData=tid)
        self._dup_combo.blockSignals(False)

    def _on_duplicate_changed(self, index: int) -> None:
        tid = self._dup_combo.currentData()
        if tid:
            self._load_colors(get_theme(tid))

    def _on_color_changed(self, key: str, value: str) -> None:
        self._colors[key] = value
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        QTimer.singleShot(150, lambda: self.sig_preview.emit(dict(self._colors)))

    def _current_colors(self) -> dict[str, str]:
        return {key: self._colors.get(key, "#000000") for key in THEME_KEYS}

    def _on_save(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self,
                I18n.translate("ui", "theme_name"),
                I18n.translate("ui", "theme_name_placeholder"),
            )
            return
        theme_id = save_user_theme(
            label=name,
            colors=self._current_colors(),
            theme_id=self._editing_id,
        )
        set_preview_colors(None)
        self.sig_saved.emit(theme_id)

    def _on_cancel(self) -> None:
        set_preview_colors(None)
        self.sig_cancelled.emit()
