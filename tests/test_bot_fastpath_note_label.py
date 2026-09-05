"""T219 (#167): на быстром пути текст записи подписан как текст для отчёта.

Быстрый путь (T121, D064) кладёт слова аудитора в запись как есть — они и
становятся формулировкой, которую отчёт печатает партнёру. Подпись «ваши слова»
была верна ровно наполовину: слова действительно его, но в показе это уже не
эхо сказанного, а строка документа. Разница не словесная — расписку в получении
не вычитывают.

Цена видна на правке. Аудитор меняет зону кнопкой, формулировка остаётся
прежней и называет старое место: партнёру уезжает запись, спорящая сама с
собой. Путь с подтверждением подписывает тот же текст «В отчёт» — и тот же
самый текст на двух путях назывался по-разному.

Сверяется не буква подписи, а её совпадение между путями: тексты переводятся и
правятся, и требовать здесь конкретную фразу значило бы чинить тест на каждую
правку формулировки. Совпадать они обязаны на обоих языках интерфейса — язык
это параметр, и правка, доведённая только по-русски, оставила бы англоязычного
аудитора с прежней подписью.
"""

from __future__ import annotations

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

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import UI_LANGS, t
from src.domain import get_item, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза на синтетической карте: строка «Печь» плюс колонка
#: «грязная» → CLN05. Та же, на которой стоят тесты быстрого пути.
CLEAR = "печь грязная"

#: Чем подменяется текст записи в сверке подписей: строка, которой заведомо нет
#: ни в одном шаблоне, иначе подпись нашлась бы не в той строке сообщения.
MARK = "⁣текст-запись⁣"


def _подпись(сообщение: str, текст: str) -> str:
    """Чем подписан текст записи: всё, что стоит перед ним в его строке."""
    строка = next(s for s in сообщение.splitlines() if текст in s)
    return строка.split(текст)[0]


async def test_подпись_текста_совпадает_с_путём_подтверждения() -> None:
    """Один и тот же текст на двух путях называется одинаково — на обоих языках."""
    for lang in UI_LANGS:
        быстрый = t(
            "record.fixed", lang, line="", guess="", title="", note=MARK, cue="строка карты"
        )
        с_подтверждением = t("record.confirmed", lang, line="", guess="", title="", note=MARK)

        assert _подпись(быстрый, MARK) == _подпись(с_подтверждением, MARK), (
            f"на языке {lang} быстрый путь подписывает текст записи иначе, "
            f"чем путь с подтверждением: {_подпись(быстрый, MARK)!r} "
            f"против {_подпись(с_подтверждением, MARK)!r}"
        )


async def test_на_быстром_пути_показана_именно_эта_подпись(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сторож к подмене доказательства: подпись сверена на живом показе, не только в словаре.

    Совпадение шаблонов ничего не стоит, если показ собирается не из них: сюда
    приходит настоящий диспетчер, настоящая сверка с картой и настоящая запись.
    """
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    ожидаемая = _подпись(t("record.confirmed", "ru", line="", guess="", title="", note=MARK), MARK)
    assert _подпись(session.last_text, CLEAR) == ожидаемая, (
        f"показ быстрого пути подписывает слова иначе: {session.last_text!r}"
    )
    assert get_item("CLN05").question("ru") in session.last_text, (
        "вопрос пункта пропал из показа — сверять подпись стало не у чего"
    )
