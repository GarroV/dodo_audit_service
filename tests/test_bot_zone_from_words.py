"""T124: зона берётся из слов текущего комментария, а не из памяти (задача #99).

Правило 6 `docs/03-recording-rules.md`: «Зону определяет человек или разбор его
комментария». Спека — «зона бралась из моих слов». D047 — зона из слов
комментария. D048 памяти отводит роль **догадки**, а не источника: «последняя
названная зона запоминается и подставляется как первая догадка».

До задачи порядок был обратный: зона всегда бралась из заметок бота, то есть из
памяти о ПРОШЛОЙ записи, а слова текущего комментария на неё не влияли вовсе.
«В зале лужа» ложилось в горячий цех, а бот при этом писал «записал по вашим
словам» — вычет уезжал в чужую зону отчёта партнёру.

Здесь проверяются обе половины: сам разбор слов (`zones.zone_from_words`) и то,
что разбор реально стоит перед памятью в разговоре.
"""

from __future__ import annotations

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

from src.bot import sidecar, zones
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.domain import get_state, start_inspection

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def начата() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-08-21", auditor="Гарро")


# --- разбор слов сам по себе ------------------------------------------------


@pytest.mark.parametrize(
    ("слова", "зона"),
    [
        ("в зале разлито и скользко", "dining"),
        ("Полы в зале не убраны с утра", "dining"),
        ("касса грязная", "dining"),
        ("щель между оборудованием и стеной за кассовой стойкой", "dining"),
        ("пол грязный, тепловой участок", "hot_kitchen"),
        ("загрязнение рабочего стола, холодный участок", "cold_kitchen"),
        ("тестомес не накрыт", "dough"),
        ("мусор в углу, мучной участок", "dough"),
        # Дословный пример владельца из ответа 03.09.2026: «мучной цех, на
        # дрожжах нет маркировки». Приёмка поймала, что разбор его не узнавал.
        ("мучной цех, на дрожжах нет маркировки", "dough"),
        ("налёт, посудный участок", "dishwashing"),
        ("dish station is not cleaned", "dishwashing"),
        ("на стеллаже хранения коробки на полу", "dry_storage"),
        ("в низкотемпературном шкафу наледь", "freezer"),
        ("в морозилке наледь", "freezer"),
        ("в среднетемпературном шкафу коробки на полу", "fridge"),
        ("пол в бытовом блоке в пыли", "staff"),
        ("utility block is a mess", "staff"),
        # Составное имя зоны («Бытовой блок / раздевалка») — два имени одного
        # места, и произносят их по отдельности. Ветку разрезания имени
        # (`_SPLIT` в `src/bot/zones.py`) стережёт именно эта пара случаев:
        # без них зона с косой чертой в названии перестала бы узнаваться по
        # второму имени молча.
        ("в раздевалке не убрано", "staff"),
        ("the changing room is a mess", "staff"),
        ("внешний контур в подтёках", "facade"),
        ("на внешнем контуре здания мусор", "facade"),
        ("the low-temperature cabinet door does not close", "freezer"),
        ("dirt on the floor in the heat station", "hot_kitchen"),
    ],
)
def test_зона_читается_из_слов_аудитора(domain_env: Path, слова: str, зона: str) -> None:
    assert zones.zone_from_words(слова, chat_id=CHAT_ID) == зона


@pytest.mark.parametrize(
    "слова",
    [
        "",
        "нагар на подине печи",
        "просрочка чизкейк",
        "мусор в углу",
    ],
)
def test_слова_без_зоны_зоны_не_дают(domain_env: Path, слова: str) -> None:
    """Молчание лучше догадки: зону в этом случае подставит память (D048)."""
    assert zones.zone_from_words(слова, chat_id=CHAT_ID) is None


def test_две_названные_зоны_не_выбираются_за_аудитора(domain_env: Path) -> None:
    """Названы обе — выбирать между ними системе нечем, и она не выбирает."""
    слова = "тепловой участок, холодный участок: открытый продукт носят между ними"
    assert zones.zone_from_words(слова, chat_id=CHAT_ID) is None


