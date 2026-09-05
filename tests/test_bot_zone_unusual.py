"""T147: пометка «зона не из списка пункта» видна везде, где видна запись (#118).

Движок нетипичную зону не отклоняет, а помечает флагом `zone_unusual`
(`engine/audit.py`, `docs/04-engine.md`). Показать пометку обязан бот — так и
записано в собственной документации `view.confirm_line`: «иначе о ней узнает
только партнёр».

Ровно это и происходило. Пометку показывала одна первичная фиксация. Правка
зоны — самый частый способ увести запись туда, где пункта нет, — молчала;
список перед сборкой отчёта молчал тоже. Аудитор менял зону, видел
«Поправлено», ничего настораживающего не читал и отправлял отчёт. Партнёр
получал вычет по одной зоне со свидетельством про другую.

Проверяется настоящим диалогом: запись заводится движком в правильной зоне,
уводится в неправильную кнопкой правки, и дальше смотрим ровно то, что аудитор
прочитал бы в чате.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, feed, make_bot, text_message
from bot_harness import callback_query as callback

from src.bot import view
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import add_finding, edit_finding, start_inspection

# Меток у модуля нет: два случая ниже синхронные — они смотрят на строки, а не
# на диалог, и метка asyncio на них была бы неправдой. Боевая методика файлу не
# нужна: он идёт по синтетической через `domain_env` (T141).

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Пункт заведён только для горячего цеха, поэтому зал для него — нетипичная
#: зона. Пара взята из `tests/test_domain_finding_invariants.py`, где тот же
#: флаг проверяется со стороны движка.
ПУНКТ = "CLN05"
СВОЯ_ЗОНА = "hot_kitchen"
ЧУЖАЯ_ЗОНА = "dining"

ПОМЕТКА = t("record.zone_unusual", "ru")


def начать_с_записью() -> None:
    """Проверка с одной записью в правильной для пункта зоне."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-08-21", auditor="Гарро")
    finding = add_finding(CHAT_ID, code=ПУНКТ, level="D1", zone=СВОЯ_ЗОНА, text="нагар на поду")
    assert finding.zone_unusual is False, "оснастка начала с уже нетипичной зоны"


@pytest.mark.asyncio
async def test_правка_зоны_в_чужую_называет_пометку_вслух(domain_env: Path) -> None:
    """Главный случай задачи: правка уводит запись туда, где пункта нет."""
    начать_с_записью()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback(f"ez:1:{ЧУЖАЯ_ЗОНА}"))

    assert ПОМЕТКА in session.last_text, (
        "правка увела запись в зону, где пункта нет, а бот ответил «Поправлено» "
        "и ничем не насторожил"
    )


@pytest.mark.asyncio
async def test_правка_обратно_в_свою_зону_пометку_снимает(domain_env: Path) -> None:
    """Пометка идёт от записи, а не липнет к ней навсегда.

    Иначе получилось бы предупреждение, которое ничего не значит: аудитор
    исправил зону, а строка продолжает пугать.
    """
    начать_с_записью()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback(f"ez:1:{ЧУЖАЯ_ЗОНА}"))
    await feed(dp, bot, callback(f"ez:1:{СВОЯ_ЗОНА}"))

    assert ПОМЕТКА not in session.last_text, "зона снова своя, а пометка осталась"


@pytest.mark.asyncio
async def test_список_перед_сборкой_отчёта_показывает_пометку(domain_env: Path) -> None:
    """Предвычитка — последний момент, когда ошибку в зоне ещё можно поймать.

    После неё отчёт уходит партнёру, и пометки в нём нет (её не показывает
    `engine/report.py`). Значит, список в чате — единственное место, где
    нетипичная зона обязана быть видна перед отправкой.
    """
    начать_с_записью()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, callback(f"ez:1:{ЧУЖАЯ_ЗОНА}"))
    session.clear()
    await feed(dp, bot, text_message("/finish"))

    список = [текст for текст in session.texts if f"#1 {ПУНКТ}" in текст]
    assert список, "в итоге завершения нет строки записи"
    assert ПОМЕТКА in список[0], "список перед сборкой отчёта о нетипичной зоне молчит"


@pytest.mark.asyncio
async def test_обычная_запись_список_не_засоряет(domain_env: Path) -> None:
    """Пометка редкая и потому заметная — на каждой строке она бы обесценилась."""
    начать_с_записью()
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    assert ПОМЕТКА not in "\n".join(session.texts), "пометка стоит у записи в её же зоне"


def test_пометка_собирается_одним_правилом_на_все_три_строки(domain_env: Path) -> None:
    """Фиксация, правка и список обязаны решать про пометку одинаково.

    Три места, три отдельных условия — это ровно тот способ, которым пометка и
    потерялась в двух из трёх. Здесь проверяется, что правило одно.
    """
    начать_с_записью()
    запись = edit_finding(CHAT_ID, 1, zone=ЧУЖАЯ_ЗОНА)
    assert запись.zone_unusual is True, "движок не пометил запись — проверять нечего"

    assert ПОМЕТКА in view.confirm_line(запись, "ru", chat_id=CHAT_ID)
    assert ПОМЕТКА in view.changed_line(запись, "ru", chat_id=CHAT_ID)
    assert ПОМЕТКА in view.record_lines([запись], "ru", chat_id=CHAT_ID)


def test_пометка_переводится_вместе_с_остальным_интерфейсом(domain_env: Path) -> None:
    """Язык — параметр и здесь: английский аудитор читает предупреждение по-английски."""
    начать_с_записью()
    запись = edit_finding(CHAT_ID, 1, zone=ЧУЖАЯ_ЗОНА)
    английская = t("record.zone_unusual", "en")

    assert английская in view.changed_line(запись, "en", chat_id=CHAT_ID)
    assert английская in view.record_lines([запись], "en", chat_id=CHAT_ID)
