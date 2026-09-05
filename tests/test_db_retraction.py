"""T210, T233: снятие сданной проверки из истории.

Неверный отчёт не правится — заводится новый, а старый снимается (D086), и
снимается ПОМЕТКОЙ, а не удалением строки (D089). Отсюда три свойства, которые
здесь и проверяются: причина снятия обязательна; снятой проверки в истории не
видно, а администратору она видна ИМЕННО КАК СНЯТАЯ; та же проверка после
снятия сливается заново новой строкой, а не упирается в отпечаток.

Разграничение видимости здесь проверяется через продуктовые вызовы. То, что
его держит база, а не эти вызовы, — тема соседнего файла
(`test_db_retraction_policies.py`): там всё идёт сырым SQL под обеими ролями.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import requires_db
from db_harness import RETRACTION_URL_VAR, set_retraction_env

psycopg = pytest.importorskip("psycopg")

from src.db.errors import ConfigError, DbError, RetractionError  # noqa: E402
from src.db.push import push_inspection  # noqa: E402
from src.db.queries import findings_by_unit, get_inspection, list_inspections  # noqa: E402
from src.db.retract import retract_inspection  # noqa: E402
from src.domain import add_finding, start_inspection  # noqa: E402

pytestmark = requires_db

ТОЧКА = "Белград-1"
ПРИЧИНА = "правил ошибку в шапке"


@pytest.fixture
def retraction_env(db_env: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Подключение администратора истории рядом с подключением приложения.

    Однострочная обёртка над помощником из `db_harness` — почему не общая
    фикстура, написано там же.
    """
    return set_retraction_env(db_env, monkeypatch)


def _проверка(chat_id: int, *, точка: str = ТОЧКА, арендатор: str = "default") -> str:
    """Настоящая проверка через контракт `domain`, затем слив в базу."""
    start_inspection(chat_id, unit=точка, kind="planned", report_lang="ru", tenant=арендатор)
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    return push_inspection(chat_id)


