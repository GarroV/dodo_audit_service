"""Что делает блок, когда базы нет на связи, и что торчит из него наружу.

Первая половина — про Definition of Done блока: «падение базы не роняет
проверку на точке». Проверяется не «когда-нибудь упадёт», а конкретное: каждая
дверь блока при мёртвой строке подключения отдаёт `PushError` — один тип на
весь блок, чтобы вызывающему хватило одного `except` и проверка на точке шла
своим чередом. Отказ, прилетевший наружу исключением драйвера, этот `except`
не поймает.

Вторая половина — про сам контракт: функции блока обязаны доставаться из
`src.db` по именам, записанным в `docs/forge/blocks/db.md`. Внутри пакета они
подгружаются лениво (PEP 562), и опечатка в карте ленивой загрузки не видна
ничем, кроме такой проверки: модуль импортируется, тесты блока ходят в
подмодули напрямую, и всё выглядит рабочим до первого вызова из бота.

Настоящий Postgres здесь не нужен: адрес заведомо мёртвый.
"""

from __future__ import annotations

import pytest

# `psycopg` — зависимость блока `db`, а не всего проекта.
psycopg = pytest.importorskip("psycopg")

import src.db as db  # noqa: E402 — после importorskip намеренно
from src.db.directory import list_units, resolve_unit, upsert_unit  # noqa: E402
from src.db.errors import PushError  # noqa: E402
from src.db.photos import upload_photos  # noqa: E402

#: Порт 1 на петле: подключения там нет и быть не может, ждать нечего.
МЁРТВЫЙ_DSN = "postgresql://nobody@127.0.0.1:1/nowhere"


class ЗаписнойСклад:
    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise AssertionError("до хранилища дойти не должно: базы нет на связи")


def test_справочник_при_мёртвой_базе_отдаёт_отказ_блока(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", МЁРТВЫЙ_DSN)
    with pytest.raises(PushError):
        resolve_unit("Белград 2")
    with pytest.raises(PushError):
        list_units()
    with pytest.raises(PushError):
        upsert_unit("Белград 2", aliases=("БГ2",))


def test_выгрузка_кадров_при_мёртвой_базе_отдаёт_отказ_блока(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """И не трогает хранилище: платить за выгрузку, которую некуда записать, незачем."""
    monkeypatch.setenv("DATABASE_URL", МЁРТВЫЙ_DSN)
    with pytest.raises(PushError):
        upload_photos(
            "00000000-0000-0000-0000-000000000000",
            fetch=lambda _: b"frame bytes",
            storage=ЗаписнойСклад(),
        )


def test_пустое_название_ищется_без_похода_в_базу(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустая строка — это `None` на месте, а не отказ связи: искать нечего.

    Проверка держится именно на мёртвом адресе: дойди запрос до базы, вместо
    `None` прилетел бы `PushError`.
    """
    monkeypatch.setenv("DATABASE_URL", МЁРТВЫЙ_DSN)
    assert resolve_unit("   ") is None


# --- публичная поверхность блока ---------------------------------------------


@pytest.mark.parametrize(
    "имя",
    [
        "push_inspection",
        "list_inspections",
        "upload_photos",
        "upsert_unit",
        "resolve_unit",
        "list_units",
        "Unit",
        "InspectionRow",
        "DbError",
        "ConfigError",
        "PushError",
        "StorageError",
    ],
)
def test_контракт_блока_достаётся_из_пакета(имя: str) -> None:
    """Опечатка в карте ленивой загрузки иначе всплыла бы только у вызывающего."""
    assert getattr(db, имя) is not None
    assert имя in db.__all__, f"{имя} достаётся, но не объявлен в __all__ — контракт разъехался"


def test_несуществующее_имя_остаётся_ошибкой_а_не_молчаливым_none() -> None:
    with pytest.raises(AttributeError):
        db.такого_нет  # noqa: B018 — обращение и есть проверяемое действие
