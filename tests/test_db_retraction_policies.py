"""T210, T233: снятая проверка видна и правима только администратору — и держит это база.

Соседний файл (`test_db_retraction.py`) проверяет операцию снятия продуктовыми
вызовами. Здесь всё идёт сырым SQL под обеими непривилегированными ролями,
потому что тема ровно одна: разграничение обязано держаться построчными
политиками, а не тем, что продуктовый код не пишет лишних запросов. Проверка в
коде снимается вместе с кодом; политика — нет.

Отказ RLS **молчалив**: строка просто не видна для записи, и запрос
отчитывается «затронуто 0 строк», а не падает. Поэтому каждая проверка смотрит
и на число затронутых строк, и на содержимое после попытки: «не упало» здесь
не значит ничего.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import APP_ROLE, requires_db
from db_harness import ADMIN_ROLE, admin_role_dsn

psycopg = pytest.importorskip("psycopg")

from src.db.push import push_inspection  # noqa: E402
from src.domain import add_finding, attach_photo, start_inspection  # noqa: E402

pytestmark = requires_db

ПРИЧИНА = "правил ошибку в шапке"


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


def _счёт(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> int:
    строка = _строка(dsn, sql, params)
    assert строка is not None
    return int(строка[0])


def _проверка(chat_id: int, *, точка: str) -> str:
    """Проверка продуктовым путём: она и есть документ, ушедший партнёру."""
    start_inspection(chat_id, unit=точка, kind="planned", report_lang="ru")
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    attach_photo(chat_id, 1, "tg-file-901")
    return push_inspection(chat_id)


def _выгрузить_кадры(pg_dsn: str, inspection_id: str) -> None:
    """Проставить кадрам ссылку в хранилище — ПОДГОТОВКА данных, а не проверка.

    Идёт под привилегированной ролью намеренно: суперпользователь обходит
    политики всегда, и для подготовки это законно ровно потому, что ни одно
    утверждение теста на этом подключении не делается.
    """
    _выполнить(
        pg_dsn,
        "update photos set storage_path = 's3://корзина/объект', uploaded_at = now() "
        "where inspection_id = %s",
        (inspection_id,),
    )


def _снять(admin_dsn: str, inspection_id: str) -> int:
    return _выполнить(
        admin_dsn,
        "update inspections set retracted_at = now(), retraction_reason = %s "
        "where id = %s and status = 'finalized' and retracted_at is null",
        (ПРИЧИНА, inspection_id),
    )


# --- роли, без которых всё остальное бессмысленно -----------------------------


def test_обе_роли_заведены_и_ни_одна_не_обходит_политики(db_env: str, pg_dsn: str) -> None:
    """Если эта проверка красная — все остальные в файле зелены по неверной причине.

    Суперпользователь обходит RLS ВСЕГДА. Разграничение видимости, проверенное
    под такой ролью, зелено независимо от того, есть оно вообще или нет.
    """
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select rolname, rolsuper, rolbypassrls from pg_roles where rolname = any(%s)",
            ([APP_ROLE, ADMIN_ROLE],),
        )
        роли = {строка[0]: (строка[1], строка[2]) for строка in cur.fetchall()}

    assert set(роли) == {APP_ROLE, ADMIN_ROLE}, f"заведены не обе роли: {sorted(роли)}"
    for имя, (суперпользователь, обход) in роли.items():
        assert суперпользователь is False, f"роль {имя} — суперпользователь, она обходит RLS"
        assert обход is False, f"у роли {имя} bypassrls: политики на ней не держат ничего"
    assert _строка(db_env, "select current_user") == (APP_ROLE,)
    assert _строка(admin_role_dsn(db_env), "select current_user") == (ADMIN_ROLE,)


# --- кто снимает --------------------------------------------------------------


def test_приложение_снять_проверку_не_может(domain_env: Path, db_env: str) -> None:
    """Снятие — операция управляющей компании, а бот ходит под ролью приложения."""
    ident = _проверка(601, точка="Белград-1")

    затронуто = _выполнить(
        db_env,
        "update inspections set retracted_at = now(), retraction_reason = %s where id = %s",
        ("самовольно", ident),
    )

    assert затронуто == 0
    состояние = _строка(
        admin_role_dsn(db_env),
        "select retracted_at, retraction_reason from inspections where id = %s",
        (ident,),
    )
    assert состояние == (None, None), "проверка всё-таки снята ролью приложения"


def test_администратор_переписать_оценку_не_может(domain_env: Path, db_env: str) -> None:
    """Право администратора — пометка, а не правка документа.

    Ограничивает его привилегия на КОЛОНКИ, а не выражение политики: `with
    check` не видит старой строки, и «менять разрешено только пометку» в нём не
    выражается вовсе. Поэтому проверяются обе попытки — и голая правка оценки,
    и правка, приклеенная к законному снятию одним запросом.
    """
    ident = _проверка(602, точка="Белград-1")
    admin = admin_role_dsn(db_env)

    for запрос, параметры in (
        ("update inspections set pct = 100 where id = %s", (ident,)),
        (
            "update inspections set pct = 100, retracted_at = now(), "
            "retraction_reason = %s where id = %s",
            (ПРИЧИНА, ident),
        ),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _выполнить(admin, запрос, параметры)

    оценка = _строка(admin, "select pct, retracted_at from inspections where id = %s", (ident,))
    assert оценка is not None
    assert float(оценка[0]) != 100.0, "оценку переписали"
    assert оценка[1] is None


def test_администратор_снятую_обратно_не_возвращает(domain_env: Path, db_env: str) -> None:
    """Снятие односторонне, как и печать: `using` не пускает правку снятой строки."""
    ident = _проверка(603, точка="Белград-1")
    admin = admin_role_dsn(db_env)
    assert _снять(admin, ident) == 1

    затронуто = _выполнить(
        admin,
        "update inspections set retracted_at = null, retraction_reason = null where id = %s",
        (ident,),
    )

    assert затронуто == 0
    строка = _строка(admin, "select retracted_at from inspections where id = %s", (ident,))
    assert строка is not None and строка[0] is not None, "снятую проверку вернули в историю"


def test_администратор_строк_не_удаляет(domain_env: Path, db_env: str) -> None:
    """Снятие — пометка. Права удалять у администратора нет вовсе, и это видно в привилегиях."""
    ident = _проверка(604, точка="Белград-1")
    admin = admin_role_dsn(db_env)
    _снять(admin, ident)

    for запрос in (
        "delete from inspections where id = %s",
        "delete from photos where inspection_id = %s",
        "delete from findings where inspection_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _выполнить(admin, запрос, (ident,))

    assert _счёт(admin, "select count(*) from inspections where id = %s", (ident,)) == 1


# --- что видно --------------------------------------------------------------


def test_снятой_проверки_приложению_не_видно_а_администратору_видно(
    domain_env: Path, db_env: str
) -> None:
    """Ровно то, ради чего заведена вторая роль (D089)."""
    снятая = _проверка(605, точка="Белград-1")
    живая = _проверка(606, точка="Белград-2")
    admin = admin_role_dsn(db_env)
    _снять(admin, снятая)

    assert _счёт(db_env, "select count(*) from inspections where id = %s", (снятая,)) == 0
    assert _счёт(db_env, "select count(*) from inspections where id = %s", (живая,)) == 1
    assert _счёт(admin, "select count(*) from inspections where id = %s", (снятая,)) == 1


def test_тела_снятой_проверки_приложению_тоже_не_видно(domain_env: Path, db_env: str) -> None:
    """Заслон стоит на шапке, но тело обязано уходить вместе с ней.

    Иначе находки снятой проверки продолжали бы отвечать на вопрос «что у этой
    точки повторяется» — то есть отозванный документ так и работал бы
    требованием к партнёру, только без шапки.
    """
    ident = _проверка(607, точка="Белград-1")
    admin = admin_role_dsn(db_env)
    _снять(admin, ident)

    for таблица, условие in (
        ("findings", "inspection_id = %s"),
        ("photos", "inspection_id = %s"),
        ("inspection_info", "inspection_id = %s"),
    ):
        # Запрос собирается из СВОИХ строк, перечисленных здесь же, а не из
        # ввода: динамической сборки текста SQL из чужого здесь нет.
        запрос = f"select count(*) from {таблица} where {условие}"  # noqa: S608
        assert _счёт(db_env, запрос, (ident,)) == 0, f"{таблица} снятой проверки видны"
        assert _счёт(admin, запрос, (ident,)) >= 0

    assert _счёт(admin, "select count(*) from findings where inspection_id = %s", (ident,)) == 1
    формулировок = (
        "select count(*) from translations t join findings f on f.id = t.entity_id "
        "where t.entity_type = 'finding' and f.inspection_id = %s"
    )
    assert _счёт(db_env, формулировок, (ident,)) == 0, "формулировки снятой проверки видны"
    assert _счёт(admin, формулировок, (ident,)) >= 1


def test_живой_проверки_новые_правила_не_касаются(domain_env: Path, db_env: str) -> None:
    """Встречное утверждение: заслон закрылся не на всём подряд, и `0004` не ослаб."""
    ident = _проверка(608, точка="Белград-1")

    assert _счёт(db_env, "select count(*) from inspections where id = %s", (ident,)) == 1
    assert _счёт(db_env, "select count(*) from findings where inspection_id = %s", (ident,)) == 1
    assert _счёт(db_env, "select count(*) from photos where inspection_id = %s", (ident,)) == 1
    # Заморозка завершённой проверки (миграция 0004) на месте.
    assert _выполнить(db_env, "update inspections set pct = 100 where id = %s", (ident,)) == 0
    оценка = _строка(db_env, "select pct from inspections where id = %s", (ident,))
    assert оценка is not None and float(оценка[0]) != 100.0


# --- что нельзя написать ------------------------------------------------------


def test_тело_снятой_проверки_приложению_не_переписать(domain_env: Path, db_env: str) -> None:
    """ЗАСАДА, ради которой заведены политики «шапка обязана быть видна».

    Прежние заслоны написаны как `not exists (... status = 'finalized')`: у
    невидимой шапки такой запрет становится ИСТИННЫМ, то есть открывается. Без
    новых политик тело снятой проверки стало бы правимым, притом молча.
    """
    ident = _проверка(609, точка="Белград-1")
    admin = admin_role_dsn(db_env)
    _снять(admin, ident)

    assert (
        _выполнить(
            db_env, "update findings set code = 'ПОДМЕНА' where inspection_id = %s", (ident,)
        )
        == 0
    )
    assert _выполнить(db_env, "delete from findings where inspection_id = %s", (ident,)) == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as отказ_вставки:
        # Дописать находку в снятую проверку — то же «переписать», с другой
        # стороны. Здесь отказ не молчалив: на вставке построчная политика
        # падает, а не «не видит строки», — и текст называет ту самую политику,
        # ради которой всё затевалось.
        _выполнить(
            db_env,
            "insert into findings (inspection_id, n, code, level, zone) "
            "values (%s, 99, 'ДОПИСКА', 'D3', 'zone')",
            (ident,),
        )

    assert "findings_follow_visible_inspection" in str(отказ_вставки.value)

    состояние = _строка(
        admin,
        "select count(*), min(code) from findings where inspection_id = %s",
        (ident,),
    )
    assert состояние == (1, "CLN05"), "тело снятой проверки изменилось"


def test_кадр_снятой_проверки_убирает_администратор_и_только_отметкой(
    domain_env: Path, db_env: str, pg_dsn: str
) -> None:
    """Уборка кадров — часть снятия, то есть работа управляющей компании.

    И она ограничена той же привилегией на колонки: подменить ссылку кадра
    задним числом администратор не может, а отметить убранный — может.
    """
    ident = _проверка(610, точка="Белград-1")
    admin = admin_role_dsn(db_env)
    _выгрузить_кадры(pg_dsn, ident)
    _снять(admin, ident)

    assert (
        _выполнить(
            db_env,
            "update photos set purged_at = now() where inspection_id = %s",
            (ident,),
        )
        == 0
    ), "кадр снятой проверки убрала роль приложения"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _выполнить(
            admin,
            "update photos set storage_path = 'подмена' where inspection_id = %s",
            (ident,),
        )

    assert (
        _выполнить(admin, "update photos set purged_at = now() where inspection_id = %s", (ident,))
        == 1
    )
    кадр = _строка(
        admin, "select storage_path, purged_at from photos where inspection_id = %s", (ident,)
    )
    assert кадр is not None
    assert кадр[0] == "s3://корзина/объект", "ссылка кадра подменена"
    assert кадр[1] is not None


def test_кадр_живой_проверки_замораживается_как_прежде(
    domain_env: Path, db_env: str, pg_dsn: str
) -> None:
    """Расширение `photos_uploaded_only_once` — исключение, а не снятие заслона."""
    ident = _проверка(611, точка="Белград-1")
    _выгрузить_кадры(pg_dsn, ident)

    затронуто = _выполнить(
        db_env,
        "update photos set storage_path = 's3://чужая/подмена' where inspection_id = %s",
        (ident,),
    )

    assert затронуто == 0
    кадр = _строка(db_env, "select storage_path from photos where inspection_id = %s", (ident,))
    assert кадр is not None and кадр[0] == "s3://корзина/объект"
