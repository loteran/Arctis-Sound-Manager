# Copyright (C) 2026 loteran
# SPDX-License-Identifier: GPL-3.0-or-later
"""#337: stream_mix (0x47) ends with the stream's mic level (spec field
`mic`; GG on a Nova Elite sends 01 47 64 64 64 0a). ASM sent three values,
so the padding set the stream's mic to 0 on every write."""
from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from arctis_sound_manager.config import DeviceConfiguration
from arctis_sound_manager.core import CoreEngine
from tests.test_status_untagged_replies import DEVICES_DIR, _make_engine

_yaml = YAML(typ="safe")
STREAM = ("line_out_stream_main_left_volume", "line_out_stream_main_right_volume",
          "line_out_stream_aux_volume", "line_out_stream_mic_volume")


@pytest.mark.parametrize("profile, rid", [("nova_elite.yaml", 0x01),
                                          ("nova_pro_omni.yaml", 0x01),
                                          ("nova_pro_wireless.yaml", 0x06)])
def test_every_stream_mix_write_carries_the_mic(profile, rid):
    cfg = DeviceConfiguration(_yaml.load(DEVICES_DIR / profile))
    engine = _make_engine(cfg)
    engine.device_settings = {"line_out_stream_main_left_volume": 80,
                              "line_out_stream_main_right_volume": 80,
                              "line_out_stream_aux_volume": 60,
                              "line_out_stream_mic_volume": 10}
    settings = {c.name: c for section in cfg.settings.values() for c in section}
    assert "line_out_stream_mic_volume" in settings
    for name in STREAM:
        frame = CoreEngine._resolve_update_sequence(engine, settings[name],
                                                    engine.device_settings[name])
        assert frame[:2] == [rid, 0x47] and len(frame) == 6, (name, frame)
        assert frame[5] == 10, (name, frame)
