"""Internationalisation — translation loader and Jinja2 helper."""

import json
from pathlib import Path
from typing import Callable

_translations: dict[str, dict[str, str]] = {}
SUPPORTED_LANGS = {"en", "ar"}
DEFAULT_LANG = "en"


def load_translations() -> None:
    trans_dir = Path("frontend/translations")
    if not trans_dir.exists():
        return
    for lang_file in trans_dir.glob("*.json"):
        lang = lang_file.stem
        if lang in SUPPORTED_LANGS:
            _translations[lang] = json.loads(lang_file.read_text(encoding="utf-8"))


def translate(key: str, lang: str = DEFAULT_LANG) -> str:
    return _translations.get(lang, {}).get(key, key)


def make_translator(lang: str) -> Callable[[str], str]:
    """Returns a bound translate function for use in Jinja2 context."""

    def _t(key: str) -> str:
        return translate(key, lang)

    return _t


def detect_lang(cookies: dict[str, str], accept_language: str = "") -> str:
    lang = cookies.get("lang", "")
    if lang in SUPPORTED_LANGS:
        return lang
    if "ar" in accept_language.lower():
        return "ar"
    return DEFAULT_LANG
