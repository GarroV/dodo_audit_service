"""T211: снятие проверки инструментом MCP — граница, подтверждение и отказы.

Само снятие живёт в блоке `db` и проверено там (`test_db_retraction.py`,
`test_db_retraction_policies.py`). Здесь проверяется ровно то, что добавляет
инструмент, и ничего сверх того:

* **подтверждение** — снять вслепую по одному идентификатору нельзя;
* **отказ человеческими словами** — чужая проверка, уже снятая, незапечатанная,
  причина не названа;
* **чужой текст в ответ не уезжает** — в сообщении драйвера базы стоит адрес
  и имя базы, а ответ инструмента уходит в модель;
* **снятая проверка пропадает из обычного чтения — через сам инструмент**, а не
  через слой под ним: заслон стоит в базе, но проверять его надо там, где на
  него смотрит спрашивающий.

Право на вызов (кому открыто снятие) проверяется отдельно —
`test_mcp_retraction_access.py`: для него база не нужна вовсе.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import requires_db
from db_harness import set_retraction_env

pytest.importorskip("psycopg")

from src.mcp import retraction
from src.mcp import tools as reads
from src.mcp.errors import ToolError

pytestmark = requires_db

ТОЧКА = "Белград-1"
АРЕНДАТОР = "default"
ПРИЧИНА = "перепутана точка в шапке, отчёт заведён заново"


@pytest.fixture
def retraction_env(db_env: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Подключение администратора истории рядом с подключением приложения."""
    return set_retraction_env(db_env, monkeypatch)


def _проверка(chat_id: int, *, точка: str = ТОЧКА, арендатор: str = АРЕНДАТОР) -> str:
    """Настоящая проверка через контракт `domain`, затем слив в базу."""
    from src.db.push import push_inspection
    from src.domain import add_finding, start_inspection

    start_inspection(chat_id, unit=точка, kind="planned", report_lang="ru", tenant=арендатор)
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    return push_inspection(chat_id)


def _подтверждение(ident: str, *, арендатор: str = АРЕНДАТОР) -> dict[str, str]:
    """Точка и дата так, как они записаны, — то есть годное подтверждение.

    Берутся из базы, а не пишутся литералом: дату обхода ставит продукт, и
    литерал разошёлся бы с ней в первый же день, когда тест погонят не сегодня.
    """
    from src.db.queries import get_inspection

    подробно = get_inspection(ident, tenant=арендатор, include_retracted=True)
    assert подробно is not None
    строка = подробно.inspection
    return {"confirm_unit": строка.unit_name, "confirm_date": строка.inspection_date.isoformat()}


def _снять(ident: str, *, арендатор: str = АРЕНДАТОР, причина: str = ПРИЧИНА) -> dict[str, Any]:
    return retraction.retract_inspection(
        tenant=арендатор, id=ident, reason=причина, **_подтверждение(ident, арендатор=арендатор)
    )


# --- снятие через инструмент --------------------------------------------------


def test_снятие_отдаёт_записанную_причину_и_описание_снятого(
    domain_env: Path, retraction_env: str
) -> None:
    """Ответ описывает документ, а не только подтверждает действие.

    Человек, читающий пересказ агента, обязан узнать бумагу, которой лишился
    партнёр: точку, дату и оценку — записанную, а не пересчитанную.
    """
    ident = _проверка(501)

    ответ = _снять(ident)

    assert ответ["id"] == ident
    assert ответ["reason"] == ПРИЧИНА
    assert ответ["unit"] == ТОЧКА
    assert ответ["grade"] == "A"
    assert ответ["retracted_at"], "время снятия обязано быть названо"
    assert "not reversible" in str(ответ["status"]).lower()


