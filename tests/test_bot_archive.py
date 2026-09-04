"""T123: завершённая проверка доезжает до базы (задача #98).

Проверяется здесь ровно то, чего до задачи не было: **слив зовёт сам бот**.
Тест, который вызывает `push_inspection` своими руками, этой задачи не
проверяет вовсе — он зелен и на боте, который в базу не ходит. Поэтому
сквозной случай идёт через настоящий диспетчер: запись делается нажатием,
отчёт — кнопкой, а в базу тест только смотрит.

Вторая половина файла — про то, чем слив не имеет права стать. Отчёт аудитор
уже получил, он стоит на точке, и упавшая база не отменяет ни PDF, ни письмо.
Но и промолчать нельзя: не сказав, что история не сохранена, бот оставил бы
человека уверенным в обратном. А ненастроенная база — это не сбой, а законная
конфигурация (работа без базы, D027), и выглядеть сбоем она не должна.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    Calls,
    RecordingSession,
    candidate,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    suggestion,
)
from bot_harness import callback_query as callback
from conftest import requires_db

from src import db
from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import score, start_inspection

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


def начата() -> None:
    start_inspection(
        CHAT_ID,
        "Белград 2",
        "Плановая",
        "ru",
        date="2026-08-21",
        auditor="Владимир Гарро",
    )


async def проверка_через_бота(monkeypatch: pytest.MonkeyPatch) -> RecordingSession:
    """Разговор целиком: кадр с комментарием → запись нажатием → отчёт.

    Возвращает сессию телеграма, чтобы тест смотрел на то, что увидел аудитор.
    Ни одного вызова блока `db` отсюда — их обязан сделать сам бот.
    """
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, photo_message("frame-1", caption="нагар на подине печи"))
    await feed(dp, bot, callback("rec:pick:0"))
    await feed(dp, bot, callback("fin:build"))
    return session


def подменить_слив(monkeypatch: pytest.MonkeyPatch, ответ: object) -> Calls:
    """Подменить `push_inspection` там, где его берёт бот."""
    вызовы = Calls()

    def fake(chat_id: int) -> str:
        вызовы.append((chat_id,))
        if isinstance(ответ, Exception):
            raise ответ
        return str(ответ)

    monkeypatch.setattr(db, "push_inspection", fake, raising=False)
    return вызовы


def подменить_выгрузку(monkeypatch: pytest.MonkeyPatch, ответ: object = 0) -> Calls:
    """Подменить `upload_photos`, спросив у карты кадры прямо в вызове.

    Спросить надо именно здесь: настоящая выгрузка читает кадры, пока папка
    сборки жива, и тест, отложивший чтение на потом, увидел бы пустоту не по
    вине продукта.
    """
    вызовы = Calls()

    def fake(inspection_id: str, *, fetch: Any, allow_missing: bool = False) -> int:
        вызовы.append((inspection_id, fetch("frame-1"), fetch("кадра-такого-нет"), allow_missing))
        if isinstance(ответ, Exception):
            raise ответ
        return int(ответ)

    monkeypatch.setattr(db, "upload_photos", fake, raising=False)
    return вызовы


def отправленные_документы(session: RecordingSession) -> list[Any]:
    return session.documents


# --- сквозной случай: проверка через бота оказалась в базе -------------------


@requires_db
async def test_проведённая_ботом_проверка_лежит_в_базе_с_той_же_оценкой(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главный тест задачи: бот сам сливает завершённую проверку.

    Снять вызов из `routers/finish.py` — и строк в базе не станет. Оценка
    сверяется с той, что показал движок: в базу обязана лечь она, а не
    посчитанная по дороге.
    """
    session = await проверка_через_бота(monkeypatch)
    assert отправленные_документы(session), "аудитору не отдали отчёт — слив проверять не на чем"

    эталон = score(CHAT_ID)
    строки = db.list_inspections(tenant="default")

    assert len(строки) == 1, "завершённая проверка не доехала до базы"
    (проверка,) = строки
    assert (проверка.pct, проверка.grade) == (эталон.pct, эталон.grade)
    assert проверка.unit_name == "Белград 2"
    assert проверка.findings_count == 1


