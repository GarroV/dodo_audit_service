"""T041: кириллица из шрифта проекта и нумерация страниц через CSS Paged Media.

Отчёт печатают и подшивают, поэтому страницы должны быть пронумерованы, а буквы
— читаться на любой машине. Оба свойства проверяются на собранном PDF, а не по
коду: на чистом контейнере вместо кириллицы получались квадратики, и заметить
это по исходнику было нельзя.

Разметка проверяется через `report.py html` — тот же HTML, из которого
собирается PDF (`docs/04-engine.md`), разбирать PDF ради текста не приходится.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import ROOT, TEST_DATA, Run

ШРИФТЫ = ROOT / "engine" / "assets" / "fonts"

HAS_PDFTOTEXT = shutil.which("pdftotext") is not None
# Извлечение текста из PDF нужно только тестам: в продукте его нет, поэтому в
# зависимости проекта poppler не тянется. Без него остаются проверки по HTML.
requires_pdftotext = pytest.mark.skipif(
    not HAS_PDFTOTEXT, reason="нет pdftotext (poppler) — текст из PDF не достать"
)


def html_of(report: Callable[..., Run], *args: str) -> str:
    r = report("html", *args)
    assert r.code == 0, r.text
    return r.out


def текст_pdf(path: Path) -> str:
    out = subprocess.run(  # noqa: S603 — аргументы собраны здесь, оболочки нет
        [shutil.which("pdftotext") or "pdftotext", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


# --- шрифт и кириллица -------------------------------------------------------


def test_шрифт_лежит_в_проекте() -> None:
    """Шрифт берётся из репозитория, а не из системы: иначе вид зависит от машины."""
    for имя in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        assert (ШРИФТЫ / имя).is_file(), f"нет файла шрифта {имя} в {ШРИФТЫ}"


def test_шрифт_подключён_абсолютным_путём(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    html = html_of(report)
    assert "@font-face" in html, "шрифт отчёта не подключён вовсе"
    assert f"file://{ШРИФТЫ}/DejaVuSans.ttf" in html.replace("%20", " "), (
        "шрифт подключён не абсолютным путём к файлу в проекте"
    )


@requires_pdftotext
def test_кириллица_в_pdf_читается(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Квадратики вместо букв — это то, ради чего шрифт положили в проект."""
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = report("pdf", "--out", "отчёт.pdf")
    assert r.code == 0, r.text
    текст = текст_pdf(workdir / "отчёт.pdf")
    assert "Отчёт о проверке пиццерии" in текст, "заголовок из PDF не читается как кириллица"
    # Название зоны берётся из методики, а не вписано сюда (T146): вписанное
    # означало бы копию данных управляющей компании в публичном репозитории, а
    # проверяется здесь не оно, а то, что кириллица вышла буквами.
    зона = next(
        r["name_ru"]
        for r in csv.DictReader((TEST_DATA / "zones.csv").open(encoding="utf-8-sig"))
        if r["code"] == "hot_kitchen"
    )
    assert зона in текст, "название зоны из PDF не читается"


# --- нумерация страниц -------------------------------------------------------


def test_нумерация_страниц_объявлена_в_css(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    html = html_of(report)
    assert "@bottom-center" in html, "нижнего колонтитула страницы нет"
    assert "counter(page)" in html, "номер страницы не выводится"
    assert "counter(pages)" in html, "общее число страниц не выводится"
    assert "стр." in html, "подпись номера страницы не на языке отчёта"


def test_нумерация_страниц_на_языке_отчёта(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Язык — параметр: английский отчёт не должен получить русское «стр.»."""
    html = html_of(report, "--lang", "en")
    assert "counter(page)" in html, "в английском отчёте нет номера страницы"
    assert '"p. "' in html.replace("' ", '" ').replace(" '", ' "'), (
        f"подпись номера страницы не переведена: {html[:400]!r}"
    )
    assert "стр." not in html, "в английском отчёте русская подпись номера страницы"


@requires_pdftotext
def test_номер_страницы_напечатан_в_pdf(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = report("pdf", "--out", "отчёт.pdf")
    assert r.code == 0, r.text
    текст = текст_pdf(workdir / "отчёт.pdf")
    assert "стр. 1 / " in текст, f"номер страницы не напечатан в PDF: {текст[:300]!r}"
