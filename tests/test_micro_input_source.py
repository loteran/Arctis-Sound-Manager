# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the micro_input_source setting (issue #131).

ensure_micro_capture_link() used to unconditionally force the Arctis
microphone onto the Sonar Micro EQ capture (issue #127), fighting any manual
qpwgraph routing to a different mic. It's now driven by the
GeneralSettings.micro_input_source setting: "__auto__" (default) keeps the
#127 behaviour, "__manual__" disables enforcement, and any other value is
treated as the node.name of the source to pin the capture to.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arctis_sound_manager import sonar_to_pipewire as _s2p
from arctis_sound_manager.sonar_to_pipewire import _MICRO_CAPTURE_NAME, ensure_micro_capture_link


def _settings(micro_input_source):
    return SimpleNamespace(micro_input_source=micro_input_source)


def test_auto_mode_uses_physical_in():
    """Default "__auto__" resolves the Arctis mic via _get_physical_in, exactly
    like the original #127 behaviour — no regression for existing users."""
    with patch.object(_s2p, "_get_physical_in", return_value="alsa_input.arctis-mic") as get_in, \
         patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("__auto__")), \
         patch("arctis_sound_manager.pw_utils.ensure_capture_link",
               MagicMock(return_value=True)) as link:
        # ensure_capture_link is imported inside the function body (lazy
        # import to avoid a cycle), so it must be patched at its source
        # module (arctis_sound_manager.pw_utils), not on _s2p.
        result = ensure_micro_capture_link(data=["dump"])

    get_in.assert_called_once()
    link.assert_called_once_with("alsa_input.arctis-mic", _MICRO_CAPTURE_NAME, data=["dump"])
    assert result is True


def test_device_mode_uses_configured_node_name():
    """A concrete node.name in the setting pins the capture to that source,
    bypassing _get_physical_in entirely."""
    with patch.object(_s2p, "_get_physical_in") as get_in, \
         patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("alsa_input.other-mic")), \
         patch("arctis_sound_manager.pw_utils.ensure_capture_link",
               MagicMock(return_value=True)) as link:
        result = ensure_micro_capture_link(data=["dump"])

    get_in.assert_not_called()
    link.assert_called_once_with("alsa_input.other-mic", _MICRO_CAPTURE_NAME, data=["dump"])
    assert result is True


def test_manual_mode_skips_enforcement_entirely():
    """"__manual__" must not create or tear down any link — ensure_capture_link
    is never called, and the watchdog just moves on (return False = retry-later
    semantics, but there's nothing to retry since we never try)."""
    with patch.object(_s2p, "_get_physical_in") as get_in, \
         patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("__manual__")), \
         patch("arctis_sound_manager.pw_utils.ensure_capture_link",
               MagicMock(return_value=True)) as link:
        result = ensure_micro_capture_link(data=["dump"])

    get_in.assert_not_called()
    link.assert_not_called()
    assert result is False


def test_missing_setting_falls_back_to_auto():
    """An older settings file / object without the attribute at all (or an
    empty string) must behave like "__auto__", not crash or go manual."""
    with patch.object(_s2p, "_get_physical_in", return_value="alsa_input.arctis-mic") as get_in, \
         patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=SimpleNamespace()), \
         patch("arctis_sound_manager.pw_utils.ensure_capture_link",
               MagicMock(return_value=True)) as link:
        result = ensure_micro_capture_link(data=["dump"])

    get_in.assert_called_once()
    link.assert_called_once_with("alsa_input.arctis-mic", _MICRO_CAPTURE_NAME, data=["dump"])
    assert result is True


def test_auto_mode_no_device_returns_false():
    """No headset attached in auto mode — nothing to link to, watchdog retries
    later (unchanged #127 behaviour)."""
    with patch.object(_s2p, "_get_physical_in", return_value=""), \
         patch("arctis_sound_manager.settings.GeneralSettings.read_from_file",
               return_value=_settings("__auto__")), \
         patch("arctis_sound_manager.pw_utils.ensure_capture_link",
               MagicMock(return_value=True)) as link:
        result = ensure_micro_capture_link()

    link.assert_not_called()
    assert result is False
