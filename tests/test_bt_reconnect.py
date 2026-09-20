"""The Bluetooth reconnector: what it asks BlueZ for, and how often."""

from arctis_sound_manager import bt_reconnect as br


def test_mac_is_read_from_the_node_name():
    assert br.mac_of("bluez_output.30_96_10_49_54_E2.1") == "30:96:10:49:54:E2"
    assert br.mac_of("alsa_output.usb-SteelSeries-00.analog-stereo") is None
    assert br.mac_of("") is None


def _reconnector(monkeypatch, paired=True, connected=False):
    calls: list[list[str]] = []

    class _Res:
        stdout = "Connection successful\n"
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "info":
            r = _Res()
            r.stdout = (f"Device {cmd[2]}\n\tPaired: {'yes' if paired else 'no'}\n"
                        f"\tConnected: {'yes' if connected else 'no'}\n")
            return r
        return _Res()

    monkeypatch.setattr(br.subprocess, "run", fake_run)

    # Run the worker inline so the test sees the connect synchronously.
    class _Thread:
        def __init__(self, target, args, **kw):
            self._t, self._a = target, args

        def start(self):
            self._t(*self._a)

    monkeypatch.setattr(br.threading, "Thread", _Thread)
    return br.BluetoothReconnector(), calls


def test_missing_paired_device_is_asked_to_connect(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    r.tick({"chat": "bluez_output.30_96_10_49_54_E2.1"}, lambda n: False, now=0.0)
    assert ["bluetoothctl", "connect", "30:96:10:49:54:E2"] in calls


def test_attempts_back_off_and_reset_when_the_node_returns(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    saved = {"game": "bluez_output.30_96_10_49_54_E2.1"}
    r.tick(saved, lambda n: False, now=0.0)
    r.tick(saved, lambda n: False, now=5.0)       # inside the first 15 s: nothing
    assert sum(c[1] == "connect" for c in calls) == 1
    r.tick(saved, lambda n: False, now=16.0)      # second try, then 30 s
    assert sum(c[1] == "connect" for c in calls) == 2
    r.tick(saved, lambda n: False, now=40.0)
    assert sum(c[1] == "connect" for c in calls) == 2
    r.tick(saved, lambda n: True, now=41.0)       # back: backoff forgotten
    # Gone again well outside the "sent away on purpose" window (see below):
    # the first try is immediate, not the 60 s the backoff had reached.
    later = 41.0 + br.DELIBERATE_WINDOW_S + 1
    r.tick(saved, lambda n: False, now=later)
    assert sum(c[1] == "connect" for c in calls) == 3


def test_a_connected_or_unpaired_device_is_left_alone(monkeypatch):
    r, calls = _reconnector(monkeypatch, connected=True)
    r.tick({"chat": "bluez_output.30_96_10_49_54_E2.1"}, lambda n: False, now=0.0)
    assert not any(c[1] == "connect" for c in calls)
    r, calls = _reconnector(monkeypatch, paired=False)
    r.tick({"chat": "bluez_output.30_96_10_49_54_E2.1"}, lambda n: False, now=0.0)
    assert not any(c[1] == "connect" for c in calls)


def test_non_bluetooth_outputs_cost_nothing(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    probed = []
    r.tick({"game": "alsa_output.x"}, lambda n: probed.append(n) or True, now=0.0)
    assert not calls and not probed


def test_a_device_sent_away_after_a_reconnect_is_left_alone(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    saved = {"chat": "bluez_output.30_96_10_49_54_E2.1"}
    r.tick(saved, lambda n: False, now=0.0)       # transport failed: reconnect
    assert sum(c[1] == "connect" for c in calls) == 1
    r.tick(saved, lambda n: True, now=5.0)        # back
    r.tick(saved, lambda n: False, now=60.0)      # the user pressed Disconnect
    r.tick(saved, lambda n: False, now=120.0)
    r.tick(saved, lambda n: False, now=900.0)
    assert sum(c[1] == "connect" for c in calls) == 1
    r.tick(saved, lambda n: True, now=1000.0)     # the user connected it again
    r.tick(saved, lambda n: False, now=2000.0)    # much later: a failure again
    assert sum(c[1] == "connect" for c in calls) == 2


def test_a_drop_long_after_a_reconnect_is_still_a_failure(monkeypatch):
    r, calls = _reconnector(monkeypatch)
    saved = {"chat": "bluez_output.30_96_10_49_54_E2.1"}
    r.tick(saved, lambda n: False, now=0.0)
    r.tick(saved, lambda n: True, now=5.0)
    r.tick(saved, lambda n: False, now=5.0 + br.DELIBERATE_WINDOW_S + 1)
    assert sum(c[1] == "connect" for c in calls) == 2
