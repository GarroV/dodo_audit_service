"""T111: завершённая проверка — документ, а не строка, которую можно поправить.

Разбор при приёмке прошлой волны показал живым прогоном, что завершённую
проверку можно переписать и удалить: `update ... → UPDATE 1`,
`delete ... → DELETE 1`. Ни триггеров, ни `REVOKE`, ни RLS в схеме не было.

Главная засада этой задачи не в политиках, а в роли. Роль, под которой шли и
миграции, и приложение, — суперпользователь Postgres, а он **обходит RLS
всегда**. Политики поверх такой роли зелены и не держат ничего. Поэтому здесь
проверяется прежде всего сама роль: тест, идущий под суперпользователем,
бесполезен целиком, и это состояние надо уметь отличить от рабочего.

Отдельно: отказ RLS на UPDATE и DELETE **молчаливый** — строка просто не видна
для записи, и запрос отчитывается «затронуто 0 строк», а не падает. Поэтому
каждая проверка смотрит и на число затронутых строк, и на содержимое: «не
упало» здесь ничего не значит.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import APP_ROLE, requires_db

psycopg = pytest.importorskip("psycopg")

from src.db.push import push_inspection  # noqa: E402
from src.domain import add_finding, attach_photo, start_inspection  # noqa: E402

pytestmark = requires_db


def _завершённая(chat_id: int = 301) -> str:
    """Проверка, слитая продуктовым путём: она и есть «документ, ушедший партнёру»."""
    start_inspection(chat_id, unit="Белград-1", kind="planned", report_lang="ru")
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    return push_inspection(chat_id)


def _выполнить(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> int:
    """Выполнить запись и вернуть число затронутых строк."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        затронуто = cur.rowcount
        conn.commit()
    return затронуто


