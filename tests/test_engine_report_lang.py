"""T175: незнакомый язык отчёта — отказ, а не молчаливая подмена русским.

`report.py` брал язык так: `--lang`, иначе язык из шапки, иначе `ru`; а потом
неизвестное значение молча заменял на `ru` и выходил с нулевым кодом. Опечатка
в коде языка отправляла партнёру не тот документ, и узнать об этом можно было
только прочитав результат: письмо на немецком приходило русским.

Проверка `init`/`meta` этой дырки не закрывает — состояние это обычный JSON на
диске, и в расчёт приходят проверки, начатые до появления валидации шапки.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from conftest import Run


def test_письмо_на_незаведённом_языке_отказ(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    r = report("letter", "--lang", "de")
    assert r.code != 0, "письмо на незаведённом языке собрано вместо отказа"
    assert "мы провели проверку" not in r.out, "вместо немецкого письма напечатано русское"


def test_отказ_называет_язык_и_доступные(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    r = report("letter", "--lang", "de")
    assert "de" in r.text, f"в сообщении нет отвергнутого языка: {r.text!r}"
    assert "ru" in r.text and "en" in r.text, f"в сообщении нет доступных языков: {r.text!r}"


def test_html_на_незаведённом_языке_отказ(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    r = report("html", "--lang", "de")
    assert r.code != 0, "HTML на незаведённом языке собран вместо отказа"
    assert "<h1>" not in r.out, "напечатан отчёт вместо отказа"


def test_pdf_на_незаведённом_языке_не_собирается(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    r = report("pdf", "--lang", "de", "--out", str(workdir / "отчёт.pdf"))
    assert r.code != 0, "PDF на незаведённом языке собран вместо отказа"
    assert not (workdir / "отчёт.pdf").exists(), "файл отчёта создан при отказе"


def test_незаведённый_язык_в_шапке_проверки_отказ(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Шапку правят руками и приносят из проверок, начатых до валидации."""
    path = workdir / "inspection.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["meta"]["lang"] = "de"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    r = report("letter")
    assert r.code != 0, "язык из шапки молча заменён русским"
    assert "de" in r.text, f"в сообщении нет отвергнутого языка: {r.text!r}"


def test_заведённый_язык_по_прежнему_печатается(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Сторож против отказа на всём подряд: русский и английский не тронуты."""
    for lang, marker in (("ru", "мы провели проверку"), ("en", "we inspected")):
        r = report("letter", "--lang", lang)
        assert r.code == 0, r.text
        assert marker in r.out, f"письмо на «{lang}» не собралось: {r.out!r}"
