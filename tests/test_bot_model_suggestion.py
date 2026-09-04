"""T181 (D077): бот сохраняет то, что предложил, рядом с тем, что зафиксировано.

Сигнал о промахе рождается в этом блоке и нигде больше: кандидат у бота в руках
ровно в тот момент, когда аудитор жмёт кнопку. Не передай он предложение при
фиксации — и его больше негде взять: `pending` живёт только в памяти и
перезапуска не переживает намеренно.

Владелец, дословно: «при несостыковках, или если пользователь добавит что-то в
духе "ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ЧИСТОТА" то мы долполняем наш список
терминов». Боевой список бот не пополняет и пополнять не будет — D077 запрещает
прямо. Его дело — сохранить сигнал, а не применить его.

**Предложением считается ПЕРВЫЙ ответ модели, а не выбранная кнопка.** Иначе
сигнала не будет никогда: что аудитор нажал, то и записалось, расхождение
всегда пустое. Аудитор, выбравший второго кандидата, — это и есть «модель
предложила одно, человек поправил на другое».

**Ручной перечень предложения не даёт.** Он показывается ровно тогда, когда
модель не ответила ничего (недоступна или пустой список), и сравнивать там не с
чем. Пусто в базе означает «не предлагала» — это не то же самое, что «попала».
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    build_report,
    candidate,
    feed,
    make_bot,
    manual,
    photo_message,
    stub_classify,
    stub_manual,
    suggestion,
)
from bot_harness import callback_query as callback
from conftest import requires_data, requires_db

from src import db
from src.bot import sidecar
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.keyboards import EDIT_PREFIX
from src.domain import Finding, get_state, start_inspection
from src.recognize.errors import ModelUnavailable
from src.recognize.models import UNKNOWN_ZONE

pytestmark = [pytest.mark.asyncio, requires_data]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Однозначная фраза синтетической карты: строка «Печь» произнесена целиком,
#: колонка выбрана словом «грязная» → CLN05, единственный класс D1.
CLEAR = "печь грязная"


def начата() -> None:
    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-09-04", auditor="Гарро")


def записи() -> list[Finding]:
    state = get_state(CHAT_ID)
    assert state is not None, "проверки нет — смотреть не на что"
    return state.findings


# --- разбор моделью: предложение доезжает до записи --------------------------


async def test_подтверждённая_запись_помнит_предложение_модели(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Аудитор согласился с моделью — предложение всё равно сохранено.

    Попадание модели нужно так же, как промах: без него доля промахов считается
    от неизвестно чего, а порог отбора ставить не по чему.
    """
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen", confidence=0.77)))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="нагар на подине печи"))
    await feed(dp, bot, callback("rec:pick:0"))

    (запись,) = записи()
    assert (запись.suggested_code, запись.suggested_level, запись.suggested_zone) == (
        "CLN05",
        "D1",
        "hot_kitchen",
    )
    assert запись.suggested_confidence == pytest.approx(0.77)


async def test_выбранный_второй_кандидат_оставляет_предложением_первого(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главный случай задачи: аудитор поправил пункт, и это видно.

    Записать предложением нажатую кнопку значило бы стереть сигнал ровно там,
    где он появляется: предложенная и итоговая тройки совпали бы всегда.
    """
    начата()
    stub_classify(
        monkeypatch,
        suggestion(
            candidate("CLN02", "D1", "hot_kitchen", confidence=0.6),
            candidate("CLN05", "D1", "hot_kitchen", confidence=0.3),
        ),
    )
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="нагар на подине печи"))
    await feed(dp, bot, callback("rec:pick:1"))

    (запись,) = записи()
    assert запись.code == "CLN05", "записался не тот пункт, который выбрал аудитор"
    assert запись.suggested_code == "CLN02", "предложением записали нажатую кнопку"
    assert запись.suggested_confidence == pytest.approx(0.6), "уверенность взята не у предложения"


async def test_зона_кнопкой_не_подменяет_ответ_модели(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модель не назвала места — так и записано, а зону выбрал человек.

    `UNKNOWN` — это ОТВЕТ модели («из слов места не видно»), а не отсутствие
    ответа, и подменять его выбранной зоной значит спрятать её отказ.
    """
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", UNKNOWN_ZONE, confidence=0.5)))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="нагар"))
    await feed(dp, bot, callback("rec:pick:0"))
    await feed(dp, bot, callback("rec:zp:hot_kitchen"))

    (запись,) = записи()
    assert запись.zone == "hot_kitchen"
    assert запись.suggested_zone == UNKNOWN_ZONE


async def test_молчание_модели_о_зоне_записано_её_ответом_а_не_пустотой(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модель вернула пустую зону — это тот же ответ «места не видно».

    Пустая строка уехала бы в базу как `NULL`, то есть как «модель не
    предлагала ничего», рядом с названным ею пунктом: строка на три четверти
    пустая, и в выборке для управляющей компании она выглядела бы поломкой, а
    не отказом модели назвать место.
    """
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "", confidence=0.5)))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="нагар"))
    await feed(dp, bot, callback("rec:pick:0"))
    await feed(dp, bot, callback("rec:zp:hot_kitchen"))

    (запись,) = записи()
    assert запись.zone == "hot_kitchen"
    assert запись.suggested_zone == UNKNOWN_ZONE, "молчание модели о зоне записано пустотой"


# --- фиксация словами: предложение делает сверка, уверенности у неё нет ------


async def test_запись_по_словам_помнит_пункт_сверки_без_уверенности(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Быстрый путь — тот самый список терминов, ради которого всё и затевалось.

    Уверенности он не считает: строгий критерий из пяти условий либо сходится,
    либо нет. Ноль вместо неё был бы ложью — «ни в чём не уверена» осмысленно.
    """
    начата()
    вызовы = stub_classify(monkeypatch, suggestion())
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))

    assert not вызовы, "модель звали — быстрый путь не сработал, случай не тот"
    (запись,) = записи()
    assert запись.suggested_code == запись.code
    assert запись.suggested_zone == запись.zone
    assert запись.suggested_confidence is None


