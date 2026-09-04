"""Расхождение версии методики показано человеку и с выбором (T167, задача #135).

Подсчёт оценки отказывается считать проверку по методике, отличной от
записанной в ней (T148): методику успевают издать заново, пока проверка идёт, и
посчитать в таком виде значит выдать оценку по новой методике под старой
отметкой. Отказ движка при этом честный и подробный — но написан он тому, кто
зовёт блок из кода: аудитор стоит на точке с телефоном.

До этой задачи такой отказ доезжал до него общим текстом последнего рубежа
(«что-то пошло не так»). Ни двух версий, ни выхода из тупика в нём нет, а выхода
два, и оба решает человек: перевести проверку на действующую методику (явный
вызов, оставляющий след в самой проверке) или вернуть прежнюю версию на диск и
досчитать по ней. Бот не выбирает ни то, ни другое — он называет обе версии и
даёт кнопки.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, callback_query, feed, make_bot, text_message

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import VERSION_KEEP_CALLBACK, VERSION_SYNC_CALLBACK
from src.domain import add_finding, checklist_version, get_state, list_items, start_inspection
from src.domain.config import check_environment
from src.domain.state import DOMAIN_KEY, HISTORY_KEY, state_file

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)


@pytest.fixture
def методика(data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Своя копия методики: тест её переиздаёт, боевой каталог не трогает."""
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return data_copy


def штрафной_пункт(data_dir: Path) -> tuple[str, str, str]:
    """Пункт, зона и класс с ненулевым вычетом — всё вычитано из методики."""
    ставки = json.loads((data_dir / "scoring.json").read_text(encoding="utf-8"))
    штрафные = {
        уровень for уровень, ставка in (ставки.get("penalty") or {}).items() if float(ставка) > 0
    }
    for пункт in list_items():
        зоны = [z for z in пункт.zones if z != "*"]
        классы = [c for c in пункт.levels if c in штрафные]
        if зоны and классы:
            return пункт.code, зоны[0], классы[0]
    raise AssertionError("в методике не нашлось пункта с зоной и штрафным классом")


def издать_заново(data_dir: Path) -> None:
    """Правка методики, меняющая отпечаток версии: приписка в критериях."""
    критерии = data_dir / "criteria.md"
    критерии.write_text(
        критерии.read_text(encoding="utf-8") + "\n<!-- издание теста -->\n", encoding="utf-8"
    )


def начать(методика: Path) -> str:
    """Проверка с одной штрафной записью. Возвращает записанную версию методики."""
    start_inspection(CHAT_ID, unit="Тестовая", kind="planned", report_lang="ru")
    код, зона, класс = штрафной_пункт(методика)
    add_finding(CHAT_ID, код, класс, зона, "формулировка теста")
    состояние = get_state(CHAT_ID)
    assert состояние is not None
    return состояние.checklist_version


async def завершить(методика: Path) -> tuple[Any, Any, Any, str]:
    """Проверка, переизданная методика и нажатая «Завершить»."""
    отметка = начать(методика)
    издать_заново(методика)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/finish"))
    return dp, bot, session, отметка


async def test_расхождение_версий_названо_обеими_версиями_а_не_общим_отказом(
    методика: Path,
) -> None:
    """Человеку нужны обе: по ним и видно, что методику переиздали под проверкой."""
    _dp, _bot, session, отметка = await завершить(методика)

    текст = session.last_text
    assert отметка in текст, "не названа версия, которой помечена проверка"
    assert checklist_version() in текст, "не названа версия, которая действует сейчас"


async def test_выбор_отдан_аудитору_кнопками_а_не_сделан_за_него(методика: Path) -> None:
    """Оба выхода — решение человека, поэтому оба стоят кнопками."""
    _dp, _bot, session, _ = await завершить(методика)

    assert session.keyboard_data() == [VERSION_SYNC_CALLBACK, VERSION_KEEP_CALLBACK]


async def test_проверка_сама_собой_на_новую_методику_не_переезжает(методика: Path) -> None:
    """Отказ ничего не чинит молча: D033 запрещает менять проверку задним числом."""
    _dp, _bot, _session, отметка = await завершить(методика)

    состояние = get_state(CHAT_ID)
    assert состояние is not None
    assert состояние.checklist_version == отметка


async def test_перевод_на_действующую_методику_идёт_по_кнопке_и_со_следом(
    методика: Path,
) -> None:
    """След остаётся в самой проверке: отвечать «по какой методике считали» придётся потом."""
    dp, bot, session, отметка = await завершить(методика)

    await feed(dp, bot, callback_query(VERSION_SYNC_CALLBACK))

    состояние = get_state(CHAT_ID)
    assert состояние is not None
    assert состояние.checklist_version == checklist_version(), "перевод не состоялся"
    сырое = json.loads(state_file(CHAT_ID, check_environment()).read_text("utf-8"))
    история = сырое[DOMAIN_KEY][HISTORY_KEY]
    assert история and история[-1]["from"] == отметка
    assert история[-1]["to"] == checklist_version()
    assert session.texts[-1], "после перевода бот ничего не сказал"


async def test_после_перевода_итог_показывается_как_обычно(методика: Path) -> None:
    """Тупик кончается там же, где начался: аудитор видит итог и кнопки завершения."""
    dp, bot, session, _ = await завершить(методика)
    session.clear()

    await feed(dp, bot, callback_query(VERSION_SYNC_CALLBACK))

    assert any("%" in текст for текст in session.texts), "итог с процентом не показан"
    assert "fin:build" in session.keyboard_data(), "кнопок завершения нет"


async def test_отказ_разобраться_с_методикой_ничего_не_переставляет(методика: Path) -> None:
    """Второй выход — вернуть прежнюю версию на диск, и делает это человек, не бот."""
    dp, bot, session, отметка = await завершить(методика)
    session.clear()

    await feed(dp, bot, callback_query(VERSION_KEEP_CALLBACK))

    состояние = get_state(CHAT_ID)
    assert состояние is not None
    assert состояние.checklist_version == отметка, "проверка всё-таки переехала"
    assert session.texts, "бот промолчал в ответ на выбор аудитора"
