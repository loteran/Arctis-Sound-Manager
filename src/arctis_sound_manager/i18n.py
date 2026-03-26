import json
import logging
from configparser import ConfigParser
from pathlib import Path

from arctis_sound_manager.constants import HOME_LANG_FOLDER


_LANG_FILE = Path.home() / ".config" / "arctis_manager" / ".language"


class I18n:
    _instance: 'I18n'
    translations: ConfigParser
    _lang: str = 'en'
    _callbacks: list = []

    @staticmethod
    def get_instance() -> 'I18n':
        if getattr(I18n, '_instance', None) is None:
            I18n._instance = I18n()

        return I18n._instance

    def __init__(self):
        self.translations = ConfigParser()
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
        return f'{I18n.get_instance().translations.get(section, f"{key}", fallback=key)}'.split('#')[0].strip()
