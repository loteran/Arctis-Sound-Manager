"""Every general ConfigSetting needs an annotated GeneralSettings field.

A ConfigSetting declared in settings_config without a matching annotated
field never reaches GeneralSettings.to_dict() — so the Settings page has no
value and draws no row — and GeneralSettings.__init__ drops it when the file
is read back. preset_sync_enabled / preset_sync_announce shipped that way in
v1.7.4: the toggle was documented but invisible.
"""
from arctis_sound_manager.settings import GeneralSettings


def test_every_general_config_setting_has_a_field():
    fields = GeneralSettings.__annotations__
    configs = [*GeneralSettings.settings_config, *GeneralSettings.dac_settings_config]
    missing = [c.name for c in configs if c.name not in fields]
    assert missing == []


def test_preset_sync_toggles_reach_the_gui_and_survive_a_reload():
    d = GeneralSettings().to_dict()
    assert d['preset_sync_enabled'] is True
    assert d['preset_sync_announce'] is True
    gs = GeneralSettings(preset_sync_enabled=False)
    assert gs.to_dict()['preset_sync_enabled'] is False
