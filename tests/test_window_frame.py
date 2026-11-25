"""Title bar and frame colours via a KDE colour scheme (KDE Plasma only)."""
import pytest

from arctis_sound_manager.gui import theme as T
from arctis_sound_manager.gui import window_frame as WF


@pytest.fixture()
def app():
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    application.setProperty("KDE_COLOR_SCHEME_PATH", None)
    yield application
    application.setProperty("KDE_COLOR_SCHEME_PATH", None)
    T.set_active_theme("steelseries")


def test_scheme_colours_the_title_bar_like_the_sidebar():
    text = WF.colour_scheme(T.THEMES["neon"])
    assert "[Colors:Header]\nBackgroundNormal=5,5,8\n" in text           # neon BG_SIDEBAR #050508
    assert "[WM]\nactiveBackground=5,5,8\n" in text
    assert "inactiveForeground=42,102,85" in text                        # neon TEXT_SECONDARY


def test_nothing_outside_kde(app, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    WF.apply(app)
    assert app.property("KDE_COLOR_SCHEME_PATH") is None
    assert not any(tmp_path.iterdir())


def test_kde_gets_a_scheme_per_theme(app, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    T.set_active_theme("aurora")
    WF.apply(app)
    first = app.property("KDE_COLOR_SCHEME_PATH")
    assert first and "activeBackground=10,10,26" in open(first).read()  # aurora BG_SIDEBAR
    T.set_active_theme("neon")
    WF.apply(app)
    second = app.property("KDE_COLOR_SCHEME_PATH")
    assert second != first and "activeBackground=5,5,8" in open(second).read()