def test_снятая_проверка_пропадает_из_каждого_чтения_ИНСТРУМЕНТАМИ(
    domain_env: Path, retraction_env: str
) -> None:
    """Главное утверждение файла — и смотрит оно ровно туда, куда смотрит агент.

    Прячет снятую проверку построчная политика базы, и в блоке `db` это уже
    проверено. Но спрашивающий видит не политику, а ответ инструмента: между
    ними лежит слой, который однажды прочитает историю вторым способом — под
    подключением администратора, «чтобы было полнее», — и снятая проверка
    вернётся в выдачу, не сломав ни одного теста уровнем ниже.

    Поэтому перебираются все читающие инструменты сразу: инструмент, который
    начнёт показывать снятое, покраснеет здесь.
    """
    снятая = _проверка(502)
    живая = _проверка(503, точка="Белград-2")
    _снять(снятая)

    список = reads.list_inspections(tenant=АРЕНДАТОР)
    видно = {строка["id"] for строка in список["inspections"]}
    assert живая in видно
    assert снятая not in видно

    история = reads.unit_history(tenant=АРЕНДАТОР, unit=ТОЧКА)
    assert снятая not in {строка["id"] for строка in история["history"]}

    находки = reads.findings_by_unit(tenant=АРЕНДАТОР, unit=ТОЧКА)
    assert находки["findings"] == [], "находки снятой проверки остались в истории точки"

    сводка = reads.network_summary(tenant=АРЕНДАТОР)
    assert сводка["inspections"] == 1, "снятая проверка сосчитана в сводке по сети"

    карточка = reads.get_inspection(tenant=АРЕНДАТОР, id=снятая)
    assert карточка["found"] is False
    assert карточка["inspection"] is None
    assert карточка["findings"] == []

    письмо = reads.inspection_letter(tenant=АРЕНДАТОР, id=снятая)
    assert письмо["found"] is False
    assert письмо["letter"] is None, "письмо по отозванному документу пересобралось"

    # Встречное утверждение: живая проверка теми же вызовами читается. Без него
    # всё выше было бы зелено и на чтении, которое сломалось целиком.
    assert reads.get_inspection(tenant=АРЕНДАТОР, id=живая)["found"] is True


# --- подтверждение ------------------------------------------------------------


def test_чужая_дата_в_подтверждении_ничего_не_снимает(
    domain_env: Path, retraction_env: str
) -> None:
    """Промах по идентификатору обязан упереться в подтверждение, а не в снятие."""
    ident = _проверка(504)
    подтверждение = _подтверждение(ident)

    with pytest.raises(ToolError) as отказ:
        retraction.retract_inspection(
            tenant=АРЕНДАТОР,
            id=ident,
            reason=ПРИЧИНА,
            confirm_unit=подтверждение["confirm_unit"],
            confirm_date="2001-01-01",
        )

    assert "не снята" in str(отказ.value)
    assert ТОЧКА in str(отказ.value), "отказ обязан сказать, чем эта проверка является"
    assert reads.get_inspection(tenant=АРЕНДАТОР, id=ident)["inspection"]["id"] == ident, (
        "проверка снята, хотя подтверждение не сошлось"
    )


def test_чужая_точка_в_подтверждении_ничего_не_снимает(
    domain_env: Path, retraction_env: str
) -> None:
    ident = _проверка(505)
    подтверждение = _подтверждение(ident)

    with pytest.raises(ToolError, match="не снята"):
        retraction.retract_inspection(
            tenant=АРЕНДАТОР,
            id=ident,
            reason=ПРИЧИНА,
            confirm_unit="Белград-2",
            confirm_date=подтверждение["confirm_date"],
        )

    assert reads.get_inspection(tenant=АРЕНДАТОР, id=ident)["inspection"]["id"] == ident


def test_имя_точки_сверяется_ключом_продукта_а_не_побуквенно(
    domain_env: Path, retraction_env: str
) -> None:
    """Человек копирует название из карточки, а не набирает его посимвольно.

    Сравнение идёт тем же ключом сопоставления, которым продукт сводит точки
    (`db.units.normalize_unit_name`): своё второе правило означало бы, что
    подтверждение не принимает имя, под которым проверка и лежит.
    """
    ident = _проверка(506)
    подтверждение = _подтверждение(ident)

    ответ = retraction.retract_inspection(
        tenant=АРЕНДАТОР,
        id=ident,
        reason=ПРИЧИНА,
        confirm_unit=f"  {подтверждение['confirm_unit'].upper()}  ",
        confirm_date=подтверждение["confirm_date"],
    )

    assert ответ["id"] == ident


