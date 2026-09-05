"""T225 (#181): бот показывает аудитору ЕГО издание методики, а не действующее.

Задачей T169 домен научился читать издание конкретной проверки: снимок берётся
на старте, и запись, правка, подсчёт и отчёт идут по нему. Бот же звал
справочники без чата — то есть по каталогу, который лежит в `AUDIT_DATA_DIR`
СЕЙЧАС. Публикация методики посреди выезда (D049) переставляет этот каталог, и
дальше расходилось всё, что аудитор видит: кнопки зон, кнопки классов, вопрос
пункта в отказе, названия зон в показе записи, состав информационной части.

Цена не косметическая. Аудитору предлагается пункт или класс, которых в его
издании нет, — движок запись не примет, и на точке это выглядит поломкой на
ровном месте. Обратный случай тише и хуже: зона, переименованная переизданием,
перестаёт узнаваться в словах аудитора, и запись уходит в зону из памяти.

Проверяется опытом, а не чтением кода: в каждом тесте проверка заводится,
методика переиздаётся под ней, и дальше спрашивается бот — настоящим
диспетчером там, где путь идёт через него.

Формулировок методики здесь нет (D073): пункты, зоны и классы вычитаны из
данных на ходу, а «переиздание» делается правкой той же копии.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    candidate,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    suggestion,
)
from bot_harness import callback_query as callback

from src import domain
from src.bot import info, refusal, view, zones
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import UI_LANGS

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Название, которого в методике заведомо нет: им переиздание и подменяет старое.
НОВОЕ_ИМЯ = "Зона нового издания"

#: Класс, которого у пункта не было: им переиздание подменяет прежний набор.
ЧУЖОЙ_КЛАСС = "D3"


@pytest.fixture
def методика(data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Своя копия методики: тест её переиздаёт, общий каталог не трогает."""
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return data_copy


def _строки(путь: Path) -> tuple[list[dict[str, str]], list[str]]:
    with путь.open(encoding="utf-8-sig", newline="") as f:
        строки = list(csv.DictReader(f))
    return строки, list(строки[0].keys())


def _записать(путь: Path, строки: list[dict[str, str]], колонки: list[str]) -> None:
    with путь.open("w", encoding="utf-8", newline="") as f:
        писарь = csv.DictWriter(f, fieldnames=колонки)
        писарь.writeheader()
        писарь.writerows(строки)


def пункт_с_классами(chat_id: int = CHAT_ID) -> tuple[str, str, list[str]]:
    """Пункт с зоной и хотя бы двумя классами, ни один из которых не `ЧУЖОЙ_КЛАСС`."""
    for пункт in domain.list_items(chat_id=chat_id):
        зоны = [z for z in пункт.zones if z != "*"]
        if зоны and len(пункт.levels) >= 2 and ЧУЖОЙ_КЛАСС not in пункт.levels:
            return пункт.code, зоны[0], list(пункт.levels)
    raise AssertionError("в методике не нашлось пункта с зоной и двумя классами")


def начать() -> tuple[str, str, list[str]]:
    """Проверка с одной записью. Возвращает пункт, зону и классы ЕГО издания."""
    domain.start_inspection(CHAT_ID, "Тестовая", "planned", "ru")
    код, зона, классы = пункт_с_классами()
    domain.add_finding(CHAT_ID, код, классы[0], зона, "формулировка теста")
    return код, зона, классы


def переиздать_зону(data_dir: Path, код: str) -> str:
    """Переиздание, переименовавшее зону на всех языках. Возвращает прежнее имя."""
    путь = data_dir / "zones.csv"
    строки, колонки = _строки(путь)
    зона = next(с for с in строки if с["code"] == код)
    прежнее = зона["name_ru"]
    for lang in UI_LANGS:
        зона[f"name_{lang}"] = f"{НОВОЕ_ИМЯ} {lang}"
    _записать(путь, строки, колонки)
    return прежнее


def переиздать_классы(data_dir: Path, код: str) -> None:
    """Переиздание, оставившее пункту единственный и другой класс."""
    путь = data_dir / "checklist.csv"
    строки, колонки = _строки(путь)
    next(с for с in строки if с["id"] == код)["levels"] = ЧУЖОЙ_КЛАСС
    _записать(путь, строки, колонки)


def снять_пункт(data_dir: Path, код: str) -> None:
    """Переиздание, снявшее пункт вовсе."""
    путь = data_dir / "checklist.csv"
    строки, колонки = _строки(путь)
    _записать(путь, [с for с in строки if с["id"] != код], колонки)


