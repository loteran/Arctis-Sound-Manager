"""
test_device_detection.py — Simulated headset detection tests.

For every PID declared across all device YAMLs, verify that:
  1. The YAML loads and validates without errors (DeviceConfiguration).
  2. CoreEngine._find_hid_device() returns the mock device for that PID.
  3. CoreEngine._find_hid_device() returns None for an unknown PID.
  4. configure_virtual_sinks() picks the correct DeviceConfiguration.
  5. kernel_detach() calls detach_kernel_driver for each declared interface.
  6. The device_init command list is well-formed (no empty rows, bytes in range).
  7. The status request bytes are non-zero when a status block is declared.
  8. No two device families share a PID (no ambiguous routing).
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration

# ── helpers ───────────────────────────────────────────────────────────────────

DEVICES_DIR = Path(__file__).parent.parent / "src" / "arctis_sound_manager" / "devices"
YAML_FILES  = sorted(DEVICES_DIR.glob("*.yaml"))

_yaml = YAML(typ="safe")


def _load_config(path: Path) -> DeviceConfiguration:
    raw = _yaml.load(path)
    return DeviceConfiguration(raw)


def _all_configs() -> list[DeviceConfiguration]:
    return [_load_config(f) for f in YAML_FILES]


def _make_mock_usb_device(vendor_id: int, product_id: int) -> MagicMock:
    """Return a pyusb Device mock with a single HID interface (bInterfaceClass=3)."""
    import usb.core as _usb_core

    intf = MagicMock()
    intf.bInterfaceClass = 3  # USB_CLASS_HID

    cfg_obj = MagicMock()
    cfg_obj.__iter__ = MagicMock(return_value=iter([intf]))

    # spec=Device makes isinstance(dev, Device) return True so _find_hid_device
    # wraps it in [dev] instead of calling list(dev) which breaks the hierarchy.
    dev = MagicMock(spec=_usb_core.Device)
    dev.idVendor  = vendor_id
    dev.idProduct = product_id
    dev.__iter__  = MagicMock(return_value=iter([cfg_obj]))
    dev.is_kernel_driver_active = MagicMock(return_value=True)
    dev.detach_kernel_driver    = MagicMock()
    dev.attach_kernel_driver    = MagicMock()
    return dev


def _make_engine_stub() -> MagicMock:
    """Minimal engine stub with just the attributes _find_hid_device / kernel_detach need."""
    stub = MagicMock()
    stub._device_lock = threading.RLock()
    stub._usb_write_lock = threading.Lock()
    return stub


# ── parametrize all (config, pid) pairs ───────────────────────────────────────

def _all_config_pid_pairs():
    for path in YAML_FILES:
        cfg = _load_config(path)
        for pid in cfg.product_ids:
            yield pytest.param(cfg, pid, id=f"{cfg.name}|pid={hex(pid)}")


# ── 1. YAML validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", YAML_FILES, ids=[p.stem for p in YAML_FILES])
def test_yaml_loads_without_error(path: Path):
    cfg = _load_config(path)
    assert cfg.name
    assert cfg.vendor_id == 0x1038
    assert cfg.product_ids


# ── 2. _find_hid_device matches every declared PID ────────────────────────────

@pytest.mark.parametrize("cfg,pid", list(_all_config_pid_pairs()))
def test_find_hid_device_matches_pid(cfg: DeviceConfiguration, pid: int):
    from arctis_sound_manager.core import CoreEngine

    mock_dev = _make_mock_usb_device(cfg.vendor_id, pid)
    engine   = _make_engine_stub()

    with patch("usb.core.find", return_value=mock_dev):
        found = CoreEngine._find_hid_device(engine, cfg.vendor_id, [pid])

    assert found is mock_dev, f"Expected mock device for PID {hex(pid)}, got None"


# ── 3. _find_hid_device returns None for an unknown PID ──────────────────────

def test_find_hid_device_returns_none_for_unknown_pid():
    from arctis_sound_manager.core import CoreEngine

    engine = _make_engine_stub()
    with patch("usb.core.find", return_value=None):
        found = CoreEngine._find_hid_device(engine, 0x1038, [0xDEAD])
    assert found is None


# ── 4. configure_virtual_sinks picks the right config ────────────────────────

@pytest.mark.parametrize("cfg,pid", list(_all_config_pid_pairs()))
def test_configure_virtual_sinks_selects_correct_config(cfg: DeviceConfiguration, pid: int):
    from arctis_sound_manager.core import CoreEngine

    mock_dev    = _make_mock_usb_device(cfg.vendor_id, pid)
    all_configs = _all_configs()

    def fake_find(idVendor, idProduct):
        return mock_dev if idVendor == cfg.vendor_id and idProduct == pid else None

    engine = _make_engine_stub()
    engine.device_config         = None
    engine.device_configurations = all_configs
    # Bind the real _find_hid_device so configure_virtual_sinks actually finds our mock.
    engine._find_hid_device = lambda *a: CoreEngine._find_hid_device(engine, *a)

    with patch("usb.core.find", side_effect=fake_find), \
         patch.object(CoreEngine, "kernel_detach",    lambda *a, **k: None), \
         patch.object(CoreEngine, "init_device",      lambda *a, **k: None), \
         patch.object(CoreEngine, "new_device_status", lambda *a: MagicMock()), \
         patch("arctis_sound_manager.core.DeviceSettings"), \
         patch("arctis_sound_manager.core.device_state.set_current_device"), \
         patch("arctis_sound_manager.core.PulseAudioManager.get_instance"), \
         patch("arctis_sound_manager.core.OledManager"):

        CoreEngine.configure_virtual_sinks(engine)

    assert engine.device_config is not None, \
        f"configure_virtual_sinks set no config for PID {hex(pid)}"
    assert pid in engine.device_config.product_ids, (
        f"Wrong config selected for PID {hex(pid)}: got '{engine.device_config.name}'"
    )


# ── 5. kernel_detach calls detach for each declared interface ─────────────────

@pytest.mark.parametrize("cfg,pid", list(_all_config_pid_pairs()))
def test_kernel_detach_covers_all_interfaces(cfg: DeviceConfiguration, pid: int):
    from arctis_sound_manager.core import CoreEngine

    mock_dev = _make_mock_usb_device(cfg.vendor_id, pid)

    expected_interfaces = list(set([
        cfg.command_interface_index[0],
        *cfg.listen_interface_indexes,
        cfg.dial_interface_index,
    ]))

    engine = _make_engine_stub()
    engine._all_used_interfaces = MagicMock(return_value=expected_interfaces)

    CoreEngine.kernel_detach(engine, mock_dev, cfg)

    assert mock_dev.detach_kernel_driver.call_count == len(expected_interfaces), (
        f"{cfg.name} PID {hex(pid)}: expected {len(expected_interfaces)} detach calls, "
        f"got {mock_dev.detach_kernel_driver.call_count}"
    )


# ── 6. device_init rows are well-formed ───────────────────────────────────────

@pytest.mark.parametrize("path", YAML_FILES, ids=[p.stem for p in YAML_FILES])
def test_device_init_well_formed(path: Path):
    cfg = _load_config(path)
    if cfg.device_init is None:
        pytest.skip("No device_init defined")

    for i, row in enumerate(cfg.device_init):
        assert row, f"Row {i} is empty in {path.stem}"
        for j, byte in enumerate(row):
            if isinstance(byte, str):
                # DSL tokens like 'value', 'status.request', 'settings.*' are valid
                assert byte, f"Empty string token at row {i} col {j} of {path.stem}"
            else:
                assert 0x00 <= byte <= 0xFF, \
                    f"Byte {hex(byte)} out of range at row {i} col {j} of {path.stem}"


# ── 7. status request bytes are non-zero when a status block exists ───────────

@pytest.mark.parametrize("path", YAML_FILES, ids=[p.stem for p in YAML_FILES])
def test_status_request_nonzero(path: Path):
    cfg = _load_config(path)
    if cfg.status is None:
        pytest.skip("No status config defined")
    if not cfg.status.response_mapping:
        pytest.skip("Empty response_mapping — status polling not implemented for this device")

    assert cfg.status.request != 0, f"{path.stem}: status.request is 0 but response_mapping is non-empty"


# ── 8. No duplicate PIDs across device families ───────────────────────────────

def test_no_duplicate_pids_across_families():
    seen: dict[int, str] = {}
    for cfg in _all_configs():
        for pid in cfg.product_ids:
            assert pid not in seen, (
                f"PID {hex(pid)} declared in both '{seen[pid]}' and '{cfg.name}'"
            )
            seen[pid] = cfg.name
