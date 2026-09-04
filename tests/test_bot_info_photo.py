"""T179: кадр информационной части доезжает до отчёта, а не до вежливого отказа.

Владелец просил, чтобы информационную часть можно было заполнить текстом или
голосом **и приложить фото** (D069). Текст и голос работали с T158, кадр бот
принимал — и говорил аудитору, что в отчёт попадёт только текст. Тогда это было
правдой: движок хранил поле строкой.

С T172 движок держит поле парой «текст + кадры» и печатает кадр под текстом
своего поля. Значит, прежняя фраза стала неверной, а сам кадр обязан доехать —
и не до команды движка, а до страницы PDF.

Что здесь защищается, по важности:

**Кадр в отчёте.** Последний случай собирает документ через настоящий диспетчер
и достаёт из готового PDF картинки: единственное утверждение, ради которого
задача заведена, — «партнёр видит снимок».

**Карта кадров.** Кадр хранится идентификатором телеграма, а не путём. Не
попади ссылка в карту, которую бот отдаёт движку, — движок напечатал бы на её
месте красную отметку «фотография не приложена». Это было бы ХУЖЕ прежнего
поведения: раньше бот честно предупреждал, а стало бы молчаливое «фото
потеряно».

**Кадр без подписи не теряется.** Поле печатается кадром РЯДОМ С ТЕКСТОМ, и
кадра без ответа в отчёте не существует. Поэтому снимок ждёт ответа на тот же
вопрос, а если вопрос пропустили — бот говорит об этом вслух.

**Прежней лжи больше нет.** Текст «в отчёт попадает только текст» снят из
каталога целиком: оставленный, он однажды всплыл бы на другом пути.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    FAKE_TOKEN,
    RecordingSession,
    feed,
    make_bot,
    photo_message,
    text_message,
)
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.inspection import state_path
from src.bot.texts import TEXTS, t
from src.domain import add_finding, start_inspection
from src.domain.config import check_environment
from src.report.photos import misses_text, resolve_photos

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Первое поле информационной части (D070). Кадр в тестах прикладывается к нему.
ПЕРВОЕ = "INF01"


def начать(*, с_кадром: bool = True) -> None:
    """Проверка с одной записью — отчёту нужно что собирать."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", ui_lang="ru", speech_lang="ru")
    finding = add_finding(CHAT_ID, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")
    assert finding.n == 1
    if с_кадром:
        from src.domain import attach_photo

        attach_photo(CHAT_ID, 1, "record-frame")


def поле(code: str = ПЕРВОЕ) -> dict[str, Any]:
    """Информационное поле так, как оно лежит в состоянии проверки."""
    raw = json.loads(state_path(CHAT_ID).read_text(encoding="utf-8"))
    value = dict(raw.get("info") or {}).get(code)
    if isinstance(value, dict):
        return {"text": str(value.get("text") or ""), "photos": list(value.get("photos") or [])}
    return {"text": str(value or ""), "photos": []}


async def дойти_до_информационной_части(dp: Any, bot: Any) -> None:
    await feed(dp, bot, text_message("/finish"))
    await feed(dp, bot, callback("fin:build"))


class _ФотоСессия(RecordingSession):
    """Сессия, отдающая на скачивание настоящий JPEG.

    Базовая отдаёт пустой поток: файл на диске появляется, но картинкой не
    является, и по такому кадру нельзя утверждать, что партнёр увидит снимок.
    """

    def __init__(self, content: bytes) -> None:
        super().__init__()
        self._content = content

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109 — сигнатура из BaseSession
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield self._content


def jpeg(tmp_path: Path) -> bytes:
    """Настоящий JPEG: движок открывает кадр через Pillow и пересжимает его."""
    from PIL import Image

    path = tmp_path / "кадр.jpg"
    Image.new("RGB", (240, 180), (60, 140, 200)).save(path, "JPEG")
    return path.read_bytes()


def есть_картинка_в_pdf(pdf: Path) -> bool:
    """Есть ли внутри собранного PDF хоть одна картинка.

    По самому файлу, а не по внешнему `pdfimages`: набор не должен зависеть от
    того, поставлен ли на машине poppler. Проверено встречным случаем — тот же
    отчёт без кадра метки `/Subtype /Image` не содержит вовсе, и до правки этот
    тест был красным именно на ней.
    """
    return b"/Subtype /Image" in pdf.read_bytes()


# --- кадр доезжает до поля ----------------------------------------------------


async def test_кадр_с_подписью_прикладывается_к_полю(domain_env: Path) -> None:
    """Подпись — это ответ на вопрос, а кадр — то, что к нему приложено."""
    начать()
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame", caption="Витрина собрана как надо"))

    assert поле()["text"] == "Витрина собрана как надо", "подпись потеряна"
    assert поле()["photos"] == ["info-frame"], "кадр до поля не доехал"


async def test_кадр_без_подписи_ждёт_ответа_и_прикладывается_к_нему(domain_env: Path) -> None:
    """Кадра без ответа в отчёте не существует: он печатается рядом с текстом."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame"))

    assert поле() == {"text": "", "photos": []}, "поле записано без ответа аудитора"
    assert t("info.photo_taken", "ru") in session.texts, "бот промолчал о судьбе кадра"

    await feed(dp, bot, text_message("Витрина собрана как надо"))

    assert поле()["photos"] == ["info-frame"], "кадр не пристал к ответу на тот же вопрос"


async def test_бот_больше_не_обещает_что_печатается_только_текст(domain_env: Path) -> None:
    """Прежняя фраза стала неверной, и снята она целиком, а не переписана мимо."""
    assert "info.photo_only_text" not in TEXTS, (
        "текст про «только текст» остался в каталоге и однажды всплывёт"
    )


async def test_кадр_не_переезжает_на_следующий_вопрос(domain_env: Path) -> None:
    """Кадр принадлежит вопросу, на котором стоит разговор, а не проверке целиком.

    Случай нарочно тот, где кадр остался неиспользованным: вопрос пропущен, а
    следующий отвечен. Переехавший снимок напечатался бы партнёру рядом с чужим
    ответом — и никто, кроме него, этого бы не увидел.
    """
    начать()
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame"))
    await feed(dp, bot, callback("info:skip"))
    await feed(dp, bot, callback("info:no"))  # INF03 — «да/нет»

    assert поле("INF03")["text"], "второй вопрос не записался — случай не проверен"
    assert поле("INF03")["photos"] == [], "кадр первого вопроса уехал во второй"


async def test_пропуск_вопроса_с_кадром_не_проходит_молча(domain_env: Path) -> None:
    """Кадр приложен, ответа нет — печатать его не рядом с чем. Сказать вслух."""
    начать()
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame"))
    session.clear()
    await feed(dp, bot, callback("info:skip"))

    assert поле() == {"text": "", "photos": []}, "пропущенное поле записано"
    assert any(t("info.photo_dropped", "ru", count=1) in text for text in session.texts), (
        "бот пропустил вопрос вместе с кадром и промолчал"
    )


# --- кадр доезжает до отчёта --------------------------------------------------


async def test_ссылка_кадра_поля_попадает_в_карту_кадров(domain_env: Path, tmp_path: Path) -> None:
    """Без карты движок прочитал бы идентификатор телеграма путём и не нашёл файла.

    Это и есть то место, из-за которого «кадр доехал до команды» ещё не значит
    «кадр доехал до отчёта»: на месте ненайденного кадра движок печатает
    красную отметку «фотография не приложена».
    """
    начать(с_кадром=False)
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame", caption="Витрина собрана как надо"))

    файл = tmp_path / "кадр.jpg"
    файл.write_bytes(b"jpeg")
    план = resolve_photos(CHAT_ID, check_environment(), lambda ref: файл)

    assert план.mapping.get("info-frame") == str(файл.resolve()), (
        f"кадр поля мимо карты кадров: {план.mapping}"
    )
    assert план.misses == [], план.misses


async def test_потерянный_кадр_поля_назван_полем_а_не_записью(
    domain_env: Path, tmp_path: Path
) -> None:
    """Промах кадра поля не должен выдавать себя за промах записи №N."""
    начать(с_кадром=False)
    bot, _session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame", caption="Витрина собрана как надо"))

    план = resolve_photos(CHAT_ID, check_environment(), lambda ref: None)

    assert план.misses == [(ПЕРВОЕ, "info-frame")], план.misses
    # И то же самое словами, которые прочитает аудитор: «запись №INF01» отправила
    # бы его искать запись с таким номером, а её не существует.
    сказано = misses_text(план.misses)
    assert f"поле {ПЕРВОЕ}" in сказано, сказано
    assert "запись" not in сказано, сказано


async def test_кадр_поля_доезжает_до_pdf(domain_env: Path, tmp_path: Path) -> None:
    """Главное утверждение задачи: партнёр видит снимок на странице отчёта.

    У записи кадра нарочно нет — тогда единственная картинка в собранном
    документе может прийти только из информационного поля.
    """
    начать(с_кадром=False)
    session = _ФотоСессия(jpeg(tmp_path))
    bot = Bot(token=FAKE_TOKEN, session=session)
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame", caption="Витрина собрана как надо"))
    await feed(dp, bot, callback("info:done"))

    assert len(session.documents) == 1, "отчёт не собрался"
    pdf = Path(str(session.documents[0].document.path))
    assert есть_картинка_в_pdf(pdf), "в отчёте нет ни одной картинки — кадр поля не доехал"


async def test_кадр_поля_не_числится_кадром_без_записи(domain_env: Path) -> None:
    """Кадр приложен к полю — значит, он не «остался без записи» и не потерян."""
    начать(с_кадром=False)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await дойти_до_информационной_части(dp, bot)
    await feed(dp, bot, photo_message("info-frame", caption="Витрина собрана как надо"))
    session.clear()
    await feed(dp, bot, text_message("/records"))

    assert not any("Кадры без записи" in text for text in session.texts), (
        f"кадр поля показан как потерянный: {session.texts}"
    )
