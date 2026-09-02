"""Демо-набор: английский целиком, изолированный, идемпотентный (T074, T100).

Три вещи, которые этот файл сторожит, и все три уже ломались.

1. **Набор данных вообще есть в git.** Правило `data/` в `.gitignore` не было
   привязано к корню и совпадало с любым каталогом `data` на любой глубине,
   включая `demo/data/`. Синтетический чек-лист демо не попал в индекс ни
   одним коммитом и потерялся вместе с рабочей копией — приёмка этого не
   увидела, потому что у автора файлы лежали на диске. Проверка `git
   ls-files` ловит ровно это: файл на диске есть, а в репозитории нет.

2. **Английский — весь, а не только отчёт.** Демо обязано быть англоязычным
   целиком (конституция, раздел «Демо-режим»). Кириллица ищется во всех
   артефактах демо: методика, состояние, имя файла отчёта, письмо партнёру.

3. **Демо не пишет в боевое состояние.** Сид зовут и на сервере, где `.env`
   указывает `STATE_DIR` на настоящие проверки. Каталог демо задаётся
   отдельной переменной `DEMO_STATE_DIR`, и `STATE_DIR` сид не читает вовсе.

Сид запускается подпроцессом, а не импортом: проверяется настоящая точка
входа (`python tools/seed_demo.py`) вместе с её работой с окружением, и
переменные демо физически не могут утечь в процесс теста.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DATA = ROOT / "demo" / "data"
SEED = ROOT / "tools" / "seed_demo.py"

#: Файлы методики, без которых `domain.check_environment` не пускает дальше.
REQUIRED_DEMO_FILES = ("checklist.csv", "zones.csv", "scoring.json", "criteria.md")

CYRILLIC = re.compile(r"[Ѐ-ӿ]")

#: Чат демо-проверки — тот же, что в `tools/seed_demo.py`.
DEMO_CHAT_DIR = "chat_999000000001"

#: Якорь демо-оценки: четыре находки (3×D1 + 1×D2) на ставках демо-методики
#: (D1 = 1.0, D2 = 5.0) дают 100 − 3 − 5 = 92 %, а это по её же правилам
#: класс B. Число здесь не для красоты: если демо-набор поедет, показывать
#: заказчику будут не то, что задумано, и заметить это иначе нечем.
DEMO_PCT = "92"
DEMO_GRADE = "B"


def cyrillic_in(text: str) -> list[str]:
    """Строки с кириллицей — чтобы отказ называл место, а не факт."""
    return [line for line in text.splitlines() if CYRILLIC.search(line)]


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        ["git", *args],  # noqa: S607 — git берётся из PATH намеренно, как во всём проекте
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


# --- сам набор данных --------------------------------------------------------


def test_demo_dataset_is_complete() -> None:
    missing = [name for name in REQUIRED_DEMO_FILES if not (DEMO_DATA / name).is_file()]
    assert not missing, f"в {DEMO_DATA} не хватает файлов методики демо: {missing}"


def test_demo_dataset_is_tracked_by_git() -> None:
    tracked = set(git("ls-files", "demo/data").stdout.split())
    expected = {f"demo/data/{name}" for name in REQUIRED_DEMO_FILES}
    assert expected <= tracked, (
        f"файлы демо есть на диске, но не в репозитории: {sorted(expected - tracked)}. "
        f"Так уже терялся весь демо-набор — правило .gitignore съедало demo/data/"
    )


def test_demo_state_dir_is_ignored_by_git() -> None:
    r = git("check-ignore", "demo/state/anything.pdf")
    assert r.returncode == 0, (
        "demo/state не в .gitignore — собранный демо-отчёт уедет в публичный репозиторий"
    )


def test_demo_dataset_has_no_cyrillic() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(DEMO_DATA.rglob("*")):
        if not path.is_file():
            continue
        lines = cyrillic_in(path.read_text(encoding="utf-8"))
        if lines:
            offenders[path.name] = lines[:3]
    assert not offenders, f"демо обязано быть английским целиком, а кириллица есть: {offenders}"


def test_demo_zone_shares_sum_to_100() -> None:
    with (DEMO_DATA / "zones.csv").open(encoding="utf-8-sig") as fh:
        shares = [float(row["share_pct"]) for row in csv.DictReader(fh)]
    assert shares, "в demo/data/zones.csv нет ни одной зоны"
    total = sum(shares)
    assert abs(total - 100.0) < 0.01, (
        f"сумма долей зон {total}, а не 100: движок при таком расхождении молча "
        f"раскидывает доли поровну, и разбивка по зонам в демо будет выдуманной"
    )


# --- прогон сида -------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Демо, посеянное один раз в одноразовые каталоги.

    `STATE_DIR` и `AUDIT_DATA_DIR` намеренно указывают на пустые каталоги,
    изображающие боевой стенд: сид обязан их не тронуть и работать по своим.
    """
    prod_state = tmp_path_factory.mktemp("prod-state")
    prod_data = tmp_path_factory.mktemp("prod-data")
    demo_state = tmp_path_factory.mktemp("demo-state")

    env = dict(os.environ)
    env["STATE_DIR"] = str(prod_state)
    env["AUDIT_DATA_DIR"] = str(prod_data)
    env["DEMO_STATE_DIR"] = str(demo_state)
    run = subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        [sys.executable, str(SEED)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "run": run,
        "prod_state": prod_state,
        "prod_data": prod_data,
        "demo_state": demo_state,
    }


