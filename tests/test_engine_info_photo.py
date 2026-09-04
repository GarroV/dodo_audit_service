"""T172: к полю информационной части прикладывается кадр.

Владелец просил, чтобы информационную часть можно было заполнить текстом или
голосом **и приложить фото**. Бот кадр принимал и честно говорил аудитору, что
в отчёт попадёт только текст: движок хранил поле строкой, и места под снимок в
этой структуре не было.

Здесь проверяется вторая половина требования — хранение и печать кадра рядом с
полем. Поле теперь хранится не строкой, а парой «текст + кадры»; строка старой
проверки читается по-прежнему, потому что состояние это обычный JSON на диске.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from conftest import Run


def кадр(path: Path) -> Path:
    """Настоящий JPEG: движок открывает кадр через Pillow и сжимает его."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 60), (60, 140, 200)).save(path, "JPEG")
    return path


def info_of(workdir: Path) -> dict:
    return json.loads((workdir / "inspection.json").read_text(encoding="utf-8"))["info"]


def test_кадр_ложится_рядом_с_текстом_поля(started: Callable[..., Run], workdir: Path) -> None:
    r = started("info", "--qid", "INF06", "--text", "поднять скорость выдачи", "--photo", "a.jpg")
    assert r.code == 0, r.text
    поле = info_of(workdir)["INF06"]
    assert поле["text"] == "поднять скорость выдачи", поле
    assert поле["photos"] == ["a.jpg"], поле


def test_несколько_кадров_и_через_запятую(started: Callable[..., Run], workdir: Path) -> None:
    r = started(
        "info",
        "--qid",
        "INF06",
        "--text",
        "рост",
        "--photo",
        "a.jpg,b.jpg",
        "--photo",
        "c.jpg",
    )
    assert r.code == 0, r.text
    assert info_of(workdir)["INF06"]["photos"] == ["a.jpg", "b.jpg", "c.jpg"]


def test_поле_без_кадра_остаётся_полем_без_кадра(
    started: Callable[..., Run], workdir: Path
) -> None:
    assert started("info", "--qid", "INF06", "--text", "только словами").code == 0
    assert info_of(workdir)["INF06"] == {"text": "только словами", "photos": []}


def test_правка_текста_не_теряет_кадр(started: Callable[..., Run], workdir: Path) -> None:
    """Расшифровку голоса правят повторной записью — кадр при этом не при чём."""
    assert started("info", "--qid", "INF06", "--text", "первый", "--photo", "a.jpg").code == 0
    assert started("info", "--qid", "INF06", "--text", "исправленный").code == 0
    поле = info_of(workdir)["INF06"]
    assert поле["text"] == "исправленный", поле
    assert поле["photos"] == ["a.jpg"], "правка текста уронила приложенный кадр"


def test_новые_кадры_заменяют_прежние(started: Callable[..., Run], workdir: Path) -> None:
    assert started("info", "--qid", "INF06", "--text", "т", "--photo", "a.jpg").code == 0
    assert started("info", "--qid", "INF06", "--text", "т", "--photo", "b.jpg").code == 0
    assert info_of(workdir)["INF06"]["photos"] == ["b.jpg"]


def test_ошибочный_кадр_снимается(started: Callable[..., Run], workdir: Path) -> None:
    """Иначе кадр, приложенный по ошибке, уедет партнёру и снять его будет нечем."""
    assert started("info", "--qid", "INF06", "--text", "т", "--photo", "a.jpg").code == 0
    r = started("info", "--qid", "INF06", "--text", "т", "--clear-photos")
    assert r.code == 0, r.text
    assert info_of(workdir)["INF06"]["photos"] == []


def test_приложить_и_снять_разом_отказ(started: Callable[..., Run], workdir: Path) -> None:
    r = started("info", "--qid", "INF06", "--text", "т", "--photo", "a.jpg", "--clear-photos")
    assert r.code != 0, "«приложить» и «снять» вместе не должны молча выбирать одно"
    assert "INF06" not in info_of(workdir), "поле записано при отказе"


def test_поле_старой_проверки_строкой_читается(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Проверки, заведённые до T172, хранят поле строкой — это не поломка."""
    path = workdir / "inspection.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["info"] = {"INF06": "старое поле строкой"}
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    r = report("html")
    assert r.code == 0, r.text
    assert "старое поле строкой" in r.out, "поле старой проверки пропало из отчёта"
    r = started("list")
    assert r.code == 0, r.text
    assert "старое поле строкой" in r.out, r.out


def test_list_называет_число_кадров(started: Callable[..., Run]) -> None:
    assert started("info", "--qid", "INF06", "--text", "т", "--photo", "a.jpg,b.jpg").code == 0
    r = started("list")
    assert r.code == 0, r.text
    строка = [x for x in r.out.splitlines() if "INF06" in x]
    assert строка and "×2" in строка[0], f"в списке не видно кадров поля: {строка}"


def test_кадр_поля_печатается_в_отчёте(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    кадр(workdir / "поле.jpg")
    поле = started("info", "--qid", "INF06", "--text", "зоны роста", "--photo", "поле.jpg")
    assert поле.code == 0, поле.text
    r = report("html")
    assert r.code == 0, r.text
    assert "зоны роста" in r.out, "текст поля пропал"
    assert "data:image" in r.out, "кадр поля в отчёт не попал"


def test_кадр_поля_печатается_и_в_режиме_без_фотографий(
    started: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Приложение печатает кадры независимо от `--photos` — как у записей D0."""
    кадр(workdir / "поле.jpg")
    поле = started("info", "--qid", "INF06", "--text", "зоны роста", "--photo", "поле.jpg")
    assert поле.code == 0, поле.text
    r = report("html", "--photos", "none")
    assert r.code == 0, r.text
    assert "data:image" in r.out, "кадр поля потерян в режиме --photos none"


def test_поле_без_кадра_не_помечено_как_без_фотофиксации(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Пометка D074 отвечает на вопрос «где фото», а поле — это текстовый ответ."""
    assert started("info", "--qid", "INF06", "--text", "зоны роста").code == 0
    r = report("html")
    assert r.code == 0, r.text
    assert "Без фотофиксации" not in r.out, "текстовое поле помечено как запись без кадра"


def test_потерянный_кадр_поля_не_пропадает_молча(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    assert started("info", "--qid", "INF06", "--text", "т", "--photo", "нет-такого.jpg").code == 0
    r = report("html")
    assert r.code == 0, r.text
    assert "Фотография не приложена" in r.out, "промах кадра поля не отмечен в отчёте"
    assert "нет-такого.jpg" in r.err, f"промах кадра поля не назван в stderr: {r.err!r}"


def test_срок_плана_действий_читается_из_поля_с_кадром(
    started: Callable[..., Run], report: Callable[..., Run]
) -> None:
    """Письмо берёт срок плана из информационного поля — форма поля его не ломает."""
    assert started("info", "--qid", "INF07", "--text", "2026-09-15", "--photo", "a.jpg").code == 0
    started("add", "--qid", "PRD09", "--level", "D2", "--zone", "fridge")
    r = report("letter")
    assert r.code == 0, r.text
    assert "15.09.2026" in r.out, f"срок плана из поля не подставлен: {r.out!r}"


def test_score_json_отдаёт_поле_с_кадрами(started: Callable[..., Run]) -> None:
    assert started("info", "--qid", "INF06", "--text", "т", "--photo", "a.jpg").code == 0
    r = started("score", "--json")
    assert r.code == 0, r.text
    поле = json.loads(r.out)["info"]["INF06"]
    assert поле["text"] == "т" and поле["photos"] == ["a.jpg"], поле
