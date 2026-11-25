"""Inline stylesheets and tinted icons follow the active theme, by colour role."""
import pytest

from arctis_sound_manager.gui import theme as T


@pytest.fixture()
def themed(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    T.install_stylesheet_dedup()
    monkeypatch.setattr(T, "_ACTIVE_THEME", T._ACTIVE_THEME)
    monkeypatch.setattr(T, "_APPLIED", None)
    T.set_active_theme("aurora")
    yield
    T.set_active_theme("steelseries")
    T._APPLIED = None


def test_colour_map_by_role():
    mapping = T.colour_map(T.THEMES["aurora"], T.THEMES["neon"])
    assert mapping[T.THEMES["aurora"]["TEXT_SECONDARY"].lower()] == T.THEMES["neon"]["TEXT_SECONDARY"]
    sheet = f"color: {T.THEMES['aurora']['ACCENT']}; background: #123456;"
    assert T.translate_sheet(sheet, mapping) == f"color: {T.THEMES['neon']['ACCENT']}; background: #123456;"


def test_no_theme_uses_a_default_colour_in_another_role():
    # setStyleSheet() translates default-theme colours to the active theme;
    # a theme colour written through c() must not be mistaken for one.
    default = T.THEMES["steelseries"]
    for name, colours in T.THEMES.items():
        for key, value in colours.items():
            for dkey, dvalue in default.items():
                if value.lower() == dvalue.lower():
                    assert colours.get(dkey, "").lower() == value.lower(), (name, key, dkey)


def test_default_constant_and_theme_switch(themed):
    from PySide6.QtWidgets import QLabel
    label = QLabel("x")
    label.setStyleSheet(f"color: {T.TEXT_SECONDARY};")        # the default theme's constant
    assert T.THEMES["aurora"]["TEXT_SECONDARY"] in label.styleSheet()

    T.set_active_theme("neon")
    T.retheme_widgets()
    assert label.styleSheet() == f"color: {T.THEMES['neon']['TEXT_SECONDARY']};"


def test_tinted_icon_follows(themed):
    from arctis_sound_manager.gui.components import HEADPHONE_ICON, SvgIconWidget
    icon = SvgIconWidget(HEADPHONE_ICON, T.ACCENT, size=16)
    assert icon._color == T.THEMES["aurora"]["ACCENT"]
    T.set_active_theme("neon")
    T.retheme_widgets()
    assert icon._color == T.THEMES["neon"]["ACCENT"]


def test_arrow_files_named_after_their_colour_follow(themed, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    mapping = T.colour_map(T.THEMES["aurora"], T.THEMES["neon"])
    old = T._chevron_icon(T.THEMES["aurora"]["TEXT_SECONDARY"], False)
    sheet = f"QComboBox::down-arrow {{ image: url({old}); }}"
    new = T.translate_sheet(sheet, mapping)
    assert T.THEMES["neon"]["TEXT_SECONDARY"].lstrip("#").lower() in new