def кнопки(session: object) -> list[str]:
    return session.keyboard_texts()  # type: ignore[attr-defined,no-any-return]


async def test_кнопки_зон_при_правке_остаются_изданием_проверки(методика: Path) -> None:
    """Аудитор жмёт «сменить зону» — и видит зоны СВОЕГО издания."""
    _, зона, _ = начать()
    прежнее = переиздать_зону(методика, зона)
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback("edit:1:zone"))

    надписи = кнопки(session)
    assert прежнее in надписи, "аудитору предложены зоны действующей методики, а не его издания"
    assert not [n for n in надписи if n.startswith(НОВОЕ_ИМЯ)], "в перечень попало переиздание"


async def test_кнопки_классов_при_правке_остаются_изданием_проверки(методика: Path) -> None:
    """Класс, которого в издании проверки у пункта нет, движок бы не принял."""
    код, _, классы = начать()
    переиздать_классы(методика, код)
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, callback("edit:1:level"))

    надписи = кнопки(session)
    assert sorted(n for n in надписи if n in классы) == sorted(классы), (
        "аудитору предложены классы действующей методики"
    )
    assert ЧУЖОЙ_КЛАСС not in надписи, "предложен класс, которого в издании проверки нет"


async def test_название_зоны_в_показе_записи_остаётся_изданием_проверки(методика: Path) -> None:
    """Зона в показе записи — та же, что аудитор выбирал кнопкой."""
    _, зона, _ = начать()
    прежнее = переиздать_зону(методика, зона)

    запись = domain.get_state(CHAT_ID).findings[0]  # type: ignore[union-attr]

    assert прежнее in view.confirm_line(запись, "ru", chat_id=CHAT_ID)
    assert прежнее in view.record_lines([запись], "ru", chat_id=CHAT_ID)
    assert view.zone_title(зона, "ru", chat_id=CHAT_ID) == прежнее


async def test_зона_узнаётся_по_имени_из_издания_проверки(методика: Path) -> None:
    """Слова аудитора не обязаны догонять переиздание посреди его же выезда."""
    _, зона, _ = начать()
    прежнее = переиздать_зону(методика, зона)

    assert zones.zone_from_words(прежнее, chat_id=CHAT_ID) == зона, (
        "зона перестала узнаваться в словах после переиздания"
    )


async def test_вопрос_пункта_в_отказе_остаётся_изданием_проверки(методика: Path) -> None:
    """Снятый переизданием пункт бот всё равно называет словами, а не кодом."""
    код, _, _ = начать()
    вопрос = domain.get_item(код, chat_id=CHAT_ID).question("ru")
    снять_пункт(методика, код)

    assert refusal.item_title(код, "ru", chat_id=CHAT_ID) == вопрос, (
        "отказ назвал пункт кодом: методику прочитали действующую"
    )


async def test_информационная_часть_спрашивает_поля_издания_проверки(методика: Path) -> None:
    """Состав вопросов не меняется на середине: аудитор отвечает на свой набор."""
    начать()
    спрошены = [поле.code for поле, _ in info.fields_to_ask("ru", chat_id=CHAT_ID)]
    assert спрошены, "в синтетической методике нет информационных пунктов — проверять нечего"
    снять_пункт(методика, спрошены[0])

    снова = [поле.code for поле, _ in info.fields_to_ask("ru", chat_id=CHAT_ID)]

    assert снова == спрошены, "набор вопросов изменился переизданием посреди проверки"


async def test_кнопки_зон_при_фиксации_остаются_изданием_проверки(
    методика: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Второй вход к тем же кнопкам: зону спрашивают и на самой фиксации.

    Дорога длиннее (кадр без слов, разбор, «Выбрать пункт»), и написан этот
    случай отдельно не для симметрии: снятие чата ИМЕННО здесь не краснило
    ничего во всём наборе — проверено порчей.
    """
    _, зона, _ = начать()
    прежнее = переиздать_зону(методика, зона)
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    кадр = photo_message("кадр-без-слов")
    await feed(dp, bot, кадр)
    await feed(dp, bot, callback(f"rec:analyze:{кадр.message_id}"))
    await feed(dp, bot, callback("rec:manual"))

    надписи = session.keyboard_texts()
    assert прежнее in надписи, f"при фиксации предложены зоны действующей методики: {надписи}"
    assert not [n for n in надписи if n.startswith(НОВОЕ_ИМЯ)], "в перечень попало переиздание"
