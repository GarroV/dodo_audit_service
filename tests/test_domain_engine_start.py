"""T189: не состоявшийся старт интерпретатора — не отказ движка по существу.

Симптом пришёл от аудитора, а не из лога: на занятую машину бот отвечает
«Не записал: {item} · {zone}. Сбой на моей стороне» — то есть говорит, что
запись отклонена. Движок при этом не сказал ничего: под нагрузкой системный
вызов внутри вычисления собственных путей интерпретатора прерывается сигналом,
`getpath` получает `InterruptedError: [Errno 4] Interrupted system call`, и
процесс умирает ДО первой строки `audit.py`:

    InterruptedError: [Errno 4] Interrupted system call    (<frozen getpath>)
    Fatal Python error: error evaluating path
    Python runtime state: core initialized

`run_audit` видел ненулевой код возврата и отдавал наверх `EngineError` с
текстом интерпретатора — машинная беда приходила человеку отказом по существу.
Тот же дефект уже вылечен в блоке `mcp` (T187, `src/mcp/checklist.py`); здесь
он лечится в общем вызове движка, через который в движок ходит весь блок `bot`.

Повтор безопасен ровно тогда, когда до кода движка дело не дошло, и об этом
говорит сам интерпретатор (`Python runtime state: core initialized`), а не
догадка по коду возврата. Это существенно именно здесь: `run_audit` зовут и на
запись находки тоже, и повтор запуска, который успел поработать, превратил бы
запись аудитора в дубль. Поэтому в файле проверяются обе стороны различения —
что несостоявшийся старт повторяется и что всё остальное не повторяется
никогда.

**Запуски считаются на стороне родителя, а не подделкой движка.** Счётчик
внутри подделки был написан первым и оказался негодным ровно по причине,
которую чинит задача: на занятой машине (load average 282) один из трёх
запусков подделки умирал сам, не дойдя до своей первой строки, и тест
показывал 2 запуска из 3 на исправном коде. Считать запуски может только тот,
кто их делает.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from src.domain import check_environment
from src.domain.config import Settings
from src.domain.engine import ENGINE_ATTEMPTS, run_audit
from src.domain.errors import EngineError

CHAT = 8461

#: Интерпретатор умер в `getpath` — ни одной строки движка не выполнено.
СТАРТ_НЕ_СОСТОЯЛСЯ = (
    "InterruptedError: [Errno 4] Interrupted system call\n"
    "Fatal Python error: error evaluating path\n"
    "Python runtime state: core initialized\n"
)

#: Тот же фатальный отказ рантайма, но УЖЕ ПОСЛЕ инициализации: строки движка
#: могли выполниться, состояние проверки могло измениться. Повторять нельзя.
УМЕР_ПОЗЖЕ = "Fatal Python error: Segmentation fault\nPython runtime state: initialized\n"

#: Настоящий отказ движка: он разобрал команду и объяснил словами, почему нет.
ОТКАЗ_ДВИЖКА = "зона «нет-такой-зоны» не найдена в методике\n"


class Движок:
    """Подделка запуска движка, считающая свои вызовы в родительском процессе.

    Подменяет `_launch`, то есть подпроцесса не заводит вовсе: число попыток —
    единственное, что здесь проверяется точным числом, и оно не должно зависеть
    от того, повезло ли машине запустить ещё один интерпретатор.
    """

    def __init__(self, *ответы: tuple[int, str]) -> None:
        #: Пары «код возврата, stderr» по попыткам. Последняя повторяется:
        #: подделка не обязана знать, сколько раз её позовут.
        self.ответы = ответы
        self.запусков = 0

    def __call__(
        self, args: Sequence[str], *, work: Path, env: dict[str, str], settings: Settings
    ) -> subprocess.CompletedProcess[str]:
        self.запусков += 1
        код, вывод = self.ответы[min(self.запусков, len(self.ответы)) - 1]
        поток = "" if код else вывод
        return subprocess.CompletedProcess(
            args=list(args), returncode=код, stdout=поток, stderr="" if код == 0 else вывод
        )


@pytest.fixture
def настройки(domain_env: Path) -> Settings:
    return check_environment()


def _подставить(monkeypatch: pytest.MonkeyPatch, движок: Движок) -> Движок:
    monkeypatch.setattr("src.domain.engine._launch", движок)
    return движок


def _позвать(настройки: Settings) -> str:
    return run_audit(["score", "--json"], chat_id=CHAT, settings=настройки, create=True)


# --- несостоявшийся старт: повторяем -----------------------------------------


def test_несостоявшийся_старт_повторяется(
    настройки: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Настоящая причина T189: интерпретатор не дожил до первой строки движка."""
    движок = _подставить(monkeypatch, Движок((1, СТАРТ_НЕ_СОСТОЯЛСЯ)))

    with pytest.raises(EngineError):
        _позвать(настройки)

    assert движок.запусков == ENGINE_ATTEMPTS, "несостоявшийся старт обязан повторяться"


