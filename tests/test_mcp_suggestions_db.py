"""T165 на настоящей базе: предложения собираются из того, что и правда записано.

Сборку саму по себе проверяет `tests/test_mcp_suggestions.py` — без базы, над
готовыми строками. Здесь проверяется то, что без Postgres не проверить вовсе:

1. **Чтение находок за период идёт через официальный контракт блока `db`** и
   отдаёт предложение модели рядом с записью — ту самую пару, ради которой
   заводили T164 и T181.
2. **Чужого арендатора не видно.** Предложения для управляющей компании
   собираются по проверкам ОДНОГО арендатора: снятый фильтр обязан валить тест.
3. **Предложенный вызов правки исполним.** Это главное свойство файла:
   предложение, которое `edit_photo_cue` отклонит по ширине строки, ничего не
   стоит — а ширину видно только на настоящей карте слов с несколькими
   колонками, где «грязь» и «поломка» это два разных вопроса про один объект.
4. **Фраза аудитора доезжает от слива до предложения** (T194). Дорога тут и
   есть предмет: домен держит слова у себя, слив кладёт их в колонку (T185),
   чтение возвращает — и ни одного из этих шагов не видно в сборке над
   готовыми строками.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from conftest import requires_db

pytest.importorskip("psycopg")

from src.db.push import push_inspection
from src.domain import add_finding, start_inspection
from src.domain.models import Suggestion
from src.mcp.checklist import Store
from src.mcp.checklist_tools import edit_photo_cue, photo_cue_suggestions
from src.mcp.tools import read_findings_over_period

pytestmark = requires_db

АРЕНДАТОР_А = "партнёр-а"
АРЕНДАТОР_Б = "партнёр-б"

#: Строка карты слов в `tests/methodology`: «Печь | CLN05 | TEH05», две колонки —
#: «Грязь» и «Поломка». Модель предлагает CLN05 (нагар), аудитор записывает
#: CLN06 (мебель участка): промах, за которым стоит именно строка карты.
ПРЕДЛОЖЕНО = "CLN05"
ЗАПИСАНО = "CLN06"


def _проверка(
    chat_id: int,
    *,
    арендатор: str,
    точка: str = "Белград-1",
    дата: str = "2026-08-15",
    предложение: Suggestion | None = None,
    слова: str = "",
) -> str:
    start_inspection(
        chat_id, unit=точка, kind="planned", report_lang="ru", tenant=арендатор, date=дата
    )
    add_finding(
        chat_id,
        code=ЗАПИСАНО,
        level="D1",
        zone="hot_kitchen",
        text="стол в пятнах",
        source="photo",
        words=слова,
        suggested=предложение,
    )
    return push_inspection(chat_id)


def _предложение(confidence: float | None = 0.87) -> Suggestion:
    return Suggestion(code=ПРЕДЛОЖЕНО, level="D1", zone="hot_kitchen", confidence=confidence)


@pytest.fixture
def хранилище(tmp_path: Path, data_copy: Path) -> Store:
    """Хранилище версий поверх копии синтетической методики — с её картой слов."""
    return Store(root=tmp_path / "хранилище", live=data_copy)


# --- чтение за период --------------------------------------------------------


def test_предложение_модели_доезжает_до_сборки(domain_env: Path, db_env: str) -> None:
    _проверка(701, арендатор=АРЕНДАТОР_А, предложение=_предложение())

    найденное = read_findings_over_period(tenant=АРЕНДАТОР_А, date_from=None, date_to=None)

    assert найденное.inspections == 1
    assert найденное.units == 1
    (находка,) = найденное.rows
    assert находка.code == ЗАПИСАНО
    assert находка.suggested_code == ПРЕДЛОЖЕНО
    assert находка.corrections() == ("code",)


def test_находок_чужого_арендатора_в_выборке_нет(domain_env: Path, db_env: str) -> None:
    """Снятый фильтр по арендатору обязан валить этот тест: предложения одного
    партнёра, собранные по проверкам другого, — это утечка его истории."""
    _проверка(702, арендатор=АРЕНДАТОР_Б, точка="Ниш-1", предложение=_предложение())

    найденное = read_findings_over_period(tenant=АРЕНДАТОР_А, date_from=None, date_to=None)

    assert найденное.rows == ()
    assert найденное.inspections == 0


def test_период_отсекает_проверки_вне_окна(domain_env: Path, db_env: str) -> None:
    """Обе проверки на ОДНОЙ точке, и это условие теста, а не подробность.

    Находки читаются по точкам, а не по проверкам; когда точки в окне и вне
    его разные, окно отсекает их уже выбором точек, и отбор по проверкам
    периода ничего не делает. Пропущенный отбор виден только здесь: у точки,
    попавшей в окно, приезжает ВСЯ её история, включая проверки за прошлый год.
    """
    _проверка(703, арендатор=АРЕНДАТОР_А, дата="2026-08-15", предложение=_предложение())
    _проверка(704, арендатор=АРЕНДАТОР_А, дата="2026-07-01", предложение=_предложение())

    найденное = read_findings_over_period(
        tenant=АРЕНДАТОР_А,
        date_from=date(2026, 8, 1),
        date_to=None,
    )

    assert найденное.inspections == 1
    assert [находка.inspection_date for находка in найденное.rows] == [date(2026, 8, 15)]


# --- предложение целиком -----------------------------------------------------


def test_промах_собирается_в_предложение_с_нужной_строкой_карты(
    domain_env: Path, db_env: str, хранилище: Store
) -> None:
    _проверка(705, арендатор=АРЕНДАТОР_А, предложение=_предложение())

    итог = photo_cue_suggestions(tenant=АРЕНДАТОР_А, store=хранилище)

    assert итог["applied"] is False
    (промах,) = итог["code_misses"]
    assert промах["suggested_code"] == ПРЕДЛОЖЕНО
    assert промах["recorded_code"] == ЗАПИСАНО
    assert промах["count"] == 1
    assert [строка["phrase"] for строка in промах["cue_rows"]] == ["Печь"]


def test_предложенный_вызов_правки_действительно_принимается(
    domain_env: Path, db_env: str, хранилище: Store
) -> None:
    """Главное свойство файла. Предложение, собранное не той ширины, правка
    отклоняет («в таблице этого раздела 2 колонки с кодами, а названо 3»), и
    предложение, которое нельзя исполнить, не стоит ничего."""
    _проверка(706, арендатор=АРЕНДАТОР_А, предложение=_предложение())

    итог = photo_cue_suggestions(tenant=АРЕНДАТОР_А, store=хранилище)
    (вызов,) = итог["code_misses"][0]["suggested_edits"]
    assert вызов["tool"] == "edit_photo_cue"

    принято = edit_photo_cue(
        tenant=АРЕНДАТОР_А,
        store=хранилище,
        version_name="imf",
        **вызов["arguments"],
    )

    assert принято["version"]
    assert принято["published"] is False


def test_фраза_аудитора_доезжает_от_слива_до_предложения(
    domain_env: Path, db_env: str, хранилище: Store
) -> None:
    """T194 целиком: слова сказаны на точке, записаны сливом (T185) и названы в
    предложении для управляющей компании — дословно, вместе со строкой карты.

    Проверяется на настоящей базе, потому что дорога тут и есть предмет: домен
    держит слова у себя, слив кладёт их в колонку, чтение возвращает. Сборка
    над готовыми строками этого пути не касается вовсе."""
    _проверка(
        708,
        арендатор=АРЕНДАТОР_А,
        предложение=_предложение(),
        слова="ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ЧИСТОТА",
    )

    итог = photo_cue_suggestions(tenant=АРЕНДАТОР_А, store=хранилище)

    (промах,) = итог["code_misses"]
    assert промах["heard"]["phrases"] == [
        {"phrase": "ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ЧИСТОТА", "count": 1}
    ]
    assert промах["heard"]["without_words"] == 0
    assert [строка["phrase"] for строка in промах["cue_rows"]] == ["Печь"]


def test_запись_без_слов_названа_записью_без_слов_а_не_пустой_фразой(
    domain_env: Path, db_env: str, хранилище: Store
) -> None:
    """Аудитор ничего не говорил — выбрал пункт кнопкой. В базе это `NULL`, и
    выдать его пустой фразой значило бы показать произнесённое молчание."""
    _проверка(709, арендатор=АРЕНДАТОР_А, предложение=_предложение(), слова="")

    итог = photo_cue_suggestions(tenant=АРЕНДАТОР_А, store=хранилище)

    услышано = итог["code_misses"][0]["heard"]
    assert услышано["phrases"] == []
    assert услышано["without_words"] == 1
    assert итог["considered"]["without_words"] == 1


def test_быстрый_путь_без_уверенности_из_выборки_не_выпадает(
    domain_env: Path, db_env: str, хранилище: Store
) -> None:
    """Запись быстрого пути идёт БЕЗ уверенности, и это самый ценный промах —
    пункт показан без подтверждения аудитора. Порог обязан её пропустить."""
    _проверка(707, арендатор=АРЕНДАТОР_А, предложение=_предложение(confidence=None))

    итог = photo_cue_suggestions(tenant=АРЕНДАТОР_А, store=хранилище, min_confidence=0.95)

    assert итог["code_misses"][0]["count"] == 1
    assert итог["considered"]["without_confidence"] == 1


def test_проверок_нет_вовсе_это_сказано_словами(
    domain_env: Path, db_env: str, хранилище: Store
) -> None:
    итог = photo_cue_suggestions(tenant=АРЕНДАТОР_А, store=хранилище)

    assert "no findings" in итог["status"]
    assert итог["code_misses"] == []
