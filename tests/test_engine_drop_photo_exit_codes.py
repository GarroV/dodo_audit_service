"""Отказ обязан быть отказом: команда не возвращает успех, не сделав работу.

Найдено на приёмке блока engine-fix: `drop` несуществующей записи завершался
кодом 0. Бот по коду возврата сказал бы аудитору «удалено», а запись осталась бы
в отчёте.

Существование файла фотографии здесь намеренно не проверяется: в боте кадр
хранится идентификатором телеграма, а не путём на диске. Пропавший файл ловится
на сборке отчёта — задача T043 блока report.
"""

import json
import subprocess
import sys
from pathlib import Path

from conftest import TEST_DATA

ENGINE = Path(__file__).resolve().parents[1] / "engine" / "audit.py"


def _run(state: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Методика задаётся явно (T141). Без `CHECKLIST_DIR` движок берёт её из своей
    # копии рядом со скриптом — то есть из боевого `data/`, которого на чужой
    # машине нет, а у нас он меняется чужими руками.
    return subprocess.run(  # noqa: S603 — аргументы собираем сами, ввода извне тут нет
        [sys.executable, str(ENGINE), *args],
        capture_output=True,
        text=True,
        env={
            "INSPECTION_FILE": str(state),
            "CHECKLIST_DIR": str(TEST_DATA),
            "PATH": "/usr/bin:/bin",
        },
    )


def _started(tmp_path: Path) -> Path:
    state = tmp_path / "inspection.json"
    _run(
        state,
        "init",
        "--unit",
        "Точка",
        "--city",
        "Город",
        "--auditor",
        "Аудитор",
        "--type",
        "Плановая",
        "--date",
        "2026-08-28",
        "--lang",
        "ru",
    )
    _run(
        state,
        "add",
        "--qid",
        "CLN06",
        "--level",
        "D1",
        "--zone",
        "hot_kitchen",
        "--evidence",
        "нагар",
    )
    return state


def test_drop_несуществующей_записи_отказывает(tmp_path: Path) -> None:
    state = _started(tmp_path)

    result = _run(state, "drop", "99")

    assert result.returncode != 0, "удалять нечего — это отказ, а не успех"
    assert "99" in (result.stderr + result.stdout)


def test_drop_существующей_записи_успешен(tmp_path: Path) -> None:
    state = _started(tmp_path)

    result = _run(state, "drop", "1")

    assert result.returncode == 0
    assert json.loads(state.read_text())["findings"] == []
