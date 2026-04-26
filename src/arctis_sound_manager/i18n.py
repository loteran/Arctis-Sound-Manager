import json
import logging
from configparser import ConfigParser
from pathlib import Path

from arctis_sound_manager.constants import HOME_LANG_FOLDER


_LANG_FILE = Path.home() / ".config" / "arctis_manager" / ".language"
_logger = logging.getLogger('I18n')


class I18n:
    _instance: 'I18n'
    translations: ConfigParser
    _en_translations: ConfigParser
    _lang: str = 'en'
    _callbacks: list = []
    # Avoid logging the same missing key on every translate() call (UI refresh
    # easily produces hundreds of lookups per second).
    _missing_logged: set[tuple[str, str]] = set()

    @staticmethod
    def get_instance() -> 'I18n':
        if getattr(I18n, '_instance', None) is None:
            I18n._instance = I18n()

        return I18n._instance

    def __init__(self):
        self.translations = ConfigParser()
        self._en_translations = ConfigParser()
        # Pre-load EN once so we can fall back when the active locale lacks a key.
        en_path = Path(__file__).parent / 'lang' / 'en.ini'
        if en_path.is_file():
            try:
                self._en_translations.read(en_path)
            except Exception as e:
                _logger.warning(f'Failed to pre-load en.ini for fallback: {e!r}')
        self._callbacks = []
        saved = self._load_saved_lang()
        self.set_language(saved)

    @staticmethod
    def _load_saved_lang() -> str:
        if _LANG_FILE.exists():
            try:
                return _LANG_FILE.read_text().strip() or 'en'
            except Exception:
                pass
        return 'en'

    @staticmethod
    def _save_lang(lang_code: str) -> None:
        _LANG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LANG_FILE.write_text(lang_code)

    def set_language(self, lang_code: str, default: str = 'en') -> None:
        home_path = HOME_LANG_FOLDER / f'{lang_code or default}.ini'
        sys_path = Path(__file__).parent / 'lang' / f'{lang_code or default}.ini'

        lang_file = home_path if home_path.is_file() else sys_path
        logger = logging.getLogger('I18n')
        if not lang_file.exists():
            lang_file = Path(__file__).parent / 'lang' / f'{default}.ini'
            logger.warning(f'Language file not found, falling back to {lang_file}')

        if not lang_file.exists():
            logger.critical(f'Language file {lang_file} not found')
            return

        self._lang = lang_code or default
        self.translations = ConfigParser()
        self.translations.read(lang_file)
        self._save_lang(self._lang)

        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                pass

    @staticmethod
    def current_lang() -> str:
        return I18n.get_instance()._lang

    @staticmethod
    def on_language_changed(callback) -> None:
        I18n.get_instance()._callbacks.append(callback)

    @staticmethod
    def translate(section: str, key: str|int) -> str:
        inst = I18n.get_instance()
        skey = f'{key}'
        # 1. Active locale.
        if inst.translations.has_option(section, skey):
            return inst.translations.get(section, skey).split('#')[0].strip()
        # 2. EN fallback so a missing translation in fr/de/etc. still shows
        #    a real string instead of the bare lookup key.
        if inst._lang != 'en' and inst._en_translations.has_option(section, skey):
            sentinel = (section, skey)
            if sentinel not in inst._missing_logged:
                inst._missing_logged.add(sentinel)
                _logger.warning(
                    f'Missing translation [{inst._lang}] {section}.{skey} — using en fallback.'
                )
            return inst._en_translations.get(section, skey).split('#')[0].strip()
        # 3. No translation anywhere — return the key so the UI shows
        #    something readable, and log once.
        sentinel = (section, skey)
        if sentinel not in inst._missing_logged:
            inst._missing_logged.add(sentinel)
            _logger.warning(f'Missing translation [{inst._lang}] {section}.{skey} — using key as label.')
        return skey
