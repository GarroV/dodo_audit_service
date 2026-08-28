"""Вызов движка подпроцессом — единственный способ, которым блок его трогает.

Движок не импортируется никогда: расчёт оценки должен физически не иметь шанса
продублироваться в коде продукта (контракт `engine-not-imported` в
`lint-imports`). Путь к состоянию передаётся переменной `INSPECTION_FILE`,
каталог методики — `CHECKLIST_DIR`: без второй переменной движок взял бы данные
из своей копии рядом со скриптом или из форка `checklist_data/` в рабочей папке.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import Settings, assert_no_checklist_fork
from .errors import EngineError, InspectionNotStarted

STATE_FILE_NAME = "inspection.json"
CHAT_DIR_PREFIX = "chat_"

#: Движок ждёт освобождения состояния до 30 секунд и только потом отказывает.
#: Здесь запас на это ожидание: бот не должен висеть вечно, но и обрывать
#: честное ожидание блокировки нельзя.
TIMEOUT_SEC = 90.0


def chat_dir(chat_id: int, settings: Settings) -> Path:
    """Папка проверки этого чата. Одна проверка — одна папка (решение D007)."""
    return settings.state_dir / f"{CHAT_DIR_PREFIX}{chat_id}"


def state_file(chat_id: int, settings: Settings) -> Path:
    return chat_dir(chat_id, settings) / STATE_FILE_NAME


def option(name: str, value: str) -> str:
    """`--имя=значение` одним словом.

    Через пробел argparse принял бы формулировку аудитора, начинающуюся с
    дефиса, за имя ключа и отказал бы на ровном месте.
    """
    return f"--{name}={value}"


def run_audit(
    args: Sequence[str],
    *,
    chat_id: int,
    settings: Settings,
    create: bool = False,
    require_state: bool = True,
) -> str:
    """Выполнить команду `audit.py` в папке чата и вернуть её вывод.

    Ненулевой код — `EngineError` с текстом самого движка: проверки методики
    живут в нём, блок их не повторяет, но и не глотает. Отсутствие начатой
    проверки — отдельный отказ: бот на него предлагает начать проверку, а не
    показывает аудитору внутренности движка.
    """
    work = chat_dir(chat_id, settings)
    if create:
        work.mkdir(parents=True, exist_ok=True)
    elif require_state and not state_file(chat_id, settings).is_file():
        raise InspectionNotStarted(
            f"В этом чате проверка не начата — нет {state_file(chat_id, settings)}. "
            f"Сначала start_inspection()"
        )
    # Рабочий каталог движка — папка чата: именно относительно неё он ищет форк
    # чек-листа и резолвит относительные пути фотографий.
    assert_no_checklist_fork(work)

    env = dict(os.environ)
    env["INSPECTION_FILE"] = str(state_file(chat_id, settings))
    env["CHECKLIST_DIR"] = str(settings.data_dir)
    command = [sys.executable, str(settings.audit_script), *args]
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
        raise EngineError(
            f"Движок не ответил за {TIMEOUT_SEC:g} с — состояние проверки занято "
            f"другим процессом. Команда: {' '.join(args)}",
            code=-1,
            command=" ".join(args),
        ) from exc
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip() or "движок отказал без объяснения"
        raise EngineError(message, code=done.returncode, command=" ".join(args))
    return done.stdout
