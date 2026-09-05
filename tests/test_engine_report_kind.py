"""T180: вид проверки печатается в шапке самого отчёта, а не только в письме.

Решение D084. До этой правки вид проверки существовал в документах ровно в
одном месте — в тексте письма. Подпись для него (`t["type"]`) лежала в словаре
отчёта с самого начала, но печатать её было нечем: единственный потребитель в
`build_html` умер вместе с таблицей `TYPE_EN` (T177), и строка шапки пустовала.
Партнёр подшивает отчёт, а не письмо, и из подшитого документа нельзя было
узнать, плановая это была проверка или повторная.

Здесь проверяется, что вид стоит в шапке отчёта и что он там **по языку
отчёта**. Язык отчёта — отдельное поле, не язык интерфейса и не язык, на
котором проверку однажды завели: один и тот же осмотр печатается партнёру
по-русски и по-английски, и вид проверки обязан назваться на языке документа.
Тот же дефект уже ловился на письме (`test_engine_kind_code.py`); отчёт — второй
документ, и повторить в нём ошибку было бы так же дёшево.

Данные синтетические: методика подставляется оснасткой (`CHECKLIST_DIR` →
`tests/methodology`), проверка заводится здесь же.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Run

#: Извлечение текста из PDF нужно только тестам, поэтому poppler не в
#: зависимостях продукта. Без него остаются проверки по HTML — тому самому, из
#: которого собирается PDF.
requires_pdftotext = pytest.mark.skipif(
    shutil.which("pdftotext") is None, reason="нет pdftotext (poppler) — текст из PDF не достать"
)

#: Подпись строки вида проверки в шапке отчёта, по языку документа. Написана
#: буквально, а не взята из словаря движка: сверка словаря с самим собой не
#: покраснеет, даже если строка исчезнет из шапки целиком.
ПОДПИСЬ = {"ru": "Вид проверки", "en": "Inspection type"}


def html_of(report: Callable[..., Run], *args: str) -> str:
    r = report("html", *args)
    assert r.code == 0, r.text
    return r.out


def вид_в_шапке(html: str, lang: str) -> str:
    """Слово из строки шапки «вид проверки». Нет строки — это провал теста."""
    подпись = ПОДПИСЬ[lang]
    m = re.search(rf'<td class="k">{re.escape(подпись)}</td><td>([^<]*)</td>', html)
    assert m is not None, f"в шапке отчёта нет строки «{подпись}»:\n{html[:1500]}"
    return m.group(1)


def текст_pdf(path: Path) -> str:
    out = subprocess.run(  # noqa: S603 — аргументы собраны здесь, оболочки нет
        ["pdftotext", str(path), "-"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def test_вид_проверки_стоит_в_шапке_отчёта(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Главное: место в шаблоне больше не пустует."""
    assert audit("init", "--unit", "Тестовая", "--kind", "repeat", "--lang", "ru").code == 0
    assert вид_в_шапке(html_of(report), "ru") == "Повторная"


def test_отчёт_называет_вид_по_языку_отчёта_а_не_заведения(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Проверка заведена по-русски, отчёт собирается по-английски."""
    assert audit("init", "--unit", "Тестовая", "--kind", "repeat", "--lang", "ru").code == 0
    en = html_of(report, "--lang", "en")
    assert вид_в_шапке(en, "en") == "Repeat"
    assert "Повторная" not in en, "в английский отчёт попало русское слово"


def test_отчёт_на_русском_по_английской_проверке(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Обратное направление: заведена по-английски, отчёт запрошен по-русски."""
    assert audit("init", "--unit", "Store", "--kind", "unscheduled", "--lang", "en").code == 0
    assert вид_в_шапке(html_of(report), "en") == "Unscheduled"
    ru = html_of(report, "--lang", "ru")
    assert вид_в_шапке(ru, "ru") == "Внеплановая"
    assert "Unscheduled" not in ru, "в русский отчёт попало английское слово"


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_отчёт_и_письмо_называют_вид_одним_словом(
    audit: Callable[..., Run], report: Callable[..., Run], lang: str
) -> None:
    """Код превращается в слово в ОДНОМ месте, иначе два документа разойдутся.

    Отчёт и письмо собираются разными функциями, и соблазн подставить слово
    в шапку отдельным обращением к таблице видов стоит ровно одной строки.
    Разошлись бы они молча и не сразу — на переименовании или на третьем языке.
    """
    assert audit("init", "--unit", "Тестовая", "--kind", "planned", "--lang", "ru").code == 0
    слово = вид_в_шапке(html_of(report, "--lang", lang), lang)
    письмо = report("letter", "--lang", lang)
    assert письмо.code == 0, письмо.text
    assert слово, "в шапке отчёта пусто"
    assert слово in письмо.out, f"отчёт называет вид «{слово}», а письмо — иначе:\n{письмо.out}"


def test_старая_проверка_печатает_вид_на_своём_языке(
    audit: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Проверка, заведённая словом до T177: на своём языке слово печатается как есть."""
    assert audit("init", "--unit", "Тестовая", "--type", "Плановая", "--lang", "ru").code == 0
    assert вид_в_шапке(html_of(report), "ru") == "Плановая"


def test_старая_проверка_не_собирает_отчёт_на_чужом_языке(
    audit: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Отказ, а не чужое слово в шапке и не молчаливо пустая строка.

    Письмо в этом случае отказывалось и раньше. Отчёт обязан вести себя так же:
    подставить в английский документ слово, записанное по-русски, значит
    повторить сам дефект, а напечатать шапку без вида — потерять поле молча,
    ради которого задача и заводилась.
    """
    assert audit("init", "--unit", "Тестовая", "--type", "Плановая", "--lang", "ru").code == 0
    en = report("html", "--lang", "en")
    assert en.code != 0, f"английский отчёт собрался по виду, записанному словом:\n{en.out[:800]}"
    assert "Плановая" in en.text, f"в отказе нет записанного слова: {en.text!r}"
    assert "--kind" in en.text, f"в отказе нет способа починить: {en.text!r}"

    pdf = report("pdf", "--out", "отчёт.pdf", "--lang", "en")
    assert pdf.code != 0, "PDF собрался, хотя вид проверки на этом языке назвать нечем"
    assert not (workdir / "отчёт.pdf").exists(), "файла нет, но он объявлен собранным"


@requires_pdftotext
@pytest.mark.parametrize(("lang", "слово"), [("ru", "Повторная"), ("en", "Repeat")])
def test_вид_проверки_виден_в_собранном_pdf(
    audit: Callable[..., Run], report: Callable[..., Run], workdir: Path, lang: str, слово: str
) -> None:
    """Не «по коду должно печататься», а текст из собранного файла.

    Отчёт партнёру уходит PDF-ом, и между HTML и PDF стоит рендерер: строку
    можно потерять на вёрстке, ничего при этом не сломав.
    """
    assert audit("init", "--unit", "Тестовая", "--kind", "repeat", "--lang", "ru").code == 0
    r = report("pdf", "--out", "отчёт.pdf", "--lang", lang)
    assert r.code == 0, r.text
    текст = текст_pdf(workdir / "отчёт.pdf")
    assert ПОДПИСЬ[lang] in текст, f"в собранном PDF нет подписи вида проверки:\n{текст[:800]}"
    assert слово in текст, f"в собранном PDF нет самого вида проверки:\n{текст[:800]}"