def test_длинное_название_зоны_сильнее_короткого(domain_env: Path) -> None:
    """Зона, названная одним словом, слабее зоны, названной двумя.

    Разбор идёт по названным целиком строкам, и более длинная выигрывает:
    иначе оборудование или обстановка с похожим названием утащили бы запись в
    чужую зону. Здесь «зал» задевает гостевой зал одной основой, а тепловой
    участок назван двумя, — выигрывает участок.
    """
    assert zones.zone_from_words("зал, тепловой участок", chat_id=CHAT_ID) == "hot_kitchen"
    assert (
        zones.zone_from_words("течь под мойкой, тепловой участок", chat_id=CHAT_ID) == "hot_kitchen"
    )


def test_зоны_берутся_из_методики_а_не_из_списка_в_коде(domain_env: Path) -> None:
    """Код зоны, которого нет в методике, разбор вернуть не может."""
    from src.domain import list_zones

    известные = {z.code for z in list_zones()}
    for слова in ("в зале лужа", "посудный участок в налёте", "внешний контур в подтёках"):
        assert zones.zone_from_words(слова, chat_id=CHAT_ID) in известные


# --- разговор: слова сильнее памяти -----------------------------------------


@pytest.mark.asyncio
async def test_запись_ложится_в_названную_словами_зону_а_не_в_запомненную(
    domain_env: Path,
) -> None:
    """Тот самый случай из сверки: «в зале» после записи на тепловом участке.

    Память говорит «тепловой участок», слова — «зал». Побеждают слова: иначе
    вычет уезжает в чужую зону отчёта партнёру, а бот при этом пишет «по вашим
    словам».
    """
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, _ = make_bot()

    await feed(
        build_dispatcher(SETTINGS),
        bot,
        photo_message("frame-1", caption="в зале урна переполнена"),
    )

    проверка = get_state(CHAT_ID)
    assert проверка is not None and проверка.findings, "запись не появилась"
    assert проверка.findings[-1].zone == "dining", "зону взяли из памяти, а не из слов"


@pytest.mark.asyncio
async def test_зона_из_слов_уходит_подсказкой_и_в_модель(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Разбор моделью получает ту же зону: она подсказка, а не украшение."""
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    звали = stub_classify(monkeypatch, suggestion(candidate("SFT03", "D1", "dining")))
    bot, _ = make_bot()

    await feed(
        build_dispatcher(SETTINGS),
        bot,
        photo_message("frame-1", caption="сотрудник без перчаток в зале"),
    )

    assert звали, "разбор не состоялся — проверять подсказку зоны не на чем"
    _note, _photo, подсказка, _lang = звали[-1]
    assert подсказка == "dining", "модели ушла зона из памяти, хотя аудитор назвал другую"


@pytest.mark.asyncio
async def test_память_подставляется_когда_зоны_в_словах_нет(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D048 в силе: не названа — берётся последняя, как догадка."""
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    звали = stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, _ = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, photo_message("frame-1", caption="мусор в углу"))

    assert звали, "разбор не состоялся — проверять подсказку зоны не на чем"
    _note, _photo, подсказка, _lang = звали[-1]
    assert подсказка == "hot_kitchen"


@pytest.mark.asyncio
async def test_запись_по_словам_называет_зону_догадкой_если_её_не_называли(
    domain_env: Path,
) -> None:
    """Быстрый путь пишет без подтверждения — значит, не имеет права умалчивать.

    Зона взята из памяти, а сообщение говорит «по вашим словам»: про зону это
    неправда, и именно на ней промах остаётся незамеченным.
    """
    начата()
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, photo_message("frame-1", caption="печь в нагаре"))

    запись = get_state(CHAT_ID)
    assert запись is not None and запись.findings, "быстрый путь не сработал — проверять нечего"
    assert "Тепловой участок" in session.last_text
    assert "не называли" in session.last_text, "зона из памяти выдана за слова аудитора"


@pytest.mark.asyncio
async def test_названная_словами_зона_догадкой_не_называется(
    domain_env: Path,
) -> None:
    """Зону произнесли — оговорки в записи нет, она была бы шумом."""
    начата()
    sidecar.remember_zone(CHAT_ID, "dining")
    bot, session = make_bot()

    await feed(
        build_dispatcher(SETTINGS),
        bot,
        photo_message("frame-1", caption="печь в нагаре, тепловой участок"),
    )

    запись = get_state(CHAT_ID)
    assert запись is not None and запись.findings
    assert запись.findings[-1].zone == "hot_kitchen"
    assert "не называли" not in session.last_text
