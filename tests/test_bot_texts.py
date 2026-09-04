"""Каталог текстов интерфейса: язык — параметр, а не константа.

Проверяется не красота формулировок, а три свойства, на которых держится
принцип проекта: текст берётся по ключу, обе локали заполнены одновременно, а
неизвестный язык отвергается вместо молчаливого отката на русский.
"""

from __future__ import annotations

from string import Formatter

import pytest

from src.bot.errors import BotTextError
from src.bot.texts import TEXTS, UI_LANGS, t


def _params(template: str) -> set[str]:
    """Имена параметров строки каталога: `{note}` и `{cue}` из шаблона."""
    return {field for _, field, _, _ in Formatter().parse(template) if field}


def test_known_key_returns_text_in_asked_language() -> None:
    assert t("start.ask_unit", "ru") != t("start.ask_unit", "en")


@pytest.mark.parametrize("lang", UI_LANGS)
def test_every_key_is_filled_in_every_language(lang: str) -> None:
    missing = [key for key, langs in TEXTS.items() if not (langs.get(lang) or "").strip()]
    assert missing == [], f"нет перевода на «{lang}»: {missing}"


def test_every_language_of_a_key_takes_the_same_parameters() -> None:
    """Расхождение параметров между локалями — отказ у второго языка, и только у него.

    Хендлер подставляет один и тот же набор на любом языке. Появись в
    английской строке параметр, которого нет в русской (или наоборот), русский
    аудитор ничего не заметит, а английский получит отказ каталога вместо
    ответа — и найдётся это на точке, а не здесь. Проверка тем нужнее, чем
    длиннее строка: у быстрого пути (T117) их шесть в одном сообщении.
    """
    mismatched = {
        key: {lang: sorted(_params(text)) for lang, text in langs.items()}
        for key, langs in TEXTS.items()
        if len({frozenset(_params(text)) for text in langs.values()}) > 1
    }
    assert mismatched == {}, f"наборы параметров разошлись между языками: {mismatched}"


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


def test_parameter_named_lang_does_not_collide_with_interface_language() -> None:
    """У подтверждения старта есть параметр «язык отчёта» — он не должен спорить с языком текста."""
    text = t(
        "start.started",
        "ru",
        unit="Белград 2",
        kind="Плановая",
        lang="English",
        auditor="Владимир Гарро",
        # Строка про обрезку имени (T128): пустая, когда обрезки не было, — но
        # параметр обязателен, потому что `t()` отказывает на недостающем.
        auditor_note="",
        date="2026-09-02",
    )
    assert "English" in text
    assert "Плановая" in text
