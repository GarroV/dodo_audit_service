"""T114: чтение находок отдаёт то, что записал аудитор, — и в том же порядке.

`test_db_reads_tenant.py` и `test_db_queries_tenant.py` покрывают границу
арендаторов, предел выдачи, отбор по периоду и порядок выдачи по проверкам.
Здесь — остальное поведение `get_inspection` и `findings_by_unit`: содержимое
полей находки и шапки проверки, порядок находок внутри проверки, различие
`None` и пустой строки у комментария, источник записи, язык речи проверки и
отказ на кривом идентификаторе.

Каждый тест ловит свою немую подмену: перепутанные `text`/`comment` в SQL,
снятый `order by f.n`, не ту колонку в разборе строки курсора — все они молча
отдают документ, который выглядит правдоподобно, но описывает не то, что
записал аудитор.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from conftest import requires_db

psycopg = pytest.importorskip("psycopg")

from src.db.directory import upsert_unit  # noqa: E402 — после importorskip намеренно
from src.db.errors import DbError  # noqa: E402
from src.db.push import push_inspection  # noqa: E402
from src.db.queries import findings_by_unit, get_inspection  # noqa: E402
from src.domain import (  # noqa: E402
    SOURCE_COMMENT,
    add_finding,
    checklist_version,
    start_inspection,
)
from src.domain import score as domain_score  # noqa: E402

pytestmark = requires_db

АРЕНДАТОР = "партнёр-находки"


def _проверка_с_находками(
    chat_id: int,
    *,
    unit: str = "Белград-1",
    дата: str = "2026-03-01",
    speech_lang: str = "ru",
    находки: tuple[tuple[str, str], ...] = (),
) -> str:
    """Завершённая проверка через официальный контракт домена, а не прямой INSERT.

    `находки` — пары «зона, текст», добавляются в переданном порядке кодом
    `CLN05` уровня `D1`: одного кода и уровня хватает, чтобы проверить порядок
    и содержимое, а разбор методики здесь не при чём.
    """
    start_inspection(
        chat_id,
        unit=unit,
        kind="planned",
        report_lang="ru",
        speech_lang=speech_lang,
        date=дата,
        tenant=АРЕНДАТОР,
    )
    for zone, text in находки:
        add_finding(chat_id, code="CLN05", level="D1", zone=zone, text=text)
    return push_inspection(chat_id)


def test_шапка_проверки_совпадает_с_записанным(domain_env: Path, db_env: str) -> None:
    """Разъехавшаяся шапка показала бы партнёру не ту точку, не тот вид
    проверки или не ту дату обхода — а расхождение не всплыло бы нигде, кроме
    сравнения с тем, что реально записал аудитор."""
    chat_id = 501
    insp_id = _проверка_с_находками(
        chat_id,
        unit="Белград-1",
        дата="2026-03-01",
        находки=(("hot_kitchen", "нагар на печи"), ("cold_kitchen", "грязный стол")),
    )
    ожидаемая_версия = checklist_version()
    оценка = domain_score(chat_id)

    прочитанная = get_inspection(insp_id, tenant=АРЕНДАТОР)

    assert прочитанная is not None
    шапка = прочитанная.inspection
    assert шапка.unit_name == "Белград-1"
    assert шапка.kind == "planned"
    assert шапка.inspection_date == date(2026, 3, 1)
    assert шапка.report_lang == "ru"
    assert шапка.checklist_version == ожидаемая_версия
    assert шапка.findings_count == 2
    assert шапка.pct == pytest.approx(оценка.pct), (
        "процент в базе разошёлся с тем, что вернул движок"
    )
    assert шапка.grade == оценка.grade, "буква в базе разошлась с тем, что вернул движок"


def test_разбивка_оценки_совпадает_со_score_движка(domain_env: Path, db_env: str) -> None:
    """`deductions`/`counts`/`by_zone` — это `Score` движка как лежит: подмена
    здесь молчит и на проценте, и на букве, потому что и то, и другое уже
    посчитано отдельно и хранится рядом."""
    chat_id = 502
    insp_id = _проверка_с_находками(
        chat_id,
        находки=(("hot_kitchen", "нагар на печи"), ("dough", "мука на полу")),
    )
    оценка = domain_score(chat_id)

    прочитанная = get_inspection(insp_id, tenant=АРЕНДАТОР)

    assert прочитанная is not None
    assert прочитанная.deductions == pytest.approx(оценка.deductions)
    assert прочитанная.counts == оценка.counts
    assert set(прочитанная.by_zone) == set(оценка.by_zone), (
        "разбивка по зонам в базе называет не те зоны, что вернул движок"
    )
    for код, зона in оценка.by_zone.items():
        запись = прочитанная.by_zone[код]
        assert isinstance(запись, dict), "by_zone в базе обязан лежать словарями, а не объектами"
        assert запись["loss"] == pytest.approx(зона.loss), (
            f"вычет зоны {код} в базе разошёлся с тем, что посчитал движок"
        )


def test_находки_идут_по_номеру_по_возрастанию(domain_env: Path, db_env: str) -> None:
    """Порядок находок в отчёте — это порядок протокола обхода. Отсортируй их
    по зоне или по чему-то ещё, и партнёр читает описание нарушений не в том
    порядке, в каком их фиксировал аудитор на месте."""
    chat_id = 503
    insp_id = _проверка_с_находками(
        chat_id,
        находки=(
            ("staff", "находка раз"),
            ("hot_kitchen", "находка два"),
            ("dough", "находка три"),
        ),
    )

    прочитанная = get_inspection(insp_id, tenant=АРЕНДАТОР)

    assert прочитанная is not None
    assert [f.n for f in прочитанная.findings] == [1, 2, 3]
    assert [f.zone for f in прочитанная.findings] == ["staff", "hot_kitchen", "dough"], (
        "находки отсортировались не по номеру записи (например, по названию зоны)"
    )
    первая = прочитанная.findings[0]
    assert первая.code == "CLN05"
    assert первая.level == "D1"
    assert первая.text == "находка раз"
    assert первая.lang == "ru"


def test_находка_без_комментария_это_None_а_не_пустая_строка(domain_env: Path, db_env: str) -> None:
    """Пустая строка вместо `None` выглядела бы так, будто аудитор осознанно
    написал «ничего», хотя он просто не оставил комментария к находке."""
    chat_id = 504
    insp_id = _проверка_с_находками(chat_id, находки=(("hot_kitchen", "нагар на печи"),))

    прочитанная = get_inspection(insp_id, tenant=АРЕНДАТОР)

    assert прочитанная is not None
    находка = прочитанная.findings[0]
    assert находка.text == "нагар на печи"
    assert находка.comment is None, "комментария не было — база обязана вернуть None, а не ''"


def test_source_пусто_без_записи_и_comment_со_слов_аудитора(domain_env: Path, db_env: str) -> None:
    """Источник — это заявление «за формулировку отвечает аудитор». Потерянный
    или подменённый `source` стирает это заявление и для записи без источника
    (проверки до D044), и для записанной со слов человека."""
    chat_id = 505
    start_inspection(chat_id, unit="Белград-1", kind="planned", report_lang="ru", tenant=АРЕНДАТОР)
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="без источника")
    add_finding(
        chat_id,
        code="CLN05",
        level="D1",
        zone="dough",
        text="со слов аудитора",
        source=SOURCE_COMMENT,
    )
    insp_id = push_inspection(chat_id)

    прочитанная = get_inspection(insp_id, tenant=АРЕНДАТОР)

    assert прочитанная is not None
    # По номеру записи, а не позицией в списке: у этого теста своя забота —
    # источник, а не порядок находок, который проверяется отдельным тестом.
    по_номеру = {f.n: f for f in прочитанная.findings}
    без_источника, со_словами = по_номеру[1], по_номеру[2]
    assert без_источника.source == "", "запись без источника обязана вернуть пустую строку"
    assert со_словами.source == SOURCE_COMMENT, (
        "источник «со слов аудитора» потерялся или подменился"
    )


def test_lang_находки_это_язык_речи_проверки(domain_env: Path, db_env: str) -> None:
    """Слой чтения обязан вернуть формулировку на языке РЕЧИ проверки, а не
    молча поискать её на языке отчёта или интерфейса — иначе на проверке,
    записанной не по-русски, текст находки тихо пропадёт вместо перевода."""
    chat_id = 506
    insp_id = _проверка_с_находками(
        chat_id,
        speech_lang="en",
        находки=(("hot_kitchen", "burnt oven surface"),),
    )

    прочитанная = get_inspection(insp_id, tenant=АРЕНДАТОР)

    assert прочитанная is not None
    находка = прочитанная.findings[0]
    assert находка.lang == "en"
    assert находка.text == "burnt oven surface"


def test_проверка_без_находок_читается(domain_env: Path, db_env: str) -> None:
    """Пустая проверка (обход без единого нарушения) — законный документ:
    `None` вместо разбора выглядел бы как «такой проверки нет», хотя она есть
    и хотя бы её шапку партнёр должен получить."""
    chat_id = 507
    insp_id = _проверка_с_находками(chat_id, находки=())

    прочитанная = get_inspection(insp_id, tenant=АРЕНДАТОР)

    assert прочитанная is not None
    assert прочитанная.findings == ()
    assert прочитанная.inspection.findings_count == 0


def test_кривой_идентификатор_это_отказ_а_не_не_найдено() -> None:
    """«Не UUID» и «не найдено», слитые в один ответ, прячут опечатку в
    идентификаторе за правдоподобным «такой проверки нет».

    Регулярка требует именно фразу разбора («не похоже на идентификатор»), а
    не любое слово «идентификатор» в тексте: иначе отказ базы на кривом UUID
    (тот же тип исключения, но по другой причине) прошёл бы тест молча.
    """
    with pytest.raises(DbError, match="не похоже на идентификатор"):
        get_inspection("не-uuid", tenant=АРЕНДАТОР)


def test_findings_by_unit_свежие_проверки_впереди(domain_env: Path, db_env: str) -> None:
    """Свежая проверка обязана оказаться в начале списка: перепутанный
    порядок покажет вычеты по старым протоколам раньше новых, и повторное
    нарушение перестанет быть видно как повтор."""
    старая = _проверка_с_находками(
        508,
        дата="2026-01-10",
        находки=(("hot_kitchen", "старая находка раз"), ("dough", "старая находка два")),
    )
    свежая = _проверка_с_находками(
        509,
        дата="2026-02-20",
        находки=(("staff", "свежая находка"),),
    )

    находки = findings_by_unit(tenant=АРЕНДАТОР, unit="Белград-1")

    assert [f.inspection_id for f in находки] == [свежая, старая, старая], (
        "свежая по дате обхода проверка обязана идти первой, а внутри проверки — по номеру"
    )
    assert [f.n for f in находки[1:]] == [1, 2]


def test_findings_by_unit_называет_проверку_в_каждой_строке(domain_env: Path, db_env: str) -> None:
    """Находка без даты и без точки в строке не отвечает на вопрос, ради
    которого её читают («что повторяется у этой пиццерии») — без этих полей
    строку нельзя связать с конкретным протоколом обхода."""
    insp_id = _проверка_с_находками(
        510,
        дата="2026-03-05",
        находки=(("hot_kitchen", "нагар на печи"),),
    )

    находки = findings_by_unit(tenant=АРЕНДАТОР, unit="Белград-1")

    assert len(находки) == 1
    строка = находки[0]
    assert строка.inspection_id == insp_id
    assert строка.unit_name == "Белград-1"
    assert строка.inspection_date == date(2026, 3, 5)


def test_findings_by_unit_нормализует_но_не_ищет_по_синониму(domain_env: Path, db_env: str) -> None:
    """Обрезка краёв и регистра — то же правило, что и у слива. Карту
    синонимов эта выборка спрашивать не должна: справочник — отдельные данные
    с отдельным владельцем, и молчаливое подмешивание чужого совпадения сюда
    отдало бы находки не той точки под видом синонима."""
    insp_id = _проверка_с_находками(
        511,
        дата="2026-04-01",
        находки=(("hot_kitchen", "нагар на печи"),),
    )
    upsert_unit("Белград 2", aliases=("БГ2",), tenant=АРЕНДАТОР)

    по_обрезанному = findings_by_unit(tenant=АРЕНДАТОР, unit=" белград-1 ")
    assert [f.inspection_id for f in по_обрезанному] == [insp_id], (
        "нормализация регистра и краёв не сработала при чтении находок точки"
    )

    по_синониму = findings_by_unit(tenant=АРЕНДАТОР, unit="БГ2")
    assert по_синониму == [], (
        "выборка нашла точку по синониму — карту синонимов эта функция спрашивать не должна"
    )


def test_findings_by_unit_неизвестная_точка_это_пустой_список(
    domain_env: Path, db_env: str
) -> None:
    """Точка, которой не было в этой сети, — законный пустой список, а не
    отказ: партнёр мог опечататься в названии, и это не поломка чтения."""
    assert findings_by_unit(tenant=АРЕНДАТОР, unit="Никогда не существовавшая точка") == []


def test_недоступная_база_это_отказ_а_не_пустота(monkeypatch: pytest.MonkeyPatch) -> None:
    """Мёртвая база не должна выглядеть как «проверки нет» или «нарушений
    нет» — оба ответа увели бы от настоящей причины: подключение не работает."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://nouser@127.0.0.1:1/nodb?connect_timeout=2")

    with pytest.raises(DbError, match="по идентификатору"):
        get_inspection(str(uuid.uuid4()), tenant=АРЕНДАТОР)
    with pytest.raises(DbError, match="находки точки"):
        findings_by_unit(tenant=АРЕНДАТОР, unit="Белград-1")