def _run(seeded: dict[str, object]) -> subprocess.CompletedProcess[str]:
    run = seeded["run"]
    assert isinstance(run, subprocess.CompletedProcess)
    return run


def _dir(seeded: dict[str, object], key: str) -> Path:
    value = seeded[key]
    assert isinstance(value, Path)
    return value


def test_seed_exits_clean(seeded: dict[str, object]) -> None:
    run = _run(seeded)
    assert run.returncode == 0, run.stdout + run.stderr


def test_seed_does_not_touch_state_dir(seeded: dict[str, object]) -> None:
    prod_state = _dir(seeded, "prod_state")
    left = sorted(p.name for p in prod_state.iterdir())
    assert not left, (
        f"сид написал в STATE_DIR боевого стенда: {left}. Демо обязано жить в своём "
        f"каталоге (DEMO_STATE_DIR) и не смешиваться с настоящими проверками"
    )


def test_seed_runs_on_the_demo_checklist(seeded: dict[str, object]) -> None:
    """Демо считается по своей методике, а не по боевой из AUDIT_DATA_DIR.

    Проверка не «каталог боевой методики не изменился» — писать туда сид и не
    умеет, такое утверждение было бы пустым (выяснено порчей: тест оставался
    зелёным, когда перекрытие AUDIT_DATA_DIR убирали). Смотрим на результат:
    коды находок обязаны быть из `demo/data/checklist.csv`.
    """
    chat = _dir(seeded, "demo_state") / DEMO_CHAT_DIR
    state = json.loads((chat / "inspection.json").read_text(encoding="utf-8"))
    with (DEMO_DATA / "checklist.csv").open(encoding="utf-8-sig") as fh:
        demo_ids = {row["id"] for row in csv.DictReader(fh)}
    used = {f["qid"] for f in state["findings"]}
    assert used, "в демо-проверке нет ни одной находки"
    assert used <= demo_ids, (
        f"демо посчитано не по своей методике: коды {sorted(used - demo_ids)} "
        f"отсутствуют в demo/data/checklist.csv"
    )


def test_seed_uses_demo_state_dir(seeded: dict[str, object]) -> None:
    chat = _dir(seeded, "demo_state") / DEMO_CHAT_DIR
    assert (chat / "inspection.json").is_file(), f"состояния демо нет в DEMO_STATE_DIR: {chat}"


def test_seed_score_is_the_demo_anchor(seeded: dict[str, object]) -> None:
    out = _run(seeded).stdout
    assert f"{DEMO_PCT}% grade {DEMO_GRADE}" in out, out


