"""Вызов движка подпроцессом — единственный способ, которым блок его трогает.

Движок не импортируется никогда: расчёт оценки должен физически не иметь шанса
продублироваться в коде продукта (контракт `engine-not-imported` в
`lint-imports`). Путь к состоянию передаётся переменной `INSPECTION_FILE`,
каталог методики — `CHECKLIST_DIR`: без второй переменной движок взял бы данные
из своей копии рядом со скриптом или из форка `checklist_data/` в рабочей папке.

Ненулевой код возврата вердиктом движка считается НЕ всегда (T189). Под
нагрузкой машины интерпретатор подпроцесса умирает ДО первой строки `audit.py`:
системный вызов внутри вычисления его собственных путей прерывается сигналом.
Выданная за вердикт, эта беда приходит аудитору отказом по существу — «Не
записал… Сбой на моей стороне» там, где движок не сказал ничего. Такой запуск
повторяется, и только такой: см. `ENGINE_ATTEMPTS`.

Здесь же живёт вся политика повтора — число попыток и паузы между ними, — и
живёт она в одном месте на весь продукт: тем же расписанием повторяют
`src/mcp/checklist.py` и `src/report/engine.py` (T190).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from .config import Settings, assert_no_checklist_fork
from .errors import EngineError, InspectionNotStarted

STATE_FILE_NAME = "inspection.json"
CHAT_DIR_PREFIX = "chat_"

#: Ключ, под которым в файле проверки лежат поля блока — те, которых у движка
#: нет: языки, издание методики, арендатор. Стоит здесь, рядом с именем и
#: путём файла, а не в `state`: устройство файла читают двое — `state` ведёт
#: его целиком, `edition` берёт оттуда одно поле, не поднимая разбор всей
#: проверки, — и второе имя того же ключа разошлось бы с первым молча.
DOMAIN_KEY = "domain"

#: Движок ждёт освобождения состояния до 30 секунд и только потом отказывает.
#: Здесь запас на это ожидание: бот не должен висеть вечно, но и обрывать
#: честное ожидание блокировки нельзя.
TIMEOUT_SEC = 90.0

#: Что печатает сам интерпретатор, умерший фатально. В выводе движка такого не
#: бывает: каждый свой отказ он объясняет словами и выходит нормально.
RUNTIME_FATAL = "Fatal Python error"

#: ...и что при этом инициализация не закончилась. Состояние интерпретатор
#: называет сам, и `core initialized` означает, что до первой строки скрипта
#: дело НЕ дошло: движок не работал, состояние проверки не тронуто.
RUNTIME_BEFORE_SCRIPT = "Python runtime state: core initialized"

#: Сколько раз пробуем запустить движок, если старт не состоялся, и сколько
#: ждём между попытками. Числа общие на все три места, откуда движок зовут
#: подпроцессом (`src/domain/engine.py`, `src/mcp/checklist.py`,
#: `src/report/engine.py`): три копии одной политики разъехались бы при первой
#: же правке одной из них, и тогда в одинаковой беде три места повели бы себя
#: по-разному.
#:
#: Причина повтора (T189): на занятой машине системный вызов на старте
#: интерпретатора прерывается сигналом, `getpath` получает `InterruptedError:
#: [Errno 4] Interrupted system call`, и процесс умирает, не выполнив ни строки
#: движка.
#:
#: Причина паузы (T190): трёх НЕМЕДЛЕННЫХ повторов не хватило — настоящий
#: движок умирал до первой строки три раза подряд на нагрузке 250+. Немедленный
#: повтор отличается от предыдущей попытки на доли миллисекунды и попадает в то
#: же занятое окно машины. Длина окна измерена (замер 05.09.2026, нагрузка 600
#: на 10 ядрах, 240 окон за 90 с): медиана 405 мс, p90 768 мс, максимум 1919 мс;
#: за 1 с кончаются 98% окон, за 2 с — все наблюдавшиеся. Отсюда и расписание:
#: первый повтор дешёвый (0,2 с уже длиннее 38% окон), последний перешагивает
#: любое наблюдавшееся.
#:
#: Оговорка, без которой числа врут: сама фатальная смерть интерпретатора в
#: замере НЕ воспроизвелась — 262 запуска при нагрузке до 792 прошли без единой.
#: Поэтому вероятность отказа на попытку не измерена, измерена длительность
#: занятого окна — то единственное, что покупает пауза.
RETRY_PAUSE_SEC = (0.2, 0.5, 1.0, 2.0)

#: Потолок молчания на повторах. Аудитор стоит на точке и ждёт ответа бота:
#: молчание дольше бюджета он читает как зависший бот, а это хуже честного
#: отказа. Расписание обязано укладываться в него — проверяется тестом.
RETRY_BUDGET_SEC = 4.0

#: Попыток на одну больше, чем пауз между ними: иначе число попыток и
#: расписание разъедутся молча.
#:
#: Повторяется ровно не состоявшийся старт и никакой другой случай. Через
#: `run_audit` идут и записи находок: повтор запуска, который успел
#: поработать, дописал бы вторую такую же запись. Умерший ПОСЛЕ инициализации
#: не повторяется поэтому никогда — отличает одно от другого сам интерпретатор
#: своими словами, а не догадка по коду возврата.
ENGINE_ATTEMPTS = len(RETRY_PAUSE_SEC) + 1


def retry_pause(attempt: int) -> float:
    """Сколько ждать после попытки номер `attempt`, прежде чем делать следующую."""
    return RETRY_PAUSE_SEC[attempt - 1]


def pause_before_retry(attempt: int) -> None:
    """Подождать перед повтором. Платится только за повтор, не за успех."""
    time.sleep(retry_pause(attempt))


logger = logging.getLogger(__name__)


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

    for attempt in range(1, ENGINE_ATTEMPTS + 1):
        done = _launch(args, work=work, env=env, settings=settings)
        if done.returncode == 0:
            return done.stdout
        output = f"{done.stderr}\n{done.stdout}"
        if RUNTIME_FATAL in output:
            _refuse_no_verdict(args, output=output, attempt=attempt, chat_id=chat_id)
            pause_before_retry(attempt)
            continue
        message = (done.stderr or done.stdout).strip() or "движок отказал без объяснения"
        raise EngineError(message, code=done.returncode, command=" ".join(args))
    raise AssertionError("недостижимо: цикл попыток выходит возвратом или отказом")


def _launch(
    args: Sequence[str], *, work: Path, env: dict[str, str], settings: Settings
) -> subprocess.CompletedProcess[str]:
    """Один запуск `audit.py`. Не уложился в срок — отказ, и повтору не подлежит."""
    command = [sys.executable, str(settings.audit_script), *args]
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
        raise EngineError(
            f"Движок не ответил за {TIMEOUT_SEC:g} с — состояние проверки занято "
            f"другим процессом. Команда: {' '.join(args)}",
            code=-1,
            command=" ".join(args),
        ) from exc


def _refuse_no_verdict(args: Sequence[str], *, output: str, attempt: int, chat_id: int) -> None:
    """Интерпретатор движка умер фатально. Вернуться — значит повторить запуск.

    Повторяется только не состоявшийся старт: движок не работал, состояние
    проверки не тронуто, и второй заход не сделает из записи аудитора дубль.
    Во всех остальных случаях — отказ СВОИМИ словами: текст интерпретатора,
    выданный за слова движка, посылает человека искать дефект в методике
    (T189), которого там нет.
    """
    command = " ".join(args)
    again = RUNTIME_BEFORE_SCRIPT in output and attempt < ENGINE_ATTEMPTS
    started = "старт не состоялся" if RUNTIME_BEFORE_SCRIPT in output else "умер после старта"
    logger.warning(
        "интерпретатор движка умер в чате %s (%s, попытка %s, %s): %s",
        chat_id,
        started,
        attempt,
        "повторяем" if again else "больше не повторяем",
        command,
    )
    if again:
        return
    times = "" if attempt == 1 else f" {attempt} раза подряд"
    raise EngineError(
        f"Движок не дал ответа: его интерпретатор умер до ответа{times}. "
        f"Это беда на стороне машины, а не отказ по команде «{command}»",
        code=-1,
        command=command,
    )
