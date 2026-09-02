"""Нормализация названия точки: ключ сопоставления повторного ввода (T091).

Чистая функция — Postgres не нужен, поэтому тесты идут всегда, без пропуска.
"""

from __future__ import annotations

from src.db.units import normalize_unit_name


def test_совпадает_с_точностью_до_пробелов_и_регистра() -> None:
    assert normalize_unit_name("Белград-1") == normalize_unit_name("  белград-1  ")


def test_схлопывает_внутренние_повторные_пробелы() -> None:
    assert normalize_unit_name("Белград   1") == normalize_unit_name("Белград 1")


def test_разные_точки_дают_разный_ключ() -> None:
    assert normalize_unit_name("Белград-1") != normalize_unit_name("Белград-2")


def test_не_путает_кириллицу_и_латиницу_визуально_похожие() -> None:
    """«О» из «БГ2» и «O» латиницей — разные символы, справочник синонимов сюда не входит (T092)."""
    assert normalize_unit_name("БГ2") != normalize_unit_name("BG2")
