"""T233: кадры снятой проверки убираются из хранилища (D089).

Проверяется не «функция отработала без исключения», а то самое место — что
объекта в хранилище ПОСЛЕ снятия действительно нет, а в базе про это записано.
Хранилище настоящее в том смысле, в каком оно настоящее у выгрузки: `moto`
подменяет S3 внутри процесса, включая обращения на выставленный `endpoint_url`,
то есть проверяется тот же путь, каким продукт пойдёт к своему хранилищу.

Строка кадра при этом остаётся, и путь в ней остаётся тоже: затёртый путь
превратил бы кадр в «ещё не выгруженный», и выгрузка залила бы его обратно.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import requires_db
from db_harness import set_retraction_env

psycopg = pytest.importorskip("psycopg")
boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from src.db.errors import RetractionError, StorageError  # noqa: E402
from src.db.photos import upload_photos  # noqa: E402
from src.db.push import push_inspection  # noqa: E402
from src.db.retract import retract_inspection  # noqa: E402
from src.db.storage import S3PhotoStorage, StorageSettings  # noqa: E402
from src.domain import add_finding, attach_photo, start_inspection  # noqa: E402

pytestmark = requires_db

ТОЧКА = "Белград-1"
КОРЗИНА = "inspection-frames"
АДРЕС_ХРАНИЛИЩА = "http://storage.local.test:8246"
ПРИЧИНА = "правил ошибку в шапке"


@pytest.fixture
def retraction_env(db_env: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Подключение администратора истории рядом с подключением приложения.

    Однострочная обёртка над помощником из `db_harness` — почему не общая
    фикстура, написано там же.
    """
    return set_retraction_env(db_env, monkeypatch)


КАДРЫ = {
    "tg-file-101": b"\xff\xd8\xff\xe0 frame one",
    "tg-file-102": b"\xff\xd8\xff\xe0 frame two",
}


def _кадр(file_id: str) -> bytes | None:
    return КАДРЫ.get(file_id)


class ОтказавшийСклад:
    """Хранилище, которое не убирает. Считает попытки: уборка обязана быть повторяемой."""

    def __init__(self) -> None:
        self.попыток = 0

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise StorageError(f"хранилище недоступно, объект {key} не принят")

    def delete(self, key: str) -> None:
        self.попыток += 1
        raise StorageError(f"хранилище недоступно, объект {key} не убран")


@pytest.fixture
def настоящий_s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[S3PhotoStorage, Any]]:
    """Драйвер поверх S3, подменённого `moto`, плюс отдельный клиент для чтения.

    Отдельный клиент не педантизм: заглядывать в хранилище через внутренности
    проверяемого объекта — значит проверять его представление о себе, а не то,
    что там на самом деле лежит.
    """
    monkeypatch.setenv("MOTO_S3_CUSTOM_ENDPOINTS", АДРЕС_ХРАНИЛИЩА)
    with moto.mock_aws():
        settings = StorageSettings(
            bucket=КОРЗИНА,
            access_key_id="ключ-теста",
            secret_access_key="секрет-теста",  # выдуманный, не настоящий
            endpoint_url=АДРЕС_ХРАНИЛИЩА,
        )
        client = boto3.client(
            "s3",
            endpoint_url=АДРЕС_ХРАНИЛИЩА,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            region_name=settings.region,
        )
        client.create_bucket(Bucket=КОРЗИНА)
        yield S3PhotoStorage(settings), client


def _проверка_с_кадрами(chat_id: int, *, кадры: tuple[str, ...]) -> str:
    start_inspection(chat_id, unit=ТОЧКА, kind="planned", report_lang="ru")
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    for file_id in кадры:
        attach_photo(chat_id, 1, file_id)
    return push_inspection(chat_id)


