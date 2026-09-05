"""Вызов `engine/report.py` подпроцессом — единственный способ собрать отчёт.

Движок не импортируется: расчёт оценки и разметка отчёта не имеют права
продублироваться в коде продукта (контракт `engine-not-imported` в
`lint-imports`). Всё, что знает блок о движке, — как его позвать и как понять,
что он сделал работу.

Пути берутся у блока `domain`: он один разбирает окружение и раскладывает
проверки по папкам на чат. Второй копии этих правил здесь нет намеренно —
разошлись бы, и отчёт собрался бы по чужому состоянию.

Ненулевой код возврата вердиктом сборщика считается НЕ всегда (T191). Под
нагрузкой машины интерпретатор подпроцесса умирает ДО первой строки
`report.py`: системный вызов внутри вычисления его собственных путей
прерывается сигналом. Выданная за отказ сборки, эта беда приходит аудитору
дампом чужого процесса вместо ответа. Такой запуск повторяется, и только такой.

**Повтор здесь безопаснее, чем в домене, и это довод, а не совпадение.** Сборка
отчёта только читает состояние проверки: движок открывает `inspection.json`,
считает оценку и пишет PDF отдельным файлом. Повторный заход дублировать
нечего. В домене тот же довод неверен — через `run_audit` идут и записи
находок, и запуск, успевший поработать, дописал бы вторую такую же запись.
Различение всё равно то же самое: повторяется только не состоявшийся старт, о
котором сказал сам интерпретатор.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from src.domain.config import Settings, assert_no_checklist_fork, check_environment
from src.domain.engine import (
    ENGINE_ATTEMPTS,
    RUNTIME_BEFORE_SCRIPT,
    RUNTIME_FATAL,
    chat_dir,
    state_file,
)
from src.domain.errors import InspectionNotStarted

from .errors import ReportError

logger = logging.getLogger(__name__)

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
    бот на него предлагает начать проверку. Не состоявшийся старт
    интерпретатора — не отказ вовсе: он повторяется (T191).
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

    for attempt in range(1, ENGINE_ATTEMPTS + 1):
        done = _launch(args, work=work, env=env, settings=settings)
        if done.returncode == 0:
            return done.stdout
        output = f"{done.stderr}\n{done.stdout}"
        if RUNTIME_FATAL in output:
            _refuse_no_verdict(args, output=output, attempt=attempt, chat_id=chat_id)
            continue
        message = (done.stderr or done.stdout).strip() or "движок отказал без объяснения"
        raise ReportError(message)
    raise AssertionError("недостижимо: цикл попыток выходит возвратом или отказом")


def _launch(
    args: Sequence[str], *, work: Path, env: dict[str, str], settings: Settings
) -> subprocess.CompletedProcess[str]:
    """Один запуск `report.py`. Не уложился в срок — отказ, и повтору не подлежит."""
    command = [sys.executable, str(report_script(settings)), *args]
    try:
        return subprocess.run(  # noqa: S603 — список аргументов собран здесь, оболочки нет
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


def _refuse_no_verdict(args: Sequence[str], *, output: str, attempt: int, chat_id: int) -> None:
    """Интерпретатор сборщика умер фатально. Вернуться — значит повторить запуск.

    Повторяется только не состоявшийся старт: сборщик не работал, отчёта нет и
    быть не могло. Умерший позже не повторяется — не ради состояния (сборка его
    не меняет), а потому что различение одно на все три вызывающих движка: три
    разных правила на одну и ту же беду разъедутся при первой же правке одного.

    Отказ — СВОИМИ словами: текст интерпретатора, выданный за слова сборщика,
    приходит аудитору дампом чужого процесса вместо ответа (T191).
    """
    command = " ".join(args)
    again = RUNTIME_BEFORE_SCRIPT in output and attempt < ENGINE_ATTEMPTS
    started = "старт не состоялся" if RUNTIME_BEFORE_SCRIPT in output else "умер после старта"
    logger.warning(
        "интерпретатор сборщика отчёта умер в чате %s (%s, попытка %s, %s): %s",
        chat_id,
        started,
        attempt,
        "повторяем" if again else "больше не повторяем",
        command,
    )
    if again:
        return
    times = "" if attempt == 1 else f" {attempt} раза подряд"
    raise ReportError(
        f"Сборщик отчёта не дал ответа: его интерпретатор умер до ответа{times}. "
        f"Это беда на стороне машины, а не отказ по команде «{command}»"
    )


def settings() -> Settings:
    """Разобранное и проверенное окружение. Отказ — `ConfigError` блока `domain`."""
    return check_environment()
