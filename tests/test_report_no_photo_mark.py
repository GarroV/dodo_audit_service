"""T163: запись без кадра помечена в отчёте партнёру (решение D074).

Владелец, дословно: «партнёр видит, на чём запись держится, и не ищет фото,
которого нет». До этого запись без кадра выглядела в отчёте ровно так же, как
запись, у которой кадр есть, но не попал в этот режим сборки, — пустое место
под текстом читалось как потерянная фотография.

Пометку нельзя путать с отметкой промаха: «Фотография не приложена» означает,
что кадр к записи привязан, а файл по ссылке не нашёлся, — это дефект сборки, и
он остаётся красным. «Без фотофиксации» означает, что кадра и не было.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from conftest import Run

БЕЗ_ФОТО = "Без фотофиксации"
БЕЗ_ФОТО_EN = "No photo taken"
ПРОМАХ = "Фотография не приложена"


def кадр(path: Path) -> Path:
    """Настоящий JPEG: движок открывает кадр через Pillow и сжимает его."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 60), (200, 120, 60)).save(path, "JPEG")
    return path


def test_нарушение_без_кадра_помечено(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    started(
        "add",
        "--qid",
        "CLN05",
        "--level",
        "D1",
        "--zone",
        "hot_kitchen",
        "--evidence",
        "нагар на поде",
    )
    r = report("html")
    assert r.code == 0, r.text
    assert БЕЗ_ФОТО in r.out, "запись сделана без кадра, а партнёру об этом не сказано"


def test_нарушение_с_кадром_не_помечено(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    started(
        "add",
        "--qid",
        "CLN05",
        "--level",
        "D1",
        "--zone",
        "hot_kitchen",
        "--evidence",
        "нагар на поде",
    )
    started("photo", "1", "--add", str(кадр(workdir / "p01.jpg")))
    r = report("html")
    assert r.code == 0, r.text
    assert БЕЗ_ФОТО not in r.out, "у записи есть кадр, а она объявлена сделанной без фотофиксации"


def test_пометка_не_подменяет_отметку_промаха(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Привязанный, но потерянный кадр — дефект сборки, а не запись без фотофиксации."""
    started(
        "add",
        "--qid",
        "CLN05",
        "--level",
        "D1",
        "--zone",
        "hot_kitchen",
        "--evidence",
        "нагар на поде",
    )
    started("photo", "1", "--add", "нет-такого-файла.jpg")
    r = report("html")
    assert r.code == 0, r.text
    assert ПРОМАХ in r.out, "потерянный кадр перестал быть заметен"
    assert БЕЗ_ФОТО not in r.out, "потерянный кадр выдан за запись, сделанную без фотографии"


def test_информационная_запись_без_кадра_помечена(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Записи приложения держатся на том же — партнёр читает их так же."""
    started(
        "add",
        "--qid",
        "INF10",
        "--level",
        "D0",
        "--zone",
        "hot_kitchen",
        "--evidence",
        "замер без снимка",
    )
    r = report("html")
    assert r.code == 0, r.text
    assert БЕЗ_ФОТО in r.out, "информационная запись без кадра не помечена"


def test_пометка_переведена(started: Callable[..., Run], report: Callable[..., Run]) -> None:
    started(
        "add",
        "--qid",
        "CLN05",
        "--level",
        "D1",
        "--zone",
        "hot_kitchen",
        "--evidence",
        "нагар на поде",
    )
    r = report("html", "--lang", "en")
    assert r.code == 0, r.text
    assert БЕЗ_ФОТО_EN in r.out, "в английском отчёте пометка осталась русской или пропала"
    assert БЕЗ_ФОТО not in r.out


def test_без_фотоприложения_пометки_нет(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """`--photos none` не показывает кадров вовсе — обещать их отсутствие не о чем."""
    started(
        "add",
        "--qid",
        "CLN05",
        "--level",
        "D1",
        "--zone",
        "hot_kitchen",
        "--evidence",
        "нагар на поде",
    )
    r = report("html", "--photos", "none")
    assert r.code == 0, r.text
    assert БЕЗ_ФОТО not in r.out, "кадры не печатаются вовсе, а пометка про них осталась"