def _строки(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _ключи_в_хранилище(клиент: Any) -> set[str]:
    ответ = клиент.list_objects_v2(Bucket=КОРЗИНА)
    return {объект["Key"] for объект in ответ.get("Contents", [])}


def test_кадры_снятой_проверки_убраны_из_хранилища_а_строки_остались(
    domain_env: Path, retraction_env: str, настоящий_s3: tuple[S3PhotoStorage, Any]
) -> None:
    """Объектов в хранилище нет, строки кадров на месте, путь в них записан."""
    склад, читатель = настоящий_s3
    ident = _проверка_с_кадрами(501, кадры=tuple(КАДРЫ))
    assert upload_photos(ident, fetch=_кадр, storage=склад) == 2
    assert len(_ключи_в_хранилище(читатель)) == 2

    снятие = retract_inspection(ident, tenant="default", reason=ПРИЧИНА, storage=склад)

    assert снятие.photos_purged == 2
    assert _ключи_в_хранилище(читатель) == set(), "объекты снятой проверки остались в хранилище"
    кадры = _строки(
        retraction_env,
        "select storage_path, purged_at from photos where inspection_id = %s",
        (ident,),
    )
    assert len(кадры) == 2, "строки кадров пропали — уборка удалила больше, чем объекты"
    assert all(путь and путь.startswith("s3://") for путь, _ in кадры), (
        "путь затёрт: такой кадр выглядит невыгруженным, и выгрузка зальёт его обратно"
    )
    assert all(убран is not None for _, убран in кадры), "отметки об уборке нет"


def test_уборка_повторяема_и_второй_раз_ничего_не_убирает(
    domain_env: Path, retraction_env: str, настоящий_s3: tuple[S3PhotoStorage, Any]
) -> None:
    """Повторное снятие доделывает уборку, а не платит за уже убранное дважды."""
    склад, _ = настоящий_s3
    ident = _проверка_с_кадрами(502, кадры=("tg-file-101",))
    upload_photos(ident, fetch=_кадр, storage=склад)
    первое = retract_inspection(ident, tenant="default", reason=ПРИЧИНА, storage=склад)

    второе = retract_inspection(ident, tenant="default", reason=ПРИЧИНА, storage=склад)

    assert первое.photos_purged == 1
    assert второе.photos_purged == 0


def test_невыгруженный_кадр_убирать_нечего_и_отметки_он_не_получает(
    domain_env: Path, retraction_env: str, настоящий_s3: tuple[S3PhotoStorage, Any]
) -> None:
    """«Убран» на кадре, которого в хранилище не было, — записанная неправда."""
    склад, _ = настоящий_s3
    ident = _проверка_с_кадрами(503, кадры=("tg-file-101",))

    снятие = retract_inspection(ident, tenant="default", reason=ПРИЧИНА, storage=склад)

    assert снятие.photos_purged == 0
    кадры = _строки(
        retraction_env,
        "select storage_path, purged_at from photos where inspection_id = %s",
        (ident,),
    )
    assert кадры == [(None, None)]


def test_отказ_хранилища_не_прячется_а_пометка_уже_записана(
    domain_env: Path, retraction_env: str, настоящий_s3: tuple[S3PhotoStorage, Any]
) -> None:
    """Уборка, отчитавшаяся успехом с половиной кадров, оставила бы объекты навсегда.

    И встречное свойство: снятие от отказа хранилища не откатывается. Документ
    отозван у партнёра в тот момент, когда поставлена пометка; откатить её
    из-за недоступного хранилища значило бы вернуть отозванный отчёт в историю.
    """
    склад, читатель = настоящий_s3
    ident = _проверка_с_кадрами(504, кадры=("tg-file-101",))
    upload_photos(ident, fetch=_кадр, storage=склад)
    отказавший = ОтказавшийСклад()

    with pytest.raises(RetractionError) as отказ:
        retract_inspection(ident, tenant="default", reason=ПРИЧИНА, storage=отказавший)

    assert "хранилищ" in str(отказ.value).lower()
    assert отказавший.попыток == 1
    снята = _строки(retraction_env, "select retracted_at from inspections where id = %s", (ident,))
    assert снята[0][0] is not None, "пометку откатили из-за хранилища"
    # А теперь тем же вызовом, но с работающим хранилищем, — уборка доделывается.
    доделано = retract_inspection(ident, tenant="default", reason=ПРИЧИНА, storage=склад)
    assert доделано.photos_purged == 1
    assert _ключи_в_хранилище(читатель) == set()


def test_ссылка_не_того_вида_останавливает_уборку_и_называет_кадр(
    domain_env: Path, pg_dsn: str, retraction_env: str, настоящий_s3: tuple[S3PhotoStorage, Any]
) -> None:
    """Убирать по догадке нельзя: пересчитанный ключ найдёт либо ничего, либо чужое.

    Подменённая ссылка ставится прямым запросом под привилегированной ролью —
    подготовка данных, а не проверка: продуктовым путём такая ссылка в базу не
    попадает, а разобрать её однажды придётся (историю за прошлые годы зальют
    программно, D035).
    """
    склад, _ = настоящий_s3
    ident = _проверка_с_кадрами(505, кадры=("tg-file-101",))
    upload_photos(ident, fetch=_кадр, storage=склад)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "update photos set storage_path = 'какая-то строка' where inspection_id = %s",
            (ident,),
        )
        conn.commit()

    with pytest.raises(RetractionError) as отказ:
        retract_inspection(ident, tenant="default", reason=ПРИЧИНА, storage=склад)

    assert "s3://корзина/ключ" in str(отказ.value)


def test_кадры_чужой_проверки_уборка_не_трогает(
    domain_env: Path, retraction_env: str, настоящий_s3: tuple[S3PhotoStorage, Any]
) -> None:
    """Без этого «убрано два» было бы зелено и на коде, убирающем всё подряд."""
    склад, читатель = настоящий_s3
    снимаемая = _проверка_с_кадрами(506, кадры=("tg-file-101",))
    upload_photos(снимаемая, fetch=_кадр, storage=склад)
    соседняя = _проверка_с_кадрами(507, кадры=("tg-file-102",))
    upload_photos(соседняя, fetch=_кадр, storage=склад)

    retract_inspection(снимаемая, tenant="default", reason=ПРИЧИНА, storage=склад)

    оставшиеся = _ключи_в_хранилище(читатель)
    assert len(оставшиеся) == 1
    assert all(соседняя in ключ for ключ in оставшиеся)
