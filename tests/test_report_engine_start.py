"""T191: сборка отчёта болеет тем же, чем болел домен, — не состоявшимся стартом.

Под нагрузкой машины интерпретатор подпроцесса умирает ДО первой строки
`engine/report.py`: системный вызов внутри вычисления его собственных путей
прерывается сигналом.

    InterruptedError: [Errno 4] Interrupted system call    (<frozen getpath>)
    Fatal Python error: error evaluating path
    Python runtime state: core initialized

`run_report` видел ненулевой код возврата и отдавал наверх `ReportError` с
текстом интерпретатора; `build_pdf` заворачивал его в `PdfNotBuilt`, и аудитор
получал дамп чужого процесса вместо ответа. Лечение то же, что в `src/mcp`
(T187) и в `src/domain` (T189): несостоявшийся старт повторяется, умерший ПОСЛЕ
инициализации — никогда, и отличает одно от другого сам интерпретатор своими
словами, а не догадка по коду возврата.

**Почему повтор здесь безопаснее, чем в домене.** Сборка отчёта только читает
состояние проверки: движок открывает `inspection.json`, считает оценку и пишет
PDF отдельным файлом. Повторный заход не может создать дубль записи — дублировать
нечего. В домене этот довод неверен: через `run_audit` идут и записи находок, и
там повтор запуска, успевшего поработать, дописал бы вторую такую же запись.
Довод проверяется в последнем разделе файла настоящим движком: состояние
сверяется побайтно до и после повторённой сборки.

**Запуски считаются на стороне родителя, а не подделкой движка** — по той же
причине, что и в `tests/test_domain_engine_start.py`: на занятой машине подделка
и сама может не стартовать, и счётчик внутри неё показал бы меньше правды.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from src.domain import add_finding, start_inspection
from src.domain.config import Settings, check_environment
from src.domain.engine import ENGINE_ATTEMPTS, state_file
from src.report import build_pdf
from src.report.engine import run_report
from src.report.errors import PdfNotBuilt, ReportError

CHAT = 8471

#: Интерпретатор умер в `getpath` — ни одной строки сборщика не выполнено.
СТАРТ_НЕ_СОСТОЯЛСЯ = (
    "InterruptedError: [Errno 4] Interrupted system call\n"
    "Fatal Python error: error evaluating path\n"
    "Python runtime state: core initialized\n"
)

#: Тот же фатальный отказ рантайма, но УЖЕ ПОСЛЕ инициализации.
УМЕР_ПОЗЖЕ = "Fatal Python error: Segmentation fault\nPython runtime state: initialized\n"

#: Настоящий отказ сборщика: он разобрал команду и объяснил словами, почему нет.
ОТКАЗ_ДВИЖКА = "шрифт с кириллицей не найден: engine/assets/fonts\n"

НАСТОЯЩИЙ_ДВИЖОК = Path(__file__).resolve().parents[1] / "engine" / "audit.py"
НАСТОЯЩИЙ_СБОРЩИК = Path(__file__).resolve().parents[1] / "engine" / "report.py"


class Сборщик:
    """Подделка запуска `report.py`, считающая свои вызовы в родительском процессе."""

    def __init__(self, *ответы: tuple[int, str]) -> None:
        #: Пары «код возврата, вывод» по попыткам; последняя повторяется.
        self.ответы = ответы
        self.запусков = 0

    def __call__(
        self, args: Sequence[str], *, work: Path, env: dict[str, str], settings: Settings
    ) -> subprocess.CompletedProcess[str]:
        self.запусков += 1
        код, вывод = self.ответы[min(self.запусков, len(self.ответы)) - 1]
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=код,
            stdout=вывод if код == 0 else "",
            stderr="" if код == 0 else вывод,
        )


@pytest.fixture(autouse=True)
def _не_ждать_между_попытками(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пауза между попытками (T190) проверяется в `tests/test_engine_start_policy.py`.

    Здесь она только удлиняла бы прогон: этот файл проверяет ЧИСЛО попыток и
    слова отказа, а не расписание. Замерено: без этой фикстуры набор дорожает на
    26 секунд, и платит их каждый прогон каждого разработчика.
    """
    monkeypatch.setattr("src.report.engine.pause_before_retry", lambda _: None)