def test_seed_report_name_is_english(seeded: dict[str, object]) -> None:
    """Отчёт лежит в `reports/` рядом с состоянием — задача T104.

    Изначально тест искал PDF рядом с `inspection.json`, и это было верно на
    момент его написания: блок infra рос от ветки, где сборка ещё клала отчёт
    туда. Пока блоки шли параллельно, расхождение было невидимо — вылезло оно
    при слиянии, у того, кто его не вносил.
    """
    chat = _dir(seeded, "demo_state") / DEMO_CHAT_DIR
    pdfs = sorted(p.name for p in (chat / "reports").glob("*.pdf"))
    assert len(pdfs) == 1, f"ожидался ровно один отчёт демо, найдено: {pdfs}"
    assert not list(chat.glob("*.pdf")), (
        "отчёт лёг рядом с состоянием, а не в reports/ — T104 отменён незаметно"
    )
    name = pdfs[0]
    assert name.startswith("Audit "), f"имя отчёта демо не английское: {name}"
    assert not CYRILLIC.search(name), f"в имени отчёта демо кириллица: {name}"


def test_seed_letter_is_english(seeded: dict[str, object]) -> None:
    chat = _dir(seeded, "demo_state") / DEMO_CHAT_DIR
    letter = chat / "letter.txt"
    assert letter.is_file(), (
        "сид не собрал письмо партнёру — проверить его язык нечем, а письмо тоже часть демо"
    )
    text = letter.read_text(encoding="utf-8")
    assert text.strip(), "письмо демо пустое"
    assert not cyrillic_in(text), f"в письме демо кириллица: {cyrillic_in(text)[:3]}"


def test_seed_state_is_english(seeded: dict[str, object]) -> None:
    chat = _dir(seeded, "demo_state") / DEMO_CHAT_DIR
    raw = (chat / "inspection.json").read_text(encoding="utf-8")
    state = json.loads(raw)
    assert state["meta"]["lang"] == "en"
    block = state["domain"]
    assert block["ui_lang"] == "en", (
        "язык интерфейса демо-проверки не английский: бот на демо-пути заговорит по-русски"
    )
    assert block["speech_lang"] == "en"
    assert not cyrillic_in(raw), f"в состоянии демо кириллица: {cyrillic_in(raw)[:3]}"


def test_seed_report_content_is_english(seeded: dict[str, object]) -> None:
    """Содержимое отчёта, а не только имя файла: HTML — тот же материал, что в PDF."""
    chat = _dir(seeded, "demo_state") / DEMO_CHAT_DIR
    env = dict(os.environ)
    env["INSPECTION_FILE"] = str(chat / "inspection.json")
    env["AUDIT_DATA_DIR"] = str(DEMO_DATA)
    run = subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        [sys.executable, str(ROOT / "engine" / "report.py"), "html"],
        cwd=str(chat),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert not cyrillic_in(run.stdout), f"в отчёте демо кириллица: {cyrillic_in(run.stdout)[:5]}"


def test_seed_is_idempotent(seeded: dict[str, object]) -> None:
    """Повторный запуск возвращает демо к чистому виду, а не удваивает находки."""
    demo_state = _dir(seeded, "demo_state")
    chat = demo_state / DEMO_CHAT_DIR
    before = json.loads((chat / "inspection.json").read_text(encoding="utf-8"))
    (chat / "leftover.txt").write_text("мусор от прошлого показа", encoding="utf-8")

    env = dict(os.environ)
    env["STATE_DIR"] = str(_dir(seeded, "prod_state"))
    env["AUDIT_DATA_DIR"] = str(_dir(seeded, "prod_data"))
    env["DEMO_STATE_DIR"] = str(demo_state)
    run = subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне нет
        [sys.executable, str(SEED)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr

    after = json.loads((chat / "inspection.json").read_text(encoding="utf-8"))
    assert len(after["findings"]) == len(before["findings"])
    assert not (chat / "leftover.txt").exists(), (
        "повторный сид оставил чужой файл: демо должно возвращаться к чистому виду, "
        "а не накапливать мусор от прошлых показов"
    )
