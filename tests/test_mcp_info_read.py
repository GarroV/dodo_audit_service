"""Информационная часть проверки в карточке чтения (T207): `src/mcp/info_part.py`.

Модуль читает семь полей, которые аудитор заполняет в конце обхода, и подписывает
каждое формулировкой методики — но только той версии, которой помечена сама
проверка (`detail.inspection.checklist_version`), а не сегодняшней боевой. Файл
проверяет ровно этот модуль, без базы: `InspectionDetail`/`InspectionRow`/`InfoRow`
собраны руками, методика разложена на диске фикстурой `build_methodology`.

Каталог версии годится не потому, что назван правильно, а потому, что ЕЮ
ЯВЛЯЕТСЯ: `pinned` (через который читает и этот модуль) сверяет отпечаток
каталога с версией проверки, а не верит имени на двери (T236). Поэтому там,
где тесту нужен настоящий снимок с добавленным пунктом, версия считается по
факту разложенного содержимого — `_снимок_с_пунктом` ниже — а не выдумывается
литералом.

Формулировки пунктов здесь целиком синтетические — свои для этого файла, а не
взятые из `data/` (репозиторий публичный, боевая методика вне git, D073). Совпадение
с боевой формулировкой поймал бы `tests/test_methodology_leak.py`.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from mcp_checklist_harness import build_methodology

from src.db.models import InfoRow, InspectionDetail, InspectionRow
from src.domain.config import DATA_FILES
from src.domain.version import VERSION_FILE, compose
from src.mcp import info_part
from src.mcp.errors import ToolError
from src.mcp.letters import Papers, version_of


def _пункт(code: str, ru: str, en: str) -> str:
    """Строка методики: синтетический пункт информационной части с кодом и формулировкой."""
    return f"{code},info,Раздел,Section,{ru},{en},,,1\n"


def _снимок_с_пунктом(tmp_path: Path, пункт: str, *, name: str, day: str) -> tuple[Path, str]:
    """Разложить снимок версии, которым он и является, — с одним лишним пунктом.

    Пункт обязан войти в отпечаток ДО того, как считается версия, а не
    дописываться в уже изданный каталог задним числом: иначе отпечаток
    разошёлся бы с тем именем, под которым лежит каталог, и `pinned` (T236)
    его бы не признал. Поэтому версия сперва считается во временном каталоге
    (тот же приём, что `harness_edition` оснастки), а уже под этим именем
    раскладывается настоящий снимок.

    Возвращает каталог снимка и версию, которой он теперь является: версия
    зависит от текста пункта и не выдумывается литералом.
    """

    def _разложить(куда: Path) -> str:
        build_methodology(куда)
        with (куда / "checklist.csv").open("a", encoding="utf-8") as файл:
            файл.write(пункт)
        (куда / VERSION_FILE).write_text(f"{name} {day}\n", encoding="utf-8")
        return compose(куда, DATA_FILES)

    with tempfile.TemporaryDirectory() as черновик:
        версия = _разложить(Path(черновик))
    снимок = tmp_path / "store" / "versions" / версия
    _разложить(снимок)
    return снимок, версия


def _строка(*, checklist_version: str, report_lang: str) -> InspectionRow:
    """Шапка записанной проверки, минимально нужная для чтения информационной части."""
    return InspectionRow(
        id="11111111-1111-1111-1111-111111111111",
        tenant_code="укашка",
        unit_name="Тест-1",
        chat_id=1,
        kind="planned",
        inspection_date=date(2026, 8, 19),
        report_lang=report_lang,
        checklist_version=checklist_version,
        pct=97.5,
        grade="A",
        findings_count=0,
        pushed_at="2026-08-19T18:00:00+02:00",
    )


def _проверка(
    *, checklist_version: str, report_lang: str, info: tuple[InfoRow, ...]
) -> InspectionDetail:
    """Проверка целиком с пустыми находками: их модуль `info_part` не читает вовсе."""
    return InspectionDetail(
        inspection=_строка(checklist_version=checklist_version, report_lang=report_lang),
        deductions=0.0,
        counts={},
        by_zone={},
        findings=(),
        info=info,
    )


def test_подпись_берётся_из_версии_проверки_а_не_из_боевой_методики(tmp_path: Path) -> None:
    """Главный тест файла: сегодняшняя методика не имеет права подписать вчерашний ответ.

    Снимок и боевая методика здесь — два РАЗНЫХ издания с разной формулировкой
    одного и того же пункта, а версия проверки считается ПО СНИМКУ (T236:
    `pinned` сверяет отпечаток каталога с версией, а не имя на двери). Снимок
    побеждает потому, что он и есть то издание, которым проверка помечена, —
    боевая методика с чужой формулировкой этим изданием не является и
    отклоняется сверкой, а не порядком перебора мест.

    Порча, которая однажды предпочтёт боевую версию снимку, показала бы цену
    буквально: человек читает подпись сегодняшнего вопроса под прошлогодним
    ответом (D033, D049) и не узнаёт об этом никак — подпись из соседней
    версии выглядит настоящей.
    """
    боевая_ru, боевая_en = "Согласован ли новый срок предписания", "Is the new deadline agreed"
    снимок_ru, снимок_en = "Устраивает ли партнёра прежний срок", "Does the old deadline still hold"
    код = "INF07"

    _, версия = _снимок_с_пунктом(
        tmp_path, _пункт(код, снимок_ru, снимок_en), name="pinned-old", day="2026-09-05"
    )

    боевая = tmp_path / "live"
    build_methodology(боевая)
    with (боевая / "checklist.csv").open("a", encoding="utf-8") as файл:
        файл.write(_пункт(код, боевая_ru, боевая_en))
    assert version_of(боевая) != версия, "боевая методика обязана быть ДРУГИМ изданием, не снимком"

    проверка = _проверка(
        checklist_version=версия,
        report_lang="ru",
        info=(InfoRow(code=код, text="партнёр согласен на прежний срок"),),
    )
    бумаги = Papers(live=боевая, store=tmp_path / "store")

    секция = info_part.read(проверка, lang="ru", papers=бумаги)

    assert секция.fields[0]["title"] == снимок_ru, "подпись обязана быть из снимка версии проверки"
    assert all(поле["title"] != боевая_ru for поле in секция.fields), (
        "формулировка боевой методики просочилась в ответ по проверке прежней версии"
    )


def test_методики_этой_версии_нет_вовсе_ответы_всё_равно_отдаются(tmp_path: Path) -> None:
    """Переставленный каталог методики не имеет права спрятать записанное в базе.

    Ни снимка нужной версии в хранилище, ни совпадения версии у боевой методики
    нет: `title` обязан стать `None` у каждого поля, а не отказом инструмента —
    информационная часть читается без единого файла методики, в отличие от письма.
    """
    build_methodology(tmp_path / "live")
    with (tmp_path / "live" / "checklist.csv").open("a", encoding="utf-8") as файл:
        файл.write(_пункт("INF01", "формулировка, которой не место в ответе", "must not leak"))
    (tmp_path / "store").mkdir()

    проверка = _проверка(
        checklist_version="policy-2019-01-01-000000000000",
        report_lang="ru",
        info=(InfoRow(code="INF01", text="ответ аудитора"), InfoRow(code="INF03", text="да")),
    )
    бумаги = Papers(live=tmp_path / "live", store=tmp_path / "store")

    секция = info_part.read(проверка, lang=None, papers=бумаги)

    assert [поле["code"] for поле in секция.fields] == ["INF01", "INF03"]
    assert [поле["text"] for поле in секция.fields] == ["ответ аудитора", "да"]
    assert all(поле["title"] is None for поле in секция.fields), (
        "методики этой версии нет — подписи быть не должно"
    )
    assert "by code alone" in секция.note, "ответ обязан сказать словами, что подписи нет"
    assert "is not at hand" in секция.note, "причина — версии нет на машине, а не пропуск пункта"


def test_язык_параметр_а_не_константа(tmp_path: Path) -> None:
    """Подпись переводится по названному языку, ответ аудитора — никогда.

    Один и тот же аргумент `lang` решает и подпись поля, и `Section.lang`
    ответа; не названный язык обязан браться из `report_lang` записанной
    проверки, а не подставляться константой.
    """
    _, версия = _снимок_с_пунктом(
        tmp_path,
        _пункт("INF01", "Смена закрыта по чек-листу", "Shift closed per checklist"),
        name="langtest",
        day="2026-09-05",
    )

    проверка = _проверка(
        checklist_version=версия,
        report_lang="en",
        info=(InfoRow(code="INF01", text="ответ аудитора дословно"),),
    )
    бумаги = Papers(live=None, store=tmp_path / "store")

    по_en = info_part.read(проверка, lang="en", papers=бумаги)
    по_ru = info_part.read(проверка, lang="ru", papers=бумаги)
    по_умолчанию = info_part.read(проверка, lang=None, papers=бумаги)

    assert по_en.lang == "en"
    assert по_ru.lang == "ru"
    assert по_умолчанию.lang == "en", "не названный язык обязан браться из report_lang проверки"

    assert по_en.fields[0]["title"] == "Shift closed per checklist"
    assert по_ru.fields[0]["title"] == "Смена закрыта по чек-листу"
    assert по_умолчанию.fields[0]["title"] == по_en.fields[0]["title"]

    тексты = {по_en.fields[0]["text"], по_ru.fields[0]["text"], по_умолчанию.fields[0]["text"]}
    assert тексты == {"ответ аудитора дословно"}, (
        "ответ аудитора не переводится и не меняется по языку"
    )


def test_check_lang_незнакомый_язык_отказ_а_не_подстановка() -> None:
    """Незнакомый язык — `ToolError`, а не молчаливый откат на русский.

    Подпись поля есть только на языках, заведённых в методике: подставленный
    вместо незнакомого языка русский читался бы как перевод, которого никто не
    делал.
    """
    assert info_part.check_lang(None) is None
    assert info_part.check_lang(" EN ") == "en"
    assert info_part.check_lang("RU") == "ru"
    for плохой in ("sr", "SR", " fr ", "ru2"):
        with pytest.raises(ToolError):
            info_part.check_lang(плохой)


def test_порядок_полей_записанный_а_не_по_алфавиту_кода(tmp_path: Path) -> None:
    """Порядок разделов задаёт бот; переставленные разделы — другая бумага.

    Записанный порядок кодов (`INF07`, `INF01`, `INF03`) намеренно не совпадает
    с алфавитным — иначе тест был бы зелёным и на сортировке по коду.
    """
    _, версия = _снимок_с_пунктом(
        tmp_path,
        _пункт("INF07", "пункт семь", "item seven")
        + _пункт("INF01", "пункт один", "item one")
        + _пункт("INF03", "пункт три", "item three"),
        name="ordertest",
        day="2026-09-05",
    )

    проверка = _проверка(
        checklist_version=версия,
        report_lang="ru",
        info=(
            InfoRow(code="INF07", text="ответ7"),
            InfoRow(code="INF01", text="ответ1"),
            InfoRow(code="INF03", text="ответ3"),
        ),
    )
    бумаги = Papers(live=None, store=tmp_path / "store")

    секция = info_part.read(проверка, lang=None, papers=бумаги)

    assert [поле["code"] for поле in секция.fields] == ["INF07", "INF01", "INF03"]
    assert [поле["text"] for поле in секция.fields] == ["ответ7", "ответ1", "ответ3"]
    assert [поле["title"] for поле in секция.fields] == ["пункт семь", "пункт один", "пункт три"]


def test_пункта_нет_в_методике_этой_версии_подписи_нет_и_код_назван(tmp_path: Path) -> None:
    """Методика есть, а пункта в ней нет — другой случай, чем «методики нет вовсе».

    `title` обязан стать `None` только у пропавшего пункта, а `note` — назвать
    именно его код. Отсутствие фразы «is not at hand» отличает этот случай от
    того, где методики этой версии на машине нет совсем.
    """
    _, версия = _снимок_с_пунктом(
        tmp_path, _пункт("INF01", "пункт один", "item one"), name="partialtest", day="2026-09-05"
    )

    проверка = _проверка(
        checklist_version=версия,
        report_lang="ru",
        info=(InfoRow(code="INF01", text="ответ1"), InfoRow(code="INF09", text="ответ9")),
    )
    бумаги = Papers(live=None, store=tmp_path / "store")

    секция = info_part.read(проверка, lang=None, papers=бумаги)

    поля = {поле["code"]: поле for поле in секция.fields}
    assert поля["INF01"]["title"] == "пункт один"
    assert поля["INF09"]["title"] is None
    assert "INF09" in секция.note, "note обязан назвать код пункта, которого нет в этой версии"
    assert "has no wording for them in this language" in секция.note
    assert "is not at hand" not in секция.note, "методика найдена — это другой случай, чем случай 2"


def test_у_пункта_нет_формулировки_на_спрошенном_языке(tmp_path: Path) -> None:
    """Пункт на месте, а английского текста у него нет — и это НЕ «пункта нет».

    Случай не выдуманный: английская формулировка необязательна при заведении
    пункта через агента (`add_checklist_item`, `question_en` можно не называть),
    и такой пункт живёт в методике с одним русским текстом. Ответ обязан честно
    сказать, что формулировки нет НА ЭТОМ ЯЗЫКЕ, — «в методике нет такого
    пункта» было бы про методику управляющей компании неправдой.
    """
    _, версия = _снимок_с_пунктом(
        tmp_path, _пункт("INF06", "пункт только по-русски", ""), name="onelang", day="2026-09-05"
    )

    проверка = _проверка(
        checklist_version=версия,
        report_lang="ru",
        info=(InfoRow(code="INF06", text="ответ6"),),
    )
    бумаги = Papers(live=None, store=tmp_path / "store")

    по_русски = info_part.read(проверка, lang="ru", papers=бумаги)
    по_английски = info_part.read(проверка, lang="en", papers=бумаги)

    assert по_русски.fields[0]["title"] == "пункт только по-русски"
    assert по_английски.fields[0]["title"] is None, "пустая формулировка выдана за подпись"
    assert "INF06" in по_английски.note
    assert "has no wording for them in this language" in по_английски.note
    assert "is not at hand" not in по_английски.note, (
        "методика найдена, а ответ винит отсутствие версии — чинить будут не то"
    )


def test_каталог_версии_есть_но_изданием_не_является_ответы_отдаются_без_подписи(
    tmp_path: Path,
) -> None:
    """Недоразложенное хранилище версий отнимает подписи, а не ответы.

    Каталог версии есть на диске, но снимок положили не до конца — пустой
    каталог без единого файла методики. Сверка отпечатка (T236) его этим
    изданием не признаёт: отпечаток пустого каталога с версией проверки не
    сходится, и до попытки прочитать `checklist.csv` дело не доходит вовсе —
    `pinned` отказывает раньше. Инструмент чтения обязан всё равно отдать
    записанное и сказать, что подписей нет: отказ здесь означал бы, что чужая
    недоразложенность прячет от человека документ, который уже ушёл партнёру.
    """
    версия = "brokenstore-2026-09-05-000000000005"
    (tmp_path / "store" / "versions" / версия).mkdir(parents=True)

    проверка = _проверка(
        checklist_version=версия,
        report_lang="ru",
        info=(InfoRow(code="INF01", text="ответ1"),),
    )
    бумаги = Papers(live=None, store=tmp_path / "store")

    секция = info_part.read(проверка, lang=None, papers=бумаги)

    assert [поле["text"] for поле in секция.fields] == ["ответ1"]
    assert секция.fields[0]["title"] is None
    assert "by code alone" in секция.note
    assert str(tmp_path) not in секция.note, "путь с диска уехал в ответ агенту"


def test_язык_отчёта_которого_нет_в_методике_не_роняет_чтение(tmp_path: Path) -> None:
    """Строка из базы не имеет права уронить инструмент чтения.

    Язык отчёта проверяется при заведении проверки (`domain.state`), то есть
    сегодня в базе такого не лежит. Но проверку туда кладём не только мы и не
    только сегодня, а падение на чтении выглядело бы поломкой инструмента —
    человек чинил бы MCP вместо строки, которую кто-то записал мимо контракта.
    """
    _, версия = _снимок_с_пунктом(
        tmp_path, _пункт("INF01", "пункт один", "item one"), name="oddlang", day="2026-09-05"
    )

    проверка = _проверка(
        checklist_version=версия,
        report_lang="sr",
        info=(InfoRow(code="INF01", text="ответ1"),),
    )
    бумаги = Papers(live=None, store=tmp_path / "store")

    секция = info_part.read(проверка, lang=None, papers=бумаги)

    assert секция.lang == "sr"
    assert [поле["text"] for поле in секция.fields] == ["ответ1"]
    assert секция.fields[0]["title"] is None
    assert "by code alone" in секция.note


def test_версия_с_побегом_из_хранилища_не_читается(tmp_path: Path) -> None:
    """Версия приезжает из базы и подставляется в путь каталога — значит, сторож.

    За `..` положена НАСТОЯЩАЯ работающая методика, а не пустой каталог: без
    этого тест был бы зелёным по неверной причине — побег упирался бы во второй
    сторож «такого каталога нет», и снятие проверки имени ничего бы не меняло
    (та же ловушка, что на T098 и T120). Здесь побег работает: снятый сторож
    читает чужой каталог и подписывает поля его формулировками.
    """
    чужая = tmp_path / "sneaky"
    build_methodology(чужая)
    with (чужая / "checklist.csv").open("a", encoding="utf-8") as файл:
        файл.write(_пункт("INF01", "формулировка из чужого каталога", "wording from outside"))
    (tmp_path / "store" / "versions").mkdir(parents=True)

    проверка = _проверка(
        checklist_version="../../sneaky",
        report_lang="ru",
        info=(InfoRow(code="INF01", text="ответ1"),),
    )
    бумаги = Papers(live=None, store=tmp_path / "store")

    секция = info_part.read(проверка, lang=None, papers=бумаги)

    assert секция.fields[0]["title"] is None, (
        "имя версии с побегом прочитало каталог за пределами хранилища"
    )
    assert "формулировка из чужого каталога" not in секция.note
    assert секция.fields[0]["text"] == "ответ1", "записанный ответ пропал вместе с отказом"