@pytest.fixture
def проверка(domain_env: Path) -> Path:
    """Начатая проверка с одной записью. Возвращает каталог состояния."""
    start_inspection(
        CHAT,
        unit="Белград-1",
        kind="planned",
        report_lang="ru",
        date="2026-08-21",
        auditor="Василий Гарро",
        city="Белград",
    )
    add_finding(CHAT, code="CLN05", level="D1", zone="hot_kitchen", text="Нагар на подине печи")
    return domain_env


def _подставить(monkeypatch: pytest.MonkeyPatch, сборщик: Сборщик) -> Сборщик:
    monkeypatch.setattr("src.report.engine._launch", сборщик)
    return сборщик


def _позвать(настройки: Settings) -> str:
    return run_report(["letter"], chat_id=CHAT, settings=настройки)


# --- несостоявшийся старт: повторяем -----------------------------------------


def test_несостоявшийся_старт_повторяется(проверка: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящая причина T191: интерпретатор не дожил до первой строки сборщика."""
    сборщик = _подставить(monkeypatch, Сборщик((1, СТАРТ_НЕ_СОСТОЯЛСЯ)))

    with pytest.raises(ReportError):
        _позвать(check_environment())

    assert сборщик.запусков == ENGINE_ATTEMPTS, "несостоявшийся старт обязан повторяться"


def test_со_второго_запуска_сборка_доходит_до_движка(
    проверка: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное утверждение файла: аудитор получает отчёт, а не дамп чужого
    процесса, если старт не состоялся с первого раза."""
    сборщик = _подставить(
        monkeypatch, Сборщик((1, СТАРТ_НЕ_СОСТОЯЛСЯ), (0, "Здравствуйте, коллеги!"))
    )

    текст = _позвать(check_environment())

    assert "Здравствуйте" in текст
    assert сборщик.запусков == 2


# --- всё остальное: не повторяем ---------------------------------------------


def test_умерший_после_инициализации_не_повторяется(
    проверка: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Различение одно на все три вызывающих: повторяется только тот запуск, про
    который сам интерпретатор сказал, что до первой строки дело не дошло."""
    сборщик = _подставить(monkeypatch, Сборщик((1, УМЕР_ПОЗЖЕ)))

    with pytest.raises(ReportError):
        _позвать(check_environment())

    assert сборщик.запусков == 1, "правило одно на три места: умерший после старта не повторяется"


def test_отказ_сборщика_остаётся_отказом_сборщика_и_не_повторяется(
    проверка: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Движок объяснил словами, почему отчёта нет, — этот текст доходит как есть."""
    сборщик = _подставить(monkeypatch, Сборщик((2, ОТКАЗ_ДВИЖКА)))

    with pytest.raises(ReportError) as отказ:
        _позвать(check_environment())

    assert "шрифт" in str(отказ.value), "текст движка обязан дойти до вызывающего как есть"
    assert сборщик.запусков == 1, "движок ответил — повторять его ответ нечем"


def test_слова_интерпретатора_не_доходят_до_аудитора(
    проверка: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Fatal Python error` в ответе бота — дамп чужого процесса вместо ответа:
    аудитор идёт искать дефект в отчёте, которого там нет."""
    _подставить(monkeypatch, Сборщик((1, СТАРТ_НЕ_СОСТОЯЛСЯ)))

    with pytest.raises(PdfNotBuilt) as отказ:
        build_pdf(CHAT)

    текст = str(отказ.value)
    assert "Fatal Python error" not in текст
    assert "InterruptedError" not in текст
    assert "машины" in текст, "отказ обязан назвать себя бедой на стороне машины"


# --- то же самое, но через настоящий подпроцесс -------------------------------


def test_умерший_интерпретатор_опознаётся_в_выводе_настоящего_подпроцесса(
    проверка: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Признак читается из `stderr` живого подпроцесса, а не из подставленного
    объекта. Числа запусков тут нет намеренно: подделка на занятой машине
    умирает и сама, и тогда запусков станет на один больше."""
    корень = tmp_path / "подставной"
    (корень / "engine").mkdir(parents=True)
    (корень / "engine" / "audit.py").symlink_to(НАСТОЯЩИЙ_ДВИЖОК)
    тело = "".join(
        f"print({строка!r}, file=sys.stderr)\n"
        for строка in СТАРТ_НЕ_СОСТОЯЛСЯ.strip().splitlines()
    )
    (корень / "engine" / "report.py").write_text(
        "import sys\n" + тело + "sys.exit(1)\n", encoding="utf-8"
    )
    monkeypatch.setattr("src.domain.config._REPO_ROOT", корень)

    with pytest.raises(ReportError) as отказ:
        _позвать(check_environment())

    assert "Fatal Python error" not in str(отказ.value)
    assert "не дал ответа" in str(отказ.value)


# --- повтор и состояние проверки: настоящий движок ----------------------------
#
# Довод, ради которого повтор здесь разрешён шире, чем в домене: сборка отчёта
# только читает. Проверяется он не рассуждением о коде, а настоящим
# `engine/report.py` над настоящим состоянием: сборка повторяется после
# несостоявшегося старта, и состояние проверки обязано остаться тем же байт в
# байт — дублировать в нём нечего.


def _обёртка(tmp_path: Path) -> tuple[Path, Path]:
    """Сборщик отчёта, который на первом заходе умирает до своей первой строки.

    Кладётся как `engine/report.py` внутрь подставного корня — так его подхватят
    все обращения блока, а не только те, куда удалось передать свои настройки.
    Рядом символьной ссылкой лежит настоящий `audit.py`: проверку заводит он.

    Каждый заход отмечается в журнале: без счёта запусков проверка не различала
    бы повтор и его отсутствие.
    """
    корень = tmp_path / "подставной"
    (корень / "engine").mkdir(parents=True)
    (корень / "engine" / "audit.py").symlink_to(НАСТОЯЩИЙ_ДВИЖОК)
    журнал = tmp_path / "заходы.txt"
    смерть = "".join(
        f"    print({строка!r}, file=sys.stderr)\n"
        for строка in СТАРТ_НЕ_СОСТОЯЛСЯ.strip().splitlines()
    )
    (корень / "engine" / "report.py").write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        f"журнал = Path({str(журнал)!r})\n"
        'журнал.write_text(журнал.read_text() + "x" if журнал.exists() else "x")\n'
        "if len(журнал.read_text()) == 1:\n" + смерть + "    sys.exit(1)\n"
        f"os.execv(sys.executable, [sys.executable, {str(НАСТОЯЩИЙ_СБОРЩИК)!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    return корень, журнал


def _заходов(журнал: Path) -> int:
    return len(журнал.read_text(encoding="utf-8")) if журнал.exists() else 0


def _слепок(путь: Path) -> str:
    return hashlib.md5(путь.read_bytes()).hexdigest()  # noqa: S324 — сверка файла, не подпись


def test_повтор_несостоявшегося_старта_не_меняет_состояние_проверки(
    проверка: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сборка отчёта только читает — потому повтор здесь и безопаснее, чем в
    домене. Отчёт собирается со второго захода, состояние остаётся тем же."""
    корень, журнал = _обёртка(tmp_path)
    было = _слепок(state_file(CHAT, check_environment()))
    monkeypatch.setattr("src.domain.config._REPO_ROOT", корень)

    отчёт = build_pdf(CHAT)

    assert _заходов(журнал) == 2, "повтора не было — проверять нечего"
    assert отчёт.is_file() and отчёт.read_bytes()[:5] == b"%PDF-", "отчёт не собрался"
    assert _слепок(state_file(CHAT, check_environment())) == было, (
        "повторённая сборка изменила состояние проверки — довод «она только читает» неверен"
    )


def test_отчёт_собирается_один_раз_а_не_дважды(
    проверка: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обратная сторона: удавшийся заход не повторяется. Повтор стоил бы
    аудитору лишних секунд на точке ровно там, где всё уже собралось."""
    корень, журнал = _обёртка(tmp_path)
    журнал.write_text("x", encoding="utf-8")  # первый заход уже «состоялся»
    monkeypatch.setattr("src.domain.config._REPO_ROOT", корень)

    build_pdf(CHAT)

    assert _заходов(журнал) == 2, "удавшийся заход повторён — сборка позвана лишний раз"


def test_не_начатая_проверка_отказывает_без_запуска_сборщика(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повтор не должен размазаться на случаи, где звать движок незачем."""
    сборщик = _подставить(monkeypatch, Сборщик((0, "не должно быть вызвано")))

    with pytest.raises(Exception, match="проверка не начата"):
        run_report(["letter"], chat_id=777091, settings=check_environment())

    assert сборщик.запусков == 0
