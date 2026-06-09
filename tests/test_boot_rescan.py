"""Tests for the periodic boot-time re-scan fix (issue #76).

A device present at boot fires no udev 'add' event.  Without the retry loop
the daemon would never detect it unless the user triggered a USB autosuspend
resume (e.g. by joining a call).  These tests verify:

  1. _rescan_for_device() calls configure_virtual_sinks when _device_ready=False.
  2. _rescan_for_device() skips configure_virtual_sinks when _device_ready=True.
  3. _rescan_for_device() always clears _rescan_in_flight (happy path).
  4. _rescan_for_device() always clears _rescan_in_flight even when configure raises.
  5. _device_ready is False immediately after construction (before any device found).
  6. _device_ready becomes True after a successful configure_virtual_sinks.
  7. _device_ready becomes False after teardown.
"""

import threading
from unittest.mock import MagicMock, patch, call

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_engine_stub():
    """Build a minimal CoreEngine instance via __new__ (skips real __init__).

    Only populates the attributes touched by the re-scan / readiness path.
    Mirrors the pattern used in test_usb_reenum_ebusy.py.
    """
    from arctis_sound_manager.core import CoreEngine

    engine = CoreEngine.__new__(CoreEngine)
    engine.logger = MagicMock()
    engine._device_lock = threading.RLock()
    engine._detect_lock = threading.Lock()
    engine._device_ready = False
    engine._rescan_in_flight = False
    engine._logged_no_device = False
    engine.usb_device = None
    engine.device_config = None
    engine.oled_manager = None
    return engine


def _make_device_config(vendor_id: int = 0x1038):
    cfg = MagicMock()
    cfg.vendor_id = vendor_id
    cfg.command_interface_index = [0, 0]
    cfg.listen_interface_indexes = [1]
    cfg.dial_interface_index = 2
    cfg.dial_interface_candidates = []
    return cfg


# ── _rescan_for_device ────────────────────────────────────────────────────────


def test_rescan_calls_configure_when_not_ready():
    """configure_virtual_sinks must be called when _device_ready is False."""
    engine = _make_engine_stub()
    engine._device_ready = False
    engine._rescan_in_flight = True  # will be cleared by the method

    with patch.object(engine, 'configure_virtual_sinks') as mock_configure:
        engine._rescan_for_device()

    mock_configure.assert_called_once()


def test_rescan_skips_configure_when_ready():
    """configure_virtual_sinks must NOT be called when _device_ready is True."""
    engine = _make_engine_stub()
    engine._device_ready = True
    engine._rescan_in_flight = True

    with patch.object(engine, 'configure_virtual_sinks') as mock_configure:
        engine._rescan_for_device()

    mock_configure.assert_not_called()


def test_rescan_clears_in_flight_on_success():
    """_rescan_in_flight must be False after a successful scan."""
    engine = _make_engine_stub()
    engine._rescan_in_flight = True

    with patch.object(engine, 'configure_virtual_sinks'):
        engine._rescan_for_device()

    assert engine._rescan_in_flight is False


def test_rescan_clears_in_flight_on_exception():
    """_rescan_in_flight must be False even when configure_virtual_sinks raises."""
    engine = _make_engine_stub()
    engine._rescan_in_flight = True

    with patch.object(engine, 'configure_virtual_sinks', side_effect=RuntimeError("boom")):
        engine._rescan_for_device()  # must not propagate

    assert engine._rescan_in_flight is False


def test_rescan_logs_warning_on_exception():
    """A configure exception must be logged as a warning, not re-raised."""
    engine = _make_engine_stub()

    with patch.object(engine, 'configure_virtual_sinks', side_effect=OSError("usb gone")):
        engine._rescan_for_device()  # no exception expected

    engine.logger.warning.assert_called_once()


# ── _device_ready flag transitions ───────────────────────────────────────────


def test_device_ready_false_initially():
    """_device_ready must be False right after the flags are initialised."""
    engine = _make_engine_stub()
    assert engine._device_ready is False


