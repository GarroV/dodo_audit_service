"""T011, T015, T016, T017: честные коды возврата и содержимое отчёта.

Главная находка, из-за которой блок вообще существует: `report.py pdf`
печатал путь и возвращал 0, не собрав файл. Бот по коду возврата решил бы,
что отчёт готов, и отдал бы аудитору мусор.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Run, requires_data

pytestmark = requires_data

HAS_RENDERER = importlib.util.find_spec("weasyprint") is not None
# Правдоподобный «прошлый отчёт»: заголовок настоящего PDF и достаточный размер.
STALE_PDF = b"%PDF-1.4 old report" + b"\0" * 3000
requires_renderer = pytest.mark.skipif(not HAS_RENDERER, reason="WeasyPrint не установлен")

# Дата проверки во всех тестах — 2026-08-21 (фикстура `started`).
# CLN05: срок 14 дней → 04.09.2026. CLN06: срок 1 день → 22.08.2026.
# PRD09 D2: срок 10 дней с потолком D2 = 10 → 31.08.2026. D3 — «немедленно».


def html_of(report: Callable[..., Run]) -> str:
    r = report("html")
    assert r.code == 0, r.text
    return r.out


# --- T011: коды возврата -----------------------------------------------------


def test_pdf_без_рендерера_возвращает_ненулевой_код(
    started: Callable[..., Run],
    report: Callable[..., Run],
    workdir: Path,
    no_renderer: dict[str, str],
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = report("pdf", "--out", "отчёт.pdf", env_extra=no_renderer)
    assert r.code != 0, "сборка провалилась, а код успеха — бот отдаст аудитору пустоту"
    assert not (workdir / "отчёт.pdf").exists(), "файла нет, но он объявлен собранным"


def test_pdf_не_выдаёт_за_успех_старый_файл(
    started: Callable[..., Run],
    report: Callable[..., Run],
    workdir: Path,
    no_renderer: dict[str, str],
) -> None:
    """Бот пишет отчёт в одно и то же место: прошлый файл не доказательство сборки."""
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    stale = workdir / "отчёт.pdf"
    stale.write_bytes(STALE_PDF)
    r = report("pdf", "--out", "отчёт.pdf", env_extra=no_renderer)
    assert r.code != 0, "старый файл по пути --out засчитан за собранный отчёт"


def test_pdf_не_теряет_прошлый_отчёт_при_провале(
    started: Callable[..., Run],
    report: Callable[..., Run],
    workdir: Path,
    no_renderer: dict[str, str],
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    stale = workdir / "отчёт.pdf"
    stale.write_bytes(STALE_PDF)
    report("pdf", "--out", "отчёт.pdf", env_extra=no_renderer)
    assert stale.exists(), "провал новой сборки снёс прошлый отчёт"


def test_pdf_в_несуществующую_папку_падает(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = report("pdf", "--out", "нет-такой-папки/отчёт.pdf")
    assert r.code != 0, "путь в несуществующую папку обязан быть отказом"
    assert "Traceback" not in r.err, f"вместо внятного отказа — трейсбек: {r.err[-300:]!r}"
    assert "нет-такой-папки" in r.text, f"в сообщении не названа папка: {r.text!r}"


@requires_renderer
def test_pdf_собирается_и_это_настоящий_pdf(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = report("pdf", "--out", "отчёт.pdf")
    assert r.code == 0, r.text
    out = workdir / "отчёт.pdf"
    assert out.exists(), "код 0, а файла нет"
    assert out.read_bytes()[:5] == b"%PDF-", "собрано что-то, но это не PDF"
    assert out.stat().st_size > 1000, "PDF подозрительно пустой"


@requires_renderer
def test_pdf_перезаписывает_свой_прошлый_файл(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    out = workdir / "отчёт.pdf"
    out.write_bytes(STALE_PDF)
    r = report("pdf", "--out", "отчёт.pdf")
    assert r.code == 0, r.text
    assert b"\0\0\0\0" not in out.read_bytes()[:200], "старый файл остался на месте"


def test_letter_печатает_текст_и_возвращает_ноль(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = report("letter")
    assert r.code == 0, r.text
    assert len(r.out.strip()) > 200, f"письмо подозрительно короткое: {r.out!r}"


def test_letter_падает_на_проверке_без_пиццерии(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Состояния, созданные до валидации шапки, не должны давать письмо-пустышку."""
    path = workdir / "inspection.json"
    st = json.loads(path.read_text(encoding="utf-8"))
    st["meta"]["unit"] = ""
    path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    r = report("letter")
    assert r.code != 0, "письмо партнёру без названия точки не должно уходить как готовое"


