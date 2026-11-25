"""A downloaded language file is laid over the bundled one, not swapped in.

Upstream ships most locales as empty Crowdin stubs; lang_updater copies them
into HOME_LANG_FOLDER, and a stub there used to hide a complete bundled
translation."""
from arctis_sound_manager import i18n


def _load(monkeypatch, tmp_path, home_text: str | None):
    builtin = tmp_path / 'builtin'
    builtin.mkdir()
    (builtin / 'xx.ini').write_text('[ui]\na = bundled A\nb = bundled B\n')
    home = tmp_path / 'home'
    home.mkdir()
    if home_text is not None:
        (home / 'xx.ini').write_text(home_text)
    monkeypatch.setattr(i18n, 'HOME_LANG_FOLDER', home)
    monkeypatch.setattr(i18n, '__file__', str(builtin.parent / 'i18n.py'))
    (builtin.parent / 'lang').symlink_to(builtin)
    monkeypatch.setattr(i18n.I18n, '_save_lang', staticmethod(lambda code: None))
    inst = i18n.I18n.__new__(i18n.I18n)
    inst._callbacks = []
    inst.set_language('xx')
    return inst.translations


def test_an_empty_downloaded_stub_does_not_hide_the_bundled_file(monkeypatch, tmp_path):
    t = _load(monkeypatch, tmp_path, '[status]\n')
    assert t.get('ui', 'a') == 'bundled A'


def test_keys_in_the_download_still_win(monkeypatch, tmp_path):
    t = _load(monkeypatch, tmp_path, '[ui]\na = downloaded A\n')
    assert t.get('ui', 'a') == 'downloaded A'
    assert t.get('ui', 'b') == 'bundled B'


def test_without_a_download_the_bundled_file_is_used(monkeypatch, tmp_path):
    assert _load(monkeypatch, tmp_path, None).get('ui', 'b') == 'bundled B'


def test_an_unreadable_download_keeps_the_bundled_file(monkeypatch, tmp_path):
    """Not the default language: the bundled translation was fine."""
    t = _load(monkeypatch, tmp_path, "[ui]\na = x\n[ui]\nb = y\n")   # duplicate section
    assert t.get("ui", "a") == "bundled A"