def _строка(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# --- роль, без которой всё остальное бессмысленно ----------------------------


def test_приложение_ходит_под_ролью_которая_не_обходит_политики(db_env: str) -> None:
    """Если эта проверка красная — все остальные в файле зелены по неверной причине."""
    роль = _строка(
        db_env,
        "select current_user, rolsuper, rolbypassrls from pg_roles where rolname = current_user",
    )

    assert роль is not None
    assert роль[0] == APP_ROLE, f"приложение ходит в базу под ролью {роль[0]}, а не {APP_ROLE}"
    assert роль[1] is False, "роль приложения — суперпользователь, он обходит RLS всегда"
    assert роль[2] is False, "у роли приложения bypassrls: политики на ней не держат ничего"


def test_историю_схемы_приложению_не_отдали(db_env: str) -> None:
    """Накат — не работа приложения. Прав на служебную таблицу у него нет вовсе."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _выполнить(
            db_env,
            "insert into schema_migrations (filename, checksum) values ('поддельная', 'x')",
        )


# --- сама проверка -----------------------------------------------------------


def test_слив_запечатывает_проверку(domain_env: Path, db_env: str) -> None:
    """Черновик не переживает транзакцию слива: наружу выходит только запечатанное."""
    inspection_id = _завершённая()

    статус = _строка(db_env, "select status from inspections where id = %s", (inspection_id,))

    assert статус == ("finalized",)


def test_завершённую_проверку_нельзя_переписать(domain_env: Path, db_env: str) -> None:
    inspection_id = _завершённая()
    было = _строка(db_env, "select pct, grade from inspections where id = %s", (inspection_id,))

    затронуто = _выполнить(
        db_env,
        "update inspections set pct = 100, grade = 'A+' where id = %s",
        (inspection_id,),
    )

    assert затронуто == 0, "правка завершённой проверки прошла"
    стало = _строка(db_env, "select pct, grade from inspections where id = %s", (inspection_id,))
    assert стало == было, f"оценка в базе изменилась: было {было}, стало {стало}"


def test_завершённую_проверку_нельзя_удалить(domain_env: Path, db_env: str) -> None:
    inspection_id = _завершённая()

    затронуто = _выполнить(db_env, "delete from inspections where id = %s", (inspection_id,))

    assert затронуто == 0, "завершённая проверка удалилась"
    assert _строка(db_env, "select 1 from inspections where id = %s", (inspection_id,)) == (1,)


def test_находку_завершённой_проверки_нельзя_переписать_и_удалить(
    domain_env: Path, db_env: str
) -> None:
    """Тело документа — те же слова партнёру, что и шапка."""
    inspection_id = _завершённая()

    правка = _выполнить(
        db_env,
        "update findings set level = 'D3', code = 'XXX01' where inspection_id = %s",
        (inspection_id,),
    )
    удаление = _выполнить(db_env, "delete from findings where inspection_id = %s", (inspection_id,))

    assert правка == 0, "находка завершённой проверки переписалась"
    assert удаление == 0, "находка завершённой проверки удалилась"
    осталось = _строка(
        db_env,
        "select code, level from findings where inspection_id = %s",
        (inspection_id,),
    )
    assert осталось == ("CLN05", "D1")


def test_в_завершённую_проверку_нельзя_дописать_находку(domain_env: Path, db_env: str) -> None:
    """Дописать находку задним числом — то же «переписать», только с другой стороны.

    Отказ на вставке, в отличие от правки, громкий: `with check` не пропускает
    новую строку явной ошибкой, а не тихим нулём.
    """
    inspection_id = _завершённая()

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _выполнить(
            db_env,
            "insert into findings (inspection_id, n, code, level, zone) "
            "values (%s, 99, 'CLN09', 'D3', 'hot_kitchen')",
            (inspection_id,),
        )


def test_формулировки_завершённой_проверки_заморожены(domain_env: Path, db_env: str) -> None:
    """Текст находки — это и есть отчёт. Заморозить только числа значило бы
    оставить возможность переписать документ, не тронув ни одной цифры."""
    inspection_id = _завершённая()

    правка = _выполнить(
        db_env,
        "update translations set text = 'ничего не было' where entity_type = 'finding' "
        "and entity_id in (select id from findings where inspection_id = %s)",
        (inspection_id,),
    )
    подпись = _выполнить(
        db_env,
        "update translations set text = 'Отлично' where entity_type = 'inspection' "
        "and entity_id = %s",
        (inspection_id,),
    )

    assert правка == 0, "формулировка находки завершённой проверки переписалась"
    assert подпись == 0, "подпись оценки завершённой проверки переписалась"
    остался = _строка(
        db_env,
        "select text from translations where entity_type = 'finding' and field = 'text' "
        "and entity_id in (select id from findings where inspection_id = %s)",
        (inspection_id,),
    )
    assert остался == ("нагар на печи",)


def test_кадр_завершённой_проверки_нельзя_удалить(domain_env: Path, db_env: str) -> None:
    start_inspection(302, unit="Белград-1", kind="planned", report_lang="ru")
    add_finding(302, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    attach_photo(302, 1, "file-302")
    inspection_id = push_inspection(302)

    удаление = _выполнить(db_env, "delete from photos where inspection_id = %s", (inspection_id,))

    assert удаление == 0, "кадр завершённой проверки удалился"
    assert _строка(
        db_env, "select telegram_file_id from photos where inspection_id = %s", (inspection_id,)
    ) == ("file-302",)


def test_кадр_выгружается_один_раз_и_дальше_заморожен(domain_env: Path, db_env: str) -> None:
    """Выгрузка (T094) идёт ПОСЛЕ завершения проверки — политика обязана её пускать.

    И ровно один раз: подменить ссылку на объект задним числом нельзя, иначе
    доказательство в отчёте партнёра указывало бы на другой кадр.
    """
    start_inspection(303, unit="Белград-1", kind="planned", report_lang="ru")
    add_finding(303, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    attach_photo(303, 1, "file-303")
    inspection_id = push_inspection(303)

    выгрузка = _выполнить(
        db_env,
        "update photos set storage_path = 's3://корзина/кадр.jpg', uploaded_at = now() "
        "where inspection_id = %s",
        (inspection_id,),
    )
    подмена = _выполнить(
        db_env,
        "update photos set storage_path = 's3://чужая/другой.jpg' where inspection_id = %s",
        (inspection_id,),
    )

    assert выгрузка == 1, "политика не пустила выгрузку кадра — сломан T094"
    assert подмена == 0, "ссылку на выгруженный кадр подменили задним числом"
    assert _строка(
        db_env, "select storage_path from photos where inspection_id = %s", (inspection_id,)
    ) == ("s3://корзина/кадр.jpg",)


# --- политика различает статус, а не запрещает всё подряд --------------------

_ЧЕРНОВИК_SQL = """
insert into inspections (
    tenant_code, unit_id, chat_id, kind, inspection_date, report_lang,
    ui_lang, speech_lang, checklist_version, pct, grade, source_fingerprint, status
) values (
    'default', %s, 555, 'planned', current_date, 'ru', 'ru', 'ru', 'v1',
    90.0, 'B', 'черновик-555', 'draft'
)
returning id
"""


def test_незапечатанную_проверку_править_можно_и_запечатать_тоже(
    domain_env: Path, db_env: str
) -> None:
    """Иначе «запрет статусом» неотличим от сплошного «нельзя ничего».

    Сплошной запрет выглядел бы так же зелено и охранял бы неизвестно что: по
    нему нельзя сказать, работает ли правило вообще. Здесь видно, что политика
    различает именно статус — и что дверь односторонняя.
    """
    with psycopg.connect(db_env) as conn, conn.cursor() as cur:
        cur.execute("insert into tenants (code) values ('default') on conflict do nothing")
        cur.execute(
            "insert into units (tenant_code, name, name_normalized) "
            "values ('default', 'Черновая', 'черновая') returning id"
        )
        row = cur.fetchone()
        assert row is not None
        cur.execute(_ЧЕРНОВИК_SQL, (row[0],))
        черновик = cur.fetchone()
        assert черновик is not None
        conn.commit()
    черновик_id = черновик[0]

    правка_черновика = _выполнить(
        db_env, "update inspections set pct = 91 where id = %s", (черновик_id,)
    )
    печать = _выполнить(
        db_env, "update inspections set status = 'finalized' where id = %s", (черновик_id,)
    )
    правка_после = _выполнить(
        db_env, "update inspections set pct = 100 where id = %s", (черновик_id,)
    )
    распечатать = _выполнить(
        db_env, "update inspections set status = 'draft' where id = %s", (черновик_id,)
    )

    assert правка_черновика == 1, "незапечатанную проверку править нельзя — это не правило, а стена"
    assert печать == 1, "черновик невозможно запечатать: правило запрещает и то, ради чего оно есть"
    assert правка_после == 0, "запечатанную проверку переписали"
    assert распечатать == 0, "печать оказалась обратимой — запрет обходится сменой статуса"