# --- T015: сроки устранения --------------------------------------------------


def test_срок_устранения_печатается_по_каждому_нарушению(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    started("add", "--qid", "CLN06", "--level", "D1", "--zone", "dining")
    html = html_of(report)
    assert html.count("Устранить до") >= 2, "срок напечатан не у каждого нарушения"
    assert "04.09.2026" in html, "срок CLN05 (14 дней от 21.08.2026) не напечатан"
    assert "22.08.2026" in html, "срок CLN06 (1 день от 21.08.2026) не напечатан"


def test_срок_критического_нарушения_немедленно(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "PRD09", "--level", "D3", "--zone", "fridge")
    html = html_of(report)
    assert "немедленно" in html, "у D3 срок обязан быть «немедленно», а не дата"


def test_срок_не_печатается_у_информационных_записей(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "INF10", "--level", "D0", "--zone", "fridge")
    html = html_of(report)
    assert "Устранить до" not in html, "у замера температуры нет срока устранения"


# --- T016: два шаблона письма ------------------------------------------------


def test_письмо_чистой_проверки_без_плана_действий(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Обе боевые проверки были такими, и оба письма пришлось переписывать руками."""
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = report("letter")
    assert r.code == 0, r.text
    low = r.out.lower()
    assert "просим до" not in low, "с чистой проверки требуют прислать план действий"
    assert "31.08.2026" not in r.out, "в чистом письме стоит срок плана действий"
    assert "d2" in low, "письмо не говорит, что существенных нарушений нет"


def test_письмо_с_нарушениями_требует_план_действий(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "PRD09", "--level", "D2", "--zone", "fridge")
    r = report("letter")
    assert r.code == 0, r.text
    low = r.out.lower()
    assert "просим до" in low and "план действий" in low, (
        "при D2 письмо обязано требовать план действий"
    )
    assert "31.08.2026" in r.out, "срок плана (дата проверки + 10 дней) не подставлен"


def test_английское_письмо_чистой_проверки_без_плана(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    r = report("letter", "--lang", "en")
    assert r.code == 0, r.text
    low = r.out.lower()
    assert "please send us" not in low, "английское письмо требует план на чистой проверке"
    assert "31.08.2026" not in r.out, "в чистом письме стоит срок плана действий"


def test_английское_письмо_с_нарушениями_требует_план(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started("add", "--qid", "PRD09", "--level", "D2", "--zone", "fridge")
    r = report("letter", "--lang", "en")
    assert r.code == 0, r.text
    low = r.out.lower()
    assert "please send us your action plan" in low, (
        "при D2 английское письмо обязано требовать план"
    )


def test_шаблоны_письма_разные_а_не_один_с_вставками(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    clean = report("letter").out
    started("add", "--qid", "PRD09", "--level", "D2", "--zone", "fridge")
    dirty = report("letter").out
    assert clean.strip() and dirty.strip()
    assert clean != dirty, "письмо не зависит от того, есть ли существенные нарушения"


# --- T017: партнёр в шапке ---------------------------------------------------


def test_партнёр_попадает_в_шапку_отчёта(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    r = audit("init", "--unit", "Тестовая", "--date", "2026-08-21", "--partner", "ООО «Пример»")
    assert r.code == 0, r.text
    html = html_of(report)
    assert "ООО «Пример»" in html, "партнёр из init --partner не выведен"
    assert "Партнёр" in html, "нет подписи строки партнёра"


def test_без_партнёра_строки_в_шапке_нет(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    html = html_of(report)
    assert "Партнёр" not in html, "пустая строка партнёра печатается зря"
