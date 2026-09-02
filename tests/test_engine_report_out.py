"""T104: сборка отчёта не пишет рядом с состоянием и не затирает эталон.

`report.py pdf` без `--out` брал имя из шапки проверки и клал файл в рабочий
каталог — то есть ровно туда, где лежит `inspection.json` и, в `examples/`,
эталонный отчёт с тем же именем. Ручной смоук сборки 02.09.2026 затёр эталон
Белград-1, и восстановить его неоткуда: `examples/` вне git (решение D002).

Проверяется наблюдаемый результат: файл с именем отчёта, лежавший рядом с
состоянием, после сборки байт в байт тот же, а собранный отчёт лежит в
отдельном каталоге вывода.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Run, requires_data

pytestmark = requires_data

HAS_RENDERER = importlib.util.find_spec("weasyprint") is not None
requires_renderer = pytest.mark.skipif(not HAS_RENDERER, reason="WeasyPrint не установлен")

#: Имя, которое движок даёт отчёту проверки из фикстуры `started`.
ИМЯ_ОТЧЁТА = "Аудит Тестовая - Тест - 21.08.2026.pdf"
#: Правдоподобный «эталон»: заголовок настоящего PDF и узнаваемая начинка.
ЭТАЛОН = "%PDF-1.4 эталон, восстановить неоткуда".encode() + b"\0" * 3000


@pytest.fixture
def проверка(started: Callable[..., Run]) -> Callable[..., Run]:
    """Начатая проверка с одной записью — из неё уже собирается отчёт."""
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    return started


@requires_renderer
def test_сборка_не_затирает_файл_рядом_с_состоянием(
    проверка: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Тот самый случай: эталон лежит рядом с проверкой и называется так же."""
    эталон = workdir / ИМЯ_ОТЧЁТА
    эталон.write_bytes(ЭТАЛОН)

    r = report("pdf")

    assert r.code == 0, r.text
    assert эталон.read_bytes() == ЭТАЛОН, "сборка затёрла файл, лежавший рядом с состоянием"


@requires_renderer
def test_отчёт_собирается_в_отдельный_каталог(
    проверка: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """Путь напечатан, файл по нему есть, и это не соседство с состоянием."""
    r = report("pdf")

    assert r.code == 0, r.text
    путь = Path(r.out.strip())
    assert путь.parent != workdir, f"отчёт снова лёг рядом с состоянием: {путь}"
    assert путь.is_file(), f"напечатан путь к несуществующему файлу: {путь}"
    assert путь.read_bytes()[:5] == b"%PDF-", "собрано что-то, но это не PDF"
    assert путь.name == ИМЯ_ОТЧЁТА, f"имя отчёта разошлось с прежними: {путь.name!r}"


@requires_renderer
def test_out_dir_кладёт_отчёт_куда_сказано(
    проверка: Callable[..., Run], report: Callable[..., Run], tmp_path: Path
) -> None:
    цель = tmp_path / "выгрузка"
    цель.mkdir()

    r = report("pdf", "--out-dir", str(цель))

    assert r.code == 0, r.text
    assert (цель / ИМЯ_ОТЧЁТА).is_file(), f"в {цель} отчёта нет, напечатано: {r.out!r}"
    assert Path(r.out.strip()) == цель / ИМЯ_ОТЧЁТА


@requires_renderer
def test_out_dir_создаёт_каталог(
    проверка: Callable[..., Run], report: Callable[..., Run], tmp_path: Path
) -> None:
    """Каталог вывода — это место для отчётов, а не заранее заведённая папка."""
    цель = tmp_path / "нет-такой-папки" / "отчёты"

    r = report("pdf", "--out-dir", str(цель))

    assert r.code == 0, r.text
    assert (цель / ИМЯ_ОТЧЁТА).is_file(), f"каталог вывода не создан: {r.text!r}"


@requires_renderer
def test_out_кладёт_отчёт_ровно_по_указанному_пути(
    проверка: Callable[..., Run], report: Callable[..., Run], workdir: Path
) -> None:
    """`--out` — явное указание человека, и оно сильнее каталога вывода."""
    r = report("pdf", "--out", "отчёт.pdf")

    assert r.code == 0, r.text
    assert (workdir / "отчёт.pdf").is_file(), "явный --out перестал работать"
    assert r.out.strip() == "отчёт.pdf", f"напечатан не тот путь: {r.out!r}"


def test_out_и_out_dir_вместе_отказ(
    проверка: Callable[..., Run], report: Callable[..., Run], tmp_path: Path
) -> None:
    """Два разных указания, куда класть отчёт: молча выбрать одно нельзя."""
    r = report("pdf", "--out", str(tmp_path / "явный.pdf"), "--out-dir", str(tmp_path))

    assert r.code != 0, "движок молча выбрал одно из двух противоречащих указаний"
    assert "--out" in r.text and "--out-dir" in r.text, f"отказ не называет флаги: {r.text!r}"
    assert not (tmp_path / "явный.pdf").exists(), "отказ, а файл всё равно собран"
