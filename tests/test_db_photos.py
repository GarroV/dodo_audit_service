"""T094: кадры проверки уезжают в хранилище, а в базе остаётся живая ссылка.

`telegram_file_id` снаружи не открывается и живёт ровно столько, сколько живёт
бот с этим токеном: в базе он мёртвая ссылка на доказательство. Поэтому
проверяется не «функция отработала без исключения», а то самое место — что в
хранилище действительно лежат ТЕ ЖЕ байты по ТОМУ ЖЕ ключу, что записан в
строке кадра.

Хранилище в прогоне поднимать нечем и не нужно: `moto` подменяет S3 внутри
процесса, включая обращения на выставленный `endpoint_url` (переменная
`MOTO_S3_CUSTOM_ENDPOINTS`), — то есть проверяется в том числе путь «адрес
задан явно», которым продукт и пойдёт к своему хранилищу. Настоящий
S3-совместимый сервер поднимается профилем `storage` в docker-compose и нужен
для ручного смоука, а не для прогона.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import requires_db

psycopg = pytest.importorskip("psycopg")
boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from src.db.config import load_storage_settings  # noqa: E402
from src.db.errors import ConfigError, PushError, StorageError  # noqa: E402
from src.db.photos import upload_photos  # noqa: E402
from src.db.push import push_inspection  # noqa: E402
from src.db.storage import S3PhotoStorage, StorageSettings, object_key  # noqa: E402
from src.domain import add_finding, attach_photo, start_inspection  # noqa: E402

pytestmark = requires_db

ТОЧКА = "Белград-1"
КОРЗИНА = "inspection-frames"
АДРЕС_ХРАНИЛИЩА = "http://storage.local.test:8244"

# Байты разные у каждого кадра намеренно: одинаковая заглушка не отличила бы
# «положили тот кадр» от «положили хоть что-то».
КАДРЫ = {
    "tg-file-001": b"\xff\xd8\xff\xe0 frame one",
    "tg-file-002": b"\xff\xd8\xff\xe0 frame two",
}


def _кадр(file_id: str) -> bytes | None:
    return КАДРЫ.get(file_id)


def _проверка_с_кадрами(chat_id: int, *, кадры: tuple[str, ...]) -> str:
    """Настоящая проверка через контракт `domain`, затем слив в базу."""
    start_inspection(chat_id, unit=ТОЧКА, kind="planned", report_lang="ru")
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    for file_id in кадры:
        attach_photo(chat_id, 1, file_id)
    return push_inspection(chat_id)


def _строки(dsn: str, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


class ЗаписнойСклад:
    """Хранилище-двойник: помнит, что и сколько раз клали."""

    def __init__(self) -> None:
        self.положено: dict[str, bytes] = {}
        self.вызовов = 0

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self.вызовов += 1
        self.положено[key] = data
        return f"s3://{КОРЗИНА}/{key}"


class ОтказавшийСклад:
    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise StorageError(f"хранилище недоступно, объект {key} не принят")


@pytest.fixture
def настоящий_s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[S3PhotoStorage, object]]:
    """Драйвер поверх S3, подменённого `moto`, с явно заданным адресом.

    Отдаётся парой с отдельным клиентом для чтения: заглядывать в хранилище
    через внутренности проверяемого объекта — значит проверять его же
    представление о себе, а не то, что там на самом деле лежит.
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


# --- кадр действительно попадает в хранилище ---------------------------------


def test_кадры_попадают_в_хранилище_а_в_базу_ложится_ссылка_на_объект(
    domain_env: Path, db_env: str, настоящий_s3: tuple[S3PhotoStorage, object]
) -> None:
    склад, читатель = настоящий_s3
    inspection_id = _проверка_с_кадрами(11, кадры=tuple(КАДРЫ))

    выгружено = upload_photos(inspection_id, fetch=_кадр, storage=склад)

    assert выгружено == len(КАДРЫ)
    строки = _строки(
        db_env,
        "select id, telegram_file_id, storage_path, uploaded_at from photos "
        "where inspection_id = %s order by created_at, id",
        (inspection_id,),
    )
    assert len(строки) == len(КАДРЫ)
    for photo_id, file_id, storage_path, uploaded_at in строки:
        ключ = object_key(inspection_id, str(photo_id))
        assert storage_path == f"s3://{КОРЗИНА}/{ключ}", "в базе не ссылка на объект хранилища"
        assert uploaded_at is not None, "кадр помечен выгруженным без отметки времени"
        # То самое место: в хранилище лежат ИМЕННО эти байты, а не «что-то есть».
        лежит = читатель.get_object(Bucket=КОРЗИНА, Key=ключ)["Body"].read()  # type: ignore[attr-defined]
        assert лежит == КАДРЫ[str(file_id)], f"по ключу {ключ} лежат не байты этого кадра"
        assert str(file_id) in КАДРЫ, "идентификатор телеграма затёрт — история потеряна"


def test_ключ_объекта_собран_из_идентификаторов_а_не_из_названия_точки(
    domain_env: Path, db_env: str
) -> None:
    """Названия точек партнёров коммерчески чувствительны, в имени файла им не место."""
    склад = ЗаписнойСклад()
    inspection_id = _проверка_с_кадрами(12, кадры=("tg-file-001",))

    upload_photos(inspection_id, fetch=_кадр, storage=склад)

    (ключ,) = склад.положено
    assert inspection_id in ключ
    assert ТОЧКА.casefold() not in ключ.casefold()
    assert "нагар" not in ключ


