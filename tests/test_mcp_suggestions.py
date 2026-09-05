"""T165: расхождения «модель предложила — аудитор поправил» собираются в предложения.

Решение D077 и требование владельца дословно: «при несостыковках, или если
пользователь добавит что-то в духе "ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО
ЧИСТОТА" то мы долполняем наш список терминов». Слово «мы» здесь — управляющая
компания, и это ровно та граница, которую проверяет этот файл: сборка ГОТОВИТ
предложение человеку и сама не трогает ни боевую карту слов, ни хранилище
версий.

Проверяется чистая сборка — над готовыми строками находок, без базы. Чтение
базы проверено там, где живёт чтение, а разводить Postgres ради подсчёта пар
«предложено → записано» значило бы не проверять как следует ни того, ни другого.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from src.db.models import FindingRow
from src.mcp.errors import ToolError
from src.mcp.suggestions import CueRow, build

ВЕРСИЯ = "imf-2026-09-03-3f5a91b2c7d0"

#: Карта слов синтетическая, как и везде в тестах блока: боевая лежит вне git
#: (D002), и на чужой машине её может не быть.
#: Раздел «Чистота» — одна колонка кандидатов, раздел «Оборудование» — две
#: («грязь» и «поломка»), и это не для красоты: правка принимает ровно столько
#: ячеек, сколько колонок у раздела, поэтому предложение обязано попадать в ту
#: колонку, где стоит промахнувшийся код.
КАРТА = (
    CueRow(section="Чистота", phrase="Пол в разводах, лужа на полу", columns=(("CLN01",),)),
    CueRow(section="Чистота", phrase="Стена в подтёках", columns=(("CLN02",),)),
    CueRow(section="Оборудование", phrase="Печь", columns=(("EQP07",), ("TEH05",))),
)


def находка(
    *,
    n: int = 1,
    code: str = "CLN01",
    level: str = "D1",
    zone: str = "fridge",
    unit: str = "Белград-1",
    suggested_code: str | None = "CLN01",
    suggested_level: str | None = "D1",
    suggested_zone: str | None = "fridge",
    confidence: float | None = 0.8,
) -> FindingRow:
    """Одна записанная находка вместе с тем, что модель предложила до неё."""
    return FindingRow(
        id=f"00000000-0000-0000-0000-00000000000{n}",
        inspection_id="11111111-1111-1111-1111-111111111111",
        unit_name=unit,
        inspection_date=date(2026, 9, 3),
        n=n,
        code=code,
        level=level,
        zone=zone,
        zone_unusual=False,
        source="photo",
        lang="ru",
        text="пол грязный",
        comment=None,
        suggested_code=suggested_code,
        suggested_level=suggested_level,
        suggested_zone=suggested_zone,
        suggested_confidence=confidence,
    )


def собрать(*находки: FindingRow, **прочее: Any) -> dict[str, Any]:
    параметры: dict[str, Any] = {
        "cues": КАРТА,
        "version": ВЕРСИЯ,
        "inspections": 1,
        "units": 1,
    }
    параметры.update(прочее)
    return build(находки, **параметры)


# --- ничего не применяется ----------------------------------------------------


def test_боевая_карта_слов_не_пополняется_а_только_предлагается() -> None:
    """Жёсткая граница D077: пополнять список автоматически нельзя, потому что
    быстрый путь показывает пункт БЕЗ подтверждения аудитора (D064) — неверное
    слово уехало бы в отчёт партнёру без чьего-либо ведома."""
    итог = собрать(находка(code="CLN07"))

    assert итог["applied"] is False
    assert "not applied" in итог["status"]


def test_предложение_названо_вызовом_который_сделает_человек() -> None:
    итог = собрать(находка(code="CLN07"))

    промах = итог["code_misses"][0]
    assert промах["suggested_edits"] == [
        {
            "tool": "edit_photo_cue",
            "arguments": {"phrase": "Пол в разводах, лужа на полу", "codes": ["CLN01, CLN07"]},
        }
    ]


def test_код_дописывается_в_ту_колонку_где_стоит_промахнувшийся() -> None:
    """Колонки раздела значат разное: «грязь» и «поломка» — два вопроса про
    один объект. Код, положенный не в ту, ответил бы «грязь» на вопрос о
    поломке, а ячеек в вызове обязано быть ровно столько, сколько колонок, —
    иначе правку отклонят по ширине строки (`photo_cues._check_codes`)."""
    итог = собрать(находка(suggested_code="TEH05", code="TEH06"))

    assert итог["code_misses"][0]["suggested_edits"] == [
        {
            "tool": "edit_photo_cue",
            "arguments": {"phrase": "Печь", "codes": ["EQP07", "TEH05, TEH06"]},
        }
    ]


# --- где модель промахивается -------------------------------------------------


def test_одинаковые_промахи_складываются_в_одну_строку_со_счётом() -> None:
    итог = собрать(
        находка(n=1, code="CLN07"),
        находка(n=2, code="CLN07"),
        находка(n=3, code="CLN07", unit="Белград-2"),
    )

    промахи = итог["code_misses"]
    assert len(промахи) == 1
    assert промахи[0]["suggested_code"] == "CLN01"
    assert промахи[0]["recorded_code"] == "CLN07"
    assert промахи[0]["count"] == 3
    assert промахи[0]["units"] == ["Белград-1", "Белград-2"]


def test_частый_промах_идёт_первым() -> None:
    итог = собрать(
        находка(n=1, code="CLN07"),
        находка(n=2, code="CLN07"),
        находка(n=3, suggested_code="CLN02", code="CLN09"),
    )

    assert [(m["suggested_code"], m["count"]) for m in итог["code_misses"]] == [
        ("CLN01", 2),
        ("CLN02", 1),
    ]


def test_попадание_модели_промахом_не_считается() -> None:
    итог = собрать(находка(), находка(n=2))

    assert итог["code_misses"] == []
    assert итог["considered"]["with_suggestion"] == 2
    assert итог["considered"]["corrected"] == 0


def test_запись_без_предложения_в_счёт_промахов_не_входит() -> None:
    """Запись заведена вручную или начата до T181: предложения не было вовсе,
    и промахом это назвать нельзя — модель ничего не говорила."""
    итог = собрать(
        находка(n=1, suggested_code=None, suggested_level=None, suggested_zone=None),
        находка(n=2, code="CLN07"),
    )

    assert итог["considered"]["findings"] == 2
    assert итог["considered"]["with_suggestion"] == 1
    assert итог["code_misses"][0]["count"] == 1


# --- быстрый путь: уверенности нет, а строка самая ценная ----------------------


def test_запись_без_уверенности_порогом_не_отсекается() -> None:
    """Быстрый путь (T113) сверяется со списком слов и уверенности не считает
    вовсе. Это самые ценные строки — промах БЕЗ подтверждения аудитора, — и
    отбор «уверенность выше порога» потерял бы ровно их."""
    итог = собрать(находка(code="CLN07", confidence=None), min_confidence=0.9)

    assert итог["code_misses"][0]["count"] == 1
    assert итог["considered"]["without_confidence"] == 1


def test_уверенность_ниже_порога_отсекается_и_это_сказано_числом() -> None:
    итог = собрать(находка(code="CLN07", confidence=0.4), min_confidence=0.9)

    assert итог["code_misses"] == []
    assert итог["considered"]["below_threshold"] == 1


def test_в_строке_промаха_видно_сколько_записей_шло_без_уверенности() -> None:
    итог = собрать(
        находка(n=1, code="CLN07", confidence=None),
        находка(n=2, code="CLN07", confidence=0.62),
        находка(n=3, code="CLN07", confidence=0.91),
    )

    assert итог["code_misses"][0]["confidence"] == {"min": 0.62, "max": 0.91, "unknown": 1}


# --- нечего мерить — это ответ, а не «промахов нет» ---------------------------


def test_ни_одного_предложения_в_периоде_сказано_словами() -> None:
    """Худший исход — пустой список, прочитанный как «модель не промахивается».
    Предложения хранятся только с T181, и до неё сигнала о промахе не было."""
    итог = собрать(находка(suggested_code=None, suggested_level=None, suggested_zone=None))

    assert итог["code_misses"] == []
    assert "nothing to measure" in итог["status"]
    assert "no model suggestion" in итог["status"]


def test_находок_нет_вовсе_это_отдельный_ответ() -> None:
    итог = собрать()

    assert "no findings" in итог["status"]
    assert "nothing to measure" not in итог["status"]


# --- строка карты, которую надо править ----------------------------------------


def test_промах_называет_строку_карты_ведущую_к_неверному_коду() -> None:
    итог = собрать(находка(code="CLN07"))

    assert итог["code_misses"][0]["cue_rows"] == [
        {"section": "Чистота", "phrase": "Пол в разводах, лужа на полу", "codes": ["CLN01"]}
    ]


def test_промах_мимо_карты_слов_говорит_что_править_нечего() -> None:
    """Код предложила модель, а не карта: строки, которую стоило бы поправить,
    в карте нет. Молча отдать пустой список нельзя — он читался бы как «карта
    в порядке»."""
    итог = собрать(находка(suggested_code="ZZZ99", code="CLN07"))

    промах = итог["code_misses"][0]
    assert промах["cue_rows"] == []
    assert промах["suggested_edits"] == []
    assert "no cue row" in промах["note"]


# --- класс и зона -------------------------------------------------------------


def test_поправленный_класс_считается_отдельно_от_пункта() -> None:
    итог = собрать(находка(level="D2"))

    assert итог["code_misses"] == []
    assert len(итог["level_misses"]) == 1
    промах = итог["level_misses"][0]
    assert промах["code"] == "CLN01"
    assert промах["suggested_level"] == "D1"
    assert промах["recorded_level"] == "D2"
    assert промах["count"] == 1
    assert "criteria.md" in промах["note"]


def test_класс_при_поправленном_пункте_в_счёт_не_идёт() -> None:
    """Аудитор сменил пункт — значит предложенный класс относился к другому
    вопросу, и сравнивать его с записанным не с чем."""
    итог = собрать(находка(code="CLN07", level="D2"))

    assert итог["level_misses"] == []
    assert итог["code_misses"][0]["count"] == 1


def test_поправленная_зона_считается_и_при_верном_пункте() -> None:
    итог = собрать(находка(zone="dough"))

    assert итог["zone_misses"] == [
        {
            "suggested_zone": "fridge",
            "recorded_zone": "dough",
            "count": 1,
            "codes": ["CLN01"],
            "confidence": {"min": 0.8, "max": 0.8, "unknown": 0},
        }
    ]


def test_несделанное_предложение_зоны_промахом_не_считается() -> None:
    """Модель зоны не назвала — это не «поправили», это «нечего было
    поправлять». Слитые в одно, они завели бы промах на пустом месте."""
    итог = собрать(находка(suggested_zone=None, zone="dough"))

    assert итог["zone_misses"] == []
    assert итог["considered"]["no_zone_proposed"] == 1


# --- пределы и оговорки -------------------------------------------------------


def test_выдача_упирается_в_предел_и_говорит_об_этом() -> None:
    находки = [находка(n=i, suggested_code=f"AAA{i:02d}", code=f"BBB{i:02d}") for i in range(1, 6)]

    итог = собрать(*находки, limit=2)

    assert len(итог["code_misses"]) == 2
    assert итог["truncated"] is True


def test_чтение_упершееся_в_потолок_помечено_даже_без_обрезки_строк() -> None:
    итог = собрать(находка(code="CLN07"), truncated=True)

    assert итог["truncated"] is True


def test_ответ_говорит_что_сырых_слов_аудитора_в_истории_нет() -> None:
    """T185: колонки под сырые слова в базе ещё нет. Предложение поэтому
    называет строку карты, а не фразу, которую аудитор произнёс, — и читающий
    обязан узнать об этом из ответа, а не догадаться."""
    итог = собрать(находка(code="CLN07"))

    assert any("raw words" in оговорка for оговорка in итог["caveats"])


def test_считанное_видно_числами_с_предметом() -> None:
    итог = собрать(находка(n=1, code="CLN07"), находка(n=2), inspections=4, units=2)

    assert итог["considered"] == {
        "inspections": 4,
        "units": 2,
        "findings": 2,
        "with_suggestion": 2,
        "without_confidence": 0,
        "below_threshold": 0,
        "corrected": 1,
        "no_level_proposed": 0,
        "no_zone_proposed": 0,
    }


def test_версия_методики_чьей_картой_считали_названа() -> None:
    итог = собрать(находка(code="CLN07"))

    assert итог["checklist_version"] == ВЕРСИЯ


@pytest.mark.parametrize("порог", [-0.1, 1.5])
def test_порог_вне_доли_это_отказ(порог: float) -> None:
    """Уверенность — доля от нуля до единицы (`domain.Suggestion`). Порог в
    процентах отобрал бы пустоту и выглядел бы работающим фильтром."""
    with pytest.raises(ToolError) as отказ:
        собрать(находка(), min_confidence=порог)

    assert "0" in str(отказ.value) and "1" in str(отказ.value)
