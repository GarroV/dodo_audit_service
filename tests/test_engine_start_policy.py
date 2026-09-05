"""T190: три немедленных повтора не спасают, и политика повтора одна на всех.

Лечение T187/T189/T191 повторяет не состоявшийся старт интерпретатора движка
три раза подряд и без паузы. Этого мало, и это измерено, а не предположено: блок,
лечивший домен, поймал живьём на нагрузке 250+ настоящий движок, умерший до
первой строки **три раза подряд**, — то есть все три попытки уложились в одно и
то же занятое окно машины, и аудитор всё равно получил «беда на стороне машины».

Отсюда два требования, и оба проверяются здесь:

1. **Попыток больше трёх, и между ними есть пауза.** Немедленный повтор
   отличается от предыдущей попытки на доли миллисекунды и попадает в то же
   окно; пауза разносит попытки по времени. Пауза при этом растёт, а её сумма
   ограничена бюджетом: аудитор стоит на точке и ждёт ответа бота, и слишком
   долгое молчание он читает как зависший бот — это хуже честного отказа.

2. **Числа общие для всех трёх вызывающих движка** (`src/domain/engine.py`,
   `src/mcp/checklist.py`, `src/report/engine.py`). Три копии одной политики
   разъедутся при первой же правке одной из них, и тогда в одинаковой беде три
   места поведут себя по-разному — разбираться в этом будет тот, у кого на руках
   один симптом на три разных причины. Поэтому здесь проверяется не равенство
   значений (равными они могут оказаться и случайно), а то, что объявление
   живёт ровно в одном файле, и что все трое ждут ровно по расписанию.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

import src.domain.engine as domain_engine
import src.mcp.checklist as mcp_checklist
import src.report.engine as report_engine
from src.domain import start_inspection
from src.domain.config import Settings, check_environment
from src.domain.errors import EngineError
from src.mcp.errors import EngineNoVerdictError
from src.report.errors import ReportError

CHAT = 8472

SRC = Path(__file__).resolve().parents[1] / "src"

#: Интерпретатор умер в `getpath` — ни одной строки движка не выполнено.
СТАРТ_НЕ_СОСТОЯЛСЯ = (
    "InterruptedError: [Errno 4] Interrupted system call\n"
    "Fatal Python error: error evaluating path\n"
    "Python runtime state: core initialized\n"
)

#: Что намерено 05.09.2026 на этой машине (10 ядер, load average 600, 240
#: занятых окон за 90 с): сколько держится состояние, в котором процесс вытеснен
#: и его системный вызов может быть прерван. Медиана 405 мс, p90 768 мс,
#: максимум 1919 мс; за 1 с кончались 98% окон, за 2 с — все.
ОКНО_P90_СЕК = 0.768
ОКНО_МАКС_СЕК = 1.919

#: Первый повтор платится за каждую беду, даже мгновенную, — он обязан быть
#: дешёвым.
ПЕРВЫЙ_ПОВТОР_НЕ_ДОРОЖЕ = 0.5

#: Имена, которыми задаётся политика повтора. Каждое обязано быть объявлено один
#: раз на весь продукт.
ПОЛИТИКА = re.compile(r"^(ENGINE_ATTEMPTS|RETRY_PAUSE_SEC|RETRY_BUDGET_SEC)\s*=", re.M)


def _расписание() -> list[float]:
    return [domain_engine.retry_pause(n) for n in range(1, domain_engine.ENGINE_ATTEMPTS)]


def _где_объявлено() -> dict[str, list[str]]:
    """Имя политики → файлы `src/`, которые его объявляют."""
    найдено: dict[str, list[str]] = {}
    for файл in sorted(SRC.rglob("*.py")):
        for имя in ПОЛИТИКА.findall(файл.read_text(encoding="utf-8")):
            найдено.setdefault(имя, []).append(str(файл.relative_to(SRC)))
    return найдено


# --- одна политика на три места ----------------------------------------------


def test_политика_повтора_объявлена_ровно_в_одном_файле() -> None:
    """Разъехавшись, три места поведут себя по-разному в одинаковой беде."""
    объявления = _где_объявлено()
    assert объявления, "политика повтора не объявлена нигде"
    разошлись = {имя: файлы for имя, файлы in объявления.items() if len(файлы) > 1}
    assert not разошлись, f"политика повтора объявлена не в одном месте: {разошлись}"


def test_все_трое_берут_число_попыток_из_одного_места() -> None:
    """Значения могут совпасть и случайно — поэтому рядом стоит проверка выше."""
    assert (
        domain_engine.ENGINE_ATTEMPTS
        == mcp_checklist.ENGINE_ATTEMPTS
        == report_engine.ENGINE_ATTEMPTS
    )


# --- сколько попыток и какая пауза -------------------------------------------


def test_попыток_больше_трёх() -> None:
    """Три подряд ловили живьём на нагрузке 250+ — значит, трёх не хватает."""
    assert domain_engine.ENGINE_ATTEMPTS > 3, (
        "три немедленных повтора уже проверены боем и не спасли"
    )


def test_пауза_растёт_от_попытки_к_попытке() -> None:
    """Первый повтор дешёвый: чаще всего беда мгновенная, и ждать незачем.
    Дальше пауза растёт — занятое окно машины может быть длиннее секунды."""
    расписание = _расписание()
    assert расписание, "паузы между попытками нет вовсе"
    assert all(шаг > 0 for шаг in расписание), f"нулевая пауза в расписании: {расписание}"
    assert расписание == sorted(set(расписание)), (
        f"пауза не растёт строго: {расписание}. Одинаковые паузы — это те же три "
        f"немедленных повтора, только позже: все они укладываются в одно окно"
    )
    assert расписание[0] <= ПЕРВЫЙ_ПОВТОР_НЕ_ДОРОЖЕ, (
        f"первый повтор стоит аудитору {расписание[0]:g} с; чаще всего беда "
        f"мгновенная, и эта секунда потрачена зря"
    )


def test_последняя_пауза_перешагивает_измеренное_занятое_окно() -> None:
    """Ради чего пауза вообще нужна. Замер 05.09.2026 (нагрузка 600 на 10 ядрах,
    240 окон за 90 с): медиана окна 405 мс, p90 768 мс, максимум 1919 мс. Пауза
    короче окна возвращает попытку в то же самое окно — ровно так и получились
    три неудачи подряд, с которых началась T190."""
    расписание = _расписание()
    assert расписание[-1] >= ОКНО_МАКС_СЕК, (
        f"самая длинная пауза {расписание[-1]:g} с короче самого длинного "
        f"измеренного занятого окна ({ОКНО_МАКС_СЕК:g} с)"
    )
    покрыто = [шаг for шаг in расписание if шаг >= ОКНО_P90_СЕК]
    assert len(покрыто) >= 2, (
        f"перешагивают p90 занятого окна ({ОКНО_P90_СЕК:g} с) только {len(покрыто)} "
        f"пауз из {len(расписание)} — на одну попытку надежды мало"
    )


def test_ожидание_укладывается_в_бюджет_аудитора() -> None:
    """Аудитор стоит на точке. Молчание дольше бюджета он читает как зависший
    бот, а это хуже честного отказа — поэтому у суммы пауз есть потолок."""
    всего = sum(_расписание())
    assert всего <= domain_engine.RETRY_BUDGET_SEC, (
        f"повторы молчат {всего:g} с при бюджете {domain_engine.RETRY_BUDGET_SEC:g} с"
    )


def test_пауза_ждёт_ровно_столько_сколько_обещает(monkeypatch: pytest.MonkeyPatch) -> None:
    """Расписание — не украшение: `pause_before_retry` спит именно по нему."""
    ждали: list[float] = []
    monkeypatch.setattr(time, "sleep", ждали.append)

    domain_engine.pause_before_retry(2)

    assert ждали == [domain_engine.retry_pause(2)]


# --- все трое ждут по одному расписанию --------------------------------------


class НеСтартует:
    """Подделка запуска для `domain` и `report`: интерпретатор мёртв всегда."""

    def __call__(
        self, args: Sequence[str], *, work: Path, env: dict[str, str], settings: Settings
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(args), returncode=1, stdout="", stderr=СТАРТ_НЕ_СОСТОЯЛСЯ
        )


def _не_стартует_mcp(
    script: Path, args: list[str], *, data_dir: Path, cwd: Path, state: Path | None
) -> tuple[int, str]:
    """Подделка запуска для `mcp`: у него свой вид `_launch`, беда та же."""
    return 1, СТАРТ_НЕ_СОСТОЯЛСЯ


def _записывать_паузы(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    ждали: list[float] = []
    monkeypatch.setattr(time, "sleep", ждали.append)
    return ждали


def test_домен_ждёт_между_попытками(domain_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ждали = _записывать_паузы(monkeypatch)
    monkeypatch.setattr("src.domain.engine._launch", НеСтартует())

    with pytest.raises(EngineError):
        domain_engine.run_audit(["score"], chat_id=CHAT, settings=check_environment(), create=True)

    assert ждали == _расписание(), "домен повторяет не по общему расписанию"


def test_отчёт_ждёт_между_попытками(domain_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    start_inspection(CHAT, unit="Белград-1", kind="planned", report_lang="ru")
    ждали = _записывать_паузы(monkeypatch)
    monkeypatch.setattr("src.report.engine._launch", НеСтартует())

    with pytest.raises(ReportError):
        report_engine.run_report(["letter"], chat_id=CHAT, settings=check_environment())

    assert ждали == _расписание(), "сборка отчёта повторяет не по общему расписанию"


def test_хранилище_методики_ждёт_между_попытками(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ждали = _записывать_паузы(monkeypatch)
    monkeypatch.setattr("src.mcp.checklist._launch", _не_стартует_mcp)

    with pytest.raises(EngineNoVerdictError):
        mcp_checklist._run(
            mcp_checklist.MANAGE_SCRIPT,
            ["validate"],
            data_dir=tmp_path,
            cwd=tmp_path,
            state=None,
        )

    assert ждали == _расписание(), "хранилище методики повторяет не по общему расписанию"


def test_удавшийся_запуск_не_ждёт(domain_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пауза платится только за повтор. Лишняя секунда на каждой удавшейся
    команде стоила бы аудитору больше, чем спасает весь этот повтор."""
    ждали = _записывать_паузы(monkeypatch)
    ответы = iter([(1, СТАРТ_НЕ_СОСТОЯЛСЯ), (0, "ok")])

    def запуск(
        args: Sequence[str], *, work: Path, env: dict[str, str], settings: Settings
    ) -> subprocess.CompletedProcess[str]:
        код, вывод = next(ответы)
        return subprocess.CompletedProcess(
            args=list(args), returncode=код, stdout=вывод if код == 0 else "", stderr=вывод
        )

    monkeypatch.setattr("src.domain.engine._launch", запуск)
    domain_engine.run_audit(["score"], chat_id=CHAT, settings=check_environment(), create=True)

    assert ждали == [domain_engine.retry_pause(1)], "ждали не ровно один раз — за один повтор"
