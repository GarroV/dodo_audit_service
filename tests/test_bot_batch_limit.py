"""Разбирается не больше пяти кадров пачки, про остальные бот говорит (T232).

Решения владельца D091 и D093. Пачка без комментария разбирается по кадру
(T206), то есть стоит столько вызовов модели, сколько кадров: предел назван,
чтобы одно нажатие «Разобрать?» не превращалось в десяток вызовов. Второе
решение выбрало, как предел выглядит для человека: разбираются пять, остальные
аудитор присылает отдельной пачкой — пачка больше пяти признана исключением, а
не рабочим случаем, и прятать её «во внутреннюю обработку порциями» не нужно.

Что защищает этот файл — четыре вещи.

**Разбирается ровно предел.** Вызовов модели столько же, сколько разобранных
кадров, и ни одним больше.

**Про лишние кадры сказано словами.** Молча отброшенные, они выглядят
записанными: аудитор уходит с точки, считая фотофиксацию сделанной, а в отчёт
партнёру не попадает ничего. Это и есть та потеря, которой решение D093
посвящено.

**Лишние кадры не пропадают.** Они остаются в списке присланного (T068) и
возвращаются аудитору при завершении проверки.

**Пачка по предел включительно ничего не теряет.** Предел не должен срабатывать
там, где терять нечего: пять кадров разбираются все пять, и лишнего сообщения
нет.
"""

from __future__ import annotations

from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    Calls,
    candidate,
    feed,
    make_bot,
    photo_message,
    suggestion,
)
from bot_harness import callback_query as callback

from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import ANALYZE_PREFIX
from src.bot.routers.record import BATCH_LIMIT
from src.bot.texts import t
from src.domain import get_state, start_inspection
from src.recognize.models import Suggestion

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Кадр, которым тест закрывает альбом: кадр чужой группы закрывает предыдущий
#: альбом немедленно. Тем же приёмом закрывает альбом `test_bot_batch_frames`.
CLOSING_FRAME = "closing-frame"


def started() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru")
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")


def stub_calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Подменить разбор так, чтобы был виден КАЖДЫЙ вызов: их число и есть предмет."""
    calls = Calls()

    def fake(note: str, photo: object = None, zone_hint: object = None, **kw: Any) -> Suggestion:
        calls.append((note, photo, zone_hint))
        return suggestion(candidate("CLN05", "D1", "hot_kitchen", "кадр"))

    monkeypatch.setattr("src.bot.routers.record.classify", fake)
    return calls


async def разобрать_пачку(dp: Any, bot: Any, *file_ids: str) -> None:
    """Прислать альбом, закрыть его событием и нажать «Разобрать?»."""
    first = photo_message(file_ids[0], media_group_id="album")
    await feed(dp, bot, first)
    for file_id in file_ids[1:]:
        await feed(dp, bot, photo_message(file_id, media_group_id="album"))
    await feed(dp, bot, photo_message(CLOSING_FRAME))
    await feed(dp, bot, callback(f"{ANALYZE_PREFIX}{first.message_id}"))


def dispatcher() -> Any:
    """Окно альбома длинное: закрывать альбом обязано событие, а не таймер."""
    return build_dispatcher(SETTINGS, album_window=5.0)


ПАЧКА_СЕМЬ = tuple(f"frame-{i}" for i in range(1, 8))


async def test_из_пачки_больше_предела_разбирается_ровно_предел(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Одно нажатие не превращается в десяток вызовов модели (D091)."""
    started()
    зовы = stub_calls(monkeypatch)
    bot, _ = make_bot()

    await разобрать_пачку(dispatcher(), bot, *ПАЧКА_СЕМЬ)

    assert len(зовы) == BATCH_LIMIT, f"разобрано {len(зовы)} кадров вместо {BATCH_LIMIT}"