def _строка(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# --- сама пометка -------------------------------------------------------------


def test_снятие_ставит_пометку_и_записывает_причину(domain_env: Path, retraction_env: str) -> None:
    """Строка остаётся на месте: снятие — пометка, а не удаление (D089)."""
    ident = _проверка(401)

    снятие = retract_inspection(ident, tenant="default", reason=ПРИЧИНА)

    assert снятие.inspection_id == ident
    assert снятие.reason == ПРИЧИНА
    строка = _строка(
        retraction_env,
        "select retracted_at, retraction_reason, status, pct from inspections where id = %s",
        (ident,),
    )
    assert строка is not None, "строка проверки исчезла — снятие удалило документ"
    assert строка[0] is not None, "пометки снятия нет"
    assert строка[1] == ПРИЧИНА
    assert строка[2] == "finalized", "снятие не должно распечатывать проверку"
    assert float(строка[3]) == 100.0 - 0.5, "оценка снятой проверки изменилась"


def test_причина_снятия_обязательна(domain_env: Path, retraction_env: str) -> None:
    """Снятие без причины неотличимо от подчистки истории (D089).

    Проверяется не только отказ, но и то, что проверка ОСТАЛАСЬ живой: отказ,
    случившийся после записи, был бы худшим исходом — он выглядит как «ничего
    не произошло».
    """
    ident = _проверка(402)

    for пустая in ("", "   ", "\n\t"):
        with pytest.raises(RetractionError) as отказ:
            retract_inspection(ident, tenant="default", reason=пустая)
        assert "причина" in str(отказ.value).lower()

    строка = _строка(retraction_env, "select retracted_at from inspections where id = %s", (ident,))
    assert строка is not None and строка[0] is None, "проверка снята, хотя причины не назвали"


def test_причина_обрезается_по_краям_но_не_переписывается(
    domain_env: Path, retraction_env: str
) -> None:
    """Причину пишет человек: внешние пробелы — не часть причины, а всё прочее — часть."""
    ident = _проверка(403)

    снятие = retract_inspection(ident, tenant="default", reason=f"  {ПРИЧИНА}  ")

    assert снятие.reason == ПРИЧИНА
    строка = _строка(
        retraction_env, "select retraction_reason from inspections where id = %s", (ident,)
    )
    assert строка is not None and строка[0] == ПРИЧИНА


def test_снятие_повторяемо_и_причину_не_переписывает(domain_env: Path, retraction_env: str) -> None:
    """Второй вызов доделывает уборку, а не правит основание задним числом.

    Причина снятия — запись о том, почему документ отозван. Если бы повторный
    вызов её переписывал, снятие стало бы способом менять основание после
    того, как партнёру уже сказали.
    """
    ident = _проверка(404)
    первое = retract_inspection(ident, tenant="default", reason=ПРИЧИНА)

    второе = retract_inspection(ident, tenant="default", reason="совсем другая причина")

    assert второе.reason == ПРИЧИНА
    assert второе.retracted_at == первое.retracted_at, "время снятия переписали"
    строка = _строка(
        retraction_env, "select retraction_reason from inspections where id = %s", (ident,)
    )
    assert строка is not None and строка[0] == ПРИЧИНА


# --- чего снять нельзя --------------------------------------------------------


def test_чужую_проверку_снять_нельзя(domain_env: Path, retraction_env: str) -> None:
    """Арендатор — не украшение вызова: снять можно только своё."""
    чужая = _проверка(405, точка="Будапешт-1", арендатор="partner-b")

    with pytest.raises(RetractionError) as отказ:
        retract_inspection(чужая, tenant="default", reason=ПРИЧИНА)

    assert "нет" in str(отказ.value)
    строка = _строка(retraction_env, "select retracted_at from inspections where id = %s", (чужая,))
    assert строка is not None and строка[0] is None, "чужую проверку всё-таки сняли"


def test_несуществующую_проверку_снять_нельзя(domain_env: Path, retraction_env: str) -> None:
    """«Сняли ноль строк» не бывает успехом: снимают всегда конкретный документ."""
    import uuid

    with pytest.raises(RetractionError):
        retract_inspection(str(uuid.uuid4()), tenant="default", reason=ПРИЧИНА)


def test_кривой_идентификатор_отличим_от_несуществующего(
    domain_env: Path, retraction_env: str
) -> None:
    """Опечатка в аргументе и «такой проверки нет» — разные вещи."""
    with pytest.raises(DbError) as отказ:
        retract_inspection("не-идентификатор", tenant="default", reason=ПРИЧИНА)
    assert "не похоже на идентификатор" in str(отказ.value)


def test_снять_то_на_что_сослались_нельзя(
    domain_env: Path, pg_dsn: str, retraction_env: str
) -> None:
    """У сославшейся проверки пропало бы основание, и заметить это было бы негде.

    Ссылка повторной проверки на исходную (`repeat_of_id`) заводится здесь
    прямым запросом под привилегированной ролью: домен эту связь пока не
    отдаёт (D029), а форма в схеме есть с самого начала — и снятие обязано её
    учитывать уже сейчас, а не с того дня, когда появится источник данных.
    """
    исходная = _проверка(406)
    повторная = _проверка(407, точка="Белград-2")
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "update inspections set repeat_of_id = %s where id = %s", (исходная, повторная)
        )
        conn.commit()

    with pytest.raises(RetractionError) as отказ:
        retract_inspection(исходная, tenant="default", reason=ПРИЧИНА)

    assert повторная in str(отказ.value), "отказ не назвал, кто сослался"
    строка = _строка(
        retraction_env, "select retracted_at from inspections where id = %s", (исходная,)
    )
    assert строка is not None and строка[0] is None, "исходную сняли, оборвав ссылку"


# --- чтение -------------------------------------------------------------------


def test_снятой_проверки_в_истории_не_видно(domain_env: Path, retraction_env: str) -> None:
    """Снятая проверка выбывает из истории точки — ради этого снятие и делают."""
    снятая = _проверка(408)
    живая = _проверка(409, точка="Белград-2")
    retract_inspection(снятая, tenant="default", reason=ПРИЧИНА)

    видно = {строка.id for строка in list_inspections(tenant="default")}

    assert живая in видно
    assert снятая not in видно


def test_администратор_видит_снятую_и_видит_что_она_снята(
    domain_env: Path, retraction_env: str
) -> None:
    """«Снятой не видно» и «снятой не было» — разные ответы, и второй был бы ложью."""
    снятая = _проверка(410)
    retract_inspection(снятая, tenant="default", reason=ПРИЧИНА)

    строки = {
        строка.id: строка for строка in list_inspections(tenant="default", include_retracted=True)
    }

    assert снятая in строки
    assert строки[снятая].retracted is True
    assert строки[снятая].retraction_reason == ПРИЧИНА