async def test_правка_зоны_после_записи_по_словам_видна_расхождением(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ровно пример владельца: система назвала одно, аудитор поправил на другое.

    Правка меняет запись и НЕ трогает предложение — только поэтому расхождение и
    заметно. Это единственный способ увидеть промах быстрого пути: подтверждения
    у него нет (D064), и промах иначе остался бы тихим.
    """
    начата()
    stub_classify(monkeypatch, suggestion())
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)
    sidecar.remember_zone(CHAT_ID, "hot_kitchen")

    await feed(dp, bot, photo_message("frame-1", caption=CLEAR))
    (до,) = записи()
    assert до.zone == "hot_kitchen", "случай не тот: зона и так не горячий цех"

    await feed(dp, bot, callback(f"{EDIT_PREFIX}1:zone"))
    await feed(dp, bot, callback("ez:1:dining"))

    (после,) = записи()
    assert после.zone == "dining", "правка зоны не применилась"
    assert после.suggested_zone == "hot_kitchen", "правка переписала предложение системы"


# --- ручной перечень: предложения не было, и врать об этом нельзя ------------


async def test_ручной_выбор_без_модели_не_придумывает_предложения(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модель недоступна — сравнивать не с чем, и пусто честнее выдумки.

    Записать предложением выбранный человеком пункт значило бы утопить настоящие
    промахи в выборке: ручных записей на порядок больше.
    """
    начата()
    stub_classify(monkeypatch, ModelUnavailable("модель недоступна"))
    stub_manual(monkeypatch, (manual("CLN05", ("D1",)),))
    bot, _ = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="что-то не то у печи"))
    await feed(dp, bot, callback("rec:zm:hot_kitchen"))
    await feed(dp, bot, callback("rec:mi:0"))

    (запись,) = записи()
    assert запись.code == "CLN05"
    assert запись.suggested_code == "", "ручной выбор записан как предложение модели"
    assert запись.suggested_confidence is None


# --- сквозной случай: обе тройки доезжают до живой базы ----------------------


@requires_db
async def test_поправленное_предложение_лежит_в_базе_обеими_тройками(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главная проверка задачи, и она обязана идти до самой базы.

    Разговор целиком через настоящий диспетчер: модель предложила `CLN02` в
    горячем цехе, аудитор выбрал другой пункт кнопкой и следом сменил зону.
    В базе после слива обязаны лежать ОБЕ тройки — предложенная и итоговая, — а
    «что аудитор поправил» считается их сравнением и отдельно не хранится
    нигде (D077, T181).

    Тест на состоянии проверки этого не показал бы: между файлом и базой стоит
    слив, и до T181 он клал во все четыре колонки честный `NULL`.
    """
    начата()
    stub_classify(
        monkeypatch,
        suggestion(
            candidate("CLN02", "D1", "hot_kitchen", confidence=0.51),
            candidate("CLN05", "D1", "hot_kitchen", confidence=0.31),
        ),
    )
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="нагар на подине печи"))
    await feed(dp, bot, callback("rec:pick:1"))
    await feed(dp, bot, callback(f"{EDIT_PREFIX}1:zone"))
    await feed(dp, bot, callback("ez:1:dining"))
    await build_report(dp, bot)

    assert session.documents, "отчёт не отдан — слива могло и не быть"
    (строка,) = db.findings_by_unit(tenant="default", unit="Белград 2")

    assert (строка.code, строка.zone) == ("CLN05", "dining"), "в базу легла не итоговая тройка"
    assert (строка.suggested_code, строка.suggested_level, строка.suggested_zone) == (
        "CLN02",
        "D1",
        "hot_kitchen",
    ), "предложение модели до базы не доехало"
    assert строка.suggested_confidence == pytest.approx(0.51)
    assert set(строка.corrections()) == {"code", "zone"}, (
        "база не видит правки аудитора сравнением двух троек одной строки"
    )
