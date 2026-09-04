"""T151: отказ сборки отчёта говорит с человеком, а не с администратором (#122).

Задача T127 запретила показывать аудитору внутренности движка: командной
строки в чате нет, отказ разбирается, а не пересказывается. Сборка отчёта это
правило обходила стороной — она пересказывала отказ дословно, и человек,
стоящий в пиццерии, читал:

    Отчёт не собрался: Отчёт не собран: Рендерер PDF недоступен: … Нужен
    WeasyPrint 69.x и системные библиотеки Pango. HTML сохранён: /var/folders/…

Три вещи разом: удвоенный служебный префикс («не собрался» + «не собран»),
абсолютный путь во временный каталог и инструкция по установке системных
библиотек. Сделать с этим на точке нельзя ничего, а починить — тем более.

Отказ здесь настоящий: движок запускается подпроцессом в окружении, где
рендерера нет, и падает своим обычным `sys.exit`. Подменять текст отказа
выдумкой нельзя — проверялась бы выдумка.

Образец рядом: отказ базы из очереди T123 (`finish.not_archived`) и отказ
разбора из T154 (`record.degraded`) — оба говорят, что случилось и что делать,
а разбор оставляют журналу.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, feed, make_bot, text_message
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import add_finding, start_inspection

pytestmark = [pytest.mark.asyncio]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Слова, которых человеку на точке в чате быть не должно ни в каком отказе.
#: Половина — внутренности рендерера, половина — способ их починить, и то и
#: другое адресовано не тому, кто это прочитает.
ЗАПРЕЩЕНО = ("WeasyPrint", "Pango", "weasyprint", ".html", "Traceback", "audit.py", "report.py")


@pytest.fixture
def стенд_без_рендерера(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Машина, на которой PDF собрать нечем — как площадка без системных библиотек.

    Подменяется не текст отказа, а сам рендерер: движку подкладывается модуль
    `weasyprint`, который при импорте падает. Дальше движок отказывает своими
    словами — теми самыми, которые и утекали аудитору.
    """
    заглушка = tmp_path / "stub-lib"
    заглушка.mkdir()
    (заглушка / "weasyprint.py").write_text(
        "raise ImportError('рендерер недоступен — заглушка теста')\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", str(заглушка))


def начать_с_записью() -> None:
    start_inspection(CHAT_ID, "Белград 2", "Плановая", "ru", date="2026-08-21", auditor="Гарро")
    add_finding(CHAT_ID, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на поду")


@pytest.mark.usefixtures("стенд_без_рендерера")
async def test_отказ_сборки_не_показывает_внутренностей(
    domain_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Главный случай задачи: в чат — что случилось и что делать, разбор — в журнал."""
    начать_с_записью()
    bot, session = make_bot()

    with caplog.at_level("ERROR"):
        await feed(build_dispatcher(SETTINGS), bot, callback("fin:build"))

    сказанное = "\n".join(session.texts)
    assert сказанное, "сборка не удалась, а бот промолчал"
    for слово in ЗАПРЕЩЕНО:
        assert слово not in сказанное, f"аудитору показано «{слово}» — это не его дело"
    assert "/" not in сказанное.replace("/finish", ""), "в чат ушёл путь на диске"
    assert session.last_text == t("finish.pdf_failed", "ru")
    assert "WeasyPrint" in caplog.text, "разбор не записан в журнал — чинить будет нечем"


@pytest.mark.usefixtures("стенд_без_рендерера")
async def test_служебный_префикс_не_удваивается(domain_env: Path) -> None:
    """«Отчёт не собрался: Отчёт не собран: …» — два слоя обёртки в одной строке.

    Так выглядит пересказ отказа: каждый слой добавляет своё вступление, а
    читает их все человек на точке.
    """
    начать_с_записью()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback("fin:build"))

    сказанное = session.last_text
    assert сказанное.lower().count("не собра") == 1, f"служебный префикс удвоился: «{сказанное}»"


@pytest.mark.usefixtures("стенд_без_рендерера")
async def test_отказ_сборки_говорит_что_делать(domain_env: Path) -> None:
    """Отказ без выхода оставляет аудитора там же, где он стоял.

    Записи проверки при неудачной сборке целы, и это главное, что человеку надо
    знать — иначе он решит, что день пропал, и начнёт проверку заново.
    """
    начать_с_записью()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback("fin:build"))

    assert "/finish" in session.last_text, "аудитору не сказано, как попробовать снова"


async def test_неудача_письма_не_называется_несобранным_отчётом(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отчёт уже у аудитора в руках — говорить ему «отчёт не собрался» неправда.

    Письмо собирается вторым вызовом движка, после отдачи PDF. Обе неудачи шли
    одним текстом, и второй раз он врал: документ аудитор к этому моменту уже
    получил.
    """
    from src.report.errors import ReportError

    начать_с_записью()

    def письмо_не_собралось(chat_id: int) -> str:
        raise ReportError("движок отказал: /var/folders/tmp/xxx.html")

    monkeypatch.setattr("src.bot.routers.finish.build_letter", письмо_не_собралось)
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback("fin:build"))

    assert session.documents, "отчёт не дошёл до аудитора — проверять нечего"
    assert session.last_text == t("finish.letter_failed", "ru")
    assert "/var/folders" not in session.last_text, "в чат ушёл путь на диске"


async def test_отказ_старта_проверки_не_показывает_стека(domain_env: Path) -> None:
    """Тот же запрет на входе в проверку, а не только на выходе (T127).

    `start.failed` пересказывал отказ движка дословно, а движок отвечает
    вызывающему из командной строки — полным стеком с путями к своим файлам.
    Найдено по ходу T151: дефект того же рода и в том же блоке.

    Отказ настоящий: на месте состояния лежит каталог, и движку некуда писать.
    Так это и выглядит на площадке после неудачного монтирования тома.
    """
    from src.domain.config import check_environment
    from src.domain.engine import state_file

    файл = state_file(CHAT_ID, check_environment())
    файл.parent.mkdir(parents=True, exist_ok=True)
    файл.mkdir()

    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback("start:new"))
    await feed(dp, bot, text_message("Белград 2"))
    await feed(dp, bot, callback("start:kind:planned"))
    await feed(dp, bot, callback("start:lang:ru"))

    assert "Traceback" not in session.last_text, "аудитору показан стек движка"
    assert "audit.py" not in session.last_text, "аудитору показан путь к файлу движка"
    assert str(файл) not in session.last_text, "аудитору показан путь к состоянию"
    assert session.last_text == t("start.failed", "ru")