def test_со_второго_запуска_команда_доходит_до_движка(
    настройки: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное утверждение файла: аудитор получает ответ движка, а не «Сбой на
    моей стороне», если старт не состоялся с первого раза."""
    движок = _подставить(monkeypatch, Движок((1, СТАРТ_НЕ_СОСТОЯЛСЯ), (0, "записано")))

    вывод = _позвать(настройки)

    assert "записано" in вывод
    assert движок.запусков == 2


# --- всё остальное: не повторяем ---------------------------------------------


def test_умерший_после_инициализации_не_повторяется(
    настройки: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повтор безопасен только до первой строки движка. Умерший позже мог
    успеть дописать находку — повторённый запуск сделал бы из неё дубль."""
    движок = _подставить(monkeypatch, Движок((1, УМЕР_ПОЗЖЕ)))

    with pytest.raises(EngineError):
        _позвать(настройки)

    assert движок.запусков == 1, "после инициализации повторять нельзя: запись станет дублем"


def test_отказ_движка_остаётся_отказом_движка_и_не_повторяется(
    настройки: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Различение работает в обе стороны: разобранную и отклонённую команду
    объясняет движок своими словами, и второй раз её не зовут."""
    движок = _подставить(monkeypatch, Движок((2, ОТКАЗ_ДВИЖКА)))

    with pytest.raises(EngineError) as отказ:
        _позвать(настройки)

    assert "нет-такой-зоны" in str(отказ.value), "текст движка обязан дойти до аудитора как есть"
    assert движок.запусков == 1, "движок ответил — повторять его ответ нечем"


def test_слова_интерпретатора_не_выдаются_за_отказ_движка(
    настройки: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Fatal Python error` в журнале посылает человека искать дефект в
    методике, которого там нет: движок этой команды не видел."""
    _подставить(monkeypatch, Движок((1, СТАРТ_НЕ_СОСТОЯЛСЯ)))

    with pytest.raises(EngineError) as отказ:
        _позвать(настройки)

    текст = str(отказ.value)
    assert "Fatal Python error" not in текст
    assert "InterruptedError" not in текст
    assert "движок" in текст.lower(), "отказ обязан назвать себя бедой на стороне машины"


# --- то же самое, но через настоящий подпроцесс -------------------------------


def _подделка(tmp_path: Path, тело: str) -> Path:
    путь = tmp_path / "подделка_движка.py"
    путь.write_text("import sys\n" + тело, encoding="utf-8")
    return путь


def test_умерший_интерпретатор_опознаётся_в_выводе_настоящего_подпроцесса(
    настройки: Settings, tmp_path: Path
) -> None:
    """Признак читается из `stderr` живого подпроцесса, а не из подставленного
    объекта. Числа запусков тут нет намеренно: подделка на занятой машине
    умирает и сама, и тогда запусков станет на один больше — а проверяется
    здесь опознание, и оно от этого не меняется."""
    тело = "".join(
        f"print({строка!r}, file=sys.stderr)\n"
        for строка in СТАРТ_НЕ_СОСТОЯЛСЯ.strip().splitlines()
    )
    подделка = _подделка(tmp_path, тело + "sys.exit(1)\n")

    with pytest.raises(EngineError) as отказ:
        _позвать(replace(настройки, audit_script=подделка))

    assert "Fatal Python error" not in str(отказ.value)
    assert "не дал ответа" in str(отказ.value)