async def test_про_неразобранные_кадры_бот_говорит_словами(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Молча отброшенный кадр аудитор считает записанным (D093)."""
    started()
    stub_calls(monkeypatch)
    bot, session = make_bot()

    await разобрать_пачку(dispatcher(), bot, *ПАЧКА_СЕМЬ)

    сказано = t(
        "record.batch_over_limit",
        "ru",
        count=len(ПАЧКА_СЕМЬ),
        limit=BATCH_LIMIT,
        rest=len(ПАЧКА_СЕМЬ) - BATCH_LIMIT,
    )
    assert сказано in session.texts, (
        f"бот не сказал про кадры сверх предела; сказано было: {session.texts!r}"
    )
    assert t("record.batch", "ru", count=len(ПАЧКА_СЕМЬ)) not in session.texts, (
        "бот объявил разбор всей пачки, а разобрал только часть"
    )


async def test_номера_кадров_в_отбивке_считаются_из_разобранных(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Кадр 6 из 7» под пятью сообщениями — обещание, которого бот не выполнил."""
    started()
    stub_calls(monkeypatch)
    bot, session = make_bot()

    await разобрать_пачку(dispatcher(), bot, *ПАЧКА_СЕМЬ)

    шапки = [
        t("record.candidates_batch", "ru", no=no, total=BATCH_LIMIT, lines="")[:20]
        for no in range(1, BATCH_LIMIT + 1)
    ]
    for шапка in шапки:
        assert any(текст.startswith(шапка) for текст in session.texts), (
            f"нет сообщения отбивки с шапкой {шапка!r}"
        )


async def test_кадры_сверх_предела_остаются_в_списке_присланного(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не разобрали — не значит потеряли: кадр вернётся при завершении (T068)."""
    started()
    stub_calls(monkeypatch)
    bot, _ = make_bot()

    await разобрать_пачку(dispatcher(), bot, *ПАЧКА_СЕМЬ)

    известные = {frame.file_id for frame in sidecar.read(CHAT_ID).frames}
    assert set(ПАЧКА_СЕМЬ) <= известные, "кадры сверх предела исчезли из списка присланного"
    без_записи = {frame.file_id for frame in sidecar.unclaimed(CHAT_ID, used=set())}
    assert set(ПАЧКА_СЕМЬ[BATCH_LIMIT:]) <= без_записи, (
        "кадры сверх предела не попадут в список кадров без записи"
    )


async def test_пачка_по_предел_включительно_разбирается_целиком(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Предел не срабатывает там, где терять нечего."""
    started()
    зовы = stub_calls(monkeypatch)
    bot, session = make_bot()

    await разобрать_пачку(dispatcher(), bot, *ПАЧКА_СЕМЬ[:BATCH_LIMIT])

    assert len(зовы) == BATCH_LIMIT
    assert t("record.batch", "ru", count=BATCH_LIMIT) in session.texts
    assert all(
        t("record.batch_over_limit", "ru", count=BATCH_LIMIT, limit=BATCH_LIMIT, rest=0)
        not in текст
        for текст in session.texts
    ), "бот пожаловался на предел там, где ничего не отложено"


async def test_записей_после_предела_не_больше_чем_разобрано(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нажатий столько же, сколько разборов: неразобранный кадр записать нечем."""
    started()
    stub_calls(monkeypatch)
    bot, _ = make_bot()

    await разобрать_пачку(dispatcher(), bot, *ПАЧКА_СЕМЬ)

    state = get_state(CHAT_ID)
    assert state is not None
    assert list(state.findings) == [], "запись появилась без подтверждения аудитором (D046)"


async def test_на_английской_проверке_предел_объясняется_по_английски(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Язык — параметр и здесь."""
    start_inspection(CHAT_ID, "Белград 2", "planned", "en", ui_lang="en")
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")
    stub_calls(monkeypatch)
    bot, session = make_bot()

    await разобрать_пачку(dispatcher(), bot, *ПАЧКА_СЕМЬ)

    ожидалось = t(
        "record.batch_over_limit",
        "en",
        count=len(ПАЧКА_СЕМЬ),
        limit=BATCH_LIMIT,
        rest=len(ПАЧКА_СЕМЬ) - BATCH_LIMIT,
    )
    assert ожидалось in session.texts
