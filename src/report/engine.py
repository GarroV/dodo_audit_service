"""Вызов `engine/report.py` подпроцессом — единственный способ собрать отчёт.

Движок не импортируется: расчёт оценки и разметка отчёта не имеют права
продублироваться в коде продукта (контракт `engine-not-imported` в
`lint-imports`). Всё, что знает блок о движке, — как его позвать и как понять,
что он сделал работу.

Пути берутся у блока `domain`: он один разбирает окружение и раскладывает
проверки по папкам на чат. Второй копии этих правил здесь нет намеренно —
разошлись бы, и отчёт собрался бы по чужому состоянию.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from src.domain.config import Settings, assert_no_checklist_fork, check_environment
from src.domain.engine import chat_dir, state_file
from src.domain.errors import InspectionNotStarted

from .errors import ReportError

#: Сборка PDF Белград-1 с 19 фотографиями — 1 секунда (`docs/04-engine.md`).
#: Запас на медленный диск и большой альбом; вечно висеть бот не должен.
TIMEOUT_SEC = 180.0

#: Скрипт отчёта лежит рядом с движком оценки. Путь выводится из того, что уже
#: разобрал `domain`, чтобы обе копии кода не расходились в понимании, где
#: движок: подменили каталог движка — подменились оба скрипта разом.
REPORT_SCRIPT_NAME = "report.py"


def report_script(settings: Settings) -> Path:
    return settings.audit_script.with_name(REPORT_SCRIPT_NAME)


def run_report(
    args: Sequence[str],
    *,
    chat_id: int,
    settings: Settings,
    env_extra: dict[str, str] | None = None,
) -> str:
    """Выполнить команду `report.py` в папке чата и вернуть её stdout.

    Ненулевой код — `ReportError` с текстом движка: он объясняет провал сборки
    точнее, чем это сделал бы пересказ. Отсутствие проверки — отдельный отказ,
    бот на него предлагает начать проверку.
    """
    state = state_file(chat_id, settings)
    if not state.is_file():
        raise InspectionNotStarted(
            f"В этом чате проверка не начата — нет {state}. Собирать отчёт не из чего"
        )
    script = report_script(settings)
    if not script.is_file():
        raise ReportError(f"Сборщик отчёта не найден: {script}")
    work = chat_dir(chat_id, settings)
    # Рабочий каталог движка — папка чата: относительно неё он ищет форк
    # чек-листа и резолвит относительные пути кадров.
    assert_no_checklist_fork(work)

    env = dict(os.environ)
    env["INSPECTION_FILE"] = str(state)
    env["CHECKLIST_DIR"] = str(settings.data_dir)
    env.update(env_extra or {})
    command = [sys.executable, str(script), *args]
    try:
        done = subprocess.run(  # noqa: S603 — список аргументов собран здесь, оболочки нет
            command,
            cwd=str(work),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReportError(
            f"Сборка отчёта не закончилась за {TIMEOUT_SEC:g} с. Команда: {' '.join(args)}"
        ) from exc
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip() or "движок отказал без объяснения"
        raise ReportError(message)
    return done.stdout


def settings() -> Settings:
    """Разобранное и проверенное окружение. Отказ — `ConfigError` блока `domain`."""
    return check_environment()
