"""T178: расхождение версии методики на сливе называется, а не прячется.

Слив зовёт подсчёт оценки (`domain.score`), а тот с T148 отказывается считать
проверку по методике, отличной от записанной в ней: методику успевают издать
заново, пока проверка идёт. Отказ домена подробен и несёт обе версии полями
(`recorded`, `current`) — но `push_inspection` ловил его вместе со всеми
`DomainError` и заворачивал в общий `PushError` «слив не удался».

Чем это кончалось тихо: бот на завершении проверки разбирает исходы слива по
ТИПУ (`db.ConfigError` — базы нет, прочий `db.DbError` — база не приняла), и
расхождение версий попадало во вторую ветку. Аудитор получал «историю сохранить
не удалось» — сообщение про базу, которая на самом деле в полном порядке, — а
единственный, кто может выбрать выход, это он.

Случай узкий (методику переиздали между итогом и сливом, то есть между двумя
нажатиями), но именно узкие случаи и разбирают по типу исключения, а не по
тексту. Поэтому проверяется тип и поля, а не формулировка.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import requires_db

psycopg = pytest.importorskip("psycopg")

from src.db.errors import DbError, PushError, VersionMismatchError  # noqa: E402
from src.db.push import push_inspection  # noqa: E402
from src.domain import add_finding, checklist_version, list_items, start_inspection  # noqa: E402

pytestmark = requires_db

АРЕНДАТОР = "версия-методики"


@pytest.fixture
def методика(data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Своя копия методики: тест её переиздаёт, общий каталог не трогает.

    Подменяет `domain_env` целиком (тот же набор переменных), потому что этому
    тесту нужна методика, которую можно ПРАВИТЬ прямо во время проверки, — а
    `tests/methodology` общий на весь набор.
    """
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return data_copy


def _штрафной_пункт(data_dir: Path) -> tuple[str, str, str]:
    """Пункт, зона и класс с ненулевым вычетом — всё вычитано из самой методики."""
    ставки = json.loads((data_dir / "scoring.json").read_text(encoding="utf-8"))
    штрафные = {
        уровень for уровень, ставка in (ставки.get("penalty") or {}).items() if float(ставка) > 0
    }
    for пункт in list_items():
        зоны = [z for z in пункт.zones if z != "*"]
        классы = [c for c in пункт.levels if c in штрафные]
        if зоны and классы:
            return пункт.code, зоны[0], классы[0]
    raise AssertionError("в методике не нашлось пункта с зоной и штрафным классом")


def _издать_заново(data_dir: Path) -> None:
    """Правка методики, меняющая отпечаток версии: приписка в критериях."""
    критерии = data_dir / "criteria.md"
    критерии.write_text(
        критерии.read_text(encoding="utf-8") + "\n<!-- издание теста -->\n", encoding="utf-8"
    )


def _проверка(методика: Path, chat_id: int) -> str:
    """Проверка с одной штрафной записью. Возвращает записанную версию методики."""
    start_inspection(chat_id, unit="Белград-1", kind="planned", report_lang="ru", tenant=АРЕНДАТОР)
    пункт, зона, класс = _штрафной_пункт(методика)
    add_finding(chat_id, code=пункт, level=класс, zone=зона, text="нагар на печи")
    return checklist_version()


def test_расхождение_версии_на_сливе_отличимо_по_типу(методика: Path, db_env: str) -> None:
    """Главная проверка задачи: тип отказа отличается от «база не приняла».

    Разбирать этот случай по тексту нельзя: текст уходит человеку и правится,
    а ветка кода, которая его показывает, обязана выбираться типом.
    """
    записанная = _проверка(методика, 851)
    _издать_заново(методика)
    действующая = checklist_version()
    assert записанная != действующая, "издание не поменяло версию — дальше проверять нечего"

    with pytest.raises(VersionMismatchError) as отказ:
        push_inspection(851)

    assert отказ.value.recorded == записанная
    assert отказ.value.current == действующая


def test_расхождение_версии_остаётся_отказом_слива_для_прежних_потребителей(
    методика: Path, db_env: str
) -> None:
    """Новый тип не выпадает мимо тех, кто ловит слив целиком.

    Бот перехватывает `db.DbError` и не имеет права упасть от того, что у
    отказа появился более точный тип: падение здесь означает необработанное
    исключение на завершении проверки, то есть последний рубеж вместо любого
    сообщения вообще.
    """
    _проверка(методика, 852)
    _издать_заново(методика)

    with pytest.raises(PushError):
        push_inspection(852)
    with pytest.raises(DbError):
        push_inspection(852)


def test_в_отказе_названы_обе_версии_и_выход(методика: Path, db_env: str) -> None:
    """Текст пишется человеку, а не только коду.

    Обе версии в нём нужны потому, что выхода из расхождения два и оба решает
    человек: перевести проверку на действующую методику или вернуть прежнюю на
    диск. Без версий он не выберет ни одного.
    """
    записанная = _проверка(методика, 853)
    _издать_заново(методика)
    действующая = checklist_version()

    with pytest.raises(VersionMismatchError) as отказ:
        push_inspection(853)

    текст = str(отказ.value)
    assert записанная in текст and действующая in текст, "в отказе нет версий, между которыми выбор"
    assert "sync_checklist_version" in текст, "в отказе не назван способ выйти из расхождения"


def test_расхождение_версии_не_оставляет_проверку_в_базе(методика: Path, db_env: str) -> None:
    """Отказ обязан быть целым: посчитать не смогли — записывать нечего.

    Подсчёт идёт до первого запроса, поэтому строки не появляется вовсе. Это
    проверяется, а не подразумевается: наполовину слитая проверка выглядела бы
    в истории точки обычной записью с чужой оценкой.
    """
    _проверка(методика, 854)
    _издать_заново(методика)

    with pytest.raises(VersionMismatchError):
        push_inspection(854)

    with psycopg.connect(db_env) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from inspections where tenant_code = %s", (АРЕНДАТОР,))
        assert cur.fetchone() == (0,), (
            "проверка легла в базу, хотя оценку по ней считать отказались"
        )
