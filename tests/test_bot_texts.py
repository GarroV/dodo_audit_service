"""Каталог текстов интерфейса: язык — параметр, а не константа.

Проверяется не красота формулировок, а три свойства, на которых держится
принцип проекта: текст берётся по ключу, обе локали заполнены одновременно, а
неизвестный язык отвергается вместо молчаливого отката на русский.
"""

from __future__ import annotations

import pytest

from src.bot.errors import BotTextError
from src.bot.texts import TEXTS, UI_LANGS, t


def test_known_key_returns_text_in_asked_language() -> None:
    assert t("start.ask_unit", "ru") != t("start.ask_unit", "en")


@pytest.mark.parametrize("lang", UI_LANGS)
def test_every_key_is_filled_in_every_language(lang: str) -> None:
    missing = [key for key, langs in TEXTS.items() if not (langs.get(lang) or "").strip()]
    assert missing == [], f"нет перевода на «{lang}»: {missing}"


def test_unknown_language_is_refused_not_silently_russian() -> None:
    with pytest.raises(BotTextError, match="sr"):
        t("start.ask_unit", "sr")


def test_unknown_key_is_refused() -> None:
    with pytest.raises(BotTextError, match=r"нет в каталоге"):
        t("start.no_such_key", "ru")


def test_parameters_are_substituted() -> None:
    text = t("start.resumed", "ru", unit="Белград 2", date="2026-09-02", findings=3)
    assert "Белград 2" in text
    assert "2026-09-02" in text
    assert "3" in text


def test_missing_parameter_is_refused_not_printed_as_brace() -> None:
    with pytest.raises(BotTextError, match="unit"):
        t("start.resumed", "ru", date="2026-09-02", findings=3)