def test_device_ready_true_after_successful_configure():
    """A full successful configure_virtual_sinks sets _device_ready=True."""
    from arctis_sound_manager.core import CoreEngine

    engine = _make_engine_stub()
    engine._device_ready = False

    dc = _make_device_config()

    import usb.core as _usb_core
    intf = MagicMock()
    intf.bInterfaceClass = 3  # USB_CLASS_HID
    cfg_obj = MagicMock()
    cfg_obj.__iter__ = MagicMock(return_value=iter([intf]))
    mock_dev = MagicMock(spec=_usb_core.Device)
    mock_dev.idVendor = 0x1038
    mock_dev.idProduct = 0x1234
    mock_dev.__iter__ = MagicMock(return_value=iter([cfg_obj]))
    mock_dev.is_kernel_driver_active = MagicMock(return_value=False)

    engine.device_configurations = [dc]
    engine.device_config = None
    engine.usb_device = None
    engine._active_extra_dial_interfaces = []
    engine._usb_write_lock = threading.Lock()

    with patch.object(CoreEngine, '_find_hid_device', return_value=mock_dev), \
         patch.object(CoreEngine, 'kernel_detach', return_value=True), \
         patch.object(CoreEngine, '_discover_physical_nodes', return_value=("game_sink", None, None)), \
         patch.object(CoreEngine, 'init_device', return_value=None), \
         patch.object(CoreEngine, 'redirect_to_media_sink', return_value=None), \
         patch.object(CoreEngine, 'new_device_status', return_value=MagicMock()), \
         patch.object(CoreEngine, 'setup_loopbacks', return_value=None), \
         patch.object(CoreEngine, '_update_active_dial_interfaces', return_value=None), \
         patch('arctis_sound_manager.core.DeviceSettings') as MockDS, \
         patch('arctis_sound_manager.core.device_state.set_current_device'), \
         patch('arctis_sound_manager.core.check_and_fix_stale_configs', return_value=(False, False), create=True):

        # DeviceSettings mock: settings must be an observable-like with add_observer
        mock_ds_instance = MagicMock()
        mock_ds_instance.settings = MagicMock()
        mock_ds_instance.settings.add_observer = MagicMock()
        MockDS.return_value = mock_ds_instance

        # Patch the sonar import inside the method
        with patch.dict('sys.modules', {
            'arctis_sound_manager.sonar_to_pipewire': MagicMock(
                check_and_fix_stale_configs=MagicMock(return_value=(False, False))
            )
        }):
            CoreEngine.configure_virtual_sinks(engine)

    assert engine._device_ready is True


def test_device_ready_false_after_teardown():
    """teardown() must set _device_ready=False."""
    from arctis_sound_manager.core import CoreEngine

    engine = _make_engine_stub()
    engine._device_ready = True
    dc = _make_device_config()
    engine.device_config = dc
    engine.usb_device = None  # already disposed
    engine._active_extra_dial_interfaces = []
    engine._usb_write_lock = threading.Lock()

    engine.loopback_manager = MagicMock()

    with patch('usb.util.release_interface'), \
         patch('usb.util.dispose_resources'), \
         patch('usb.core.find', return_value=None), \
         patch.object(CoreEngine, 'redirect_audio_on_disconnect', return_value=None), \
         patch('arctis_sound_manager.core.device_state.clear'):

        CoreEngine.teardown(engine)

    assert engine._device_ready is False


# ── log throttling ────────────────────────────────────────────────────────────


def test_no_device_log_emitted_only_once():
    """The 'no device' warning must not repeat every re-scan cycle."""
    from arctis_sound_manager.core import CoreEngine

    engine = _make_engine_stub()
    engine.device_configurations = [_make_device_config()]

    with patch.object(CoreEngine, '_find_hid_device', return_value=None):
        CoreEngine.configure_virtual_sinks(engine)
        CoreEngine.configure_virtual_sinks(engine)
        CoreEngine.configure_virtual_sinks(engine)

    # Warning logged exactly once despite three calls
    warning_calls = [c for c in engine.logger.warning.call_args_list
                     if 'No supported device' in str(c)]
    assert len(warning_calls) == 1
