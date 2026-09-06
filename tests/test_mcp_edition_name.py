"""T243: годность ИМЕНИ издания — одно правило на весь продукт, и отказ по делу.

Продолжение T236 и та же болезнь, только на другом вопросе. Там расходились
сверки СОДЕРЖИМОГО каталога издания, здесь — сверки его ИМЕНИ.

Имя набора методики задаёт управляющая компания файлом `checklist_version.txt`,
и оно бывает не латиницей. Полка снимков домена такое издание принимала (список
запрещённого: `.`, `..` и разделители пути), а сторож MCP был белым списком
латиницы — и для проверки, помеченной изданием вроде `имф-2026-09-01-…`,
инструменты не отдавали ни письма, ни подписей полей.

Хуже самого отказа было то, ЧТО он говорил. Письмо посылало искать пропавший
снимок, которого никто не терял; карточка сообщала, что методики этой версии
нет на машине, стоя на ней ногами. Оба ответа неверны по причине и никуда не
ведут.

Здесь проверяется закрытие: правило одно (`domain.version.is_one_segment`),
законное издание читается всеми поверхностями, а негодный идентификатор
отказывает по настоящей причине — и на письме, и в карточке.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from mcp_checklist_harness import build_edition, build_methodology

from src.db.models import FindingRow, InfoRow, InspectionDetail, InspectionRow
from src.domain.config import DATA_FILES, Settings
from src.domain.edition import shelf, snapshot
from src.domain.version import SEGMENT_BYTES, edition_of, is_one_segment
from src.mcp import info_part, letters
from src.mcp.checklist import VERSIONS_DIR
from src.mcp.errors import ChecklistError, ToolError

#: Имя набора от управляющей компании — не латиницей. Ровно тот случай, ради
#: которого задача заведена: выдумывать его за УК продукт не имеет права.
НАБОР = "имф"
ДЕНЬ = "2026-09-01"

#: Пункт, подпись которого читает карточка проверки. Формулировки выдуманы:
#: боевая методика лежит вне git (D002).
ПУНКТ = "INF07,info,Итоги,Wrap-up,Срок устранения,Deadline,D1,*,10\n"


def _издание(каталог: Path, *, name: str = НАБОР, day: str = ДЕНЬ) -> str:
    """Каталог издания с пунктом информационной части и его идентификатор.

    Отпечаток считается ПОСЛЕ дописанного пункта: имя каталога обязано быть
    этим изданием, а не просто походить на него (сверка T236).
    """
    build_edition(каталог, name=name, day=day)
    with (каталог / "checklist.csv").open("a", encoding="utf-8") as файл:
        файл.write(ПУНКТ)
    издание = edition_of(каталог, DATA_FILES)
    assert издание is not None, "каталог обязан быть изданием"
    return издание


def _проверка(version: str) -> InspectionDetail:
    """Записанная проверка, помеченная этим изданием: одна находка, одно поле."""
    строка = InspectionRow(
        id="11111111-1111-1111-1111-111111111111",
        tenant_code="укашка",
        unit_name="Белград-1",
        chat_id=744230399,
        kind="planned",
        inspection_date=date(2026, 8, 19),
        report_lang="ru",
        checklist_version=version,
        pct=99.5,
        grade="A",
        findings_count=1,
        pushed_at="2026-08-19T18:00:00+02:00",
    )
    находка = FindingRow(
        id="22222222-2222-2222-2222-222222222222",
        inspection_id=строка.id,
        unit_name="Белград-1",
        inspection_date=date(2026, 8, 19),
        n=1,
        code="CLN01",
        level="D1",
        zone="fridge",
        zone_unusual=False,
        source="comment",
        lang="ru",
        text="Пол в холодильнике в разводах",
        comment="",
    )
    return InspectionDetail(
        inspection=строка,
        deductions=0.5,
        counts={"D1": 1},
        by_zone={},
        findings=(находка,),
        info=(InfoRow(code="INF07", text="30.09.2026"),),
    )


@pytest.fixture
def нелатинское(tmp_path: Path) -> tuple[str, letters.Papers]:
    """Издание с нелатинским именем набора, лежащее снимком в хранилище версий."""
    издание = _издание(tmp_path / "издание")
    shutil.copytree(tmp_path / "издание", tmp_path / "store" / VERSIONS_DIR / издание)
    build_methodology(tmp_path / "live")
    return издание, letters.Papers(live=tmp_path / "live", store=tmp_path / "store")


# --- законное издание читается всеми поверхностями -----------------------------


def test_издание_с_нелатинским_именем_набора_опознаётся(
    нелатинское: tuple[str, letters.Papers],
) -> None:
    """Тот самый дефект: `pinned` не доходил до каталога вовсе.

    Проверяется и само имя: тест, где идентификатор случайно оказался бы
    латинским, был бы зелен и на белом списке.
    """
    издание, бумаги = нелатинское

    assert издание.startswith(f"{НАБОР}-{ДЕНЬ}-"), f"издание внезапно латиницей: {издание!r}"

    найдено = letters.pinned(издание, бумаги)

    assert найдено is not None
    каталог, откуда = найдено
    assert каталог.name == издание
    assert откуда == letters.FROM_SNAPSHOT


def test_письмо_по_такой_проверке_собирается(нелатинское: tuple[str, letters.Papers]) -> None:
    """До правки инструмент письма отказывал, и отказ уводил не туда."""
    издание, бумаги = нелатинское

    ответ = letters.build(_проверка(издание), lang=None, papers=бумаги)

    письмо = ответ["letter"]
    assert isinstance(письмо, str)
    assert "99.5%" in письмо
    assert ответ["checklist_version"] == издание
    assert ответ["methodology"] == letters.FROM_SNAPSHOT


def test_карточка_подписывает_поля_из_такого_издания(
    нелатинское: tuple[str, letters.Papers],
) -> None:
    """Вторая поверхность: подписи полей брались из методики — и не брались.

    Сверяется сама подпись, а не только её наличие: `title`, оказавшийся не
    формулировкой из этого издания, читался бы как ответ на другой вопрос.
    """
    издание, бумаги = нелатинское

    секция = info_part.read(_проверка(издание), lang="ru", papers=бумаги)

    assert [поле["title"] for поле in секция.fields] == ["Срок устранения"]
    assert "by code alone" not in секция.note


def test_правка_методики_изданной_нелатинским_набором_доходит_до_версии(
    tmp_path: Path,
) -> None:
    """Третья поверхность: правка через агента.

    Имя набора наследуется из `checklist_version.txt` как есть (`_resolve_name`),
    и белый список ловил его в самом конце — после проверки движком, у имени
    каталога новой версии. Правка при этом теряется, а отказ говорит про
    «не похоже на версию».
    """
    from src.mcp.checklist import Store, apply_change, versions

    живая = tmp_path / "живая"
    _издание(живая)
    хранилище = Store(root=tmp_path / "хранилище", live=живая)

    итог = apply_change(
        хранилище,
        tenant="укашка",
        tool="add_checklist_item",
        command="add",
        options={
            "id": "TST01",
            "process": "Проба",
            "question-ru": "Проба пера",
            "levels": "D1",
            "zones": "fridge",
            "days": 5,
            "criteria": "D1: проба",
        },
        version_name=None,
        today=date(2026, 9, 3),
    )

    assert итог.accepted, f"правка отклонена: {итог.refusal}"
    assert итог.version is not None
    assert итог.version.startswith(f"{НАБОР}-2026-09-03-")
    assert итог.version in {версия.version for версия in versions(хранилище)}


# --- правило одно ---------------------------------------------------------------


#: Имена, на которых два сторожа обязаны сойтись. Половина законна (издание УК
#: не латиницей, точка в отпечатке, длинное имя), половина — нет.
ИМЕНА = [
    "имф-2026-09-01-3f5a91b2c7d0",
    "imf-2026-09-03-3f5a91b2c7d0",
    "ИМФ-2026-09-01-3f5a91b2c7d0",
    "local-3f5a91b2c7d0",
    "имф.2026.09.01-3f5a91b2c7d0",
    "..",
    ".",
    "",
    "../побег",
    "imf/вложенно",
    "имф\\побег",
    "имф\0побег",
    "и" * (SEGMENT_BYTES // 2),
    "a" * (SEGMENT_BYTES + 1),
    " ",
    "  имф-2026-09-01-3f5a91b2c7d0  ",
]


@pytest.mark.parametrize("имя", ИМЕНА)
def test_полка_домена_и_сторож_MCP_отвечают_одинаково(имя: str) -> None:
    """Сведение, ради которого задача заведена.

    Сверяются не два списка, а два ОТВЕТА на один вопрос: правило теперь одно
    (`domain.version.is_one_segment`), и разойтись им негде. Тест переживёт
    любую правку самого правила и покраснеет ровно тогда, когда у поверхностей
    снова заведутся свои мнения.

    Сверяется по ОБРЕЗАННОМУ значению, и это не поблажка. Обрезка пробелов —
    приведение аргумента, приехавшего от агента, и делают её здесь все три
    сторожа модуля (`_check_name`, `check_code`, `_check_version`) одинаково;
    правилом является то, что стоит ПОСЛЕ неё. Утверждение сформулировано так,
    что обрезка в него входит явно, а не прячется оговоркой.
    """
    годно = is_one_segment(имя.strip())

    отказал = False
    try:
        letters.check_version(имя)
    except ToolError:
        отказал = True

    assert отказал is not годно, f"поверхности разошлись на «{имя}»"


def test_длина_мерится_байтами_а_не_знаками() -> None:
    """Предел взят у файловой системы (`NAME_MAX`), и он байтовый.

    Мерка в знаках пропустила бы кириллическое имя вдвое длиннее предела: оно
    дошло бы до `os.replace` и упало голым `ENAMETOOLONG` посреди правки
    методики.
    """
    assert is_one_segment("и" * (SEGMENT_BYTES // 2))
    assert not is_one_segment("и" * (SEGMENT_BYTES // 2 + 1))
    assert is_one_segment("a" * SEGMENT_BYTES)
    assert not is_one_segment("a" * (SEGMENT_BYTES + 1))


def test_полка_домена_принимает_то_же_издание(tmp_path: Path) -> None:
    """Встречная половина сведения: домен на этом имени не изменился.

    Правило переехало из `domain.edition` в `domain.version`, и переезд обязан
    быть переездом, а не правкой поведения.
    """
    издание = _издание(tmp_path / "выезд")
    настройки = Settings(
        data_dir=tmp_path / "выезд",
        state_dir=tmp_path / "state",
        audit_script=tmp_path / "audit.py",
    )
    полка = shelf(настройки) / издание
    shutil.copytree(tmp_path / "выезд", полка)

    assert snapshot(настройки, издание) == полка
    assert snapshot(настройки, "../побег") is None


# --- отказ называет настоящую причину -------------------------------------------


def test_письмо_на_негодном_идентификаторе_не_врёт_про_пропавший_снимок(
    нелатинское: tuple[str, letters.Papers],
) -> None:
    """Отказ обязан вести к тому, что чинить.

    Негодный идентификатор — это испорченная строка проверки, а не потерянный
    снимок. Прежний отказ («не похоже на версию методики», латинский пример)
    отправлял человека переименовывать законное издание его же управляющей
    компании.
    """
    _, бумаги = нелатинское

    with pytest.raises(ToolError) as отказ:
        letters.build(_проверка("../побег"), lang=None, papers=бумаги)

    сказано = str(отказ.value)
    assert "именем каталога" in сказано
    assert "снимка нет" not in сказано
    assert "не похоже на версию" not in сказано


def test_карточка_различает_негодный_идентификатор_и_потерянный_снимок(
    нелатинское: tuple[str, letters.Papers],
) -> None:
    """Карточка отказывать не имеет права: она читается и без методики.

    Но и молчать не имеет права тоже. До правки оба случая приезжали одним
    предложением «version is not at hand», и человек шёл искать снимок,
    которого никто не терял.
    """
    _, бумаги = нелатинское

    негодный = info_part.read(_проверка("../побег"), lang="ru", papers=бумаги)
    потерянный = info_part.read(_проверка("imf-2026-01-01-000000000000"), lang="ru", papers=бумаги)

    # Ответы аудитора отдаются в обоих случаях: чинить надо запись, а не
    # прятать документ, который уже ушёл партнёру.
    assert [поле["text"] for поле in негодный.fields] == ["30.09.2026"]
    assert [поле["title"] for поле in негодный.fields] == [None]

    assert "recorded identifier" in негодный.note
    assert "not at hand" not in негодный.note
    assert "not at hand" in потерянный.note
    assert негодный.note != потерянный.note


def test_негодный_идентификатор_не_уводит_чтение_за_пределы_хранилища(
    tmp_path: Path,
) -> None:
    """Сторож имени остаётся сторожем пути, а не украшением текста.

    За `..` лежит НАСТОЯЩАЯ работающая методика: без неё побег упирался бы во
    второй сторож («такого каталога нет»), и тест был бы зелёным по неверной
    причине.
    """
    чужое = _издание(tmp_path / "чужое", name="soseda")
    (tmp_path / "state" / "methodology").mkdir(parents=True)
    бумаги = letters.Papers(live=None, store=None, shelf=tmp_path / "state" / "methodology")

    with pytest.raises(ToolError):
        letters.pinned("../../чужое", бумаги)
    with pytest.raises(ToolError):
        letters.pinned(f"../../{чужое}", бумаги)


def test_отказ_хранилища_остаётся_отказом_хранилища(tmp_path: Path) -> None:
    """Тип отказа у поверхностей свой, и путать их нельзя.

    `ChecklistError` означает «движок не принял правку методики»; на пути
    чтения проверок ни правки, ни движка нет, и отказ там — `ToolError`.
    Правило при этом одно на обоих.
    """
    from src.mcp.checklist import Store, read_items

    build_methodology(tmp_path / "живая")
    хранилище = Store(root=tmp_path / "хранилище", live=tmp_path / "живая")

    with pytest.raises(ChecklistError):
        read_items(хранилище, version="../побег")
    with pytest.raises(ToolError):
        letters.check_version("../побег")