@pytest.mark.parametrize(
    ("поле", "значение"),
    [("confirm_unit", "   "), ("confirm_date", ""), ("confirm_date", "19.08.2026")],
)
def test_подтверждение_без_внятного_ответа_это_отказ_до_базы(
    domain_env: Path, retraction_env: str, поле: str, значение: str
) -> None:
    """Пустое подтверждение — не «подтверждения не требуется»."""
    ident = _проверка(507)
    аргументы = {**_подтверждение(ident), поле: значение}

    with pytest.raises(ToolError):
        retraction.retract_inspection(tenant=АРЕНДАТОР, id=ident, reason=ПРИЧИНА, **аргументы)

    assert reads.get_inspection(tenant=АРЕНДАТОР, id=ident)["inspection"]["id"] == ident


# --- отказы человеческими словами ---------------------------------------------


def test_чужая_проверка_отвечает_тем_же_что_несуществующая(
    domain_env: Path, retraction_env: str
) -> None:
    """Разные ответы дали бы перебору идентификаторов состав чужой истории."""
    чужая = _проверка(508, арендатор="сосед")

    with pytest.raises(ToolError) as отказ:
        retraction.retract_inspection(
            tenant=АРЕНДАТОР,
            id=чужая,
            reason=ПРИЧИНА,
            confirm_unit=ТОЧКА,
            confirm_date="2026-08-19",
        )
    у_чужого = str(отказ.value)

    with pytest.raises(ToolError) as нет_такой:
        retraction.retract_inspection(
            tenant=АРЕНДАТОР,
            id="00000000-0000-0000-0000-000000000009",
            reason=ПРИЧИНА,
            confirm_unit=ТОЧКА,
            confirm_date="2026-08-19",
        )

    assert у_чужого.replace(чужая, "") == str(нет_такой.value).replace(
        "00000000-0000-0000-0000-000000000009", ""
    ), "по разнице ответов читается, существует ли чужая проверка"


def test_уже_снятая_называет_записанную_причину_и_не_переписывает_её(
    domain_env: Path, retraction_env: str
) -> None:
    """Повторный вызов не «готово»: своя причина не записывается, и это отказ.

    Ответ «готово» агент однажды перескажет человеку как «новая причина
    принята» — то же соображение, по которому отклонённая правка методики
    приходит отказом, а не полем в обычном ответе.
    """
    from src.db.queries import get_inspection

    ident = _проверка(509)
    _снять(ident)

    with pytest.raises(ToolError) as отказ:
        _снять(ident, причина="другая причина, записанная позже")

    assert ПРИЧИНА in str(отказ.value), "отказ обязан назвать записанную причину"
    подробно = get_inspection(ident, tenant=АРЕНДАТОР, include_retracted=True)
    assert подробно is not None
    assert подробно.inspection.retraction_reason == ПРИЧИНА, "причина снятия переписана"


def test_причина_не_названа_отказ_говорит_чего_не_хватает(
    domain_env: Path, retraction_env: str
) -> None:
    """Отказ приходит словами блока `db`: правило живёт там, пересказ разошёлся бы."""
    ident = _проверка(510)

    with pytest.raises(ToolError) as отказ:
        _снять(ident, причина="   ")

    assert "причин" in str(отказ.value).lower()
    assert "D089" in str(отказ.value)


