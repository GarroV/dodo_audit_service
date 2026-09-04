"""T093: слив завершённой проверки в базу.

Сценарий строится так же, как настоящая проверка: `start_inspection` +
`add_finding` через официальный контракт `domain` (`docs/forge/blocks/domain.md`),
а не подложенный вручную JSON, — чтобы тест ловил расхождение с реальным
поведением домена, а не только с тем, что блок `db` о нём себе вообразил.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import requires_db

# `psycopg` — зависимость блока `db`, а не всего проекта: без этой строки сбор
# этого файла падает целиком в окружении, где её ещё не поставили (см.
# аналогичный приём и объяснение в `tests/conftest.py`, раздел про `db`).
psycopg = pytest.importorskip("psycopg")

from src.db.errors import PushError  # noqa: E402 — после importorskip намеренно
from src.db.push import push_inspection  # noqa: E402
from src.db.queries import list_inspections  # noqa: E402
from src.domain import add_finding, start_inspection  # noqa: E402
from src.domain import score as domain_score  # noqa: E402
from src.domain.config import check_environment  # noqa: E402
from src.domain.engine import state_file  # noqa: E402

pytestmark = requires_db


def _начать(chat_id: int, unit: str = "Белград-1") -> None:
    start_inspection(chat_id, unit=unit, kind="planned", report_lang="ru")
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    add_finding(chat_id, code="CLN06", level="D1", zone="hot_kitchen", text="течь под мойкой")


def _строки(dsn: str, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_слив_записывает_проверку_находки_и_переводы(domain_env: Path, db_env: str) -> None:
    _начать(1)
    эталон = domain_score(1)  # то же, что покажет бот и напечатает отчёт

    inspection_id = push_inspection(1)

    (найдена,) = _строки(
        db_env, "select pct, grade from inspections where id = %s", (inspection_id,)
    )
    assert найдена == (эталон.pct, эталон.grade), "в базе не та оценка, что посчитал движок"

    находки = _строки(
        db_env,
        "select code, level, zone from findings where inspection_id = %s order by n",
        (inspection_id,),
    )
    assert находки == [("CLN05", "D1", "hot_kitchen"), ("CLN06", "D1", "hot_kitchen")]

    формулировки = _строки(
        db_env,
        "select field, lang, text from translations where entity_type = 'finding' "
        "and entity_id in (select id from findings where inspection_id = %s) "
        "order by field",
        (inspection_id,),
    )
    assert ("text", "ru", "нагар на печи") in формулировки


def test_повторный_вызов_не_создаёт_дубль(domain_env: Path, db_env: str) -> None:
    _начать(2)

    первый_id = push_inspection(2)
    второй_id = push_inspection(2)

    assert первый_id == второй_id
    (число,) = _строки(db_env, "select count(*) from inspections where id = %s", (первый_id,))
    assert число == (1,)
    (сколько_находок,) = _строки(
        db_env, "select count(*) from findings where inspection_id = %s", (первый_id,)
    )
    assert сколько_находок == (2,), "повторный слив продублировал находки"


def test_две_проверки_одной_точки_используют_одну_запись_units(
    domain_env: Path, db_env: str
) -> None:
    _начать(3, unit="Белград-2")
    _начать(4, unit="  белград-2  ")  # тот же ввод с лишними пробелами и другим регистром

    push_inspection(3)
    push_inspection(4)

    (число_точек,) = _строки(
        db_env, "select count(*) from units where name_normalized = %s", ("белград-2",)
    )
    assert число_точек == (1,), "две проверки одной точки завели два разных id точки"


def test_нет_проверки_в_чате_это_явный_отказ(domain_env: Path, db_env: str) -> None:
    with pytest.raises(PushError, match="нет"):
        push_inspection(999)


def test_недоступная_база_не_роняет_вызов_молча(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Испорченный вход — заведомо мёртвый адрес базы, а не выдуманное поведение."""
    _начать(5)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nouser@127.0.0.1:1/nodb?connect_timeout=2")

    with pytest.raises(PushError, match="чата 5"):
        push_inspection(5)


def test_испорченная_дата_в_состоянии_это_явный_отказ(domain_env: Path, db_env: str) -> None:
    """Испорченный вход подтверждён прямым чтением файла после порчи."""
    _начать(6)
    settings = check_environment()
    path = state_file(6, settings)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["meta"]["date"] = "вчера"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["meta"]["date"] == "вчера", (
        "порча даты не применилась — тест ничего не проверяет"
    )

    with pytest.raises(PushError, match=r"[Дд]ата"):
        push_inspection(6)


def test_list_inspections_фильтрует_по_точке_и_отдаёт_строку_целиком(
    domain_env: Path, db_env: str
) -> None:
    _начать(7, unit="Белград-1")
    _начать(8, unit="Ниш-1")
    id7 = push_inspection(7)
    push_inspection(8)

    # Арендатор обязателен с T110: чьи проверки читаем — обязана сказать
    # выборка, а не подразумевать.
    только_белград = list_inspections(tenant="default", unit="Белград-1")

    assert len(только_белград) == 1
    строка = только_белград[0]
    assert строка.id == id7
    assert строка.chat_id == 7
    assert строка.unit_name == "Белград-1"
    assert строка.findings_count == 2
    assert строка.pushed_at, "время слива не должно быть пустым"
    assert len(list_inspections(tenant="default")) >= 2


def _снять_версию(chat_id: int) -> None:
    """Убрать версию методики из состояния — так выглядят проверки, созданные
    до того, как версия стала записываться (T025)."""
    settings = check_environment()
    path = state_file(chat_id, settings)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.setdefault("domain", {})["checklist_version"] = ""
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["domain"]["checklist_version"] == "", (
        "версия не снялась — тест ничего не проверяет"
    )


def test_проверка_без_версии_методики_не_сливается_молча(domain_env: Path, db_env: str) -> None:
    """Пустая версия — отказ, а не тихая пустота в колонке.

    Отчёт заморожен на той версии, по которой посчитан (D033, D050): запись без
    версии несравнима ни с чем, а положенная молча портит аналитику незаметно.
    Так это и вскрылось на смоуке приёмки 02.09 — issue #76.
    """
    _начать(21)
    _снять_версию(21)

    with pytest.raises(PushError, match=r"версия методики"):
        push_inspection(21)


def test_старую_проверку_без_версии_можно_слить_явно(domain_env: Path, db_env: str) -> None:
    """История за прошлые годы заливается программно (D035) — но осознанно,
    отдельным флагом, а не потому что проверку никто не глядя пропустил."""
    _начать(22)
    _снять_версию(22)

    inspection_id = push_inspection(22, allow_unknown_version=True)

    (найдена,) = _строки(
        db_env, "select checklist_version from inspections where id = %s", (inspection_id,)
    )
    assert найдена[0] in ("", None), "версия должна остаться пустой, а не быть придуманной"