# --- повторяемость -----------------------------------------------------------


def test_повторный_вызов_не_выгружает_те_же_кадры_второй_раз(domain_env: Path, db_env: str) -> None:
    склад = ЗаписнойСклад()
    inspection_id = _проверка_с_кадрами(13, кадры=tuple(КАДРЫ))

    первый = upload_photos(inspection_id, fetch=_кадр, storage=склад)
    второй = upload_photos(inspection_id, fetch=_кадр, storage=склад)

    assert (первый, второй) == (len(КАДРЫ), 0)
    assert склад.вызовов == len(КАДРЫ), "кадры уехали в хранилище повторно — платим дважды"


# --- отказы не проходят молча ------------------------------------------------


def test_потерянный_кадр_не_проходит_молча(domain_env: Path, db_env: str) -> None:
    """Иначе половина ссылок осталась бы мёртвой навсегда, и никто бы не узнал."""
    склад = ЗаписнойСклад()
    inspection_id = _проверка_с_кадрами(14, кадры=("tg-file-001", "tg-потерян"))

    with pytest.raises(PushError, match="tg-потерян"):
        upload_photos(inspection_id, fetch=_кадр, storage=склад)

    выгруженные = _строки(
        db_env,
        "select telegram_file_id from photos where inspection_id = %s and storage_path is not null",
        (inspection_id,),
    )
    assert выгруженные == [("tg-file-001",)], "успевшие выгрузиться кадры откатились"


def test_явное_разрешение_допускает_пропуск_потерянного(domain_env: Path, db_env: str) -> None:
    склад = ЗаписнойСклад()
    inspection_id = _проверка_с_кадрами(15, кадры=("tg-file-001", "tg-потерян"))

    выгружено = upload_photos(inspection_id, fetch=_кадр, storage=склад, allow_missing=True)

    assert выгружено == 1
    (пусто,) = _строки(
        db_env,
        "select count(*) from photos where inspection_id = %s and storage_path is null",
        (inspection_id,),
    )
    assert пусто == (1,)


def test_отказ_хранилища_не_оставляет_в_базе_ссылку_на_несуществующий_объект(
    domain_env: Path, db_env: str
) -> None:
    inspection_id = _проверка_с_кадрами(16, кадры=("tg-file-001",))

    with pytest.raises(PushError, match="хранилище недоступно"):
        upload_photos(inspection_id, fetch=_кадр, storage=ОтказавшийСклад())

    (без_ссылки,) = _строки(
        db_env,
        "select count(*) from photos where inspection_id = %s and storage_path is null",
        (inspection_id,),
    )
    assert без_ссылки == (1,), "ссылка проставлена, хотя объект в хранилище не лёг"


def test_несуществующая_проверка_это_отказ_а_не_ноль_выгруженных(db_env: str) -> None:
    """Ноль неотличим от честного нуля у проверки без кадров — и ошибка проходит."""
    with pytest.raises(PushError, match="нет проверки"):
        upload_photos("00000000-0000-0000-0000-000000000000", fetch=_кадр, storage=ЗаписнойСклад())


# --- доступ к хранилищу живёт в окружении ------------------------------------


def test_без_доступа_к_хранилищу_отказ_с_перечислением_чего_не_хватает() -> None:
    with pytest.raises(ConfigError, match="S3_BUCKET"):
        load_storage_settings({"S3_ACCESS_KEY_ID": "k", "S3_SECRET_ACCESS_KEY": "s"})


def test_адрес_хранилища_из_окружения_доезжает_до_клиента() -> None:
    """Смена хранилища — правка переменной; проверяется фактический адрес клиента."""
    settings = load_storage_settings(
        {
            "S3_BUCKET": КОРЗИНА,
            "S3_ACCESS_KEY_ID": "k",
            "S3_SECRET_ACCESS_KEY": "s",
            "S3_ENDPOINT_URL": АДРЕС_ХРАНИЛИЩА,
        }
    )
    assert S3PhotoStorage(settings).endpoint_url == АДРЕС_ХРАНИЛИЩА


def test_пустой_адрес_означает_настоящий_aws_а_не_подставленный_по_умолчанию() -> None:
    settings = load_storage_settings(
        {"S3_BUCKET": КОРЗИНА, "S3_ACCESS_KEY_ID": "k", "S3_SECRET_ACCESS_KEY": "s"}
    )
    assert settings.endpoint_url is None
    assert "amazonaws.com" in S3PhotoStorage(settings).endpoint_url


def test_отказ_поставщика_превращается_в_отказ_блока_на_границе_драйвера(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Перевод исключений делает драйвер, а не тот, кто его зовёт.

    Иначе вызывающему пришлось бы ловить исключения boto3 — то есть знать про
    конкретного поставщика, и смена хранилища перестала бы быть правкой
    конфига. Корзины нет намеренно: это самый дешёвый настоящий отказ S3.
    """
    monkeypatch.setenv("MOTO_S3_CUSTOM_ENDPOINTS", АДРЕС_ХРАНИЛИЩА)
    with moto.mock_aws():
        склад = S3PhotoStorage(
            StorageSettings(
                bucket="no-such-bucket",
                access_key_id="ключ-теста",
                secret_access_key="секрет-теста",  # выдуманный, не настоящий
                endpoint_url=АДРЕС_ХРАНИЛИЩА,
            )
        )
        with pytest.raises(StorageError, match="no-such-bucket"):
            склад.put("inspections/x/y.jpg", b"frame", content_type="image/jpeg")