def test_незапечатанная_проверка_снятию_не_подлежит(
    domain_env: Path, pg_dsn: str, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Слив, оборванный до печати, — это черновик, а не выданный документ.

    Печать снимается под ролью, СОЗДАВШЕЙ базу, а не под администратором
    истории: тому выданы ровно две колонки (`retracted_at`,
    `retraction_reason`), и правка `status` запрещена ему привилегией. Это не
    помеха тесту, а подтверждение заслона: администратор снимает проверку, но
    не распечатывает её обратно.
    """
    import psycopg

    set_retraction_env(db_env, monkeypatch)
    ident = _проверка(511)
    подтверждение = _подтверждение(ident)
    with psycopg.connect(pg_dsn) as conn:
        # Печать снимается сырым SQL намеренно: продуктового пути «распечатать
        # обратно» нет и не будет, а состояние, в которое слив попадает при
        # обрыве, воспроизвести чем-то надо.
        conn.execute("update inspections set status = 'draft' where id = %s", (ident,))
        conn.commit()

    with pytest.raises(ToolError) as отказ:
        retraction.retract_inspection(tenant=АРЕНДАТОР, id=ident, reason=ПРИЧИНА, **подтверждение)

    assert "запечат" in str(отказ.value).lower()


def test_снятие_без_подключения_администратора_называет_переменную(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ настройки не должен читаться как «такой проверки нет»."""
    ident = _проверка(512)
    monkeypatch.delenv("DATABASE_RETRACTION_URL", raising=False)

    with pytest.raises(ToolError) as отказ:
        retraction.retract_inspection(
            tenant=АРЕНДАТОР,
            id=ident,
            reason=ПРИЧИНА,
            confirm_unit=ТОЧКА,
            confirm_date="2026-08-19",
        )

    assert "DATABASE_RETRACTION_URL" in str(отказ.value)
    assert "dodo_audit_admin" in str(отказ.value)


# --- чужой текст в ответ не уезжает -------------------------------------------

#: Похоже на то, что печатает psycopg, когда не удалось подключиться: адрес,
#: порт и имя базы. Настоящую строку подключения сюда класть незачем — важно,
#: что этот текст пришёл ИЗВНЕ и в ответе агенту его быть не должно.
ЧУЖОЙ_ТЕКСТ = 'connection to server at "10.0.0.7", port 5432 failed: database "истории" absent'


def test_текст_отказа_драйвера_в_ответ_не_попадает(
    domain_env: Path, retraction_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ответ инструмента уходит в модель, то есть за пределы машины (T120).

    Блок `db` отвечает на беду окружения тем же типом, что и на отказ по
    существу, и приводит в тексте сообщение драйвера целиком. Инструмент обязан
    различать их не разбором строки: обёрнутый отказ узнаётся по `__cause__` —
    он есть ровно там, где в текст подставлен чужой текст.
    """
    import src.db.retract as снятие_блока
    from src.db.errors import RetractionError

    ident = _проверка(513)

    def упасть(*_: object, **__: object) -> None:
        raise RetractionError(f"Снятие не удалось (OperationalError): {ЧУЖОЙ_ТЕКСТ}") from (
            OSError("исходная беда")
        )

    monkeypatch.setattr(снятие_блока, "retract_inspection", упасть)

    with pytest.raises(ToolError) as отказ:
        _снять(ident)

    assert ЧУЖОЙ_ТЕКСТ not in str(отказ.value), "текст драйвера уехал агенту"
    assert "10.0.0.7" not in str(отказ.value)
    assert "OSError" in str(отказ.value), "вид отказа назвать надо: без него чинить нечего"
    assert ident in str(отказ.value)


def test_отказ_по_существу_доезжает_словами_блока_db(
    domain_env: Path, retraction_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Встречное утверждение: заслон не глушит отказы, написанные для человека.

    Без него проверка выше была бы зелена и на коде, который прячет ВСЕ отказы
    снятия за общим текстом, — а тогда «причина не названа» и «проверка не
    запечатана» перестали бы доходить до спрашивающего.
    """
    import src.db.retract as снятие_блока
    from src.db.errors import RetractionError

    ident = _проверка(514)
    сказано = "на неё уже сослались как на исходную — сперва разобраться со ссылающимися"

    def отказать(*_: object, **__: object) -> None:
        raise RetractionError(сказано)

    monkeypatch.setattr(снятие_блока, "retract_inspection", отказать)

    with pytest.raises(ToolError) as отказ:
        _снять(ident)

    assert сказано in str(отказ.value)


def test_повтор_с_пустой_причиной_говорит_про_снятие_а_не_про_причину(
    domain_env: Path, retraction_env: str
) -> None:
    """Ответ обязан говорить о том, чего вызов не сделает, а не о лишнем поле.

    У снятой проверки причина уже записана и не переписывается — значит
    названная вызовом причина не участвует вовсе, в том числе пустая. Разговор
    про «не названа причина» здесь увёл бы человека к полю, которое ни на что
    не влияет, вместо главного: снимать нечего.
    """
    ident = _проверка(515)
    _снять(ident)

    with pytest.raises(ToolError) as отказ:
        _снять(ident, причина="")

    assert "снята раньше" in str(отказ.value)
    assert ПРИЧИНА in str(отказ.value), "записанная причина обязана быть названа"
