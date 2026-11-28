"""Settings answered while the window is still being built (#432).

The one-time settings request goes out before _build_window(); a page that
pumps the event loop while it is built (the Clips install screen) delivered
the reply before the page table existed, and the GUI crashed with
"'QMainApp' object has no attribute '_built_pages'".
"""
from unittest.mock import MagicMock


def _app():
    from arctis_sound_manager.gui.main_app import QMainApp
    app = QMainApp.__new__(QMainApp)
    app.settings = {}
    app._window_ready = False
    app._early_settings = None
    return app


def test_settings_before_the_window_are_kept_not_applied():
    app = _app()
    app.on_settings_received({"has_dac": True, "has_hardware_eq": True})
    assert app._early_settings == {"has_dac": True, "has_hardware_eq": True}
    assert app.settings == {}


def test_settings_after_the_window_are_applied():
    app = _app()
    app._window_ready = True
    app._sidebar_buttons = MagicMock()
    app._sidebar = MagicMock()
    app._stack = MagicMock()
    app._built_pages = {}
    app.main_window = MagicMock()
    app.main_window.isVisible.return_value = False
    app.on_settings_received({"has_dac": False})
    assert app.settings == {"has_dac": False}
