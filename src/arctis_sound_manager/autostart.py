# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import os
import shutil
import subprocess
from pathlib import Path

from arctis_sound_manager import service_control as sc

logger = logging.getLogger(__name__)

_SERVICE = "arctis-manager.service"
_GUI_SERVICE = "arctis-gui.service"
_GUI_SERVICE_TEMPLATE = """\
[Unit]
Description=Arctis Sound Manager — System Tray
After=graphical-session.target arctis-manager.service
Wants=arctis-manager.service

[Service]
Type=simple
ExecStart={asm_gui} --systray
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""


def detect_environment() -> str:
    if "HYPRLAND_INSTANCE_SIGNATURE" in os.environ:
        return "hyprland"
    if "SWAYSOCK" in os.environ:
        return "sway"
    xdg = os.environ.get("XDG_CURRENT_DESKTOP", "")
    for part in xdg.split(":"):
        token = part.strip().lower()
        if not token:
            continue
        if token in {"gnome", "unity", "pantheon"}:
            return "gnome"
        if token in {"kde", "plasma"}:
            return "kde"
        if token in {"xfce", "hyprland", "sway", "cinnamon", "mate",
                     "lxqt", "lxde", "budgie", "i3"}:
            return token
    return "unknown"


def systemd_user_available() -> bool:
    if shutil.which("systemctl") is None:
        return False
    try:
        subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True, timeout=2,
        )
        return True
    except Exception:
        return False


class SystemdBackend:
    name = "systemd"

    def is_available(self) -> bool:
        return systemd_user_available()

    def is_enabled(self) -> bool:
        return sc.is_enabled("arctis-manager")

    def enable(self) -> None:
        sc.enable("arctis-manager")
        gui_path = self._ensure_gui_service()
        if gui_path and gui_path.exists():
            sc.enable("arctis-gui")

    def disable(self) -> None:
        sc.disable("arctis-manager")
        gui_path = Path.home() / ".config" / "systemd" / "user" / _GUI_SERVICE
        if gui_path.exists():
            sc.disable("arctis-gui")

    def display_name(self) -> str:
        return "systemd user service"

    def _ensure_gui_service(self) -> Path | None:
        gui_path = Path.home() / ".config" / "systemd" / "user" / _GUI_SERVICE
        if gui_path.exists():
            return gui_path
        asm_gui = shutil.which("asm-gui")
        if not asm_gui:
            return None
        try:
            gui_path.parent.mkdir(parents=True, exist_ok=True)
            gui_path.write_text(_GUI_SERVICE_TEMPLATE.format(asm_gui=asm_gui))
            sc.daemon_reload()
        except OSError as e:
            logger.warning("autostart: could not write arctis-gui.service: %s", e)
            return None
        return gui_path


class DinitBackend:
    name = "dinit"

    def is_available(self) -> bool:
        return shutil.which("dinitctl") is not None

    def is_enabled(self) -> bool:
        from arctis_sound_manager.init_system import (
            is_dinit_service_enabled, is_xdg_autostart_enabled,
        )
        return is_dinit_service_enabled("arctis-manager") and is_xdg_autostart_enabled()

    def enable(self) -> None:
        sc.enable("arctis-manager")
        # arctis-gui has no dinit service — XDG autostart is the right mechanism
        # (dinit services have no $DISPLAY/$WAYLAND_DISPLAY).
        from arctis_sound_manager.init_system import (
            write_xdg_autostart, _has_xdg_autostart_consumer, write_xprofile_fallback,
        )
        write_xdg_autostart()  # also cleans up stale xprofile on DE systems
        if not _has_xdg_autostart_consumer():
            write_xprofile_fallback()

    def disable(self) -> None:
        sc.disable("arctis-manager")
        from arctis_sound_manager.init_system import (
            remove_xdg_autostart, remove_xprofile_fallback,
        )
        remove_xdg_autostart()
        remove_xprofile_fallback()

    def display_name(self) -> str:
        return "dinit user service"


class XdgAutostartBackend:
    name = "xdg"

    @property
    def _desktop_path(self) -> Path:
        return Path.home() / ".config" / "autostart" / "arctis-manager.desktop"

    def is_available(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        p = self._desktop_path
        if not p.exists():
            return False
        text = p.read_text(errors="replace")
        return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text

    def enable(self) -> None:
        exe = shutil.which("asm-gui") or "asm-gui"
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Arctis Sound Manager\n"
            "Comment=Headset audio control (autostart)\n"
            f"Exec={exe} --systray\n"
            "Icon=arctis-manager\n"
            "Terminal=false\n"
            "Categories=AudioVideo;Audio;Utility;\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        try:
            self._desktop_path.parent.mkdir(parents=True, exist_ok=True)
            self._desktop_path.write_text(content)
        except OSError as e:
            logger.warning("autostart: could not write XDG desktop file: %s", e)
            return
        # Issue #25: bare WMs (i3/openbox/XLibre) without a DE ignore the .desktop.
        # Add a ~/.xprofile fallback so X11 sessions still launch asm-gui at login.
        from arctis_sound_manager.init_system import (
            _has_xdg_autostart_consumer, write_xprofile_fallback,
        )
        if not _has_xdg_autostart_consumer():
            write_xprofile_fallback()

    def disable(self) -> None:
        try:
            self._desktop_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("autostart: could not remove XDG desktop file: %s", e)
        # Mirror cleanup of the .xprofile fallback we may have added in enable().
        from arctis_sound_manager.init_system import remove_xprofile_fallback
        remove_xprofile_fallback()

    def display_name(self) -> str:
        return "XDG autostart (.desktop)"


_AUTOSTART_MARKER = "# arctis-sound-manager-autostart"


class _LineMarkerBackend:
    name = ""
    _exec_directive = ""

    @property
    def _config_path(self) -> Path:
        raise NotImplementedError

    def is_available(self) -> bool:
        return self._config_path.parent.exists()

    def is_enabled(self) -> bool:
        p = self._config_path
        if not p.exists():
            return False
        return _AUTOSTART_MARKER in p.read_text(errors="replace")

    def enable(self) -> None:
        exe = shutil.which("asm-gui") or "asm-gui"
        block = f"{_AUTOSTART_MARKER}\n{self._exec_directive} = {exe} --systray\n"
        try:
            p = self._config_path
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(block)
                return
            text = p.read_text(errors="replace")
            if _AUTOSTART_MARKER in text:
                return  # idempotent
            sep = "" if text.endswith("\n") else "\n"
            p.write_text(text + sep + block)
        except OSError as e:
            logger.warning("autostart: could not write %s: %s", self._config_path, e)

    def disable(self) -> None:
        try:
            p = self._config_path
            if not p.exists():
                return
            lines = p.read_text(errors="replace").splitlines(keepends=True)
            out = []
            skip_next = False
            for line in lines:
                if skip_next:
                    skip_next = False
                    continue
                if line.rstrip("\n") == _AUTOSTART_MARKER:
                    skip_next = True
                    continue
                out.append(line)
            tmp = p.with_suffix(".tmp")
            tmp.write_text("".join(out))
            tmp.replace(p)
        except OSError as e:
            logger.warning("autostart: could not update %s: %s", self._config_path, e)

    def display_name(self) -> str:
        return f"{self.name} config ({self._config_path})"


class HyprlandBackend(_LineMarkerBackend):
    name = "hyprland"
    _exec_directive = "exec-once"

    @property
    def _config_path(self) -> Path:
        return Path.home() / ".config" / "hypr" / "hyprland.conf"


class SwayBackend(_LineMarkerBackend):
    name = "sway"
    _exec_directive = "exec"

    @property
    def _config_path(self) -> Path:
        return Path.home() / ".config" / "sway" / "config"


def _all_backends():
    return [SystemdBackend(), DinitBackend(), XdgAutostartBackend(),
            HyprlandBackend(), SwayBackend()]


def pick_backend():
    from arctis_sound_manager.init_system import detect_init
    env = detect_environment()
    init = detect_init()

    if env == "hyprland" and HyprlandBackend().is_available():
        return HyprlandBackend()
    if env == "sway" and SwayBackend().is_available():
        return SwayBackend()
    if init == "dinit" and DinitBackend().is_available():
        return DinitBackend()
    if systemd_user_available() and env in {
        "gnome", "kde", "xfce", "cinnamon", "mate",
        "lxqt", "lxde", "budgie", "unity", "pantheon",
    }:
        return SystemdBackend()
    return XdgAutostartBackend()


def autostart_enabled() -> bool:
    for backend in _all_backends():
        try:
            if backend.is_available() and backend.is_enabled():
                return True
        except Exception:
            pass
    return False


def set_autostart(enabled: bool) -> None:
    if enabled:
        active = pick_backend()
        _cleanup_other_backends(active.name)
        try:
            active.enable()
        except Exception as e:
            logger.warning("autostart: enable failed (%s): %s", active.name, e)
    else:
        for backend in _all_backends():
            try:
                if backend.is_available():
                    backend.disable()
            except Exception as e:
                logger.warning("autostart: disable failed (%s): %s", backend.name, e)


def _cleanup_other_backends(active_name: str) -> None:
    for backend in _all_backends():
        if backend.name == active_name:
            continue
        try:
            if backend.is_available() and backend.is_enabled():
                backend.disable()
        except Exception as e:
            logger.warning("autostart: cleanup failed (%s): %s", backend.name, e)


def active_backend_name() -> str:
    return pick_backend().name
