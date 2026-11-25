"""Volume Boost / Smart Volume level changes are applied live, without the
filter-chain restart that recreates every channel (and pauses a browser)."""
from unittest.mock import MagicMock

from arctis_sound_manager.sonar_to_pipewire import (_SMART_PRESETS, _sc4m_node,
                                                    diff_filter_conf)

LIMITER = ('    { type = ladspa  name = limiter  plugin = /l.so  label = fastLookaheadLimiter'
           '  control = { "Input gain (dB)" = %s  "Limit (dB)" = -1.0  "Release time (s)" = 0.1 } }')


def _hesuvi(gain, header='# HeSuVi  |  Immersion: 50%'):
    return '\n'.join([header, 'nodes = [', LIMITER % gain, ']'])


def test_ladspa_control_and_comment_changes_are_live_when_opted_in():
    old, new = _hesuvi('0.0'), _hesuvi('-3.0', '# HeSuVi  |  Immersion: 50%  |  Boost: 3.0 dB')
    assert diff_filter_conf(old, new, ladspa=True) == {'limiter': {'Input gain (dB)': -3.0}}
    # The default stays as strict as before for every other caller.
    assert diff_filter_conf(old, new) is None


def test_two_line_ladspa_node():
    a = _sc4m_node('compressor', _SMART_PRESETS['balanced'], 30, '/p.so')
    b = _sc4m_node('compressor', _SMART_PRESETS['balanced'], 70, '/p.so')
    diff = diff_filter_conf(f'x\n{a}\ny', f'x\n{b}\ny', ladspa=True)
    assert set(diff) == {'compressor'} and 'Ratio (1:n)' in diff['compressor']


def test_ladspa_shape_changes_still_need_a_restart():
    renamed = LIMITER.replace('"Limit (dB)"', '"Ceiling (dB)"')
    assert diff_filter_conf(LIMITER % '0.0', renamed % '0.0', ladspa=True) is None
    other_plugin = (LIMITER % '0.0').replace('/l.so', '/m.so')
    assert diff_filter_conf(LIMITER % '0.0', other_plugin, ladspa=True) is None


def test_live_apply_plan(tmp_path, monkeypatch):
    from arctis_sound_manager.gui import sonar_page

    conf = tmp_path / 'h.conf'
    conf.write_text(_hesuvi('0.0'))
    before = {'effect_input.hesuvi': (conf, conf.read_text())}
    setter = MagicMock(return_value=True)
    monkeypatch.setattr('arctis_sound_manager.pw_utils.set_filter_controls', setter)

    assert sonar_page._live_apply_conf_changes(before) is True   # nothing changed
    setter.assert_not_called()

    conf.write_text(_hesuvi('-3.0'))
    assert sonar_page._live_apply_conf_changes(before) is True
    setter.assert_called_once_with('effect_input.hesuvi', {'limiter:Input gain (dB)': -3.0})

    setter.return_value = False                                   # set-param refused
    assert sonar_page._live_apply_conf_changes(before) is False

    conf.write_text('nodes = [\n]')                               # shape change
    assert sonar_page._live_apply_conf_changes(before) is False


def test_multi_line_ladspa_node():
    """plugin / label on lines of their own: the node is still matched."""
    def conf(level):
        return "\n".join(["{ type = ladspa  name = compressor",
                           "  plugin = /p.so", "  label = sc4m",
                           f'  control = {{ "Ratio (1:n)" = {level} }} }}'])
    assert diff_filter_conf(conf("2.0"), conf("3.0"), ladspa=True) == {
        "compressor": {"Ratio (1:n)": 3.0}}
