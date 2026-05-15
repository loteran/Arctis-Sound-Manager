import json
import logging
from configparser import ConfigParser, RawConfigParser
from pathlib import Path

from arctis_sound_manager.constants import HOME_LANG_FOLDER

_BUILTIN_LANG_DIR = Path(__file__).parent / 'lang'


_LANG_FILE = Path.home() / ".config" / "arctis_manager" / ".language"
_logger = logging.getLogger('I18n')

try:
    from babel import Locale as _BabelLocale
    _BABEL_AVAILABLE = True
except ImportError:
    _BabelLocale = None  # type: ignore[assignment,misc]
    _BABEL_AVAILABLE = False
    _logger.warning('babel not installed — plural forms will fall back to English one/other rule')

_FALLBACK_CATEGORY = 'other'


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
        self.translations = RawConfigParser()
        self._en_translations = RawConfigParser()
        self._babel_locale_cache: dict[str, object] = {}
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
        self.translations = RawConfigParser()
        self.translations.read(lang_file)
        self._save_lang(self._lang)

        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                pass

    @staticmethod
    def available_languages() -> list[tuple[str, str]]:
        """Return (code, display_name) for languages with >= 80% coverage, sorted by code.

        Display names come from babel (native name). English is always included.
        Scans built-in lang dir first, then HOME_LANG_FOLDER for community downloads.
        """
        en_cp = RawConfigParser()
        en_cp.read(_BUILTIN_LANG_DIR / 'en.ini')
        en_key_count = sum(len(list(en_cp.items(s))) for s in en_cp.sections())
        threshold = int(en_key_count * 0.80)

        def _native_name(code: str) -> str:
            if _BABEL_AVAILABLE and _BabelLocale is not None:
                try:
                    name = _BabelLocale.parse(code).get_display_name(code)
                    if name:
                        return name[0].upper() + name[1:]
                except Exception:
                    pass
            return code.upper()

        def _key_count(cp: RawConfigParser) -> int:
            return sum(len(list(cp.items(s))) for s in cp.sections())

        seen: dict[str, str] = {}
        for path in sorted(_BUILTIN_LANG_DIR.glob("*.ini")):
            code = path.stem
            if code == 'en':
                seen['en'] = 'English'
                continue
            cp = RawConfigParser()
            try:
                cp.read(path)
            except Exception:
                continue
            if _key_count(cp) >= threshold:
                seen[code] = _native_name(code)

        if HOME_LANG_FOLDER.exists():
            for path in sorted(HOME_LANG_FOLDER.glob("*.ini")):
                code = path.stem
                if code in seen:
                    continue
                cp = RawConfigParser()
                try:
                    cp.read(path)
                except Exception:
                    continue
                if _key_count(cp) >= threshold:
                    seen[code] = _native_name(code)

        return sorted(seen.items())

    @staticmethod
    def current_lang() -> str:
        return I18n.get_instance()._lang

    @staticmethod
    def on_language_changed(callback) -> None:
        I18n.get_instance()._callbacks.append(callback)

    def _plural_category(self, count: int) -> str:
        if not _BABEL_AVAILABLE:
            return 'one' if count == 1 else 'other'
        cached = self._babel_locale_cache.get(self._lang)
        if cached is None:
            try:
                cached = _BabelLocale.parse(self._lang)
            except Exception as e:
                _logger.warning(f'Babel could not parse locale {self._lang!r}: {e!r} — using en rule')
                try:
                    cached = _BabelLocale.parse('en')
                except Exception:
                    return 'one' if count == 1 else 'other'
            self._babel_locale_cache[self._lang] = cached
        return cached.plural_form(count)

    @staticmethod
    def translate_plural(section: str, key: str, count: int, **fmt) -> str:
        inst = I18n.get_instance()
        category = inst._plural_category(count)
        fmt.setdefault('count', count)

        def _lookup(cp: ConfigParser, cat: str) -> str | None:
            skey = f'{key}_{cat}'
            if cp.has_option(section, skey):
                return cp.get(section, skey).split('#')[0].strip()
            return None

        for cp in (inst.translations, inst._en_translations if inst._lang != 'en' else None):
            if cp is None:
                continue
            raw = _lookup(cp, category) or _lookup(cp, _FALLBACK_CATEGORY)
            if raw is not None:
                try:
                    return raw.format(**fmt)
                except (KeyError, IndexError) as e:
                    _logger.warning(
                        f'Plural format error [{inst._lang}] {section}.{key}_{category}: {e!r}'
                    )
                    return raw

        sentinel = (section, f'{key}_*')
        if sentinel not in inst._missing_logged:
            inst._missing_logged.add(sentinel)
            _logger.warning(
                f'Missing plural translation [{inst._lang}] {section}.{key} (count={count})'
            )
        return key

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