@requires_db
async def test_повторная_сборка_отчёта_не_плодит_вторую_проверку(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Аудитор пересобрал отчёт — в базе по-прежнему одна проверка."""
    await проверка_через_бота(monkeypatch)

    bot, session = make_bot()
    await feed(build_dispatcher(SETTINGS), bot, callback("fin:build"))

    assert отправленные_документы(session), "второй отчёт не собрался — сравнивать нечего"
    assert len(db.list_inspections(tenant="default")) == 1


# --- слив зовётся после отчёта и тянет за собой кадры ------------------------


async def test_кадры_уходят_в_хранилище_следом_за_проверкой(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`upload_photos` зовётся с id из слива и картой кадров, уже скачанной ботом.

    Карта — та же, по которой собирался PDF: качать кадры второй раз ради
    хранилища значило бы платить телеграму дважды за один и тот же кадр.
    """
    подменить_слив(monkeypatch, "insp-1")
    выгрузки = подменить_выгрузку(monkeypatch, 1)

    await проверка_через_бота(monkeypatch)

    assert len(выгрузки) == 1, "кадры завершённой проверки в хранилище не поехали"
    inspection_id, кадр, чужой, _ = выгрузки[0]
    assert inspection_id == "insp-1", "кадры уехали не к той проверке, что легла в базу"
    assert кадр is not None, "по карте кадров не достаются байты"
    assert чужой is None, "карта отдала байты кадра, которого в ней нет"


async def test_слив_идёт_после_того_как_отчёт_отдан(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порядок: сначала документ аудитору, потом база.

    Иначе упавшая или медленная база задержала бы отчёт человека, который
    стоит на точке и ждёт его.
    """
    начата()
    stub_classify(monkeypatch, suggestion(candidate("CLN05", "D1", "hot_kitchen")))
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    подменить_выгрузку(monkeypatch)
    видел: list[str] = []

    def fake(chat_id: int) -> str:
        видел.extend(type(c).__name__ for c in session.calls)
        return "insp-1"

    monkeypatch.setattr(db, "push_inspection", fake, raising=False)

    await feed(dp, bot, photo_message("frame-1", caption="нагар на подине печи"))
    await feed(dp, bot, callback("rec:pick:0"))
    await feed(dp, bot, callback("fin:build"))

    assert "SendDocument" in видел, "слив пошёл раньше, чем аудитор получил отчёт"


# --- отказ базы не отменяет отчёт, но и не молчит ---------------------------


async def test_отказ_слива_не_отменяет_отчёт_и_называется_вслух(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """База упала — PDF всё равно у аудитора, но он знает, что истории нет."""
    подменить_слив(monkeypatch, db.PushError("база не на связи"))
    выгрузки = подменить_выгрузку(monkeypatch)

    session = await проверка_через_бота(monkeypatch)

    assert отправленные_документы(session), "из-за базы аудитор остался без отчёта"
    assert session.last_text == t("finish.not_archived", "ru")
    assert not выгрузки, "кадры поехали в хранилище проверки, которой в базе нет"


async def test_отказ_хранилища_называется_отдельно_от_отказа_базы(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверка в базе есть, кадров нет — это другой исход, и звучит он иначе."""
    подменить_слив(monkeypatch, "insp-1")
    подменить_выгрузку(monkeypatch, db.StorageError("хранилище не приняло кадр"))

    session = await проверка_через_бота(monkeypatch)

    assert отправленные_документы(session)
    assert session.last_text == t("finish.photos_not_archived", "ru")


async def test_ненастроенная_база_не_выглядит_сбоем(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DATABASE_URL` не задан — это работа без базы, а не поломка.

    Ни одного слова про несохранённую историю: аудитор ничего не терял, а
    пугать его сообщением о том, чего у продукта в этой конфигурации нет,
    значит приучать не читать сообщения.
    """
    session = await проверка_через_бота(monkeypatch)

    assert отправленные_документы(session)
    assert t("finish.not_archived", "ru") not in session.texts
    assert t("finish.photos_not_archived", "ru") not in session.texts
    assert "Письмо партнёру" in session.last_text
