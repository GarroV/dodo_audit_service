"""T094: выгрузка кадров проверки в хранилище.

`telegram_file_id` живёт ровно столько, сколько живёт бот с этим токеном, и
снаружи не открывается вовсе: в базе он становится мёртвой ссылкой на
доказательство, ради которого проверка и делалась. Поэтому при завершении
кадры уезжают в S3-совместимое хранилище (D054), а в строке кадра появляется
ссылка, которая работает независимо от телеграма.

Байты сюда приносит вызывающий, а не берёт этот модуль сам, и это не мелочь
устройства: токен телеграма есть только у бота (`src/bot/photos.py`), а
границы модулей запрещают блоку `db` знать про блок `bot` — импорт наверх
роняет прогон. Тот же приём уже применён в сборке отчёта: `report.build_pdf`
принимает готовую карту «ссылка → файл», а не угадывает её сам.

Пропавший кадр не проходит молча. Выгрузка, вернувшая «успех» с половиной
кадров, оставила бы в базе часть ссылок мёртвыми навсегда и никому об этом не
сказала — а именно от этого задача и заводилась.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg

from .config import check_environment, load_storage_settings
from .errors import PushError, StorageError
from .storage import PHOTO_CONTENT_TYPE, PhotoStorage, S3PhotoStorage, object_key

#: Кто умеет достать байты кадра по его идентификатору в телеграме. `None` —
#: кадра больше нет; это не исключение, а обычный исход (файл протух, телеграм
#: не отдал), и решение о нём принимается на уровне выше.
FetchPhoto = Callable[[str], bytes | None]

_SELECT_PENDING_SQL = """
select id, telegram_file_id
from photos
where inspection_id = %s and storage_path is null
order by created_at, id
"""

_MARK_UPLOADED_SQL = """
update photos set storage_path = %s, uploaded_at = now() where id = %s
"""

_INSPECTION_EXISTS_SQL = "select 1 from inspections where id = %s"


def _require_inspection(conn: psycopg.Connection[Any], inspection_id: str) -> None:
    """Проверки нет — это отказ, а не «выгружено ноль кадров».

    Ноль здесь неотличим от честного нуля у проверки без единого кадра, и
    вызывающий записал бы себе успех там, где ошибся идентификатором.
    """
    with conn.cursor() as cur:
        cur.execute(_INSPECTION_EXISTS_SQL, (inspection_id,))
        if cur.fetchone() is None:
            raise PushError(
                f"В базе нет проверки {inspection_id} — выгружать кадры некуда и незачем"
            )


def upload_photos(
    inspection_id: str,
    *,
    fetch: FetchPhoto,
    storage: PhotoStorage | None = None,
    allow_missing: bool = False,
) -> int:
    """Выгрузить кадры проверки в хранилище и вернуть, сколько выгружено.

    Повторяемо и доливаемо: берутся только те кадры, у которых ссылки в
    хранилище ещё нет (`storage_path is null`), и каждая ссылка фиксируется
    своей транзакцией. Поэтому обрыв на середине не откатывает уже выгруженное,
    а повторный вызов доделывает остаток и не платит за то же дважды.

    `fetch` возвращает байты кадра по его идентификатору в телеграме или
    `None`, если кадра больше нет. Хотя бы один такой кадр — отказ `PushError`
    с перечислением потерянных: часть ссылок иначе осталась бы мёртвой, и никто
    бы об этом не узнал. Залить остальное намеренно — `allow_missing=True`,
    и тогда это осознанное решение вызывающего, а не случайность.

    `storage` подменяется только проверками; в работе драйвер собирается из
    окружения (`S3_*`), и площадка хранилища живёт там же, где площадка базы —
    в переменных, а не в коде.
    """
    settings = check_environment()
    store = storage if storage is not None else S3PhotoStorage(load_storage_settings())

    uploaded = 0
    missing: list[str] = []
    try:
        with psycopg.connect(settings.dsn) as conn:
            _require_inspection(conn, inspection_id)
            with conn.cursor() as cur:
                cur.execute(_SELECT_PENDING_SQL, (inspection_id,))
                pending = cur.fetchall()

            for photo_id, file_id in pending:
                data = fetch(file_id)
                if data is None:
                    missing.append(str(file_id))
                    continue
                key = object_key(inspection_id, str(photo_id))
                uri = store.put(key, data, content_type=PHOTO_CONTENT_TYPE)
                with conn.cursor() as cur:
                    cur.execute(_MARK_UPLOADED_SQL, (uri, photo_id))
                conn.commit()
                uploaded += 1
    except PushError:
        raise
    except psycopg.Error as exc:
        raise PushError(
            f"Выгрузка кадров проверки {inspection_id} не удалась "
            f"({type(exc).__name__}): {exc} Уже выгруженные кадры остались "
            f"выгруженными — повторный вызов доделает остаток"
        ) from exc
    except StorageError as exc:
        # Наружу у блока один тип отказа записи (`PushError`), иначе вызывающему
        # пришлось бы знать про хранилище то, что знать он не должен. Ловится
        # именно `StorageError`, а не `Exception`: широкий перехват подменял бы
        # собой и ошибку в коде вызывающего — настоящая поломка выглядела бы
        # отказом хранилища и молча уходила в «повторим позже».
        raise PushError(
            f"Хранилище не приняло кадр проверки {inspection_id} "
            f"({type(exc).__name__}): {exc} Выгруженные до отказа кадры остались "
            f"выгруженными — повторный вызов доделает остаток"
        ) from exc

    if missing and not allow_missing:
        raise PushError(
            f"Кадры проверки {inspection_id} не удалось получить из телеграма: "
            f"{', '.join(missing)}. Выгружено {uploaded}, остальные ссылки в базе "
            f"остались мёртвыми. Залить проверку без них намеренно — "
            f"upload_photos(..., allow_missing=True)"
        )
    return uploaded