def test_живая_проверка_снятой_не_помечена(domain_env: Path, retraction_env: str) -> None:
    """Встречное утверждение: пометка не стоит у всех подряд.

    Без него проверка выше была бы зелена и на коде, который помечает снятым
    что угодно, — а такой код прятал бы из истории всё.
    """
    живая = _проверка(411)

    строки = {
        строка.id: строка for строка in list_inspections(tenant="default", include_retracted=True)
    }

    assert строки[живая].retracted is False
    assert строки[живая].retraction_reason == ""


def test_снятая_проверка_по_идентификатору_отвечает_ничем(
    domain_env: Path, retraction_env: str
) -> None:
    """Тому, кто снятых не видит, они и не существуют — тот же ответ, что у чужой."""
    ident = _проверка(412)
    retract_inspection(ident, tenant="default", reason=ПРИЧИНА)

    assert get_inspection(ident, tenant="default") is None

    подробно = get_inspection(ident, tenant="default", include_retracted=True)
    assert подробно is not None
    assert подробно.inspection.retracted is True
    assert подробно.inspection.retraction_reason == ПРИЧИНА
    assert len(подробно.findings) == 1, "тело снятой проверки администратору тоже видно"


def test_находки_снятой_проверки_из_истории_точки_уходят(
    domain_env: Path, retraction_env: str
) -> None:
    """Отозванный документ, посчитанный за повтор, — требование по пропавшему основанию."""
    снятая = _проверка(413)
    _проверка(414)
    retract_inspection(снятая, tenant="default", reason=ПРИЧИНА)

    находки = findings_by_unit(tenant="default", unit=ТОЧКА)

    проверки = {находка.inspection_id for находка in находки}
    assert len(находки) == 1, "находки снятой проверки остались в истории точки"
    assert снятая not in проверки


# --- отпечаток ----------------------------------------------------------------


def test_после_снятия_ту_же_проверку_можно_слить_заново(
    domain_env: Path, retraction_env: str
) -> None:
    """Ровно тот сценарий из решения: завели новый отчёт вместо снятого (D086).

    До частичной уникальности отпечатка (миграция `0010`) этот слив упирался в
    уникальный индекс и падал отказом «Postgres не вернул строку после INSERT»,
    не сказав ни слова о снятии.
    """
    первый = _проверка(415)
    retract_inspection(первый, tenant="default", reason=ПРИЧИНА)

    второй = push_inspection(415)

    assert второй != первый, "слив вернул снятую проверку вместо новой"
    видно = {строка.id for строка in list_inspections(tenant="default")}
    assert видно == {второй}
    всего = {строка.id for строка in list_inspections(tenant="default", include_retracted=True)}
    assert всего == {первый, второй}, "снятая проверка пропала из истории администратора"


def test_повторный_слив_живой_проверки_дубля_по_прежнему_не_создаёт(
    domain_env: Path, retraction_env: str
) -> None:
    """Встречное утверждение к предыдущему: частичность индекса не отменила сверку."""
    первый = _проверка(416)

    assert push_inspection(416) == первый


# --- окружение ----------------------------------------------------------------


def test_без_подключения_администратора_снятие_отказывает(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Право видеть снятое живёт в базе: без своей роли снимать нечем."""
    ident = _проверка(417)
    monkeypatch.delenv(RETRACTION_URL_VAR, raising=False)

    with pytest.raises(ConfigError) as отказ:
        retract_inspection(ident, tenant="default", reason=ПРИЧИНА)

    assert RETRACTION_URL_VAR in str(отказ.value)


def test_без_подключения_администратора_снятых_не_показывают_а_отказывают(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Снятых нет» вместо «вам их не видно» — тихий ответ, который никто не перепроверит."""
    _проверка(418)
    monkeypatch.delenv(RETRACTION_URL_VAR, raising=False)

    with pytest.raises(ConfigError):
        list_inspections(tenant="default", include_retracted=True)
    with pytest.raises(ConfigError):
        get_inspection(str(_проверка(419)), tenant="default", include_retracted=True)
