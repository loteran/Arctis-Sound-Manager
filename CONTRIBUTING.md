# Contributing to Arctis Sound Manager

Thank you for your interest in contributing!

## Translating ASM

ASM uses [Crowdin](https://crowdin.com/project/arctis-sound-manager) for community translations.

### How to contribute a translation

1. Go to the [Crowdin project](https://crowdin.com/project/arctis-sound-manager)
2. Create a free Crowdin account (or sign in with GitHub)
3. Select your language and start translating

Translations are synced automatically every Monday. When a language reaches **80 %** coverage, it is pulled into the next release.

### Adding a new language

If your language is not listed on Crowdin, open a [GitHub issue](https://github.com/loteran/Arctis-Sound-Manager/issues) and request it.

### Source strings

The English source file is located at:
```
src/arctis_sound_manager/lang/en.ini
```
All sections and keys must exist in `en.ini` before they can be translated.

## Code contributions

- Target the `develop` branch for all pull requests — `main` is the stable release branch.
- Follow the existing code style (no linter config yet, just keep it consistent).
- For new features, open an issue first to discuss the approach.
