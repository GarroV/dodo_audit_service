"""T193 (#158): рецепт отпечатка сменился — уже слитая проверка не задваивается.

Источник записи ушёл из отпечатка тем же доводом, каким из него исключены
сырые слова аудитора и предложение модели (`tests/test_db_fingerprint.py`). Но
отпечаток — не просто число: это ИДЕНТИЧНОСТЬ строки, уже лежащей в базе.
Сменить рецепт прямым удалением поля значит объявить каждую слитую проверку
неизвестной: повторный слив не увидит её по новому отпечатку, вставит вторую
строку, и разобрать эти две строки обратно уже нельзя — миграция `0004`
запрещает и править, и удалять запечатанную проверку.

Поэтому смена рецепта делается совместимо: слив ищет проверку и по ПРЕЖНИМ
рецептам (`previous_fingerprints`), и только не найдя — вставляет по текущему.
Сегодня строк в базе ноль, и на этом можно было бы успокоиться, но проверить
это можно только на своей машине: базы владельца и площадки отсюда не видны.
Правило дешевле однократной проверки и работает независимо от неё.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import requires_db

psycopg = pytest.importorskip("psycopg")

from src.db.fingerprint import compute_fingerprint, previous_fingerprints  # noqa: E402
from src.db.push import push_inspection  # noqa: E402
from src.domain import SOURCE_PHOTO, add_finding, get_state, start_inspection  # noqa: E402
from src.domain import score as domain_score  # noqa: E402

pytestmark = requires_db

ЧАТ = 1930
ТОЧКА = "Белград-1"
АРЕНДАТОР = "default"


def _проверка() -> None:
    start_inspection(ЧАТ, unit=ТОЧКА, kind="planned", report_lang="ru")
    # Источник обязателен для смысла теста: рецепты различаются только им, и
    # без него прежний отпечаток совпал бы с текущим, а тест стал бы зелёным,
    # ничего не проверяя.
    add_finding(
        ЧАТ,
        code="CLN05",
        level="D1",
        zone="hot_kitchen",
        text="нагар на печи",
        source=SOURCE_PHOTO,
    )


def _отпечатки() -> tuple[str, tuple[str, ...]]:
    состояние = get_state(ЧАТ)
    assert состояние is not None, "проверка исчезла из состояния"
    assert состояние.findings[0].source == SOURCE_PHOTO, "домен не отдал источник"
    оценка = domain_score(ЧАТ)
    текущий = compute_fingerprint(состояние, оценка, tenant_code=АРЕНДАТОР)
    прежние = previous_fingerprints(состояние, оценка, tenant_code=АРЕНДАТОР)
    return текущий, прежние


def _число_проверок(dsn: str) -> int:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from inspections")
        строка = cur.fetchone()
    assert строка is not None
    return int(строка[0])


def test_прежний_рецепт_отличается_от_текущего(domain_env: Path, db_env: str) -> None:
    """Сторож смысла: рецепты обязаны разойтись, иначе тест ниже ничего не ловит.

    Красный на неисправленном коде — там источник ещё входит в отпечаток, и
    «прежний» рецепт равен текущему.
    """
    _проверка()
    текущий, прежние = _отпечатки()

    assert прежние, "прежних рецептов не объявлено — совместимость проверять нечем"
    assert текущий not in прежние, (
        "прежний отпечаток совпал с текущим: источник записи из рецепта не ушёл"
    )


def test_слитое_прежним_рецептом_не_задваивается(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверка, лежащая в базе под прежним отпечатком, находится и вторая не пишется.

    Слив прежним кодом воспроизводится подменой рецепта в самом сливе: это
    ровно то, что делал вчерашний продукт, и никакого другого способа получить
    такую строку у теста нет — рукописный INSERT проверял бы форму запроса, а
    не поведение слива.
    """
    _проверка()
    текущий, (прежний, *_) = _отпечатки()

    # Возврат — тем же `setattr`, а не `monkeypatch.undo()`: экземпляр
    # `monkeypatch` в тесте один на всех, и `undo` снял бы заодно `STATE_DIR`
    # и `DATABASE_URL`, поставленные фикстурами `domain_env` и `db_env`.
    monkeypatch.setattr("src.db.push.compute_fingerprint", lambda *a, **k: прежний)
    первый = push_inspection(ЧАТ)
    monkeypatch.setattr("src.db.push.compute_fingerprint", compute_fingerprint)

    assert _число_проверок(db_env) == 1
    with psycopg.connect(db_env) as conn, conn.cursor() as cur:
        cur.execute("select source_fingerprint from inspections")
        строка = cur.fetchone()
    assert строка is not None and строка[0] == прежний, "оснастка не положила прежний отпечаток"

    второй = push_inspection(ЧАТ)

    assert второй == первый, "слив не узнал проверку, лежащую под прежним отпечатком"
    assert _число_проверок(db_env) == 1, (
        "смена рецепта отпечатка задвоила уже слитую проверку — "
        "разделить эти две строки обратно нельзя, запечатанная не удаляется"
    )
    assert текущий != прежний
