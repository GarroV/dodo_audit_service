"""Проверяющий подставляется по Telegram ID (T063, решение D032).

Аудитор не вводит своё имя руками ни при какой развилке: имя берётся из карты
`AUDITOR_NAMES`, а если ID в ней нет — из профиля Telegram. Пустой строки на
выходе быть не должно: она уехала бы в шапку отчёта партнёру.
"""

from __future__ import annotations

from src.bot.auditor import auditor_name


def test_name_from_map_wins_over_telegram_profile() -> None:
    names = {111: "Владимир Гарро"}
    assert auditor_name(111, "vlad 🍕", names) == "Владимир Гарро"


def test_unknown_id_falls_back_to_telegram_profile_name() -> None:
    assert auditor_name(222, "Пётр Петров", {111: "Владимир Гарро"}) == "Пётр Петров"


def test_blank_profile_name_gives_id_not_empty_header() -> None:
    assert auditor_name(222, "   ", {}) == "Telegram 222"


def test_profile_name_is_trimmed() -> None:
    assert auditor_name(222, "  Пётр Петров  ", {}) == "Пётр Петров"


def test_blank_value_in_map_is_ignored_like_absent() -> None:
    assert auditor_name(111, "Пётр Петров", {111: "   "}) == "Пётр Петров"
