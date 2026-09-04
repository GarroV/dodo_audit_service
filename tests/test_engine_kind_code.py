"""T177: вид проверки хранится кодом, а словом становится при печати.

Вид проверки записывался в шапку движка готовым СЛОВОМ — по языку отчёта, тем,
что был выбран при заведении проверки. Дальше письмо на другом языке переводить
было нечем: движок сопоставлял строки таблицей `TYPE_EN` с молчаливым возвратом
исходника. Кончалось это тем, что за одним видом было закреплено два разных
английских слова («Planned» в проверке, «Scheduled» в таблице движка), а письмо
на русском по английской проверке уходило партнёру с английским словом в шапке.

Здесь проверяется обратное: код лежит в проверке, слово подставляется по языку
ПЕЧАТИ, а там, где кода нет (проверки, заведённые до этой правки), движок
отказывается печатать вид на чужом языке вместо того, чтобы подставить слово,
записанное на другом.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from conftest import Run

from src.domain import INSPECTION_KINDS


def meta_of(workdir: Path) -> dict:
    return json.loads((workdir / "inspection.json").read_text(encoding="utf-8"))["meta"]


def type_line(letter: str) -> str:
    """Строка письма, в которой напечатан вид проверки."""
    for line in letter.splitlines():
        if "Вид проверки" in line or "Inspection type" in line:
            return line
    raise AssertionError(f"в письме нет строки с видом проверки:\n{letter}")


def test_код_вида_ложится_в_шапку_кодом(audit: Callable[..., Run], workdir: Path) -> None:
    r = audit("init", "--unit", "Белград-1", "--kind", "repeat", "--lang", "ru")
    assert r.code == 0, r.text
    m = meta_of(workdir)
    assert m["kind"] == "repeat", f"вид проверки записан не кодом: {m!r}"
    assert not m.get("type"), "рядом с кодом осталось слово — два поля на один факт"


def test_русская_проверка_печатает_вид_по_языку_письма(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Первое направление дефекта: проверка на русском, письмо на английском."""
    assert audit("init", "--unit", "Белград-1", "--kind", "repeat", "--lang", "ru").code == 0
    ru = report("letter")
    assert ru.code == 0, ru.text
    assert "Повторная" in type_line(ru.out), type_line(ru.out)
    en = report("letter", "--lang", "en")
    assert en.code == 0, en.text
    assert "Repeat" in type_line(en.out), type_line(en.out)


def test_английская_проверка_печатает_вид_по_языку_письма(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Второе направление: проверка на английском, письмо запрошено на русском."""
    assert audit("init", "--unit", "Belgrade-1", "--kind", "unscheduled", "--lang", "en").code == 0
    en = report("letter")
    assert en.code == 0, en.text
    assert "Unscheduled" in type_line(en.out), type_line(en.out)
    ru = report("letter", "--lang", "ru")
    assert ru.code == 0, ru.text
    assert "Внеплановая" in type_line(ru.out), type_line(ru.out)


def test_у_вида_одно_английское_слово_а_не_два(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """`Scheduled` из снятой таблицы движка не должно остаться нигде."""
    assert audit("init", "--unit", "Белград-1", "--kind", "planned", "--lang", "ru").code == 0
    en = report("letter", "--lang", "en")
    assert en.code == 0, en.text
    assert "Scheduled" not in en.out, "печатается слово снятой таблицы TYPE_EN"
    assert "Planned" in type_line(en.out), type_line(en.out)


def test_неизвестный_код_вида_отвергается(audit: Callable[..., Run], workdir: Path) -> None:
    r = audit("init", "--unit", "Белград-1", "--kind", "вечерняя")
    assert r.code != 0, "вид проверки вне справочника обязан быть отклонён"
    assert "вечерняя" in r.text, f"в сообщении нет отвергнутого кода: {r.text!r}"
    assert "planned" in r.text, f"в сообщении нет известных кодов: {r.text!r}"
    assert not (workdir / "inspection.json").exists(), "состояние создано при отказе"


def test_код_и_слово_вместе_отвергаются(audit: Callable[..., Run], workdir: Path) -> None:
    """Два поля на один факт — не выбор движка, а вопрос к вызывающему."""
    r = audit("init", "--unit", "Белград-1", "--kind", "planned", "--type", "Плановая")
    assert r.code != 0, "код и слово вместе не должны молча выбирать одно из двух"
    assert not (workdir / "inspection.json").exists(), "состояние создано при отказе"


def test_без_вида_проверка_плановая_кодом(audit: Callable[..., Run], workdir: Path) -> None:
    """Умолчание осталось прежним по смыслу, но записывается кодом, а не словом."""
    r = audit("init", "--unit", "Белград-1", "--lang", "en")
    assert r.code == 0, r.text
    assert meta_of(workdir)["kind"] == "planned", meta_of(workdir)


def test_старая_проверка_печатается_на_своём_языке_как_прежде(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Проверка, заведённая словом: на своём языке письмо не меняется ни в знаке."""
    assert audit("init", "--unit", "Белград-1", "--type", "Плановая", "--lang", "ru").code == 0
    ru = report("letter")
    assert ru.code == 0, ru.text
    assert "Вид проверки — Плановая." in ru.out, type_line(ru.out)


def test_старая_проверка_на_чужом_языке_отказ_а_не_чужое_слово(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Ровно то, что уходило партнёру: русское слово в английском письме."""
    assert audit("init", "--unit", "Белград-1", "--type", "Плановая", "--lang", "ru").code == 0
    en = report("letter", "--lang", "en")
    assert en.code != 0, "вид, записанный словом на другом языке, напечатан как есть"
    assert "Плановая" in en.text, f"в сообщении нет записанного слова: {en.text!r}"
    assert "--kind" in en.text, f"в сообщении нет способа починить: {en.text!r}"


def test_meta_переводит_старую_проверку_на_код(
    audit: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    assert audit("init", "--unit", "Белград-1", "--type", "Плановая", "--lang", "ru").code == 0
    r = audit("meta", "--kind", "planned")
    assert r.code == 0, r.text
    m = meta_of(workdir)
    assert m["kind"] == "planned" and not m.get("type"), m
    en = report("letter", "--lang", "en")
    assert en.code == 0, en.text
    assert "Planned" in type_line(en.out), type_line(en.out)


def test_неизвестный_код_в_шапке_роняет_печать(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Состояние — обычный JSON на диске, и в него приходят чужие правки."""
    path = workdir / "inspection.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["meta"]["kind"] = "вечерняя"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    r = report("letter")
    assert r.code != 0, "неизвестный код вида напечатан как есть"
    assert "вечерняя" in r.text, r.text


def test_таблица_видов_движка_и_предметной_области_не_разошлись(
    audit: Callable[..., Run],
) -> None:
    """Движок не импортирует `src` по контракту слоёв, поэтому таблица у него своя.

    Своя таблица — это дубль факта, и единственное, что держит дубль честным, —
    проверка. Разойдутся (переименование, третий язык в одном месте из двух) —
    краснеет здесь, а не в шапке письма у партнёра.
    """
    r = audit("kinds")
    assert r.code == 0, r.text
    got: dict[str, dict[str, str]] = {}
    for line in r.out.splitlines():
        if not line.strip():
            continue
        code, *titles = [p.strip() for p in line.split("|")]
        got[code] = dict(t.split("=", 1) for t in titles)
    expected = {code: dict(titles) for code, titles in INSPECTION_KINDS.items()}
    assert got == expected, "таблица видов проверки в движке разошлась с src/domain/kinds.py"
